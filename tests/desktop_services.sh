#!/usr/bin/env bash
# Run desktop_services.py and a tray end-to-end test inside a private
# session bus with KDE's KGlobalAccel, a notification server and Xvfb.
# Needs: dbus-daemon, Xvfb, kglobalaccel5 (libkf5globalaccel-bin), dunst.
# Usage: tests/desktop_services.sh PYTHON
set -euo pipefail
PY=${1:-python3}
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD QT_QPA_PLATFORM=offscreen
export XDG_RUNTIME_DIR=$(mktemp -d) XDG_CONFIG_HOME=$(mktemp -d) XDG_DATA_HOME=$(mktemp -d)
chmod 700 "$XDG_RUNTIME_DIR"
SHOTS=$(mktemp -d)

Xvfb :91 -screen 0 1280x800x24 >/dev/null 2>&1 &
pids=($!)
eval "$(dbus-launch --sh-syntax)"
pids+=("$DBUS_SESSION_BUS_PID")
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT
sleep 1
# kglobalaccel needs an X display for key grabs; give D-Bus activation one too.
$PY -c "from flatshot import dbus; dbus.call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
'UpdateActivationEnvironment','a{ss}',{'DISPLAY':':91','QT_QPA_PLATFORM':'xcb'})"
DISPLAY=:91 dunst >/dev/null 2>&1 &
pids+=($!)
sleep 1

echo "== services"
$PY tests/desktop_services.py

echo "== tray end-to-end"
mkdir -p "$XDG_CONFIG_HOME/flatshot"
printf '{"save_dir": "%s", "clipboard": "none", "backend": "qt"}\n' "$SHOTS" > "$XDG_CONFIG_HOME/flatshot/config.json"
$PY -m flatshot --tray > "$SHOTS.log" 2>&1 &
pids+=($!)
sleep 3
$PY -c "from flatshot import dbus, shortcuts as s
dbus.call(s.SERVICE, '/component/flatshot', s.COMPONENT_IFACE, 'invokeShortcut', 'ss', 'screen', 'default')"
sleep 2
$PY -m flatshot --full   # forwarded to the tray over the local socket
sleep 2
$PY -m flatshot --quit
sleep 1
cat "$SHOTS.log"
count=$(ls "$SHOTS"/*.png 2>/dev/null | wc -l)
echo "screenshots taken by the tray: $count"
[ "$count" -eq 2 ] || { echo "FAIL: expected 2 screenshots"; exit 1; }
echo "tray end-to-end ok"
