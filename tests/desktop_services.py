"""Integration test against real desktop services on the session bus:
KDE's KGlobalAccel daemon and a notification server (run by
desktop_services.sh). Exercises register / assign / persist / press and
notification action round-trips."""

import sys
from pathlib import Path

from jeepney import DBusAddress, new_signal
from jeepney.io.blocking import open_dbus_connection

from flatshot import dbus, shortcuts
from flatshot.notify import PATH as NOTIFY_PATH, SERVICE as NOTIFY_SERVICE, Notifier
from flatshot.qt import QCoreApplication, QTimer

app = QCoreApplication(sys.argv)
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def run_until(predicate, ms=5000):
    QTimer.singleShot(ms, app.quit)
    timer = QTimer()
    timer.timeout.connect(lambda: predicate() and app.quit())
    timer.start(50)
    app.exec()
    timer.stop()


# -- KDE global shortcuts --------------------------------------------------
g = shortcuts.GlobalShortcuts()
check(g.supported(), "kglobalaccel is on the bus")
check(g.start(), f"register component ({g.error})")
check(g.get("region") == "Ctrl+Print", f"default region shortcut, got {g.get('region')!r}")
ok, now = g.assign("region", "Meta+Shift+X")
check(ok and now == "Meta+Shift+X", f"assign Meta+Shift+X, got {(ok, now)!r}")
check(g.get("region") == "Meta+Shift+X", "assignment reads back")
ok, now = g.assign("screen", "")
check(ok and g.get("screen") == "", "clear a shortcut")
check(g.owner_of("Meta+Shift+X", "screen") == "Flatshot › Capture region",
      f"conflict reports the owner, got {g.owner_of('Meta+Shift+X', 'screen')!r}")
check(g.owner_of("Meta+Shift+X", "region") == "", "no conflict with itself")

again = shortcuts.GlobalShortcuts()
check(again.start() and again.get("region") == "Meta+Shift+X", "user shortcut survives re-registration")

pressed = []
g.triggered.connect(pressed.append)
QTimer.singleShot(200, lambda: dbus.call(shortcuts.SERVICE, "/component/flatshot", shortcuts.COMPONENT_IFACE,
                                         "invokeShortcut", "ss", "region", "default"))
run_until(lambda: pressed)
check(pressed == ["region"], f"shortcut press reaches the app, got {pressed}")

# -- Notifications with actions ---------------------------------------------
n = Notifier(interactive=True)
acted = []
n.send("Screenshot saved", "test", image=Path(__file__).parent.parent / "flatshot/assets/flatshot.png",
       actions={"default": "Open", "folder": "Show in folder"}, on_action=acted.append)
check(len(n._handlers) == 1, "notification server accepted Notify with actions and hints")
nid = next(iter(n._handlers), 0)
conn = open_dbus_connection("SESSION")
QTimer.singleShot(200, lambda: conn.send(new_signal(DBusAddress(NOTIFY_PATH, interface=NOTIFY_SERVICE),
                                                    "ActionInvoked", "us", (nid, "folder"))))
run_until(lambda: acted)
check(acted == ["folder"], f"notification action reaches the app, got {acted}")

print("FAILED: " + ", ".join(failures) if failures else "desktop services ok")
sys.exit(1 if failures else 0)
