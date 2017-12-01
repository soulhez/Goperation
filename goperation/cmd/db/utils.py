from simpleutil.config import cfg
from simpleservice.plugin.utils import init_plugin_database

from goperation.manager import models as manager_models


database_init_opts = [
    cfg.StrOpt('user',
               default='root',
               help='mysql database root user name'),
    cfg.StrOpt('passwd',
               default='',
               help='mysql database root password'),
    cfg.StrOpt('host',
               default='127.0.0.1',
               help='mysql host or ipaddress'),
    cfg.PortOpt('port',
                default=3306,
                 help='mysql server post'),
    cfg.StrOpt('schema',
               required=True,
               help='target mysql database schema')
]


def init_manager(db_info):
    init_plugin_database(db_info, manager_models)
