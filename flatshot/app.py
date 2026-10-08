"""Entry point: one-shot capture, tray app, or a message to the tray app."""

import argparse
import os
import signal
import sys

from flatshot import __version__, config, ipc, theme
from flatshot.qt import QApplication, QGuiApplication, QIcon, Qt, QTimer
from flatshot.capture import parse_geometry


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="flatshot", description="Region screenshots with a floating draw toolbar.")
    ap.add_argument("-d", "--delay", type=float, default=0, metavar="SEC", help="wait before capturing")
    instant = ap.add_argument_group("instant captures (no UI)").add_mutually_exclusive_group()
    instant.add_argument("-f", "--full", action="store_true", help="capture every screen")
    instant.add_argument("-m", "--monitor", action="store_true", help="capture the monitor under the pointer")
    instant.add_argument("-w", "--window", action="store_true", help="capture the active window")
    instant.add_argument("-l", "--last-region", action="store_true",
                         help="capture the region captured last time")
    instant.add_argument("-r", "--region", metavar="WxH+X+Y", help="capture this area (logical pixels)")
    ap.add_argument("--pin", action="store_true", help="pin the result to the screen instead of saving it")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="save to this file instead of the screenshot folder ('-' writes PNG to stdout)")
    ap.add_argument("-i", "--image", metavar="FILE", help="annotate an existing image instead of the screen")
    ap.add_argument("--backend", choices=list(config.BACKENDS),
                    help="screen capture helper (default: from settings, else auto)")
    ap.add_argument("--no-copy", action="store_true", help="don't copy the result to the clipboard")
    ap.add_argument("--no-save", action="store_true", help="don't save the result to disk")
    ap.add_argument("--no-scan", action="store_true", help="don't look for QR codes / barcodes")
    ap.add_argument("--tray", action="store_true", help="run in the system tray with global shortcuts")
    ap.add_argument("--settings", action="store_true", help="open the settings window")
    ap.add_argument("--quit", action="store_true", help="stop the running tray app")
    ap.add_argument("--print-config", action="store_true", help="print the effective config and exit")
    ap.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"flatshot {__version__}")
    args = ap.parse_args(argv)
    if args.region is not None and parse_geometry(args.region) is None:
        ap.error(f"--region: expected WxH+X+Y (like 800x600+100+50), got {args.region!r}")
    return args


def _mode(args) -> str:
    if args.full:
        return "screens"
    if args.monitor:
        return "monitor"
    if args.window:
        return "window"
    if args.last_region:
        return "last"
    if args.region:
        return "rect"
    return "region"


def make_app() -> QApplication:
    # Opt out of the desktop's Qt style, palette and fonts before Qt starts.
    os.environ.pop("QT_STYLE_OVERRIDE", None)
    # Everything is software-painted; skip Qt Wayland's EGL setup, which can
    # cost hundreds of milliseconds on the first window (= the first capture).
    if "QT_WAYLAND_CLIENT_BUFFER_INTEGRATION" not in os.environ:
        os.environ["QT_WAYLAND_CLIENT_BUFFER_INTEGRATION"] = "none"
        rules = os.environ.get("QT_LOGGING_RULES", "")
        # ... and don't warn that "none" isn't a real integration.
        os.environ["QT_LOGGING_RULES"] = ";".join(filter(None, [rules, "qt.qpa.wayland.warning=false"]))
    QApplication.setDesktopSettingsAware(False)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("flatshot")
    app.setApplicationDisplayName("Flatshot")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("flatshot")
    app.setWindowIcon(QIcon(theme.ICON_PATH))
    theme.use(config.load().theme)
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app)
    return app


def _forwardable(args) -> str | None:
    """The tray-app command equivalent to these arguments, if any."""
    if args.image or args.output or args.backend or args.no_copy or args.no_save or args.no_scan:
        return None  # one-off options: handle in this process
    if args.quit:
        return "quit"
    if args.settings:
        return "settings"
    if args.tray:
        return "settings"  # already running: show something useful
    mode = _mode(args)
    if mode == "rect":
        mode = f"rect:{args.region.strip()}"
    return f"{mode} {args.delay:g}" + (" pin" if args.pin else "")


def run_once(app, args) -> int:
    from flatshot import pin
    from flatshot.notify import Notifier
    from flatshot.session import Request, Session

    cfg = config.load()
    if args.no_copy:
        cfg.clipboard = "none"
    if args.no_save:
        cfg.save_to_disk = False
    state = {"code": 0}

    def finished(code, holds_clipboard):
        state["code"] = code
        if pin.open_count():
            pin.when_all_closed(app.quit)  # stay up while something is pinned
            return
        if not holds_clipboard:
            app.quit()
            return
        # We own the clipboard with no helper to hand it to: stay alive
        # (windowless) until something else takes it, or for 10 minutes.
        QGuiApplication.clipboard().dataChanged.connect(app.quit)
        QTimer.singleShot(10 * 60 * 1000, app.quit)

    request = Request(mode=_mode(args), rect=parse_geometry(args.region or ""), pin=args.pin, image=args.image,
                      output=args.output, backend=args.backend, scan=not args.no_scan)
    session = Session(cfg, request, Notifier(interactive=False), finished)
    QTimer.singleShot(max(0, int(args.delay * 1000)), session.start)
    app.exec()
    return state["code"]


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if args.self_test:
        from flatshot.selftest import run
        return run()
    app = make_app()
    if args.print_config:
        print(config.dump(config.load()))
        return 0

    command = _forwardable(args)
    if command and ipc.send(command):
        return 0  # the tray app took it
    if args.quit:
        return 0
    if args.tray:
        from flatshot.tray import TrayApp

        tray = TrayApp(app)
        if not tray.start():
            return 1
        return app.exec()
    if args.settings:
        from flatshot.settings import SettingsWindow

        window = SettingsWindow(config.load(), shortcuts=None)
        window.closed.connect(app.quit)
        window.show()
        return app.exec()
    return run_once(app, args)
