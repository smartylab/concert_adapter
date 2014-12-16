import yaml
import concert_service_link_graph
import threading
import rospy
import time


__author__ = 'mkk'


class ChatterTester(threading.Thread):
    __slots__ = [
        'linkgraph'
    ]


    def __init__(self, adapter):
        threading.Thread.__init__(self)
        self.adapter = adapter
        linkgraph_yaml = yaml.load("""
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
        impl_name, impl = concert_service_link_graph.load_linkgraph_from_yaml(linkgraph_yaml)
        rospy.loginfo("Sample linkgraph loaded:\n%s" % impl)
        self.linkgraph = impl


    def run(self):
        time.sleep(10)
        rospy.loginfo("Allocating with the sample linkgraph...")
        self.adapter._inquire_resources_to_allocate(self.linkgraph)


class TeleopTester(threading.Thread):
    __slots__ = [
        'linkgraph'
    ]


    def __init__(self, adapter):
        threading.Thread.__init__(self)
        self.adapter = adapter
        linkgraph_yaml = yaml.load("""
            name: "Teleop"
            nodes:
              - id: turtlebot
                uri: rocon:/turtlebot
                min: 1
                max: 1
            topics:
              - id: teleop
                type: geometry_msgs/Twist
            actions: []
            edges:
              - start: teleop
                finish: turtlebot
                remap_from: teleop
                remap_to: /conversation/teleop
              - start: turtlebot
                finish: teleop
                remap_from: teleop
                remap_to: /conversation/teleop
        """)
        impl_name, impl = concert_service_link_graph.load_linkgraph_from_yaml(linkgraph_yaml)
        rospy.loginfo("Sample linkgraph loaded:\n%s" % impl)
        self.linkgraph = impl


    def run(self):
        time.sleep(10)
        rospy.loginfo("Allocating with the sample linkgraph...")
        self.adapter._inquire_resources_to_allocate(self.linkgraph)

