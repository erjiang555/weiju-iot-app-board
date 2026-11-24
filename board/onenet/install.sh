#!/bin/sh
set -eu
umask 077
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKUP=/onenet-backup-20261122
mkdir -p "$BACKUP" /opt/weiju-onenet /var/lib/weiju-onenet
if [ ! -f "$BACKUP/rc.local" ]; then
    cp -p /etc/rc.local "$BACKUP/rc.local"
    cat /sys/class/leds/sys-led/trigger > "$BACKUP/led-trigger.txt"
    cat /sys/class/leds/sys-led/brightness > "$BACKUP/led-brightness.txt"
fi
if [ -f /etc/init.d/weiju-onenet ]; then /etc/init.d/weiju-onenet stop; fi
cp "$SOURCE/onenet_agent.py" "$SOURCE/test_agent.py" /opt/weiju-onenet/
cp -R "$SOURCE/vendor" /opt/weiju-onenet/
cp "$SOURCE/vendor/certificate.pem" /opt/weiju-onenet/onenet-ca.pem
cp "$SOURCE/config.json" /etc/weiju-onenet.json
chmod 600 /etc/weiju-onenet.json
cp "$SOURCE/weiju-onenet.init" /etc/init.d/weiju-onenet
chmod 755 /etc/init.d/weiju-onenet
cd /opt/weiju-onenet
python3 -m unittest -v test_agent
python3 - <<'PY'
from pathlib import Path
p = Path('/etc/rc.local')
text = p.read_text()
line = '/etc/init.d/weiju-onenet start'
if line not in text:
    if '\nexit 0' not in text:
        raise RuntimeError('No rc.local exit marker; autostart was not changed')
    p.write_text(text.replace('\nexit 0', '\n# Weiju OneNET temperature and LED service\n' + line + '\nexit 0', 1))
PY
sync
/etc/init.d/weiju-onenet start
