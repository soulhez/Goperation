from simpleutil.utils.threadgroup import ThreadGroup
#  Routes or wsgi
CORE_ROUTES = []
EXTEND_ROUTES = []
# work pool for plugin servies
pool = ThreadGroup(thread_pool_size=100)
