#!/bin/sh
# Manual recovery only. This briefly disconnects BOTH ESP32 and USB camera.
# Use only on the verified I.MX6ULL host path, after checking dmesg -110 errors.
set -eu
test -d /sys/bus/platform/drivers/ci_hdrc/ci_hdrc.1
printf ci_hdrc.1 > /sys/bus/platform/drivers/ci_hdrc/unbind
sleep 2
printf ci_hdrc.1 > /sys/bus/platform/drivers/ci_hdrc/bind
sleep 3
ls -l /dev/ttyACM* 2>/dev/null || true
