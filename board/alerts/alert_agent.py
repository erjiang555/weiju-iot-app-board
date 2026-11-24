#!/usr/bin/env python3
"""CSI -> camera burst -> local person detector -> authenticated App event feed.
Compatible with the board's Python 3.5. No images leave the board unless the
paired App requests a confirmed event. Negative working frames are removed.
"""
import fcntl,glob,json,logging,os,queue,select,signal,socket,subprocess,termios,threading,time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path('/var/lib/weiju-alerts')
RUN=Path('/var/run/weiju-alerts')
LOG=logging.getLogger('weiju-alerts')
STOP=threading.Event()
JOBS=queue.Queue(maxsize=1)
LOCK=threading.RLock()
SERIAL_LOCK=threading.Lock()
SERIAL_FD=None
STATE={'installed':True,'armed':True,'phase':'starting','sensor':{},'last_result':'','last_capture_time':0}

def atomic_json(path,value):
    temp=str(path)+'.tmp'
    with open(temp,'w') as f:
        json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,str(path))

def read_json(path,default):
    try:
        with open(str(path)) as f:return json.load(f)
    except (OSError,ValueError):return default

def update(**fields):
    with LOCK:
        STATE.update(fields);STATE['updated_time']=int(time.time())
        atomic_json(RUN/'status.json',STATE)

def camera(path,body=None):
    data=None if body is None else json.dumps(body).encode('utf-8')
    with urlopen(Request('http://127.0.0.1:8080'+path,data=data,headers={'Content-Type':'application/json'}),timeout=6) as r:
        raw=r.read(1048577)
    if len(raw)>1048576:raise ValueError('camera_response_too_large')
    return raw if path=='/snapshot.jpg' else json.loads(raw.decode('utf-8'))

def find_sensor():
    for name in sorted(glob.glob('/sys/class/tty/ttyACM*')):
        node=Path(os.path.realpath(name))
        for parent in [node]+list(node.parents):
            try:
                if (parent/'idVendor').read_text().strip()=='303a' and (parent/'idProduct').read_text().strip()=='1001':
                    return '/dev/'+Path(name).name
            except OSError:pass
    return None

def send_sensor(data):
    raw=(json.dumps(data)+'\n').encode('utf-8')
    with SERIAL_LOCK:
        if SERIAL_FD is None:raise RuntimeError('sensor_not_connected')
        sent=0
        while sent<len(raw):
            _,ready,_=select.select([],[SERIAL_FD],[],2)
            if not ready:raise RuntimeError('sensor_write_timeout')
            sent+=os.write(SERIAL_FD,raw[sent:])

def serial_reader():
    global SERIAL_FD
    while not STOP.is_set():
        fd=None
        try:
            port=find_sensor()
            if not port:raise RuntimeError('sensor_not_connected')
            fd=os.open(port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
            attrs=termios.tcgetattr(fd);attrs[0]=termios.IGNPAR;attrs[1]=0;attrs[2]=termios.CS8|termios.CREAD|termios.CLOCAL;attrs[3]=0;attrs[4]=attrs[5]=termios.B115200;attrs[6][termios.VMIN]=0;attrs[6][termios.VTIME]=1
            termios.tcsetattr(fd,termios.TCSANOW,attrs)
            with SERIAL_LOCK:SERIAL_FD=fd
            update(sensor_port=port,sensor_error='')
            buffer=bytearray();last=time.monotonic();last_trigger=0
            while not STOP.is_set():
                ready,_,_=select.select([fd],[],[],1)
                if not ready:
                    if time.monotonic()-last>8:raise RuntimeError('sensor_no_data')
                    continue
                block=os.read(fd,4096)
                if not block:raise RuntimeError('sensor_disconnected')
                buffer.extend(block)
                if len(buffer)>16384:buffer.clear()
                while b'\n' in buffer:
                    line,_,remaining=bytes(buffer).partition(b'\n');buffer=bytearray(remaining)
                    try:message=json.loads(line.decode('utf-8'))
                    except (ValueError,UnicodeError):continue
                    if not isinstance(message,dict):continue
                    if message.get('type')=='status' and message.get('sensor')=='wifi_csi_activity':
                        last=time.monotonic();message['received_time']=int(time.time());update(sensor=message,sensor_error='')
                        with LOCK:allowed=STATE['armed'] and STATE['phase'] not in ('capturing','recognizing')
                        if allowed and message.get('state')=='ready' and message.get('motion') is True and time.monotonic()-last_trigger>=15:
                            try:JOBS.put_nowait({'trigger':'wifi_csi','score':message.get('score')});last_trigger=time.monotonic()
                            except queue.Full:pass
                    elif message.get('type')=='event' and message.get('event')=='motion_start':
                        with LOCK:allowed=STATE['armed'] and STATE.get('sensor',{}).get('state')=='ready'
                        if allowed:
                            try:JOBS.put_nowait({'trigger':'wifi_csi','score':message.get('score')});last_trigger=time.monotonic()
                            except queue.Full:pass
        except Exception as exc:
            update(sensor_error=str(exc)[:120],sensor={})
            if not STOP.is_set():STOP.wait(2)
        finally:
            with SERIAL_LOCK:SERIAL_FD=None
            if fd is not None:
                try:os.close(fd)
                except OSError:pass

def infer(path):
    args=['/opt/weiju-alerts/person_detector','/opt/weiju-alerts/models/mobilenet_ssd_voc_ncnn.param','/opt/weiju-alerts/models/mobilenet_ssd_voc_ncnn.bin']
    proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:out,err=proc.communicate((str(path)+'\n').encode('utf-8'),timeout=25)
    except subprocess.TimeoutExpired:
        proc.kill();proc.communicate();raise RuntimeError('person_inference_timeout')
    if proc.returncode:raise RuntimeError('person_inference_failed')
    result=json.loads(out.decode('utf-8'))
    if result.get('ok') is not True:raise RuntimeError(result.get('error','person_inference_failed'))
    return result

def confirmed_event(job,path,result):
    events=read_json(ROOT/'index.json',[])
    event_id=max(int(time.time()*1000),int(events[-1]['id'])+1 if events else 1)
    destination=ROOT/'events'/(str(event_id)+'.jpg')
    os.replace(str(path),str(destination))
    event={'id':event_id,'created_at':int(time.time()),'trigger':job['trigger'],
           'captured_at':job.get('captured_at',int(time.time())),
           'confirmed_at':int(time.time()),
           'pipeline_ms':int((time.monotonic()-job.get('started_mono',time.monotonic()))*1000),
           'type':'person_detected','person_count':result['count'],'confidence':result['confidence'],
           'bbox':result['bbox'],'inference_ms':result['inference_ms'],'csi_score':job.get('score')}
    events.append(event);expired=events[:-100];events=events[-100:]
    atomic_json(ROOT/'index.json',events)
    for old in expired:
        try:(ROOT/'events'/(str(int(old['id']))+'.jpg')).unlink()
        except OSError:pass
    update(last_event_id=event_id,last_result='person_detected',last_person_count=result['count'],last_confidence=result['confidence'])
    LOG.info('Person event id=%s count=%s confidence=%.3f trigger=%s',event_id,result['count'],result['confidence'],job['trigger'])

def worker():
    last_job=0
    while not STOP.is_set():
        try:job=JOBS.get(timeout=1)
        except queue.Empty:continue
        if job['trigger']=='wifi_csi' and time.monotonic()-last_job<15:continue
        with LOCK:armed=STATE['armed']
        if job['trigger']=='wifi_csi' and not armed:continue
        last_job=time.monotonic();job['started_mono']=last_job;frames=[]
        try:
            update(phase='capturing',last_result='',last_capture_time=int(time.time()))
            s=camera('/api/status')
            if s['state']!='running':camera('/api/start',{})
            for n in range(25):
                s=camera('/api/status')
                if s['state']=='running':break
                if s['state']=='error':raise RuntimeError(s['error'])
                if STOP.wait(0.3):return
            else:raise RuntimeError('camera_start_timeout')
            # Capture the entire burst before CPU-intensive inference begins.
            for i in range(3):
                raw=camera('/snapshot.jpg')
                if not(raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9')):raise RuntimeError('invalid_camera_jpeg')
                path=ROOT/'work'/('capture-%d.jpg'%i);path.write_bytes(raw);frames.append((path,int(time.time())))
                if i<2 and STOP.wait(0.5):return
            update(phase='recognizing')
            found=False
            for path,captured_at in frames:
                if STOP.is_set():return
                result=infer(path)
                if result.get('person') is True and result.get('confidence',0)>=0.60:
                    job['captured_at']=captured_at;confirmed_event(job,path,result);found=True;break
            if not found:update(last_result='no_person',last_confidence=0,last_person_count=0)
            update(phase='idle')
        except Exception as exc:
            LOG.warning('Capture/inference failed: %s',str(exc))
            update(phase='error',last_result=str(exc)[:180])
        finally:
            for path,captured_at in frames:
                try:path.unlink()
                except OSError:pass
            # The existing camera service releases itself after 20 s without
            # viewers. Never stop a live user's stream from this worker.

def controls():
    path=RUN/'control.sock'
    try:path.unlink()
    except OSError:pass
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path));os.chmod(str(path),0o600);server.listen(4);server.settimeout(1)
    while not STOP.is_set():
        try:conn,_=server.accept()
        except socket.timeout:continue
        with conn:
            conn.settimeout(2)
            try:
                raw=bytearray()
                while b'\n' not in raw and len(raw)<1024:
                    block=conn.recv(1024-len(raw))
                    if not block:break
                    raw.extend(block)
                req=json.loads(bytes(raw).split(b'\n')[0].decode('utf-8'))
                if not isinstance(req,dict):raise ValueError('invalid_request')
                action=req.get('action')
                if action=='capture' and set(req)=={'action'}:
                    with LOCK:busy=STATE['phase'] in ('capturing','recognizing')
                    if busy:raise ValueError('capture_busy')
                    JOBS.put_nowait({'trigger':'manual','score':None})
                elif action=='calibrate' and set(req)=={'action'}:send_sensor({'cmd':'calibrate','seconds':30})
                elif action=='arm' and set(req)=={'action','value'} and type(req['value']) is bool:
                    update(armed=req['value']);atomic_json(ROOT/'config.json',{'armed':req['value']})
                else:raise ValueError('unknown_action')
                response={'ok':True}
            except Exception as exc:response={'ok':False,'error':str(exc)[:160] or 'queue_full'}
            try:conn.sendall((json.dumps(response)+'\n').encode('utf-8'))
            except OSError:pass
    server.close()

def main():
    os.umask(0o077)
    for p in (ROOT,ROOT/'events',ROOT/'work',RUN,Path('/var/log/weiju-alerts')):p.mkdir(parents=True,exist_ok=True)
    lock=open(str(RUN/'agent.pid'),'a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.seek(0);lock.truncate();lock.write(str(os.getpid()));lock.flush()
    handler=RotatingFileHandler('/var/log/weiju-alerts/agent.log',maxBytes=262144,backupCount=2);handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));LOG.addHandler(handler);LOG.setLevel(logging.INFO)
    config=read_json(ROOT/'config.json',{'armed':True});update(armed=config.get('armed') is True,phase='idle')
    signal.signal(signal.SIGTERM,lambda a,b:STOP.set());signal.signal(signal.SIGINT,lambda a,b:STOP.set())
    for target in (serial_reader,worker,controls):
        t=threading.Thread(target=target);t.daemon=True;t.start()
    while not STOP.wait(1):pass
    update(phase='stopped')

if __name__=='__main__':main()
