"""Where a finished capture goes: disk, clipboard, notification."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flatshot.qt import QBuffer, QByteArray, QGuiApplication, QImage, QImageWriter, QIODevice, QMimeData

from flatshot.config import Config


@dataclass
class Delivery:
    path: Path | None = None
    saved: bool = False  # path is in the screenshot folder (not a temp file)
    copied: bool = False
    size: tuple[int, int] = (0, 0)
    # True when the clipboard is held by this process and it must stay alive.
    holds_clipboard: bool = False


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return data.data()


def _unique(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return candidate


# (config value, label, Qt writer name, lossy)
FORMATS = [
    ("png", "PNG", "png", False),
    ("jpg", "JPEG", "jpeg", True),
    ("webp", "WebP", "webp", True),
    ("avif", "AVIF", "avif", True),
    ("jxl", "JPEG XL", "jxl", True),
]


def writable_formats() -> list[tuple[str, str, str, bool]]:
    """The FORMATS this Qt can actually write (WebP/AVIF/JXL need plugins)."""
    have = {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}
    return [f for f in FORMATS if f[2] in have]


def _format(key: str) -> tuple[str, str, str, bool]:
    for f in writable_formats():
        if f[0] == key:
            return f
    if key != "png":
        print(f"flatshot: can't write {key} here (missing Qt image plugin?), saving PNG", file=sys.stderr)
    return FORMATS[0]


def save(image: QImage, cfg: Config, explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        ext = path.suffix.lower().lstrip(".")
        fmt = _format("jpg" if ext == "jpeg" else ext) if ext else FORMATS[0]
    else:
        fmt = _format(cfg.format)
        path = _unique(Path(cfg.save_dir) / f"{datetime.now().strftime(cfg.filename)}.{fmt[0]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    quality = cfg.quality if fmt[3] else -1
    if not image.save(str(path), fmt[2], quality):
        raise OSError(f"could not write {path}")
    return path


def _pipe_to(argv: list[str], data: bytes) -> bool:
    """Feed a clipboard helper that forks to keep serving the selection."""
    if shutil.which(argv[0]) is None:
        return False
    try:
        # The helpers fork into the background; don't keep pipes they inherit.
        subprocess.run(argv, input=data, check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _clipboard_helpers(mime: str) -> list[list[str]]:
    if os.environ.get("WAYLAND_DISPLAY"):
        return [["wl-copy", "--type", mime]]
    if os.environ.get("DISPLAY"):
        return [["xclip", "-selection", "clipboard", "-t", mime, "-i"]]
    return []


def copy_image(image: QImage) -> tuple[bool, bool]:
    """Returns (copied, holds_clipboard)."""
    data = png_bytes(image)
    for argv in _clipboard_helpers("image/png"):
        if _pipe_to(argv, data):
            return True, False
    mime = QMimeData()
    mime.setImageData(image)
    mime.setData("image/png", QByteArray(data))
    QGuiApplication.clipboard().setMimeData(mime)
    return True, True


def copy_text(text: str) -> tuple[bool, bool]:
    for argv in _clipboard_helpers("text/plain;charset=utf-8"):
        if _pipe_to(argv, text.encode()):
            return True, False
    QGuiApplication.clipboard().setText(text)
    return True, True


def _spawn(argv: list[str], **kw) -> bool:
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, **kw)
        return True
    except OSError:
        return False


def open_file(path: Path) -> bool:
    return _spawn(["xdg-open", str(path)])


def show_in_folder(path: Path) -> bool:
    """Select the file in the file manager (Dolphin, Nautilus, ...)."""
    from flatshot import dbus

    try:
        dbus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1",
                  "ShowItems", "ass", [path.resolve().as_uri()], "")
        return True
    except dbus.DBusError:
        return _spawn(["xdg-open", str(path.parent)])


def run_command(template: str, path: Path) -> bool:
    """Run the user's after-capture command; {path} becomes the quoted file path."""
    command = template.replace("{path}", shlex.quote(str(path)))
    return _spawn(["/bin/sh", "-c", command], env={**os.environ, "FLATSHOT_PATH": str(path)})


def _temp_path() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "flatshot"
    base.mkdir(parents=True, exist_ok=True)
    return base / datetime.now().strftime("Screenshot_%Y-%m-%d_%H-%M-%S.png")


def deliver(image: QImage, cfg: Config, explicit: str | None = None) -> Delivery:
    """Run the configured after-capture steps (except notifying)."""
    result = Delivery(size=(image.width(), image.height()))
    if explicit or cfg.save_to_disk:
        result.path = save(image, cfg, explicit)
        result.saved = True
    elif cfg.needs_file():
        # Clipboard-path, open or command need a file even when not keeping one.
        result.path = _unique(_temp_path())
        if not image.save(str(result.path)):
            raise OSError(f"could not write {result.path}")
    if cfg.clipboard == "image":
        result.copied, result.holds_clipboard = copy_image(image)
    elif cfg.clipboard == "path" and result.path:
        result.copied, result.holds_clipboard = copy_text(str(result.path))
    if result.path:
        if cfg.open_after == "image":
            open_file(result.path)
        elif cfg.open_after == "folder":
            show_in_folder(result.path)
        if cfg.run_command.strip():
            run_command(cfg.run_command, result.path)
    return result
