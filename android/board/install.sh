#!/bin/sh
set -eu
cd /tmp/weiju-mobile-install
mkdir -p /opt/weiju-mobile /etc/weiju-mobile
chmod 700 /etc/weiju-mobile
test -f /etc/weiju-mobile/config.json || cp config.json /etc/weiju-mobile/config.json
test -f /etc/weiju-mobile/server.pem || cp server.pem /etc/weiju-mobile/server.pem
test -f /etc/weiju-mobile/server-key.pem || cp server-key.pem /etc/weiju-mobile/server-key.pem
chmod 600 /etc/weiju-mobile/*
cp mobile_api.py /opt/weiju-mobile/mobile_api.py
cp voice_audio.py /opt/weiju-mobile/voice_audio.py
cp alerts_api.py /opt/weiju-mobile/alerts_api.py
cp weiju-mobile.init /etc/init.d/weiju-mobile
chmod 755 /etc/init.d/weiju-mobile
python3 -m py_compile /opt/weiju-mobile/mobile_api.py
python3 -m py_compile /opt/weiju-mobile/voice_audio.py
python3 - <<'PY'
from pathlib import Path
import os
import re
import shutil
import subprocess

p = Path('/etc/rc.local')
s = p.read_text()
line = '/etc/init.d/weiju-mobile start'
if not re.search(r'(?m)^' + re.escape(line) + r'$', s):
    markers = list(re.finditer(r'(?m)^exit 0[ \t]*$', s))
    if len(markers) != 1:
        raise RuntimeError('Expected one final rc.local exit 0 line')
    backup = Path('/etc/weiju-mobile/rc.local.before-mobile')
    if not backup.exists():
        shutil.copy2(str(p), str(backup))
    m = markers[0]
    temporary = Path('/etc/rc.local.mobile-new')
    temporary.write_text(s[:m.start()] + line + '\n' + s[m.start():])
    os.chmod(str(temporary), p.stat().st_mode & 0o777)
    subprocess.check_call(['sh', '-n', str(temporary)])
    os.replace(str(temporary), str(p))
PY
/etc/init.d/weiju-mobile restart
sync
echo MOBILE_ADAPTER_INSTALLED
