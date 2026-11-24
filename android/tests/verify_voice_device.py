"""Run short authenticated voice checks against the powered I.MX6ULL."""
import audioop
import hashlib
import http.client
import json
import math
from pathlib import Path
import ssl
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
private = ROOT / 'private'
assert len(sys.argv) == 1 or sys.argv[1:] == ['--vpn'], 'Use --vpn for the Tailscale address'
pair_name = 'device-pairing-vpn.weiju' if len(sys.argv) > 1 else 'device-pairing.weiju'
pair = json.loads((private / pair_name).read_text(encoding='utf-8'))
context = ssl.create_default_context(cafile=str(private / 'server.pem'))
context.check_hostname = False


def connect():
    connection = http.client.HTTPSConnection(pair['host'], 8443, context=context, timeout=12)
    connection.connect()
    actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
    assert actual == pair['cert_sha256'], 'Device certificate mismatch'
    return connection


def request(method, path, body=None, kind=None, auth=True):
    connection = connect()
    headers = {'Connection': 'close'}
    if auth:
        headers['Authorization'] = 'Bearer ' + pair['token']
    if kind:
        headers['Content-Type'] = kind
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    return connection, response


connection, response = request('GET', '/v1/voice/listen', auth=False)
assert response.status == 401
response.read()
connection.close()
print('PASS unauthenticated microphone access denied')

connection, response = request('POST', '/v1/voice/play', b'\0' * 3200, 'application/json')
assert response.status == 415
response.read()
connection.close()
print('PASS wrong voice format denied')

tone = b''.join(struct.pack('<h', int(5500 * math.sin(2 * math.pi * 440 * i / 16000)))
                for i in range(16000))
connection, response = request('POST', '/v1/voice/play', tone,
                               'application/vnd.weiju.pcm16le')
payload = json.loads(response.read().decode('utf-8'))
assert response.status == 200 and payload.get('ok') is True
connection.close()
print('PASS one-second voice played on board')

connection, response = request('GET', '/v1/voice/listen')
assert response.status == 200
assert response.getheader('Content-Type') == 'application/vnd.weiju.pcm16le'
capture = response.read(32000)
stop_connection, stop_response = request('POST', '/v1/voice/stop', b'{}', 'application/json')
assert stop_response.status == 200
stop_response.read()
stop_connection.close()
connection.close()
assert len(capture) >= 3200
average = audioop.avg(capture, 2)
ac_rms = audioop.rms(audioop.bias(capture, 2, -average), 2)
print('PASS microphone stream protocol: {} bytes; RMS {}, AC RMS {}, average {}, peak {}'.format(
    len(capture), audioop.rms(capture, 2), ac_rms, average, audioop.max(capture, 2)))

time.sleep(0.5)
connection, response = request('GET', '/v1/voice/listen')
assert response.status == 200
assert len(response.read(3200)) == 3200
stop_connection, stop_response = request('POST', '/v1/voice/stop', b'{}', 'application/json')
assert stop_response.status == 200
stop_response.read()
stop_connection.close()
connection.close()
print('PASS microphone can be reopened after disconnect')

connection, response = request('GET', '/v1/status')
assert response.status == 200 and json.loads(response.read().decode('utf-8'))['control']
connection.close()
print('PASS existing status interface still works')
