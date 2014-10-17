#!/usr/bin/env python
#
# License: BSD
#   https://raw.github.com/robotics-in-concert/concert_services/license/LICENSE
#
##############################################################################
# Imports
##############################################################################

import sys
import threading
import time
import datetime

from std_msgs.msg import String

import rospy
import rocon_python_comms
import concert_service_utilities
import concert_scheduler_requests
import unique_id
import std_msgs.msg as std_msgs
import rocon_std_msgs.msg as rocon_std_msgs
import scheduler_msgs.msg as scheduler_msgs
import concert_service_msgs.msg as concert_service_msgs
import bpel_adapter.msg as adapter_msgs
import rospkg
import concert_service_link_graph
import rocon_uri

#from server import AdapterSOAPServer as soap_server
from server import Starter

from geometry_msgs.msg import Twist

##############################################################################
# Classes
##############################################################################

class AdapterSvc:
    '''
      Listens for requests to gain a robot.
    '''
    __slots__ = [
        'service_name',
        'service_description',
        'service_priority',
        'service_id',
        'allocation_timeout',
        # 'allocated_resources',
        'requester',
        'linkgraph',
        'lock',
        'pending_requests',   # a list of request id's pending feedback from the scheduler
        'allocated_requests'  # dic of { rocon uri : request id } for captured teleop robots.
        'bpel_service_subscriber',
        'bpel_package_subscriber',
        'bpel_command_subscriber'
    ]

    def __init__(self):
        ####################
        # Initiate SOAP Server
        ####################
        self.soap_server = Starter()
        self.soap_server.daemon = True
        self.soap_server.start()

        ####################
        # Discovery
        ####################
        (self.service_name, self.service_description, self.service_priority, self.service_id) = concert_service_utilities.get_service_info()
        try:
            known_resources_topic_name = rocon_python_comms.find_topic('scheduler_msgs/KnownResources', timeout=rospy.rostime.Duration(5.0), unique=True)
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("AdapterSvc : could not locate the scheduler's known resources topic [%s]" % str(e))
            sys.exit(1)

        ####################
        # Setup
        ####################
        self.allocated_resources = []
        self.requester = self.setup_requester(self.service_id)
        self.lock = threading.Lock()
        self.pending_requests = []
        self.allocated_requests = {}
        self.command=''

        # Subscribe a topic from BPEL service
        self.bpel_package_subscriber = rospy.Subscriber('package_find_request', String, self.package_find_request_callback)
        self.bpel_service_subscriber = rospy.Subscriber('resources_alloc_request', adapter_msgs.Adapter, self.resources_alloc_request_callback)
        self.bpel_command_subscriber = rospy.Subscriber('command_request', adapter_msgs.Adapter, self.command_request_callback)

        self.allocation_timeout = rospy.get_param('allocation_timeout', 15.0)  # seconds

    def setup_requester(self, uuid):
        try:
            scheduler_requests_topic_name = concert_service_utilities.find_scheduler_requests_topic()
            #rospy.loginfo("Service : found scheduler [%s][%s]" % (topic_name))
        except rocon_python_comms.NotFoundException as e:
            rospy.logerr("AdapterSVC : %s" % (str(e)))
            return  # raise an exception here?
        frequency = concert_scheduler_requests.common.HEARTBEAT_HZ
        return concert_scheduler_requests.Requester(self.requester_feedback, uuid, 0, scheduler_requests_topic_name, frequency)

    def package_find_request_callback(self, msg):
        '''
            For setting a linkgraph, this method is invoked by receiving a package name from bpel service
            @param msg: incoming message
            @type msg: std_msgs.msg (String)
        '''
        pkg_name = msg.data
        rospack = rospkg.RosPack()
        ###################################
        # To get a linkgraph by loading a linkgraph file using package name
        # Remaining address to the linkgraph file should be discussed to make a pattern
        # Currently there is no pattern for the address
        ###################################
        address_linkgraph = rospack.get_path(pkg_name) + "/services/chatter/chatter.link_graph"

        impl_name, impl = concert_service_link_graph.load_linkgraph_from_file(address_linkgraph)
        self.linkgraph = impl

    def resources_alloc_request_callback(self, msg):
        '''
          For allocating a resource, this method is invoked by receiving a topic from bpel service
          @param msg: incoming message
          @type msg: adapter_msgs.Adapter
        '''
        result = False
        # send a request
        rospy.loginfo("AdapterSvc : send request by topic")

        resource_list = []
        for node in self.linkgraph.nodes:

            print '============================================================'
            print node
            print '============================================================'

            #if node.uri == msg.resource.uri:
            if node.resource == msg.resource.uri:
                resource = self._node_to_resource(node, self.linkgraph)
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

    def command_request_callback(self, msg):
        '''
          Called by Subscriber when the command arrived from the server
          This method control the turtlebot according to the command
          @param msg : incoming message
          @type msg: adapter_msgs.Adapter
        '''
        topic_name = "/turtlebot/android/virtual_joystick/cmd_vel"
        publisher = rospy.Publisher(topic_name, Twist)

        data = adapter_msgs.Adapter()

        command = msg.command
        command_duration = msg.command_duration

        print "=================================================="
        rospy.loginfo("command_request_callback : ready to send cmd_vel to turtlebot")
        rospy.loginfo("command_request_callback : command = %s", command)
        print "=================================================="
        twist = Twist();

        if command == 'forward':
            print 'in forward'
            twist.linear.x = 0.1; twist.linear.y = 0; twist.linear.z = 0
            twist.angular.x = 0; twist.angular.y = 0; twist.angular.z = 0

        elif command == 'backward':
            print 'in backward'
            twist.linear.x = -0.1; twist.linear.y = 0; twist.linear.z = 0
            twist.angular.x = 0; twist.angular.y = 0; twist.angular.z = 0

        elif command == 'cycle_left':
            print 'left'
            twist.linear.x = 0; twist.linear.y = 0; twist.linear.z = 0
            twist.angular.x = 0; twist.angular.y = 0; twist.angular.z = 1

        elif command == 'cycle_right':
            print 'right'
            twist.linear.x = 0; twist.linear.y = 0; twist.linear.z = 0
            twist.angular.x = 0; twist.angular.y = 0; twist.angular.z = -1

        print "=================================================="
        rospy.loginfo("command_request_callback : command value : ")
        print command
        rospy.loginfo("command_request_callback : command duration value : ")
        print command_duration
        rospy.loginfo("command_request_callback : published twist value : ")
        print twist

        r = rospy.Rate(10)
        stop_time = time.time()+command_duration

        # Publish Twist!
        while time.time() < stop_time:
            publisher.publish(twist)
            r.sleep()

        print "=================================================="
        command_reply_publisher = rospy.Publisher('/services/adapter/command_reply', std_msgs.String)
        time.sleep(1)
        command_reply_publisher.publish("command_success")

    def set_topic_for_resource(self, msg):
        '''
          Set a topic for resource

          @param msg : incoming message
          @type msg: scheduler_msgs.ServiceMsg
        '''
        topic_name = "/services/adapter/required_resources"
        # rostopic find topic_name
        try:
            # assuming all topics here come in as /x/y/z/topicname or /x/y/z/topicname_355af31d
            topic_names = rocon_python_comms.find_topic('scheduler_msgs/SchedulerRequests', timeout=rospy.rostime.Duration(5.0), unique=False)
            topic_name = min(topic_names, key=len)
        except rocon_python_comms.NotFoundException:
            raise rocon_python_comms.NotFoundException("couldn't find the concert scheduler topics, aborting")

    def requester_feedback(self, request_set):
        '''
          Keep an eye on our pending requests and see if they get allocated here.
          Once they do, kick them out of the pending requests list so _ros_capture_teleop_callback
          can process and reply to the interaction.

          @param request_set : the modified requests
          @type dic { uuid.UUID : scheduler_msgs.ResourceRequest }
        '''
        for request_id, request in request_set.requests.iteritems():

            if request.msg.status == scheduler_msgs.Request.GRANTED:
                if request_id in self.pending_requests:
                    self.pending_requests.remove(request_id)

                    resource_alloc_reply_publisher = rospy.Publisher('/services/adapter/resources_alloc_reply', std_msgs.String)
                    time.sleep(1)
                    resource_alloc_reply_publisher.publish("resource_alloc_success")
                    print "resource_alloc_reply is sent"
            elif request.msg.status == scheduler_msgs.Request.CLOSED:
                self.pending_requests.remove(request_id)
                self.granted_requests.remove(request_id)

    def cancel_all_requests(self):
        '''
          Exactly as it says! Used typically when shutting down or when
          it's lost more allocated resources than the minimum required (in which case it
          cancels everything and starts reissuing new requests).
        '''
        #self.lock.acquire()
        self.requester.cancel_all()
        self.requester.send_requests()
        #self.lock.release()

    def _node_to_resource(self, node, linkgraph):
        '''
          Convert linkgraph information for a particular node to a scheduler_msgs.Resource type.

          @param node : a node from the linkgraph
          @type concert_msgs.LinkNode

          @param linkgraph : the entire linkgraph (used to lookup the node's edges)
          @type concert_msgs.LinkGraph

          @return resource
          @rtype scheduler_msgs.Resource
        '''
        resource = scheduler_msgs.Resource()
        resource.rapp = rocon_uri.parse(node.resource).rapp
        resource.uri = node.resource
        resource.remappings = [rocon_std_msgs.Remapping(e.remap_from, e.remap_to) for e in linkgraph.edges if e.start == node.id or e.finish == node.id]
        return resource

##############################################################################
# Launch point
##############################################################################

if __name__ == '__main__':
    rospy.init_node('adapter_svc')
    adapter = AdapterSvc()
    rospy.spin()
    if not rospy.is_shutdown():
        adapter.cancel_all_requests()
