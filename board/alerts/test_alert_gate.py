"""Exercise the worker with injected camera/inference dependencies, no hardware."""
import importlib.util,json,tempfile,threading,time
from pathlib import Path

spec=importlib.util.spec_from_file_location('agent','/opt/weiju-alerts/alert_agent.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def run_case(results):
    with tempfile.TemporaryDirectory(prefix='weiju-alert-test-') as tmp:
        a.ROOT=Path(tmp);a.RUN=Path(tmp)/'run'
        for p in (a.ROOT/'events',a.ROOT/'work',a.RUN):p.mkdir()
        a.STOP.clear();a.STATE={'armed':True,'phase':'idle'}
        calls=[]
        def camera(path,body=None):
            calls.append(path)
            if path=='/snapshot.jpg':return b'\xff\xd8test\xff\xd9'
            return {'state':'running'}
        a.camera=camera
        count=[0]
        def infer(path):
            r=results[min(count[0],len(results)-1)];count[0]+=1
            if isinstance(r,Exception):raise r
            return r
        a.infer=infer
        a.JOBS.put({'trigger':'manual','score':None})
        t=threading.Thread(target=a.worker);t.start()
        end=time.monotonic()+8
        while time.monotonic()<end:
            if a.STATE.get('last_result'):break
            time.sleep(0.1)
        a.STOP.set();t.join(3)
        events=a.read_json(a.ROOT/'index.json',[])
        assert not list((a.ROOT/'work').glob('*.jpg')),'working images not cleaned'
        assert '/api/stop' not in calls,'must not interrupt live viewers'
        return events,a.STATE

negative={'ok':True,'person':False,'count':0,'confidence':0,'inference_ms':1,'bbox':[0,0,0,0]}
positive={'ok':True,'person':True,'count':1,'confidence':0.9,'inference_ms':1,'bbox':[0.1,0.1,0.8,0.9]}
events,state=run_case([negative]);assert not events and state['last_result']=='no_person'
print('PASS no-person capture never produces notification event')
events,state=run_case([negative,positive]);assert len(events)==1 and events[0]['type']=='person_detected'
print('PASS confirmed person produces one persisted event')
events,state=run_case([RuntimeError('inference_failed')]);assert not events and state['phase']=='error'
print('PASS inference failure does not become person alert')
low=dict(positive,confidence=0.3)
events,state=run_case([low]);assert not events
print('PASS low-confidence detection is rejected')
