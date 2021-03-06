import datetime
import sqlalchemy as sa
from sqlalchemy import orm
from sqlalchemy.sql import and_
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.dialects.mysql import SMALLINT
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.dialects.mysql import BOOLEAN
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.dialects.mysql import BLOB
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.dialects.mysql import ENUM

from simpleutil.utils import timeutils
from simpleutil.utils import uuidutils

from simpleservice.ormdb.models import MyISAMTableBase
from simpleservice.ormdb.models import InnoDBTableBase
from simpleservice.plugin.models import PluginTableBase

from goperation.manager import common as manager_common


def realnowint(after=0):
    if after:
        def t():
            return int(timeutils.realnow()) + after
        return t
    else:
        return int(timeutils.realnow())


class ResponeDetail(PluginTableBase):
    detail_id = sa.Column(INTEGER(unsigned=True), nullable=False, primary_key=True)
    agent_id = sa.Column(sa.ForeignKey('agentrespones.agent_id', ondelete="CASCADE", onupdate='RESTRICT'),
                         default=0, nullable=False, primary_key=True)
    request_id = sa.Column(sa.ForeignKey('agentrespones.request_id', ondelete="RESTRICT", onupdate='RESTRICT'),
                           nullable=False,
                           primary_key=True)
    resultcode = sa.Column(TINYINT, nullable=False, default=manager_common.RESULT_UNKNOWN)
    result = sa.Column(VARCHAR(manager_common.MAX_DETAIL_RESULT), nullable=False, default='{}')
    __table_args__ = (
            sa.Index('request_id_index', 'request_id'),
            InnoDBTableBase.__table_args__
    )


class AgentRespone(PluginTableBase):
    agent_id = sa.Column(sa.ForeignKey('agents.agent_id', ondelete="CASCADE", onupdate='RESTRICT'),
                         nullable=False,
                         primary_key=True)
    request_id = sa.Column(sa.ForeignKey('asyncrequests.request_id', ondelete="RESTRICT", onupdate='RESTRICT'),
                           nullable=False, primary_key=True)
    server_time = sa.Column(INTEGER(unsigned=True), default=realnowint, nullable=False)
    # agent respone unix time in seconds
    agent_time = sa.Column(INTEGER(unsigned=True), nullable=False)
    resultcode = sa.Column(TINYINT, nullable=False, default=manager_common.RESULT_UNKNOWN)
    result = sa.Column(VARCHAR(manager_common.MAX_AGENT_RESULT),
                       nullable=False, default='agent respone rpc request')
    details = orm.relationship(ResponeDetail, backref='agentrespone', lazy='joined',
                               primaryjoin=and_(agent_id == ResponeDetail.agent_id,
                                                request_id == ResponeDetail.request_id),
                               cascade='delete,delete-orphan,save-update')
    __table_args__ = (
            sa.Index('request_id_index', 'request_id'),
            InnoDBTableBase.__table_args__
    )


class AsyncRequest(PluginTableBase):
    request_id = sa.Column(CHAR(36), default=uuidutils.generate_uuid,
                           nullable=False, primary_key=True)
    request_time = sa.Column(INTEGER(unsigned=True),
                             default=realnowint, nullable=False)
    # if request finish
    status = sa.Column(TINYINT, nullable=False, default=manager_common.UNFINISH)
    # write agent respone into database or just expire "expire" seconds
    expire = sa.Column(INTEGER, nullable=False, default=0)
    # request should finish at finish time
    # when agent get a rpc came, if cur time > finishtime
    # agent will drop the package
    finishtime = sa.Column(INTEGER(unsigned=True), default=realnowint(5), nullable=False)
    # request should finish before deadline time
    # if find cur time > deadline, it will not check return any more
    deadline = sa.Column(INTEGER(unsigned=True), default=realnowint(10), nullable=False)
    resultcode = sa.Column(TINYINT, nullable=False, default=manager_common.RESULT_UNKNOWN)
    result = sa.Column(VARCHAR(manager_common.MAX_REQUEST_RESULT),
                       nullable=False, default='waiting respone')
    # AgentRespone list
    respones = orm.relationship(AgentRespone, backref='asyncrequest', lazy='select',
                                cascade='delete, delete-orphan')
    __table_args__ = (
        sa.Index('request_time_index', 'request_time'),
        InnoDBTableBase.__table_args__
    )


class AgentResponeBackLog(PluginTableBase):
    """request after deadline scheduled timer will insert a AgentRespone log with status time out
    if agent respone affter deadline, will get an error primary key error
    at this time, recode into  agentresponebacklogs table
    """
    agent_id = sa.Column(INTEGER(unsigned=True), nullable=False, default=0, primary_key=True)
    request_id = sa.Column(VARCHAR(36),
                           nullable=False, primary_key=True)
    server_time = sa.Column(INTEGER(unsigned=True), default=realnowint, nullable=False)
    agent_time = sa.Column(INTEGER(unsigned=True), nullable=False)
    resultcode = sa.Column(TINYINT, nullable=False, default=manager_common.RESULT_UNKNOWN)
    result = sa.Column(VARCHAR(manager_common.MAX_AGENT_RESULT),
                       nullable=False, default='agent respone rpc request')
    status = sa.Column(BOOLEAN, nullable=False, default=0)
    # will not link to ResponeDetail
    # save respone detail into MEDIUMBLOB column
    details = sa.Column(MEDIUMBLOB, nullable=True)
    __table_args__ = (
            sa.Index('request_id_index', 'request_id'),
            InnoDBTableBase.__table_args__
    )


class AllocatedPort(PluginTableBase):
    port = sa.Column(SMALLINT(unsigned=True),
                     nullable=False, primary_key=True)
    agent_id = sa.Column(sa.ForeignKey('agents.agent_id', ondelete="RESTRICT", onupdate='RESTRICT'),
                         nullable=False, primary_key=True)
    entity_id = sa.Column(sa.ForeignKey('agententitys.id', ondelete="CASCADE", onupdate='RESTRICT'),
                          nullable=False)
    endpoint_id = sa.Column(sa.ForeignKey('agentendpoints.id', ondelete="CASCADE", onupdate='CASCADE'),
                            nullable=False)
    endpoint = sa.Column(VARCHAR(manager_common.MAX_ENDPOINT_NAME_SIZE), nullable=False)
    entity = sa.Column(INTEGER(unsigned=True), nullable=False)
    desc = sa.Column(VARCHAR(256), nullable=True, default=None)
    __table_args__ = (
            sa.Index('agent_index', 'agent_id'),
            sa.Index('ports_index', 'agent_id', 'endpoint', 'entity'),
            InnoDBTableBase.__table_args__
    )


class AgentEntity(PluginTableBase):
    id = sa.Column(BIGINT(unsigned=True), nullable=False, primary_key=True, autoincrement=True)
    entity = sa.Column(INTEGER(unsigned=True),
                       nullable=False)
    endpoint = sa.Column(VARCHAR(manager_common.MAX_ENDPOINT_NAME_SIZE), nullable=False)
    endpoint_id = sa.Column(sa.ForeignKey('agentendpoints.id', ondelete="CASCADE", onupdate='CASCADE'),
                            nullable=False)
    agent_id = sa.Column(sa.ForeignKey('agents.agent_id', ondelete="CASCADE", onupdate='RESTRICT'),
                         nullable=False)
    desc = sa.Column(VARCHAR(256), nullable=True, default=None)
    ports = orm.relationship(AllocatedPort, backref='appentity', lazy='select',
                             cascade='delete,delete-orphan,save-update')
    __table_args__ = (
            sa.UniqueConstraint('entity', 'endpoint', name='unique_entity'),
            sa.Index('endpoint_index', 'endpoint'),
            sa.Index('entitys_index', 'endpoint', 'agent_id'),
            InnoDBTableBase.__table_args__
    )


class AgentEndpoint(PluginTableBase):
    id = sa.Column(BIGINT(unsigned=True), nullable=False, primary_key=True, autoincrement=True)
    endpoint = sa.Column(VARCHAR(manager_common.MAX_ENDPOINT_NAME_SIZE),
                         nullable=False)
    agent_id = sa.Column(sa.ForeignKey('agents.agent_id', ondelete="CASCADE", onupdate='RESTRICT'),
                         nullable=False)
    entitys = orm.relationship(AgentEntity, backref='agentendpoint', lazy='select',
                               cascade='delete,delete-orphan,save-update')
    ports = orm.relationship(AllocatedPort, backref='agentendpoint', lazy='select',
                             cascade='delete,delete-orphan')
    __table_args__ = (
            sa.UniqueConstraint('endpoint', 'agent_id', name='unique_endpoint'),
            sa.Index('endpoint_index', 'endpoint'),
            InnoDBTableBase.__table_args__
    )


class Agent(PluginTableBase):
    agent_id = sa.Column(INTEGER(unsigned=True), nullable=False,
                         default=1, primary_key=True)
    agent_type = sa.Column(VARCHAR(64), nullable=False)
    create_time = sa.Column(INTEGER(unsigned=True),
                            default=realnowint, nullable=False)
    host = sa.Column(VARCHAR(manager_common.MAX_HOST_NAME_SIZE), nullable=False)
    # 0 not active, 1 active  -1 mark delete
    status = sa.Column(TINYINT, default=manager_common.UNACTIVE, nullable=False)
    # total cpu number
    cpu = sa.Column(INTEGER(unsigned=True),  default=0, server_default='0', nullable=False)
    # total memory can be used
    memory = sa.Column(INTEGER(unsigned=True),  default=0, server_default='0', nullable=False)
    # total disk space left can be used
    disk = sa.Column(BIGINT(unsigned=True), default=0, server_default='0', nullable=False)
    ports_range = sa.Column(VARCHAR(manager_common.MAX_PORTS_RANGE_SIZE),
                            nullable=True)
    endpoints = orm.relationship(AgentEndpoint, backref='agent', lazy='select',
                                 cascade='delete,delete-orphan,save-update')
    entitys = orm.relationship(AgentEntity, backref='agent', lazy='select',
                               cascade='delete,delete-orphan')
    ports = orm.relationship(AllocatedPort, backref='agent', lazy='select',
                             cascade='delete,delete-orphan')
    __table_args__ = (
            sa.Index('host_index', 'host'),
            InnoDBTableBase.__table_args__
    )


class DownFile(PluginTableBase):
    md5 = sa.Column(CHAR(32), nullable=False, primary_key=True)
    downloader = sa.Column(VARCHAR(12), nullable=False)
    adapter_args = sa.Column(BLOB, nullable=True)
    address = sa.Column(VARCHAR(512), nullable=False)
    ext = sa.Column(VARCHAR(12), nullable=False)
    size = sa.Column(BIGINT, nullable=False)
    status = sa.Column(VARCHAR(16), ENUM(*manager_common.DOWNFILESTATUS),
                       default=manager_common.DOWNFILE_FILEOK, nullable=False)
    uploadtime = sa.Column(DATETIME, default=datetime.datetime.now)
    desc = sa.Column(VARCHAR(512), nullable=True)
    __table_args__ = (
            sa.UniqueConstraint('md5', name='md5_unique'),
            InnoDBTableBase.__table_args__
    )


class AgentReportLog(PluginTableBase):
    """Table for recode agent status"""
    # build by Gprimarykey
    report_time = sa.Column(BIGINT(unsigned=True), nullable=False,
                            default=uuidutils.Gkey, primary_key=True)
    agent_id = sa.Column(sa.ForeignKey('agents.agent_id'),
                         nullable=False)
    # psutil.process_iter()
    # status()
    # num_fds()
    # num_threads()  num_threads()
    date = sa.Column(CHAR(10), nullable=False)
    hour = sa.Column(TINYINT(unsigned=True), nullable=False)
    min = sa.Column(TINYINT(unsigned=True), nullable=False)
    running = sa.Column(INTEGER(unsigned=True), nullable=False)
    sleeping = sa.Column(INTEGER(unsigned=True), nullable=False)
    num_fds = sa.Column(INTEGER(unsigned=True), nullable=False)
    num_threads = sa.Column(INTEGER(unsigned=True), nullable=False)
    # cpu info  count
    # psutil.cpu_stats() ctx_switches interrupts soft_interrupts
    context = sa.Column(INTEGER(unsigned=True), nullable=False)
    interrupts = sa.Column(INTEGER(unsigned=True), nullable=False)
    sinterrupts = sa.Column(INTEGER(unsigned=True), nullable=False)
    # psutil.cpu_times irq softirq user system nice iowait
    irq = sa.Column(INTEGER(unsigned=True), nullable=False)
    sirq = sa.Column(INTEGER(unsigned=True), nullable=False)
    user = sa.Column(TINYINT(unsigned=True), nullable=False)
    system = sa.Column(TINYINT(unsigned=True), nullable=False)
    nice = sa.Column(TINYINT(unsigned=True), nullable=False)
    iowait = sa.Column(TINYINT(unsigned=True), nullable=False)
    # mem info  MB
    # psutil.virtual_memory() used cached  buffers free
    used = sa.Column(INTEGER(unsigned=True), nullable=False)
    cached = sa.Column(INTEGER(unsigned=True), nullable=False)
    buffers = sa.Column(INTEGER(unsigned=True), nullable=False)
    free = sa.Column(INTEGER(unsigned=True), nullable=False)
    # partion left size MB
    left = sa.Column(BIGINT(unsigned=True), nullable=False)
    # network  count
    # psutil.net_connections()  count(*)
    listen = sa.Column(INTEGER(unsigned=True), nullable=False)
    syn = sa.Column(INTEGER(unsigned=True), nullable=False)
    enable = sa.Column(INTEGER(unsigned=True), nullable=False)
    closeing = sa.Column(INTEGER(unsigned=True), nullable=False)
    __table_args__ = (
            sa.Index('agent_id_index', agent_id),
            sa.Index('date_index', date),
            sa.Index('hour_index', hour),
            MyISAMTableBase.__table_args__
    )


class JobStep(PluginTableBase):
    job_id = sa.Column(sa.ForeignKey('schedulejobs.job_id', ondelete="CASCADE", onupdate='RESTRICT'),
                       nullable=False, primary_key=True)
    step = sa.Column(TINYINT,  nullable=False, primary_key=True)
    executor = sa.Column(VARCHAR(64), nullable=False)
    execute = sa.Column(VARCHAR(256), nullable=True)
    revert = sa.Column(VARCHAR(256), nullable=True)
    method = sa.Column(VARCHAR(64), nullable=True)
    kwargs = sa.Column(BLOB, nullable=True)             # kwargs for executor
    rebind = sa.Column(BLOB, nullable=True)             # execute rebind  taskflow
    provides = sa.Column(BLOB, nullable=True)           # execute provides taskflow
    resultcode = sa.Column(TINYINT, nullable=False, default=manager_common.RESULT_UNKNOWN)
    result = sa.Column(VARCHAR(manager_common.MAX_JOB_RESULT),
                       nullable=False, default='not executed')

    __table_args__ = (
            InnoDBTableBase.__table_args__
    )


class ScheduleJob(PluginTableBase):
    job_id = sa.Column(BIGINT(unsigned=True), default=uuidutils.Gkey, nullable=False, primary_key=True)
    times = sa.Column(TINYINT(unsigned=True),  nullable=True, default=1)
    interval = sa.Column(INTEGER(unsigned=True),  nullable=False, default=300)
    schedule = sa.Column(sa.ForeignKey('agents.agent_id'), nullable=False)
    start = sa.Column(INTEGER(unsigned=True), nullable=False)
    retry = sa.Column(TINYINT, nullable=False, default=0)
    revertall = sa.Column(BOOLEAN, nullable=False, default=0)
    desc = sa.Column(VARCHAR(512), nullable=False)
    kwargs = sa.Column(BLOB, nullable=True)
    steps = orm.relationship(JobStep, backref='schedulejob', lazy='joined',
                             cascade='delete,delete-orphan,save-update')

    __table_args__ = (
            InnoDBTableBase.__table_args__
    )


class User(PluginTableBase):
    id = sa.Column(INTEGER(unsigned=True), nullable=False, primary_key=True, autoincrement=True)
    username = sa.Column(VARCHAR(12), nullable=False)
    salt = sa.Column(CHAR(6), nullable=False)
    password = sa.Column(VARCHAR(32), nullable=False)
    email = sa.Column(VARCHAR(32), nullable=True)
    mobile = sa.Column(VARCHAR(32), nullable=True)
    webchat = sa.Column(VARCHAR(32), nullable=True)
    desc = sa.Column(VARCHAR(256), nullable=True, default=None)
    __table_args__ = (
        sa.UniqueConstraint('username', name='username_unique'),
        InnoDBTableBase.__table_args__
    )
