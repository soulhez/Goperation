import time
import simpleservice

from simpleutil.config import cfg
from goperation import config

from goperation.api.client import ManagerClient


a = 'C:\\Users\\loliz_000\\Desktop\\etc\\goperation\\goperation.conf'
b = 'C:\\Users\\loliz_000\\Desktop\\etc\\goperation\\gcenter.conf'
config.configure('test', [a, b])


wsgi_url = '172.31.0.110'
wsgi_port = 7999

client = ManagerClient(wsgi_url, wsgi_port, token='goperation-trusted-token')


# for r in client.asyncs_index()['data']:
#     print r

print client.async_show(request_id='fa768282-d91f-40fc-9ad0-3c59254d799c', body={'details': True})

# asyncs_index(self, body
# async_show(self, request_id, body)
# async_details(self, request_id, body)

# async_responses(self, request_id, body)
# async_overtime(self, request_id, body)