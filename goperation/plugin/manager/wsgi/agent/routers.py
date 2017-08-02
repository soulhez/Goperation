from simpleservice.wsgi import router
from simpleservice.wsgi.middleware import controller_return_response

from goperation.plugin.manager.wsgi.agent import controller
from goperation.plugin.manager import common as manager_common

COLLECTION_ACTIONS = ['index', 'create']
MEMBER_ACTIONS = ['show', 'update', 'delete']


class Routers(router.RoutersBase):

    resource_name = manager_common.AGENT
    collection_name = resource_name + 's'

    def append_routers(self, mapper, routers):
        controller_intance = controller_return_response(controller.AgentReuest(),
                                                        controller.FAULT_MAP)
        collection = mapper.collection(collection_name=self.collection_name,
                                       resource_name=self.resource_name,
                                       controller=controller_intance,
                                       member_prefix='/{agent_id}',
                                       collection_actions=COLLECTION_ACTIONS,
                                       member_actions=MEMBER_ACTIONS)
        # send file to agent
        collection.member.link('file', method='POST')
        # upgrade agent code (upgrade rpm package)
        collection.member.link('upgrade', method='PUT')
        collection.member.link('active', method='PATCH')
        collection.member.link('status', method='GET')
        # agent show online when it start
        self._add_resource(
            mapper, controller_intance,
            path='/%s/online' % self.resource_name,
            put_action='online')

        return collection
