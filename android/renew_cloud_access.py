"""Issue a finite product token locally; the long-term key never enters the APK."""
from pathlib import Path
import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import quote

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--days',type=int,default=30)
    args=parser.parse_args()
    if not 1<=args.days<=30:
        raise ValueError('Token lifetime must be 1 to 30 days')
    key=base64.b64decode((ROOT/'private/product-access-key.txt').read_text().strip(),validate=True)
    product_id=os.environ.get('WEIJU_PRODUCT_ID','')
    if not re.match(r'^[A-Za-z0-9]{6,64}$', product_id):
        raise ValueError('Set WEIJU_PRODUCT_ID to your OneNET product ID')
    resource='products/'+product_id
    expires=int(time.time())+args.days*86400
    version='2022-05-01'
    message='{}\nsha256\n{}\n{}'.format(expires,resource,version).encode('utf-8')
    signature=base64.b64encode(hmac.new(key,message,hashlib.sha256).digest()).decode('ascii')
    token='&'.join('{}={}'.format(k,quote(str(v),safe='')) for k,v in [
        ('version',version),('res',resource),('et',expires),('method','sha256'),('sign',signature)])
    pairing=json.loads((ROOT/'private/device-pairing.weiju').read_text())
    pairing.update({'cloud_token':token,'cloud_expires':expires,'product_id':product_id})
    target=ROOT/'private/device-pairing-cloud.weiju'
    target.write_text(json.dumps(pairing,ensure_ascii=False,indent=2),encoding='utf-8')
    expiry=datetime.datetime.fromtimestamp(expires,datetime.timezone(datetime.timedelta(hours=8)))
    print('CLOUD_PAIRING_READY; expiry='+expiry.isoformat()+'; token omitted')

if __name__=='__main__':main()
