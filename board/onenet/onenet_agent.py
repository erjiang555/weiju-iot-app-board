#!/usr/bin/env python3
"""OneNET CPU temperature and LED bridge. Compatible with Python 3.5."""
from __future__ import print_function
import base64
import fcntl
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import threading
from urllib.parse import quote

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor'))
import paho.mqtt.client as mqtt

LOG = logging.getLogger('weiju-onenet')
STOP = False


def token(config, expires):
    resource = 'products/{}/devices/{}'.format(config['product_id'], config['device_name'])
    values = [str(expires), 'sha256', resource, '2018-10-31']
    signature = base64.b64encode(hmac.new(
        base64.b64decode(config['device_key']), '\n'.join(values).encode('utf-8'),
        hashlib.sha256).digest()).decode('ascii')
    return '&'.join('{}={}'.format(k, quote(v, safe='')) for k, v in [
        ('version', values[3]), ('res', resource), ('et', values[0]),
        ('method', values[1]), ('sign', signature)])


class PinnedTLSContext(ssl.SSLContext):
    """Validate the vendor's self-signed server certificate before MQTT login.

    OneNET's published certificate has CN=OneNET MQTTS, not a DNS hostname.
    Require its CA validation AND an exact SHA-256 certificate match instead
    of accepting arbitrary certificates or relying on an invalid hostname.
    """
    def wrap_socket(self, *args, **kwargs):
        kwargs['do_handshake_on_connect'] = True
        connection = super(PinnedTLSContext, self).wrap_socket(*args, **kwargs)
        actual = hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, self.expected_sha256):
            connection.close()
            raise ssl.SSLError('OneNET server certificate fingerprint mismatch')
        return connection


def tls_context(ca_file):
    context = PinnedTLSContext(ssl.PROTOCOL_TLSv1_2)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = False
    context.load_verify_locations(cafile=ca_file)
    with open(ca_file) as stream:
        certificate = ssl.PEM_cert_to_DER_cert(stream.read())
    context.expected_sha256 = hashlib.sha256(certificate).hexdigest()
    return context


def read_text(path):
    with open(path) as stream:
        return stream.read().strip()


def write_text(path, value):
    with open(path, 'w') as stream:
        stream.write(str(value) + '\n')


class Hardware(object):
    def __init__(self, config, state_file):
        self.led = config['led_path']
        self.temperature = config['temperature_path']
        self.state_file = state_file
        self.last_saved = None
        self.lock = threading.RLock()

    def initialize(self):
        desired = False
        try:
            desired = json.loads(read_text(self.state_file))['led_switch']
            if type(desired) is not bool:
                raise ValueError('Stored LED state must be bool')
        except (OSError, ValueError, KeyError):
            pass
        self.set_led(desired)

    def values(self):
        with self.lock:
            return self._values()

    def _values(self):
        temperature = float(read_text(self.temperature)) / 1000.0
        if not math.isfinite(temperature) or not -40 <= temperature <= 125:
            raise ValueError('CPU temperature outside the configured range')
        # Half-degree values are exact in binary and pass OneNET float-step checks.
        temperature = round(temperature * 2.0) / 2.0
        return {'cpu_temperature': temperature,
                'led_switch': int(read_text(os.path.join(self.led, 'brightness'))) > 0}

    def set_led(self, value):
        with self.lock:
            return self._set_led(value)

    def _set_led(self, value):
        if type(value) is not bool:
            raise ValueError('led_switch must be a JSON boolean')
        maximum = int(read_text(os.path.join(self.led, 'max_brightness')))
        write_text(os.path.join(self.led, 'trigger'), 'none')
        write_text(os.path.join(self.led, 'brightness'), maximum if value else 0)
        actual = int(read_text(os.path.join(self.led, 'brightness'))) > 0
        if actual != value:
            raise IOError('LED readback differs from requested state')
        if self.last_saved != value:
            temporary = self.state_file + '.tmp'
            with open(temporary, 'w') as stream:
                json.dump({'led_switch': actual}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_file)
            self.last_saved = value
        return actual


class LocalControl(threading.Thread):
    """Root-only local UI API. No device key or token is returned."""
    def __init__(self, config, hardware, path='/var/run/weiju-onenet/control.sock'):
        super(LocalControl, self).__init__()
        self.daemon = True
        self.hardware = hardware
        self.path = path
        self.changed = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.state = {'phase': 'starting', 'last_report_time': 0,
                      'last_control_time': 0, 'last_control_source': '',
                      'product_id': config['product_id'],
                      'device_name': config['device_name'],
                      'interval_seconds': config['interval_seconds']}
        directory = os.path.dirname(path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.path.exists(path):
            os.unlink(path)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        os.chmod(path, 0o600)
        self.server.listen(4)
        self.server.settimeout(0.5)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def mark_control(self, source):
        self.update(last_control_source=source, last_control_time=int(time.time()))

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
        result.update(self.hardware.values())
        result['sample_time'] = int(time.time())
        return result

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError('Expected a JSON object')
        action = request.get('action')
        if action == 'set_led':
            if set(request) != {'action', 'value'}:
                raise ValueError('Invalid LED request')
            self.hardware.set_led(request['value'])
            self.mark_control('touchscreen')
            self.changed.set()
            LOG.info('Local touchscreen LED applied actual=%s', request['value'])
        elif action != 'status' or set(request) != {'action'}:
            raise ValueError('Unknown local action')
        return {'ok': True, 'status': self.snapshot()}

    def run(self):
        while not self.stopping.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1.0)
                try:
                    raw = bytearray()
                    while b'\n' not in raw:
                        chunk = connection.recv(4096 - len(raw))
                        if not chunk:
                            raise ValueError('Incomplete request')
                        raw.extend(chunk)
                        if len(raw) >= 4096:
                            raise ValueError('Request too large')
                    response = self.handle(json.loads(bytes(raw).split(b'\n', 1)[0].decode('utf-8')))
                except (ValueError, OSError, UnicodeError, TypeError) as error:
                    response = {'ok': False, 'error': str(error)[:120]}
                try:
                    connection.sendall((json.dumps(response) + '\n').encode('utf-8'))
                except OSError:
                    pass

    def close(self):
        self.stopping.set()
        self.server.close()
        self.join(timeout=2)
        if os.path.exists(self.path):
            os.unlink(self.path)


class Bridge(object):
    def __init__(self, config, hardware, control=None):
        self.config, self.hardware = config, hardware
        self.control = control
        self.prefix = '$sys/{}/{}/thing/property/'.format(config['product_id'], config['device_name'])
        self.ready = False
        self.connected = False
        self.next_report = 0
        self.pending_reports = {}
        self.last_id = 0
        self.client = mqtt.Client(client_id=config['device_name'], clean_session=True,
                                  protocol=mqtt.MQTTv311)
        self.client.max_queued_messages_set(20)
        self.client.tls_set_context(tls_context(config['ca_file']))
        self.client.username_pw_set(config['product_id'], token(config, int(time.time()) + 86400))
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def send(self, suffix, message):
        info = self.client.publish(self.prefix + suffix,
                                   json.dumps(message, separators=(',', ':')), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise IOError('MQTT publish failed: {}'.format(info.rc))

    def on_connect(self, client, userdata, flags, result):
        if result != 0:
            LOG.error('MQTT authentication rejected: %s', result)
            return
        self.connected = True
        if self.control:
            self.control.update(phase='subscribing')
        LOG.info('MQTT connected with validated pinned TLS certificate')
        result, self.subscription_id = client.subscribe([
            (self.prefix + 'set', 1), (self.prefix + 'get', 1),
            (self.prefix + 'post/reply', 1)])
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise IOError('MQTT subscription request failed')

    def on_subscribe(self, client, userdata, message_id, granted_qos):
        if message_id == self.subscription_id:
            if len(granted_qos) != 3 or any(q >= 128 for q in granted_qos):
                raise IOError('OneNET rejected required subscriptions')
            self.ready = True
            LOG.info('Control and telemetry subscriptions ready')

    def on_disconnect(self, client, userdata, result):
        self.ready = False
        self.connected = False
        if self.control:
            self.control.update(phase='connecting')
        LOG.info('MQTT disconnected: %s', result)

    def report(self):
        values = self.hardware.values()
        self.last_id = max(self.last_id + 1, int(time.time() * 1000))
        message_id = str(self.last_id)
        self.send('post', {'id': message_id, 'version': '1.0',
                          'params': {key: {'value': value} for key, value in values.items()}})
        self.pending_reports[message_id] = time.monotonic()
        self.next_report = time.monotonic() + self.config['interval_seconds']
        LOG.info('Reported CPU=%.1f C LED=%s id=%s', values['cpu_temperature'],
                 values['led_switch'], message_id)

    def on_message(self, client, userdata, message):
        message_id = None
        suffix = message.topic[len(self.prefix):] if message.topic.startswith(self.prefix) else ''
        if suffix not in ('set', 'get', 'post/reply'):
            return
        try:
            if len(message.payload) > 65536:
                raise ValueError('Message too large')
            data = json.loads(message.payload.decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            message_id = data.get('id')
            if not isinstance(message_id, str) or not message_id.isdigit() or len(message_id) > 13:
                message_id = None
                raise ValueError('Invalid message id')
            if suffix == 'post/reply':
                known_report = self.pending_reports.pop(message_id, None) is not None
                LOG.info('OneNET report acknowledgement id=%s code=%s', message_id, data.get('code'))
                if data.get('code') != 200:
                    if self.control:
                        self.control.update(phase='report_error')
                    LOG.error('OneNET rejected the reported properties: %s', str(data.get('msg', ''))[:160])
                elif known_report and self.control:
                    self.control.update(phase='online', last_report_time=int(time.time()))
                return
            if suffix == 'set':
                params = data.get('params')
                if not isinstance(params, dict) or set(params) != {'led_switch'}:
                    raise ValueError('Only led_switch can be changed')
                actual = self.hardware.set_led(params['led_switch'])
                if getattr(self, 'control', None):
                    self.control.mark_control('cloud')
                self.send('set_reply', {'id': message_id, 'code': 200, 'msg': 'success'})
                LOG.info('LED command applied id=%s actual=%s', message_id, actual)
                self.report()
            else:
                params = data.get('params')
                if not isinstance(params, list) or not params or any(
                        p not in ('cpu_temperature', 'led_switch') for p in params):
                    raise ValueError('Unknown property requested')
                values = self.hardware.values()
                self.send('get_reply', {'id': message_id, 'code': 200, 'msg': 'success',
                                        'data': {p: values[p] for p in params}})
        except (ValueError, TypeError, UnicodeError, OSError) as error:
            LOG.warning('Rejected %s request: %s', suffix, error)
            if message_id and suffix in ('set', 'get'):
                self.send(suffix + '_reply', {'id': message_id, 'code': 400,
                                             'msg': str(error)[:160]})

    def run(self):
        started = time.monotonic()
        if self.control:
            self.control.update(phase='connecting')
        self.client.connect(self.config['host'], self.config['port'], keepalive=30)
        while not STOP:
            result = self.client.loop(timeout=1.0)
            if result != mqtt.MQTT_ERR_SUCCESS:
                raise IOError('MQTT connection lost: {}'.format(result))
            now = time.monotonic()
            if not self.ready and now - started > 15:
                raise IOError('MQTT setup timeout')
            local_change = self.control and self.control.changed.is_set()
            if self.ready and (now >= self.next_report or local_change):
                if self.control:
                    self.control.changed.clear()
                self.report()
            if any(now - sent > 40 for sent in self.pending_reports.values()):
                raise IOError('No OneNET property acknowledgement for 40 seconds')
            if now - started > 43200:
                LOG.info('Refreshing device token')
                break


def pause(seconds):
    end = time.monotonic() + seconds
    while not STOP and time.monotonic() < end:
        time.sleep(min(1, max(0, end - time.monotonic())))


def synchronize_clock():
    for server in ('ntp.aliyun.com', 'ntp.tencent.com'):
        try:
            with open(os.devnull, 'w') as sink:
                subprocess.check_call(['/usr/sbin/ntpdate', '-u', server],
                                      stdout=sink, stderr=sink, timeout=18)
            LOG.info('Clock synchronized via %s', server)
            return True
        except (OSError, subprocess.SubprocessError):
            if STOP:
                return False
    # A working RTC can supply time when an NTP service is unavailable.
    return time.time() >= 1767225600


def main():
    global STOP
    os.umask(0o077)
    for directory in ('/var/log/weiju-onenet', '/var/lib/weiju-onenet'):
        os.makedirs(directory, exist_ok=True)
    handler = RotatingFileHandler('/var/log/weiju-onenet/agent.log',
                                 maxBytes=262144, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    lock = open('/var/run/weiju-onenet.pid', 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('weiju-onenet is already running')
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
    hardware = Hardware(config, '/var/lib/weiju-onenet/led.json')
    hardware.initialize()
    control = LocalControl(config, hardware)
    control.start()
    control.update(phase='syncing_clock')
    LOG.info('Service started; interval=%ss', config['interval_seconds'])
    while not STOP and not synchronize_clock():
        LOG.warning('Waiting for network time')
        pause(30)
    backoff = 2
    while not STOP:
        bridge = None
        try:
            bridge = Bridge(config, hardware, control)
            bridge.run()
            backoff = 2
        except Exception as error:
            control.update(phase='connecting')
            LOG.warning('Connection retry needed: %s', error)
        finally:
            if bridge:
                bridge.client.disconnect()
                bridge.client.loop(timeout=0.2)
        pause(backoff)
        backoff = min(backoff * 2, 30)
    LOG.info('Service stopped')
    control.update(phase='stopped')
    control.close()


if __name__ == '__main__':
    main()
