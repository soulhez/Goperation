from simpleflow import api
from simpleflow.task import Task
from simpleflow.types import failure
from simpleflow.storage import Connection
from simpleflow.storage.middleware import LogBook
from simpleflow.engines.engine import ParallelActionEngine

from goperation.taskflow import common
from goperation.manager.rpc.agent.application import taskflow

LOG = taskflow.LOG

class StandardTask(Task):

    def __init__(self, middleware, provides=None, rebind=None,
                 requires=None, revert_requires=None):
        super(StandardTask, self).__init__(name='%s_%d' % (self.__class__.__name__,  middleware.entity),
                                           provides=provides,
                                           requires=requires, auto_extract=True, rebind=rebind, inject=None,
                                           ignore_list=None, revert_rebind=revert_requires, revert_requires=None)
        self.middleware = middleware
        middleware.set_return(self.__class__.__name__)

    def revert(self, result, *args, **kwargs):
        if isinstance(result, failure.Failure):
            if LOG:
                LOG.error(result.pformat())
            self.middleware.set_return(self.__class__.__name__, common.EXECUTE_FAIL)

    def post_execute(self):
        self.middleware.set_return(self.__class__.__name__, common.EXECUTE_SUCCESS)


class EntityTask(Task):

    def __init__(self, session, flow, store):
        super(EntityTask, self).__init__(name='engine_%s' % flow.name)
        book = LogBook(self.name)
        self.book_uuid = book.uuid
        self.connection = Connection(session)
        self.engine = api.load(self.connection, flow, book=book, store=store,
                               engine_cls=ParallelActionEngine)

    def execute(self):
        try:
            self.engine.run()
        except Exception as e:
            if LOG:
                LOG.exception('Entity execute cache %s' % e.__class__.__name__)
        finally:
            # cleanup sub taskflow engine logbook
            self.connection.destroy_logbook(self.book_uuid)


def format_store_rebind(store, rebind):
    for key in rebind:
        if key not in store:
            store[key] = None
