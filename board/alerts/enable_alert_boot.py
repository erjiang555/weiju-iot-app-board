from pathlib import Path
import os
import re
import shutil
import subprocess

p = Path('/etc/rc.local')
s = p.read_text()
line = '/etc/init.d/weiju-alerts start'
if not re.search(r'(?m)^' + re.escape(line) + r'$', s):
    markers = list(re.finditer(r'(?m)^exit 0[ \t]*$', s))
    if len(markers) != 1:
        raise RuntimeError('Expected one final rc.local exit 0 line')
    backup = Path('/var/lib/weiju-alerts/backup/rc.local.before-autostart')
    if not backup.exists():
        shutil.copy2(str(p), str(backup))
    m = markers[0]
    tmp = p.with_name('rc.local.alert-new')
    tmp.write_text(s[:m.start()] + line + '\n' + s[m.start():])
    os.chmod(str(tmp), p.stat().st_mode & 0o777)
    subprocess.check_call(['sh', '-n', str(tmp)])
    os.replace(str(tmp), str(p))
print('ALERT_AUTOSTART_ENABLED')
