"""Hand-drawn line icons on a 24x24 grid, so no icon theme is involved."""

import math

from flatshot.qt import QColor, QPainter, QPainterPath, QPen, QPixmap, QPointF, QRectF, Qt


def _path(*segments):
    """Build a path from lists of points; each list is one open polyline."""
    path = QPainterPath()
    for points in segments:
        path.moveTo(*points[0])
        for pt in points[1:]:
            path.lineTo(*pt)
    return path


def _region(p, c):
    p.drawPath(_path(
        [(4, 9), (4, 4), (9, 4)], [(15, 4), (20, 4), (20, 9)],
        [(20, 15), (20, 20), (15, 20)], [(9, 20), (4, 20), (4, 15)],
        [(12, 9.5), (12, 14.5)], [(9.5, 12), (14.5, 12)],
    ))


def _select(p, c):
    arrow = _path([(6, 3.5), (6, 18), (10, 14.5), (12.7, 20), (15.2, 18.9), (12.5, 13.4), (17.5, 13.2)])
    arrow.closeSubpath()
    p.drawPath(arrow)


def _pen(p, c):
    body = _path([(4, 20), (5, 15.5), (15.5, 5), (19, 8.5), (8.5, 19)])
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(13, 7.5), QPointF(16.5, 11))


def _line(p, c):
    p.drawLine(QPointF(5, 19), QPointF(19, 5))


def _arrow(p, c):
    p.drawPath(_path([(5, 19), (18, 6)], [(10, 6), (18, 6), (18, 14)]))


def _rect(p, c):
    p.drawRoundedRect(QRectF(4, 6, 16, 12), 2.5, 2.5)


def _solid(p, c):
    p.setBrush(c)
    p.drawRoundedRect(QRectF(4, 6, 16, 12), 2.5, 2.5)


def _ellipse(p, c):
    p.drawEllipse(QRectF(4, 6, 16, 12))


def _marker(p, c):
    body = _path([(9, 14), (16, 4.5), (20, 7.5), (13, 17)])
    body.closeSubpath()
    p.drawPath(body)
    p.drawPath(_path([(9, 14), (7.5, 17.5), (11, 17.5), (13, 17)]))
    bar = QColor(c)
    bar.setAlphaF(0.55)
    p.fillRect(QRectF(4, 19.5, 16, 2), bar)


def _text(p, c):
    p.drawPath(_path([(6, 8), (6, 5.5), (18, 5.5), (18, 8)], [(12, 5.5), (12, 19)], [(9.5, 19), (14.5, 19)]))


def _pixelate(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(3):
        for j in range(3):
            cell = QColor(c)
            cell.setAlphaF(1.0 if (i + j) % 2 == 0 else 0.35)
            p.setBrush(cell)
            p.drawRoundedRect(QRectF(4 + i * 5.6, 4 + j * 5.6, 4.6, 4.6), 1, 1)


def _blur(p, c):
    # Dots fading out from the middle, as a blur spreads a point.
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(4):
        for j in range(4):
            d = abs(i - 1.5) + abs(j - 1.5)  # 1 in the middle, 3 at the corners
            dot = QColor(c)
            dot.setAlphaF((1.0, 0.7, 0.4)[round(d) - 1])
            p.setBrush(dot)
            r = (2.1, 1.6, 1.1)[round(d) - 1]
            p.drawEllipse(QPointF(5.5 + i * 4.33, 5.5 + j * 4.33), r, r)


def _eyedropper(p, c):
    # A pipette, tip down at the left: the bulb, its collar, and the tube.
    p.drawPath(_path([(4, 20), (5.5, 18.5)], [(5.5, 18.5), (5.5, 16), (13, 8.5)], [(5.5, 18.5), (8, 18.5), (15.5, 11)],
                     [(11.5, 7), (17, 12.5)]))
    p.save()
    p.translate(16.6, 7.4)
    p.rotate(45)
    p.drawRoundedRect(QRectF(-2.6, -4.2, 5.2, 6.4), 2.6, 2.6)
    p.restore()


def _save(p, c):
    # Down into a tray.
    p.drawPath(_path([(12, 4), (12, 14)], [(7.5, 9.5), (12, 14), (16.5, 9.5)], [(4, 14), (4, 19.5), (20, 19.5), (20, 14)]))


def _save_as(p, c):
    # A pencil writing into a tray.
    p.drawPath(_path([(4, 14), (4, 19.5), (20, 19.5), (20, 14)]))
    body = _path([(8, 15.5), (8.7, 12.4), (15.6, 5.5), (18.5, 8.4), (11.6, 15.3)])
    body.closeSubpath()
    p.drawPath(body)


def _fit(p, c):
    # A magnifier with a picture in its lens: zoom to fit (not full screen).
    p.drawEllipse(QRectF(3.5, 3.5, 13, 13))
    p.drawLine(QPointF(14.6, 14.6), QPointF(20.5, 20.5))
    p.drawRoundedRect(QRectF(6.8, 7.6, 6.4, 4.8), 1, 1)


def _counter(p, c):
    p.drawEllipse(QRectF(4, 4, 16, 16))
    p.drawPath(_path([(10.5, 9.5), (12.5, 8), (12.5, 16)]))


def _undo(p, c):
    path = _path([(9, 6), (5, 10), (9, 14)])
    path.moveTo(5, 10)
    path.lineTo(14.5, 10)
    path.cubicTo(20.5, 10, 20.5, 19, 14.5, 19)
    path.lineTo(10, 19)
    p.drawPath(path)


def _redo(p, c):
    p.translate(24, 0)
    p.scale(-1, 1)
    _undo(p, c)


def _screen(p, c):
    p.drawRoundedRect(QRectF(3, 4, 18, 12), 2.5, 2.5)
    p.drawPath(_path([(12, 16), (12, 20)], [(8, 20), (16, 20)]))


def _copy(p, c):
    p.drawRoundedRect(QRectF(9, 9, 11, 11), 2.5, 2.5)
    path = QPainterPath()
    path.moveTo(5, 15)
    path.lineTo(5, 6.5)
    path.quadTo(5, 5, 6.5, 5)
    path.lineTo(15, 5)
    p.drawPath(path)


def _open(p, c):
    p.drawPath(_path([(10, 5), (5.5, 5), (5, 5.5), (5, 18.5), (5.5, 19), (18.5, 19), (19, 18.5), (19, 14)],
                     [(13, 5), (19, 5), (19, 11)], [(19, 5), (11, 13)]))


def _close(p, c):
    p.drawPath(_path([(6.5, 6.5), (17.5, 17.5)], [(17.5, 6.5), (6.5, 17.5)]))


def _codes(p, c):
    for x, y in ((4, 4), (14, 4), (4, 14)):
        p.drawRoundedRect(QRectF(x, y, 6, 6), 1.5, 1.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    for x, y in ((15.5, 15.5), (18.5, 18.5), (15.5, 18.5), (18.5, 14), (6, 6), (16, 6), (6, 16)):
        p.drawRect(QRectF(x - 1.1, y - 1.1, 2.2, 2.2))


def _codes_off(p, c):
    faded = QColor(c)
    faded.setAlphaF(0.55)
    pen = p.pen()
    p.setPen(QPen(faded, pen.widthF()))
    _codes(p, faded)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(pen)
    p.drawLine(QPointF(4, 20), QPointF(20, 4))


def _pin(p, c):
    p.drawPath(_path([(10, 4.5), (10, 9.5), (7, 13.5), (17, 13.5), (14, 9.5), (14, 4.5)]))
    p.drawLine(QPointF(8.5, 4.5), QPointF(15.5, 4.5))
    p.drawLine(QPointF(12, 13.5), QPointF(12, 20))


def _check(p, c):
    p.drawPath(_path([(5, 12.5), (10, 17.5), (19, 7)]))


def _record(p, c):
    p.drawEllipse(QRectF(4, 4, 16, 16))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QRectF(8.4, 8.4, 7.2, 7.2))


def _mic(p, c):
    p.drawRoundedRect(QRectF(9, 3.5, 6, 11), 3, 3)
    path = QPainterPath()
    path.moveTo(5.5, 11.5)
    path.cubicTo(5.5, 20, 18.5, 20, 18.5, 11.5)
    p.drawPath(path)
    p.drawLine(QPointF(12, 17.8), QPointF(12, 20.5))


def _speaker(p, c):
    body = _path([(4, 9.5), (7.5, 9.5), (12, 5.5), (12, 18.5), (7.5, 14.5), (4, 14.5)])
    body.closeSubpath()
    p.drawPath(body)
    for r in (3.5, 6.5):
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(12 - r, 12 - r, 2 * r, 2 * r), 50)
        arc.arcTo(QRectF(12 - r, 12 - r, 2 * r, 2 * r), 50, -100)
        p.drawPath(arc)


def _cursor(p, c):
    body = _path([(6, 4), (18, 11), (12.5, 12.5), (10, 18)])
    body.closeSubpath()
    p.drawPath(body)


def _cursor_off(p, c):
    faded = QColor(c)
    faded.setAlphaF(0.55)
    pen = p.pen()
    p.setPen(QPen(faded, pen.widthF(), pen.style(), pen.capStyle(), pen.joinStyle()))
    _cursor(p, faded)
    p.setPen(pen)
    p.drawLine(QPointF(4, 20), QPointF(20, 4))


def _pause(p, c):
    p.drawLine(QPointF(9, 6.5), QPointF(9, 17.5))
    p.drawLine(QPointF(15, 6.5), QPointF(15, 17.5))


def _resume(p, c):
    body = _path([(8, 5.5), (18.5, 12), (8, 18.5)])
    body.closeSubpath()
    p.setBrush(c)
    p.drawPath(body)


def _stop(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(7, 7, 10, 10), 2, 2)


def _trash(p, c):
    p.drawPath(_path([(4.5, 7), (19.5, 7)], [(9.5, 7), (9.5, 4.5), (14.5, 4.5), (14.5, 7)],
                     [(6.5, 7), (7.5, 19.5), (16.5, 19.5), (17.5, 7)]))


def _magnet(p, c):
    # A horseshoe magnet, tilted: a U-shaped bar with its two poles marked off.
    p.save()
    p.translate(12, 12)
    p.rotate(45)
    p.translate(-12, -11.5)
    bar = QPainterPath()
    bar.moveTo(5, 3.5)
    bar.lineTo(5, 12)
    bar.arcTo(QRectF(5, 5, 14, 14), 180, 180)
    bar.lineTo(19, 3.5)
    bar.lineTo(14.5, 3.5)
    bar.lineTo(14.5, 12)
    bar.arcTo(QRectF(9.5, 9.5, 5, 5), 0, -180)
    bar.lineTo(9.5, 3.5)
    bar.closeSubpath()
    p.drawPath(bar)
    p.drawPath(_path([(5, 7.5), (9.5, 7.5)], [(14.5, 7.5), (19, 7.5)]))
    p.restore()


_ICONS = {
    "region": _region, "select": _select, "pen": _pen, "line": _line, "arrow": _arrow, "rect": _rect,
    "ellipse": _ellipse, "marker": _marker, "text": _text, "pixelate": _pixelate, "blur": _blur,
    "counter": _counter, "undo": _undo, "redo": _redo, "screen": _screen,
    "copy": _copy, "open": _open, "close": _close, "check": _check,
    "codes": _codes, "codes-off": _codes_off, "pin": _pin, "record": _record, "mic": _mic,
    "speaker": _speaker, "cursor": _cursor, "cursor-off": _cursor_off, "solid": _solid, "pause": _pause,
    "resume": _resume, "stop": _stop, "trash": _trash,
    "magnet": _magnet, "eyedropper": _eyedropper, "fit": _fit, "save": _save,
    "save-as": _save_as,
}


_cache: dict = {}


def paint(p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Draw icon ``name`` filling ``rect``. Each is drawn once per size,
    colour and screen scale, then copied: the toolbar repaints with every
    move of the crosshair behind it."""
    dpr = p.device().devicePixelRatioF() if p.device() is not None else 1.0
    fx, fy = rect.x() % 1, rect.y() % 1  # (drawn at the same fraction of a pixel as asked)
    key = (name, round(rect.width(), 2), round(rect.height(), 2), color.rgba(), round(fx, 2), round(fy, 2), dpr)
    icon = _cache.get(key)
    if icon is None:
        icon = QPixmap(max(1, math.ceil((rect.width() + 1) * dpr)), max(1, math.ceil((rect.height() + 1) * dpr)))
        icon.setDevicePixelRatio(dpr)
        icon.fill(Qt.GlobalColor.transparent)
        q = QPainter(icon)
        _draw(q, name, QRectF(fx, fy, rect.width(), rect.height()), color)
        q.end()
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = icon
    p.drawPixmap(QPointF(rect.x() - fx, rect.y() - fy), icon)


def _draw(p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.topLeft())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    pen = QPen(color, 1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _ICONS[name](p, color)
    p.restore()


# -- the tray icon ---------------------------------------------------------------

TRAY_STYLES = ("color", "theme", "white", "black", "auto")


def _desktop_is_dark() -> bool:
    """Whether the desktop uses a dark colour scheme, for the "auto" tray
    icon. Qt is told to ignore the desktop's palette, so this asks the
    style hints first, then KDE's own colours; unknown counts as dark (the
    usual panel)."""
    import configparser
    import os
    from pathlib import Path

    from flatshot.qt import QGuiApplication

    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", lambda: None)()
    if scheme is not None:
        name = getattr(scheme, "name", str(scheme))
        if name.endswith("Dark"):
            return True
        if name.endswith("Light"):
            return False
    home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    kde = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        kde.read(Path(home) / "kdeglobals")
        r, g, b = (int(v) for v in kde["Colors:Window"]["BackgroundNormal"].split(",")[:3])
    except (KeyError, ValueError, OSError, configparser.Error):
        return True
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128


def _draw_logo(p: QPainter, size: int, back: QColor | None, frame: QColor, dot: QColor) -> None:
    """The Flatshot logo (assets/flatshot.svg, a 256 grid) at ``size`` px.
    Without a background the marks fill the icon and are drawn bolder, as
    symbolic tray icons are."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if back is not None:
        p.scale(size / 256, size / 256)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(back)
        p.drawRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
        width = 18
    else:
        lo, hi = 44, 212  # the marks, with room for their stroke
        p.scale(size / (hi - lo), size / (hi - lo))
        p.translate(-lo, -lo)
        width = 24
    pen = QPen(frame, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(_path([(60, 104), (60, 60), (104, 60)], [(152, 60), (196, 60), (196, 104)],
                     [(196, 152), (196, 196), (152, 196)], [(104, 196), (60, 196), (60, 152)]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(dot)
    grow = 0 if back is not None else 6
    p.drawRoundedRect(QRectF(104 - grow, 104 - grow, 48 + 2 * grow, 48 + 2 * grow), 12, 12)
    p.restore()


def tray_icon(style: str):
    """The tray icon in ``style`` (TRAY_STYLES): the app's own colours, the
    theme's, or one plain colour on a transparent background."""
    from flatshot.qt import QIcon
    from flatshot.theme import C, ICON_PATH

    if style not in TRAY_STYLES or style == "color":
        return QIcon(ICON_PATH)
    if style == "auto":
        style = "white" if _desktop_is_dark() else "black"
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        if style == "theme":
            _draw_logo(p, size, C.BASE, C.ACCENT, C.CODE)
        else:
            ink = QColor("#FFFFFF" if style == "white" else "#000000")
            _draw_logo(p, size, None, ink, ink)
        p.end()
        icon.addPixmap(pm)
    return icon
