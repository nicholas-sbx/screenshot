"""The long-running tray app: system tray icon, KDE global shortcuts,
notification actions, and the target of `flatshot` invocations."""

import sys
from pathlib import Path

from flatshot import config, ipc, output
from flatshot.notify import Notifier
from flatshot.qt import QAction, QIcon, QMenu, QObject, QSystemTrayIcon, QTimer
from flatshot.session import Request, Session
from flatshot.shortcuts import ACTIONS, GlobalShortcuts
from flatshot.theme import ICON_PATH

# Let a menu or notification popup fade out before the screen is frozen.
MENU_DELAY_MS = 220


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
        self.shortcuts.triggered.connect(lambda action: self.capture(full=action == "screen"))
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
        self.menu_actions["region"] = menu.addAction("Capture region")
        self.menu_actions["region"].triggered.connect(lambda: self.capture(delay_ms=MENU_DELAY_MS))
        self.menu_actions["screen"] = menu.addAction("Capture all screens")
        self.menu_actions["screen"].triggered.connect(lambda: self.capture(full=True, delay_ms=MENU_DELAY_MS))
        menu.addAction("Capture region in 3 seconds").triggered.connect(lambda: self.capture(delay_ms=3000))
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
            self.capture(full=True, delay_ms=MENU_DELAY_MS)

    def _open_folder(self):
        folder = Path(config.load().save_dir)
        folder.mkdir(parents=True, exist_ok=True)
        output.open_file(folder)

    # -- commands ------------------------------------------------------------

    def handle(self, command: str):
        name, *rest = command.split()
        delay = float(rest[0]) if rest else 0
        if name == "capture":
            self.capture(delay_ms=int(delay * 1000))
        elif name == "full":
            self.capture(full=True, delay_ms=int(delay * 1000))
        elif name == "settings":
            self.show_settings()
        elif name == "quit":
            self.quit()

    def capture(self, full=False, image: str | None = None, delay_ms: int = 0):
        if self.session is not None:
            return  # one capture at a time
        cfg = config.load()  # pick up settings changes
        self.session = Session(cfg, Request(full=full, image=image), self.notifier, self._finished, self._on_action)
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
