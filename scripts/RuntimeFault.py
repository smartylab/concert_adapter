
# Import Dependent Modules of ROS, Rocon, and Concert
import rospy
import rocon_python_comms
import rocon_uri
import concert_service_utilities
import concert_scheduler_requests
import concert_service_link_graph
import roslib.message

import RuntimeFaultHandler as handler
from adapter import ConcertAdapter as adapter

class Fault(Exception):
    """
    Base class for runtime errors handled by Adapter
    """
    def __init__(self, adapter_class, cause, handler_class):
        '''
        Initiate a Fault object with its cause and handler
        :param adapter: concert adapter object which is currently running on ROCON
        :param cause: cause of this fault
        :param handler_class: concrete fault handler object
        :return:
        '''
        self.adapter = adapter_class
        self.handler = handler_class
        self.cause = cause

        rospy.loginfo(cause + "Fault is raised...")

    # To return the cause of this fault
    def getCause(self):
        return self.cause

    # To report fault information using a log message of ROS
    def printErrorMessage(self, msg):
        rospy.loginfo("Cause[%s] : %s", self.cause, msg)


# Concrete Fault Class to raise Resource Scheduler Not Available Fault
class ResourceSchedulerNotAvailableFault(Fault):
    """
    Error raised when the resource scheduler is not available

    """
    def __init__(self, adapter):
        '''
        :param adapter: concert adapter object which is currently running on ROCON
        :return:
        '''

        # To call __init__ method of super class (Fault) to set
        # cause of fault and its handler
        Fault.__init__(self, adapter, 'Resource Scheduler', handler.FaultReporter(adapter))

        rospy.loginfo("ResourceSchedulerNotAvailableFault is raised...")

        ################################
        ### execute a remedy process ###
        ################################

        rospy.loginfo()
        rospy.loginfo("########## ResourceSchedulerNotAvailableFault ##########")

        # set method and error message
        self.method = "errorReport" # name of BPEL method
        self.msg = "Error: " + self.cause + " is not available..."

        # Step 1. print an error message
        rospy.loginfo("########## Step 1. print an error message ##########")
        self.printErrorMessage(self.msg)

        # Step 2. Report Error message to BPEL by using the handler object
        # call doRemedyAction of fault handler
        rospy.loginfo("########## Step 2. Report Error message to BPEL by using the handler object ##########")
        self.handler.doRemedyAction(self.method, self.msg)


class ResourceAllocationTimeOutFault(Fault):
    """
    Error raised when allocating resources is delayed
    """
    occurrence = False

    def __init__(self, adapter, linkgraph):
        '''
        :param adapter: concert adapter object which is currently running on ROCON
        :param linkgraph: a linkgraph containing the rapp which is not allocated within a given period of time
        :return:
        '''
        Fault.__init__(self, adapter, 'Resource Allocation', handler.TopicResender(adapter))
        self.linkgraph = linkgraph

        ################################
        ### execute a remedy process ###
        ################################

        # set method and error message
        self.method = "errorReport"
        self.msg = "Error: " + self.cause + " is not finished within 1 second..."

        rospy.loginfo("########## ResourceAllocationTimeOutFault ##########")

        # Step 1. print an error message
        rospy.loginfo("########## Step 1. Print an error message ##########")
        self.printErrorMessage(self.msg)

        # check an occurence of the same error
        if ResourceAllocationTimeOutFault.occurrence == False:
            # Step 2. Check states of Resource Scheduler
            rospy.loginfo("########## Step 2. Check states of Resource Scheduler ##########")

            if rospy.get_param("/concert/scheduler") is None: # if the scheduler is not available, then
                # Step 3a. Send an error message to BPEL
                rospy.loginfo("########## Step 3a. Send an error message to BPEL ##########")

                # set a handler to send an error message to BPEL
                self.handler = handler.FaultReporter(self.adapter)

                # execute an handling action
                self.handler.doRemedyAction(self.method, self.msg)
            else:
                # Step 3b. Re-send a resource allocation request to Scheduler
                # set a handler to send a resource allocation request
                rospy.loginfo("########## Step 3b. Re-send a resource allocation request to Scheduler ##########")
                self.handler = handler.RappAllocationRequester(self.adapter)

                # set method
                self.method = "resource_allocation"

                # change flag value
                ResourceAllocationTimeOutFault.occurrence = True

                # execute an handling action
                self.handler.doRemedyAction(self.method, self.linkgraph)
        else:
            # Step 4. Send an error message to BPEL
            # set a handler to send an error message to BPEL

            rospy.loginfo("########## Step 4. Send an error message to BPEL ##########")
            self.handler = handler.FaultReporter(self.adapter)

            # change flag value
            ResourceAllocationTimeOutFault.occurrence = False

            # execute an handling action
            self.handler.doRemedyAction(self.method, self.msg)


class RappNotAvailableFault(Fault):
    """
    Error raised when a requested robot app is not available
    """
    def __init__(self, adapter, rapp, linkgraph):
        '''
        :param adapter: concert adapter object which is currently running on ROCON
        :param rapp:
        :param linkgraph:
        :return:
        '''
        Fault.__init__(self, adapter, 'RobotApp', handler.RappAllocationRequester(adapter))
        self.rapp = rapp
        self.topic = self.__get_topic_from_linkgraph(rapp, linkgraph)

        ################################
        ### execute a remedy process ###
        ################################

        #rospy.loginfo()
        rospy.loginfo("########## RappNotAvailableFault ##########")

        # set method and error message
        self.msg = "Error: " + self.cause + " is not finished within 1 second..."

        # Step 1. Print an error message
        rospy.loginfo("########## Step 1. Print an error message ##########")
        self.printErrorMessage(self.msg)

        # Step 2. Request the Resource Scheduler to allocate another rapp
        rospy.loginfo("########## Step 2. Request the Resource Scheduler to allocate another rapp ##########")

        self.handler = handler.RappAllocationRequester(self.adapter)
        self.method = "rapp_allocation"
        self.handler.doRemedyAction(self.method, self.rapp)

        # Step 3. Re-send Topic to the new rapp
        rospy.loginfo("########## Step 3. Re-send Topic to the new rapp ##########")
        self.handler = handler.TopicResender(self.adapter)
        self.handler.doRemedyAction(self.rapp, self.topic)


    # To retrieve a Topic from linkgraph
    def __get_topic_from_linkgraph(self, rapp, linkgraph):
        '''
        :param rapp: rapp
        :param linkgraph: linkgraph that includes the rapp
        :return: rapp and topic sent to the rapp
        '''

        topic_matched = {}
        # To retrieve a topic for the rapp
        for topic in linkgraph.topics:
            if topic.id == rapp.id:
                topic_matched = topic

        return topic_matched


class RappInvocationTimeOutFault(Fault):
    """
    Error raised when a rapp invocation doesn't provide its response within a threshold time period (e.g. 3s)
    """
    occurrence = False

    def __init__(self, adapter, rapp, linkgraph):
        '''
        :param adapter: concert adapter object which is currently running on ROCON
        :param rapp:
        :param linkgraph:
        :return:
        '''
        Fault.__init__(self, adapter, 'Robotapp (' + rapp.id + ')', handler.TopicResender(adapter))
        self.rapp = rapp
        self.topic = self.__get_topic_from_linkgraph(rapp, linkgraph)

        ################################
        ### execute a remedy process ###
        ################################

        # set method and error message
        self.msg = "Error: A request to " + self.cause + " is delayed."

        # check an occurence of the same error
        if RappInvocationTimeOutFault.occurrence == False:
            # Step 1. Print an error message
            self.printErrorMessage(self.msg)

            # Step 2. Check availability of the given Rapp
            if rospy.has_param(self.rapp.resource):
                # Step 3a. Re-send Topic to the given Rapp
                # set a handler to send a resource allocation request
                rospy.loginfo("########## Step 3b. Re-send a resource allocation request to Scheduler ##########")
                self.handler = handler.RappAllocationRequester(self.adapter)

                # set method
                self.method = "rapp_allocation"

                # change flag value
                RappInvocationTimeOutFault.occurrence = True

                # execute an handling action
                self.handler.doRemedyAction(self.method, self.rapp)
            else :  # if the rapp is not available, then
                rospy.loginfo("Error: Rapp is not available...")

                # Step 3b. Raise RappNotAvailableFault
                rospy.loginfo("########## Step 3a. Raise RappNotAvailableFault ##########")

                # set a handler to send an error message to BPEL
                self.handler = handler.FaultReporter(self.adapter)

                # set method
                self.method = "timeout_method"

                # execute an handling action
                self.handler.doRemedyAction(self.method, self.msg)
        else:
            # Step 4. Send an error message to BPEL
            # set a handler to send an error message to BPEL

            rospy.loginfo("########## Step 4. Send an error message to BPEL ##########")
            self.handler = handler.FaultReporter(self.adapter)

            # change flag value
            RappInvocationTimeOutFault.occurrence = False

            # execute an handling action
            self.handler.doRemedyAction(self.method, self.msg)


    # To retrieve a rapp from linkgraph
    def __get_topic_from_linkgraph(self, rapp, linkgraph):
        '''
        :param rapp: rapp
        :param linkgraph: linkgraph that includes the rapp
        :return: rapp and topic sent to the rapp
        '''

        topic_matched = {}
        # To retrieve a topic for the rapp
        for topic in linkgraph.topics:
            if topic.id == rapp.id:
                topic_matched = topic

        return topic_matched


class InvalidReturnFault(Fault):
    """
    Error raised when a rapp invocation doesn't provide its response within a threshold time period (e.g. 3s)
    allocating resources is delayed
    """
    blacklist = {}

    def __init__(self, adapter, rapp, linkgraph):
        Fault.__init__(self, adapter, 'Robotapp (' + rapp.id + ')', handler.TopicResender(adapter))
        self.rapp = rapp
        self.topic = self.__get_topic_from_linkgraph(rapp, linkgraph)

        ################################
        ### execute a remedy process ###
        ################################
        # set an error message
        self.msg = "Error: " + self.cause + " returns invalid data."

        # Step 1. Check an existence of the given rapp in the blacklist
        rospy.loginfo("########## Step 1. Check an existence of the given Rapp in the BalckList ##########")
        if rapp.id not in InvalidReturnFault.blacklist: # if the given rapp is not yet in the blacklist, then
            rospy.loginfo("########## Step 2a. Register the Rapp into the BlackList as 'temporal' ##########")
            # Step 2a. To register the given rapp in the blacklist as 'temporal'
            InvalidReturnFault.blacklist[rapp.id] = 'temp'

            # Step 3a. To send a request to Resource Scheduler
            # for allocating new rapp
            rospy.loginfo("########## Step 3a. Re-allocate a Rapp having the same uri ##########")
            self.handler = handler.RappAllocationRequester(self.adapter)

            # set method
            self.method = "rapp_allocation"

            # execute an handling action
            self.handler.doRemedyAction(self.method, self.rapp)
        else :
            rospy.loginfo("########## Step 2b. Register the Rapp into the BlackList if the Rapp is not in the List ##########")
            # Step 2b. To register the given rapp in the blacklist as 'fixed'
            InvalidReturnFault.blacklist[rapp.id] = 'fixed'

            # Step 3b. To send an error message to BPEL
            rospy.loginfo("########## Step 3b. Send an error message to BPEL ##########")
            self.handler = handler.FaultReporter(self.adapter)

            # set method
            self.method = "invalidreturn_fault"

            # execute an handling action
            self.handler.doRemedyAction(self.method, self.msg)


    # To retrieve a rapp from linkgraph
    def __get_topic_from_linkgraph(self, rapp, linkgraph):
        '''
        :param rapp: rapp
        :param linkgraph: linkgraph that includes the rapp
        :return: rapp and topic sent to the rapp
        '''

        topic_matched = {}
        # To retrieve a topic for the rapp
        for topic in linkgraph.topics:
            if topic.id == rapp.id:
                topic_matched = topic

        return topic_matched