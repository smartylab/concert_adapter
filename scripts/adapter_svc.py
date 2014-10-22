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


# Constants
NODE_NAME = 'adapter'
LOGGING_TAG = NODE_NAME + "/adapter"
DEBUGING_TAG = "[DEBUG]" + LOGGING_TAG
DEFAULT_QUEUE_SIZE = 1024
SOAP_SERVER_HOST_NAME = 'localhost'
SOAP_SERVER_PORT_NUMBER = '8008'


class ConcertAdapter(object):
    __slots__ = [
        'soap_server',
        'service_name',
        'service_description',
        'service_priority',
        'service_id',
        'allocation_timeout',
        'requester'
    ]


    def __init__(self):
        # Initialization
        (self.service_name, self.service_description, self.service_priority, self.service_id) = concert_service_utilities.get_service_info()
        self.allocation_timeout = rospy.get_param('allocation_timeout', 15.0)  # seconds
        # Checking the scheduler's KnownResources topic
        try:
            rocon_python_comms.find_topic('scheduler_msgs/KnownResources', timeout=rospy.rostime.Duration(5.0), unique=True)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("%s: Could not locate the scheduler's known_resources topic. [%s]" % (LOGGING_TAG, str(e)))
            sys.exit(1)
        # Setting up the requester
        self._set_requester(self.service_id)
        # Starting the SOAP server
        self.start_soap_server


#####################################################################
# Preparation for adaptation: SOAP Server
#####################################################################
    def start_soap_server(self):
        """
        To launch a SOAP server as a thread
        :return:
        """
        threading.Thread(target=self._soap_server_worker).start()


    def _soap_server_worker(self):
        """
        To launch a SOAP server for the adapter
        :return:
        """
        dispatcher = SoapDispatcher('op_adapter_soap_disp', location = self.address, action = self.address,
                namespace = "http://smartylab.co.kr/products/op/adapter", prefix="tns", trace = True, ns = True)
        dispatcher.register_function('adapt', self.on_service_invocation_received, returns={'out': str},
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
        # Single Node Registeration
        #
        print("%s: Starting a SOAP server...", LOGGING_TAG)
        httpd = HTTPServer(("", int(SOAP_SERVER_PORT_NUMBER)), SOAPHandler)
        httpd.dispatcher = dispatcher

        print("%s: The SOAP server started. [%s:%s]" % (LOGGING_TAG, SOAP_SERVER_HOST_NAME, SOAP_SERVER_PORT_NUMBER))
        httpd.serve_forever()


#####################################################################
# Preparation for adaptation: Requester
#####################################################################
    def _set_requester(self, uuid):
        """
        To set a requester
        :param uuid:
        :return:
        """
        try:
            scheduler_requests_topic = concert_service_utilities.find_scheduler_requests_topic()
            self.requester = concert_scheduler_requests.Requester(self._on_requester_reply_received, uuid=self.service_id, topic=scheduler_requests_topic)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("%s: Could not locate the scheduler's scheduler_requests topic. [%s]" % LOGGING_TAG, str(e))
            sys.exit(1)


#####################################################################
# Communication between the BPEL engine and the SOAP server
#####################################################################
    def _on_service_invocation_received(self, linkgraph):
        # To validate the linkgraph
        #
        # To allocate resources
        self._inquire_resources_to_allocate(linkgraph)


    def _on_single_node_service_invocation_received(self, node):
        pass


#####################################################################
# Resource allocation related methods
#####################################################################
    def _on_requester_reply_received(self, request_set):
        for request_id, request in request_set.requests.iteritems():

            if request.msg.status == scheduler_msgs.Request.GRANTED:
                if request_id in self.pending_requests:
                    self.pending_requests.remove(request_id)
                    # Do more...
                    #
            elif request.msg.status == scheduler_msgs.Request.CLOSED:
                self.pending_requests.remove(request_id)
                self.granted_requests.remove(request_id)


    def _inquire_resources_to_allocate(self, linkgraph):
        """
        Let the requester allocate resources specified in the linkgraph

        :param linkgraph:
        :return:
        """

        print("%s: linkgraph [%s]" % (DEBUGING_TAG, linkgraph))

        result = False
        print("%s: allocating resources..." % LOGGING_TAG)
        resource_list = []
        for node in linkgraph.nodes:
            print("%s: allocating the resource [%s]" % LOGGING_TAG, node)
            resource = self._gen_resource(node, linkgraph)
            resource_list.append(resource)

        # Calling requester
        self.requester.new_request(resource_list)
        self.requester.send_requests()


    def _on_resource_allocated(self, msg):
        pass


    def _call_resource(self, resource, params):
        """

        :param resource:
        :param params:
        :return:
        """
        pass


    def release_allocated_resources(self):
        #self.lock.acquire()
        self.requester.cancel_all()
        self.requester.send_requests()
        #self.lock.release()


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


#####################################################################
# Tester (will be removed)
#####################################################################
class Tester(threading.Thread):
    def __init__(self, adapter):
        chatter_linkgraph_yaml = yaml.load("""
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
        """)
        impl_name, impl = concert_service_link_graph.load_linkgraph_from_yaml(chatter_linkgraph_yaml)
        adapter._inquire_resources_to_allocate(impl)


    def run(self):
        pass


#####################################################################
# Main method to launch the adapter
#####################################################################
if __name__ == '__main__':
    print("%s: Starting the concert adapter..." % LOGGING_TAG)
    rospy.loginfo("%s: Starting the concert adapter..." % LOGGING_TAG)
    rospy.init_node(NODE_NAME)
    adapter = ConcertAdapter()
    #tester = Tester(adapter) # to be removed
    rospy.spin()
    if not rospy.is_shutdown():
        adapter.release_allocated_resources()