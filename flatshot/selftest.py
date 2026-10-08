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
    assert [w.title for w in found] == ["QR card", "Notes — Kate"], found
    ctl._windows_found(found)
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

    print(f"self-test ok ({app.platformName()}, Qt {qt.QT_VERSION}, {qt.BINDING})")
    return 0
