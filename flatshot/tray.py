"""The long-running tray app: system tray icon, KDE global shortcuts,
notification actions, and the target of `flatshot` invocations."""

import sys
from pathlib import Path

from flatshot import config, ipc, output, pin
from flatshot.notify import Notifier
from flatshot.qt import QAction, QIcon, QImage, QMenu, QObject, QSystemTrayIcon, QTimer
from flatshot.capture import parse_geometry
from flatshot.session import MODES, Request, Session
from flatshot.shortcuts import ACTIONS, GlobalShortcuts
from flatshot.theme import ICON_PATH

# Let a menu or notification popup fade out before the screen is frozen.
MENU_DELAY_MS = 220

# global shortcut / menu action -> (capture mode, pin)
SHORTCUT_MODES = {
    "region": ("region", False),
    "window": ("window", False),
    "monitor": ("monitor", False),
    "screen": ("screens", False),
    "last": ("last", False),
    "pin": ("region", True),
}
# Commands from older `flatshot` invocations.
LEGACY_COMMANDS = {"capture": "region", "full": "screens"}


class TrayApp(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.notifier = Notifier(interactive=True)
        self.shortcuts = GlobalShortcuts()
        self.server = ipc.Server()
        self.session: Session | None = None
        self.settings = None
        self.tray: QSystemTrayIcon | None = None
        self.menu_actions: dict[str, QAction] = {}

    def start(self) -> bool:
        if not self.server.listen():
            print("flatshot: already running", file=sys.stderr)
            return False
        self.server.command.connect(self.handle)
        self.shortcuts.triggered.connect(self._shortcut)
        self.shortcuts.changed.connect(self._update_menu)
        if not self.shortcuts.start():
            print(f"flatshot: global shortcuts unavailable ({self.shortcuts.error})", file=sys.stderr)
        self._build_tray()
        if self.tray is None:
            print("flatshot: no system tray found; running in the background anyway", file=sys.stderr)
        return True

    # -- tray ----------------------------------------------------------------

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(ICON_PATH), self)
        self.tray.setToolTip("Flatshot")
        menu = QMenu()
        # Active window makes no sense straight from the menu (the menu has focus).
        for action in ("region", "monitor", "screen", "last", "pin"):
            self.menu_actions[action] = menu.addAction(ACTIONS[action][0])
            self.menu_actions[action].triggered.connect(
                lambda _=False, a=action: self._shortcut(a, delay_ms=MENU_DELAY_MS))
        menu.addSeparator()
        menu.addAction("Capture region in 3 seconds").triggered.connect(lambda: self.capture(delay_ms=3000))
        menu.addAction("Capture active window in 3 seconds").triggered.connect(
            lambda: self.capture("window", delay_ms=3000))
        menu.addSeparator()
        menu.addAction("Open screenshots folder").triggered.connect(self._open_folder)
        menu.addAction("Settings…").triggered.connect(self.show_settings)
        menu.addSeparator()
        menu.addAction("Quit Flatshot").triggered.connect(self.quit)
        self._menu = menu  # QSystemTrayIcon doesn't take ownership
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self._update_menu()
        self.tray.show()

    def _update_menu(self):
        if not self.shortcuts.active:
            return
        for action, item in self.menu_actions.items():
            keys = self.shortcuts.get(action)
            label = ACTIONS[action][0]
            item.setText(f"{label}\t{keys}" if keys else label)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.capture(delay_ms=MENU_DELAY_MS)
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.capture("screens", delay_ms=MENU_DELAY_MS)

    def _open_folder(self):
        folder = output.base_folder(config.load())
        folder.mkdir(parents=True, exist_ok=True)
        output.open_file(folder)

    # -- commands ------------------------------------------------------------

    def _shortcut(self, action: str, delay_ms: int = 0):
        mode, pinned = SHORTCUT_MODES.get(action, ("region", False))
        self.capture(mode, pinned=pinned, delay_ms=delay_ms)

    def handle(self, command: str):
        """``<mode>[:WxH+X+Y] [delay] [pin]``, ``settings`` or ``quit``."""
        name, *rest = command.split() or [""]
        if name == "settings":
            self.show_settings()
        elif name == "quit":
            self.quit()
        else:
            mode, _, geometry = LEGACY_COMMANDS.get(name, name).partition(":")
            if mode not in MODES:
                return
            try:
                delay = float(rest[0]) if rest else 0
            except ValueError:
                delay = 0
            self.capture(mode, rect=parse_geometry(geometry), pinned="pin" in rest,
                         delay_ms=int(max(0, delay) * 1000))

    def capture(self, mode="region", image: str | None = None, rect=None, pinned=False, delay_ms: int = 0):
        if self.session is not None:
            return  # one capture at a time
        cfg = config.load()  # pick up settings changes
        request = Request(mode=mode, rect=rect, pin=pinned, image=image)
        self.session = Session(cfg, request, self.notifier, self._finished, self._on_action)
        QTimer.singleShot(delay_ms, self.session.start)

    def _finished(self, code, holds_clipboard):
        self.session = None

    def _on_action(self, key: str, path: Path):
        if key in ("default", "open"):
            output.open_file(path)
        elif key == "folder":
            output.show_in_folder(path)
        elif key == "annotate" and path.exists():
            self.capture(image=str(path), delay_ms=MENU_DELAY_MS)
        elif key == "pin":
            image = QImage(str(path))
            if not image.isNull():
                pin.show(image)

    def show_settings(self):
        from flatshot.settings import SettingsWindow

        if self.settings is None:
            self.settings = SettingsWindow(config.load(), self.shortcuts)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def quit(self):
        if self.tray:
            self.tray.hide()
        self.app.quit()
