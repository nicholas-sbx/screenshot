"""Each screen on its own, at its own scale, through flatshot-kwin-grab and a
fake KWin (tests/fake_kwin_screenshot.py with FAKE_KWIN_SCREENS="A:800x600,
B:960x720"), on two offscreen monitors at 1x and 1.5x (tests/mixed-scales.json).

Run: QT_QPA_PLATFORM=offscreen:configfile=tests/mixed-scales.json \\
     FLATSHOT_KWIN_GRAB=... PYTHONPATH=. python3 tests/kwin_screens.py
"""

import sys
import tempfile
import time
from pathlib import Path

from flatshot.qt import QApplication, QColor, QImage, QRect, QRectF

app = QApplication(sys.argv)

from flatshot import capture, config  # noqa: E402
from flatshot.notify import Notifier  # noqa: E402
from flatshot.session import Request, Session  # noqa: E402


def pixel(x, y, tag):
    return 0xFF000000 | (x % 256) << 16 | (y % 256) << 8 | tag


def wait(cond, seconds=5):
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


# The capture: each screen's own picture, and the whole desktop made from them at 1.5x.
screens = {}
t = time.monotonic()
desktop = capture.grab_desktop("kwin", screens=screens)
ms = (time.monotonic() - t) * 1000
assert screens["A"].size().width() == 800 and screens["B"].size().width() == 960, screens
assert screens["B"].format() == QImage.Format.Format_RGB32, "ARGB32 made RGB32"
assert screens["B"].pixel(300, 200) == pixel(300, 200, 0x20), hex(screens["B"].pixel(300, 200))
assert (desktop.width(), desktop.height()) == (2160, 900), desktop.size()
assert desktop.pixel(1200 + 300, 200) == pixel(300, 200, 0x20), "B in the whole desktop, pixel for pixel"
print(f"ok   each screen at its own scale in {ms:.0f} ms via {capture.last_grab}")

# With and without the pointer at once: four pictures in one request.
screens, extra = {}, []
capture.grab_desktop("kwin", screens=screens, also_pointer=extra)
assert screens[("pointer", "B")].pixel(5, 5) == pixel(5, 5, 0xA0) and screens["B"].pixel(5, 5) == pixel(5, 5, 0x20)
assert extra and extra[0].size() == desktop.size()
print("ok   with and without the pointer, each screen")

# The overlays: each shows its own screen's pixels; a selection on B is B's own pixels.
tmp = Path(tempfile.mkdtemp())
done = []
cfg = config.Config(save_dir=str(tmp), notify=False, clipboard="none", backend="kwin", screenshot_method="screens",
                    show_codes=False, detect_windows=False)
s = Session(cfg, Request(scan=False), Notifier(interactive=False), lambda code, holds: done.append(code))
s.start()
a, b = sorted(s.overlays, key=lambda o: o.target_screen.name())
assert (a.base.width(), a.dpr()) == (800, 1.0) and (b.base.width(), b.dpr()) == (960, 1.5), (b.base.size(), b.dpr())
part = b.render(QRectF(10, 20, 100, 50))
assert (part.width(), part.height()) == (150, 75), part.size()
assert part.pixel(0, 0) == pixel(15, 30, 0x20), hex(part.pixel(0, 0))
s.capture(b, QRectF(10, 20, 100, 50))
assert wait(lambda: done), "the capture didn't finish"
saved = list(tmp.glob("*.png"))
assert len(saved) == 1 and QImage(str(saved[0])).width() == 150, saved
print("ok   overlays show each screen's own pixels; a selection on B saved at 150 x 75")

# The pointer button: each screen's own picture with the pointer.
cfg_toggle = config.Config(save_dir=str(tmp), notify=False, clipboard="none", backend="kwin",
                           screenshot_method="screens", show_codes=False, detect_windows=False, region_pointer="toggle")
s = Session(cfg_toggle, Request(scan=False), Notifier(interactive=False), lambda *a: None)
s.start()
b = max(s.overlays, key=lambda o: o.target_screen.name())
assert b.pixels().pixel(30, 40) == pixel(30, 40, 0x20)
s.toggle_pointer()
assert b.pixels().pixel(30, 40) == pixel(30, 40, 0xA0) and b.dpr() == 1.5, hex(b.pixels().pixel(30, 40))
s.cancel()
print("ok   the pointer button, each screen")

# An instant capture of an area on B: from B's own picture.
done.clear()
s = Session(cfg, Request(mode="rect", rect=QRect(810, 20, 100, 50), scan=False), Notifier(interactive=False),
            lambda code, holds: done.append(code))
s.start()
assert wait(lambda: done) and done == [0], done
newest = max(tmp.glob("*.png"), key=lambda p: p.stat().st_mtime_ns)
shot = QImage(str(newest))
assert (shot.width(), shot.height()) == (150, 75) and QColor(shot.pixel(0, 0)).rgb() == pixel(15, 30, 0x20), \
    (shot.size(), hex(shot.pixel(0, 0)))
print("ok   an area on B, without the overlay: B's own pixels")
print("each screen ok")
