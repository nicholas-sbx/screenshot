"""Grab the whole desktop as one image.

Wayland doesn't let ordinary clients read the screen, so we lean on a
trusted helper. On KDE Plasma that's ``flatshot-kwin-grab`` (our own small
native helper, allowed to use KWin's screenshot API, raw pixels, no PNG round
trip) and then ``spectacle``; ``grim`` on wlroots compositors;
``gnome-screenshot`` on GNOME. On X11 Qt can grab directly.
"""

import fcntl
import os
import re
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from pathlib import Path

from flatshot.qt import QGuiApplication, QImage, QPainter, QRect, QRectF

# Where packages install the KWin helper; its .desktop file must name this
# exact path for KWin to authorize it.
KWIN_HELPER_PATHS = ("/usr/lib/flatshot/flatshot-kwin-grab", "/usr/libexec/flatshot/flatshot-kwin-grab")


class CaptureError(RuntimeError):
    pass


def virtual_geometry() -> QRect:
    rect = QRect()
    for screen in QGuiApplication.screens():
        rect = rect.united(screen.geometry())
    return rect


def parse_geometry(text: str) -> QRect | None:
    """'WxH+X+Y' (as in Flameshot and X11 geometry) -> QRect, global logical px."""
    m = re.fullmatch(r"\s*(\d+)x(\d+)([+-]\d+)([+-]\d+)\s*", text or "")
    if not m:
        return None
    w, h, x, y = (int(v) for v in m.groups())
    return QRect(x, y, w, h) if w > 0 and h > 0 else None


def to_pixels(image: QImage, rect: QRect) -> QRect:
    """A rect in global logical coordinates -> its pixels in a desktop grab."""
    virt = virtual_geometry()
    scale = image.width() / max(1, virt.width())
    return QRect(round((rect.x() - virt.x()) * scale), round((rect.y() - virt.y()) * scale),
                 round(rect.width() * scale), round(rect.height() * scale)).intersected(image.rect())


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


def kwin_helper() -> str | None:
    override = os.environ.get("FLATSHOT_KWIN_GRAB")
    for path in ([override] if override else []) + list(KWIN_HELPER_PATHS):
        if path and os.access(path, os.X_OK):
            return path
    return None


def _kwin(pointer: bool) -> QImage:
    """KWin's picture: through the helper kept running (``--serve``), KWin
    writing the pixels straight into a pipe of ours and on into the image;
    else through a helper run for this one capture."""
    helper = kwin_helper()
    if helper is None:
        raise CaptureError("flatshot-kwin-grab is not installed")
    server = _kwin_server(helper)
    if server is not None:
        try:
            return server.grab(pointer)
        except _ServerGone:
            _drop_server(server)  # (a new one is started next time)
    return _kwin_once(helper, pointer)


def _kwin_once(helper: str, pointer: bool) -> QImage:
    proc = subprocess.Popen([helper] + (["--cursor"] if pointer else []), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    try:
        started = time.monotonic()
        head = proc.stdout.readline()  # (unbuffered: no pixels read along with it)
        if not head:
            proc.wait(15)
            raise CaptureError(proc.stderr.read().decode(errors="replace").strip() or f"exit {proc.returncode}")
        replied = time.monotonic()
        image = _read_raw(head, proc.stdout.fileno())
        _note_times(started, replied)
        return image
    finally:
        proc.stdout.close()
        proc.stderr.close()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()


# How the last KWin capture's time divided, for --verbose.
_kwin_times = ""


def _note_times(started: float, replied: float):
    global _kwin_times
    now = time.monotonic()
    _kwin_times = f"KWin answered in {(replied - started) * 1000:.0f} ms, pixels in {(now - replied) * 1000:.0f} ms"


def _read_raw(head: bytes, fd: int) -> QImage:
    """The picture after a "FLATSHOT-RAW w h stride format" line: read from
    ``fd`` straight into the image's memory where the rows are laid out
    alike (they are, for KWin's 32-bit formats)."""
    parts = head.split()
    if len(parts) != 5 or parts[0] != b"FLATSHOT-RAW":
        raise CaptureError("unexpected output from flatshot-kwin-grab: " + head.decode(errors="replace").strip())
    width, height, stride, fmt = (int(x) for x in parts[1:])
    image = QImage(width, height, QImage.Format(fmt))
    if image.isNull():
        raise CaptureError(f"unsupported pixel format {fmt}")
    if image.bytesPerLine() == stride:
        _read_exactly(fd, _writable(image))
        return image
    data = bytearray(stride * height)
    _read_exactly(fd, memoryview(data))
    return QImage(bytes(data), width, height, stride, QImage.Format(fmt)).copy()


def _writable(image: QImage) -> memoryview:
    bits = image.bits()
    if hasattr(bits, "setsize"):  # PyQt6
        bits.setsize(image.sizeInBytes())
    return memoryview(bits).cast("B")


def _read_exactly(fd: int, into: memoryview, timeout: float = 15.0):
    got, size = 0, len(into)
    while got < size:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            raise CaptureError("flatshot-kwin-grab: no picture came")
        n = os.readv(fd, [into[got:]])
        if n == 0:
            raise CaptureError(f"short image from flatshot-kwin-grab ({got} of {size} bytes)")
        got += n


def _big_pipe() -> tuple[int, int]:
    """A pipe as big as the system allows (up to 16 MiB), so a picture goes
    through in a few large writes rather than hundreds of 64 KiB ones."""
    r, w = os.pipe2(os.O_CLOEXEC)
    try:
        with open("/proc/sys/fs/pipe-max-size") as f:
            size = min(int(f.read()), 16 << 20)
        fcntl.fcntl(w, getattr(fcntl, "F_SETPIPE_SZ", 1031), size)
    except (OSError, ValueError):
        pass  # (the usual size then)
    return r, w


class _ServerGone(Exception):
    pass


class _KWinServer:
    """flatshot-kwin-grab --serve, started once and kept: a capture then
    costs neither starting a process nor connecting to D-Bus."""

    def __init__(self, helper: str):
        self.helper = helper
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self.proc = subprocess.Popen([helper, "--serve"], stdin=theirs, stdout=subprocess.DEVNULL,
                                         start_new_session=True)
        finally:
            theirs.close()
        mine.settimeout(5)
        self.sock = mine
        self.lock = threading.Lock()
        try:
            hello = mine.recv(64)
        except OSError as e:
            self.close()
            raise _ServerGone(str(e)) from e
        if not hello.startswith(b"FLATSHOT-SERVE"):
            self.close()
            raise _ServerGone("no hello from flatshot-kwin-grab --serve")

    def grab(self, pointer: bool) -> QImage:
        r, w = _big_pipe()
        started = time.monotonic()
        try:
            with self.lock:  # (one request at a time; the pixels are read after, side by side)
                try:
                    socket.send_fds(self.sock, [b"grab cursor" if pointer else b"grab"], [w])
                    os.close(w)
                    w = -1
                    head = self.sock.recv(512)
                except OSError as e:
                    raise _ServerGone(str(e)) from e
            if not head:
                raise _ServerGone("flatshot-kwin-grab --serve stopped")
            if head.startswith(b"ERR "):
                raise CaptureError(head[4:].decode(errors="replace").strip())
            replied = time.monotonic()
            image = _read_raw(head, r)
            _note_times(started, replied)
            return image
        finally:
            os.close(r)
            if w >= 0:
                os.close(w)

    def close(self):
        try:
            self.sock.close()  # (the helper ends when it sees that)
        finally:
            try:
                self.proc.wait(1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


_server: _KWinServer | None = None
_server_lock = threading.Lock()
_server_failed: set = set()  # helpers (path, mtime) that can't serve: too old, or broken


def _kwin_server(helper: str) -> _KWinServer | None:
    """The helper kept running, started if need be; None if it can't be."""
    global _server
    if os.environ.get("FLATSHOT_KWIN_SERVE", "1") == "0":
        return None
    try:
        key = (helper, os.stat(helper).st_mtime_ns)
    except OSError:
        return None
    with _server_lock:
        if _server is not None and _server.helper == helper and _server.proc.poll() is None:
            return _server
        if _server is not None:
            _server.close()
            _server = None
        if key in _server_failed:
            return None
        try:
            version = subprocess.run([helper, "--version"], capture_output=True, timeout=5).stdout.split()
            if len(version) < 2 or int(version[-1]) < 3:
                raise _ServerGone("too old to stay running")
            _server = _KWinServer(helper)
        except (OSError, ValueError, subprocess.SubprocessError, _ServerGone):
            _server_failed.add(key)
            return None
        return _server


def _drop_server(server: _KWinServer):
    global _server
    with _server_lock:
        if _server is server:
            _server = None
    server.close()


def kwin_kept_running() -> bool:
    return _server is not None and _server.proc.poll() is None


def warm_up(preferred: str = "auto") -> None:
    """Start the KWin helper now (the tray app does, in the background), so
    the first capture doesn't wait for it."""
    order = backend_order() if preferred in ("", "auto") else [preferred]
    helper = kwin_helper()
    if order and order[0] == "kwin" and helper is not None:
        threading.Thread(target=_kwin_server, args=(helper,), daemon=True).start()


def _spectacle(pointer: bool) -> QImage:
    extra = ["--pointer"] if pointer else []
    return _run_tool(lambda p: ["spectacle", "--background", "--nonotify", "--fullscreen", *extra, "--output", p])


def _grim(pointer: bool) -> QImage:
    return _run_tool(lambda p: ["grim", *(["-c"] if pointer else []), p])


def _gnome_screenshot(pointer: bool) -> QImage:
    return _run_tool(lambda p: ["gnome-screenshot", *(["-p"] if pointer else []), "-f", p])


def _qt(pointer: bool) -> QImage:
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
    "kwin": ("", _kwin),
    "spectacle": ("spectacle", _spectacle),
    "grim": ("grim", _grim),
    "gnome-screenshot": ("gnome-screenshot", _gnome_screenshot),
}


def backend_order() -> list[str]:
    if not QGuiApplication.platformName().startswith("wayland"):
        return ["qt"]
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop:
        return ["kwin", "spectacle", "grim", "gnome-screenshot"]
    if "gnome" in desktop:
        return ["gnome-screenshot", "grim", "spectacle"]
    return ["grim", "spectacle", "gnome-screenshot"]


# Which helper took the last screenshot, and what it was asked for (for --verbose).
last_grab = ""

# Helpers that can be asked twice at once (Spectacle may hand a second
# request to the first instance).
TWICE = ("kwin", "grim")


def grab_desktop(preferred: str = "auto", pointer: bool = False, also_pointer: list | None = None) -> QImage:
    """Every screen as one image; ``pointer`` draws the mouse pointer in
    (where the helper supports it). With ``also_pointer`` (a list), the
    same moment with the pointer drawn in is appended to it too, where the
    helper can take both at once."""
    global last_grab
    last_grab = ""
    order = backend_order() if preferred in ("", "auto") else [preferred]
    errors = []
    for name in order:
        if also_pointer is not None and name in TWICE and not pointer:
            exe, fn = BACKENDS[name]
            if not exe or shutil.which(exe) is not None:
                second = threading.Thread(target=_also, args=(fn, also_pointer), daemon=True)
                second.start()
                try:
                    image = fn(False)
                except Exception as e:  # noqa: BLE001 — the next backend
                    second.join(5)
                    also_pointer.clear()
                    errors.append(f"{name}: {e}")
                    continue
                second.join(5)  # (both ask for the same frame: about as quick as one)
                note = _opaque(image)
                for other in also_pointer:
                    _opaque(other)
                last_grab = _describe(name, ", with and without the pointer at once") + note + (
                    "" if also_pointer else " (the one with the pointer failed)")
                return image
        if name == "qt":
            fn = _qt
        else:
            exe, fn = BACKENDS.get(name, (name, None))
            if fn is None or (exe and shutil.which(exe) is None):
                errors.append(f"{name}: not installed")
                continue
        try:
            image = fn(pointer)
            note = _opaque(image)
            last_grab = _describe(name, ", with the pointer" if pointer else "") + note + (
                f" (after {'; '.join(errors)})" if errors else "")
            return image
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or b"").decode(errors="replace").strip().splitlines()
            errors.append(f"{name}: {detail[-1] if detail else e}")
        except Exception as e:  # noqa: BLE001 — try the next backend
            errors.append(f"{name}: {e}")
    raise CaptureError("could not capture the screen\n  " + "\n  ".join(errors))


_OPAQUE = (QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied)


def _opaque(image: QImage) -> str:
    """Make a screenshot RGB32, in place, the format Qt shows and draws
    fastest. A screen is opaque, so ARGB32 is the same bytes and is only
    relabelled; anything else is converted (about a millisecond). Without
    this, an ARGB32 picture costs a premultiplying pass over every pixel
    each time it's made ready to show. Returns a note for --verbose."""
    fmt = image.format()
    if fmt == QImage.Format.Format_RGB32:
        return ""
    if fmt in _OPAQUE:
        image.reinterpretAsFormat(QImage.Format.Format_RGB32)
    else:
        image.convertTo(QImage.Format.Format_RGB32)
    return f", {fmt.name.removeprefix('Format_')} as RGB32"


def _describe(name: str, what: str) -> str:
    """Which helper took it, for --verbose: for KWin's, whether it was kept
    running and how the time divided."""
    if name != "kwin":
        return name + what
    notes = (["kept running"] if kwin_kept_running() else []) + ([_kwin_times] if _kwin_times else [])
    return f"kwin{what}" + (f" ({'; '.join(notes)})" if notes else "")


def _also(fn, out: list):
    try:
        out.append(fn(True))
    except Exception:  # noqa: BLE001 — then there's no pointer to show
        pass
