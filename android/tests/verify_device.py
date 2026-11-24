"""Integration checks against the real board; restores initial LED/camera state."""
from pathlib import Path
import hashlib
import http.client
import json
import ssl
import time

ROOT=Path(__file__).resolve().parents[1]
pair=json.loads((ROOT/'private/device-pairing.weiju').read_text())
ctx=ssl.create_default_context(cafile=str(ROOT/'private/server.pem'))
ctx.check_hostname=False  # The exact paired leaf certificate is checked below.

def request(path, body=None, auth=True, origin=None):
    c=http.client.HTTPSConnection(pair['host'],8443,context=ctx,timeout=8)
    c.connect()
    assert hashlib.sha256(c.sock.getpeercert(binary_form=True)).hexdigest()==pair['cert_sha256']
    headers={'Content-Type':'application/json'}
    if auth:headers['Authorization']='Bearer '+pair['token']
    if origin:headers['Origin']=origin
    c.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers=headers)
    r=c.getresponse(); raw=r.read();status=r.status;c.close()
    return status,json.loads(raw)

checks=[]
def passed(label): checks.append(label);print('PASS',label,flush=True)

assert request('/v1/status',auth=False)[0]==401;passed('unauthenticated access rejected')
assert request('/v1/status',origin='https://untrusted.example')[0]==403;passed('browser-origin access rejected')
assert request('/v1/led',{'value':'true'})[0]==400;passed('invalid LED type rejected')
assert request('/v1/led',{'value':True,'path':'/etc/shadow'})[0]==400;passed('unexpected control fields rejected')
assert request('/v1/not-found')[0]==404;passed('unknown endpoint rejected')
code,status=request('/v1/status');assert code==200 and status['control'] is not None
assert -40<=status['control']['cpu_temperature']<=125
passed('real CPU temperature and version read')
original=status['control']['led_switch']
try:
    for value in (not original,original):
        code,result=request('/v1/led',{'value':value});assert code==200 and result['control']['led_switch'] is value
        assert request('/v1/status')[1]['control']['led_switch'] is value
    passed('LED toggle confirmed by hardware readback; original state restored')
finally:
    request('/v1/led',{'value':original})

(ROOT/'dist/device-verification.json').write_text(json.dumps({'passed':checks,
    'software_version':status['software_version'],'cpu_temperature':status['control']['cpu_temperature'],
    'cloud_phase':status['control']['phase']},ensure_ascii=False,indent=2),encoding='utf-8')
