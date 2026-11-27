#!/bin/sh
# Keep adb's key in /data so TVs stay paired across restarts and updates.
export HOME=/data
mkdir -p /data/.android

# adb's "listen on all interfaces" mode needs IPv6, which app containers often
# lack, so adb listens on localhost and socat forwards port 5037 to it. No ports
# are published: only Home Assistant and other apps on its internal network can
# reach it.
adb -P 5039 nodaemon server &
ADB_PID=$!
echo "Starting the ADB server on port 5037"
socat TCP-LISTEN:5037,fork,reuseaddr TCP:127.0.0.1:5039 &
SOCAT_PID=$!

# Stop the app (so Supervisor restarts it) if either process exits.
while kill -0 "$ADB_PID" 2>/dev/null && kill -0 "$SOCAT_PID" 2>/dev/null; do
  sleep 5
done
kill "$ADB_PID" "$SOCAT_PID" 2>/dev/null
exit 1
