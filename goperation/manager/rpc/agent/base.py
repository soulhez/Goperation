import random
from requests import Session
from requests import adapters

from simpleutil.config import cfg
from simpleutil.utils import jsonutils
from simpleutil.utils.attributes import validators

from simpleservice.loopingcall import IntervalLoopinTask
from simpleservice.rpc.result import BaseRpcResult

from goperation import threadpool
from goperation.utils import suicide
from goperation.api.client import AgentManagerClient
from goperation.manager import common as manager_common
from goperation.manager.targetutils import target_server
from goperation.manager.rpc.base import RpcManagerBase
from goperation.manager.rpc.agent.config import agent_group
from goperation.manager.rpc.agent.config import rpc_agent_opts
from goperation.manager.rpc.agent.ctxtdescriptor import CheckManagerRpcCtxt


CONF = cfg.CONF

LOG = None

CONF.register_opts(rpc_agent_opts, agent_group)


_Session = Session()
_Session.mount('http://', adapters.HTTPAdapter(pool_connections=1,
                                               pool_maxsize=CONF[manager_common.AGENT].http_pconn_count))


class OnlinTaskReporter(IntervalLoopinTask):
    """Report Agent online
    """
    def __init__(self, manager):
        self.manager = manager
        self.with_performance = CONF[manager_common.AGENT].report_performance
        interval = CONF[manager_common.AGENT].online_report_interval
        super(OnlinTaskReporter, self).__init__(periodic_interval=interval,
                                                initial_delay=interval+random.randint(-30, 30),
                                                stop_on_exception=False)

    def __call__(self, *args, **kwargs):
        self.manager.client.agent_report_online(self.performance_snapshot())

    def performance_snapshot(self):
        if not self.with_performance:
            return None
        # TODO do a system performance snapshot with psutil
        return None


class RpcAgentManager(RpcManagerBase):

    def __init__(self):
        super(RpcAgentManager, self).__init__(target=target_server(self.agent_type, CONF.host, fanout=True))
        # agent id
        self._agent_id = None
        # port and port and disk space info
        conf = CONF[manager_common.AGENT]
        self.ports_range = validators['type:ports_range_list'](conf.ports_range) \
            if conf.ports_range else []
        self.client = AgentManagerClient(host=CONF.host, local_ip=self.local_ip,
                                         agent_type=self.agent_type,
                                         wsgi_url=CONF[manager_common.AGENT].gcenter,
                                         wsgi_port=CONF[manager_common.AGENT].gcenter_port,
                                         token=CONF.trusted, session=_Session)
        # key: port, value endpoint name
        self.allocked_ports = {}
        # left ports
        self.left_ports = set()
        for p_range in self.ports_range:
            up, down = map(int, p_range.split('-'))
            for port in xrange(up, down):
                self.left_ports.add(port)

    def pre_start(self, external_objects):
        super(RpcAgentManager, self).pre_start(external_objects)
        # get agent id of this agent
        # if agent not exist,call create
        self.client.agent_init_self(self)
        # add online report periodic tasks
        self._periodic_tasks.insert(0, OnlinTaskReporter(self))
        for endpoint in self.endpoints:
            endpoint.pre_start(self)

    def post_start(self):
        agent_info = self.client.agent_show(self.agent_id)
        status = agent_info['status']
        if status <= manager_common.SOFTBUSY:
            raise RuntimeError('Agent can not start, receive status is %d' % status)
        # get port allocked
        remote_endpoints = []
        for endpoint in agent_info['endpoints']:
            endpoint_name = endpoint['endpoint']
            remote_endpoints.append(endpoint_name)
        add_endpoints, delete_endpoints = self.validate_endpoint(agent_info['endpoints'])
        for endpoint in agent_info['endpoints']:
            if endpoint['endpoint'] in delete_endpoints:
                if endpoint['entity'] > 0:
                    raise RuntimeError('Agent endpoint entity not zero, '
                                       'but not endpoint %s in this agent' % endpoint['endpoint'])
            for port in endpoint['ports']:
                self.frozen_port(endpoint['endpoint'], port)
        if delete_endpoints:
            self.client.agents_delete_endpoints(agent_id=self.agent_id, body={'endpoints': add_endpoints})

        remote_ports_range = jsonutils.loads_as_bytes(agent_info['ports_range'])
        if remote_ports_range != self.ports_range:
            LOG.warning('Agent ports range has been changed at remote database')
            body = {'ports_range': jsonutils.dumps_as_bytes(self.ports_range)}
            # call agent change ports_range
            self.client.agent_edit(agent_id=self.agent_id, body=body)
        # agent set status at this moment
        # before status set, all rpc will requeue by RPCDispatcher
        # so function agent_show in wsgi server do not need agent lock
        self.force_status(status)
        for endpoint in self.endpoints:
            endpoint.post_start()
        super(RpcAgentManager, self).post_start()

    def post_stop(self):
        """close all endpoint here"""
        for endpoint in self.endpoints:
            endpoint.post_stop()
        super(RpcAgentManager, self).post_stop()

    def initialize_service_hook(self):
        super(RpcAgentManager, self).initialize_service_hook()
        # check endpoint here
        for endpoint in self.endpoints:
            endpoint.initialize_service_hook()

    def validate_endpoint(self, endpoints):
        remote_endpoints = set()
        for endpoint in endpoints:
            if isinstance(endpoint, dict) and endpoint.get('endpoint'):
                remote_endpoints.add(endpoint.get('endpoint'))
            elif isinstance(endpoint, basestring):
                remote_endpoints.add(endpoint)
            else:
                raise RuntimeError('Validate endpoint fail, value type error')
        local_endpoints = set([endpoint.namespace for endpoint in self.endpoints])
        add_endpoints = local_endpoints - remote_endpoints
        delete_endpoints = remote_endpoints - local_endpoints
        return add_endpoints, delete_endpoints

    def frozen_port(self, endpoint, ports=None):
        with self.work_lock.priority(3):
            if endpoint not in self.allocked_ports:
                self.allocked_ports[endpoint] = set()
            allocked_port = set()
            if ports is None:
                ports = [ports]
            for port in ports:
                try:
                    if port is not None:
                        self.left_ports.remove(port)
                    else:
                        port = self.left_ports.pop()
                except KeyError:
                    LOG.error('Agent allocked port fail')
                    self.free_ports(allocked_port)
                    raise
                self.allocked_ports[endpoint].add(port)
                allocked_port.add(port)
            return allocked_port

    def free_ports(self, ports):
        _ports = set()
        with self.work_lock.priority(2):
            if isinstance(ports, (int, long)):
                _ports.add(ports)
            else:
                _ports = set(ports)
        for allocked_ports in self.allocked_ports.values():
            intersection_ports = allocked_ports & _ports
            for port in intersection_ports:
                allocked_ports.remove(port)
                _ports.remove(port)
                self.left_ports.add(port)
        if _ports:
            LOG.error('%d port can not be found after free ports' % len(_ports))

    @property
    def agent_id(self):
        if self._agent_id is None:
            raise RuntimeError('Attrib agent_id not set')
        return self._agent_id

    @agent_id.setter
    def agent_id(self, agent_id):
        if not isinstance(agent_id, int):
            raise RuntimeError('Agent is not value type error, not int')
        with self.work_lock.priority(0):
            if self._agent_id is None:
                self._agent_id = agent_id
            else:
                if self._agent_id != agent_id:
                    raise RuntimeError('Agent init find agent_id changed')
                LOG.warning('Do not call agent_id setter more then once')

    @CheckManagerRpcCtxt
    def rpc_active_agent(self, ctxt, **kwargs):
        if kwargs.get('agent_id') != self.agent_id or kwargs.get('agent_ipaddr') != self.local_ip:
            return BaseRpcResult(self.agent_id, ctxt,
                                 resultcode=manager_common.RESULT_ERROR,
                                 result='ACTIVE agent failure, agent id or ip not match')
        status = kwargs.get('status')
        if not isinstance(status, (int, long)) or status <= manager_common.SOFTBUSY:
            return BaseRpcResult(self.agent_id, ctxt,
                                 resultcode=manager_common.RESULT_ERROR,
                                 result='Agent change status failure, status code value error')
        if not self.set_status(status):
            return BaseRpcResult(self.agent_id, ctxt,
                                 resultcode=manager_common.RESULT_ERROR,
                                 result='ACTIVE agent failure, can not set stauts now')
        return BaseRpcResult(self.agent_id, ctxt,
                             resultcode=manager_common.RESULT_SUCCESS, result='ACTIVE agent success')

    @CheckManagerRpcCtxt
    def rpc_status_agent(self, ctxt, **kwargs):
        return BaseRpcResult(self.agent_id, ctxt,
                             resultcode=manager_common.RESULT_SUCCESS,
                             result='Get status from %s success' % self.local_ip,
                             # TODO more info of endpoint
                             details=[dict(name=endpoint.name, entitys=endpoint.entitys)
                                      for endpoint in self.endpoints])

    @CheckManagerRpcCtxt
    def rpc_delete_agent_precommit(self, ctxt, **kwargs):
        with self.work_lock.priority(0):
            if self.status <= manager_common.SOFTBUSY:
                return BaseRpcResult(self.agent_id, ctxt,
                                     resultcode=manager_common.RESULT_ERROR,
                                     result='Can not change status now')
            if kwargs['agent_id'] != self.agent_id \
                    or kwargs['agent_type'] != self.agent_type \
                    or kwargs['host'] != CONF.host \
                    or kwargs['ipaddr'] != self.local_ip:
                return BaseRpcResult(self.agent_id, ctxt,
                                     resultcode=manager_common.RESULT_ERROR,
                                     result='Not match this agent')
            for endpont in self.endpoints:
                if endpont.entitys:
                    return BaseRpcResult(self.agent_id, ctxt,
                                         resultcode=manager_common.RESULT_ERROR,
                                         result='Endpoint %s is not empty' % endpont.name)
            self.status = manager_common.PERDELETE
            msg = 'Agent %s with id %d wait delete' % (CONF.host, self.agent_id)
            LOG.info(msg)
            return BaseRpcResult(self.agent_id, ctxt,
                                 resultcode=manager_common.RESULT_SUCCESS, result=msg)

    @CheckManagerRpcCtxt
    def rpc_delete_agent_postcommit(self, ctxt, **kwargs):
        with self.work_lock.priority(0):
            if kwargs['agent_id'] != self.agent_id or \
                            kwargs['agent_type'] != self.agent_type or \
                            kwargs['host'] != CONF.host or \
                            kwargs['ipaddr'] != self.local_ip:
                return BaseRpcResult(self.agent_id, ctxt,
                                     resultcode=manager_common.RESULT_ERROR, result='Not match this agent')
            if self.status == manager_common.PERDELETE:
                self.status = manager_common.DELETED
                msg = 'Agent %s with id %d set status to DELETED success' % (CONF.host, self.agent_id)
                suicide(delay=3)
                return BaseRpcResult(self.agent_id, ctxt,
                                     resultcode=manager_common.RESULT_SUCCESS, result=msg)
            else:
                msg = 'Agent status is not PERDELETE, status is %d' % self.status
                return BaseRpcResult(self.agent_id, ctxt,
                                     resultcode=manager_common.RESULT_ERROR, result=msg)

    @CheckManagerRpcCtxt
    def rpc_upgrade_agent(self, ctxt, **kwargs):
        with self.work_lock.priority(0):
            if threadpool.threads or self.status < manager_common.SOFTBUSY:
                return BaseRpcResult(self.agent_id, ctxt,
                                     resultcode=manager_common.RESULT_ERROR,
                                     result='upgrade fail public thread pool not empty or status error')
            last_status = self.status
            self.status = manager_common.HARDBUSY
            # TODO call rpm Uvh then restart self
            self.force_status(last_status)
            return BaseRpcResult(self.agent_id, ctxt, resultcode=manager_common.RESULT_SUCCESS,
                                 result='upgrade call rpm Uvh success')