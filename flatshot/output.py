"""Where a finished capture goes: disk, clipboard, notification."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flatshot.qt import QBuffer, QByteArray, QGuiApplication, QImage, QIODevice, QMimeData

from flatshot.config import Config


@dataclass
class Delivery:
    path: Path | None = None
    copied: bool = False
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


def save(image: QImage, cfg: Config, explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
    else:
        path = _unique(Path(cfg.save_dir) / datetime.now().strftime(cfg.filename))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path)):
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


def notify(title: str, body: str, icon: str = "camera-photo") -> None:
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.Popen(["notify-send", "-a", "Flatshot", "-i", icon, title, body],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def deliver(image: QImage, cfg: Config, explicit: str | None = None) -> Delivery:
    result = Delivery()
    if explicit or cfg.save_to_disk:
        result.path = save(image, cfg, explicit)
    if cfg.copy_to_clipboard:
        result.copied, result.holds_clipboard = copy_image(image)
    if cfg.notify:
        size = f"{image.width()} × {image.height()}"
        if result.path:
            notify("Screenshot saved", f"{result.path.name} · {size}" + (" · copied" if result.copied else ""),
                   str(result.path))
        elif result.copied:
            notify("Screenshot copied", size)
    return result
