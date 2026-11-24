#!/bin/sh
set -eu
umask 077
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=/opt/weiju-voice
BACKUP=/voice-backup-20261122
mkdir -p "$BACKUP" "$ROOT/prompts" /var/lib/weiju-voice /var/log/weiju-voice
if [ ! -f "$BACKUP/rc.local" ]; then cp -p /etc/rc.local "$BACKUP/rc.local"; fi
test -f "$ROOT/runtime/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3"
test -f "$ROOT/vosk/libvosk.so"
test -f "$ROOT/vosk-model-small-cn-0.22/am/final.mdl"
if [ -f /etc/init.d/weiju-voice ]; then /etc/init.d/weiju-voice stop; fi
cp "$SOURCE/voice_core.py" "$SOURCE/voice_service.py" "$SOURCE/test_voice_core.py" "$ROOT/"
cp "$SOURCE/weiju-voice.init" /etc/init.d/weiju-voice
chmod 755 /etc/init.d/weiju-voice
python3 -m py_compile "$ROOT/voice_core.py" "$ROOT/voice_service.py"
cd "$ROOT"
python3 -m unittest -v test_voice_core
python3 - <<'PY'
from pathlib import Path
p = Path('/etc/rc.local')
text = p.read_text()
line = '/etc/init.d/weiju-voice start'
if line not in text:
    if '\nexit 0' not in text:
        raise RuntimeError('rc.local exit marker missing')
    p.write_text(text.replace('\nexit 0', '\n# Weiju offline Chinese voice commands\n' + line + '\nexit 0', 1))
PY
sync
/etc/init.d/weiju-voice start
