"""Public OneNET API round trip; use no LAN endpoint for the control path."""
from pathlib import Path
import json
import time
from urllib.parse import urlencode
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
p=json.loads((ROOT/'private/device-pairing-cloud.weiju').read_text())
query='?'+urlencode({'product_id':p['product_id'],'device_name':p['device_name']})

def call(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    req=Request('https://iot-api.heclouds.com'+path,data=data,headers={'Authorization':p['cloud_token'],'Content-Type':'application/json'})
    with urlopen(req,timeout=20) as response:r=json.load(response)
    if r.get('code')!=0:raise RuntimeError('OneNET code={} msg={}'.format(r.get('code'),r.get('msg')))
    return r['data']

def properties():
    raw=call('/thingmodel/query-device-property'+query)
    data=raw if isinstance(raw,list) else raw['list']
    return {i['identifier']:i for i in data}

detail=call('/device/detail'+query)
assert detail['status']==1
print('PASS public HTTPS device status=online',flush=True)
initial=properties()
temperature=float(initial['cpu_temperature']['value'])
assert -40<=temperature<=125
print('PASS public HTTPS temperature query; data_type='+str(initial['cpu_temperature']['data_type']),flush=True)
original=str(initial['led_switch']['value']).lower()=='true'
acks=[]
try:
    for value in (not original,original):
        sent=int(time.time()*1000)
        result=call('/thingmodel/set-device-property',{'product_id':p['product_id'],'device_name':p['device_name'],'params':{'led_switch':value}})
        assert result['code']==200,result
        for retry in range(12):
            latest=properties()['led_switch']
            if (str(latest['value']).lower()=='true') is value and int(latest['time'])>=sent-1500:break
            time.sleep(0.5)
        else:raise AssertionError('new hardware report was not received')
        acks.append({'led_switch':value,'device_code':result['code'],'message_id':result.get('id'),'report_time':latest['time']})
        print('PASS public HTTPS LED={} acknowledged by device and new report'.format(value),flush=True)
finally:
    call('/thingmodel/set-device-property',{'product_id':p['product_id'],'device_name':p['device_name'],'params':{'led_switch':original}})
assert call('/device/detail'+query)['status']==1
print('PASS board MQTT connection remains online',flush=True)
(ROOT/'dist/cloud-verification.json').write_text(json.dumps({'temperature':temperature,'cloud_status':'online',
    'commands':acks,'restored_led':original,'control_transport':'https://iot-api.heclouds.com','lan_control_used':False},indent=2),encoding='utf-8')
