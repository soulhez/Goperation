from simpleutil.config import cfg

websocket_opts = [
    cfg.StrOpt('token',
               required=True,
               short='t',
               help='webesocket socket connect token'),
    cfg.StrOpt('home',
               short='h',
               required=True,
               help='webesocket home path'
               ),
    cfg.IPOpt('listen',
              default='0.0.0.0',
              short='l',
              help='webesocket listen ip'),
    cfg.PortOpt('port',
                short='p',
                required=True,
                help='webesocket listen port'),
    cfg.BoolOpt('strict',
                default=True,
                help='webesocket use strict mode'),
    cfg.IPOpt('heartbeat',
              default=5,
              help='webesocket socket connect timeout'),
]
