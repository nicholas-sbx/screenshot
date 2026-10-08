"""Entry point and the controller that ties the overlays together."""

import argparse
import os
import signal
import subprocess
import sys

from flatshot.qt import (
    QApplication, QDesktopServices, QGuiApplication, QImage, QPixmap, QPoint, QRect, QRectF, Qt, QTimer, QUrl,
)

from flatshot import __version__, capture, config, output, scanner, shapes, theme
from flatshot.qt import keyval
from flatshot.overlay import Overlay
from flatshot.widgets import TOOLS

TOOL_KEYS = {keyval(getattr(Qt.Key, f"Key_{key}")): name for name, _, key in TOOLS}
K = {name: keyval(getattr(Qt.Key, f"Key_{name}")) for name in
     ["Escape", "Return", "Enter", "Backspace", "Z", "Y", "S", "C", "1", "BracketLeft", "BracketRight"]}


class Controller:
    def __init__(self, app: QApplication, cfg: config.Config, args):
        self.app = app
        self.cfg = cfg
        self.args = args
        tools = {name for name, _, _ in TOOLS}
        self.tool = cfg.default_tool if cfg.default_tool in tools else "region"
        self.color_index = min(max(cfg.default_color, 0), len(theme.SWATCHES) - 1)
        self.size = min(max(cfg.default_size, 0), len(theme.SIZES) - 1)
        self.overlays: list[Overlay] = []
        self.history: list[Overlay] = []
        self.redo_stack: list[Overlay] = []
        self.text_edit: tuple[Overlay, shapes.Text] | None = None
        self.hint: str | None = None
        self.scanner = scanner.Scanner()
        self.scanner.finished.connect(self._codes_found)
        self.full_image: QImage | None = None
        self.exit_code = 0
        self.done = False

    @property
    def color(self):
        return theme.SWATCHES[self.color_index]

    # -- startup -----------------------------------------------------------

    def start(self):
        try:
            if self.args.image:
                image = QImage(self.args.image)
                if image.isNull():
                    raise capture.CaptureError(f"cannot read {self.args.image}")
            else:
                image = capture.grab_desktop(self.args.backend or self.cfg.backend)
        except capture.CaptureError as e:
            self.fail(str(e))
            return
        self.full_image = image
        if self.args.full:
            self._deliver(image)
            return
        self._build_overlays(image)
        if self.cfg.scan_codes and not self.args.no_scan:
            self.scanner.start(image)

    def _build_overlays(self, image: QImage):
        if self.args.image:
            # Show the image on the primary screen, scaled to fit.
            screen = QGuiApplication.primaryScreen()
            pm = QPixmap.fromImage(image)
            fit = max(image.width() / screen.geometry().width(), image.height() / screen.geometry().height(), 1.0)
            pm.setDevicePixelRatio(fit)
            self._add_overlay(screen, pm, QPoint(0, 0))
        else:
            virt = capture.virtual_geometry()
            scale = image.width() / max(1, virt.width())
            for screen in QGuiApplication.screens():
                g = screen.geometry()
                phys = QRect(round((g.x() - virt.x()) * scale), round((g.y() - virt.y()) * scale),
                             round(g.width() * scale), round(g.height() * scale)).intersected(image.rect())
                pm = QPixmap.fromImage(image.copy(phys))
                pm.setDevicePixelRatio(phys.width() / max(1, g.width()))
                self._add_overlay(screen, pm, phys.topLeft())
        primary = QGuiApplication.primaryScreen()
        host = next((o for o in self.overlays if o.target_screen is primary), self.overlays[0])
        host.add_toolbar()
        for o in self.overlays:
            o.show_on_screen()

    def _add_overlay(self, screen, pm: QPixmap, origin: QPoint):
        self.overlays.append(Overlay(self, screen, pm, origin))

    def _codes_found(self, codes):
        if self.done:
            return
        for o in self.overlays:
            o.set_codes(codes)

    # -- tool state --------------------------------------------------------

    def refresh(self):
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
        k = keyval(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        enter = k in (K["Return"], K["Enter"])

        if self.text_edit and not ctrl:
            target, shape = self.text_edit
            if k == K["Escape"] or (enter and not shift):
                self.commit_text()
            elif enter:
                shape.text += "\n"
            elif k == K["Backspace"]:
                shape.text = shape.text[:-1]
            elif event.text() and event.text().isprintable():
                shape.text += event.text()
            target.update()
            return

        if k == K["Escape"]:
            if not overlay.cancel_gesture():
                self.cancel()
        elif ctrl and k == K["Z"]:
            self.redo() if shift else self.undo()
        elif ctrl and k == K["Y"]:
            self.redo()
        elif enter or (ctrl and k in (K["S"], K["C"])):
            self.capture(overlay, None)
        elif not ctrl and k in TOOL_KEYS:
            self.set_tool(TOOL_KEYS[k])
        elif not ctrl and K["1"] <= k < K["1"] + len(theme.SWATCHES):
            self.set_color(k - K["1"])
        elif k == K["BracketLeft"]:
            self.set_size(self.size - 1)
        elif k == K["BracketRight"]:
            self.set_size(self.size + 1)

    # -- finishing ---------------------------------------------------------

    def _hide_all(self):
        self.done = True
        for o in self.overlays:
            o.hide()

    def capture(self, overlay: Overlay, rect: QRectF | None):
        if self.done:
            return
        self.commit_text()
        image = overlay.render(rect)
        self._hide_all()
        # Let the compositor drop the overlays before doing slower work.
        QTimer.singleShot(0, lambda: self._deliver(image))

    def _deliver(self, image: QImage):
        self.done = True
        try:
            result = output.deliver(image, self.cfg, self.args.output)
        except OSError as e:
            self.fail(str(e))
            return
        if result.path:
            print(result.path)
        self._finish(result.holds_clipboard)

    def copy_code(self, code):
        self._hide_all()
        _, holds = output.copy_text(code.text)
        if self.cfg.notify:
            output.notify("Copied from code", code.text[:120], "edit-copy")
        print(code.text)
        self._finish(holds)

    def open_code(self, code):
        self._hide_all()
        url = code.text.strip()
        try:
            subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            QDesktopServices.openUrl(QUrl(url))
        self._finish(False)

    def cancel(self):
        self._hide_all()
        self.exit_code = 1
        self.app.quit()

    def fail(self, message: str):
        print(f"flatshot: {message}", file=sys.stderr)
        if self.cfg.notify:
            output.notify("Flatshot failed", message, "dialog-error")
        self._hide_all()
        self.exit_code = 2
        QTimer.singleShot(0, self.app.quit)

    def _finish(self, holds_clipboard: bool):
        if not holds_clipboard:
            QTimer.singleShot(0, self.app.quit)
            return
        # We own the clipboard with no helper to hand it to: stay alive
        # (windowless) until something else takes it, or for 10 minutes.
        QGuiApplication.clipboard().dataChanged.connect(self.app.quit)
        QTimer.singleShot(10 * 60 * 1000, self.app.quit)


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="flatshot", description="Region screenshots with a floating draw toolbar.")
    ap.add_argument("-d", "--delay", type=float, default=0, metavar="SEC", help="wait before capturing")
    ap.add_argument("-f", "--full", action="store_true", help="capture every screen immediately, no UI")
    ap.add_argument("-o", "--output", metavar="FILE", help="save to this file instead of the screenshot folder")
    ap.add_argument("-i", "--image", metavar="FILE", help="annotate an existing image instead of the screen")
    ap.add_argument("--backend", choices=["auto", "spectacle", "grim", "gnome-screenshot", "qt"],
                    help="screen capture helper (default: from config, else auto)")
    ap.add_argument("--no-copy", action="store_true", help="don't copy the result to the clipboard")
    ap.add_argument("--no-save", action="store_true", help="don't save the result to disk")
    ap.add_argument("--no-scan", action="store_true", help="don't look for QR codes / barcodes")
    ap.add_argument("--print-config", action="store_true", help="print the effective config and exit")
    ap.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"flatshot {__version__}")
    return ap.parse_args(argv)


def make_app() -> QApplication:
    # Opt out of the desktop's Qt style, palette and fonts before Qt starts.
    os.environ.pop("QT_STYLE_OVERRIDE", None)
    QApplication.setDesktopSettingsAware(False)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("flatshot")
    app.setApplicationDisplayName("Flatshot")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("flatshot")
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app)
    return app


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if args.self_test:
        from flatshot.selftest import run
        return run()
    app = make_app()
    cfg = config.load()
    if args.print_config:
        print(config.dump(cfg))
        return 0
    if args.no_copy:
        cfg.copy_to_clipboard = False
    if args.no_save:
        cfg.save_to_disk = False
    ctl = Controller(app, cfg, args)
    QTimer.singleShot(max(0, int(args.delay * 1000)), ctl.start)
    app.exec()
    return ctl.exit_code
