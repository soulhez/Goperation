from simpleservice.common import *

MAX_ENDPOINT_NAME_SIZE = 64
MAX_HOST_NAME_SIZE = 128

ENV_REQUEST_ID = 'goperation.request_id'

# agent name
AGENT = 'agent'
# agent tpye
APPLICATION = 'application'
SCHEDULER = 'scheduler'
DATABASE = 'database'


# -----------status of agent--------------
ACTIVE = 1
UNACTIVE = 0
SOFTBUSY = -10
INITIALIZING = -20
HARDBUSY = -30
DELETED = -127
# pre delete status can not be recode into database
# agent set status as PERDELETE when get a rpc cast of delete_agent_precommit
PERDELETE = -128
# -----------status of agent--------------


# ------status of async request-----------
FINISH = 1
UNFINISH = 0
# ------status of async request-----------

# default time of agent status key in redis
ONLINE_EXIST_TIME = 600

MAX_REQUEST_RESULT = 256
MAX_DETAIL_RESULT = 8192
MAX_AGENT_RESULT = 1024

MAX_PORTS_RANGE_SIZE = 1024

MAX_ROW_PER_REQUEST = 100
ROW_PER_PAGE = MAX_ROW_PER_REQUEST/10


RESULT_NOT_ALL_SUCCESS = RESULT_SUCCESS + 1
EXEC_RPC_FUNCTION_ERROR = RESULT_NOT_ALL_SUCCESS + 1