#!/bin/sh
set -eu
umask 077
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p /ota-backup-20261122 /opt/weiju-ota /var/lib/weiju-ota /var/log/weiju-ota
if [ ! -f /ota-backup-20261122/rc.local ]; then cp -p /etc/rc.local /ota-backup-20261122/rc.local; fi
cp "$SOURCE/ota_agent.py" "$SOURCE/ota_core.py" "$SOURCE/signing-public.pem" "$SOURCE/https-ca.pem" /opt/weiju-ota/
cp "$SOURCE/weiju-ota.init" /etc/init.d/weiju-ota
chmod 755 /etc/init.d/weiju-ota
python3 -m py_compile /opt/weiju-ota/ota_core.py /opt/weiju-ota/ota_agent.py
python3 - <<'PY'
from pathlib import Path
import json,os,sys
sys.path.insert(0,'/opt/weiju-ota')
from ota_core import atomic_json
version=Path('/var/lib/weiju-ota/version.json')
if not version.exists(): atomic_json(version,{'version':'1.0.0','board':'imx6ull-512-emmc'})
p=Path('/etc/rc.local'); text=p.read_text()
recover='/usr/bin/python3 /opt/weiju-ota/ota_agent.py --recover'
if recover not in text:
    marker='source /etc/profile'
    if marker not in text: raise RuntimeError('rc.local profile marker missing')
    text=text.replace(marker,recover+'\n'+marker,1)
start='/etc/init.d/weiju-ota start'
if start not in text:
    if '\nexit 0' not in text: raise RuntimeError('rc.local exit marker missing')
    text=text.replace('\nexit 0','\n# OneNET signed application OTA\n'+start+'\nexit 0',1)
p.write_text(text)
PY
sync
/etc/init.d/weiju-ota start
