import os
import six

from eventlet import event
from eventlet.semaphore import Semaphore

from simpleutil.utils import uuidutils
from simpleutil.utils import digestutils
from simpleutil.utils import jsonutils
from simpleutil.utils.singleton import singleton

from simpleservice.ormdb.engines import create_engine
from simpleservice.ormdb.orm import get_maker
from simpleservice.ormdb.api import model_query

from goperation.filemanager import common
from goperation.filemanager import exceptions
from goperation.filemanager import models
from goperation.filemanager import downloader


class TargetFile(object):

    def __init__(self, source):
        self.source = source
        self.realpath = None

    def clean(self):
        if self.realpath is not None and os.path.exists(self.realpath):
            os.remove(self.realpath)


@singleton
class FileManager(object):
    # file info schema
    SCHEMA = {
        'type': 'object',
        'properties': {
            "downloader": {'type': 'string'},
            "adapter_args": {'type': 'array'},
            "address": {'type': 'string'},
            "ext": {'type': 'string'},
            "size": {'type': 'integer'},
            "desc": {'type': 'string'},
            "uploadtime": {'type': 'string', 'format': 'date-time'},
            'marks': {
                'type': 'object',
                'properties': {
                    'uuid': {'type': 'string', 'format': 'uuid'},
                    'crc32': {'type': 'string', 'format': 'crc32'},
                    'md5': {'type': 'string', 'format': 'md5'},
                    },
                'required': ['uuid', 'crc32', 'md5']
            }
        },
        'required': ['address', 'ext', 'size', 'uploadtime', 'marks']
    }

    def __init__(self, conf, rootpath, threadpool, fget):
        self.threadpool = threadpool
        self.path = os.path.join(rootpath, conf.folder)
        self.fget = fget
        self.localfiles = {}
        self.downloading = {}
        self.lock = Semaphore()
        # init sqlite session
        engine = create_engine(sql_connection='sqlite:///%s' % conf.sqlite,
                               logging_name='filemanager')
        if not engine.has_table(models.FileDetail.__tablename__):
            # create table if needed
            models.FileManagerTables.metadata.create_all(engine)
        session_maker = get_maker(engine)
        self.session = session_maker()

    def scanning(self, strict=False):
        if not self.session:
            raise exceptions.FileManagerError('FileManager database session is None')
        not_match_files = []
        local_files = {}
        with self.lock:
            if self.downloading:
                raise exceptions.DownLoading('Can not scan when downlonding')
            # files in local disk
            for filename in os.listdir(self.path):
                full_path = os.path.join(self.path, filename)
                if os.path.isfile(full_path):
                    size = os.path.getsize(full_path)
                    uuid, ext = os.path.splitext(filename)
                    if len(ext) < 1 or not ext.startswith(os.extsep):
                        if strict:
                            raise RuntimeError('File with name %s ext value error' % filename)
                        continue
                    if not uuidutils.is_uuid_like(uuid):
                        if strict:
                            raise RuntimeError('File with name %s is not uuid' % filename)
                        continue
                    if uuid in local_files:
                        if strict:
                            raise RuntimeError('File with uuid %s is duplication' % filename)
                    local_files[uuid] = dict(size=size, ext=ext[1:])
            # files record in database
            self.localfiles.clear()
            query = model_query(self.session, models.FileDetail)
            files = query.all()
            with self.session.begin():
                for _file_detail in files:
                    filename = _file_detail.uuid + os.extsep + _file_detail.ext
                    file_path = os.path.join(self.path, filename)
                    try:
                        local_file = local_files.pop(_file_detail.uuid)
                        local_size = local_file['size']
                        local_ext = local_file['ext']
                        if local_size != _file_detail.size or local_ext != _file_detail.ext:
                            not_match_files.append(file_path)
                            _file_detail.delete()
                            continue
                    except KeyError:
                        # delete no exist file from database
                        _file_detail.delete()
                        continue
                    self.localfiles[file_path] = dict(crc32=_file_detail.crc32,
                                                      md5=_file_detail.md5,
                                                      uuid=_file_detail.uuid)
            with self.session.begin():
                while local_files:
                    uuid, local_file = local_files.popitem()
                    local_size = local_file['size']
                    local_ext = local_file['ext']
                    filename = uuid + os.extsep + local_ext
                    file_path = os.path.join(self.path, filename)
                    crc32 = digestutils.filecrc32(file_path)
                    md5 = digestutils.filemd5(file_path)
                    _file_detail = models.FileDetail(uuid=uuid, size=local_size,
                                                     crc32=crc32, md5=md5, ext=local_ext,
                                                     desc='add from scanning')
                    # add file record into database
                    self.session.add(_file_detail)
                    self.localfiles[file_path] = dict(crc32=crc32,
                                                      md5=md5,
                                                      uuid=uuid)
            # delete check fail files
            for _file in not_match_files:
                os.remove(_file)
            del not_match_files[:]

    def clean_expired(self):
        pass

    def stop(self):
        with self.lock:
            for ev in six.itervalues(self.downloading):
                try:
                    ev.wait()
                except:
                    pass
            self.session.close()
            self.session = None

    def find(self, mark):
        for path, marks in six.iteritems(self.localfiles):
            if mark in six.itervalues(marks):
                target = TargetFile(mark)
                target.realpath = path
                return target

    def get(self, target, download=True, timeout=None):
        if isinstance(target, basestring):
            target = TargetFile(target)
        if not isinstance(target, TargetFile):
            raise TypeError('Target type is not TargetFile')
        for path, marks in six.iteritems(self.localfiles):
            if target.source in six.itervalues(marks):
                target.realpath = path
                return target.realpath
        if download:
            mark = target.source
            with self.lock:
                if not self.session:
                    raise RuntimeError('FileManager closed')
                if mark in self.downloading:
                    th = self.downloading[mark]
                else:
                    th = self.threadpool.add_thread(self._download, mark, timeout)
            th.wait()
            return self.get(target=target, download=False)
        else:
            raise exceptions.NoFileFound('File Manager can not find file of %s' % target.source)

    def _download(self, mark, timeout):
        file_info = self.fget(mark)
        jsonutils.schema_validate(file_info, FileManager.SCHEMA)
        for mark in six.itervalues(file_info['marks']):
            if mark in self.downloading:
                th = self.downloading[mark]
                th.wait()
                return
        address = file_info['address']
        local_file_name = file_info['marks']['uuid'] + os.extsep + file_info['ext']
        local_path = os.path.join(self.path, local_file_name)
        if os.path.exists(local_path):
            raise exceptions.NoFileFound('File %s alreday eixst' % local_file_name)
        # default downloader http
        _downloader = downloader_factory(file_info.get('downloader', 'http'),
                                         file_info.get('adapter_args', []))
        ev = event.Event()
        self.downloading[mark] = ev
        # async downloading thread start
        th = self.threadpool.add_thread(_downloader.download, address, local_path, timeout)
        try:
            crc32, md5, size = th.wait()
            if crc32 != file_info['marks']['crc32'] \
                    or md5 != file_info['marks']['md5'] \
                    or size != file_info['size']:
                raise exceptions.FileNotMatch('File md5 or crc32 or size not the same')
        except Exception as e:
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except (OSError, IOError):
                    pass
            ev.send(exc=e)
            raise
        else:
            self.localfiles[local_path] = dict(crc32=crc32,
                                               md5=md5,
                                               uuid=file_info['uuid'])
            file_detil = models.FileDetail(uuid=file_info['uuid'], size=size,
                                           crc32=crc32, md5=md5,
                                           ext=file_info['ext'],
                                           desc=file_info.get('desc', 'unkonwn file'),
                                           address=file_info['address'],
                                           uploadtime=file_info.get('uploadtime'))
            try:
                self.session.add(file_detil)
                self.flush()
                ev.send(result=None)
            except Exception as e:
                ev.send(exc=e)
                raise
        finally:
            self.downloading.pop(mark)


def downloader_factory(adapter_cls, cls_args):
    if not adapter_cls.endswith('_cls'):
        adapter_cls += '_cls'
    cls = getattr(downloader, adapter_cls)
    return cls(*cls_args)
