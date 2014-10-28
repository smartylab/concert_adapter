#!/usr/bin/python
"""
adapter/adapter.py v0.1

This module is to adapt requests from the BPEL engine to the concert framework.

Author: Jae Yoo Lee <jaeyoo1981@gmail.com>, Moon Kwon Kim <mkdmkk@gmail.com>
Since: 2014.10.20
"""

# Import Dependent Libraries
from BaseHTTPServer import HTTPServer
import sys
import threading
from pysimplesoap.server import SoapDispatcher, SOAPHandler
import yaml
import time
import importlib


# Import Dependent Modules of ROS, Rocon, and Concert
import rospy
import rocon_python_comms
import rocon_uri
import concert_service_utilities
import concert_scheduler_requests
import concert_service_link_graph


# Import Messages
import rocon_std_msgs.msg as rocon_std_msgs
import scheduler_msgs.msg as scheduler_msgs
import concert_msgs.msg as concert_msgs
import concert_scheduler_requests.common as scheduler_common


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
        'service_name',
        'service_description',
        'service_priority',
        'service_id',
        'allocation_timeout',
        'requester',
        'httpd',
        'pending_requests',
        'allocated_resources'
    ]


    def __init__(self):
        # Initialization
        (self.service_name, self.service_description, self.service_priority, self.service_id) = concert_service_utilities.get_service_info()
        self.allocation_timeout = rospy.get_param('allocation_timeout', 15.0)  # seconds

        # Checking the scheduler's KnownResources topic
        try:
            rocon_python_comms.find_topic('scheduler_msgs/KnownResources', timeout=rospy.rostime.Duration(5.0), unique=True)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("Could not locate the scheduler's known_resources topic. [%s]" % str(e))
            sys.exit(1)

        # Setting up the requester
        self._set_requester(self.service_id)
        self.pending_requests = dict()

        # Preparing a basket for storing allocated resources
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
# Communication between the BPEL engine and the SOAP server
################################################################
    def receive_service_invocation(self, LinkGraph):
        """
        To receive a service invocation including LinkGraph
        :param LinkGraph:
        :return string:
        """
        # Converting input data (LinkGraph)
        lg_name, linkgraph = self._convert_to_linkgraph(LinkGraph)
        rospy.loginfo("Sample linkgraph loaded:\n%s" % linkgraph)

        # Requesting resource allocations
        self.wait_allocation(self._inquire_resources_to_allocate(linkgraph))

        return "Hi"


    def receive_single_node_service_invocation(self, Node):
        '''
        To receive a service invocation for a single node
        :param Node:
        :return:
        '''
        # Converting input data to LinkGraph
        lg_name, linkgraph = self._convert_to_linkgraph(Node)
        rospy.loginfo("Sample linkgraph loaded:\n%s" % linkgraph)

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
                for resource in request.msg.resources:
                    # Preparing a publisher
                    pubs = [rospy.Publisher(
                        self._remap_topic(topic.id, linkgraph.edges),
                        self._load_msg(topic.type),
                        queue_size=DEFAULT_QUEUE_SIZE
                    ) for topic in linkgraph.topics]
                    rospy.loginfo("Publishers are generated for the resource, %s. [%s]" % (
                        resource.uri,
                        ", ".join(["%s - %s" % (pub.name, pub.data_class) for pub in pubs])))
                    # Adding the allocated resources to allocated_resources
                    self.allocated_resources[resource.uri] = {'resource': resource, 'publishers': pubs}


    def _call_resource(self, resource_id, msg):
        """

        :param resource_id:
        :param msg:
        :return:
        """
        pubs = self.allocated_resources[resource_id].publishers
        for pub in pubs:
            pub.publish(msg)


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


################################################################
# Testers (will be removed)
################################################################
class ConcertAdapterTester(threading.Thread):
    __slots__ = [
        'linkgraph'
    ]


    LINKGRAPH_YAML_LIST = {
        'chatter': """
            name: "Chatter Concert"
            nodes:
              - id: dudes
                uri: rocon:/*/*#rocon_apps/listener
                min: 2
                max: 4
              - id: dudette
                uri: rocon:/*/dudette#rocon_apps/talker
                parameters:
                  message: hello world
                  frequency: 15
            topics:
              - id: chatter
                type: std_msgs/String
            actions: []
            edges:
              - start: chatter
                finish: dudes
                remap_from: chatter
                remap_to: /conversation/chatter
              - start: dudette
                finish: chatter
                remap_from: chatter
                remap_to: /conversation/chatter
        """,
        'keyop': """
            name: "Kobuki Keyop"
            nodes:
              - id: keyop
                uri: rocon:/*/*#kobuki_keyop/keyop
                min: 1
                max: 1
            topics:
              - id: teleop
                type: kobuki_msgs/KeyboardInput
            actions: []
            edges: []
        """
    }


    def __init__(self, adapter, linkgraph_yaml_key):
        threading.Thread.__init__(self)
        linkgraph_yaml = yaml.load(self.LINKGRAPH_YAML_LIST[linkgraph_yaml_key])
        impl_name, impl = concert_service_link_graph.load_linkgraph_from_yaml(linkgraph_yaml)
        rospy.loginfo("Sample linkgraph loaded:\n%s" % impl)
        self.linkgraph = impl


    def run(self):
        time.sleep(10)
        rospy.loginfo("Allocating with the sample linkgraph...")
        adapter._inquire_resources_to_allocate(self.linkgraph)


########################################################################################################
# Main method to launch the adapter
########################################################################################################
if __name__ == '__main__':
    rospy.loginfo("Starting the concert adapter...")

    rospy.init_node(NODE_NAME)
    adapter = ConcertAdapter()

    ConcertAdapterTester(adapter, 'chatter').start() # to be removed

    rospy.spin()
    if rospy.is_shutdown():
        adapter._stop_soap_server()
        adapter.release_allocated_resources()
