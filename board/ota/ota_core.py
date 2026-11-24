"""Signed application bundles and recoverable installs, Python 3.5+."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import struct
import subprocess
import tarfile
import time
from urllib.request import urlopen

BOARD = 'imx6ull-512-emmc'
MAX_PACKAGE = 32 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
FILES = {
    'QDesktop': ('/opt/QDesktop', 'qdesktop', 0o755),
    'camera_web.py': ('/opt/weiju-camera-web/camera_web.py', 'camera', 0o600),
    'index.html': ('/opt/weiju-camera-web/index.html', 'camera', 0o600),
    'onenet_agent.py': ('/opt/weiju-onenet/onenet_agent.py', 'onenet', 0o600),
    'voice_service.py': ('/opt/weiju-voice/voice_service.py', 'voice', 0o600),
    'voice_core.py': ('/opt/weiju-voice/voice_core.py', 'voice', 0o600),
}


def sha256(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = str(path) + '.tmp'
    with open(temporary, 'w') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, str(path))
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path):
    with open(str(path)) as stream:
        return json.load(stream)


def version_tuple(value):
    if not isinstance(value, str) or not re.match(r'^\d{1,5}\.\d{1,5}\.\d{1,5}$', value):
        raise ValueError('Version must be major.minor.patch')
    return tuple(int(part) for part in value.split('.'))


def validate_bundle(package, staging, public_key, product_id, current_version, target=None):
    """Inspect every member before extracting. Never execute a package script."""
    if Path(package).stat().st_size > MAX_PACKAGE:
        raise ValueError('Package exceeds size limit')
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(str(package), 'r:gz') as archive:
            members = {}
            total = 0
            for member in archive:
                if len(members) >= len(FILES) + 2:
                    raise ValueError('Too many archive members')
                name = member.name
                if name in members or name not in set(FILES) | {'manifest.json', 'manifest.sig'}:
                    raise ValueError('Unknown or duplicate archive path')
                if not member.isfile() or member.size < 1:
                    raise ValueError('Only non-empty regular files are supported')
                if member.size > MAX_EXPANDED:
                    raise ValueError('Archive member too large')
                total += member.size
                if total > MAX_EXPANDED:
                    raise ValueError('Expanded package too large')
                members[name] = member
            if not {'manifest.json', 'manifest.sig'} <= set(members):
                raise ValueError('Signature or manifest missing')
            if members['manifest.json'].size > 16384 or members['manifest.sig'].size != 256:
                raise ValueError('Invalid signature metadata size')
            for name in ('manifest.json', 'manifest.sig'):
                with open(str(staging / name), 'wb') as stream:
                    stream.write(archive.extractfile(members[name]).read())
            subprocess.check_call(['openssl', 'dgst', '-sha256', '-verify', str(public_key),
                                   '-signature', str(staging / 'manifest.sig'),
                                   str(staging / 'manifest.json')],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            manifest = read_json(staging / 'manifest.json')
            if (manifest.get('format') != 1 or manifest.get('board') != BOARD or
                    manifest.get('product_id') != product_id or manifest.get('kind') != 'weiju-apps'):
                raise ValueError('Package belongs to another board or product')
            version = manifest.get('version')
            if version_tuple(version) <= version_tuple(current_version):
                raise ValueError('Package version must be newer than the installed version')
            if manifest.get('from_version') != current_version or (target and version != target):
                raise ValueError('Source or target version does not match the upgrade task')
            files = manifest.get('files')
            if not isinstance(files, dict) or not files or set(files) != set(members) - {'manifest.json', 'manifest.sig'}:
                raise ValueError('Manifest payload list does not match archive')
            for name, metadata in files.items():
                if name not in FILES or not isinstance(metadata, dict):
                    raise ValueError('Unsupported payload')
                if metadata.get('size') != members[name].size:
                    raise ValueError('Payload size mismatch')
                if not re.match(r'^[0-9a-f]{64}$', str(metadata.get('sha256', ''))):
                    raise ValueError('Invalid payload hash')
                with open(str(staging / name), 'wb') as output:
                    shutil.copyfileobj(archive.extractfile(members[name]), output, 65536)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256(staging / name) != metadata['sha256']:
                    raise ValueError('Payload SHA-256 mismatch')
                if name.endswith('.py'):
                    compile((staging / name).read_text(encoding='utf-8'), name, 'exec')
                if name == 'QDesktop':
                    header = (staging / name).open('rb').read(20)
                    if header[:6] != b'\x7fELF\x01\x01' or struct.unpack('<H', header[18:20])[0] != 40:
                        raise ValueError('QDesktop is not an ARM little-endian 32-bit ELF')
            return manifest
    except Exception:
        shutil.rmtree(str(staging))
        raise


class Services(object):
    def pids(self, service):
        expected = {'qdesktop': '/opt/QDesktop', 'camera': '/opt/weiju-camera-web/camera_web.py',
                    'onenet': '/opt/weiju-onenet/onenet_agent.py',
                    'voice': '/opt/weiju-voice/voice_service.py'}[service]
        result = []
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                args = path.read_bytes().split(b'\0')
                if expected.encode('utf-8') in args:
                    result.append(int(path.parent.name))
            except OSError:
                pass
        return result

    def stop(self, service):
        if service != 'qdesktop':
            script = '/etc/init.d/' + {'camera': 'weiju-camera-web',
                                       'onenet': 'weiju-onenet',
                                       'voice': 'weiju-voice'}[service]
            subprocess.check_call([script, 'stop'], stdout=subprocess.DEVNULL, timeout=35)
            return
        pids = self.pids(service)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 8
        while self.pids(service) and time.monotonic() < deadline:
            time.sleep(0.2)
        if self.pids(service):
            raise RuntimeError('QDesktop did not stop; refusing to replace a running app')

    def start(self, service):
        if self.pids(service):
            return
        if service == 'qdesktop':
            log = open('/var/log/weiju-ota/qdesktop-start.log', 'w')
            try:
                subprocess.Popen(['/bin/sh', '-c', '. /etc/profile; exec /opt/QDesktop'],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
            finally:
                log.close()
        else:
            subprocess.check_call(['/etc/init.d/' + {'camera': 'weiju-camera-web',
                                                     'onenet': 'weiju-onenet',
                                                     'voice': 'weiju-voice'}[service],
                                   'start'], stdout=subprocess.DEVNULL, timeout=10)

    def healthy(self, services):
        deadline = time.monotonic() + 25
        stable_since = None
        while time.monotonic() < deadline:
            good = all(self.pids(name) for name in services)
            if good and 'camera' in services:
                try:
                    good = isinstance(json.loads(urlopen('http://127.0.0.1:8080/api/status', timeout=2).read().decode()), dict)
                except Exception:
                    good = False
            if good and 'onenet' in services:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.settimeout(2)
                        connection.connect('/var/run/weiju-onenet/control.sock')
                        connection.sendall(b'{"action":"status"}\n')
                        good = json.loads(connection.recv(8192).decode()).get('ok') is True
                except Exception:
                    good = False
            if good and 'voice' in services:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.settimeout(2)
                        connection.connect('/var/run/weiju-voice/control.sock')
                        connection.sendall(b'{"action":"status"}\n')
                        good = json.loads(connection.recv(8192).decode()).get('ok') is True
                except Exception:
                    good = False
            if good:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= 12:
                    return True
            else:
                stable_since = None
            time.sleep(0.5)
        return False


class Installer(object):
    def __init__(self, root='/', services=None):
        self.root = Path(root)
        self.state = self.root / 'var/lib/weiju-ota'
        self.state.mkdir(parents=True, exist_ok=True)
        self.services = services or Services()
        self.transaction = self.state / 'transaction.json'
        self.version_file = self.state / 'version.json'

    def destination(self, name):
        return self.root / FILES[name][0].lstrip('/')

    def current(self):
        return read_json(self.version_file)

    def replace(self, source, destination, mode):
        destination = Path(destination)
        temporary = destination.parent / (destination.name + '.ota-new')
        with open(str(source), 'rb') as src, open(str(temporary), 'wb') as dst:
            shutil.copyfileobj(src, dst, 65536)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(str(temporary), mode)
        os.replace(str(temporary), str(destination))
        sync_directory(destination.parent)

    def recover(self, start_services=True):
        if not self.transaction.exists():
            return None
        transaction = read_json(self.transaction)
        if transaction['phase'] == 'committed':
            self.transaction.unlink()
            sync_directory(self.state)
            return 'committed'
        services = transaction['services']
        for name in services:
            if start_services:
                self.services.stop(name)
        backup = Path(transaction['backup'])
        for name, metadata in transaction['files'].items():
            saved = backup / name
            if sha256(saved) != metadata['sha256']:
                raise RuntimeError('Rollback backup hash mismatch: ' + name)
            self.replace(saved, self.destination(name), metadata['mode'])
        atomic_json(self.version_file, transaction['previous'])
        if start_services:
            for name in services:
                self.services.start(name)
            if not self.services.healthy(services):
                raise RuntimeError('Rollback restored files but service health check failed')
        self.transaction.unlink()
        sync_directory(self.state)
        return 'rolled_back'

    def apply(self, staging, manifest):
        if self.transaction.exists():
            raise RuntimeError('Unresolved previous transaction; recovery required')
        previous = self.current()
        if manifest['from_version'] != previous['version']:
            raise ValueError('Installed version changed during package download')
        services = sorted({FILES[n][1] for n in manifest['files']})
        backup = self.state / 'backups' / (previous['version'] + '-' + str(int(time.time())))
        backup.mkdir(parents=True, exist_ok=False)
        original = {}
        for name in manifest['files']:
            destination = self.destination(name)
            if destination.is_symlink() or not destination.is_file():
                raise ValueError('Expected installed regular file: ' + name)
            shutil.copy2(str(destination), str(backup / name))
            with open(str(backup / name), 'rb') as saved:
                os.fsync(saved.fileno())
            original[name] = {'sha256': sha256(backup / name),
                              'mode': destination.stat().st_mode & 0o777}
        sync_directory(backup)
        transaction = {'phase': 'prepared', 'backup': str(backup), 'files': original,
                       'previous': previous, 'services': services, 'target': manifest['version']}
        atomic_json(self.transaction, transaction)
        try:
            for name in services:
                self.services.stop(name)
            for name in manifest['files']:
                self.replace(Path(staging) / name, self.destination(name), FILES[name][2])
            for name in services:
                self.services.start(name)
            if not self.services.healthy(services):
                raise RuntimeError('Updated application did not pass startup health checks')
            atomic_json(self.version_file, {'version': manifest['version'], 'board': BOARD,
                                           'installed_time': int(time.time())})
            transaction['phase'] = 'committed'
            atomic_json(self.transaction, transaction)
            self.transaction.unlink()
            sync_directory(self.state)
            return str(backup)
        except Exception:
            self.recover(start_services=True)
            raise
