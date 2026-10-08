"""Headless smoke test (``flatshot --self-test``), used by CI on every
package. Builds a fake desktop with a QR code, runs the overlay on it,
draws, renders and scans. Set FLATSHOT_SELFTEST_OUT=dir to keep images."""

import faulthandler
import json
import os
import sys

from flatshot import qt
import tempfile
import time
from pathlib import Path

from flatshot.qt import QColor, QFont, QImage, QLinearGradient, QPainter, QPointF, QRectF, Qt

TEST_URL = "https://github.com/nicholas-sbx/screenshot"
TEST_EAN = "4006381333931"
ASSETS = Path(__file__).with_name("assets")


def _fake_desktop(w=1600, h=1000) -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0, QColor("#2B3A55"))
    grad.setColorAt(1, QColor("#5C4B6E"))
    p.fillRect(img.rect(), grad)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#F5F3EF"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(120, 140, 820, 560), 14, 14)
    p.setPen(QColor("#333"))
    f = QFont()
    f.setPixelSize(30)
    p.setFont(f)
    p.drawText(QPointF(170, 220), "Some window with text")
    f.setPixelSize(18)
    p.setFont(f)
    for i in range(8):
        p.drawText(QPointF(170, 280 + i * 34), f"Line {i + 1}: lorem ipsum dolor sit amet, consectetur")
    # Fixed fixtures, so scanning is tested even where codes can't be generated.
    qr = QImage(str(ASSETS / "selftest-qr.png"))
    p.fillRect(QRectF(1060, 240, qr.width() + 40, qr.height() + 40), QColor("white"))
    p.drawImage(1080, 260, qr)
    ean = QImage(str(ASSETS / "selftest-ean13.png"))
    p.fillRect(QRectF(1060, 600, ean.width() + 40, ean.height() + 40), QColor("white"))
    p.drawImage(1080, 620, ean)
    p.end()
    return img


def run() -> int:
    # A hang must fail loudly with stack traces, not sit until CI's timeout.
    faulthandler.dump_traceback_later(120, exit=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tmp = Path(tempfile.mkdtemp(prefix="flatshot-selftest-"))
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")  # never touch the real settings
    os.environ["XDG_STATE_HOME"] = str(tmp / "state")
    from flatshot import app as appmod, config, output, scanner, shapes
    from flatshot.notify import Notifier
    from flatshot.session import Request, Session

    app = appmod.make_app()
    out_dir = os.environ.get("FLATSHOT_SELFTEST_OUT")
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    src = tmp / "desktop.png"
    desktop = _fake_desktop()
    assert desktop.save(str(src)), "could not write test image"

    cfg = config.Config(save_dir=str(tmp / "shots"), notify=False, clipboard="none", dim_opacity=45)
    finished = []
    ctl = Session(cfg, Request(image=str(src)), Notifier(interactive=False),
                  lambda code, holds: finished.append(code))
    ctl.start()
    assert ctl.overlays, "no overlay was created"
    ov = ctl.overlays[0]
    ov.resize(ov.base.deviceIndependentSize().toSize())
    app.processEvents()
    assert ctl.toolbar_overlay is ov and ov.toolbar.isVisibleTo(ov), "toolbar not on the active monitor"
    assert ctl.dim.alpha() == round(45 * 2.55)
    if os.environ.get("FLATSHOT_EXPECT_LAYER_SHELL") == "1":
        from flatshot import layershell

        assert getattr(ov, "_layer_shell", None) is not None, \
            f"overlay is not a layer-shell surface: {layershell.status}"
        for _ in range(100):  # wait for the compositor's configure
            if ov.windowHandle().isExposed():
                break
            app.processEvents()
            time.sleep(0.02)
        assert ov.windowHandle().isExposed(), "layer-shell overlay was never mapped"
        print("self-test: overlay is a layer-shell surface")

    # Codes: found, toggled with Q, dismissed with ×; toggling never revives a dismissed one.
    def key(k, text="", mods=Qt.KeyboardModifier.NoModifier):
        ctl.key(ov, qt.QKeyEvent(qt.QEvent.Type.KeyPress, qt.keyval(k), mods, text))

    codes = scanner.scan(desktop)
    which = scanner.backend()
    expected = os.environ.get("FLATSHOT_EXPECT_SCANNER")
    if expected:
        assert which == expected, f"scanner backend is {which}, expected {expected}"
    if which is not None:
        texts = sorted(c.text for c in codes)
        assert texts == sorted([TEST_URL, TEST_EAN]), f"{which} found {texts}"
        ctl._codes_found(codes)
        assert ov.chips and ov.chips[0].isVisibleTo(ov), "no code chip was shown"
        key(Qt.Key.Key_Q, "q")
        assert not ctl.codes_visible and not ov.chips[0].isVisibleTo(ov)
        key(Qt.Key.Key_Q, "q")
        assert ctl.codes_visible and ov.chips[0].isVisibleTo(ov)
        print(f"self-test: {which} found {len(codes)} codes (QR + EAN-13)")
    else:
        print("self-test: no barcode scanner installed, scan check skipped")

    dpr = ov.dpr()

    def at(x, y):  # test coordinates are in image pixels
        return QPointF(x / dpr, y / dpr)

    ov.cursor_pos = at(700, 600)
    if out_dir:
        ov.grab().save(str(Path(out_dir) / "overlay-region.png"))
    # Window detection: hover highlights the topmost window, a click captures it.
    # Fake KWin report (global logical coords, bottom to top): the text
    # window and the QR card of the fake desktop.
    from flatshot.windows import parse

    def lg(x, y, w, h):
        return [round(x / dpr), round(y / dpr), round(w / dpr), round(h / dpr)]

    found = parse(json.dumps([lg(120, 140, 820, 560) + ["Notes — Kate", "kate"],
                              lg(1060, 240, 225, 225) + ["QR card", "viewer"]]))
    assert [w.title for w in found.windows] == ["QR card", "Notes — Kate"], found.windows
    ctl._desktop_found(found)
    assert len(ov.windows) == 2
    ov.cursor_pos = at(1180, 450)
    ov._update_hover()
    assert ov.hover_window and ov.hover_window[1].title == "QR card", ov.hover_window
    ov.cursor_pos = at(300, 600)
    ov._update_hover()
    assert ov.hover_window and ov.hover_window[1].title == "Notes — Kate", ov.hover_window
    if out_dir:
        ov.grab().save(str(Path(out_dir) / "overlay-window.png"))
    ov.cursor_pos = at(1500, 900)
    ov._update_hover()
    assert ov.hover_window is None

    if which is not None:
        n = ctl.code_count()
        ctl.dismiss_code(ov, ov.codes[0][0])
        assert ctl.code_count() == n - 1
        key(Qt.Key.Key_Q, "q")
        key(Qt.Key.Key_Q, "q")
        assert ctl.code_count() == n - 1, "a dismissed code came back"

    for tool, a, b in [("arrow", (200, 760), (420, 640)), ("rect", (150, 170), (700, 240)),
                       ("pixelate", (160, 330), (560, 400)), ("marker", (160, 460), (600, 460)),
                       ("ellipse", (980, 200), (1340, 600))]:
        shape = shapes.create(tool, at(*a), ctl.color, ctl.size)
        shape.extend(at(*b), False)
        ov.commit(shape)
    ov.commit(shapes.Counter(at(140, 160), ctl.color, ctl.size, ctl.next_number()))
    text = shapes.Text(at(460, 720), ctl.color, 2)
    text.text = "flatshot"
    ctl.begin_text(ov, text)
    ctl.commit_text()
    assert len(ov.annotations) == 7, len(ov.annotations)
    ctl.undo()
    assert len(ov.annotations) == 6
    ctl.redo()
    assert len(ov.annotations) == 7

    # Keyboard: tool hotkey, typing into a text box, Enter, Ctrl+Z.
    key(Qt.Key.Key_T, "t")
    assert ctl.tool == "text", ctl.tool
    ctl.begin_text(ov, shapes.Text(at(1000, 760), ctl.color, 1))
    for ch in "hi!":
        key(Qt.Key.Key_A, ch)  # the key code doesn't matter while typing
    key(Qt.Key.Key_Backspace)
    key(Qt.Key.Key_Return)
    assert ctl.text_edit is None and ov.annotations[-1].text == "hi", ov.annotations[-1]
    key(Qt.Key.Key_Z, "", Qt.KeyboardModifier.ControlModifier)
    assert len(ov.annotations) == 7
    key(Qt.Key.Key_3, "3")
    assert ctl.color_index == 2
    key(Qt.Key.Key_P, "p")
    assert ctl.tool == "pen"
    if out_dir:
        ov.toolbar.place()
        ov.grab().save(str(Path(out_dir) / "overlay-draw.png"))

    region = ov.render(QRectF(at(100, 100), at(1000, 800)))
    assert abs(region.width() - 900) <= 2 and abs(region.height() - 700) <= 2, region.size()
    if out_dir:
        region.save(str(Path(out_dir) / "result.png"))

    # Full capture path: render, close the overlays, save, report back.
    ctl.capture(ov, QRectF(at(100, 100), at(1000, 800)))
    for _ in range(50):
        app.processEvents()
        if finished:
            break
    assert finished == [0], finished
    saved = list((tmp / "shots").glob("*.png"))
    assert len(saved) == 1 and QImage(str(saved[0])).width() == region.width(), saved

    # Settings window: builds, renders, and writes the config file.
    from flatshot.settings import SettingsWindow, Toggle
    from flatshot.shortcuts import GlobalShortcuts

    settings = SettingsWindow(config.load(), GlobalShortcuts())
    settings.resize(760, 600)
    settings.show_page(2)  # After capture
    app.processEvents()
    if out_dir:
        settings.grab().save(str(Path(out_dir) / "settings.png"))
    toggles = settings.findChildren(Toggle)
    assert len(toggles) >= 6, len(toggles)
    before = config.load().notify
    settings._save(notify=not before, dim_opacity=0, format="jpg", quality=55)
    jpg = output.save(QImage(str(src)), config.load())
    assert jpg.suffix == ".jpg" and QImage(str(jpg)).width() == QImage(str(src)).width(), jpg
    reloaded = config.load()
    assert reloaded.notify is (not before) and reloaded.dim_opacity == 0, reloaded
    settings.close()

    _instant_and_extras(app, tmp, desktop, out_dir)
    print(f"self-test ok ({app.platformName()}, Qt {qt.QT_VERSION}, {qt.BINDING})")
    return 0


def _instant_and_extras(app, tmp: Path, desktop: QImage, out_dir):
    """Instant capture modes, last region, file name templates, pinning and
    the colour picker — without a real compositor (the grab and the window
    query are stubbed)."""
    from flatshot import capture, config, output, pin, windows
    from flatshot.notify import Notifier
    from flatshot.qt import QGuiApplication, QPoint, QRect
    from flatshot.session import Request, Session

    def wait(done):
        for _ in range(200):
            app.processEvents()
            if done():
                return
            time.sleep(0.01)

    # The parts of the KWin report the instant modes use.
    report = windows.parse(json.dumps({
        "windows": [[10, 20, 300, 200, "Notes", "kate"], [50, 60, 120, 80, "a/../b", "org.kde.dolphin"]],
        "active": 1, "cursor": [5, 5]}))
    assert report.active is not None and report.active.app == "org.kde.dolphin", report.active
    assert report.windows[0].title == "a/../b" and report.cursor == QPoint(5, 5)
    assert windows.parse("not json").windows == [] and windows.parse("[1]").windows == []

    assert capture.parse_geometry("200x100+30+40") == QRect(30, 40, 200, 100)
    assert capture.parse_geometry("10x10-5+0") == QRect(-5, 0, 10, 10)
    assert capture.parse_geometry("0x10+0+0") is None and capture.parse_geometry("big") is None

    # File name templates: strftime + tokens, subfolders, nothing escapes the folder.
    shots = tmp / "templated"
    cfg = config.Config(save_dir=str(shots / "%Y" / "{app}"), filename="{mode}/{title}_{n:3}_{w}x{h}",
                        format="jpg", notify=False, clipboard="none", quality=70)
    shot = output.Shot(mode="window", app="../../evil", title="../../../etc/passwd\x00\n")
    path = output.target_path(cfg, shot, (64, 32), "jpg")
    year = time.strftime("%Y")
    assert path.parent.parent.parent == shots / year, path
    assert ".." not in path.parts and path.is_relative_to(shots), path
    assert path.name == "etc_passwd_001_64x32.jpg", path.name
    second = output.target_path(cfg, shot, (1, 1), "jpg")
    assert second.name.endswith("_002_1x1.jpg"), second.name
    assert output.target_path(config.Config(save_dir=str(shots), filename="{app}/{title}"), output.Shot(),
                              (1, 1)) == shots / "Screenshot.png"
    assert output.base_folder(cfg) == shots
    small = desktop.copy(0, 0, 64, 32)
    saved = output.save(small, cfg, shot=shot)
    assert saved.exists() and saved.suffix == ".jpg" and QImage(str(saved)).width() == 64, saved
    png = output.save(small, config.Config(save_dir=str(shots), filename="plain.png"))
    assert png.name == "plain.png" and not QImage(str(png)).isNull(), png
    assert output.target_path(config.Config(save_dir=str(shots), filename="./"), output.Shot(), (1, 1)) \
        == shots / "Screenshot.png"

    # Instant modes, with the screen grab and the window query stubbed.
    grabs = []

    def fake_grab(preferred="auto", pointer=False):
        grabs.append(pointer)
        return desktop.copy()

    real_grab, real_query, real_supported = capture.grab_desktop, windows.query_compositor, \
        windows.WindowFinder.supported
    capture.grab_desktop = fake_grab
    windows.WindowFinder.supported = staticmethod(lambda: False)
    screen = QGuiApplication.primaryScreen().geometry()
    virt = capture.virtual_geometry()
    scale = desktop.width() / virt.width()
    active = windows.Window(QRect(screen.x() + 20, screen.y() + 30, 100, 60), "Doc — Editor", "editor")
    query = {"desktop": windows.Desktop([active], active, screen.center())}
    windows.query_compositor = lambda: query["desktop"]
    out = tmp / "instant"
    try:
        def run(mode, rect=None, pin_it=False, **cfg_changes):
            done = []
            cfg = config.Config(save_dir=str(out), filename="{mode}-{app}-{n}", notify=False,
                                clipboard="none", **cfg_changes)
            session = Session(cfg, Request(mode=mode, rect=rect, pin=pin_it), Notifier(interactive=False),
                              lambda code, holds: done.append(code))
            session.start()
            wait(lambda: done)
            assert done, f"{mode}: never finished"
            return done[0]

        def newest():
            return max(out.glob("*.png"), key=lambda p: p.stat().st_mtime_ns)

        assert run("window", include_pointer=True) == 0 and grabs[-1] is True
        img = QImage(str(newest()))
        assert newest().name.startswith("window-editor-"), newest()
        assert abs(img.width() - round(100 * scale)) <= 1 and abs(img.height() - round(60 * scale)) <= 1, img.size()

        assert run("monitor") == 0 and grabs[-1] is False
        img = QImage(str(newest()))
        assert newest().name.startswith("monitor-editor-"), newest()
        assert abs(img.width() - round(screen.width() * scale)) <= 1, img.size()

        assert run("screens") == 0 and QImage(str(newest())).size() == desktop.size()

        assert run("rect", QRect(screen.x() + 10, screen.y() + 10, 50, 40)) == 0
        assert abs(QImage(str(newest())).width() - round(50 * scale)) <= 1
        assert config.load_state()["last_region"] == [screen.x() + 10, screen.y() + 10, 50, 40]
        config.update_state(last_region=[screen.x() + 4, screen.y() + 4, 30, 20])
        assert run("last") == 0 and abs(QImage(str(newest())).height() - round(20 * scale)) <= 1

        query["desktop"] = windows.Desktop()  # no active window known
        assert run("window") == 2, "window capture without an active window should fail"
        assert run("rect", QRect(virt.right() + 500, 0, 10, 10)) == 2, "off-screen region should fail"

        # Pinning instead of saving.
        before = len(list(out.glob("*.png")))
        assert run("rect", QRect(screen.x(), screen.y(), 80, 50), pin_it=True) == 0
        assert len(list(out.glob("*.png"))) == before, "a pinned capture was saved"
        assert pin.open_count() == 1
        pinned = pin._open[0]
        app.processEvents()
        if out_dir:
            pinned.grab().save(str(Path(out_dir) / "pin.png"))
        closed = []
        pin.when_all_closed(lambda: closed.append(True))
        pinned.close()
        assert pin.open_count() == 0 and closed == [True]
    finally:
        capture.grab_desktop, windows.query_compositor = real_grab, real_query
        windows.WindowFinder.supported = real_supported

    # Colour picker: I copies the colour under the pointer and finishes.
    src = tmp / "desktop.png"
    done = []
    ctl = Session(config.Config(save_dir=str(out), notify=False, clipboard="none"), Request(image=str(src), scan=False),
                  Notifier(interactive=False), lambda code, holds: done.append(code))
    ctl.start()
    ov = ctl.overlays[0]
    ov.cursor_pos = QPointF(1, 1)
    ctl.key(ov, qt.QKeyEvent(qt.QEvent.Type.KeyPress, qt.keyval(Qt.Key.Key_I), Qt.KeyboardModifier.NoModifier, "i"))
    wait(lambda: done)
    assert done == [0] and not ctl.overlays, done
    print("self-test: instant modes, last region, templates, pin and colour picker ok")

    # Toolbar pin toggle (K): the drag still captures at once, but pins instead of saving.
    from flatshot.qt import QRectF
    done = []
    pin_out = tmp / "pin-toggle"
    ctl = Session(config.Config(save_dir=str(pin_out), notify=False, clipboard="none"),
                  Request(image=str(src), scan=False), Notifier(interactive=False),
                  lambda code, holds: done.append(code))
    ctl.start()
    ov = ctl.overlays[0]
    ov.toolbar.pin_button.click()
    assert ctl.pin_mode and ov.toolbar.pin_button.active
    ctl.key(ov, qt.QKeyEvent(qt.QEvent.Type.KeyPress, qt.keyval(Qt.Key.Key_K), Qt.KeyboardModifier.NoModifier, "k"))
    assert not ctl.pin_mode
    ctl.key(ov, qt.QKeyEvent(qt.QEvent.Type.KeyPress, qt.keyval(Qt.Key.Key_K), Qt.KeyboardModifier.NoModifier, "k"))
    ctl.capture(ov, QRectF(10, 10, 60, 40))
    wait(lambda: done)
    assert done == [0] and pin.open_count() == 1 and not pin_out.exists(), (done, pin.open_count())
    pin._open[0].close()

    # Themes: every palette switches the UI colours; annotations don't follow.
    from flatshot import shapes, theme
    from flatshot.settings import SettingsWindow
    from flatshot.shortcuts import GlobalShortcuts

    def annotated() -> QImage:
        img = QImage(120, 60, QImage.Format.Format_RGB32)
        img.fill(QColor("#808080"))
        p = QPainter(img)
        text = shapes.Text(QPointF(6, 6), theme.SWATCHES[0], 2)
        text.text, text.editing = "Ab", False
        text.paint(p, None)
        shapes.Counter(QPointF(90, 30), theme.SWATCHES[5], 2, 7).paint(p, None)
        p.end()
        return img

    annotated()  # warm up font loading
    reference = annotated()
    settings = SettingsWindow(config.load(), GlobalShortcuts())
    settings.resize(760, 600)
    for name in theme.THEMES:
        settings._set_theme(name)
        app.processEvents()
        assert config.load().theme == name and theme.C.ACCENT == QColor(theme.THEMES[name][9]), name
        assert annotated() == reference, f"annotations changed with the {name} theme"
        if out_dir:
            settings.grab().save(str(Path(out_dir) / f"settings-{name}.png"))
    settings._set_theme("ember")
    settings.close()
    print(f"self-test: pin toggle and {len(theme.THEMES)} themes ok")
