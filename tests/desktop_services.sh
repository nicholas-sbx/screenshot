#!/usr/bin/env bash
# Run desktop_services.py and a tray end-to-end test inside a private
# session bus with KDE's KGlobalAccel, a notification server and Xvfb.
# Needs: dbus-daemon, Xvfb, kglobalaccel5 (libkf5globalaccel-bin), dunst,
# and a C compiler with libdbus-1 headers for the KWin capture helper.
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

echo "== KWin capture helper (against a fake org.kde.KWin.ScreenShot2)"
HELPER=$(mktemp -d)/flatshot-kwin-grab
cc -O2 -Wall -Wextra -Werror -o "$HELPER" native/flatshot-kwin-grab.c $(pkg-config --cflags --libs dbus-1)
export FLATSHOT_KWIN_GRAB=$HELPER
FAKE_KWIN_SCREENS=A:800x600,B:960x720 $PY tests/fake_kwin_screenshot.py &
pids+=($!)
sleep 1.5
for serve in 1 0; do
FLATSHOT_KWIN_SERVE=$serve $PY - <<'PYEOF'
import os, sys, time
from flatshot.qt import QGuiApplication
app = QGuiApplication(sys.argv)
from flatshot import capture
kept = os.environ["FLATSHOT_KWIN_SERVE"] == "1"
want = 0xFF000000 | (300 % 256) << 16 | (200 % 256) << 8 | 0x5A
for i in range(3):
    t = time.monotonic()
    img = capture.grab_desktop("kwin")
    ms = (time.monotonic() - t) * 1000
    assert (img.width(), img.height()) == (1500, 900), img.size()
    assert img.pixel(300, 200) == want, hex(img.pixel(300, 200))
    assert capture.kwin_kept_running() is kept, (kept, capture.last_grab)
    print(f"kwin helper ok ({capture.last_grab}): 1500x900 raw grab in {ms:.0f} ms")
extra = []
img = capture.grab_desktop("kwin", also_pointer=extra)
assert extra and extra[0].pixel(300, 200) == want, "both at once"
PYEOF
done

echo "== each screen at its own scale (1x and 1.5x monitors)"
QT_QPA_PLATFORM=offscreen:configfile=tests/mixed-scales.json PYTHONPATH=. $PY tests/kwin_screens.py

echo "== screen-cast portal (against a fake xdg-desktop-portal)"
$PY tests/fake_screencast_portal.py &
pids+=($!)
sleep 1.5
$PY tests/screencast_portal.py

echo "== tray end-to-end"
mkdir -p "$XDG_CONFIG_HOME/flatshot"
# The tray captures through the KWin helper (served by the fake above).
printf '{"save_dir": "%s", "clipboard": "none", "backend": "kwin"}\n' "$SHOTS" > "$XDG_CONFIG_HOME/flatshot/config.json"
$PY -m flatshot --tray > "$SHOTS.log" 2>&1 &
pids+=($!)
sleep 3
$PY -c "from flatshot import dbus, shortcuts as s
dbus.call(s.SERVICE, '/component/flatshot', s.COMPONENT_IFACE, 'invokeShortcut', 'ss', 'screen', 'default')"
sleep 2
$PY -m flatshot --diagnose | tee "$SHOTS.diag"
grep -q "Tray app (handles" "$SHOTS.diag" || { echo "FAIL: --diagnose didn't reach the tray"; exit 1; }
$PY -m flatshot --full   # forwarded to the tray over the local socket
sleep 2
$PY -m flatshot --monitor
sleep 2
$PY -m flatshot --region 200x100+10+20
sleep 2
$PY -c "from flatshot import dbus, shortcuts as s
dbus.call(s.SERVICE, '/component/flatshot', s.COMPONENT_IFACE, 'invokeShortcut', 'ss', 'last', 'default')"
sleep 2
$PY -m flatshot --quit
sleep 1
cat "$SHOTS.log"
count=$(ls "$SHOTS"/*.png 2>/dev/null | wc -l)
echo "screenshots taken by the tray: $count"
[ "$count" -eq 5 ] || { echo "FAIL: expected 5 screenshots"; exit 1; }
# --region and then "last region" both crop to the same 200x100 logical area.
$PY - "$SHOTS" <<'PYEOF'
import sys
from pathlib import Path
from flatshot.qt import QGuiApplication, QImage
app = QGuiApplication(sys.argv[:1])
shots = sorted(Path(sys.argv[1]).glob("*.png"), key=lambda p: p.stat().st_mtime_ns)
sizes = [QImage(str(p)).size() for p in shots]
print("sizes:", [(s.width(), s.height()) for s in sizes])
assert sizes[-1] == sizes[-2] and sizes[-1].width() < 1500, "region / last region crop"
PYEOF
echo "tray end-to-end ok"
