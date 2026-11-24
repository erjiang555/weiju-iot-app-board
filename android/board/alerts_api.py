"""Read-only event storage and root Unix-socket commands for mobile_api.py."""
import json,re,socket,time
from pathlib import Path
ROOT=Path('/var/lib/weiju-alerts')

def load(path,default):
    try:
        with open(str(path)) as f:return json.load(f)
    except (OSError,ValueError):return default

def status():
    value=load(Path('/var/run/weiju-alerts/status.json'),{'installed':False})
    if time.time()-value.get('updated_time',0)>10:value['phase']='unavailable'
    return value

def events(after=0,limit=20):
    items=load(ROOT/'index.json',[])
    return {'events':[e for e in items if e['id']>after][:limit],
            'latest_id':items[-1]['id'] if items else 0,'status':status()}

def image(event_id):
    if not re.match(r'^[0-9]{1,18}$',event_id):raise ValueError('invalid_event_id')
    if not any(str(e.get('id'))==event_id for e in load(ROOT/'index.json',[])):raise FileNotFoundError('event_not_found')
    return (ROOT/'events'/(event_id+'.jpg')).read_bytes()

def command(request):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(3)
    try:
        s.connect('/var/run/weiju-alerts/control.sock');s.sendall((json.dumps(request)+'\n').encode('utf-8'))
        raw=bytearray()
        while b'\n' not in raw and len(raw)<2048:
            part=s.recv(2048-len(raw))
            if not part:break
            raw.extend(part)
        return json.loads(bytes(raw).split(b'\n')[0].decode('utf-8'))
    finally:s.close()
