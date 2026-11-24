#!/bin/sh
set -eu

STAGE=/tmp/weiju-intercom-install
TARGET=/opt/weiju-mobile
cd "$STAGE"
test -f mobile_api.py
test -f voice_audio.py
python3 -m py_compile mobile_api.py voice_audio.py
test -f "$TARGET/mobile_api.py"
BACKUP=$(mktemp -d "$TARGET/backup-intercom-XXXXXX")
cp -p "$TARGET/mobile_api.py" "$BACKUP/mobile_api.py"
HAD_AUDIO=0
if [ -f "$TARGET/voice_audio.py" ]; then
    cp -p "$TARGET/voice_audio.py" "$BACKUP/voice_audio.py"
    HAD_AUDIO=1
fi

INSTALLED=1
rollback() {
    if [ "$INSTALLED" = 1 ]; then
        cp -p "$BACKUP/mobile_api.py" "$TARGET/mobile_api.py"
        if [ "$HAD_AUDIO" = 1 ]; then
            cp -p "$BACKUP/voice_audio.py" "$TARGET/voice_audio.py"
        else
            rm -f "$TARGET/voice_audio.py"
        fi
        /etc/init.d/weiju-mobile restart || true
        echo 'INTERCOM_INSTALL_ROLLED_BACK' >&2
    fi
}
trap rollback EXIT

cp mobile_api.py "$TARGET/mobile_api.py"
cp voice_audio.py "$TARGET/voice_audio.py"
chmod 600 "$TARGET/mobile_api.py" "$TARGET/voice_audio.py"
python3 -m py_compile "$TARGET/mobile_api.py" "$TARGET/voice_audio.py"
/etc/init.d/weiju-mobile restart
COUNT=0
until /etc/init.d/weiju-mobile status >/dev/null 2>&1; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge 10 ]; then
        /etc/init.d/weiju-mobile status
        exit 1
    fi
    sleep 1
done
/etc/init.d/weiju-mobile status
INSTALLED=0
trap - EXIT
sync
echo "INTERCOM_INSTALLED backup=$BACKUP"
