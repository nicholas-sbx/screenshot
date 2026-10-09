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

from flatshot.qt import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPointF, QRectF, Qt

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
        # KWin animates layer surfaces whose scope it doesn't know (as Normal windows).
        assert ov._layer_shell.property("scope") == layershell.SCOPE, ov._layer_shell.property("scope")
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
        assert config.load().show_codes is False, "hiding the codes isn't remembered"
        key(Qt.Key.Key_Q, "q")
        assert ctl.codes_visible and ov.chips[0].isVisibleTo(ov) and config.load().show_codes
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
    _pointer_and_snapping(app, tmp, desktop, out_dir)
    _editor(app, tmp, out_dir)
    _drawing_and_text(app, tmp, desktop, out_dir)
    _recording(app, tmp, desktop, out_dir)
    print(f"self-test ok ({app.platformName()}, Qt {qt.QT_VERSION}, {qt.BINDING})")
    return 0


def _instant_and_extras(app, tmp: Path, desktop: QImage, out_dir):
    """Instant capture modes, last region, file name templates, pinning and
    the colour picker — without a real compositor (the grab and the window
    query are stubbed)."""
    from flatshot import capture, config, output, pin, windows
    from flatshot.notify import Notifier
    from flatshot.qt import QGuiApplication, QPoint, QRect, QWheelEvent
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

    def fake_grab(preferred="auto", pointer=False, also_pointer=None):
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

    settings = SettingsWindow(config.load(), GlobalShortcuts())
    settings.resize(760, 600)
    for name in theme.THEMES:
        settings._set_theme(name)
        app.processEvents()
        assert config.load().theme == name and theme.C.ACCENT == QColor(theme.THEMES[name][9]), name
        # Back to back with Ember, so both renders see the same fonts. The
        # first text render after fonts change can differ, so warm up first.
        annotated()
        themed = annotated()
        theme.use("ember")
        assert annotated() == themed, f"annotations changed with the {name} theme"
        theme.use(name)
        if out_dir:
            settings.grab().save(str(Path(out_dir) / f"settings-{name}.png"))
    settings._set_theme("ember")
    settings.close()
    print(f"self-test: pin toggle and {len(theme.THEMES)} themes ok")

    # Magnifier / crosshair switches and size: each changes what's painted.
    def overlay_with(zoom_steps=0, **cfg_changes) -> QImage:
        session = Session(config.Config(save_dir=str(tmp / "x"), notify=False, clipboard="none", **cfg_changes),
                          Request(image=str(src), scan=False), Notifier(interactive=False), lambda *a: None)
        session.start()
        ov = session.overlays[0]
        ov.resize(ov.base.deviceIndependentSize().toSize())
        ov.toolbar.hide()
        ov.cursor_pos = QPointF(200, 200)
        session.pointer_overlay = None  # no hint pill
        notch = QPoint(0, 120 if zoom_steps > 0 else -120)
        for _ in range(abs(zoom_steps)):
            ov.wheelEvent(QWheelEvent(ov.cursor_pos, ov.cursor_pos, QPoint(), notch, Qt.MouseButton.NoButton,
                                      Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
        shot = ov.grab().toImage()
        session.cancel()
        app.processEvents()
        return shot

    both = overlay_with()
    no_loupe = overlay_with(show_loupe=False)
    no_lines = overlay_with(show_crosshair=False)
    bare = overlay_with(show_loupe=False, show_crosshair=False)
    big = overlay_with(loupe_size=240)
    zoomed_in = overlay_with(zoom_steps=3)
    zoomed_out = overlay_with(zoom_steps=-3)
    variants = [both, no_loupe, no_lines, bare, big, zoomed_in, zoomed_out]
    same = [(i, j) for i in range(len(variants)) for j in range(i + 1, len(variants)) if variants[i] == variants[j]]
    assert not same, f"magnifier / crosshair settings changed nothing: {same}"
    # Far from the pointer, with neither, the overlay is just the shaded screen.
    assert bare.pixel(600, 450) == no_loupe.pixel(600, 450)
    assert config.Config(loupe_size=5).loupe_size == config.LOUPE_SIZES[0]

    # Sound: a successful capture runs the player with the chosen file; cancelling doesn't.
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    played = tmp / "played.txt"
    fake = bin_dir / "pw-play"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{played}'\n")
    fake.chmod(0o755)
    clip = tmp / "shutter.wav"
    clip.write_bytes(b"RIFF")
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}:{old_path}"
    try:
        for sound_on, action in ((True, "cancel"), (True, "capture"), (False, "capture")):
            done = []
            session = Session(config.Config(save_dir=str(tmp / "sound"), notify=False, clipboard="none",
                                            sound=sound_on, sound_file=str(clip)),
                              Request(image=str(src), scan=False), Notifier(interactive=False),
                              lambda code, holds: done.append(code))
            session.start()
            if action == "cancel":
                session.cancel()
            else:
                session.capture(session.overlays[0], QRectF(0, 0, 40, 40))
            wait(lambda: done)
        wait(lambda: played.exists())
        time.sleep(0.2)
        assert played.read_text().split() == [str(clip.resolve())], played.read_text()
    finally:
        os.environ["PATH"] = old_path
    print("self-test: magnifier, crosshair and sound settings ok")


def _recording(app, tmp: Path, desktop: QImage, out_dir):
    """The record tool: choose and adjust an area, the options panel, the
    countdown, then a real recording of ffmpeg's test pattern with pause,
    the controls bar, stop and discard."""
    import shutil

    from flatshot import capture, config, recording, screencast, windows
    from flatshot.notify import Notifier
    from flatshot.qt import QEvent, QMouseEvent
    from flatshot.session import Request, Session

    def wait(done, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if done():
                return True
            time.sleep(0.01)
        return False

    def key(session, ov, k, text=""):
        session.key(ov, qt.QKeyEvent(qt.QEvent.Type.KeyPress, qt.keyval(k), Qt.KeyboardModifier.NoModifier, text))
        session.key_release(ov, qt.QKeyEvent(qt.QEvent.Type.KeyRelease, qt.keyval(k),
                                             Qt.KeyboardModifier.NoModifier, text))

    def mouse(ov, kind, x, y):
        types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                 "release": QEvent.Type.MouseButtonRelease}
        pos = QPointF(x, y)
        left = Qt.MouseButton.LeftButton
        event = QMouseEvent(types[kind], pos, ov.mapToGlobal(pos), Qt.MouseButton.NoButton if kind == "move" else left,
                            Qt.MouseButton.NoButton if kind == "release" else left, Qt.KeyboardModifier.NoModifier)
        {"press": ov.mousePressEvent, "move": ov.mouseMoveEvent, "release": ov.mouseReleaseEvent}[kind](event)

    _recording_crop(tmp)

    # Annotating a file can't record.
    src = tmp / "desktop.png"
    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False), Request(image=str(src), scan=False),
                Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    assert not ov.toolbar.tools["record"].isVisibleTo(ov.toolbar), "record button shown for an image"
    key(s, ov, Qt.Key.Key_V, "v")
    assert s.tool != "record"
    s.cancel()

    real_grab, real_supported = capture.grab_desktop, windows.WindowFinder.supported
    capture.grab_desktop = lambda preferred="auto", pointer=False, also_pointer=None: desktop.copy()
    windows.WindowFinder.supported = staticmethod(lambda: False)
    forced = os.environ.get("FLATSHOT_RECORDER")
    os.environ["FLATSHOT_RECORDER"] = "test"
    rec_dir = tmp / "recordings"
    try:
        done = []
        cfg = config.Config(record_dir=str(rec_dir), notify=False, clipboard="none", record_countdown=1,
                            record_format="mp4")
        s = Session(cfg, Request(record=True, scan=False), Notifier(interactive=False),
                    lambda code, holds: done.append(code))
        s.start()
        ov = s.overlays[0]
        ov.resize(ov.target_screen.geometry().size())
        app.processEvents()
        bar = ov.toolbar
        assert s.tool == "record" and bar.tools["record"].active, s.tool
        assert bar.tools["pen"].dimmed and not bar.swatches[0].isEnabled() and not bar.pin_button.isEnabled()

        # Drag out an area; it stays chosen, with the options panel under it.
        mouse(ov, "press", 60, 50)
        mouse(ov, "move", 300, 200)
        mouse(ov, "release", 300, 200)
        assert ov.rec_rect == QRectF(60, 50, 240, 150), ov.rec_rect
        assert ov.panel is not None and ov.panel.isVisibleTo(ov), "no options panel"
        assert ov.panel.geometry().top() >= ov.rec_rect.bottom(), "panel not under the area"
        # Drag the bottom-right handle, then move the whole area.
        assert ov._handle_at(QPointF(300, 200)) == "br" and ov._handle_at(QPointF(180, 125)) == "move"
        mouse(ov, "press", 300, 200)
        mouse(ov, "move", 340, 230)
        mouse(ov, "release", 340, 230)
        assert ov.rec_rect == QRectF(60, 50, 280, 180), ov.rec_rect
        mouse(ov, "press", 100, 100)
        mouse(ov, "move", 110, 90)
        mouse(ov, "release", 110, 90)
        assert ov.rec_rect == QRectF(70, 40, 280, 180), ov.rec_rect

        # Options: remembered in the settings for next time.
        ov.panel.cursor.click()
        assert s.rec_opts.cursor is False and config.load().record_cursor is False
        ov.panel.fps.click()
        assert s.rec_opts.fps == 60 and config.load().record_fps == 60
        s.set_record_option(fps=30, cursor=True)
        if "gif" in {k for k, _ in screencast.formats_available()}:
            s.set_record_option(format="gif")
            assert not ov.panel.mic.isEnabled(), "GIFs have no sound"
            s.set_record_option(format="mp4")
        if out_dir:
            ov.cursor_pos = None
            ov.grab().save(str(Path(out_dir) / "overlay-record.png"))

        # Esc goes back to choosing; Enter with nothing chosen picks the whole screen.
        key(s, ov, Qt.Key.Key_Escape)
        assert ov.rec_rect is None and not ov.panel.isVisibleTo(ov) and not done
        key(s, ov, Qt.Key.Key_Return)
        assert ov.rec_rect == QRectF(ov.rect()), ov.rec_rect
        mouse(ov, "press", 60, 50)
        mouse(ov, "move", 300, 210)
        mouse(ov, "release", 300, 210)

        problem = screencast.problem("mp4")
        if problem is not None:
            # Without a recorder, Enter says what's missing instead of counting down.
            key(s, ov, Qt.Key.Key_Return)
            assert s.countdown is None and s.hint == problem and ov.rec_rect is not None, (s.countdown, s.hint)
            print(f"self-test: record tool ok; recording skipped ({problem})")
            s.cancel()
            return

        # Countdown: Esc cancels it, Enter starts it again.
        key(s, ov, Qt.Key.Key_Return)
        assert s.countdown == 1 and not ov.panel.isVisibleTo(ov) and not bar.isVisibleTo(ov)
        if out_dir:
            ov.grab().save(str(Path(out_dir) / "overlay-countdown.png"))
        key(s, ov, Qt.Key.Key_Escape)
        assert s.countdown is None and ov.rec_rect is not None and ov.panel.isVisibleTo(ov)

        key(s, ov, Qt.Key.Key_Return)
        assert wait(lambda: recording.current() is not None and recording.current().state == "recording"), \
            "recording never started"
        rec = recording.current()
        assert not s.overlays and rec.bar is not None and rec.bar.isVisible(), "no recording controls"
        if recording._can_place():  # (not on Wayland without KWin)
            assert len(rec.marks) == 4, "no corner marks around the area"
            area = rec.target.rect
            assert not any(m.mask().translated(m.pos()).intersects(area) for m in rec.marks), \
                "a corner mark covers the area"
            assert rec.bar.geometry().top() > rec.target.rect.bottom(), "bar over the recorded area"
        wait(lambda: False, 1.0)
        if out_dir:
            rec.bar.grab().save(str(Path(out_dir) / "recording-bar.png"))
        rec.toggle_pause()
        assert wait(lambda: rec.proc is None) and rec.state == "paused"
        paused_at = rec.elapsed_ms()
        wait(lambda: False, 0.4)
        assert rec.elapsed_ms() == paused_at, "the clock ran while paused"
        rec.toggle_pause()
        assert rec.state == "recording" and len(rec.segments) == 2
        wait(lambda: False, 0.8)
        rec.stop()
        assert wait(lambda: done, 60) and done == [0], done
        files = list(rec_dir.glob("Recording_*.mp4"))
        assert len(files) == 1 and files[0].stat().st_size > 1000, files
        seconds = screencast.duration(str(files[0]))
        assert seconds is None or seconds > 1.0, seconds
        assert recording.current() is None and not rec._tmp.exists()

        # Discard: nothing saved.
        done = []
        s = Session(replace_cfg(cfg, record_countdown=0), Request(record=True, scan=False),
                    Notifier(interactive=False), lambda code, holds: done.append(code))
        s.start()
        ov = s.overlays[0]
        ov.arm(QRectF(0, 0, 120, 90))
        key(s, ov, Qt.Key.Key_Return)
        assert wait(lambda: recording.current() is not None and recording.current().state == "recording")
        recording.current().discard()
        assert wait(lambda: done) and done == [1], done
        assert len(list(rec_dir.glob("Recording_*"))) == 1, "a discarded recording was saved"

        # A recorder that quits by itself: what it recorded is kept. One that
        # fails at once: its error is reported and nothing is saved.
        import signal

        def record_until(action, launch=None):
            done = []
            s = Session(replace_cfg(cfg, record_countdown=0), Request(record=True, scan=False),
                        Notifier(interactive=False), lambda code, holds: done.append(code))
            real_launch = screencast.launch
            if launch:
                screencast.launch = launch
            try:
                s.start()
                ov = s.overlays[0]
                ov.arm(QRectF(0, 0, 120, 90))
                key(s, ov, Qt.Key.Key_Return)
                assert wait(lambda: done or (recording.current() is not None and recording.current().proc))
                action(recording.current())
                assert wait(lambda: done, 60), "the recording never finished"
            finally:
                screencast.launch = real_launch
            return done[0]

        before = len(list(rec_dir.glob("Recording_*")))
        assert record_until(lambda rec: (wait(lambda: False, 1.5), os.killpg(rec.proc.pid, signal.SIGTERM))) == 0
        assert len(list(rec_dir.glob("Recording_*"))) == before + 1, "the recorded part was lost"
        failing = lambda *a, **k: screencast.Launch(["sh", "-c", "echo 'no such screen' >&2; exit 3"])
        assert record_until(lambda rec: None, launch=failing) == 2
        assert len(list(rec_dir.glob("Recording_*"))) == before + 1, "a failed recording was saved"

        # The tray takes the recording over (so screenshots still work meanwhile);
        # the tray icon, its menu and the shortcut stop it.
        from flatshot.tray import TrayApp

        config.Config(record_dir=str(rec_dir), notify=False, clipboard="none", record_countdown=0).save()
        tray = TrayApp(app)
        # A notification's Pin opens where the screenshot was taken, unless the
        # monitors have changed since.
        from flatshot import pin
        from flatshot.qt import QRect

        shot_file = rec_dir / "pin-me.png"
        img = QImage(40, 30, QImage.Format.Format_RGB32)
        img.fill(QColor("#3DB2FF"))
        img.save(str(shot_file))
        where = QRect(30, 40, 40, 30)
        tray._on_action("pin", shot_file, at=where, layout=pin.screen_layout())
        assert pin._open[-1].at == where, pin._open[-1].at
        tray._on_action("pin", shot_file, at=where, layout=(("gone", (0, 0, 640, 480), 1.0),))
        assert pin._open[-1].at is None, "pinned where it was although the monitors changed"
        assert pin._open[-1].size() == where.size(), "the pin lost the captured size"
        # The pin is as big as the area captured, whatever its pixels: KWin's
        # can be at another scale than the monitor the pin is on.
        where = QRect(30, 40, 32, 24)  # 40 x 30 pixels: taken at 1.25
        tray._on_action("pin", shot_file, at=where, layout=pin.screen_layout())
        assert pin._open[-1].size() == where.size(), (pin._open[-1].size(), where.size())
        for w in pin._open[-3:]:
            w.close()
        tray.record()
        assert wait(lambda: tray.session is not None and tray.session.overlays)
        ov = tray.session.overlays[0]
        ov.arm(QRectF(10, 10, 200, 120))
        key(tray.session, ov, Qt.Key.Key_Return)
        assert wait(lambda: tray.session is None and tray.recording is not None
                    and tray.recording.state == "recording"), "the tray didn't take the recording over"
        assert tray.menu_actions == {} or tray.menu_actions["record"].text().startswith("Stop recording")
        wait(lambda: False, 0.6)
        tray.record()  # the shortcut again: stop
        assert wait(lambda: tray.recording is None, 60), "the tray couldn't stop the recording"
        assert len(list(rec_dir.glob("Recording_*.mp4"))) == 3
    finally:
        capture.grab_desktop, windows.WindowFinder.supported = real_grab, real_supported
        if forced is None:
            os.environ.pop("FLATSHOT_RECORDER", None)
        else:
            os.environ["FLATSHOT_RECORDER"] = forced
        shutil.rmtree(rec_dir, ignore_errors=True)
    print("self-test: record tool, countdown, pause, stop and discard ok")


def _editor(app, tmp: Path, out_dir):
    """The annotation editor: a window per image, the drawing tools, moving
    and zooming the view, Save and Save as."""
    from flatshot import editor
    from flatshot.qt import QEvent, QKeyEvent, QMouseEvent, QWheelEvent, QPoint

    src = tmp / "edit-me.png"
    pic = QImage(300, 200, QImage.Format.Format_RGB32)
    pic.fill(QColor("#808080"))
    paint = QPainter(pic)
    for x in range(0, 300, 4):
        paint.fillRect(x, 150, 2, 50, QColor("#000000"))  # stripes along the bottom, for blur
    paint.end()
    pic.save(str(src))
    ed = editor.open_file(src)
    other = editor.open_file(src)
    assert ed is not None and other is not None and editor.open_count() == 2, "two editors at once"
    other.close()
    ed.resize(900, 600)
    app.processEvents()
    assert "region" not in ed.bar.tools and "record" not in ed.bar.tools and "blur" in ed.bar.tools
    canvas = ed.canvas
    k = ed.dpr

    def mouse(kind, at, button=Qt.MouseButton.LeftButton):
        pos = canvas.offset + at / k * canvas.zoom  # (picture pixels -> canvas)
        types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                 "release": QEvent.Type.MouseButtonRelease}
        canvas.event(QMouseEvent(types[kind], pos, canvas.mapToGlobal(pos), button,
                                 button if kind != "release" else Qt.MouseButton.NoButton,
                                 Qt.KeyboardModifier.NoModifier))

    def key(k, text="", mods=Qt.KeyboardModifier.NoModifier):
        ed.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods, text))

    # A box, drawn with the mouse; undo and redo it.
    key(Qt.Key.Key_B, "b")
    assert ed.tool == "rect"
    mouse("press", QPointF(20, 20))
    mouse("move", QPointF(120, 90))
    mouse("release", QPointF(120, 90))
    assert len(ed.annotations) == 1 and ed.dirty and ed.windowTitle().startswith("●"), ed.windowTitle()
    assert ed.bar.save_button.text == "Save" and ed.bar.save_button.strong
    width = ed.bar.save_button.width()
    key(Qt.Key.Key_Z, "", Qt.KeyboardModifier.ControlModifier)
    assert not ed.annotations
    assert not ed.dirty and not ed.windowTitle().startswith("●") and ed.bar.save_button.text == "Saved", \
        "undoing back to the saved picture still shows unsaved"
    key(Qt.Key.Key_Z, "", Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert len(ed.annotations) == 1
    # Text: click, type, Enter.
    key(Qt.Key.Key_T, "t")
    mouse("press", QPointF(150, 30))
    for ch in "hi":
        key(Qt.Key.Key_A, ch)
    key(Qt.Key.Key_Return)
    assert ed.text_edit is None and ed.annotations[-1].text == "hi"
    # Blur over the stripes.
    key(Qt.Key.Key_U, "u")
    mouse("press", QPointF(100, 155))
    mouse("move", QPointF(200, 195))
    mouse("release", QPointF(200, 195))
    # The view: Ctrl+scroll zooms around the pointer, scrolling moves it, Ctrl+0 fits.
    before = canvas.zoom
    at = QPointF(canvas.width() / 2, canvas.height() / 2)
    canvas.wheelEvent(QWheelEvent(at, canvas.mapToGlobal(at), QPoint(), QPoint(0, 240), Qt.MouseButton.NoButton,
                                  Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False))
    assert canvas.zoom > before * 1.2, (before, canvas.zoom)
    offset = QPointF(canvas.offset)
    canvas.wheelEvent(QWheelEvent(at, canvas.mapToGlobal(at), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                  Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
    assert canvas.offset.y() < offset.y(), "scrolling didn't move the picture"
    canvas.set_space(True)  # Space + drag pans
    offset = QPointF(canvas.offset)
    mouse("press", QPointF(50, 50))
    canvas.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, canvas._pan + QPointF(40, 0), QPointF(),
                                      Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier))
    mouse("release", QPointF(50, 50))
    canvas.set_space(False)
    assert abs(canvas.offset.x() - offset.x() - 40) < 0.01, (offset, canvas.offset)
    assert len(ed.annotations) == 3, "panning drew something"
    key(Qt.Key.Key_0, "", Qt.KeyboardModifier.ControlModifier)
    assert canvas.fitted and canvas.zoom <= 1.0
    # The eyedropper takes a colour from the picture.
    ed.start_eyedropper()
    mouse("press", QPointF(1, 160))  # a black stripe
    assert ed.custom_color.name() == "#000000" and not ed.eyedropper
    ed.close_picker()
    if out_dir:
        ed.grab().save(str(Path(out_dir) / "editor.png"))
    # Save writes back to the file; Save as to another, which Save then uses.
    key(Qt.Key.Key_S, "", Qt.KeyboardModifier.ControlModifier)
    assert not ed.dirty and not ed.windowTitle().startswith("●")
    assert ed.bar.save_button.text == "Saved" and not ed.bar.save_button.strong
    assert ed.bar.save_button.width() == width, "Save changed width (the toolbar shifts)"
    saved = QImage(str(src))
    k_px = round(20 * k)
    assert QColor(saved.pixel(k_px, k_px)) != QColor("#808080"), "the box isn't in the saved file"
    stripes = [QColor(saved.pixel(x, 180)).red() for x in range(round(110 * k), round(190 * k))]
    assert all(40 < v < 220 for v in stripes), "the blur isn't in the saved file"
    copy = tmp / "edited-copy.jpg"
    assert ed._save_to(copy) and copy.exists() and ed.path == copy
    ed.close()
    app.processEvents()
    assert editor.open_count() == 0
    print("self-test: annotation editor ok")


def _drawing_and_text(app, tmp: Path, desktop: QImage, out_dir):
    """Shift / Ctrl / Alt while drawing, the filled rectangle, editing text,
    undo and redo greyed out with nothing to do, the pointer shown or not,
    the editor's own "Save the changes?", Copy saying Copied, and the file
    dialog without a portal."""
    from flatshot import capture, config, editor, filechooser, shapes, windows
    from flatshot.notify import Notifier
    from flatshot.qt import QEvent, QFileDialog, QKeyEvent, QMouseEvent, QPixmap, QRect
    from flatshot.session import Request, Session

    red = QColor("#ff0000")
    # Shapes and the keys held while drawing them.
    box = shapes.create("rect", QPointF(100, 100), red, 1)
    box.extend(QPointF(140, 120))
    assert box.rect() == QRectF(100, 100, 40, 20), box.rect()
    box.extend(QPointF(140, 120), center=True)  # Ctrl: out from the middle
    assert box.rect() == QRectF(60, 80, 80, 40), box.rect()
    box.extend(QPointF(140, 130), constrain=True)  # Shift: square
    assert box.rect() == QRectF(100, 100, 40, 40), box.rect()
    box.extend(QPointF(150, 150), move=True)  # Alt: moved, the same size
    assert box.rect() == QRectF(110, 120, 40, 30), box.rect()
    box.extend(QPointF(160, 150))  # let go of Alt: grows from where it was moved to
    assert box.rect() == QRectF(110, 120, 50, 30), box.rect()
    line = shapes.create("line", QPointF(0, 0), red, 1)
    line.extend(QPointF(100, 8), constrain=True)
    assert abs(line.end.y()) < 0.01 and line.start == QPointF(0, 0), line.end
    solid = shapes.create("solid", QPointF(10, 10), red, 1)
    solid.extend(QPointF(30, 30))
    pic = QImage(50, 50, QImage.Format.Format_ARGB32_Premultiplied)
    pic.fill(QColor("#ffffff"))
    paint = QPainter(pic)
    solid.paint(paint, QPixmap())
    paint.end()
    assert QColor(pic.pixel(20, 20)) == red and QColor(pic.pixel(40, 40)) != red, "the filled box isn't filled"

    # Typing: the caret moves, Shift selects, undo while typing.
    def press(shape, k, text="", mods=Qt.KeyboardModifier.NoModifier):
        return shape.key(QKeyEvent(QEvent.Type.KeyPress, k, mods, text))

    t = shapes.Text(QPointF(10, 10), red, 1)
    for ch in "hello world":
        press(t, Qt.Key.Key_A, ch)
    press(t, Qt.Key.Key_Left, "", Qt.KeyboardModifier.ControlModifier)
    assert t.cursor == 6, t.cursor
    press(t, Qt.Key.Key_Home, "", Qt.KeyboardModifier.ShiftModifier)
    assert t.selected() == "hello ", t.selected()
    press(t, Qt.Key.Key_A, "X")
    assert t.text == "Xworld" and t.cursor == 1, t.text
    press(t, Qt.Key.Key_Return, "\r", Qt.KeyboardModifier.ShiftModifier)
    press(t, Qt.Key.Key_A, "y")
    assert t.text == "X\nyworld", t.text
    press(t, Qt.Key.Key_Up)
    assert t.cursor <= 1, t.cursor
    press(t, Qt.Key.Key_End, "", Qt.KeyboardModifier.ControlModifier)
    press(t, Qt.Key.Key_Backspace, "", Qt.KeyboardModifier.ControlModifier)
    assert t.text == "X\n", t.text  # (the whole word)
    assert t.undo_edit() and t.text == "X\nyworld", t.text
    assert t.undo_edit(redo=True) and t.text == "X\n"
    assert press(t, Qt.Key.Key_Z, "", Qt.KeyboardModifier.ControlModifier) is False, "Ctrl+Z is for the caller"
    assert press(t, Qt.Key.Key_Return, "\r") == "commit"
    assert t.contains(QPointF(12, 14)) and not t.contains(QPointF(400, 400))

    # Screenshots become RGB32 as they arrive: ARGB32 relabelled, others converted.
    for fmt in (QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied,
                QImage.Format.Format_RGBA8888, QImage.Format.Format_RGBX8888, QImage.Format.Format_RGB32):
        shot = QImage(40, 30, fmt)
        shot.fill(QColor(200, 100, 50))
        note = capture._opaque(shot)
        assert shot.format() == QImage.Format.Format_RGB32, (fmt, shot.format())
        assert QColor(shot.pixel(5, 5)) == QColor(200, 100, 50), (fmt, QColor(shot.pixel(5, 5)).name())
        assert bool(note) == (fmt != QImage.Format.Format_RGB32), (fmt, note)

    # Capturing: undo and redo greyed out with nothing to do; the pointer.
    with_pointer = desktop.copy()
    paint = QPainter(with_pointer)
    paint.fillRect(QRect(20, 20, 12, 18), red)
    paint.end()

    asked = []

    def grab(preferred="auto", pointer=False, also_pointer=None):
        asked.append((pointer, also_pointer is not None))
        if also_pointer is not None:
            also_pointer.append(with_pointer.copy())
        return (with_pointer if pointer else desktop).copy()

    real_grab, real_supported = capture.grab_desktop, windows.WindowFinder.supported
    capture.grab_desktop = grab
    windows.WindowFinder.supported = staticmethod(lambda: False)
    try:
        cfg = config.Config(save_dir=str(tmp / "p"), notify=False, clipboard="none", default_tool="rect",
                            dim_opacity=0, region_pointer="toggle")
        s = Session(cfg, Request(scan=False), Notifier(interactive=False), lambda *a: None)
        s.start()
        ov = s.overlays[0]
        ov.resize(ov.target_screen.geometry().size())
        app.processEvents()
        undo, redo = ov.toolbar.history_buttons
        assert not undo.isEnabled() and not redo.isEnabled(), "undo / redo usable with nothing to do"
        ov.commit(shapes.Counter(QPointF(300, 300), red, 1, 1))
        assert undo.isEnabled() and not redo.isEnabled()
        s.undo()
        assert not undo.isEnabled() and redo.isEnabled()
        s.redo()
        assert ov.toolbar.pointer_button.isVisibleTo(ov.toolbar) and not s.show_pointer
        dpr = ov.dpr()
        spot = QRectF(QPointF(24, 24) / dpr, QPointF(28, 30) / dpr)
        assert QColor(ov.render(spot).pixel(0, 0)) != red, "the pointer is in the picture before it's asked for"
        qt.QApplication.sendEvent(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier,
                                                "m"))
        assert s.show_pointer and ov.toolbar.pointer_button.toggled_on
        assert QColor(ov.render(spot).pixel(0, 0)) == red, "the pointer isn't in the picture"
        s.toggle_pointer()
        assert QColor(ov.render(spot).pixel(0, 0)) != red
        # Ctrl held while dragging a box: it grows from the press, both ways.
        mouse = lambda kind, at, mods=Qt.KeyboardModifier.NoModifier: qt.QApplication.sendEvent(  # noqa: E731
            ov, QMouseEvent(kind, QPointF(at), QPointF(ov.mapToGlobal(at)), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseButtonRelease
                            else Qt.MouseButton.NoButton, mods))
        mouse(QEvent.Type.MouseButtonPress, QPointF(400, 400))
        mouse(QEvent.Type.MouseMove, QPointF(450, 420), Qt.KeyboardModifier.ControlModifier)
        mouse(QEvent.Type.MouseButtonRelease, QPointF(450, 420), Qt.KeyboardModifier.ControlModifier)
        assert ov.annotations[-1].rect() == QRectF(350, 380, 100, 40), ov.annotations[-1].rect()
        # Text: click, type, then click it again to change it; undo brings the old one back.
        s.set_tool("text")
        mouse(QEvent.Type.MouseButtonPress, QPointF(200, 600))
        mouse(QEvent.Type.MouseButtonRelease, QPointF(200, 600))
        for ch in "abc":
            s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, ch))
        s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r"))
        first = ov.annotations[-1]
        assert first.text == "abc" and s.text_edit is None
        n = len(ov.annotations)
        mouse(QEvent.Type.MouseButtonPress, QPointF(203, 605))
        mouse(QEvent.Type.MouseButtonRelease, QPointF(203, 605))
        assert s.text_edit is not None and s.text_edit[1].replaces is first and first.hidden
        s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_End, Qt.KeyboardModifier.NoModifier, ""))
        s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "d"))
        s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, ""))
        assert s.text_edit is not None and s.text_edit[1].text == "abc", "Ctrl+Z while typing undoes typing"
        s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "!"))
        s.key(ov, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r"))
        assert len(ov.annotations) == n + 1 and ov.annotations[-1].text == "abc!" and first.hidden
        s.undo()
        assert not first.hidden and len(ov.annotations) == n, "undo didn't bring the old text back"
        if out_dir:
            ov.toolbar.place()
            ov.toolbar.show()
            ov.grab().save(str(Path(out_dir) / "overlay-text.png"))
        s.cancel()
        # --verbose: each step of a capture, timed, on stderr.
        import contextlib
        import io

        from flatshot import timing

        said = io.StringIO()
        timing.enabled = True
        try:
            with contextlib.redirect_stderr(said):
                cfg = config.Config(save_dir=str(tmp / "v"), notify=False, clipboard="none")
                s = Session(cfg, Request(scan=False), Notifier(interactive=False), lambda *a: None)
                s.start()
                s.capture(s.overlays[0], QRectF(10, 10, 100, 80))
                for _ in range(20):
                    app.processEvents()
        finally:
            timing.enabled = False
        said = said.getvalue()
        for step in ("region capture: screenshot", "overlay shown", "chosen", "drawn", "saved"):
            assert step in said and " ms" in said, (step, said)
        # The settings can make it always or never: one screenshot, no button.
        for mode, shown in (("hidden", False), ("shown", True)):
            asked.clear()
            cfg = config.Config(save_dir=str(tmp / "p"), notify=False, clipboard="none", region_pointer=mode)
            s = Session(cfg, Request(scan=False), Notifier(interactive=False), lambda *a: None)
            s.start()
            ov = s.overlays[0]
            assert asked == [(shown, False)], (mode, asked)
            assert s.pointer_image is None and not ov.toolbar.pointer_button.isVisibleTo(ov.toolbar), mode
            s.toggle_pointer()
            assert not s.show_pointer
            assert (QColor(ov.render(spot).pixel(0, 0)) == red) is shown, mode
            s.cancel()
    finally:
        capture.grab_desktop, windows.WindowFinder.supported = real_grab, real_supported

    # The editor: greyed undo, Copied, drawings past the picture hidden, its
    # own "Save the changes?".
    src = tmp / "ask.png"
    pic = QImage(200, 100, QImage.Format.Format_RGB32)
    pic.fill(QColor("#808080"))
    pic.save(str(src))
    ed = editor.open_file(src)
    ed.resize(800, 500)
    app.processEvents()
    assert not ed.bar.history[0].isEnabled() and not ed.bar.history[1].isEnabled()
    ed.commit(shapes.Counter(QPointF(20, 20), red, 1, 1))
    assert ed.bar.history[0].isEnabled() and not ed.bar.history[1].isEnabled()
    ed.bar.copied()
    assert ed.bar.copy_button.text == "Copied"
    outside = shapes.create("solid", QPointF(150, 50), red, 1)
    outside.extend(QPointF(400, 90))
    ed.commit(outside)
    canvas = ed.canvas
    canvas.fit()
    shown = canvas.grab().toImage()
    past = canvas.offset + QPointF(260, 70) * canvas.zoom
    inside = canvas.offset + QPointF(170, 70) * canvas.zoom
    dpr = shown.devicePixelRatio()
    assert QColor(shown.pixel((inside * dpr).toPoint())) == red
    assert QColor(shown.pixel((past * dpr).toPoint())) != red, "a drawing shows past the picture's edge"
    ed.close()
    app.processEvents()
    assert editor.open_count() == 1 and ed.ask_save.isVisible(), "closing with changes didn't ask"
    if out_dir:
        ed.grab().save(str(Path(out_dir) / "editor-ask.png"))
    ed.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert editor.open_count() == 1 and not ed.ask_save.isVisible()
    ed.close()
    ed.answer("discard")
    app.processEvents()
    assert editor.open_count() == 0, "Don't save didn't close"

    # No portal here (no session bus): Qt's dialog, without blocking.
    chosen = []
    filechooser.save_file(None, "Save as", tmp / "x.png", [("PNG", ["*.png"])], chosen.append)
    dialog = None
    end = time.monotonic() + 5
    while dialog is None and time.monotonic() < end:
        app.processEvents()
        dialog = next((d for d in filechooser._pending if isinstance(d, QFileDialog)), None)
    if filechooser.dbus.available() and dialog is None:
        print("self-test: (a file-chooser portal answered; Qt's dialog not checked)")
    else:
        assert dialog is not None, "no file dialog"
        dialog.fileSelected.emit(str(tmp / "chosen.png"))
        dialog.reject()
        app.processEvents()
        assert chosen and chosen[0].endswith("chosen.png"), chosen
    print("self-test: drawing keys, filled box, text editing, pointer, undo state, save prompt ok")


def replace_cfg(cfg, **changes):
    from dataclasses import replace

    return replace(cfg, **changes)


def _recording_crop(tmp: Path):
    """The GStreamer crop, with the stream as the desktop may really send it:
    the whole workspace of two monitors, and at a different size than
    expected. The video must be exactly the area, unstretched."""
    import shutil
    import subprocess

    from flatshot import screencast
    from flatshot.qt import QRect

    # Which part of the desktop a portal stream shows, however the desktop reports it.
    left, right = QRect(0, 0, 1280, 720), QRect(1280, 0, 1366, 768)  # 1920x1080 at 150 %, then 1366x768 at 100 %
    screens = [left, right]
    assert screencast.stream_shows((0, 0), (1280, 720), 1.5, screens) == left  # logical
    assert screencast.stream_shows((0, 0), (1920, 1080), 1.5, screens) == left  # device pixels
    assert screencast.stream_shows((1280, 0), (1366, 768), 1.0, screens) == right
    assert screencast.stream_shows((0, 0), (2646, 768), 1.5, screens) == QRect(0, 0, 2646, 768)  # the workspace
    assert screencast.stream_shows((5, 5), (100, 50), 1.0, screens) == QRect(5, 5, 100, 50)  # as reported

    class Hidpi:  # the left screen above
        def geometry(self):
            return left

        def devicePixelRatio(self):
            return 2.0  # what Qt may say for 150 %

        def name(self):
            return "eDP-1"

    target = screencast.Target(QRect(100, 100, 200, 100), Hidpi(), 1.5)
    assert target.crop_in(left) == ((1920, 1080), QRect(150, 150, 300, 150)), target.crop_in(left)
    # KDE sends the whole workspace at scale 1: crop in those pixels, not upscaled.
    assert target.crop_in(QRect(0, 0, 2646, 768), (2646, 768)) == ((2646, 768), QRect(100, 100, 200, 100))

    # What a measured stream shows, when the portal says nothing (KDE sharing the
    # workspace) or says something that doesn't fit. A reported recording:
    # HDMI-A-1 1920x1080 at 0,0 with a 1366x768 monitor beside it.
    hdmi, small = QRect(0, 0, 1920, 1080), QRect(1920, 0, 1366, 768)
    area = QRect(709, 425, 307, 138)
    shown = screencast.stream_measured((3286, 1080), None, area, 1.0, [hdmi, small])
    assert shown == QRect(0, 0, 3286, 1080), shown

    class Hdmi:
        def geometry(self):
            return hdmi

        def devicePixelRatio(self):
            return 1.0

        def name(self):
            return "HDMI-A-1"

    assert screencast.Target(area, Hdmi(), 1.0).crop_in(shown, (3286, 1080)) == ((3286, 1080), QRect(709, 425, 306, 138))
    assert screencast.stream_measured((1366, 768), None, area, 1.0, [hdmi, small]) == small  # (no area: refused)
    assert screencast.stream_measured((1920, 1080), None, area, 1.0, [hdmi, small]) == hdmi
    assert screencast.stream_measured((3840, 2160), None, area, 2.0, [hdmi, small]) == hdmi  # device pixels
    twin = QRect(1920, 0, 1920, 1080)  # identical monitors: the one with the area
    assert screencast.stream_measured((1920, 1080), None, QRect(2000, 10, 50, 50), 1.0, [hdmi, twin]) == twin
    # A reported monitor that the measured stream doesn't fit is corrected.
    assert screencast.stream_measured((3286, 1080), hdmi, area, 1.0, [hdmi, small]) == QRect(0, 0, 3286, 1080)
    assert screencast.stream_measured((1920, 1080), hdmi, area, 1.0, [hdmi, small]) == hdmi

    if not (screencast.have("gst-launch-1.0") and screencast.gst_has("videotestsrc") and screencast.have("ffmpeg")
            and screencast._gst_video_encoder("mp4") is not None):
        print("self-test: recording crop skipped (needs GStreamer and ffmpeg)")
        return

    class Screen:  # a 1920 x 1080 monitor at scale 1, left of a 1366-wide one
        def geometry(self):
            return QRect(0, 0, 1920, 1080)

        def devicePixelRatio(self):
            return 1.0

        def name(self):
            return "DP-1"

    workspace = QRect(0, 0, 1920 + 1366, 1080)
    area = QRect(400, 100, 298, 152)  # across the first colour bar's edge, at x = 3286 / 7 ≈ 469
    target = screencast.Target(area, Screen())
    frame, crop = target.crop_in(workspace)
    assert frame == (3286, 1080) and crop == QRect(400, 100, 298, 152), (frame, crop)
    for sent in ((3286, 1080), (1643, 540)):  # as expected, and at half the size
        out = tmp / f"crop-{sent[0]}.mp4"
        source = (f"videotestsrc num-buffers=15 pattern=smpte ! video/x-raw,width={sent[0]},height={sent[1]},"
                  f"framerate=30/1")
        pipeline = screencast.gst_pipeline(source, frame, crop, screencast.Options(format="mp4"), str(out))
        subprocess.run(["gst-launch-1.0", "-q"] + screencast._split(pipeline), check=True, timeout=60,
                       stdout=subprocess.DEVNULL)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,sample_aspect_ratio",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
        assert probe.split(",")[:2] == ["298", "152"] and probe.split(",")[2:] in ([], ["1:1"], ["N/A"]), probe
        png = tmp / f"crop-{sent[0]}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(out), "-frames:v", "1", str(png)], check=True)
        img = QImage(str(png))
        edge = 3286 / 7 - 400  # where the white bar turns yellow, in the video
        white, yellow = img.pixelColor(round(edge) - 12, 70), img.pixelColor(round(edge) + 12, 70)
        assert white.blue() > 150 and yellow.blue() < 90 and yellow.red() > 150, (sent, white.name(), yellow.name())
    shutil.rmtree(tmp / "crop", ignore_errors=True)
    print("self-test: recording crop of a two-monitor stream ok")


def _pointer_and_snapping(app, tmp: Path, desktop: QImage, out_dir):
    """The crosshair over the toolbar, snapping to edges, the magnifier's
    square on any colour, the rainbow, picking a colour, and escapes in
    folder and file names."""
    from flatshot import config, output, shapes, theme
    from flatshot.notify import Notifier
    from flatshot.qt import QEvent, QMouseEvent, QPoint
    from flatshot.session import Request, Session

    src = tmp / "desktop.png"

    def session(**cfg_changes):
        cfg = config.Config(save_dir=str(tmp / "x"), notify=False, clipboard="none", **cfg_changes)
        s = Session(cfg, Request(image=str(src), scan=False), Notifier(interactive=False), lambda *a: None)
        s.start()
        ov = s.overlays[0]
        ov.resize(ov.base.deviceIndependentSize().toSize())
        ov.toolbar.place()
        ov.toolbar.show()
        app.processEvents()
        return s, ov

    def move(widget, pos, mods=Qt.KeyboardModifier.NoModifier):
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, mods)
        qt.QApplication.sendEvent(widget, event)

    # Esc closes when it's released, not pressed (the release would go to the
    # window underneath); a release without a press here does nothing.
    closed = []
    cfg = config.Config(save_dir=str(tmp / "x"), notify=False, clipboard="none")
    s = Session(cfg, Request(image=str(src), scan=False), Notifier(interactive=False),
                lambda code, holds: closed.append(code))
    s.start()
    ov = s.overlays[0]

    def esc(kind):
        event = qt.QKeyEvent(kind, qt.keyval(Qt.Key.Key_Escape), Qt.KeyboardModifier.NoModifier)
        (s.key if kind == QEvent.Type.KeyPress else s.key_release)(ov, event)
        app.processEvents()

    esc(QEvent.Type.KeyRelease)
    assert not s.done, "a stray Esc release closed the overlay"
    esc(QEvent.Type.KeyPress)
    assert not s.done and s.overlays, "Esc closed on the way down"
    esc(QEvent.Type.KeyRelease)
    for _ in range(20):
        app.processEvents()
    assert s.done and closed == [1], closed

    # The crosshair keeps following the pointer over the toolbar's buttons.
    s, ov = session()
    button = ov.toolbar.tools["pen"]
    move(button, QPoint(5, 7))
    assert ov.cursor_pos == QPointF(button.mapTo(ov, QPoint(5, 7))), ov.cursor_pos
    move(button, QPoint(20, 9))
    assert ov.cursor_pos == QPointF(button.mapTo(ov, QPoint(20, 9))), ov.cursor_pos
    # A finger on a button isn't a pointer: the crosshair stays where it was.
    finger = qt.QPointingDevice("flatshot test touchscreen", 99, qt.QInputDevice.DeviceType.TouchScreen,
                                qt.QPointingDevice.PointerType.Finger, qt.QInputDevice.Capability.Position, 10, 0)
    pos = QPointF(30, 12)
    qt.QApplication.sendEvent(button, QMouseEvent(QEvent.Type.MouseMove, pos, QPointF(button.mapToGlobal(pos)),
                                                  Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                                  Qt.KeyboardModifier.NoModifier, finger))
    assert ov.cursor_pos == QPointF(button.mapTo(ov, QPoint(20, 9))), "a tap moved the crosshair"

    # Picking a colour from a tool without one goes back to the last drawing tool.
    s.set_tool("arrow")
    s.set_tool("region")
    s.set_color(3)
    assert s.tool == "arrow" and s.color_index == 3, s.tool
    s.set_tool("pixelate")
    s.set_color(1)
    assert s.tool == "arrow", s.tool
    s.cancel()

    # Snapping: off by default and free; on, a selection's corner jumps to
    # the window edge (the light window starts at 120, 140 on the fake desktop).
    s, ov = session()
    dpr = ov.dpr()

    def at(x, y):  # image pixels -> overlay coordinates
        return QPointF(x / dpr, y / dpr)

    near = at(126, 640)
    assert ov._snapped(near, Qt.KeyboardModifier.NoModifier) == near and ov._edge_maps is None \
        and ov._edge_thread is None, "snapping off still did work"
    s.toggle_snap()
    assert s.snap_edges and config.load().snap_edges, "the snap toggle isn't remembered"
    assert ov.toolbar.snap_button.toggled_on
    started = time.perf_counter()
    assert ov._edge_thread is not None, "turning snapping on didn't start the edge maps"
    ov._edge_thread.join(10)
    built = time.perf_counter() - started
    assert ov._edge_maps is not None
    started = time.perf_counter()
    for _ in range(50):
        snapped = ov._snapped(near, Qt.KeyboardModifier.NoModifier)
    per_move = (time.perf_counter() - started) / 50
    assert snapped == at(120, 640), snapped
    assert ov._snapped(near, Qt.KeyboardModifier.ControlModifier) == near, "Ctrl should place freely"
    assert ov._snapped(at(500, 600), Qt.KeyboardModifier.NoModifier) == at(500, 600), "snapped with no edge near"
    # Boxes first. On a plain grey picture: a box (from 100, 80), a divider
    # line across the picture at y 86, and a short, strong stroke like a letter's.
    pic = QImage(400, 300, QImage.Format.Format_RGB32)
    pic.fill(QColor("#808080"))
    paint = QPainter(pic)
    paint.fillRect(100, 80, 200, 2, QColor("#303030"))  # the box: top, left, right, bottom
    paint.fillRect(100, 80, 2, 140, QColor("#303030"))
    paint.fillRect(298, 80, 2, 140, QColor("#303030"))
    paint.fillRect(100, 218, 200, 2, QColor("#303030"))
    paint.fillRect(0, 86, 400, 1, QColor("#303030"))  # the divider, as strong as the box
    paint.fillRect(60, 160, 2, 12, QColor("#000000"))  # a letter's stroke
    paint.end()
    pic_src = tmp / "boxes.png"
    pic.save(str(pic_src))
    b = Session(config.Config(save_dir=str(tmp / "x"), notify=False, snap_edges=True),
                Request(image=str(pic_src), scan=False), Notifier(interactive=False), lambda *a: None)
    b.start()
    bov = b.overlays[0]
    bov.snap_changed()
    bov._edge_thread.join(10)
    k = bov.dpr()
    none = Qt.KeyboardModifier.NoModifier

    def snap(x, y):
        return bov._snapped(QPointF(x / k, y / k), none)

    # Near the box's corner the divider is closer, but the corner wins (the
    # border is 2 px: its outer or inner corner).
    corner = snap(103, 85)
    assert (round(corner.x() * k), round(corner.y() * k)) in ((100, 80), (102, 82)), corner
    # A short stroke alone (a letter) isn't an edge to snap to.
    assert snap(63, 166) == QPointF(63 / k, 166 / k), snap(63, 166)
    # Away from the box, the divider is still an edge.
    divider = snap(30, 89)
    assert divider.x() == 30 / k and round(divider.y() * k) in (86, 87), divider  # (its top or bottom)
    # The distance is a setting: at 2 px the corner is out of reach.
    b.cfg.snap_distance = 2
    assert snap(106, 75) == QPointF(106 / k, 75 / k), snap(106, 75)
    b.cancel()
    # A box with rounded corners (radius 16, as many windows and cards have):
    # its sides stop short of the corner, but near it the pointer still goes
    # to the corner, rather than to a divider that's closer.
    pic.fill(QColor("#808080"))
    paint = QPainter(pic)
    paint.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint.setPen(QPen(QColor("#303030"), 2))
    paint.drawRoundedRect(QRectF(101, 81, 198, 138), 16, 16)
    paint.fillRect(0, 86, 400, 1, QColor("#303030"))
    paint.fillRect(346, 246, 54, 2, QColor("#303030"))  # a corner's sides with no curve joining them
    paint.fillRect(330, 262, 2, 38, QColor("#303030"))
    paint.end()
    pic.save(str(pic_src))
    b = Session(config.Config(save_dir=str(tmp / "x"), notify=False, snap_edges=True),
                Request(image=str(pic_src), scan=False), Notifier(interactive=False), lambda *a: None)
    b.start()
    bov = b.overlays[0]
    bov.snap_changed()
    bov._edge_thread.join(10)
    for pointer, corners in (((104, 85), ((100, 80), (102, 82))), ((295, 215), ((300, 220), (298, 218)))):
        corner = snap(*pointer)
        assert (round(corner.x() * k), round(corner.y() * k)) in corners, (pointer, corner)
    # Two lines that stop short of each other without a curve between them
    # aren't a rounded corner.
    assert snap(333, 249) == QPointF(333 / k, 249 / k), snap(333, 249)
    b.cancel()

    # A whole drag: both corners land on the window's edges.
    ov.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, at(127, 147), at(127, 147),
                                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    ov.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, at(933, 693), at(933, 693),
                                  Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert ov.sel_rect == QRectF(at(120, 140), at(940, 700)), ov.sel_rect
    ov.cancel_gesture()
    s.toggle_snap()
    assert not config.load().snap_edges
    s.cancel()
    print(f"self-test: snapping ok (edge maps {built * 1000:.0f} ms once, {per_move * 1000:.2f} ms per move)")

    # The magnifier's square shows even on a pixel of its own colour.
    flat = QImage(400, 300, QImage.Format.Format_RGB32)
    flat.fill(theme.C.ACCENT)
    flat_src = tmp / "accent.png"
    flat.save(str(flat_src))
    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False), Request(image=str(flat_src), scan=False),
                Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    ov.resize(400, 300)
    ov.toolbar.hide()
    ov.cursor_pos = QPointF(60, 60)
    s.pointer_overlay = None
    shot = ov.grab().toImage()
    box = ov._loupe_box
    if out_dir:
        shot.copy(box.adjusted(-4, -4, 4, 40)).save(str(Path(out_dir) / "loupe-on-accent.png"))
    # Its outline is black or white, whichever stands out from the pixel.
    accent = theme.C.ACCENT.getRgb()[:3]
    outline = [(x, y) for x in range(box.left() + 14, box.right() - 14) for y in range(box.top() + 14, box.bottom() - 14)
               if sum(abs(a - b) for a, b in zip(QColor(shot.pixel(x, y)).getRgb()[:3], accent)) > 200]
    assert len(outline) >= 8, "the magnifier's square vanished on a pixel of its own colour"
    # An edge it snaps to beyond what the magnifier shows (either side): no line
    # for it, and no error.
    for far in (QPointF(60 + 35, 60 - 30), QPointF(60 - 35, 60 + 30)):
        ov._snap_point = far
        canvas = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(canvas)
        try:
            ov._paint_loupe(p, ov.cursor_pos)
        finally:
            p.end()
    ov._snap_point = None
    s.cancel()

    # The hint: in the middle of the screen, faded while the pointer is near
    # it, and gone with the setting off.
    def hint_shot(cursor, **cfg):
        s = Session(config.Config(save_dir=str(tmp / "x"), notify=False, show_loupe=False, show_crosshair=False,
                                  **cfg), Request(image=str(flat_src), scan=False), Notifier(interactive=False),
                    lambda *a: None)
        s.start()
        ov = s.overlays[0]
        ov.resize(400, 300)
        ov.toolbar.hide()
        s.pointer_overlay = ov
        ov.cursor_pos = cursor
        shot = ov.grab().toImage()
        s.cancel()
        return shot

    def at_middle(shot):
        return QColor(shot.pixel(shot.width() // 2 - 60, shot.height() // 2 - 9))

    off = at_middle(hint_shot(QPointF(10, 10), show_hint=False))
    far = at_middle(hint_shot(QPointF(10, 10)))
    near = at_middle(hint_shot(QPointF(200, 120)))
    diff = lambda a, b: sum(abs(x - y) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))  # noqa: E731
    assert diff(far, off) > 60, ("no hint in the middle", far.name(), off.name())
    assert 0 < diff(near, off) < diff(far, off) * 0.5, ("the hint doesn't fade near the pointer", near.name())

    # Code cards: none covers another, even for codes side by side; the
    # padding round a code follows its size; hovering a code puts its card
    # on top.
    from flatshot.overlay import _code_box
    from flatshot.scanner import Code

    def square(x, y, side):
        return [QPointF(x, y), QPointF(x + side, y), QPointF(x + side, y + side), QPointF(x, y + side)]

    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False), Request(image=str(flat_src), scan=False),
                Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    ov.resize(400, 300)
    codes = [Code("https://example.com/one", "QRCode", square(100, 100, 60)),
             Code("https://example.com/two", "QRCode", square(110, 108, 60)),
             Code("tiny", "QRCode", square(300, 40, 12))]
    ov.set_codes(codes)
    s.refresh()
    cards = [chip.geometry() for chip in ov.chips]
    assert len(cards) == 3 and all(not a.intersects(b) for i, a in enumerate(cards) for b in cards[i + 1:]), cards
    big, tiny = _code_box(ov.codes[0][1]), _code_box(ov.codes[2][1])
    assert big.width() - 60 > tiny.width() - 12, "the padding doesn't follow the code's size"
    first = ov.chips[0]
    on_top = lambda w: ov.children().index(w) > max(ov.children().index(c) for c in ov.chips if c is not w)  # noqa: E731
    ov.cursor_pos = QPointF(102, 102)  # on the first code's box only
    ov._update_hover()
    assert on_top(first), "hovering a code didn't put its card on top"
    ov.cursor_pos = QPointF(ov.chips[1].geometry().center())
    ov._update_hover()
    assert on_top(ov.chips[1]), "hovering a card didn't put it on top"
    s.cancel()

    # Memory: once a capture is done, nothing the session keeps (a
    # notification's buttons can keep it for days) holds the pictures.
    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False, clipboard="none", snap_edges=True),
                Request(image=str(flat_src), scan=False), Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    ov.snap_changed()
    ov._edge_thread.join(10)
    shape = shapes.create("rect", QPointF(20, 20), s.color, 1)
    shape.extend(QPointF(120, 90), False)
    ov.commit(shape)
    s.capture(ov, QRectF(10, 10, 200, 150))
    app.processEvents()
    assert ov.base.isNull() and ov._pixels is None and ov._edge_maps is None and not ov.annotations, \
        "a closed overlay still holds its pictures"
    assert not s.history and not s.overlays and s.pointer_overlay is None, (s.history, s.overlays, s.pointer_overlay)

    # Blur: fine detail under it is smoothed away; nothing beside it changes.
    stripes = QImage(400, 300, QImage.Format.Format_RGB32)
    stripes.fill(QColor("#FFFFFF"))
    paint = QPainter(stripes)
    for x in range(0, 400, 4):
        paint.fillRect(x, 0, 2, 300, QColor("#000000"))
    paint.end()
    stripes_src = tmp / "stripes.png"
    stripes.save(str(stripes_src))
    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False), Request(image=str(stripes_src), scan=False),
                Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    s.set_tool("blur")
    assert s.tool == "blur" and "blur" in ov.toolbar.tools
    blur = shapes.create("blur", QPointF(100, 100), s.color, 0)
    blur.extend(QPointF(300, 200), False)
    ov.commit(blur)
    started = time.perf_counter()
    out = ov.render(None)
    took = time.perf_counter() - started
    inside = [QColor(out.pixel(x, 150)).red() for x in range(110, 290)]
    assert all(70 < v < 190 for v in inside), (min(inside), max(inside))
    assert {QColor(out.pixel(x, 50)).red() for x in range(100, 300)} == {0, 255}
    assert {QColor(out.pixel(x, 250)).red() for x in range(100, 300)} == {0, 255}
    s.cancel()
    print(f"self-test: blur ok ({took * 1000:.1f} ms)")

    # Your own colour: key 8 picks it; the picker changes it (dragging,
    # typing hex), Enter in the hex field doesn't capture, Esc closes just
    # the picker, the eyedropper takes a pixel, and it's remembered.
    from flatshot.qt import QKeyEvent
    from flatshot.session import CUSTOM

    def press(widget, k, text=""):
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            qt.QApplication.sendEvent(widget, QKeyEvent(kind, k, Qt.KeyboardModifier.NoModifier, text))

    def click(widget, pos):
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            qt.QApplication.sendEvent(widget, QMouseEvent(kind, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False), Request(image=str(stripes_src), scan=False),
                Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    press(ov, Qt.Key.Key_8, "8")
    assert s.color_index == CUSTOM and s.color == QColor(config.load().custom_color), s.color.name()
    s.set_tool("region")
    ov.toolbar.custom.click()
    assert ov.picker_open() and s.tool == s.colour_tool, "the custom swatch opens the picker"
    picker = ov.picker
    r = picker.SHADES
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
        pos = QPointF(r.right(), r.top()) if kind != QEvent.Type.MouseButtonPress else QPointF(r.center())
        picker.event(QMouseEvent(kind, pos, picker.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert s.custom_color.saturationF() > 0.95 and s.custom_color.valueF() > 0.95, s.custom_color.name()
    picker.hex.setText("22aa77")
    picker.hex.textEdited.emit("22aa77")  # (the # is optional)
    assert s.custom_color.name().upper() == "#22AA77", s.custom_color.name()
    # Enter in the hex field: it doesn't reach the overlay (which would
    # capture), and closes the picker. (Sent to the picker, not the field:
    # Qt 6.4 crashes on a key sent to a text field on a compositor with no
    # keyboard, as in the headless test.)
    press(picker, Qt.Key.Key_Return)
    assert not s.done, "Enter in the picker captured"
    picker.hex.returnPressed.emit()
    app.processEvents()
    assert not s.done and not ov.picker_open(), "Enter in the hex field captured, or left the picker open"
    assert config.load().custom_color == "#22AA77", "your colour wasn't remembered"
    ov.toolbar.custom.click()
    assert ov.picker_open()
    click(ov.toolbar.tools["pen"], QPointF(5, 5))  # a click anywhere else closes it
    assert not ov.picker_open(), "a click on the toolbar left the picker open"
    ov.toolbar.custom.click()
    click(picker, QPointF(picker.SHADES.center()))  # (but not a click in it)
    assert ov.picker_open()
    ov.toolbar.move(ov.toolbar.pos() + QPoint(-40, 60))  # dragging the toolbar takes the picker along
    assert abs(picker.y() - (ov.toolbar.geometry().bottom() + 8)) <= 1, (picker.y(), ov.toolbar.geometry())
    s.start_eyedropper()
    assert s.eyedropper and ov.cursor().shape() == Qt.CursorShape.CrossCursor
    click(ov, QPointF(0.5, 40.5))  # a black stripe
    assert s.custom_color.name() == "#000000" and not s.eyedropper and ov.picker_open(), s.custom_color.name()
    shape = shapes.create("pen", QPointF(5, 5), s.color, 1)
    assert shape.color.name() == "#000000"
    press(ov, Qt.Key.Key_Escape)  # Esc isn't eaten by the picker: it closes it and cancels as ever
    assert s.done and not ov.picker_open(), "Esc only closed the picker"
    print("self-test: your own colour ok")

    # Keys in Flatshot's windows can be changed: the new key works, the old
    # one doesn't, and the toolbar's tips say so.
    from flatshot import keys as keymod

    s = Session(config.Config(save_dir=str(tmp / "x"), notify=False, keys={"tool.pen": "J", "tool.line": "",
                                                                             "undo": "Alt+U"}),
                Request(image=str(flat_src), scan=False), Notifier(interactive=False), lambda *a: None)
    s.start()
    ov = s.overlays[0]
    press(ov, Qt.Key.Key_J, "j")
    assert s.tool == "pen", s.tool
    press(ov, Qt.Key.Key_L, "l")
    assert s.tool == "pen", "an unset key still works"
    press(ov, Qt.Key.Key_P, "p")
    assert s.tool == "pen"
    s.set_tool("rect")
    press(ov, Qt.Key.Key_P, "p")
    assert s.tool == "rect", "the old key still works"
    assert ov.toolbar.tools["pen"].hint.endswith("J"), ov.toolbar.tools["pen"].hint
    assert ov.toolbar.tools["line"].hint == "Line", ov.toolbar.tools["line"].hint
    km = keymod.Keymap({"redo": "Ctrl+Shift+Z"})
    shift_z = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.ShiftModifier, "Z")
    assert km.action(shift_z, keymod.CAPTURE) == "redo"
    shifted_bracket = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_BracketRight, Qt.KeyboardModifier.ShiftModifier, "]")
    assert km.action(shifted_bracket, keymod.EDITOR) == "size.up", "a key that needs Shift to type it"
    save = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier, "")
    assert km.action(save, keymod.EDITOR) == "save" and km.action(save, keymod.CAPTURE) is None
    s.cancel()
    from flatshot.settings import SettingsWindow

    settings = SettingsWindow(config.load(), shortcuts=None)
    settings._assign_key("tool.pen", "B")  # Rectangle's
    assert settings.key_rows["tool.pen"].error.isVisibleTo(settings) or "already" in \
        settings.key_rows["tool.pen"].error.text(), "a clash wasn't caught"
    settings._assign_key("save", "B")  # only in the editor, and B draws boxes there too
    assert "already" in settings.key_rows["save"].error.text()
    settings._assign_key("codes", "J")
    assert config.load().keys.get("codes") == "J"
    settings._reset_keys()
    assert config.load().keys == {}
    settings.close()

    # Rainbow: the crosshair's colour moves on its own, and only with the setting.
    s, ov = session(rainbow=True)
    assert ov._rainbow is not None and ov._rainbow.isActive()
    first = ov._mark_color()
    time.sleep(0.3)
    assert ov._mark_color() != first, "the rainbow doesn't move"
    s.cancel()
    s, ov = session()
    assert ov._rainbow is None and ov._mark_color() == theme.C.ACCENT
    s.cancel()

    # Folder and file names: codes in both, and escapes for literal %, { and }.
    cfg = config.Config(save_dir=str(tmp / "esc" / "%Y" / "{app} {{raw}}"), filename="{{n}} {n:2} 100%%")
    path = output.target_path(cfg, output.Shot(app="kate"), (1, 1), preview=True)
    assert path == tmp / "esc" / time.strftime("%Y") / "kate {raw}" / f"{{n}} {config.load_state().get('counter', 0) + 1:02} 100%.png", path
    assert output.base_folder(cfg) == tmp / "esc"
    print("self-test: crosshair over buttons, colours, magnifier square, rainbow and name escapes ok")
