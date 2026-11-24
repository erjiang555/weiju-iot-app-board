#!/bin/sh
set -eu
umask 077
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p /web-camera-backup-20261122 /opt/weiju-camera-web
if [ ! -f /web-camera-backup-20261122/rc.local ]; then
    cp -p /etc/rc.local /web-camera-backup-20261122/rc.local
fi
if [ -f /etc/init.d/weiju-camera-web ]; then /etc/init.d/weiju-camera-web stop; fi
cp "$SOURCE/camera_web.py" "$SOURCE/index.html" "$SOURCE/robot.png" /opt/weiju-camera-web/
cp "$SOURCE/weiju-camera-web.init" /etc/init.d/weiju-camera-web
chmod 755 /etc/init.d/weiju-camera-web
python3 -m py_compile /opt/weiju-camera-web/camera_web.py
python3 - <<'PY'
from pathlib import Path
p = Path('/etc/rc.local')
text = p.read_text()
line = '/etc/init.d/weiju-camera-web start'
if line not in text:
    if '\nexit 0' not in text:
        raise RuntimeError('rc.local exit marker missing')
    p.write_text(text.replace('\nexit 0', '\n# Weiju LAN camera webpage\n' + line + '\nexit 0', 1))
PY
sync
/etc/init.d/weiju-camera-web start
