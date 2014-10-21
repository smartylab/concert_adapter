#!/usr/bin/python
"""
concert_adapter/adapter.py v0.1

This module is to adapt requests from the BPEL engine to the concert framework.

Author: Jae Yoo Lee <jaeyoo1981@gmail.com>, Moon Kwon Kim <mkdmkk@gmail.com>
Since: 2014.10.20
"""

# Import Dependent Libraries
from BaseHTTPServer import HTTPServer
import sys
import threading
import time
from pysimplesoap.server import SoapDispatcher, SOAPHandler
import unique_id
import yaml


# Import Dependent Modules of ROS, Rocon, and Concert
import rospy
import rospkg
import rocon_python_comms
import rocon_uri
import concert_service_utilities
import concert_scheduler_requests
import concert_service_link_graph


# Import Dependent Modules of Concert Adapter
from server import AdapterSOAPServer


# Import Messages
import std_msgs.msg.String
import concert_service_msgs.msg as concert_service_msgs
import rocon_std_msgs.msg as rocon_std_msgs
import scheduler_msgs.msg as scheduler_msgs


# Constants
NODE_NAME = 'concert_adapter'
LOGGING_TAG = NODE_NAME + "/adapter"
DEFAULT_QUEUE_SIZE = 1024
SOAP_SERVER_HOST_NAME = 'localhost'
SOAP_SERVER_PORT_NUMBER = '8008'


class ConcertAdapter:
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
        self.set_requester(self.service_id)
        # Starting the SOAP server
        self.start_soap_server


#####################################################################
# SOAP server related methods
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
                args={'nodeID': str, 'resourceName': str, 'duration': str, 'options': str})
        print("%s: Starting a SOAP server...", LOGGING_TAG)
        httpd = HTTPServer(("", int(SOAP_SERVER_PORT_NUMBER)), SOAPHandler)
        httpd.dispatcher = dispatcher

        print("%s: The SOAP server started. [%s:%s]" % (LOGGING_TAG, SOAP_SERVER_HOST_NAME, SOAP_SERVER_PORT_NUMBER))
        httpd.serve_forever()


    def on_service_invocation_received(self, linkgraph):
        # To validate the linkgraph
        #
        # To allocate resources
        self.inquire_resources_to_allocate(linkgraph)


#####################################################################
# Resource allocation related methods
#####################################################################
    def set_requester(self, uuid):
        """
        To set a requester
        :param uuid:
        :return:
        """
        try:
            scheduler_requests_topic = concert_service_utilities.find_scheduler_requests_topic()
            self.requester = concert_scheduler_requests.Requester(self.on_requester_reply_received, uuid=self.service_id, topic=scheduler_requests_topic)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("%s: Could not locate the scheduler's scheduler_requests topic. [%s]" % LOGGING_TAG, str(e))
            sys.exit(1)


    def on_requester_reply_received(self, request_set):
        for request_id, request in request_set.requests.iteritems():

            if request.msg.status == scheduler_msgs.Request.GRANTED:
                if request_id in self.pending_requests:
                    self.pending_requests.remove(request_id)
                    # Do more...
                    #
            elif request.msg.status == scheduler_msgs.Request.CLOSED:
                self.pending_requests.remove(request_id)
                self.granted_requests.remove(request_id)


    def inquire_resources_to_allocate(self, linkgraph):
        """
        Let the requester allocate resources specified in the linkgraph

        :param linkgraph:
        :return:
        """

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


    def on_resource_allocated(self, msg):
        pass


    def call_resource(self, resource, params):
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
# Main method to launch the concert_adapter
#####################################################################
if __name__ == '__main__':
    rospy.init_node(NODE_NAME)
    adapter = ConcertAdapter()
    rospy.spin()
    if not rospy.is_shutdown():
        adapter.cancel_all_requests()
