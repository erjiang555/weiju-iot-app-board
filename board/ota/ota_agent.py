#!/usr/bin/env python3
"""OneNET SOTA polling client. Only signed, allowlisted applications install."""
import base64
import fcntl
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import signal
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from ota_core import Installer, validate_bundle, atomic_json, read_json, MAX_PACKAGE

ROOT = Path(__file__).resolve().parent
STATE = Path('/var/lib/weiju-ota')
LOG = logging.getLogger('weiju-ota')
STOP = False


def device_token(config):
    expires = str(int(time.time()) + 3600)
    resource = 'products/{}/devices/{}'.format(config['product_id'], config['device_name'])
    version = '2018-10-31'
    text = '\n'.join((expires, 'sha256', resource, version)).encode()
    sign = base64.b64encode(hmac.new(base64.b64decode(config['device_key']), text,
                                   hashlib.sha256).digest()).decode('ascii')
    return '&'.join('{}={}'.format(k, quote(v, safe='')) for k, v in [
        ('version', version), ('res', resource), ('et', expires), ('method', 'sha256'), ('sign', sign)])


class OTAClient(object):
    def __init__(self, config):
        self.config = config
        self.context = ssl.create_default_context(cafile=str(ROOT / 'https-ca.pem'))
        self.base = 'https://iot-api.heclouds.com/fuse-ota/{}/{}/'.format(
            quote(config['product_id'], safe=''), quote(config['device_name'], safe=''))

    def request(self, endpoint, body=None, headers=None):
        all_headers = {'Authorization': device_token(self.config)}
        all_headers.update(headers or {})
        data = None
        if body is not None:
            all_headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        return urlopen(Request(self.base + endpoint, data=data, headers=all_headers),
                       timeout=20, context=self.context)

    def json(self, endpoint, body=None):
        with self.request(endpoint, body) as response:
            result = json.loads(response.read(65536).decode('utf-8'))
        if result.get('code') != 0:
            raise RuntimeError('OneNET OTA code={} {}'.format(result.get('code'), result.get('msg', '')))
        return result.get('data')

    def report_version(self, version):
        return self.json('version', {'s_version': version, 'f_version': '4.1.15'})

    def check(self, version):
        try:
            return self.json('check?' + urlencode({'type': 2, 'version': version}))
        except RuntimeError as error:
            if 'code=12012 ' in str(error):
                return None
            raise

    def status(self, tid, step):
        return self.json('{}/status'.format(tid), {'step': step})

    def download(self, task, destination, progress):
        total = task['size']
        offset = destination.stat().st_size if destination.exists() else 0
        if offset > total:
            destination.unlink()
            offset = 0
        if offset == total:
            return
        # OneNET's Range header omits the conventional "bytes=" prefix.
        with self.request('{}/download'.format(task['tid']),
                          headers={'Range': '{}-'.format(offset)}) as response:
            if response.headers.get('Ota-Errno', '0') != '0':
                raise RuntimeError('OneNET download rejected')
            content_range = response.headers.get('Content-Range', '')
            if offset and not (content_range.startswith('bytes {}-'.format(offset)) or
                               content_range.startswith('{}-'.format(offset))):
                raise RuntimeError('Resume offset was not confirmed by the server')
            length = int(response.headers.get('Content-Length', '0'))
            if length and length != total - offset:
                raise RuntimeError('Download response size does not match task')
            with open(str(destination), 'ab' if offset else 'wb') as stream:
                while True:
                    if STOP:
                        raise InterruptedError('Service stopping')
                    block = response.read(min(65536, total - offset + 1))
                    if not block:
                        break
                    offset += len(block)
                    if offset > total:
                        raise ValueError('Download exceeds declared task size')
                    stream.write(block)
                    progress(min(99, offset * 100 // total))
                stream.flush()
                os.fsync(stream.fileno())
            if offset != total:
                raise IOError('Incomplete OTA download; will resume')


def write_status(**values):
    path = Path('/var/run/weiju-ota/status.json')
    atomic_json(path, dict(values, updated_time=int(time.time())))


def process_task(client, installer, config, task):
    if (not isinstance(task, dict) or type(task.get('tid')) is not int or task['tid'] < 1 or
            type(task.get('size')) is not int or not 1 <= task['size'] <= MAX_PACKAGE or
            task.get('type') != 1 or not isinstance(task.get('md5'), str) or len(task['md5']) != 32):
        raise ValueError('Unsupported or invalid OneNET task metadata')
    tid = task['tid']
    task_root = STATE / 'downloads' / str(tid)
    task_root.mkdir(parents=True, exist_ok=True)
    metadata_file = task_root / 'task.json'
    identity = ('tid', 'target', 'size', 'md5', 'type')
    if metadata_file.exists() and any(read_json(metadata_file).get(k) != task.get(k) for k in identity):
        shutil.rmtree(str(task_root))
        task_root.mkdir()
    atomic_json(metadata_file, task)
    package = task_root / 'package.tar.gz.part'
    if shutil.disk_usage(str(STATE)).free < task['size'] * 4 + 64 * 1024 * 1024:
        client.status(tid, 102)
        raise RuntimeError('Insufficient space for package and rollback backup')
    previous = installer.current()['version']
    write_status(phase='downloading', version=previous, target=task['target'], progress=0)
    last_progress = [-10]
    def progress(value):
        if value // 20 != last_progress[0] // 20:
            write_status(phase='downloading', version=previous, target=task['target'], progress=value)
            client.status(tid, value)
            last_progress[0] = value
    client.download(task, package, progress)
    digest = hashlib.md5()
    with open(str(package), 'rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    if digest.hexdigest().lower() != task['md5'].lower():
        package.unlink()
        client.status(tid, 205)
        raise ValueError('OneNET package MD5 mismatch')
    staging = task_root / 'staging'
    if staging.exists():
        shutil.rmtree(str(staging))
    try:
        manifest = validate_bundle(package, staging, ROOT / 'signing-public.pem',
                                   config['product_id'], previous, task['target'])
    except Exception:
        client.status(tid, 204)
        raise
    # Persist task outcome before replacing any file, including across power loss.
    pending = {'tid': tid, 'target': task['target'], 'previous': previous}
    atomic_json(STATE / 'pending-result.json', pending)
    client.status(tid, 101)
    write_status(phase='installing', version=previous, target=task['target'], progress=100)
    try:
        backup = installer.apply(staging, manifest)
    except Exception:
        pending['step'] = 206
        atomic_json(STATE / 'pending-result.json', pending)
        raise
    pending['step'] = 201
    atomic_json(STATE / 'pending-result.json', pending)
    LOG.info('Installed %s; rollback files retained at %s', task['target'], backup)
    finish_pending(client, installer)
    shutil.rmtree(str(task_root))


def finish_pending(client, installer):
    path = STATE / 'pending-result.json'
    if not path.exists():
        return
    pending = read_json(path)
    current = installer.current()['version']
    step = pending.get('step', 201 if current == pending['target'] else 206)
    task_state = client.json('{}/check'.format(pending['tid']))
    if task_state and task_state.get('status') not in (4, 5, 6):
        client.status(pending['tid'], step)
    client.report_version(current)
    path.unlink()
    write_status(phase='idle' if step == 201 else 'rolled_back', version=current,
                 last_task=pending['tid'], result=step)


def main():
    global STOP
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1 and sys.argv[1] == '--recover':
        result = Installer().recover(start_services=False)
        print('OTA_BOOT_RECOVERY', result)
        return
    os.makedirs('/var/log/weiju-ota', exist_ok=True)
    log = RotatingFileHandler('/var/log/weiju-ota/ota.log', maxBytes=262144, backupCount=2)
    log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(log)
    lock = open('/var/run/weiju-ota.pid', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()) + '\n')
    lock.flush()
    def stop(signum, frame):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with open('/etc/weiju-onenet.json') as stream:
        config = json.load(stream)
    installer = Installer()
    installer.recover()
    client = OTAClient(config)
    LOG.info('OneNET SOTA client started; installed=%s', installer.current()['version'])
    next_check = 0
    last_version = None
    while not STOP:
        if time.monotonic() < next_check:
            time.sleep(0.5)
            continue
        next_check = time.monotonic() + 60
        try:
            finish_pending(client, installer)
            version = installer.current()['version']
            if last_version != version:
                client.report_version(version)
                last_version = version
            task = client.check(version)
            if task and task.get('tid'):
                process_task(client, installer, config, task)
                last_version = None
            else:
                write_status(phase='idle', version=version)
        except Exception as error:
            LOG.warning('OTA check/install: %s', error)
            write_status(phase='waiting', version=installer.current()['version'],
                         message=str(error)[:150])
    LOG.info('OTA service stopped')


if __name__ == '__main__':
    main()
