#!/usr/bin/env python3
"""Board-local Chinese voice commands with Vosk and the onboard WM8960 MIC."""
import ctypes
import fcntl
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

from voice_core import choose_command, recognized_text

ROOT = Path('/opt/weiju-voice')
MODEL = ROOT / 'vosk-model-small-cn-0.22'
LIBRARY = ROOT / 'vosk/libvosk.so'
SOCKET = Path('/var/run/weiju-voice/control.sock')
SETTINGS = Path('/var/lib/weiju-voice/settings.json')
ONENET_SOCKET = '/var/run/weiju-onenet/control.sock'
LED_BRIGHTNESS = '/sys/class/leds/sys-led/brightness'
DURATION = 5
LOG = logging.getLogger('weiju-voice')


class OfflineRecognizer(object):
    def __init__(self, library_path=str(LIBRARY), model_path=str(MODEL)):
        self.api = ctypes.CDLL(library_path)
        self.api.vosk_set_log_level.argtypes = [ctypes.c_int]
        self.api.vosk_set_log_level(-1)
        self.api.vosk_model_new.argtypes = [ctypes.c_char_p]
        self.api.vosk_model_new.restype = ctypes.c_void_p
        self.api.vosk_model_free.argtypes = [ctypes.c_void_p]
        self.api.vosk_recognizer_new_grm.argtypes = [ctypes.c_void_p, ctypes.c_float,
                                                     ctypes.c_char_p]
        self.api.vosk_recognizer_new_grm.restype = ctypes.c_void_p
        self.api.vosk_recognizer_accept_waveform.argtypes = [ctypes.c_void_p,
                                                             ctypes.c_char_p, ctypes.c_int]
        self.api.vosk_recognizer_accept_waveform.restype = ctypes.c_int
        for name in ('vosk_recognizer_result', 'vosk_recognizer_final_result'):
            method = getattr(self.api, name)
            method.argtypes = [ctypes.c_void_p]
            method.restype = ctypes.c_char_p
        self.api.vosk_recognizer_free.argtypes = [ctypes.c_void_p]
        self.model = self.api.vosk_model_new(model_path.encode('ascii'))
        if not self.model:
            raise RuntimeError('Chinese voice model failed to load')
        self.grammar = json.dumps(['开灯', '关灯', '[unk]'],
                                  ensure_ascii=False).encode('utf-8')

    def recognize(self, chunks, canceled):
        recognizer = self.api.vosk_recognizer_new_grm(self.model, 16000.0, self.grammar)
        if not recognizer:
            raise RuntimeError('Voice recognizer could not start')
        texts = []
        try:
            for chunk in chunks:
                if canceled.is_set():
                    break
                if self.api.vosk_recognizer_accept_waveform(recognizer, chunk, len(chunk)):
                    text = recognized_text(self.api.vosk_recognizer_result(recognizer)
                                           .decode('utf-8'))
                    if text:
                        texts.append(text)
            if not canceled.is_set():
                text = recognized_text(self.api.vosk_recognizer_final_result(recognizer)
                                       .decode('utf-8'))
                if text:
                    texts.append(text)
            return texts
        finally:
            self.api.vosk_recognizer_free(recognizer)

    def close(self):
        if self.model:
            self.api.vosk_model_free(self.model)
            self.model = None


def set_microphone_input():
    commands = [
        ['amixer', 'sset', 'Right Input Mixer Boost', 'on'],
        ['amixer', 'sset', 'Right Boost Mixer RINPUT2', 'on'],
        ['amixer', 'sset', 'Right Input Boost Mixer RINPUT2', '5'],
        ['amixer', 'cset', 'name=Capture Volume', '50,50'],
    ]
    for command in commands:
        subprocess.check_call(command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=4)


def command_led(value):
    request = json.dumps({'action': 'set_led', 'value': value}).encode('utf-8') + b'\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(ONENET_SOCKET)
        connection.sendall(request)
        reply = bytearray()
        while b'\n' not in reply and len(reply) < 8192:
            block = connection.recv(1024)
            if not block:
                break
            reply.extend(block)
    response = json.loads(bytes(reply).split(b'\n', 1)[0].decode('utf-8'))
    if not response.get('ok') or response.get('status', {}).get('led_switch') is not value:
        raise RuntimeError('LED controller did not confirm the requested state')


def save_output_enabled(value):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(SETTINGS) + '.tmp')
    with temporary.open('w') as stream:
        json.dump({'speech_output_enabled': value}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(SETTINGS))


class VoiceService(object):
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.stopping = threading.Event()
        self.capture = None
        self.worker = None
        self.started = 0.0
        self.phase = 'ready'
        self.message = '离线识别已就绪'
        self.transcript = ''
        self.last_command = ''
        self.last_command_time = 0
        self.playback_lock = threading.Lock()
        self.output_enabled = False
        try:
            settings = json.loads(SETTINGS.read_text())
            self.output_enabled = settings.get('speech_output_enabled') is True
        except (OSError, ValueError):
            pass
        SOCKET.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET.exists():
            SOCKET.unlink()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(SOCKET))
        os.chmod(str(SOCKET), 0o600)
        self.server.listen(4)
        self.server.settimeout(0.5)

    def update(self, phase, message, transcript=None):
        with self.lock:
            self.phase = phase
            self.message = message
            if transcript is not None:
                self.transcript = transcript

    def status(self):
        with self.lock:
            result = {
                'phase': self.phase, 'message': self.message,
                'transcript': self.transcript,
                'last_command': self.last_command,
                'last_command_time': self.last_command_time,
                'speech_output_enabled': self.output_enabled,
                'recording_seconds_left': max(0, round(DURATION -
                    (time.monotonic() - self.started), 1)) if self.phase == 'listening' else 0,
            }
        try:
            result['led_switch'] = int(Path(LED_BRIGHTNESS).read_text().strip()) > 0
        except (OSError, ValueError):
            result['led_switch'] = None
        return result

    def audio(self, name):
        if not self.output_enabled:
            return
        with self.playback_lock:
            try:
                for command in (
                        ['amixer', 'sset', 'Speaker Playback ZC', 'on'],
                        ['amixer', 'sset', 'Speaker', '115,115'],
                        ['amixer', 'sset', 'Speaker AC', '4'],
                        ['amixer', 'sset', 'Speaker DC', '4']):
                    subprocess.check_call(command, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, timeout=4)
                source = ROOT / 'prompts' / name
                target_dir = ROOT / 'prompts' / 'stereo'
                target_dir.mkdir(exist_ok=True)
                target = target_dir / name
                if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
                    temporary = target_dir / (name + '.tmp.wav')
                    subprocess.check_call([
                        'ffmpeg', '-nostdin', '-loglevel', 'error', '-y',
                        '-i', str(source), '-filter:a', 'volume=3.5',
                        '-ac', '2', '-ar', '44100',
                        '-sample_fmt', 's16', str(temporary)
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                    os.replace(str(temporary), str(target))
                subprocess.check_call(['aplay', '-q', '-D', 'plughw:0,0', str(target)],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=8)
                LOG.info('Voice prompt played: %s', name)
            except (OSError, subprocess.SubprocessError) as error:
                LOG.warning('Audio playback unavailable: %s', error)

    def capture_chunks(self):
        set_microphone_input()
        command = ['arecord', '-q', '-D', 'plughw:0,0', '-f', 'S16_LE',
                   '-r', '16000', '-c', '1', '-t', 'raw', '-d', str(DURATION)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   bufsize=0)
        with self.lock:
            self.capture = process
            self.started = time.monotonic()
        pending = b''
        try:
            deadline = time.monotonic() + DURATION + 10
            while not self.cancel.is_set():
                if time.monotonic() > deadline:
                    raise RuntimeError('麦克风录音超时')
                ready, _, _ = select.select([process.stdout], [], [], 0.4)
                if not ready:
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                data = pending + chunk
                pending = data[-1:] if len(data) % 2 else b''
                if pending:
                    data = data[:-1]
                if data:
                    yield data
            if self.cancel.is_set():
                return
            if process.wait(timeout=2) != 0:
                detail = process.stderr.read(400).decode('utf-8', errors='replace')
                raise RuntimeError('麦克风录音失败: ' + detail.strip())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
            process.stderr.close()
            with self.lock:
                self.capture = None

    def listen_once(self):
        try:
            texts = self.recognizer.recognize(self.capture_chunks(), self.cancel)
            if self.cancel.is_set():
                self.update('ready', '已停止聆听', '')
                return
            self.update('recognizing', '正在确认指令', ' / '.join(texts))
            try:
                value = choose_command(texts)
            except ValueError:
                self.update('unrecognized', '听到不同指令，请只说开灯或关灯')
                self.audio('ack-retry.wav')
                return
            if value is None:
                self.update('unrecognized', '没有听清，请再说一次')
                self.audio('ack-retry.wav')
                return
            self.update('executing', '正在控制板载 LED')
            command_led(value)
            with self.lock:
                self.last_command = '开灯' if value else '关灯'
                self.last_command_time = int(time.time())
            self.update('done', 'LED 已点亮' if value else 'LED 已熄灭')
            self.audio('ack-on.wav' if value else 'ack-off.wav')
            LOG.info('Offline voice command applied: %s', self.last_command)
        except Exception as error:
            LOG.warning('Voice operation failed: %s', error)
            self.update('error', '语音控制未完成，请重试')

    def handle(self, request):
        if not isinstance(request, dict) or 'action' not in request:
            raise ValueError('Invalid request')
        action = request['action']
        if action == 'listen' and set(request) == {'action'}:
            with self.lock:
                if self.worker and self.worker.is_alive():
                    raise ValueError('Voice recognition is already running')
                self.cancel.clear()
                self.started = time.monotonic()
                self.phase = 'listening'
                self.message = '正在聆听，请说开灯或关灯'
                self.transcript = ''
                self.worker = threading.Thread(target=self.listen_once)
                self.worker.daemon = True
                self.worker.start()
        elif action == 'cancel' and set(request) == {'action'}:
            self.cancel.set()
            with self.lock:
                process = self.capture
            if process and process.poll() is None:
                process.terminate()
        elif action == 'set_output' and set(request) == {'action', 'enabled'}:
            if type(request['enabled']) is not bool:
                raise ValueError('Output setting must be a boolean')
            save_output_enabled(request['enabled'])
            with self.lock:
                self.output_enabled = request['enabled']
        elif action == 'test_output' and set(request) == {'action'}:
            threading.Thread(target=self.audio, args=('ack-on.wav',), daemon=True).start()
        elif action != 'status' or set(request) != {'action'}:
            raise ValueError('Unknown voice action')
        return {'ok': True, 'status': self.status()}

    def run(self):
        LOG.info('Offline Chinese voice service ready')
        while not self.stopping.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1)
                try:
                    raw = bytearray()
                    while b'\n' not in raw:
                        chunk = connection.recv(4096 - len(raw))
                        if not chunk:
                            raise ValueError('Incomplete request')
                        raw.extend(chunk)
                        if len(raw) >= 4096:
                            raise ValueError('Request too large')
                    request = json.loads(bytes(raw).split(b'\n', 1)[0].decode('utf-8'))
                    response = self.handle(request)
                except (OSError, ValueError, UnicodeError, TypeError) as error:
                    response = {'ok': False, 'error': str(error)[:120]}
                try:
                    connection.sendall((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
                except OSError:
                    pass

    def close(self):
        self.stopping.set()
        self.cancel.set()
        with self.lock:
            process = self.capture
            worker = self.worker
        if process and process.poll() is None:
            process.terminate()
        self.server.close()
        if worker:
            worker.join(timeout=15)
        if SOCKET.exists():
            SOCKET.unlink()
        if not worker or not worker.is_alive():
            self.recognizer.close()


def main():
    os.umask(0o077)
    Path('/var/log/weiju-voice').mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler('/var/log/weiju-voice/voice.log',
                                 maxBytes=262144, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    lock = open('/var/run/weiju-voice.pid', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()) + '\n')
    lock.flush()
    service = VoiceService(OfflineRecognizer())

    def stop(signum, frame):
        service.stopping.set()
        service.cancel.set()
        with service.lock:
            process = service.capture
        if process and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        service.run()
    finally:
        service.close()
        LOG.info('Voice service stopped')


if __name__ == '__main__':
    main()
