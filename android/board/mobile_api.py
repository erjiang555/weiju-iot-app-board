#!/usr/bin/env python3
"""Authenticated, pinned-TLS LAN adapter for the existing Weiju services.

Python 3.5 compatible. No cloud credential is read or exported.
"""
import fcntl
import hmac
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import select
import signal
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
import alerts_api
import voice_audio

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger('weiju-mobile')
CONFIG = {}
CLIENTS = threading.BoundedSemaphore(12)
VOICE = threading.Lock()
VOICE_STOP = threading.Event()


def local_control(request):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect('/var/run/weiju-onenet/control.sock')
        sock.sendall((json.dumps(request) + '\n').encode('utf-8'))
        data = bytearray()
        while b'\n' not in data and len(data) < 32768:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
        result = json.loads(bytes(data).split(b'\n', 1)[0].decode('utf-8'))
        if not result.get('ok'):
            raise ValueError(result.get('error', '板端控制服务不可用'))
        return result['status']
    finally:
        sock.close()


def camera_request(path, body=None):
    # Only callers in this module choose fixed paths; never proxy arbitrary URLs.
    data = None if body is None else json.dumps(body).encode('utf-8')
    return urlopen(Request('http://127.0.0.1:8080' + path, data=data,
                           headers={'Content-Type': 'application/json'}), timeout=6)


def camera_json(path, body=None):
    with camera_request(path, body) as response:
        return json.loads(response.read(32768).decode('utf-8'))


def optional_json(path):
    try:
        with open(path) as stream:
            return json.load(stream)
    except (ValueError, OSError):
        return {}


def snapshot():
    result = {'api_version': 1, 'sample_time': int(time.time()), 'control': None,
              'camera': None, 'board_model': 'I.MX6ULL'}
    try:
        result['control'] = local_control({'action': 'status'})
    except Exception:
        result['control_error'] = '设备控制服务暂不可用'
    try:
        result['camera'] = camera_json('/api/status')
    except Exception:
        result['camera_error'] = '摄像头服务暂不可用'
    version = optional_json('/var/lib/weiju-ota/version.json')
    ota = optional_json('/var/run/weiju-ota/status.json')
    result['software_version'] = version.get('version', '未知')
    result['ota'] = {k: ota[k] for k in ('phase', 'version', 'target', 'progress', 'updated_time') if k in ota}
    result['alerts'] = alerts_api.status()
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'WeijuMobile/1.0'

    def log_message(self, fmt, *args):
        pass  # Do not log tokens, request bodies or camera frames.

    def reply(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)

    def jpeg(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def authenticated(self):
        supplied = self.headers.get('Authorization', '')
        expected = 'Bearer ' + CONFIG['token']
        if not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
            self.reply(401, {'error': '配对信息无效，请重新导入设备配对文件'})
            return False
        origin = self.headers.get('Origin')
        if origin:
            self.reply(403, {'error': '仅接受配对客户端请求'})
            return False
        return True

    def do_GET(self):
        try:
            if not self.authenticated():
                return
            path = urlsplit(self.path).path
            if path == '/v1/status':
                self.reply(200, snapshot())
            elif path == '/v1/alerts':
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    after = int(query.get('after', ['0'])[0])
                    limit = int(query.get('limit', ['20'])[0])
                    if after < 0 or not 1 <= limit <= 100:
                        raise ValueError()
                except ValueError:
                    self.reply(400, {'error': '无效的事件查询参数'})
                    return
                self.reply(200, alerts_api.events(after, limit))
            elif path.startswith('/v1/alerts/') and path.endswith('/image'):
                parts = path.split('/')
                try:
                    if len(parts) != 5:
                        raise ValueError()
                    self.jpeg(alerts_api.image(parts[3]))
                except (OSError, ValueError):
                    self.reply(404, {'error': '截图不存在或已过期'})
            elif path == '/v1/camera/stream':
                self.stream()
            elif path == '/v1/voice/listen':
                self.listen_voice()
            else:
                self.reply(404, {'error': '接口不存在'})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            self.reply(503, {'error': '设备服务暂不可用，请稍后重试'})

    def do_POST(self):
        try:
            if not self.authenticated():
                return
            path = urlsplit(self.path).path
            if path == '/v1/voice/play':
                self.play_voice()
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, {'error': '请求格式必须为 JSON'})
                return
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 1024:
                self.reply(400, {'error': '无效的请求长度'})
                return
            body = json.loads(self.rfile.read(length).decode('utf-8'))
            if path == '/v1/led':
                if not isinstance(body, dict) or set(body) != {'value'} or type(body['value']) is not bool:
                    self.reply(400, {'error': 'LED 开关必须是布尔值'})
                    return
                self.reply(200, {'control': local_control({'action': 'set_led', 'value': body['value']})})
            elif path in ('/v1/alerts/capture', '/v1/alerts/calibrate', '/v1/alerts/arm'):
                action = path.rsplit('/', 1)[-1]
                if action == 'arm':
                    if not isinstance(body, dict) or set(body) != {'value'} or type(body['value']) is not bool:
                        self.reply(400, {'error': '布防状态必须是布尔值'})
                        return
                    request = {'action': action, 'value': body['value']}
                else:
                    if body != {}:
                        self.reply(400, {'error': '无效的告警请求'})
                        return
                    request = {'action': action}
                result = alerts_api.command(request)
                self.reply(200 if result.get('ok') else 409, result)
            elif path in ('/v1/camera/start', '/v1/camera/stop'):
                if body != {}:
                    self.reply(400, {'error': '无效的摄像头请求'})
                    return
                self.reply(200, camera_json('/api/' + path.rsplit('/', 1)[-1], {}))
            elif path == '/v1/voice/stop':
                if body != {}:
                    self.reply(400, {'error': '无效的语音停止请求'})
                    return
                VOICE_STOP.set()
                self.reply(200, {'ok': True})
            else:
                self.reply(404, {'error': '接口不存在'})
        except (ValueError, UnicodeError, TypeError):
            self.reply(400, {'error': '请求内容无效'})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            self.reply(503, {'error': '设备操作失败，请检查板端服务'})

    def play_voice(self):
        if self.headers.get('Content-Type') != 'application/vnd.weiju.pcm16le':
            self.reply(415, {'error': '语音格式必须为 16 kHz 单声道 PCM'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 3200 <= length <= voice_audio.MAX_PLAY_BYTES or length % 2:
            self.reply(400, {'error': '语音长度必须为 0.1 至 10 秒'})
            return
        if not VOICE.acquire(False):
            self.reply(409, {'error': '板端语音设备正在使用，请稍后重试'})
            return
        try:
            data = self.rfile.read(length)
            if len(data) != length:
                self.reply(400, {'error': '语音传输不完整'})
                return
            voice_audio.play_pcm(data)
            self.reply(200, {'ok': True, 'seconds': round(length / 32000.0, 2)})
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.reply(503, {'error': '板端喇叭播放失败，请检查音频设备'})
        finally:
            VOICE.release()

    def listen_voice(self):
        if not VOICE.acquire(False):
            self.reply(409, {'error': '板端语音设备正在使用，请稍后重试'})
            return
        VOICE_STOP.clear()
        sent = [False]

        def headers():
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.weiju.pcm16le')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            sent[0] = True

        def send(chunk):
            if VOICE_STOP.is_set():
                raise ConnectionResetError('客户端已停止收听')
            if select.select([self.connection], [], [], 0)[0]:
                raise ConnectionResetError('客户端已停止收听')
            self.wfile.write(chunk)

        try:
            voice_audio.stream_microphone(headers, send)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            if not sent[0]:
                self.reply(503, {'error': '板载麦克风无法录音，请检查设备占用'})
        finally:
            VOICE.release()

    def stream(self):
        try:
            upstream = camera_request('/stream')
        except HTTPError:
            self.reply(409, {'error': '摄像头尚未就绪，请先开始监控'})
            return
        with upstream:
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=weiju-frame')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            # Forward one complete frame at a time. Keep the existing camera viewer
            # accounting alive; do not poll snapshots which trigger its idle stop.
            while True:
                line = upstream.readline(1024)
                if not line:
                    return
                self.wfile.write(line)
                if not line.startswith(b'--weiju-frame'):
                    continue
                length = 0
                while True:
                    line = upstream.readline(1024)
                    if not line:
                        return
                    self.wfile.write(line)
                    if line.lower().startswith(b'content-length:'):
                        length = int(line.split(b':', 1)[1])
                    if line in (b'\r\n', b'\n'):
                        break
                if not 0 < length <= 2097152:
                    return
                remaining = length + 2
                while remaining:
                    data = upstream.read(min(32768, remaining))
                    if not data:
                        return
                    self.wfile.write(data)
                    remaining -= len(data)
                self.wfile.flush()


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, address):
        try:
            addr = ipaddress.ip_address(address[0])
            allowed = addr.is_loopback or any(addr in net for net in LAN_NETWORKS)
        except ValueError:
            allowed = False
        if not allowed or not CLIENTS.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super(Server, self).process_request(request, address)
        except Exception:
            CLIENTS.release()
            raise

    def process_request_thread(self, request, address):
        wrapped = None
        try:
            request.settimeout(8)
            wrapped = self.tls.wrap_socket(request, server_side=True)
            self.finish_request(wrapped, address)
        except Exception:
            pass
        finally:
            self.shutdown_request(wrapped if wrapped is not None else request)
            CLIENTS.release()


LAN_NETWORKS = [ipaddress.ip_network(v) for v in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]


def main():
    global CONFIG
    os.umask(0o077)
    with open('/etc/weiju-mobile/config.json') as stream:
        CONFIG = json.load(stream)
    if len(CONFIG.get('token', '')) < 32:
        raise ValueError('Invalid pairing key')
    os.makedirs('/var/log/weiju-mobile', exist_ok=True)
    LOG.addHandler(RotatingFileHandler('/var/log/weiju-mobile/server.log', maxBytes=131072, backupCount=1))
    LOG.setLevel(logging.INFO)
    lock = open('/var/run/weiju-mobile.pid', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    server = Server(('0.0.0.0', 8443), Handler)
    server.tls = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
    server.tls.load_cert_chain('/etc/weiju-mobile/server.pem', '/etc/weiju-mobile/server-key.pem')
    server.timeout = 0.5
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda a, b: stopping.set())
    signal.signal(signal.SIGINT, lambda a, b: stopping.set())
    LOG.info('Authenticated mobile adapter listening on LAN port 8443')
    try:
        while not stopping.is_set():
            server.handle_request()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
