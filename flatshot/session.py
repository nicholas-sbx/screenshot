"""One capture: freeze the screens, let the user draw/select, deliver.

Used both for one-shot runs (`flatshot`) and by the tray app, which runs a
new Session per shortcut press.
"""

import subprocess
import sys
from dataclasses import dataclass

from flatshot import capture, config, output, scanner, shapes, theme, windows
from flatshot.notify import Notifier
from flatshot.overlay import Overlay
from flatshot.qt import (
    QColor, QCursor, QDesktopServices, QGuiApplication, QImage, QPixmap, QPoint, QRect, QRectF, Qt, QTimer, QUrl,
    keyval,
)
from flatshot.widgets import TOOLS

TOOL_KEYS = {keyval(getattr(Qt.Key, f"Key_{key}")): name for name, _, key in TOOLS}
K = {name: keyval(getattr(Qt.Key, f"Key_{name}")) for name in
     ["Escape", "Return", "Enter", "Backspace", "Z", "Y", "S", "C", "Q", "1", "BracketLeft", "BracketRight"]}


@dataclass
class Request:
    full: bool = False  # no UI, deliver every screen right away
    image: str | None = None  # annotate this file instead of capturing
    output: str | None = None  # explicit output file
    backend: str | None = None
    scan: bool = True


class Session:
    def __init__(self, cfg: config.Config, request: Request, notifier: Notifier, on_finished, on_action=None):
        """``on_finished(exit_code, holds_clipboard)`` is called exactly once.
        ``on_action(key, path)`` handles notification buttons (tray only)."""
        self.cfg = cfg
        self.request = request
        self.notifier = notifier
        self.on_finished = on_finished
        self.on_action = on_action
        tools = {name for name, _, _ in TOOLS}
        self.tool = cfg.default_tool if cfg.default_tool in tools else "region"
        self.color_index = min(max(cfg.default_color, 0), len(theme.SWATCHES) - 1)
        self.size = min(max(cfg.default_size, 0), len(theme.SIZES) - 1)
        self.dim = QColor(theme.C.DIM)
        self.dim.setAlpha(round(cfg.dim_opacity * 2.55))
        self.codes_visible = cfg.show_codes
        self.overlays: list[Overlay] = []
        self.pointer_overlay: Overlay | None = None  # where the mouse is
        self.toolbar_overlay: Overlay | None = None  # where the toolbar is
        self.history: list[Overlay] = []
        self.redo_stack: list[Overlay] = []
        self.text_edit: tuple[Overlay, shapes.Text] | None = None
        self.hint: str | None = None
        self.scanner = scanner.Scanner()
        self.scanner.finished.connect(self._codes_found)
        self.window_finder: windows.WindowFinder | None = None
        self._windows: list | None = None
        self.done = False

    @property
    def color(self):
        return theme.SWATCHES[self.color_index]

    # -- startup -----------------------------------------------------------

    def start(self):
        if self.cfg.detect_windows and not (self.request.image or self.request.full):
            # Ask KWin for window bounds now, while the windows are as captured.
            self.window_finder = windows.WindowFinder()
            self.window_finder.found.connect(self._windows_found)
            if not self.window_finder.start():
                self.window_finder = None
        try:
            if self.request.image:
                image = QImage(self.request.image)
                if image.isNull():
                    raise capture.CaptureError(f"cannot read {self.request.image}")
            else:
                image = capture.grab_desktop(self.request.backend or self.cfg.backend)
        except capture.CaptureError as e:
            self.fail(str(e))
            return
        if self.request.full:
            self._deliver(image)
            return
        self._build_overlays(image)
        if self.cfg.scan_codes and self.request.scan:
            # Let the overlay reach the screen before scanning competes for CPU.
            QTimer.singleShot(60, lambda: None if self.done else self.scanner.start(image))

    def _build_overlays(self, image: QImage):
        if self.request.image:
            # Show the image on the screen under the mouse, scaled to fit.
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            pm = QPixmap.fromImage(image)
            g = screen.geometry()
            pm.setDevicePixelRatio(max(image.width() / g.width(), image.height() / g.height(), 1.0))
            self.overlays.append(Overlay(self, screen, pm, QPoint(0, 0)))
        else:
            virt = capture.virtual_geometry()
            scale = image.width() / max(1, virt.width())
            for screen in QGuiApplication.screens():
                g = screen.geometry()
                phys = QRect(round((g.x() - virt.x()) * scale), round((g.y() - virt.y()) * scale),
                             round(g.width() * scale), round(g.height() * scale)).intersected(image.rect())
                pm = QPixmap.fromImage(image.copy(phys))
                pm.setDevicePixelRatio(phys.width() / max(1, g.width()))
                self.overlays.append(Overlay(self, screen, pm, phys.topLeft()))
        for o in self.overlays:
            o.add_toolbar()
        # Best guess until the pointer enters an overlay (on Wayland the
        # cursor position is only known once it does).
        start_screen = QGuiApplication.screenAt(QCursor.pos())
        if self.cfg.toolbar_follows_mouse and start_screen is not None:
            first = next((o for o in self.overlays if o.target_screen is start_screen), None)
        else:
            first = None
        primary = QGuiApplication.primaryScreen()
        first = first or next((o for o in self.overlays if o.target_screen is primary), self.overlays[0])
        self.pointer_overlay = self.toolbar_overlay = first
        self._sync_toolbars()
        for o in self.overlays:
            o.show_on_screen()
        if self._windows is not None:  # KWin answered before the overlays existed
            self._windows_found(self._windows)

    def _windows_found(self, found):
        self._windows = found
        if self.done:
            return
        for o in self.overlays:
            o.set_windows(found)
        self.refresh()

    def _codes_found(self, codes):
        if self.done:
            return
        for o in self.overlays:
            o.set_codes(codes)
        self.refresh()

    # -- which monitor -----------------------------------------------------

    def activate(self, overlay: Overlay):
        """The pointer is now over ``overlay``."""
        if overlay is self.pointer_overlay:
            return
        previous = self.pointer_overlay
        self.pointer_overlay = overlay
        if self.cfg.toolbar_follows_mouse and not any(o.sel_rect is not None for o in self.overlays):
            self.toolbar_overlay = overlay
            self._sync_toolbars()
        for o in (previous, overlay):
            if o is not None:
                o.update()

    def _sync_toolbars(self):
        for o in self.overlays:
            if o.toolbar:
                o.toolbar.setVisible(o is self.toolbar_overlay and o.sel_rect is None)

    # -- tool state --------------------------------------------------------

    def refresh(self):
        self._sync_toolbars()
        for o in self.overlays:
            o.refresh()

    def set_tool(self, tool: str):
        self.commit_text()
        self.tool = tool
        self.refresh()

    def set_color(self, index: int):
        self.color_index = index
        if self.text_edit:
            self.text_edit[1].color = self.color
        self.refresh()

    def cycle_size(self):
        self.set_size((self.size + 1) % len(theme.SIZES))

    def set_size(self, size: int):
        self.size = min(max(size, 0), len(theme.SIZES) - 1)
        if self.text_edit:
            self.text_edit[1].size = self.size
        self.refresh()

    def toggle_codes(self):
        self.codes_visible = not self.codes_visible
        self.refresh()

    def code_count(self) -> int:
        return sum(len(o.codes) for o in self.overlays)

    def dismiss_code(self, overlay: Overlay, code):
        overlay.dismiss_code(code)
        self.set_hint(None)
        self.refresh()

    def set_hint(self, hint: str | None):
        self.hint = hint
        for o in self.overlays:
            o.update()

    def next_number(self) -> int:
        return 1 + sum(isinstance(s, shapes.Counter) for o in self.overlays for s in o.annotations)

    # -- history -----------------------------------------------------------

    def record(self, overlay: Overlay):
        self.history.append(overlay)
        for o in self.redo_stack:
            o.undone.clear()
        self.redo_stack.clear()

    def undo(self):
        self.commit_text()
        if self.history:
            o = self.history.pop()
            if o.undo():
                self.redo_stack.append(o)

    def redo(self):
        if self.redo_stack:
            o = self.redo_stack.pop()
            if o.redo():
                self.history.append(o)

    # -- text --------------------------------------------------------------

    def begin_text(self, overlay: Overlay, shape: shapes.Text):
        self.commit_text()
        self.text_edit = (overlay, shape)
        overlay.update()

    def commit_text(self) -> bool:
        """Finish the text being typed. Returns True if there was one."""
        if not self.text_edit:
            return False
        overlay, shape = self.text_edit
        self.text_edit = None
        shape.editing = False
        if shape.is_valid():
            overlay.commit(shape)
        overlay.update()
        return True

    # -- keys --------------------------------------------------------------

    def key(self, overlay: Overlay, event):
        # With several overlays, keyboard focus can sit on any of them; act
        # on the monitor the pointer is on.
        target = self.pointer_overlay or overlay
        k = keyval(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        enter = k in (K["Return"], K["Enter"])

        if self.text_edit and not ctrl:
            editing, shape = self.text_edit
            if k == K["Escape"] or (enter and not shift):
                self.commit_text()
            elif enter:
                shape.text += "\n"
            elif k == K["Backspace"]:
                shape.text = shape.text[:-1]
            elif event.text() and event.text().isprintable():
                shape.text += event.text()
            editing.update()
            return

        if k == K["Escape"]:
            if not any(o.cancel_gesture() for o in self.overlays):
                self.cancel()
        elif ctrl and k == K["Z"]:
            self.redo() if shift else self.undo()
        elif ctrl and k == K["Y"]:
            self.redo()
        elif enter or (ctrl and k in (K["S"], K["C"])):
            self.capture(target, None)
        elif not ctrl and k == K["Q"]:
            self.toggle_codes()
        elif not ctrl and k in TOOL_KEYS:
            self.set_tool(TOOL_KEYS[k])
        elif not ctrl and K["1"] <= k < K["1"] + len(theme.SWATCHES):
            self.set_color(k - K["1"])
        elif k == K["BracketLeft"]:
            self.set_size(self.size - 1)
        elif k == K["BracketRight"]:
            self.set_size(self.size + 1)

    # -- finishing ---------------------------------------------------------

    def _close_overlays(self):
        self.done = True
        if self.window_finder:
            self.window_finder.stop()
            self.window_finder = None
        for o in self.overlays:
            o.hide()
            o.deleteLater()
        self.overlays = []
        self.pointer_overlay = self.toolbar_overlay = None

    def capture(self, overlay: Overlay, rect: QRectF | None):
        if self.done:
            return
        self.commit_text()
        image = overlay.render(rect)
        self._close_overlays()
        # Let the compositor drop the overlays before doing slower work.
        QTimer.singleShot(0, lambda: self._deliver(image))

    def _deliver(self, image: QImage):
        self.done = True
        try:
            result = output.deliver(image, self.cfg, self.request.output)
        except OSError as e:
            self.fail(str(e))
            return
        if result.path and result.saved:
            print(result.path, flush=True)
        if self.cfg.notify:
            self._notify(result)
        self._finish(0, result.holds_clipboard)

    def _notify(self, result: output.Delivery):
        w, h = result.size
        bits = [f"{w} × {h}"]
        if result.copied:
            bits.append("path copied" if self.cfg.clipboard == "path" else "copied to clipboard")
        if result.saved:
            title = "Screenshot saved"
            bits.insert(0, result.path.name)
        else:
            title = "Screenshot copied" if result.copied else "Screenshot taken"
        actions = {}
        if result.path and self.on_action:
            actions = {"default": "Open", "open": "Open", "folder": "Show in folder", "annotate": "Annotate"}
            if not result.saved:
                actions.pop("folder")
        path = result.path
        self.notifier.send(title, "  ·  ".join(bits), image=path, actions=actions,
                           on_action=(lambda key: self.on_action(key, path)) if actions else None)

    def copy_code(self, code):
        self._close_overlays()
        _, holds = output.copy_text(code.text)
        if self.cfg.notify:
            self.notifier.send("Copied from code", code.text[:300])
        print(code.text, flush=True)
        self._finish(0, holds)

    def open_code(self, code):
        self._close_overlays()
        url = code.text.strip()
        try:
            subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            QDesktopServices.openUrl(QUrl(url))
        self._finish(0, False)

    def cancel(self):
        self._close_overlays()
        self._finish(1, False)

    def fail(self, message: str):
        print(f"flatshot: {message}", file=sys.stderr, flush=True)
        self.notifier.send("Screenshot failed", message, urgent=True)
        self._close_overlays()
        self._finish(2, False)

    def _finish(self, code: int, holds_clipboard: bool):
        callback, self.on_finished = self.on_finished, None
        if callback:
            QTimer.singleShot(0, lambda: callback(code, holds_clipboard))
