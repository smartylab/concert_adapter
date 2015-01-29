#!/usr/bin/python
"""
adapter/adapter.py v0.1

This module is to adapt requests from the BPEL engine to the concert framework.

Authors: Jae Yoo Lee <jaeyoo1981@gmail.com>, Moon Kwon Kim <mkdmkk@gmail.com>, Chun Woo Park <cnsdnsla@gmail.com>
Since: 2015.01.26
"""


# Import Dependent Libraries
from BaseHTTPServer import HTTPServer
import simplejson
import sys
import threading
import thread
from pysimplesoap.server import SoapDispatcher, SOAPHandler
from pysimplesoap.client import SoapClient
import yaml
import time
import importlib
import ast


# Import Dependent Modules of ROS, Rocon, and Concert
import rospy
import rocon_python_comms
import rocon_uri
import concert_service_utilities
import concert_scheduler_requests
import concert_service_link_graph
import roslib.message


# Import Messages
import rocon_std_msgs.msg as rocon_std_msgs
import scheduler_msgs.msg as scheduler_msgs
import concert_msgs.msg as concert_msgs
import concert_scheduler_requests.common as scheduler_common
from std_msgs.msg import String


# Import Testers
from tester import TeleopTester
from tester import ChatterTester


# Constants
NODE_NAME = 'concert_adapter'
DEFAULT_QUEUE_SIZE = 8
SOAP_SERVER_ADDRESS = 'localhost'
SOAP_SERVER_PORT = '8008'
ALLOCATION_CHECKING_DURATION = 1 # seconds
ALLOCATION_TIMEOUT = 10 # seconds


class ConcertAdapter(object):
    __slots__ = [
        'soap_server',
        'soap_client',
        'service_name',
        'service_description',
        'service_priority',
        'service_id',
        'allocation_timeout',
        'requester',
        'httpd',
        'pending_requests',
        'allocated_resources',
        'adapter2bpel_sub'
    ]


    def __init__(self):
        # Initialization
        (self.service_name, self.service_description, self.service_priority, self.service_id) = concert_service_utilities.get_service_info()
        self.allocation_timeout = rospy.get_param('allocation_timeout', 15.0)  # seconds

        # Check the scheduler's KnownResources topic
        try:
            rocon_python_comms.find_topic('scheduler_msgs/KnownResources', timeout=rospy.rostime.Duration(5.0), unique=True)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("Could not locate the scheduler's known_resources topic. [%s]" % str(e))
            sys.exit(1)

        # Set up the requester
        self._set_requester(self.service_id)
        self.pending_requests = dict()

        # Prepare a basket for storing allocated resources
        # Form: {__resource_uri__:{resource:__Resource.msg__, publisher:__Publisher__}, ...}
        self.allocated_resources = dict()

        # Starting a SOAP server as a thread
        try:
            threading.Thread(target=self._start_soap_server).start()
        except:
            rospy.loginfo("Error on SOAP Server Thread...")


########################################################################################################
# Preparation for adaptation: SOAP Server
########################################################################################################
    def _start_soap_server(self):
        """
        To launch a SOAP server for the adapter
        :return:
        """
        dispatcher = SoapDispatcher('concert_adapter_soap_server', location = SOAP_SERVER_ADDRESS, action = SOAP_SERVER_ADDRESS,
                namespace = "http://smartylab.co.kr/products/op/adapter", prefix="tns", ns = True)

        # To register a method for LinkGraph Service Invocation
        dispatcher.register_function('invoke_adapter', self.receive_service_invocation, returns={'out': str},
            args={
                'LinkGraph': {
                    'name': str,
                    'nodes': [{
                        'Node':{
                            'id': str,
                            'uri': str,
                            'min': int,
                            'max': int,
                            'parameters': [{
                                'parameter': {
                                    'message': str,
                                    'frequency': int
                                }
                            }]
                        }
                    }],
                    'topics': [{
                        'Topic':{
                            'id': str,
                            'type': str
                        }
                    }],
                    'edges': [{
                        'Edge':{
                            'start': str,
                            'finish': str,
                            'remap_from': str,
                            'remap_to': str
                        }
                    }],
                    'methods': [{
                        'Method': {
                            'address': str,
                            'namespace': str,
                            'name': str,
                            'return_name': str,
                            'param': str
                        }
                    }]
                }
            }
        )

        # To register a method for Single Node Service Invocation
        dispatcher.register_function('invoke_adapter_single_node', self.receive_single_node_service_invocation, returns={'out': str},
            args={
                'Node':{
                    'id': str,
                    'uri': str,
                    'min': int,
                    'max': int,
                    'parameters': [{
                        'parameter': {
                            'message': str,
                            'frequency': int
                        }
                    }]
                }
            }
        )

        # To register a method for Single Node Service Invocation
        dispatcher.register_function('call_resource', self._call_resource, returns={'out': str},
            args={
                'topic_name': str,
                'message': {
                    'linear': {
                        'x': float,
                        'y': float,
                        'z': float
                    },
                    'angular': {
                        'x': float,
                        'y': float,
                        'z': float
                    }
                }
            }
        )


        # To register a method for Single Node Service Invocation
        dispatcher.register_function('invoke_rapp', self._invoke_rapp, returns={'out': str},
            args={
                'svc_name': str
            }
        )


        # To register a method for Releasing Allocated Resources
        dispatcher.register_function('release_allocated_resources', self.release_allocated_resources, returns={'out': bool}, args={})

        # To create SOAP Server
        rospy.loginfo("Starting a SOAP server...")
        self.httpd = HTTPServer(("", int(SOAP_SERVER_PORT)), SOAPHandler)
        self.httpd.dispatcher = dispatcher

        # To execute SOAP Server
        rospy.loginfo("The SOAP server started. [%s:%s]" % (SOAP_SERVER_ADDRESS, SOAP_SERVER_PORT))
        self.httpd.serve_forever()


    def _stop_soap_server(self):
        '''
        To stop SOAP Server
        :return:
        '''
        rospy.loginfo("Stopping the SOAP server...")
        try:
            self.httpd.shutdown()
        except:
            rospy.loginfo("Stopping SOAP Server is failed.")


################################################################
# Preparation for adaptation: Requester
################################################################
    def _set_requester(self, uuid):
        """
        To set a requester
        :param uuid:
        :return:
        """
        try:
            scheduler_requests_topic = concert_service_utilities.find_scheduler_requests_topic()
            self.requester = concert_scheduler_requests.Requester(self._on_resource_allocated, uuid=self.service_id, topic=scheduler_requests_topic)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("Could not locate the scheduler's scheduler_requests topic. [%s]" % str(e))
            sys.exit(1)


################################################################
# Communication from the BPEL engine to the SOAP server
################################################################
    def receive_service_invocation(self, LinkGraph):
        """
        To receive a service invocation including LinkGraph
        :param LinkGraph:
        :return string:
        """
        # Convert input data (LinkGraph)
        lg_name, linkgraph = self._convert_to_linkgraph(LinkGraph)
        rospy.loginfo("Linkgraph loaded:\n%s" % linkgraph)

        # Request resource allocations
        # self.wait_allocation(self._inquire_resources_to_allocate(linkgraph))

        # Prepare subscribers for the adapter2bpel communication
        rospy.loginfo("Method info loaded:\n%s" % LinkGraph['methods'])
        self._prepare_adapter2bpel_sub(LinkGraph['methods'])

        threading.Thread(target=self.test_adapter2bpel).start()

        return "Hi"


    def test_adapter2bpel(self):
        time.sleep(5)
        pub = rospy.Publisher("/concert_adapter/invoke_first_service", String, queue_size=10)
        pub.publish("Hello BPEL.")


    def receive_single_node_service_invocation(self, Node):
        '''
        To receive a service invocation for a single node
        :param Node:
        :return:
        '''
        # Converting input data to LinkGraph
        lg_name, linkgraph = self._convert_to_linkgraph(Node)
        rospy.loginfo("Linkgraph loaded:\n%s" % linkgraph)

        # Requesting resource allocations
        self.wait_allocation(self._inquire_resources_to_allocate(linkgraph))

        return "Single Node Invocation Success"


    def wait_allocation(self, request_id):
        '''
        To wait until that resource allocations are done
        :param request_id:
        :return:
        '''

        # Setting a duration for waiting resource allocation callback
        timeout = ALLOCATION_TIMEOUT

        while(request_id in self.pending_requests):
            time.sleep(ALLOCATION_CHECKING_DURATION)
            if timeout > 0:
                timeout = timeout - 1
            else:
                self.release_allocated_resources()
                raise OSError("Timeout for Allocating Resources")
                sys.exit(1)


    def _convert_to_linkgraph(self, linkgraph):
        """
            Loading a linkgraph from input data and returns its name, and linkgraph

            :param str json: the link graph from service invocation msg

            @return name - name of linkgraph
            @rtype str
            @return linkgraph
            @rtype concert_msgs.msg.LinkGraph
        """
        lg = concert_msgs.LinkGraph()
        name = linkgraph['name'] if 'name' in linkgraph else 'Default'

        try:
            # Converting to linkgraph
            if 'nodes' in linkgraph:
                for node in linkgraph['nodes']:
                    node = node['Node']
                    node['min'] = node['min'] if 'min' in node else 1
                    node['max'] = node['max'] if 'max' in node else 1
                    node['force_name_matching'] = node['force_name_matching'] if 'force_name_matching' in node else False
                    node['parameters'] = node['parameters'] if 'parameters' in node else {}
                    lg.nodes.append(concert_msgs.LinkNode(node['id'], node['uri'], node['min'], node['max'], node['force_name_matching'],node['parameters']))
                for topic in linkgraph['topics']:
                    topic = topic['Topic']
                    lg.topics.append(concert_msgs.LinkConnection(topic['id'], topic['type']))
                if 'service' in linkgraph:
                    for service in linkgraph['services']:
                        service = service['Service']
                        lg.services.append(concert_msgs.LinkConnection(service['id'], service['type']))
                if 'actions' in linkgraph:
                    for action in linkgraph['actions']:
                        action = action['Action']
                        lg.actions.append(concert_msgs.LinkConnection(action['id'], action['type']))
                for edge in linkgraph['edges']:
                    edge = edge['Edge']
                    lg.edges.append(concert_msgs.LinkEdge(edge['start'], edge['finish'], edge['remap_from'], edge['remap_to']))
            else:
                node = linkgraph
                node['min'] = node['min'] if 'min' in node else 1
                node['max'] = node['max'] if 'max' in node else 1
                node['force_name_matching'] = node['force_name_matching'] if 'force_name_matching' in node else False
                node['parameters'] = node['parameters'] if 'parameters' in node else {}
                lg.nodes.append(concert_msgs.LinkNode(node['id'], node['uri'], node['min'], node['max'], node['force_name_matching'],node['parameters']))
        except TypeError as e:
            rospy.loginfo(e)
            sys.exit(1)

        return name, lg


################################################################
# Communication from the SOAP server to the BPEL engine
################################################################
    def _prepare_adapter2bpel_sub(self, methods):
        self.adapter2bpel_sub = []
        for m in methods:
            method = m['Method']
            rospy.loginfo("Prepare the method %s" % (method))
            sub = rospy.Subscriber("/"+NODE_NAME+"/"+method['name'], String, self.adapter2bpel_subs_callback, callback_args=method)


    def adapter2bpel_subs_callback(self, msg, callback_args=None):
        rospy.loginfo("Message Received (Adapter to BPEL): %s" % (msg))
        self.send_msg_to_bpel(callback_args['address'], callback_args['namespace'], callback_args['name'],
                              callback_args['param'], msg.__getattribute__('data'), callback_args['return_name'])


    def send_msg_to_bpel(self, address, namespace, method, param, msg, return_name):
        """
        To send a SOAP message to BPEL

        :param address:
        :param namespace:
        :param method:
        :param params:
        :param result_names:
        :return:
        """
        rospy.loginfo("===== Prepare a SOAP client =====")
        rospy.loginfo("Address: %s" % address)
        rospy.loginfo("NS: %s" % namespace)
        rospy.loginfo("=================================")

        client = SoapClient(
            location = address, # "http://localhost:8080/ode/processes/A2B"
            namespace = namespace, # "http://smartylab.co.kr/bpel/a2b"
            soap_ns='soap')
        rospy.loginfo("Sending the message to BPEL... %s(%s)" % (method, msg))
        # response = client.call(method, **(simplejson.loads(params)))
        param_dict = []
        param_dict[param] = msg
        response = client.call(method, **param_dict)
        rospy.loginfo("The message is sent.")
        return_name_splited = return_name.split('/')
        rospy.loginfo("Adapter to BPEL Result: %s" % (response[return_name_splited[0]][return_name_splited[1]]))


################################################################
# Resource allocation related methods
################################################################
    def _inquire_resources_to_allocate(self, linkgraph):
        """
        Let the requester allocate resources specified in the linkgraph

        :param linkgraph:
        :return:
        """

        rospy.loginfo("Allocating resources with the linkgraph...")

        result = False
        resource_list = []
        for node in linkgraph.nodes:
            rospy.loginfo("Allocating the resource:\n%s" % node)
            resource = self._gen_resource(node, linkgraph.edges)
            resource_list.append(resource)

        # Generating a request
        rospy.loginfo("Requesting the loaded resources...")
        request_id = self.requester.new_request(resource_list, uuid=self.service_id)

        # Pushing the request to pending_requests
        self.pending_requests[request_id] = linkgraph

        # Sending the request
        rospy.loginfo("The resources are requested with the id: %s" % request_id)
        self.requester.send_requests()

        return request_id


    def _on_resource_allocated(self, rset):
        """
        To get a notification from requester when resources are allocated
        :param rset: a set of requests
        :return:
        """
        rospy.loginfo("The resource is allocated:\n%s" % rset)

        for request_id, request in rset.requests.iteritems():
            if request.msg.status == scheduler_msgs.Request.GRANTED:

                # Removing the request from pending_requests
                linkgraph = self.pending_requests.pop(request_id)
                rospy.loginfo("====Allocated Publishers in Concert Adapter====")
                for topic in linkgraph.topics:
                    # Preparing a publisher
                    topic_name= self._remap_topic(topic.id, linkgraph.edges)
                    message_type=roslib.message.get_message_class(topic.type)
                    pub = rospy.Publisher(topic_name, message_type, queue_size=DEFAULT_QUEUE_SIZE)
                    rospy.loginfo("Topic: %s, Topic Type: %s" % (topic_name, message_type))
                    # Adding the allocated resources to allocated_resources
                    self.allocated_resources[topic_name] = pub


    def _call_resource(self, topic_name, msg_dict):
        """
        :param topic_name:
        :param msg:
        :return:
        """
        pub = self.allocated_resources[topic_name]
        msg_type = roslib.message.get_message_class(str(pub.type))
        msg_inst = msg_type()
        self.alloc_message(msg_inst, msg_dict)

        # Allocate message to msg_instance
        pub.publish(msg_inst)


    def _invoke_rapp(self, svc_name):
        if svc_name == "turtlebot":
            rospy.loginfo("invoke turtlebot teleop....")



    def alloc_message(self, msg_inst, msg_dict):
        for key in msg_dict.keys():
            if type(msg_dict[key]) is dict:
                self.alloc_message(getattr(msg_inst, key), msg_dict[key])
            else:
                setattr(msg_inst, key, msg_dict[key])


    def release_allocated_resources(self):
        self.requester.cancel_all()
        self.requester.send_requests()
        self.allocated_resources.clear()


    def _gen_resource(self, node, edges):
        """
        To convert linkgraph information for a particular node to a scheduler_msgs.Resource type

        :param node:
        :param edges:
        :return:
        """
        resource = scheduler_msgs.Resource()
        resource.rapp = rocon_uri.parse(node.resource).rapp
        resource.uri = node.resource
        resource.remappings = [rocon_std_msgs.Remapping(e.remap_from, e.remap_to) for e in edges if e.start == node.id or e.finish == node.id]

        return resource


    def _load_msg(self, type):
        try:
            components = type.split('/')
            components.insert(1, 'msg')
            module_path = ".".join(components[:-1])
            class_name = components[-1]
            rospy.loginfo("Loading the message... %s from %s" % (class_name, module_path))
            module = importlib.import_module(module_path)
            rospy.loginfo("The module is imported.")
            return getattr(module, class_name)
        except:
            rospy.loginfo("Loading failed.")
            return None


    def _remap_topic(self, prev_topic, edges):
        topics = [e.remap_to for e in edges if e.start == prev_topic]
        cnt= len(topics)
        if cnt > 0:
            if cnt > 1:
                rospy.loginfo("Duplicated remapping info. The first remapping is applied.")
            return topics[0]
        else:
            rospy.loginfo("No remapping info. The original topic name is returned.")
            return prev_topic


########################################################################################################
# Main method to launch the adapter
########################################################################################################


def callback(msg):
    rospy.loginfo(msg)


if __name__ == '__main__':
    rospy.loginfo("Starting the concert adapter...")

    rospy.init_node(NODE_NAME)

    adapter = ConcertAdapter()

    #ChatterTester(adapter).start()
    #TeleopTester(adapter).start()

    rospy.spin()

    if rospy.is_shutdown():
        adapter._stop_soap_server()
        adapter.release_allocated_resources()
