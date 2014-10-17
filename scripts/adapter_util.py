#!/usr/bin/env python

import roslib
#roslib.load_manifest('turtlebot_teleop')
import rospy
import time

import sys, select, termios, tty

from std_msgs.msg import String

import unique_id
import scheduler_msgs.msg as scheduler_msgs
from geometry_msgs.msg import Twist
import bpel_adapter.msg as adapter_msgs

import Queue
import threading
import time, random

queue = Queue.Queue(3)

package_find_pub_topic = '/services/adapter/package_find_request'
package_find_sub_topic = '/services/adapter/package_find_reply'

resource_alloc_pub_topic = '/services/adapter/resources_alloc_request'
resource_alloc_sub_topic = '/services/adapter/resources_alloc_reply'

command_pub_topic = '/services/adapter/command_request'
command_sub_topic = '/services/adapter/command_reply'

DEFAULT_QUEUE_SIZE = 1024

class AdapterUtility:
    def __init__(self):

        #rospy.init_node('ssel_node', anonymous=True)

        self.package_find_pub = rospy.Publisher(package_find_pub_topic, String, queue_size=DEFAULT_QUEUE_SIZE)
        self.package_find_sub = rospy.Subscriber(package_find_sub_topic, String, self.package_find_callback)

        self.resource_alloc_pub = rospy.Publisher(resource_alloc_pub_topic, adapter_msgs.Adapter, queue_size=DEFAULT_QUEUE_SIZE)
        self.resource_alloc_sub = rospy.Subscriber(resource_alloc_sub_topic, String, self.resource_alloc_callback)
        self.resource_alloc_flag = False

        self.command_pub = rospy.Publisher(command_pub_topic, adapter_msgs.Adapter, queue_size=DEFAULT_QUEUE_SIZE)
        self.command_sub = rospy.Subscriber(command_sub_topic, String, self.command_callback)
        self.command_flag = False

    def allocate(self, resource):
        queue.put(resource)
        print 'queue:', queue
        Allocator(queue.get()).start()

    def rapp_parse(self, uri):
        rapp = uri.split('#')[1]
        return rapp

    def pub_package_find(self, packageName):
        print packageName
        self.package_find_flag = False

        time.sleep(1)
        self.package_find_pub.publish(packageName)

    def package_find_callback(self, msg):
        if msg.data=="package_find_success":
            self.package_find_flag = True
        else:
            self.package_find_flag = False
        return


    '''
    Called by Publisher to allocate a resource
    '''
    def pub_resource_alloc(self, uri, options):
        print 'uri: ', uri, ' options: ', options
        self.resource_alloc_flag = False
        data = adapter_msgs.Adapter()
        data.resource.id = unique_id.toMsg(unique_id.fromRandom())
        data.resource.uri = uri
        data.resource.rapp = self.rapp_parse(uri)
        data.command = options

        print data

        time.sleep(1)
        self.resource_alloc_pub.publish(data)

    '''
    Called by Subscriber when the resource infomation arrived from the adapter
    '''
    def resource_alloc_callback(self, msg):
        if msg.data=="resource_alloc_success":
            self.resource_alloc_flag= True
        else:
            self.resource_alloc_flag= False
        return


    '''
    Called by Publisher to control the robot with command during 'duration'
    '''
    def pub_command(self, duration, options):
        self.command_flag = False
        data = adapter_msgs.Adapter()
        data.resource.id = unique_id.toMsg(unique_id.fromRandom())
        data.command_duration = int(duration)
        data.command = options

        print data

        time.sleep(1)
        self.command_pub.publish(data)

    '''
    Called by Subscriber when the command arrived from the adapter
    '''
    def command_callback(self, msg):
        print "==============publisher.py (command_callback)=============="
        print "msg.data: ", msg.data
        if msg.data=="command_success":
            self.command_flag= True
        else:
            self.command_flag= False
        return


class Allocator(threading.Thread):

    def __init__(self, resource):
        self.resource = resource
        self.adapter_utility = AdapterUtility()
        self.resource_alloc_flag = False
        threading.Thread.__init__(self)
        print 'start'

    def run(self):
        item = self.resource
        if item is not None:

            if item['options'] == 'package_name':
                self.adapter_utility.pub_package_find(item['resourceName'])
            else:
                # pretend we're doing something that takes 10-100 ms
                time.sleep(random.randint(10, 100)/ 100.0)
                print "===================server - Resource Allocating ==================="
                time.sleep(1)
                print item['resourceName'], " / ", item['options']
                self.adapter_utility.pub_resource_alloc(item['resourceName'], item['options'])

                while self.resource_alloc_flag != True:
                    time.sleep(0.5)
                    self.resource_alloc_flag = self.adapter_utility.resource_alloc_flag
                    #print "Receive reply of the allocation request"

            print "[[TASK]] ", item, "finished"
