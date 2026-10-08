"""User settings, read from ``$XDG_CONFIG_HOME/flatshot/config.json``.

Every key is optional; missing keys fall back to the defaults below.
"""

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from flatshot.qt import QStandardPaths


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "flatshot" / "config.json"


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "flatshot" / "state.json"


def _default_dir() -> str:
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return str(Path(pictures or Path.home() / "Pictures") / "Screenshots")


FORMAT_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "avif", "jxl")
CLIPBOARD = ("image", "path", "none")
OPEN_AFTER = ("none", "image", "folder")
BACKENDS = ("auto", "kwin", "spectacle", "grim", "gnome-screenshot", "qt")


@dataclass
class Config:
    # After capture
    save_to_disk: bool = True
    save_dir: str = ""
    filename: str = "Screenshot_%Y-%m-%d_%H-%M-%S"  # strftime pattern; the extension follows `format`
    format: str = "png"  # png | jpg | webp | avif | jxl (when Qt can write it)
    quality: int = 90  # 1-100, for lossy formats
    clipboard: str = "image"  # image | path | none
    notify: bool = True
    open_after: str = "none"  # none | image | folder
    run_command: str = ""  # shell command, {path} is replaced by the quoted file path
    theme: str = "ember"  # see theme.THEMES
    # Capture
    dim_opacity: int = 60  # screen shading in %, 0 turns it off
    detect_windows: bool = True  # hover + click a window to capture it (KDE)
    scan_codes: bool = True
    show_codes: bool = True
    toolbar_follows_mouse: bool = True
    backend: str = "auto"  # see BACKENDS
    include_pointer: bool = False  # draw the mouse pointer into instant (no-UI) captures
    default_tool: str = "region"
    default_color: int = 0
    default_size: int = 1
    # Pre-0.2 key, still honoured: false means clipboard = "none".
    copy_to_clipboard: bool | None = None

    def __post_init__(self):
        self.save_dir = os.path.expanduser(self.save_dir) if self.save_dir else _default_dir()
        if self.copy_to_clipboard is False:
            self.clipboard = "none"
        self.copy_to_clipboard = None
        if self.clipboard not in CLIPBOARD:
            self.clipboard = "image"
        if self.open_after not in OPEN_AFTER:
            self.open_after = "none"
        if self.backend not in BACKENDS:
            self.backend = "auto"
        self.dim_opacity = max(0, min(int(self.dim_opacity), 90))
        self.quality = max(1, min(int(self.quality), 100))
        self.format = str(self.format).lower().lstrip(".")
        if self.format == "jpeg":
            self.format = "jpg"
        # Older configs kept the extension in the pattern; it now follows `format`.
        stem, dot, ext = self.filename.rpartition(".")
        if dot and stem and ext.lower() in FORMAT_EXTENSIONS:
            self.filename = stem

    def needs_file(self) -> bool:
        return self.save_to_disk or self.clipboard == "path" or self.open_after != "none" or bool(self.run_command)

    def save(self) -> None:
        _write_json(config_path(), {k: v for k, v in asdict(self).items() if v is not None})


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        print(f"flatshot: ignoring {path}: {e}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def load_state() -> dict:
    """Small bits Flatshot remembers between runs (last region, counter)."""
    return _read_json(state_path())


def update_state(**changes) -> None:
    try:
        _write_json(state_path(), {**load_state(), **changes})
    except OSError as e:
        print(f"flatshot: could not save {state_path()}: {e}", file=sys.stderr)


def load() -> Config:
    data = _read_json(config_path())
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})


def dump(cfg: Config) -> str:
    return json.dumps({k: v for k, v in asdict(cfg).items() if v is not None}, indent=2)
