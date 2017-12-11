from simpleutil.config import cfg
from simpleutil.log import log as logging

from simpleservice.server import LaunchWrapper
from simpleservice.server import launch
from simpleservice.rpc.service import LauncheRpcServiceBase

from simpleservice.rpc.config import rpc_server_opts

from goperation import threadpool
from goperation import config as goperation_config

from goperation.manager.rpc.server.config import gop_rpc_server_opts
from goperation.manager.rpc.server import RpcServerManager


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


def configure(config_files=None, config_dirs=None):
    # create a new project and group named gcenter
    name='gcenter'
    # init goperation config
    gcenter_group = goperation_config.configure(name, config_files, config_dirs)
    # set gcenter config
    CONF.register_opts(rpc_server_opts, group=gcenter_group)
    CONF.register_opts(gop_rpc_server_opts, group=gcenter_group)
    return CONF[gcenter_group.name]


def run(procname, config_files, config_dirs=None):
    conf = configure(config_files=config_files, config_dirs=config_dirs)
    wrappers = []
    rpc_service = LauncheRpcServiceBase(RpcServerManager(),
                                        user=conf.rpc_user,
                                        group=conf.rpc_group,
                                        plugin_threadpool=threadpool)
    rpc_wrapper = LaunchWrapper(service=rpc_service, workers=conf.rpc_process)
    wrappers.append(rpc_wrapper)
    launch(wrappers, procname)

