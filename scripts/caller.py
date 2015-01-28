#!/usr/bin/python

import simplejson
import rospy
from std_msgs.msg import String

def callback(msg, callback_args):
    rospy.loginfo(msg)

def caller():
    pub = rospy.Publisher('concert_adapter_invoke_first_service', String, queue_size=10)
    sub = rospy.Subscriber('concert_adapter_invoke_first_service', String, callback, callback_args = {})
    rospy.init_node('adapter2bpel_caller', anonymous=True)
    rospy.loginfo("Testing Say Hello...")
    pub.publish(String(simplejson.dumps({'in':"Hello BPEL."})))
    pub.publish(String(simplejson.dumps({'in':"Hello BPEL."})))
    pub.publish(String(simplejson.dumps({'in':"Hello BPEL."})))
    pub.publish(String(simplejson.dumps({'in':"Hello BPEL."})))
    rospy.spin()

if __name__ == '__main__':
    try:
        caller()
    except rospy.ROSInterruptException:
        pass