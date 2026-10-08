"""Hand-drawn line icons on a 24x24 grid, so no icon theme is involved."""

from flatshot.qt import QColor, QPainter, QPainterPath, QPen, QPointF, QRectF, Qt


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


def _check(p, c):
    p.drawPath(_path([(5, 12.5), (10, 17.5), (19, 7)]))


_ICONS = {
    "region": _region, "pen": _pen, "line": _line, "arrow": _arrow, "rect": _rect,
    "ellipse": _ellipse, "marker": _marker, "text": _text, "pixelate": _pixelate,
    "counter": _counter, "undo": _undo, "redo": _redo, "screen": _screen,
    "copy": _copy, "open": _open, "close": _close, "check": _check,
}


def paint(p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
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
