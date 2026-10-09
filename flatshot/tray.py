"""The long-running tray app: system tray icon, KDE global shortcuts,
notification actions, and the target of `flatshot` invocations."""

import sys
from pathlib import Path

from flatshot import config, ipc, output, pin, recording, theme
from flatshot.notify import Notifier
from flatshot.qt import (
    QAction, QIcon, QImage, QMenu, QObject, QPainter, QPixmap, QRectF, QSystemTrayIcon, Qt, QTimer,
)
from flatshot.capture import parse_geometry
from flatshot.session import MODES, Request, Session
from flatshot.shortcuts import ACTIONS, GlobalShortcuts
from flatshot.theme import C, ICON_PATH, REC

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


def _stop_icon() -> QIcon:
    """The tray icon while recording: the bar's red stop button."""
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(REC)
        p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
        s = size * 0.32
        p.setBrush(C.TEXT)
        p.drawRoundedRect(QRectF((size - s) / 2, (size - s) / 2, s, s), size / 16, size / 16)
        p.end()
        icon.addPixmap(pm)
    return icon


class TrayApp(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.notifier = Notifier(interactive=True)
        self.shortcuts = GlobalShortcuts()
        from flatshot import diagnose

        self.server = ipc.Server(status=diagnose.info)
        self.session: Session | None = None
        self.recording: recording.Recording | None = None
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._update_tooltip)
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
        from flatshot import capture

        capture.warm_up(config.load().backend)  # (the KWin helper, ready for the first capture)
        return True

    # -- tray ----------------------------------------------------------------

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(ICON_PATH), self)
        self.tray.setToolTip("Flatshot")
        menu = QMenu()
        # Active window makes no sense straight from the menu (the menu has focus).
        for action in ("region", "monitor", "screen", "last", "pin", "record"):
            self.menu_actions[action] = menu.addAction(ACTIONS[action][0])
            self.menu_actions[action].triggered.connect(
                lambda _=False, a=action: self._shortcut(a, delay_ms=MENU_DELAY_MS))
        menu.addSeparator()
        menu.addAction("Capture region in 3 seconds").triggered.connect(lambda: self.capture(delay_ms=3000))
        menu.addAction("Capture active window in 3 seconds").triggered.connect(
            lambda: self.capture("window", delay_ms=3000))
        menu.addSeparator()
        menu.addAction("Annotate an image…").triggered.connect(self._choose_image)
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
        for action, item in self.menu_actions.items():
            keys = self.shortcuts.get(action) if self.shortcuts.active else ""
            label = ACTIONS[action][0]
            if action == "record" and self.recording is not None:
                label = "Stop recording"
            item.setText(f"{label}\t{keys}" if keys else label)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.recording is not None:
                self.recording.stop()
            else:
                self.capture(delay_ms=MENU_DELAY_MS)
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.capture("screens", delay_ms=MENU_DELAY_MS)

    def _open_folder(self):
        folder = output.base_folder(config.load())
        folder.mkdir(parents=True, exist_ok=True)
        output.open_file(folder)

    # -- commands ------------------------------------------------------------

    def _shortcut(self, action: str, delay_ms: int = 0):
        if action == "record":
            self.record(delay_ms)
            return
        mode, pinned = SHORTCUT_MODES.get(action, ("region", False))
        self.capture(mode, pinned=pinned, delay_ms=delay_ms)

    def handle(self, command: str):
        """``<mode>[:WxH+X+Y] [delay] [pin]``, ``edit <file>``, ``settings``
        or ``quit``."""
        name, *rest = command.split() or [""]
        if name == "edit":
            self.edit(command.partition(" ")[2])
        elif name == "settings":
            self.show_settings()
        elif name == "quit":
            self.quit()
        elif name == "record":
            try:
                delay = float(rest[0]) if rest else 0
            except ValueError:
                delay = 0
            self.record(int(max(0, delay) * 1000))
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

    def capture(self, mode="region", image: str | None = None, rect=None, pinned=False, delay_ms: int = 0,
                record=False):
        if self.session is not None:
            return  # one capture at a time
        cfg = config.load()  # pick up settings changes
        theme.use(cfg.theme)
        request = Request(mode=mode, rect=rect, pin=pinned, image=image, record=record)
        self.session = Session(cfg, request, self.notifier, self._finished, self._on_action,
                               on_recording=None if self.recording else self._recording_started)
        QTimer.singleShot(delay_ms, self.session.start)

    def record(self, delay_ms: int = 0):
        """Choose an area to record, or stop the recording in progress."""
        if self.recording is not None:
            self.recording.stop()
        else:
            self.capture(delay_ms=delay_ms, record=True)

    def _finished(self, code, holds_clipboard):
        self.session = None

    # -- recording -------------------------------------------------------------

    def _recording_started(self, rec: recording.Recording):
        self.recording = rec
        rec.changed.connect(self._update_tooltip)
        rec.finished.connect(self._recording_ended)
        if self.tray:
            self.tray.setIcon(_stop_icon())
        self._clock.start()
        self._update_tooltip()
        self._update_menu()

    def _update_tooltip(self):
        rec = self.recording
        if not self.tray or rec is None:
            return
        state = {"starting": "Starting to record", "paused": "Recording paused",
                 "saving": "Saving the recording"}.get(rec.state, "Recording")
        self.tray.setToolTip(f"Flatshot: {state}  {recording.clock(rec.elapsed_ms())}  ·  click to stop")

    def _recording_ended(self, code, holds_clipboard):
        self.recording = None
        self._clock.stop()
        if self.tray:
            self.tray.setIcon(QIcon(ICON_PATH))
            self.tray.setToolTip("Flatshot")
        self._update_menu()

    def _on_action(self, key: str, path: Path, at=None, layout=None):
        """A notification's button. ``at`` is where a screenshot was taken
        and ``layout`` the monitors then (pin.screen_layout)."""
        if key in ("default", "open"):
            output.open_file(path)
        elif key == "folder":
            output.show_in_folder(path)
        elif key == "annotate" and path.exists():
            self.edit(str(path))
        elif key == "pin":
            image = QImage(str(path))
            if not image.isNull():
                # Where it was taken, unless the monitors changed since: then
                # the desktop places it.
                pin.show(image, at if at is not None and layout == pin.screen_layout() else None)

    def edit(self, path: str):
        """Open ``path`` in an annotation editor (a window of its own)."""
        from flatshot import editor

        editor.open_file(path)

    def _choose_image(self):
        # After the menu is done with the click, and without blocking: a
        # modal dialog run from inside the tray menu's D-Bus call can leave
        # the app hanging.
        QTimer.singleShot(0, self._open_image_dialog)

    def _open_image_dialog(self):
        from flatshot import filechooser

        images = ("png", "jpg", "jpeg", "webp", "bmp", "gif", "avif", "jxl")
        filechooser.open_file(None, "Annotate an image", str(output.base_folder(config.load())),
                              [("Images", [f"*.{e}" for e in images])], self.edit)

    def show_settings(self):
        from flatshot.settings import SettingsWindow

        if self.settings is None:
            self.settings = SettingsWindow(config.load(), self.shortcuts)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def quit(self):
        if self.recording is not None:
            # Save what was recorded first.
            self.recording.stop()
            recording.when_idle(self.quit)
            return
        if self.tray:
            self.tray.hide()
        self.app.quit()
