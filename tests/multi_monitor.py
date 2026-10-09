"""Selections across monitors, on two simulated ones (800x600 and 640x480
to its right). Run with
    QT_QPA_PLATFORM=offscreen:configfile=tests/two-screens.json python tests/multi_monitor.py
"""

import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="flatshot-monitors-"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
os.environ["XDG_STATE_HOME"] = str(tmp / "state")

from flatshot import app as appmod  # noqa: E402

app = appmod.make_app()

from flatshot import capture, config, shapes, windows  # noqa: E402
from flatshot.notify import Notifier  # noqa: E402
from flatshot.qt import (  # noqa: E402
    QColor, QEvent, QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPainter, QPointF, QRect, QRectF, Qt, keyval,
)
from flatshot.session import Request, Session  # noqa: E402

screens = QGuiApplication.screens()
assert [s.geometry() for s in screens] == [QRect(0, 0, 800, 600), QRect(800, 0, 640, 480)], \
    "run with QT_QPA_PLATFORM=offscreen:configfile=tests/two-screens.json"

# The desktop: blue on the left monitor, green on the right one.
desktop = QImage(1440, 600, QImage.Format.Format_RGB32)
desktop.fill(QColor("#000000"))
p = QPainter(desktop)
p.fillRect(0, 0, 800, 600, QColor("#2050c0"))
p.fillRect(800, 0, 640, 480, QColor("#20a050"))
p.end()
capture.grab_desktop = lambda preferred="auto", pointer=False: desktop.copy()
windows.WindowFinder.supported = staticmethod(lambda: False)
windows.query_compositor = lambda: None
shots = tmp / "shots"


def session(**changes):
    done = []
    cfg = config.Config(**{"save_dir": str(shots), "filename": "{n}", "notify": False, "clipboard": "none",
                           "scan_codes": False, "detect_windows": False, **changes})
    s = Session(cfg, Request(scan=False), Notifier(interactive=False), lambda code, holds: done.append(code))
    s.start()
    for o in s.overlays:
        o.resize(o.target_screen.geometry().size())
    app.processEvents()
    return s, done


def mouse(ov, kind, x, y):
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}
    pos, left = QPointF(x, y), Qt.MouseButton.LeftButton
    event = QMouseEvent(types[kind], pos, QPointF(ov.mapToGlobal(pos)),
                        Qt.MouseButton.NoButton if kind == "move" else left,
                        Qt.MouseButton.NoButton if kind == "release" else left, Qt.KeyboardModifier.NoModifier)
    {"press": ov.mousePressEvent, "move": ov.mouseMoveEvent, "release": ov.mouseReleaseEvent}[kind](event)


def wait(done):
    for _ in range(200):
        app.processEvents()
        if done:
            return


def newest() -> QImage:
    return QImage(str(max(shots.glob("*.png"), key=lambda f: f.stat().st_mtime_ns)))


# A drag from the left monitor onto the right one, with a drawing on the right one.
s, done = session()
left, right = s.overlays
assert left.target_screen.name() == "LEFT-1" and right.target_screen.name() == "RIGHT-2"
right.commit(shapes.create("rect", QPointF(20, 150), QColor("#ff0000"), 2))
right.annotations[-1].extend(QPointF(60, 200), False)
s.set_tool("region")
mouse(left, "press", 700, 100)
mouse(left, "move", 900, 300)  # (the pointer stays with the monitor the drag started on)
assert left.sel_rect == QRectF(700, 100, 200, 200), left.sel_rect
assert right.span_rect == QRectF(-100, 100, 200, 200), right.span_rect
assert right.span_pointer == QPointF(100, 300), right.span_pointer
assert not any(o.toolbar.isVisibleTo(o) for o in s.overlays), "a toolbar shows during the drag"
mouse(left, "release", 900, 300)
wait(done)
assert done == [0], done
img = newest()
assert (img.width(), img.height()) == (200, 200), img.size()
assert QColor(img.pixel(50, 100)).name() == "#2050c0", "left part"
assert QColor(img.pixel(150, 20)).name() == "#20a050", "right part"
assert QColor(img.pixel(100 + 20, 75)).red() > 200, "the right monitor's drawing is missing"
print("ok   a drag across monitors captures both, with their drawings")

# A window across both monitors: hovering highlights both parts, a click captures all of it.
s, done = session(detect_windows=True)
left, right = s.overlays
win = windows.Window(QRect(600, 50, 400, 200), "Wide", "wide")
s._desktop_found(windows.Desktop([win], None))
left.cursor_pos = QPointF(650, 100)
left._update_hover()
assert left.hover_window and left.hover_window[1] is win
assert right.span_hover and right.span_hover[0] == QRectF(0, 50, 200, 200), right.span_hover
mouse(left, "press", 650, 100)
mouse(left, "release", 650, 100)
wait(done)
assert done == [0] and (newest().width(), newest().height()) == (400, 200), (done, newest().size())
print("ok   a window across monitors is highlighted on both and captured whole")

# Esc during the drag clears it everywhere (it acts when released).
s, done = session()
left, right = s.overlays
mouse(left, "press", 700, 100)
mouse(left, "move", 900, 300)
for kind, handler in ((QEvent.Type.KeyPress, s.key), (QEvent.Type.KeyRelease, s.key_release)):
    handler(left, QKeyEvent(kind, keyval(Qt.Key.Key_Escape), Qt.KeyboardModifier.NoModifier))
assert left.sel_rect is None and right.span_rect is None and not s.done
s.cancel()
print("ok   Esc clears a selection across monitors")

# With the setting off, a drag stays on its monitor.
s, done = session(span_monitors=False)
left, right = s.overlays
mouse(left, "press", 700, 100)
mouse(left, "move", 900, 300)
assert left.sel_rect == QRectF(700, 100, 100, 200) and right.span_rect is None, (left.sel_rect, right.span_rect)
s.cancel()
print("ok   with the setting off, a drag stays on one monitor")

# An area to record may cross monitors too (the portal shares the full
# workspace; X11 records the whole desktop), and is cut from all of it.
from flatshot import screencast  # noqa: E402

os.environ["FLATSHOT_RECORDER"] = "test-gst"
s, done = session()
left, right = s.overlays
s.set_tool("record")
mouse(left, "press", 700, 100)
mouse(left, "move", 900, 300)
mouse(left, "release", 900, 300)
assert left.rec_rect == QRectF(700, 100, 200, 200), left.rec_rect
assert right.span_rect == QRectF(-100, 100, 200, 200), right.span_rect  # its part shows on the right
target = screencast.Target(left.rec_rect.toAlignedRect(), left.target_screen, 1.0)
assert target.spans() and target.shown_area() == QRect(0, 0, 1440, 600)
frame, crop = target.crop_in(target.shown_area())
assert frame == (1440, 600) and crop == QRect(700, 100, 200, 200), (frame, crop)
argv = " ".join(screencast.launch("test-gst", target, screencast.Options(), str(tmp / "x.mkv")).argv)
assert "width=1440,height=600" in argv and "left=700" in argv, argv
argv = screencast.launch("x11", target, screencast.Options(), str(tmp / "x.mkv")).argv
assert "200x200" in argv and any(a.endswith("+700,100") for a in argv), argv
s.set_tool("region")  # leaving the record tool clears the area everywhere
assert left.rec_rect is None and right.span_rect is None
s.cancel()
# wf-recorder records one output: there the area stays on its monitor.
os.environ["FLATSHOT_RECORDER"] = "wf-recorder"
s, done = session()
left, right = s.overlays
s.set_tool("record")
mouse(left, "press", 700, 100)
mouse(left, "move", 900, 300)
assert left.sel_rect == QRectF(700, 100, 100, 200) and right.span_rect is None, (left.sel_rect, right.span_rect)
s.cancel()
os.environ.pop("FLATSHOT_RECORDER")
print("ok   a recording area crosses monitors where the recorder can take it")
print("selections across monitors ok")
