"""Grab the whole desktop as one image.

Wayland doesn't let ordinary clients read the screen, so we lean on a
trusted helper: KDE's ``spectacle`` first on Plasma, ``grim`` on wlroots
compositors, ``gnome-screenshot`` as a last resort. On X11 Qt can grab
directly.
"""

import os
import shutil
import subprocess
import tempfile

from flatshot.qt import QGuiApplication, QImage, QPainter, QRect, QRectF


class CaptureError(RuntimeError):
    pass


def virtual_geometry() -> QRect:
    rect = QRect()
    for screen in QGuiApplication.screens():
        rect = rect.united(screen.geometry())
    return rect


def _run_tool(argv_for_path) -> QImage:
    fd, path = tempfile.mkstemp(prefix="flatshot-", suffix=".png")
    os.close(fd)
    try:
        argv = argv_for_path(path)
        subprocess.run(argv, check=True, timeout=20,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        image = QImage(path)
        if image.isNull():
            raise CaptureError(f"{argv[0]} produced no image")
        return image
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _spectacle() -> QImage:
    return _run_tool(lambda p: ["spectacle", "--background", "--nonotify", "--fullscreen", "--output", p])


def _grim() -> QImage:
    return _run_tool(lambda p: ["grim", p])


def _gnome_screenshot() -> QImage:
    return _run_tool(lambda p: ["gnome-screenshot", "-f", p])


def _qt() -> QImage:
    screens = QGuiApplication.screens()
    virt = virtual_geometry()
    dpr = max(s.devicePixelRatio() for s in screens)
    image = QImage(round(virt.width() * dpr), round(virt.height() * dpr), QImage.Format.Format_RGB32)
    image.fill(0)
    image.setDevicePixelRatio(dpr)
    p = QPainter(image)
    for screen in screens:
        shot = screen.grabWindow(0)
        if shot.isNull():
            raise CaptureError("Qt could not grab the screen")
        p.drawPixmap(QRectF(screen.geometry().translated(-virt.topLeft())), shot, QRectF(shot.rect()))
    p.end()
    image.setDevicePixelRatio(1)
    return image


BACKENDS = {
    "spectacle": ("spectacle", _spectacle),
    "grim": ("grim", _grim),
    "gnome-screenshot": ("gnome-screenshot", _gnome_screenshot),
}


def backend_order() -> list[str]:
    if not QGuiApplication.platformName().startswith("wayland"):
        return ["qt"]
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop:
        return ["spectacle", "grim", "gnome-screenshot"]
    if "gnome" in desktop:
        return ["gnome-screenshot", "grim", "spectacle"]
    return ["grim", "spectacle", "gnome-screenshot"]


def grab_desktop(preferred: str = "auto") -> QImage:
    order = backend_order() if preferred in ("", "auto") else [preferred]
    errors = []
    for name in order:
        if name == "qt":
            fn = _qt
        else:
            exe, fn = BACKENDS.get(name, (name, None))
            if fn is None or shutil.which(exe) is None:
                errors.append(f"{name}: not installed")
                continue
        try:
            return fn()
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or b"").decode(errors="replace").strip().splitlines()
            errors.append(f"{name}: {detail[-1] if detail else e}")
        except Exception as e:  # noqa: BLE001 — try the next backend
            errors.append(f"{name}: {e}")
    raise CaptureError("could not capture the screen\n  " + "\n  ".join(errors))
