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
DEFAULT_QUEUE_SIZE = 1024
ALLOCATION_TIMEOUT = 15.0 # seconds

class Adapter:
    __slots__ = [
        'service_name',
        'service_description',
        'service_priority',
        'service_id',
        'allocation_timeout',
        'requester',
        'linkgraph',
        'lock',
        'pending_requests',   # a list of request id's pending feedback from the scheduler
        'allocated_requests'  # dic of { rocon uri : request id } for captured teleop robots.
        'concert_adapter_service_subscriber',
        'concert_adapter_command_subscriber'
    ]

    def __init__(self):
        """

        :return:
        """
        # Starting SOAP server
        # Setup
        pass


    def allocate(self, linkgraph):
        """

        :param resource:
        :return:
        """

        result = False
        print("[concert_adapter/adapter] allocating resources...")
        resource_list = []
        for node in linkgraph.nodes:
            print("[concert_adapter/adapter] allocating the resource: %s" % node)
            resource = self.gen_resource(node, linkgraph)
            resource_list.append(resource)
            resource_request_id = self.requester.new_request(resource_list)

        self.pending_requests.append(resource_request_id)
        self.requester.send_requests()
        timeout_time = time.time() + self.allocation_timeout

        while not rospy.is_shutdown() and time.time() < timeout_time:
            if resource_request_id in self.pending_requests:
                self.allocated_requests[msg.resource.uri] = resource_request_id
                result = True
                break
            rospy.rostime.wallsleep(0.1)

        if result == False:
            rospy.logwarn("AdapterSvc : couldn't capture required resource [timed out][%s]" % msg.resource.uri)
            self.requester.rset[resource_request_id].cancel()
        else:
            rospy.loginfo("AdapterSvc : captured resouce [%s][%s]" % (msg.resource.uri, self.allocated_requests[msg.resource.uri]))


    def gen_resource(self, node, edges):
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

    def cancel_all_requests(self):
        """
          Exactly as it says! Used typically when shutting down or when
          it's lost more allocated resources than the minimum required (in which case it
          cancels everything and starts reissuing new requests).
        """
        #self.lock.acquire()
        self.requester.cancel_all()
        self.requester.send_requests()
        #self.lock.release()

"""
Main method; To launch the ROS node
"""
if __name__ == '__main__':
    rospy.init_node(NODE_NAME)
    adapter = Adapter()
    rospy.spin()
    if not rospy.is_shutdown():
        adapter.cancel_all_requests()
