"""User settings, read from ``$XDG_CONFIG_HOME/flatshot/config.json``.

Every key is optional; missing keys fall back to the defaults below.
"""

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from flatshot.qt import QStandardPaths


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "flatshot" / "config.json"


def _default_dir() -> str:
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return str(Path(pictures or Path.home() / "Pictures") / "Screenshots")


@dataclass
class Config:
    save_dir: str = ""
    filename: str = "Screenshot_%Y-%m-%d_%H-%M-%S.png"
    save_to_disk: bool = True
    copy_to_clipboard: bool = True
    notify: bool = True
    scan_codes: bool = True
    backend: str = "auto"  # auto | spectacle | grim | gnome-screenshot | qt
    default_tool: str = "region"
    default_color: int = 0
    default_size: int = 1

    def __post_init__(self):
        self.save_dir = os.path.expanduser(self.save_dir) if self.save_dir else _default_dir()


def load() -> Config:
    path = config_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            print(f"flatshot: ignoring {path}: {e}", file=sys.stderr)
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})


def dump(cfg: Config) -> str:
    return json.dumps(asdict(cfg), indent=2)
