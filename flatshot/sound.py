"""A short sound after a capture, played by whatever audio player the
desktop already has. Fire-and-forget: it never delays the capture, and
nothing is passed through a shell."""

import os
import shutil
from pathlib import Path

from flatshot.output import _spawn

# The desktop's own screenshot sound, if a sound theme provides one.
THEME_SOUNDS = [
    "/usr/share/sounds/ocean/stereo/screen-capture.oga",  # KDE Plasma 6
    "/usr/share/sounds/freedesktop/stereo/screen-capture.oga",
    "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga",
]
# Players that take a file argument and exit when done.
PLAYERS = [
    ["pw-play"],  # PipeWire
    ["paplay"],  # PulseAudio (and pipewire-pulse)
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
]


def default_sound() -> str | None:
    return next((p for p in THEME_SOUNDS if os.path.isfile(p)), None)


def play(path: str = "") -> bool:
    """Play ``path``, or the desktop's screenshot sound when it's empty.
    False when there's no file or no player to play it with."""
    if not path and not default_sound() and shutil.which("canberra-gtk-play"):
        # No theme file we know of: let libcanberra find the theme's sound.
        return _spawn(["canberra-gtk-play", "-i", "screen-capture", "-d", "Flatshot"])
    target = Path(os.path.expanduser(path)) if path else Path(default_sound() or "")
    if not target.is_file():
        return False
    target = target.resolve()  # absolute, so it can't be read as a player option
    players = PLAYERS + ([["aplay", "-q"]] if target.suffix.lower() == ".wav" else [])
    for argv in players:
        if shutil.which(argv[0]):
            return _spawn(argv + [str(target)])
    return False
