from goperation.plugin.manager.wsgi.agent import routers as agent_routes
from goperation.plugin.manager.wsgi.agent.scheduler import routers as scheduler_routes
from goperation.plugin.manager.wsgi.asyncrequest import routers as request_routes

CORE_ROUTES = [request_routes, agent_routes, request_routes]

EXTEND_ROUTES = []
