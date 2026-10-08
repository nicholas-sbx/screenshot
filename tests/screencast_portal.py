"""Flatshot's screen-cast portal client against tests/fake_screencast_portal.py
(run by desktop_services.sh): negotiation, the restore token that skips the
screen picker next time, the PipeWire descriptor, the recorder command, a
cancelled dialog, and closing the session."""

import json
import os
import sys

from flatshot.qt import QGuiApplication, QRect, QTimer

app = QGuiApplication(sys.argv[:1])

from flatshot import config, dbus, screencast  # noqa: E402

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def negotiate(cursor=True):
    screen = QGuiApplication.primaryScreen()
    portal = screencast.Portal(screencast.Target(QRect(100, 50, 400, 300), screen), cursor)
    result = []
    portal.ready.connect(lambda: result.append(("ready", "")))
    portal.failed.connect(lambda message, cancelled: result.append(("cancelled" if cancelled else "failed", message)))
    portal.start()
    QTimer.singleShot(10000, app.quit)
    timer = QTimer()
    timer.timeout.connect(lambda: result and app.quit())
    timer.start(20)
    app.exec()
    return portal, result[0] if result else ("timeout", "")


def fake_log():
    return json.loads(dbus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                "org.flatshot.Test", "Log")[0])


screen = QGuiApplication.primaryScreen()
config.update_state(screencast_tokens={})
portal, (outcome, message) = negotiate()
check(outcome == "ready", f"portal session starts ({outcome} {message})")
check(portal.node == 42 and portal.stream_rect == QRect(0, 0, 1280, 800),
      f"stream node and monitor, got {portal.node} {portal.stream_rect}")
select = [entry[1] for entry in fake_log() if entry[0] == "SelectSources"][-1]
check(select.get("types") == 1 and select.get("cursor_mode") == 2 and select.get("persist_mode") == 2
      and "restore_token" not in select, f"first SelectSources asks for a monitor, cursor, persist: {select}")
tokens = config.load_state().get("screencast_tokens")
check(tokens == {screen.name(): "token-1"}, f"restore token kept per monitor, got {tokens}")
fd = portal.pipewire_fd()
check(isinstance(fd, int) and fd > 2 and os.fstat(fd) is not None, f"PipeWire descriptor handed over ({fd})")
os.close(fd)
target = screencast.Target(QRect(100, 50, 400, 300), screen)
launch = screencast.launch("portal", target, screencast.Options(format="webm"), "/tmp/x y.webm", portal) \
    if screencast.gst_has("vp8enc") else None
if launch is not None:
    words = " ".join(launch.argv)
    check(launch.pass_fds and f"fd={launch.pass_fds[0]}" in words and "path=42" in words
          and "location=/tmp/x y.webm" in launch.argv, f"recorder command uses the stream: {launch.argv}")
    for fd in launch.pass_fds:
        os.close(fd)
portal.close()
check(fake_log()[-1][0] == "Close", "session closed")

portal, (outcome, message) = negotiate(cursor=False)
select = [entry[1] for entry in fake_log() if entry[0] == "SelectSources"][-1]
check(outcome == "ready" and select.get("restore_token") == "token-1" and select.get("cursor_mode") == 1,
      f"second session restores the screen without asking, cursor hidden: {select}")
check(config.load_state()["screencast_tokens"] == {screen.name(): "token-2"}, "the new token replaces the old")
portal.close()

dbus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", "org.flatshot.Test", "CancelNext")
portal, (outcome, message) = negotiate()
check(outcome == "cancelled", f"a cancelled dialog is reported as such ({outcome} {message})")
portal.close()

print("FAILED: " + ", ".join(failures) if failures else "screen-cast portal ok")
sys.exit(1 if failures else 0)
