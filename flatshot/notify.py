"""Desktop notifications (org.freedesktop.Notifications) with action buttons.

Actions only work while the process stays alive to hear the click, i.e. in
the tray app. Without jeepney or a session bus, falls back to notify-send.

Each notification is one of the events in assets/flatshot.notifyrc, which
KDE's System Settings > Notifications > Flatshot lists, to choose per
event whether it pops up, plays a sound or runs a command. KDE leaves that
to the app (KNotification does it in C++ apps), so send() reads the
choices (~/.config/flatshot.notifyrc) and does what they say.
"""

import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path

from flatshot import dbus
from flatshot.qt import QObject
from flatshot.theme import ICON_PATH as ICON

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
NOTIFYRC = Path(__file__).with_name("assets") / "flatshot.notifyrc"
SOUND_EXTENSIONS = (".oga", ".ogg", ".wav", ".flac", ".mp3", ".opus")


def _data_dirs() -> list[Path]:
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    rest = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(home)] + [Path(d) for d in rest.split(":") if d]


def install_events() -> None:
    """Make Flatshot's events known to KDE's notification settings: the
    packages install flatshot.notifyrc; anywhere else (pip, the AppImage) a
    copy goes in ~/.local/share."""
    text = NOTIFYRC.read_text()
    for version in ("knotifications6", "knotifications5"):
        if any((d / version / "flatshot.notifyrc").is_file() and _read(d / version / "flatshot.notifyrc") == text
               for d in _data_dirs()):
            continue
        target = _data_dirs()[0] / version / "flatshot.notifyrc"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        except OSError as e:
            print(f"flatshot: could not install the notification events: {e}", file=sys.stderr)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _ini(text: str) -> configparser.ConfigParser:
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str  # (keys keep their case)
    try:
        ini.read_string(text)
    except configparser.Error:
        pass
    return ini


def event_settings(event: str) -> dict:
    """What KDE's settings say ``event`` does: {"actions": {"Popup", ...},
    "sound": ..., "command": ...}. Flatshot's defaults, with the user's
    changes (~/.config/flatshot.notifyrc) over them."""
    section = f"Event/{event}"
    values = {}
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    for ini in (_ini(NOTIFYRC.read_text()), _ini(_read(Path(config_home) / "flatshot.notifyrc"))):
        if ini.has_section(section):
            values.update(ini[section])
    actions = {a.strip() for a in values.get("Action", "Popup").split("|") if a.strip() and a.strip() != "None"}
    return {"actions": actions, "sound": values.get("Sound", ""), "command": values.get("Execute", "")}


def _sound_file(name: str) -> str:
    """A sound as KDE's settings name it: a file, a file:// URL, or a name
    in the sound themes (/usr/share/sounds)."""
    if name.startswith("file://"):
        name = name[len("file://"):]
    if os.path.isabs(name):
        return name
    for d in _data_dirs():
        root = d / "sounds"
        if not root.is_dir():
            continue
        for found in root.rglob(name if Path(name).suffix else f"{name}.*"):
            if found.suffix.lower() in SOUND_EXTENSIONS:
                return str(found)
    return ""


class Notifier(QObject):
    def __init__(self, interactive: bool):
        super().__init__()
        self.interactive = interactive
        self._handlers: dict[int, callable] = {}
        self._listener = None
        if interactive and dbus.available():
            self._listener = dbus.SignalListener([
                {"interface": SERVICE, "member": "ActionInvoked", "path": PATH},
                {"interface": SERVICE, "member": "NotificationClosed", "path": PATH},
            ])
            self._listener.received.connect(self._on_signal)
            if not self._listener.start():
                self._listener = None

    def send(self, summary: str, body: str = "", image: Path | None = None,
             actions: dict[str, str] | None = None, on_action=None, urgent: bool = False,
             file: Path | None = None, event: str = "notice") -> bool:
        """``actions`` maps key -> button label; "default" is a click on the bubble.
        ``file`` is what Plasma offers to drag out (default: ``image``).
        ``event`` is one of flatshot.notifyrc's: what KDE's settings say it
        does (pop up, play a sound, run a command) is done. Returns whether
        it popped up."""
        chosen = event_settings(event)
        if "Sound" in chosen["actions"] and chosen["sound"]:
            from flatshot import sound

            sound.play(_sound_file(chosen["sound"]))
        if "Execute" in chosen["actions"] and chosen["command"].strip():
            try:
                subprocess.Popen(chosen["command"], shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                pass
        if "Popup" not in chosen["actions"]:
            return False
        actions = actions if (actions and self._listener) else {}
        hints = {"desktop-entry": ("s", "flatshot"), "x-kde-eventId": ("s", event)}
        if urgent:
            hints["urgency"] = ("y", 2)
        if image:
            hints["image-path"] = ("s", image.resolve().as_uri())
        if file or image:
            hints["x-kde-urls"] = ("as", [(file or image).resolve().as_uri()])  # Plasma: thumbnail + drag-and-drop
        flat = [item for pair in actions.items() for item in pair]
        try:
            nid = dbus.call(SERVICE, PATH, SERVICE, "Notify", "susssasa{sv}i",
                            "Flatshot", 0, ICON, summary, body, flat, hints, -1)[0]
        except dbus.DBusError:
            self._notify_send(summary, body, image, urgent)
            return True
        if on_action and actions:
            self._handlers[nid] = on_action
        return True

    def _on_signal(self, iface, member, body):
        if member == "ActionInvoked" and len(body) == 2:
            handler = self._handlers.pop(body[0], None)
            if handler:
                handler(body[1])
        elif member == "NotificationClosed" and body:
            self._handlers.pop(body[0], None)

    @staticmethod
    def _notify_send(summary, body, image, urgent):
        if shutil.which("notify-send") is None:
            return
        argv = ["notify-send", "-a", "Flatshot", "-i", ICON]
        if urgent:
            argv += ["-u", "critical"]
        if image:
            argv += ["-h", f"string:image-path:{image.resolve().as_uri()}"]
        try:
            subprocess.Popen(argv + [summary, body], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
