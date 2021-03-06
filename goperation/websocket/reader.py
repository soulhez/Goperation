# -*- coding:utf-8 -*-
# Copyright (c) 2012 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from simpleutil.config import cfg
import os
import time
import select
import sys
import errno
import cgi
# import logging

import eventlet
from websockify import websocket

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

from simpleutil.utils import systemutils
from simpleutil.utils import jsonutils
from simpleutil.utils.tailutils import TailWithF
from simpleutil.utils.threadgroup import ThreadGroup


from goperation.websocket import exceptions
from goperation.websocket.base import GopWebSocketServerBase
from goperation.websocket.base import fetch_token

CONF = cfg.CONF

reader_opts = [
    cfg.IntOpt('lines',
               short='n',
               default=10,
               min=1,
               help='output the last n lines, instead of the last 10'),
    ]


class FileSendRequestHandler(websocket.WebSocketRequestHandler):

    def __init__(self, req, addr, server):
        self.lastsend = 0
        self.timeout = CONF.heartbeat * 3
        websocket.WebSocketRequestHandler.__init__(self, req, addr, server)

    def address_string(self):
        """
        fuck gethostbyaddr!!!!!
        fuck gethostbyaddr on logging!!!
        """
        host, port = self.client_address[:2]
        return host

    def do_POST(self):
        self.send_error(405, "Method Not Allowed")

    def do_GET(self):
        # 禁止通过相对路径回退
        if '..' in self.path:
            raise ValueError('Path value is illegal')

        path = self.translate_path(self.path)
        # 禁止根目录
        if path == '/':
            raise ValueError('Home value error')
        # 校验token
        try:
            if fetch_token(self.path, self.headers) != CONF.token:
                self.logger.error('Token not match')
                self.send_error(401, "Token not match")
                return None
        except exceptions.WebSocketError as e:
            self.send_error(405, e.message)
            return None

        if not self.handle_websocket():
            # 普通的http get方式
            if self.only_upgrade:
                self.send_error(405, "Method Not Allowed")
            else:
                # 如果path是文件夹,允许列出文件夹
                if os.path.isdir(path):
                    self.logger.info('handle websocket finish target is path')

                    _path = self.path.split('?',1)[0]
                    parameters = self.path[len(_path):]
                    _path = _path.split('#', 1)[0]
                    if not _path.endswith('/'):
                        # redirect browser - doing basically what apache does
                        _path = _path + "/" + parameters
                        self.send_response(301)
                        self.send_header("Location", _path)
                        self.end_headers()
                        return None
                    try:
                        filelist = os.listdir(path)
                    except os.error:
                        self.send_error(404, "No permission to list directory")
                        return None
                    _filelist = []
                    filelist.sort(key=lambda a: a.lower())
                    f = StringIO()
                    for name in filelist:
                        fullname = os.path.join(path, name)
                        displayname = name
                        if os.path.isdir(fullname):
                            displayname = name + "/"
                        if os.path.islink(fullname):
                            displayname = name + "@"
                        _filelist.append(cgi.escape(displayname))
                    # 文件夹列表生成json
                    buf = jsonutils.dumps_as_bytes(_filelist)
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=%s" % systemutils.SYSENCODE)
                    self.send_header("Content-Length", len(buf))
                    self.end_headers()
                    self.wfile.write(buf)
                    return f.close()
                else:
                    self.send_error(405, "Method Not Allowed")

    def new_websocket_client(self):
        # websocket握手成功后设置自动关闭链接
        self.close_connection = 1
        self.logger.info('Suicide cancel at %d' % int(time.time()))
        # 取消自动退出
        self.server.suicide.cancel()

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return

        cqueue = {'cqueue': []}
        rlist = [self.request]
        wlist = [self.request]

        def output(buf):
            cqueue['cqueue'].append(buf)
            self.lastsend = int(time.time())

        # 实现
        tailf = TailWithF(path=path, output=output,
                          logger=self.logger.error, rows=CONF.lines)
        pool = ThreadGroup()
        tailf.start(pool)
        self.lastrecv = int(time.time())
        try:
            while True:
                if int(time.time()) - self.lastsend > CONF.heartbeat:
                    # 发送心跳包
                    self.send_ping()
                    # 接收心跳返回
                    bufs, closed = self.recv_frames()
                    if closed:
                        self.logger.info('Send ping find close')
                        return
                    if bufs:
                        self.logger.info('Send ping but recv buffer')
                        return
                    self.lastsend = int(time.time())
                if tailf.stoped:
                    self.logger.warning('Tail intance is closed')
                    return
                try:
                    ins, outs, excepts = select.select(rlist, wlist, [], 1.0)
                except (select.error, OSError):
                    exc = sys.exc_info()[1]
                    if hasattr(exc, 'errno'):
                        err = exc.errno
                    else:
                        err = exc[0]

                    if err != errno.EINTR:
                        raise
                    else:
                        eventlet.sleep(0.01)
                        continue

                if excepts:
                    raise Exception("Socket exception")

                if self.request in outs:
                    if not cqueue['cqueue']:
                        eventlet.sleep(0.01)
                    else:
                        buffs = cqueue['cqueue']
                        cqueue['cqueue'] = []
                        # Send queued target data to the client
                        self.send_frames(buffs)
                        self.lastsend = int(time.time())

                if self.request in ins:
                    # Receive client data, decode it, and queue for target
                    bufs, closed = self.recv_frames()
                    if closed:
                        self.logger.info('Client send close')
                        return
                    self.logger.info('Client send to server')
                    return
        finally:
            tailf.stop()


class FileReadWebSocketServer(GopWebSocketServerBase):
    
    def __init__(self, logger):
        super(FileReadWebSocketServer, self).__init__(RequestHandlerClass=FileSendRequestHandler, logger=logger)
