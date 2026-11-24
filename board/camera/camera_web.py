#!/usr/bin/env python3
"""Small LAN MJPEG monitor for the i.MX6ULL and USB UVC RGB cameras."""
from __future__ import print_function
import collections
import fcntl
import glob
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger('weiju-camera-web')


class Camera(object):
    def __init__(self):
        self.condition = threading.Condition()
        self.command_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = None
        self.process = None
        self.frame = None
        self.sequence = 0
        self.viewers = 0
        self.state = 'stopped'
        self.error = ''
        self.device = ''
        self.started = 0
        self.last_activity = time.monotonic()
        self.timestamps = collections.deque(maxlen=30)

    def status(self):
        with self.condition:
            fps = 0
            if len(self.timestamps) > 1:
                duration = self.timestamps[-1] - self.timestamps[0]
                if duration > 0:
                    fps = round((len(self.timestamps) - 1) / duration, 1)
            result = {'state': self.state, 'error': self.error,
                      'device': self.device, 'camera_name': 'USB 彩色摄像头',
                      'width': 640, 'height': 480, 'target_fps': 6, 'fps': fps,
                      'frames': self.sequence, 'viewers': self.viewers,
                      'uptime': int(time.monotonic() - self.started) if self.started else 0}
        try:
            value = float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
            result['cpu_temperature'] = round(value * 2) / 2
        except (OSError, ValueError):
            result['cpu_temperature'] = None
        return result

    @staticmethod
    def find_device():
        for name in sorted(glob.glob('/sys/class/video4linux/video*')):
            driver = os.path.realpath(os.path.join(name, 'device/driver'))
            if os.path.basename(driver) != 'uvcvideo':
                continue
            device = '/dev/' + os.path.basename(name)
            try:
                formats = subprocess.check_output(
                    ['v4l2-ctl', '-d', device, '--list-formats-ext'],
                    stderr=subprocess.STDOUT, timeout=3).decode('utf-8', errors='replace')
                if "'YUYV'" in formats and '640x480' in formats:
                    return device
            except (OSError, subprocess.SubprocessError):
                continue
        raise RuntimeError('未找到 USB 彩色摄像头，请检查 USB HOST 连接')

    def start(self):
        with self.command_lock:
            with self.condition:
                if self.worker and self.worker.is_alive():
                    return
                self.frame = None
                self.sequence = 0
                self.timestamps.clear()
                self.state = 'starting'
                self.error = ''
                self.last_activity = time.monotonic()
                self.stop_event.clear()
                self.worker = threading.Thread(target=self.capture)
                self.worker.daemon = True
                self.worker.start()

    def stop(self):
        with self.command_lock:
            self.stop_event.set()
            with self.condition:
                process, worker = self.process, self.worker
                self.condition.notify_all()
            if process and process.poll() is None:
                process.terminate()
            if worker:
                worker.join(timeout=5)
            with self.condition:
                self.state = 'stopped'
                self.error = ''
                self.frame = None
                self.condition.notify_all()

    def capture(self):
        process = None
        diagnostic = None
        try:
            device = self.find_device()
            diagnostic = open('/var/log/weiju-camera-web/capture.log', 'w+')
            command = ['gst-launch-1.0', '-q', 'v4l2src', 'device=' + device,
                       'io-mode=2', '!',
                       'video/x-raw,format=YUY2,width=640,height=480,framerate=6/1',
                       '!', 'videoconvert', '!', 'jpegenc', 'quality=70',
                       '!', 'fdsink', 'fd=1', 'sync=false']
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=diagnostic, bufsize=0)
            with self.condition:
                self.process = process
                self.device = device
                self.started = time.monotonic()
            LOG.info('Starting RGB stream on %s', device)
            buffer = bytearray()
            last_frame = time.monotonic()
            while not self.stop_event.is_set():
                if time.monotonic() - last_frame > 8:
                    raise RuntimeError('摄像头没有输出画面，请检查连接或重试')
                with self.condition:
                    if self.viewers == 0 and time.monotonic() - self.last_activity > 20:
                        LOG.info('Releasing camera after viewer idle timeout')
                        break
                readable, _, _ = select.select([process.stdout], [], [], 0.5)
                if not readable:
                    if process.poll() is not None:
                        raise RuntimeError('摄像头启动失败，请先停止板子上的摄像头预览，再重试')
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('摄像头已断开或被其他程序占用，请检查后重试')
                buffer.extend(chunk)
                if len(buffer) > 2097152:
                    raise RuntimeError('摄像头画面数据异常，请重新开始监控')
                while True:
                    begin = buffer.find(b'\xff\xd8')
                    if begin < 0:
                        if len(buffer) > 1:
                            del buffer[:-1]
                        break
                    end = buffer.find(b'\xff\xd9', begin + 2)
                    if end < 0:
                        if begin:
                            del buffer[:begin]
                        break
                    frame = bytes(buffer[begin:end + 2])
                    del buffer[:end + 2]
                    last_frame = time.monotonic()
                    with self.condition:
                        self.frame = frame
                        self.sequence += 1
                        self.timestamps.append(last_frame)
                        self.state = 'running'
                        self.condition.notify_all()
        except Exception as error:
            if not self.stop_event.is_set():
                with self.condition:
                    self.state = 'error'
                    self.error = str(error)
                    self.condition.notify_all()
                LOG.warning('Capture failed: %s', error)
        finally:
            if process:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
            if diagnostic:
                diagnostic.close()
            with self.condition:
                self.process = None
                if self.state != 'error':
                    self.state = 'stopped'
                self.condition.notify_all()


CAMERA = Camera()


class Handler(BaseHTTPRequestHandler):
    server_version = 'WeijuCamera/1.0'
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        # Status polling need not create writes to eMMC every two seconds.
        if args and '/api/status' not in str(args[0]):
            LOG.info('%s %s', self.client_address[0], format % args)

    def reply(self, status, data, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/':
                self.reply(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/robot.png':
                self.reply(200, (ROOT / 'robot.png').read_bytes(), 'image/png')
            elif path == '/api/status':
                self.reply(200, CAMERA.status())
            elif path == '/snapshot.jpg':
                with CAMERA.condition:
                    frame = CAMERA.frame if CAMERA.state == 'running' else None
                self.reply(200, frame, 'image/jpeg') if frame else self.reply(503, {'error': '暂无实时画面'})
            elif path == '/stream':
                self.stream()
            else:
                self.reply(404, {'error': 'Not found'})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def do_POST(self):
        # JSON and same-origin requests prevent another website starting capture.
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            self.reply(403, {'error': '请求来源不匹配'})
            return
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.reply(415, {'error': 'Expected JSON'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 1024:
                raise ValueError('Body too large')
            json.loads(self.rfile.read(length).decode('utf-8'))
            path = urlsplit(self.path).path
            if path == '/api/start':
                CAMERA.start()
            elif path == '/api/stop':
                CAMERA.stop()
            else:
                self.reply(404, {'error': 'Not found'})
                return
            self.reply(200, CAMERA.status())
        except (ValueError, UnicodeError):
            self.reply(400, {'error': 'Invalid request'})

    def stream(self):
        with CAMERA.condition:
            if CAMERA.state != 'running' or CAMERA.viewers >= 4:
                self.reply(409, {'error': '请先开始监控，最多同时连接 4 个画面'})
                return
            CAMERA.viewers += 1
            CAMERA.last_activity = time.monotonic()
        try:
            self.connection.settimeout(4)
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=weiju-frame')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Connection', 'close')
            self.end_headers()
            sequence = -1
            while True:
                with CAMERA.condition:
                    CAMERA.condition.wait_for(lambda: CAMERA.sequence != sequence or
                                              CAMERA.state != 'running', timeout=3)
                    if CAMERA.state != 'running':
                        break
                    if CAMERA.sequence == sequence:
                        continue
                    sequence, frame = CAMERA.sequence, CAMERA.frame
                if frame:
                    self.wfile.write(b'--weiju-frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                                     str(len(frame)).encode('ascii') + b'\r\n\r\n' + frame + b'\r\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            with CAMERA.condition:
                CAMERA.viewers -= 1
                CAMERA.last_activity = time.monotonic()


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 12


def main():
    os.umask(0o077)
    os.makedirs('/var/log/weiju-camera-web', exist_ok=True)
    handler = RotatingFileHandler('/var/log/weiju-camera-web/server.log',
                                 maxBytes=262144, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    lock = open('/var/run/weiju-camera-web.pid', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()) + '\n')
    lock.flush()
    server = Server(('0.0.0.0', 8080), Handler)
    server.timeout = 0.5
    stopping = threading.Event()
    def stop(signum, frame):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info('LAN camera monitor listening on port 8080; capture initially stopped')
    try:
        while not stopping.is_set():
            server.handle_request()
    finally:
        CAMERA.stop()
        server.server_close()


if __name__ == '__main__':
    main()
