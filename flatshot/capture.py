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
    """The picture after a "FLATSHOT-RAW w h stride format" line, read
    from ``fd``."""
    into, finish = _target(head)
    _read_exactly(fd, into)
    return finish()


def _target(head: bytes):
    """Where the pixels after a "FLATSHOT-RAW w h stride format" line go:
    (buffer, finish), finish() giving the picture once they're in. Straight
    into the image's memory where its rows are laid out alike (they are,
    for KWin's 32-bit formats)."""
    parts = head.split()
    if len(parts) != 5 or parts[0] != b"FLATSHOT-RAW":
        raise CaptureError("unexpected output from flatshot-kwin-grab: " + head.decode(errors="replace").strip())
    width, height, stride, fmt = (int(x) for x in parts[1:])
    image = QImage(width, height, QImage.Format(fmt))
    if image.isNull():
        raise CaptureError(f"unsupported pixel format {fmt}")
    if image.bytesPerLine() == stride:
        return _writable(image), lambda: image
    data = bytearray(stride * height)
    return memoryview(data), lambda: QImage(bytes(data), width, height, stride, QImage.Format(fmt)).copy()


def _read_many(targets: list[tuple[int, memoryview]], timeout: float = 15.0):
    """Fill each buffer from its pipe, all side by side: KWin may write
    them in any order, and a full pipe waits for its reader."""
    left = {fd: [into, 0] for fd, into in targets}
    while left:
        ready, _, _ = select.select(list(left), [], [], timeout)
        if not ready:
            raise CaptureError("flatshot-kwin-grab: no picture came")
        for fd in ready:
            into, got = left[fd]
            n = os.readv(fd, [into[got:]])
            if n == 0:
                raise CaptureError(f"short image from flatshot-kwin-grab ({got} of {len(into)} bytes)")
            got += n
            if got == len(into):
                del left[fd]
            else:
                left[fd][1] = got


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

    def __init__(self, helper: str, version: int = 3):
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
        self.version = version
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

    def grab_screens(self, wanted: list[tuple[str, bool]]) -> list[QImage]:
        """Several screens at once, each at its own scale: ``wanted`` is
        (screen name, with the pointer) for each picture."""
        pipes = [_big_pipe() for _ in wanted]
        started = time.monotonic()
        try:
            request = "screens\n" + "\n".join(f"{int(pointer)} {name}" for name, pointer in wanted)
            with self.lock:
                try:
                    socket.send_fds(self.sock, [request.encode()], [w for _, w in pipes])
                    for i, (r, w) in enumerate(pipes):
                        os.close(w)
                        pipes[i] = (r, -1)
                    head = self.sock.recv(16384)
                except OSError as e:
                    raise _ServerGone(str(e)) from e
            if not head:
                raise _ServerGone("flatshot-kwin-grab --serve stopped")
            lines = head.splitlines()
            if len(lines) != len(wanted):
                raise CaptureError(head.decode(errors="replace").strip() or "no answer for each screen")
            for (name, _), line in zip(wanted, lines):
                if line.startswith(b"ERR "):
                    raise CaptureError(f"{name}: " + line[4:].decode(errors="replace").strip())
            replied = time.monotonic()
            targets = [_target(line) for line in lines]
            _read_many([(r, into) for (r, _), (into, _) in zip(pipes, targets)])
            _note_times(started, replied)
            return [finish() for _, finish in targets]
        finally:
            for r, w in pipes:
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
            _server = _KWinServer(helper, int(version[-1]))
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


def _kwin_screens(wanted: list[tuple[str, bool]]) -> list[QImage]:
    helper = kwin_helper()
    server = _kwin_server(helper) if helper else None
    if server is None or server.version < 4:
        raise CaptureError("each screen on its own needs flatshot-kwin-grab 4 or newer, kept running")
    try:
        return server.grab_screens(wanted)
    except _ServerGone as e:
        _drop_server(server)
        raise CaptureError(str(e)) from e


def _grim_screens(wanted: list[tuple[str, bool]]) -> list[QImage]:
    out: list = [None] * len(wanted)

    def one(i, name, pointer):
        try:
            out[i] = _run_tool(lambda p: ["grim", "-o", name, *(["-c"] if pointer else []), p])
        except Exception as e:  # noqa: BLE001
            out[i] = e

    threads = [threading.Thread(target=one, args=(i, n, c), daemon=True) for i, (n, c) in enumerate(wanted)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    for (name, _), result in zip(wanted, out):
        if not isinstance(result, QImage):
            raise CaptureError(f"{name}: {result or 'no picture'}")
    return out


# Helpers that can take each screen at its own scale (all at once).
SCREENS = {"kwin": ("", _kwin_screens), "grim": ("grim", _grim_screens)}


def compose(pictures: dict) -> QImage:
    """The whole desktop from each screen's own picture (``pictures``: screen
    name -> picture), at the highest of their scales: what's needed where
    a selection or a window crosses monitors, and to look for codes."""
    screens = [s for s in QGuiApplication.screens() if s.name() in pictures]
    virt = virtual_geometry()
    scale = max(pictures[s.name()].width() / max(1, s.geometry().width()) for s in screens)
    out = QImage(round(virt.width() * scale), round(virt.height() * scale), QImage.Format.Format_RGB32)
    out.fill(0)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for s in screens:
        g = s.geometry()
        target = QRectF((g.x() - virt.x()) * scale, (g.y() - virt.y()) * scale, g.width() * scale, g.height() * scale)
        picture = pictures[s.name()]
        if abs(target.width() - picture.width()) < 0.5 and abs(target.height() - picture.height()) < 0.5:
            p.drawImage(target.topLeft(), picture)  # (the same scale: pixel for pixel)
        else:
            p.drawImage(target, picture)
    p.end()
    return out


def _grab_screens(order: list[str], pointer: bool, also_pointer: list | None, screens: dict) -> QImage | None:
    """Each screen at its own scale, where the first helper that can do it
    can: fills ``screens`` (name -> picture; ("pointer", name) -> the same
    with the pointer, with ``also_pointer``) and returns the whole desktop
    made from them. None (and a reason in last_grab) if it can't."""
    global last_grab
    name = next((n for n in order if n in SCREENS), None)
    if name is None or name != order[0]:
        return None
    exe, fn = SCREENS[name]
    if exe and shutil.which(exe) is None:
        return None
    names = [s.name() for s in QGuiApplication.screens()]
    if not names or len(set(names)) != len(names) or not all(names):
        return None
    wanted = [(n, pointer) for n in names] + ([(n, True) for n in names] if also_pointer is not None else [])
    try:
        pictures = fn(wanted)
    except Exception as e:  # noqa: BLE001 — the whole desktop then
        last_grab = f"each screen failed ({e}), so "
        return None
    notes = {_opaque(p) for p in pictures}
    plain = dict(zip(names, pictures[:len(names)]))
    screens.update(plain)
    image = compose(plain)
    if also_pointer is not None:
        with_pointer = dict(zip(names, pictures[len(names):]))
        screens.update({("pointer", n): p for n, p in with_pointer.items()})
        also_pointer.append(compose(with_pointer))
    what = ", each screen at its own scale" + (", with and without the pointer" if also_pointer is not None else
                                               ", with the pointer" if pointer else "")
    last_grab = _describe(name, what) + "".join(sorted(notes))
    return image


def grab_desktop(preferred: str = "auto", pointer: bool = False, also_pointer: list | None = None,
                 screens: dict | None = None) -> QImage:
    """Every screen as one image; ``pointer`` draws the mouse pointer in
    (where the helper supports it). With ``also_pointer`` (a list), the
    same moment with the pointer drawn in is appended to it too, where the
    helper can take both at once. With ``screens`` (a dict), each screen is
    taken on its own, at its own scale, where the helper can (see
    _grab_screens); else it's left empty."""
    global last_grab
    last_grab = ""
    order = backend_order() if preferred in ("", "auto") else [preferred]
    if screens is not None:
        image = _grab_screens(order, pointer, also_pointer, screens)
        if image is not None:
            return image
    failed, last_grab = last_grab, ""
    errors = [failed.removesuffix(", so ")] if failed else []
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
