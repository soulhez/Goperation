import time
import eventlet
import contextlib

from sqlalchemy.sql import and_

from redis.exceptions import ConnectionError
from redis.exceptions import TimeoutError
from redis.exceptions import WatchError
from redis.exceptions import ResponseError

from simpleutil.config import cfg
from simpleutil.utils import timeutils
from simpleutil.utils import uuidutils
from simpleutil.utils import jsonutils
from simpleutil.utils import argutils
from simpleutil.utils.attributes import validators
from simpleutil.log import log as logging
from simpleutil.common.exceptions import InvalidArgument

from simpleservice.ormdb.api import model_query
from simpleservice.ormdb.api import model_count_with_key
from simpleservice.ormdb.api import model_autoincrement_id


from goperation.manager import exceptions
from goperation.manager import common as manager_common
from goperation.manager.config import manager_group
from goperation.manager.models import Agent
from goperation.manager.models import AgentEndpoint
from goperation.manager.models import AgentEntity
from goperation.manager.utils import validateutils


LOG = logging.getLogger(__name__)

CONF = cfg.CONF

class GlobalData(object):

    PREFIX = CONF[manager_group.name].redis_key_prefix
    # agent lock
    AGENT_KEY = '-'.join([PREFIX, manager_common.AGENT])
    # endpoint lock
    ENDPOINT_KEY = '-'.join([PREFIX, 'endpoint', '%s'])
    # all agent id list cache
    ALL_AGENTS_KEY = '-'.join([PREFIX, manager_common.AGENT, 'id', 'all'])

    def __init__(self, client, session):
        self.locker = '%d.%d' % (uuidutils.Gkey.sid, uuidutils.Gkey.pid)
        self.client = client
        self.session = session
        self.alloctime = int(CONF[manager_group.name].glock_alloctime*1000)
        # metadata cache dict
        self.metadatas = dict()
        # metadata cache time and baseline
        self.metatimes = dict()

    @staticmethod
    def host_online_key(agent_id):
        return '%s-online-%s-%s' % (GlobalData.PREFIX, manager_common.AGENT, str(agent_id))

    def garbage_key_collection(self, key):
        overtime = self.alloctime + int(time.time()*1000)
        client = self.client
        while True:
            try:
                client.delete(key)
                break
            except (ConnectionError, TimeoutError) as e:
                if int(time.time())*1000 > overtime:
                    LOG.critical('DELETE key %s fail %s' % (key, e.__class__.__name__))
                    client.add_garbage_keys(key, self.locker)
                    break
            eventlet.sleep(0)

    def garbage_member_collection(self, key, members, action='srem'):
        overtime = self.alloctime + int(time.time()*1000)
        client = self.client
        func = getattr(client, action)
        while True:
            try:
                # count = client.srem(key, *members)
                # count = client.zrem(key, *members)
                count = func(key, *members)
                if count != len(members):
                    LOG.critical('REM %s from %s, just success %d' % (str(members), key, count))
                break
            except (ConnectionError, TimeoutError) as e:
                if int(time.time())*1000 > overtime:
                    LOG.critical('REM %s from %s fail %s' % (str(members), key, e.__class__.__name__))
                    break
            except ResponseError:
                break
            eventlet.sleep(0)

    def lock(self, target):
        method = '_lock_%s' % target
        if not hasattr(self, method):
            raise NotImplementedError('lock %s not exist' % method)
        return getattr(self, method)

    @contextlib.contextmanager
    def _lock_autorelase(self, key, expire):
        overtime = self.alloctime + int(time.time()*1000)
        client = self.client
        while True:
            if client.set(key, self.locker, nx=True):
                break
            if int(time.time()*1000) > overtime:
                raise exceptions.AllocLockTimeout('Alloc key %s timeout' % key)
            eventlet.sleep(0.003)
        try:
            yield
        except Exception:
            client.delete(key)
        else:
            client.expire(key, expire)

    @contextlib.contextmanager
    def _lock_all_agents(self):
        overtime = self.alloctime + int(time.time()*1000)
        client = self.client
        while True:
            if client.set(self.AGENT_KEY, self.locker, nx=True):
                break
            if int(time.time()*1000) > overtime:
                raise exceptions.AllocLockTimeout('Alloc key %s timeout' % self.AGENT_KEY)
            eventlet.sleep(0.003)
        try:
            yield manager_common.ALL_AGENTS
        finally:
            self.garbage_key_collection(self.AGENT_KEY)

    @contextlib.contextmanager
    def _lock_agents(self, agents):
        agents = argutils.map_to_int(agents)
        overtime = self.alloctime + int(time.time()*1000)
        client = self.client
        agents_ids = argutils.map_with(agents, str)
        while True:
            wpipe = None
            try:
                if (agents - self.all_agents):
                    raise exceptions.TargetCountUnequal('Target agents not in all agents id list')
                locked = True
                while int(time.time()*1000) < overtime:
                    try:
                        for _id in agents_ids:
                            if client.sismember(self.AGENT_KEY, _id):
                                eventlet.sleep(0.01)
                                continue
                        locked = False
                    except ResponseError as e:
                        if not e.message.startswith('WRONGTYPE'):
                            raise
                if locked:
                    raise exceptions.AllocLockTimeout('Lock agents timeout')
                wpipe = client.pipeline()
                wpipe.watch(self.AGENT_KEY)
                wpipe.multi()
                wpipe.sadd(self.AGENT_KEY, *agents_ids)
                wpipe.execute()
                break
            except WatchError:
                if int(time.time()*1000) > overtime:
                    raise exceptions.AllocLockTimeout('Lock agents timeout')
            finally:
                if wpipe:
                    wpipe.reset()
        try:
            yield
        finally:
            if agents_ids:
                self.garbage_member_collection(self.AGENT_KEY, agents_ids)

    @contextlib.contextmanager
    def _lock_entitys(self, endpoint, entitys):
        entitys = argutils.map_to_int(entitys)
        overtime = self.alloctime + int(time.time()*1000)
        endpoint_key = self.ENDPOINT_KEY % endpoint
        client = self.client
        session = self.session(readonly=True)
        query = model_query(session, AgentEntity,
                            filter=and_(AgentEntity.entity.in_(entitys),
                                        AgentEntity.endpoint == endpoint))
        agents_ids = set()
        entitys_ids = set()
        while True:
            wpipe = None
            try:
                target_entitys = query.all()
                if len(entitys) != len(target_entitys):
                    raise exceptions.TargetCountUnequal('Target entitys not found in database')
                for entity in target_entitys:
                    entitys_ids.add(entity.entity)
                    agents_ids.add(entity.agent_id)
                while int(time.time()*1000) < overtime:
                    with client.pipeline() as pipe:
                        pipe.multi()
                        for _id in agents_ids:
                            pipe.sismember(self.AGENT_KEY, str(_id))
                        for _id in entitys_ids:
                            pipe.sismember(endpoint_key, str(_id))
                        results = pipe.execute()
                    if all([True if not result else False for result in results]):
                        break
                    else:
                        eventlet.sleep(0.01)
                wpipe = client.pipeline()
                wpipe.watch(self.AGENT_KEY, endpoint_key)
                wpipe.multi()
                wpipe.sadd(self.AGENT_KEY, *map(str, agents_ids))
                wpipe.sadd(endpoint_key, *map(str, entitys_ids))
                wpipe.execute()
                break
            except WatchError:
                if int(time.time()*1000) > overtime:
                    raise exceptions.AllocLockTimeout('Lock entitys timeout')
            except ResponseError as e:
                if not e.message.startswith('WRONGTYPE'):
                    raise
                if int(time.time()*1000) > overtime:
                    raise exceptions.AllocLockTimeout('Lock entitys timeout')
            finally:
                if wpipe:
                    wpipe.reset()
        try:
            yield agents_ids
        finally:
            self.garbage_member_collection(self.AGENT_KEY, agents_ids)
            self.garbage_member_collection(endpoint_key, entitys_ids)

    @contextlib.contextmanager
    def _lock_endpoint(self, endpoint):
        overtime = self.alloctime + int(time.time()*1000)
        client = self.client
        endpoint_key = self.ENDPOINT_KEY % endpoint
        while True:
            if client.set(endpoint_key, self.locker, nx=True):
                break
            if int(time.time()*1000) > overtime:
                raise exceptions.AllocLockTimeout('Alloc key %s timeout' % endpoint_key)
            eventlet.sleep(0)
        try:
            yield
        finally:
            self.garbage_key_collection(endpoint_key)

    @property
    def all_agents(self):
        client = self.client
        key = self.ALL_AGENTS_KEY
        # all_ids = client.smembers(key)
        all_ids = client.zrange(name=key, start=0, end=-1, withscores=False)
        id_set = set()
        if not all_ids:
            # lazy init all agent id cache
            session = self.session(readonly=True)
            with self._lock_all_agents():
                # doble check after lock
                all_ids = client.zrange(name=key, start=0, end=-1, withscores=False)
                if not all_ids:
                    query = session.query(Agent.agent_id).filter(Agent.status > manager_common.DELETED)
                    for result in query:
                        id_set.add(result[0])
                    # add agent id into redis
                    if id_set:
                        now = int(time.time())
                        # client.sadd(key, *[str(_id) for _id in id_set])
                        client.zadd(key, *[y for x in id_set for y in [now, str(x)]])
                    return id_set
        for agent_id in all_ids:
            id_set.add(int(agent_id))
        return id_set

    def flush_all_agents(self):
        with self._lock_all_agents():
            # clean all id key
            self.garbage_key_collection(self.ALL_AGENTS_KEY)

    def add_agent(self, body):
        agent = Agent()
        try:
            agent.host = validators['type:hostname'](body.pop('host'))
            agent.agent_type = body.pop('agent_type', None)
            if agent.agent_type is None or len(agent.agent_type) > 64:
                raise ValueError('Agent type info over size')
            if body.get('ports_range', None) is not None:
                agent.ports_range = jsonutils.dumps(body.pop('ports_range'))
            agent.memory = int(body.pop('memory'))
            agent.cpu = int(body.pop('cpu'))
            agent.disk = int(body.pop('disk'))
            endpoints = validateutils.validate_endpoints(body.pop('endpoints', []))
        except KeyError as e:
            raise InvalidArgument('Can not find argument: %s' % e.message)
        except ValueError as e:
            raise InvalidArgument('Argument value type error: %s' % e.message)
        agent.create_time = timeutils.realnow()
        session = self.session()
        with self._lock_all_agents():
            host_filter = and_(Agent.host == agent.host, Agent.status > manager_common.DELETED)
            if model_count_with_key(session, Agent.host, filter=host_filter) > 0:
                raise exceptions.AgentHostExist('Agent with host %s alreday eixst' % agent.host)
            agent_id = model_autoincrement_id(session, Agent.agent_id)
            with session.begin():
                agent.agent_id = agent_id
                # if agent.endpoints:
                #     for endpoint in agent.endpoints:
                #         endpoint.agent_id = agent_id
                session.add(agent)
                session.flush()
                if endpoints:
                    for endpoint in endpoints:
                        session.add(AgentEndpoint(agent_id=agent_id, endpoint=endpoint))
                        session.flush()
                # add new agent_id to cache all agent_id
                # if not self.client.sadd(self.ALL_AGENTS_KEY, str(agent.agent_id)):
                if not self.client.zadd(self.ALL_AGENTS_KEY, int(time.time()), str(agent.agent_id)):
                    raise exceptions.CacheStoneError('Cant not add agent_id to redis, key %s' % self.ALL_AGENTS_KEY)
                _endpoints = [e.endpoint for e in agent.endpoints]
                LOG.info('New anget with endpoints %s' % ','.join(_endpoints))
        return agent

    @contextlib.contextmanager
    def delete_agent(self, agent):
        agent_id = agent.agent_id if isinstance(agent, Agent) else agent
        with self._lock_agents(agent_id):
            session = self.session()
            query = model_query(session, Agent,
                                filter=and_(Agent.agent_id == agent_id,
                                            Agent.status > manager_common.DELETED))
            target_agent = query.one()
            if len(target_agent.entitys) > 0:
                raise InvalidArgument('Can not delete agent, entity not 0')
            with session.begin():
                # Mark agent deleted
                query.update({'status': manager_common.DELETED})
                # Delete endpoint of agent
                yield target_agent
            self.metadatas.pop(agent_id, None)
            self.metatimes.pop(agent_id, None)
            self.garbage_member_collection(self.ALL_AGENTS_KEY, [str(agent_id), ], 'zrem')

    def flush_onlines(self):
        with self._lock_all_agents():
            # clean host online key
            keys = self.client.keys(self.host_online_key('*'))
            self.metadatas.clear()
            self.metatimes.clear()
            if keys:
                with self.client.pipeline() as pipe:
                    for key in keys:
                        pipe.delete(key)
                    pipe.execute()
        return self.all_agents

    def agents_metadata(self, agents):
        client = self.client
        key = self.ALL_AGENTS_KEY

        all_ids = client.zrange(name=key, start=0, end=-1, withscores=True)
        zsources = dict()
        for zsource in all_ids:
            zsources[int(zsource[0])] = int(zsource[1])

        agents = set(agents)
        if agents - set(zsources.keys()):
            not_founds = [str(a) for a in (agents - set(zsources.keys()))]
            LOG.error('Agents of [%s]  can not be found' % ','.join(not_founds))
            raise InvalidArgument('Agents Can not find be found in %s' % self.ALL_AGENTS_KEY)

        now = int(time.time())
        cached = set(self.metadatas.keys())
        # agent has been deleted
        deleted = cached - agents
        if deleted:
            for agent_id in deleted:
                # remove target from metadata cache and cached list
                self.metatimes.pop(agent_id, None)
                self.metadatas.pop(agent_id, None)
                cached.remove(agent_id)
        # agent not in cache
        missed = list(agents - cached)

        for agent_id in cached:
            # cache overtime or baseline changed
            if self.metatimes[agent_id][0] != zsources[agent_id] \
                    or self.metatimes[agent_id][1] < now:
                missed.append(agent_id)

        if missed:
            pipe = self.client.pipeline()
            pipe.mget(*[self.host_online_key(agent_id) for agent_id in missed])
            for agent_id in missed:
                pipe.ttl(self.host_online_key(agent_id))
            ttls = pipe.execute()
            metadatas = ttls.pop(0)
            # metadatas = pipe.mget(*[self.host_online_key(agent_id) for agent_id in missed])
            for index, metadata in enumerate(metadatas):
                agent_id = missed[index]
                if metadata:
                    self.metatimes[agent_id] = (zsources[agent_id], now + ttls[index])
                    self.metadatas[agent_id] = jsonutils.loads_as_bytes(metadata)
                else:
                    self.metatimes.pop(agent_id, None)
                    self.metadatas.pop(agent_id, None)

        return self.metadatas

    def agent_metadata_flush(self, agent_id, metadata, expire):
        LOG.debug('try update agent metadata in global data')
        host = metadata.get('host')
        host_online_key = self.host_online_key(agent_id)

        pipe = self.client.pipeline()
        pipe.watch(host_online_key)
        pipe.multi()
        pipe.get(host_online_key)
        pipe.ttl(host_online_key)
        pipe.expire(host_online_key, expire or manager_common.ONLINE_EXIST_TIME)
        try:
            exist_agent_metadata, ttl, expire_result = pipe.execute()
        except WatchError:
            raise InvalidArgument('Host changed')

        if exist_agent_metadata is not None:
            exist_agent_metadata = jsonutils.loads_as_bytes(exist_agent_metadata)
            if exist_agent_metadata.get('host') != host:
                LOG.error('Host call online with %s, but %s alreday exist with same key' %
                          (host, exist_agent_metadata.get('host')))
                if ttl > 3:
                    if not self.client.expire(host_online_key, ttl):
                        LOG.error('Revet ttl of %s fail' % host_online_key)
                raise InvalidArgument('Agent %d with host %s alreday eixst' %
                                      (agent_id, exist_agent_metadata.get('host')))
            else:
                # replace metadata
                if exist_agent_metadata != metadata:
                    LOG.info('Agent %d metadata change' % agent_id)
                    if not self.client.set(host_online_key, jsonutils.dumps_as_bytes(metadata),
                                           ex=expire or manager_common.ONLINE_EXIST_TIME):
                        raise InvalidArgument('Another agent login with same host or '
                                              'someone set key %s' % host_online_key)
                    self.client.zadd(self.ALL_AGENTS_KEY, int(time.time()), str(agent_id))
        else:
            if not self.client.set(host_online_key, jsonutils.dumps_as_bytes(metadata),
                                   ex=expire or manager_common.ONLINE_EXIST_TIME, nx=True):
                raise InvalidArgument('Another agent login with same host or '
                                      'someone set key %s' % host_online_key)
            self.client.zadd(self.ALL_AGENTS_KEY, int(time.time()), str(agent_id))

    def agent_metadata_expire(self, agent_id, expire):
        LOG.debug('try expire agent metadata in global data')
        host_online_key = self.host_online_key(agent_id)
        if not self.client.expire(host_online_key, expire):
            raise exceptions.AgentMetadataMiss(host_online_key)
