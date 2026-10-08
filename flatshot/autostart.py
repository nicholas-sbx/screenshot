"""Start-at-login via an XDG autostart entry."""

import os
import shlex
import shutil
import sys
from pathlib import Path


def entry_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "flatshot.desktop"


def launcher() -> str:
    """How to start this same Flatshot again (AppImage, package or venv)."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return shlex.quote(appimage)
    found = shutil.which("flatshot")
    if found:
        return shlex.quote(found)
    return f"{shlex.quote(sys.executable)} -m flatshot"


def enabled() -> bool:
    return entry_path().exists()


def set_enabled(on: bool) -> None:
    path = entry_path()
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Flatshot\n"
        "Comment=Screenshot tool in the system tray\n"
        f"Exec={launcher()} --tray\n"
        "Icon=flatshot\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-phase=2\n"
    )
