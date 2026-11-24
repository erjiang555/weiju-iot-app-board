import json,time,subprocess
from urllib.request import Request,urlopen
base='http://127.0.0.1:8080'
def api(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    with urlopen(Request(base+path,data=data,headers={'Content-Type':'application/json'}),timeout=5) as r:return json.loads(r.read().decode('utf-8'))
started=api('/api/status')['state']!='running'
if started:api('/api/start',{})
for n in range(20):
    status=api('/api/status')
    if status['state']=='running':break
    if status['state']=='error':raise RuntimeError(status['error'])
    time.sleep(0.4)
else:raise RuntimeError('camera_not_ready')
path='/var/lib/weiju-alerts/work/benchmark.jpg'
with urlopen(base+'/snapshot.jpg',timeout=5) as r:raw=r.read(1048576)
assert raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9')
with open(path,'wb') as f:f.write(raw)
before=time.monotonic()
proc=subprocess.Popen(['/opt/weiju-alerts/person_detector','/opt/weiju-alerts/models/mobilenet_ssd_voc_ncnn.param','/opt/weiju-alerts/models/mobilenet_ssd_voc_ncnn.bin'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
out,err=proc.communicate((path+'\n').encode(),timeout=60)
print(out.decode().strip())
print('TOTAL_SECONDS',round(time.monotonic()-before,3),'JPEG_BYTES',len(raw),'EXIT',proc.returncode)
if proc.returncode:print(err.decode(errors='replace'))
