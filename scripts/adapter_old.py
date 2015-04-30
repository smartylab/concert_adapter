#!/usr/bin/python
"""
adapter/adapter.py v0.1

This module is to adapt requests from the BPEL engine to the concert framework.

Authors: Jae Yoo Lee <jaeyoo1981@gmail.com>, Moon Kwon Kim <mkdmkk@gmail.com>, Chun Woo Park <cnsdnsla@gmail.com>
Since: 2015.01.26
"""


# Import Dependent Libraries
from BaseHTTPServer import HTTPServer

import sys
import threading

from pysimplesoap.server import SoapDispatcher, SOAPHandler
import time

import types
import json

import rospy
import rocon_python_comms
import rocon_uri
import concert_service_utilities
import concert_scheduler_requests

import roslib.message
import genpy.rostime

import actionlib

# Import Messages
import rocon_std_msgs.msg as rocon_std_msgs
import scheduler_msgs.msg as scheduler_msgs
import concert_msgs.msg as concert_msgs

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
        'linkgraph_info',
        'publishers',
        'action_clients_map',
        'service_proxies_map'
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
        self.linkgraph_info = dict()
        self.publishers = dict()
        self.action_clients_map = dict()
        self.service_proxies_map = dict()

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
                        'Node': {
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
                        'Topic': {
                            'id': str,
                            'type': str
                        }
                    }],
                    'actions': [{
                        'Action': {
                            'id': str,           # action id
                            'type': str,         # action specification
                            'goal_type': str     # goal message type
                        }
                    }],
                    'services': [{
                        'Service': {
                            'id': str,          # service id
                            'type': str,        # service class
                            'persistency': str  # persistency
                        }
                    }],
                    'edges': [{
                        'Edge': {
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
        dispatcher.register_function('send_topic_msg', self._send_topic_msg, returns={'out': str},
            args={
                'namespace': str,
                'message_val': str
            }
        )

        # To register a method for sending Action messages
        dispatcher.register_function('send_action_msg', self._send_action_msg, returns={'out': str},
            args={
                'namespace': str,
                'message_val': str
            }
        )


        # To register a method for sending Service messages
        dispatcher.register_function('send_service_msg', self._send_service_msg, returns={'out': str},
            args={
                'namespace': str,
                'message_val': str
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
        self.linkgraph_info = LinkGraph

        # Request resource allocations
        self.wait_allocation(self._inquire_resources_to_allocate(linkgraph))

        # Prepare subscribers for the adapter2bpel communication
        rospy.loginfo("Method info loaded:\n%s" % LinkGraph['methods'])
        self._prepare_adapter2bpel_sub(LinkGraph['methods'])

        return "Hi"


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
                if 'services' in linkgraph:
                    for service in linkgraph['services']:
                        service = service['Service']
                        lg.services.append(concert_msgs.LinkConnection(service['id'], str(service['type'])+"Request"))
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
                linkgraph = self.pending_requests.pop(request_id) #Typed as link_graph defined by Rocon

                rospy.loginfo("====Allocated Publishers in Concert Adapter====")
                for topic in linkgraph.topics:
                    # Preparing a publisher
                    topic_namespace= self._remap_namespace(topic.id, linkgraph.edges)
                    message_type=roslib.message.get_message_class(topic.type)
                    pub = rospy.Publisher(topic_namespace, message_type, queue_size=DEFAULT_QUEUE_SIZE)
                    rospy.loginfo("Topic: %s, Topic Type: %s" % (topic_namespace, message_type))
                    self.publishers[topic_namespace] = pub

                rospy.loginfo("====Allocated ActionClients in Concert Adapter====")
                for action in linkgraph.actions:
                    for action_info in self.linkgraph_info['actions']:
                        if action_info['Action']['id'] == action.id:
                            # Preparing a action client
                            action_namespace= self._remap_namespace(action_info['Action']['id'], linkgraph.edges)
                            action_type=roslib.message.get_message_class(action_info['Action']['type'])

                            action_client = actionlib.SimpleActionClient(action_namespace, action_type)

                            # Adding the action_client...
                            self.action_clients_map[action_namespace] = dict()
                            self.action_clients_map[action_namespace]['action_client'] = action_client

                            self.action_clients_map[action_namespace]['goal_type'] = action_info['Action']['goal_type']
                            break;

                rospy.loginfo("====Allocated ServiceProxies in Concert Adapter====")
                for service in linkgraph.services:
                    for service_info in self.linkgraph_info['services']:
                        if service_info['Service']['id'] == service.id:
                            # Preparing a service proxy
                            service_namespace= self._remap_namespace(service_info['Service']['id'], linkgraph.edges)
                            srv_cls = roslib.message.get_service_class(str(service_info['Service']['type']))
                            srv_req_cls = roslib.message.get_service_class(str(service_info['Service']['type'])+"Request")

                            self.service_proxies_map[service_namespace] = dict()
                            self.service_proxies_map[service_namespace]['service_proxy'] = rospy.ServiceProxy(service_namespace, srv_cls, service_info['Service']['persistency'])
                            self.service_proxies_map[service_namespace]['service_request_type'] = str(service_info['Service']['type'])+'Request'
                            break;

    def release_allocated_resources(self):
        self.requester.cancel_all()
        self.requester.send_requests()
        self.publishers.clear()
        self.action_clients_map.clear()
        self.service_proxies_map.clear()


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


    def _remap_namespace(self, prev_topic, edges):
        topics = [e.remap_to for e in edges if e.start == prev_topic]
        cnt= len(topics)
        if cnt > 0:
            if cnt > 1:
                rospy.loginfo("Duplicated remapping info. The first remapping is applied.")
            return topics[0]
        else:
            rospy.loginfo("No remapping info. The original topic name is returned.")
            return prev_topic


    def _send_topic_msg(self, namespace, message_val):
        """
        :param namespace:
        :param message_val:
        :return: result of publishing the topic
        """
        result = 'success'
        try:
            pub = self.publishers[namespace]
            msg_type = roslib.message.get_message_class(str(pub.type))
            msg_inst = self.alloc_msg(msg_type(), message_val)
            pub.publish(msg_inst)

        except ValueError:
            rospy.loginfo('Error on assigning value to an attribute')
            result = 'failure'
        except AttributeError:
            rospy.loginfo('Error on finding a specific attribute')
            result = 'failure'

        return result


    def _send_service_msg(self, namespace, message_val):
        """
        :param namespace:
        :param message_val:
        :return: result of invoking the service
        """

        resp = None
        try:
            srv_proxy_map = self.service_proxies_map[namespace]
            srv_proxy = srv_proxy_map['service_proxy']
            srv_req_type = roslib.message.get_service_class(str(srv_proxy_map['service_request_type']))

            srv_req = self.alloc_msg(srv_req_type(), message_val)

            resp = srv_proxy(srv_req)

        except rospy.ServiceException as exc:
            rospy.loginfo("ROS Service Executing failed." + str(exc))
        rospy.loginfo(resp)
        return resp


    def _send_action_msg(self, namespace, message_val):
        """
        :param namespace:
        :param message_val:
        :return: result of invoking the action
        """

        resp = None
        try:
            action_client_map = self.action_clients_map[namespace]
            action_client = action_client_map['action_client']
            action_client.wait_for_server()
            goal_cls = roslib.message.get_message_class(str(action_client_map['goal_type']))
            goal = self.alloc_msg(goal_cls(), message_val)

            action_client.send_goal(goal)
            action_client.wait_for_result()

            resp = action_client.get_result()
            rospy.loginfo(resp)

        except ValueError:
            rospy.loginfo('Error on assigning value to an attribute')
            result = 'failure'
        except AttributeError:
            rospy.loginfo('Error on finding a specific attribute')
            result = 'failure'

        return resp

    def alloc_msg(self, msg_inst, msg_val_str):
        """
        :param namespace:
        :param message_val:
        :return: result of invoking the action
        """

        msg_val = json.loads(msg_val_str)
        try:
            for key in msg_val.keys():
                msg_inst_attr = getattr(msg_inst, key)

                if type(msg_inst_attr) is types.BooleanType:
                    if(str(msg_val[key]).lower() is 'true'):
                        msg_inst_attr = True
                    elif(str(msg_val[key]).lower() is 'false'):
                        msg_inst_attr = False

                elif type(msg_inst_attr) is types.IntType:
                    msg_inst_attr = int(msg_val[key])

                elif type(msg_inst_attr) is types.LongType:
                    msg_inst_attr = long(msg_val[key])

                elif type(msg_inst_attr) is types.FloatType:
                    msg_inst_attr = float(msg_val[key])

                elif type(msg_inst_attr) is types.StringType:
                    msg_inst_attr = str(msg_val[key])

                elif type(msg_inst_attr) is genpy.rostime.Time:
                    msg_inst_attr = genpy.rostime.Time(int(msg_val[key]['secs']), int(msg_val[key]['nsecs']))

                elif type(msg_inst_attr) is genpy.rostime.Duration:
                    msg_inst_attr = genpy.rostime.Duration(int(msg_val[key]['secs']), int(msg_val[key]['nsecs']))

                elif type(msg_inst_attr) is types.TupleType:
                    msg_inst_attr = tuple(msg_val[key])

                elif type(msg_inst_attr) is types.ListType:
                    msg_inst_attr = list(msg_val[key])
                else:
                    msg_inst_attr = self.alloc_msg(msg_inst_attr, msg_val[key])

                setattr(msg_inst, key, msg_inst_attr)

        except ValueError:
            rospy.loginfo('Value error..')
            raise ValueError
        except AttributeError:
            rospy.loginfo('Attribute error..')
            raise AttributeError

        return msg_inst
########################################################################################################
# Main method to launch the adapter
########################################################################################################


def callback(msg):
    rospy.loginfo(msg)


if __name__ == '__main__':
    rospy.loginfo("Starting the concert adapter...")

    rospy.init_node(NODE_NAME)

    adapter = ConcertAdapter()
    rospy.spin()

    if rospy.is_shutdown():
        adapter.release_allocated_resources()
