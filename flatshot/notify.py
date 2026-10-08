"""Desktop notifications (org.freedesktop.Notifications) with action buttons.

Actions only work while the process stays alive to hear the click, i.e. in
the tray app. Without jeepney or a session bus, falls back to notify-send.
"""

import shutil
import subprocess
from pathlib import Path

from flatshot import dbus
from flatshot.qt import QObject
from flatshot.theme import ICON_PATH as ICON

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


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
             file: Path | None = None) -> None:
        """``actions`` maps key -> button label; "default" is a click on the bubble.
        ``file`` is what Plasma offers to drag out (default: ``image``)."""
        actions = actions if (actions and self._listener) else {}
        hints = {"desktop-entry": ("s", "flatshot")}
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
            return
        if on_action and actions:
            self._handlers[nid] = on_action

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
