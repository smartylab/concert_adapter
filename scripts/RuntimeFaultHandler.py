
import rospy
import rocon_python_comms
import rocon_uri
import concert_service_utilities
import concert_scheduler_requests
import concert_service_link_graph
import roslib.message

from adapter import ConcertAdapter as adapter

from pysimplesoap.client import SoapClient

class RuntimeFaultHandler:
    # abstract method to initialize RuntimeErrorHandler
    def __init__(self):
        rospy.loginfo("Runtime Fault Handler is created... ")

    # abstract method to handle faults
    def doRemedyAction(self):
        pass

    # To set an adapter for sending topic messages
    def __set_adapter(self, adapter):
        '''
        To set an adapter which is running on ROCON
        :param adapter: concert adapter
        :return:
        '''
        self.adapter = adapter

class FaultReporter(RuntimeFaultHandler):
    # To initialize FaultReporter
    def __init__(self, adapter):
        RuntimeFaultHandler.__init__(self)
        self.__set_adapter(adapter) # To set an adapter

    # To send an error message to BPEL
    def doRemedyAction(self, method, msg):
        '''
        This just send an error report to BPEL
        :param method: Method name of BPEL for error reporting
        :param msg: An error message
        :return:
        '''
        # settings of address and namespace of BPEL
        address = "http://localhost:8080/ode/processes/CommTestBP"
        namespace = "kr.co.smartylab"

        # Prepare a SOAP client to send a message to BPEL
        client = SoapClient(
            location = address,
            namespace = namespace,
            soap_ns= 'soap')

        method = "process"
        rospy.loginfo("Sending the message to BPEL... %s(%s)" % (method, msg))

        param_dict = dict()
        param_dict["error_message"] = msg
        response = client.call(method, **param_dict)
        rospy.loginfo("The message is sent.")

    # To set an adapter for sending topic messages
    def __set_adapter(self, adapter):
        '''
        To set an adapter which is running on ROCON
        :param adapter: concert adapter
        :return:
        '''
        self.adapter = adapter

class TopicResender(RuntimeFaultHandler):
    # To initialize TopicResender
    def __init__(self, adapter):
        RuntimeFaultHandler.__init__(self)
        self.__set_adapter(adapter) # To set an adapter

    # To send a topic to the given rapp by using the resend_topic method of the Adapter
    def doRemedyAction(self, rapp, topic):
        '''
        :param rapp: robot app that has an error while sending a topic
        :param topic: topic that is sent to the given rapp
        :return:
        '''
        self.adapter.resend_topic(rapp, topic)


    # To set an adapter for sending topic messages
    def __set_adapter(self, adapter):
        '''
        To set an adapter which is running on ROCON
        :param adapter: concert adapter
        :return:
        '''
        self.adapter = adapter

class RappAllocationRequester(RuntimeFaultHandler):
    # To initialize TopicResender
    def __init__(self, adapter):
        self.__set_adapter(adapter)

    # To invoke receive_service_invocation method of the adapter
    # for re-allocating rapp which is not available
    def doRemedyAction(self, method, linkgraph):
        '''
        :param method:      # indicator of request types
        :param linkgraph:   # linkgraph including unavailable rapp
        :return:
        '''
        if method == "resource_allocation":
            self.adapter.reallocate_resources(linkgraph)
        elif method == "rapp_allocation":
            self.adapter.reallocate_rapp(linkgraph)


    # To set an adapter for sending topic messages
    def __set_adapter(self, adapter):
        '''
        To set an adapter which is running on ROCON
        :param adapter: concert adapter
        :return:
        '''
        self.adapter = adapter