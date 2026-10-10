"""User settings, read from ``$XDG_CONFIG_HOME/flatshot/config.json``.

Every key is optional; missing keys fall back to the defaults below.
"""

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
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


def _default_record_dir() -> str:
    videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    return str(Path(videos or Path.home() / "Videos") / "Screencasts")


FORMAT_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "avif", "jxl")
CLIPBOARD = ("image", "path", "none")
LOUPE_SIZES = (80, 320)  # smallest, largest
OPEN_AFTER = ("none", "image", "folder")
BACKENDS = ("auto", "kwin", "spectacle", "grim", "gnome-screenshot", "qt")
RECORD_FORMATS = ("mp4", "webm", "gif")
RECORD_FPS = (24, 30, 60)
REGION_POINTER = ("toggle", "hidden", "shown")
SCREENSHOT_METHODS = ("desktop", "screens")
TRAY_ICONS = ("color", "theme", "white", "black", "auto")  # (icons.TRAY_STYLES)


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
    sound: bool = False  # play a sound after a capture
    sound_file: str = ""  # empty: the desktop's screenshot sound
    theme: str = "ember"  # see theme.THEMES
    tray_icon: str = "color"  # color | theme | white | black | auto (white or black, like the desktop)
    # Capture
    dim_opacity: int = 60  # screen shading in %, 0 turns it off
    show_loupe: bool = True  # magnifier with coordinates and colour
    loupe_size: int = 120  # magnifier size in logical px
    show_crosshair: bool = True
    show_hint: bool = True  # what to do next, in the middle of the screen while capturing
    rainbow: bool = False  # the crosshair and the magnifier's square cycle through colours
    snap_edges: bool = False  # selections snap to edges in the picture (toolbar toggle, G)
    snap_distance: int = 10  # how close (logical px) the pointer must be to an edge
    snap_sensitivity: int = 5  # 1-10: higher snaps to fainter and shorter edges
    span_monitors: bool = True  # a selection or a clicked window may cross monitors
    detect_windows: bool = True  # hover + click a window to capture it (KDE)
    scan_codes: bool = True
    show_codes: bool = True
    toolbar_follows_mouse: bool = True
    backend: str = "auto"  # see BACKENDS
    # "desktop": one picture of the whole desktop, cut up per screen; "screens":
    # each screen on its own, at its own scale (KWin, grim), all at once.
    screenshot_method: str = "desktop"
    include_pointer: bool = False  # draw the mouse pointer into instant (no-UI) captures
    # The active window (instant capture) on KDE is KWin's picture of it: with
    # its title bar and borders, and its shadow (transparent around it).
    window_frame: bool = True
    window_shadow: bool = True
    # The pointer when picking a region: "hidden" or "shown" (one
    # screenshot), or "toggle" (hidden at first, the toolbar's button shows
    # it; two screenshots are taken at once).
    region_pointer: str = "hidden"
    default_tool: str = "region"
    default_color: int = 0  # 0-6 the toolbar's colours, 7 your own (custom_color)
    custom_color: str = "#FF4FA3"  # your own colour, the toolbar's eighth: #RRGGBB
    keys: dict = field(default_factory=dict)  # keys in Flatshot's windows you changed: action -> key (keys.py)
    default_size: int = 1
    # Screen recording
    record_dir: str = ""
    record_filename: str = "Recording_%Y-%m-%d_%H-%M-%S"  # same codes as `filename`
    record_format: str = "mp4"  # mp4 | webm | gif
    record_fps: int = 30  # 24 | 30 | 60
    record_mic: bool = False
    record_system_audio: bool = False
    record_cursor: bool = True
    record_countdown: int = 3  # seconds before recording starts, 0 for none
    # Pre-0.2 key, still honoured: false means clipboard = "none".
    copy_to_clipboard: bool | None = None

    def __post_init__(self):
        self.save_dir = os.path.expanduser(self.save_dir) if self.save_dir else _default_dir()
        self.record_dir = os.path.expanduser(self.record_dir) if self.record_dir else _default_record_dir()
        if self.record_format not in RECORD_FORMATS:
            self.record_format = "mp4"
        if self.record_fps not in RECORD_FPS:
            self.record_fps = 30
        self.record_countdown = max(0, min(int(self.record_countdown), 10))
        self.snap_distance = max(2, min(int(self.snap_distance), 40))
        self.snap_sensitivity = max(1, min(int(self.snap_sensitivity), 10))
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(self.custom_color)):
            self.custom_color = "#FF4FA3"
        self.custom_color = self.custom_color.upper()
        if not isinstance(self.keys, dict):
            self.keys = {}
        self.keys = {str(k): v for k, v in self.keys.items() if isinstance(v, str)}
        if self.copy_to_clipboard is False:
            self.clipboard = "none"
        self.copy_to_clipboard = None
        if self.clipboard not in CLIPBOARD:
            self.clipboard = "image"
        if self.open_after not in OPEN_AFTER:
            self.open_after = "none"
        if self.region_pointer not in REGION_POINTER:
            self.region_pointer = "hidden"
        if self.screenshot_method not in SCREENSHOT_METHODS:
            self.screenshot_method = "desktop"
        if self.tray_icon not in TRAY_ICONS:
            self.tray_icon = "color"
        if self.backend not in BACKENDS:
            self.backend = "auto"
        self.dim_opacity = max(0, min(int(self.dim_opacity), 90))
        self.quality = max(1, min(int(self.quality), 100))
        self.loupe_size = max(LOUPE_SIZES[0], min(int(self.loupe_size), LOUPE_SIZES[1]))
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
