#!/usr/bin/python
"""
concert_adapter/adapter.py v0.1

This module is to adapt requests from the BPEL engine to the concert framework.

Author: Jae Yoo Lee <jaeyoo1981@gmail.com>, Moon Kwon Kim <mkdmkk@gmail.com>
Since: 2014.10.20
"""

# Import Dependent Libraries
import sys
import threading
import time
import unique_id
import yaml


# Import Dependent Modules of ROS/Rocon/Concert
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
import concert_adapter.msg as adapter_msgs


# Constants
NODE_NAME = 'concert_adapter'
LOGGING_TAG = NODE_NAME + "/adapter"
DEFAULT_QUEUE_SIZE = 1024


class ConcertAdapter:
    __slots__ = [
        'service_name',
        'service_description',
        'service_priority',
        'service_id',
        'allocation_timeout',
        'requester',
        'pending_requests',
        'granted_requests'
    ]


    def __init__(self):
        # Initialization
        (self.service_name, self.service_description, self.service_priority, self.service_id) = concert_service_utilities.get_service_info()
        self.allocation_timeout = rospy.get_param('allocation_timeout', 15.0)  # seconds


        # Checking the scheduler's KnownResources topic
        try:
            rocon_python_comms.find_topic('scheduler_msgs/KnownResources', timeout=rospy.rostime.Duration(5.0), unique=True)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("%s: Could not locate the scheduler's known_resources topic. [%s]" % LOGGING_TAG, str(e))
            sys.exit(1)


        # Setting up the requester
        try:
            scheduler_requests_topic = concert_service_utilities.find_scheduler_requests_topic()
            self.requester = concert_scheduler_requests.Requester(self.requester_feedback, self.service_id, 0, scheduler_requests_topic, concert_scheduler_requests.common.HEARTBEAT_HZ)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("%s: Could not locate the scheduler's scheduler_requests topic. [%s]" % LOGGING_TAG, str(e))
            sys.exit(1)

        # Starting the SOAP server
        #


    def inquire_resources_to_allocate(self, linkgraph):
        """ -> allocate_resources
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

        #
        # call requester


    def set_allocated_resources(self, resource, params):
        """ -> on_resources_allocated
        Callback method for the requester

        :param resource:
        :param params:
        :return:
        """
        pass


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


# Main method to launch the concert_adapter
if __name__ == '__main__':
    rospy.init_node(NODE_NAME)
    adapter = Adapter()
    rospy.spin()
    if not rospy.is_shutdown():
        adapter.cancel_all_requests()
