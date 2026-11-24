#!/usr/bin/env python3
"""Supervise the userspace VPN daemon on the existing Python 3.5 system."""
import fcntl
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import subprocess
import threading

os.umask(0o077)
for directory in ('/var/run/weiju-tailscale', '/var/lib/weiju-tailscale', '/var/log/weiju-tailscale'):
    os.makedirs(directory, mode=0o700, exist_ok=True)
lock=open('/var/run/weiju-tailscale/runner.pid','a+')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
lock.seek(0); lock.truncate(); lock.write(str(os.getpid())); lock.flush()
log=logging.getLogger('weiju-tailscale')
handler=RotatingFileHandler('/var/log/weiju-tailscale/daemon.log', maxBytes=524288, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
log.addHandler(handler); log.setLevel(logging.INFO)
stopping=threading.Event()
process=None

def stop(signum,frame):
    stopping.set()
    if process and process.poll() is None: process.terminate()

signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
args=['/opt/weiju-tailscale/tailscaled', '--tun=userspace-networking',
      '--state=/var/lib/weiju-tailscale/tailscaled.state',
      '--socket=/var/run/weiju-tailscale/tailscaled.sock', '--port=41641',
      '--no-logs-no-support']
while not stopping.is_set():
    process=subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1)
    for line in process.stdout: log.info(line.rstrip())
    code=process.wait()
    log.info('Daemon exited code=%s',code)
    if not stopping.is_set(): stopping.wait(5)
