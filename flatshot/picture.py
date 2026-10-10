"""Changing the picture itself in the annotation editor: crop (with a margin
where the crop reaches past its edges), cut out a slice, rotate, flip and
resize. Each takes the picture (a QPixmap whose device pixel ratio says
how many pixels make a logical one) and gives a new one, plus how a point
on the old picture moves onto the new one (logical px): the drawings are
moved with it, and stay drawings."""

from flatshot.qt import QColor, QPainter, QPixmap, QPointF, QRect, QRectF, QSizeF, Qt, QTransform

from flatshot import shapes

RATIOS = {"free": None, "1:1": 1.0, "4:3": 4 / 3, "16:9": 16 / 9, "original": "original"}


def _new(width: int, height: int, dpr: float, fill: QColor | None = None) -> QPixmap:
    pm = QPixmap(max(1, width), max(1, height))
    pm.setDevicePixelRatio(dpr)
    pm.fill(fill if fill is not None else Qt.GlobalColor.transparent)
    return pm


def _px(base: QPixmap, r: QRectF) -> QRect:
    """A logical rect in the picture's pixels."""
    d = base.devicePixelRatio()
    return QRect(round(r.x() * d), round(r.y() * d), round(r.width() * d), round(r.height() * d))


def crop(base: QPixmap, rect: QRectF, margin: QColor | None = None):
    """``rect`` (logical) of the picture. Where it reaches past the
    picture's edges there's a margin: transparent, or ``margin``."""
    d = base.devicePixelRatio()
    px = _px(base, rect)
    out = _new(px.width(), px.height(), d, margin)
    p = QPainter(out)
    p.drawPixmap(QPointF(-px.x() / d, -px.y() / d), base)
    p.end()
    shift = QPointF(px.x() / d, px.y() / d)
    return out, lambda pt: pt - shift


def cut(base: QPixmap, start: float, end: float, rows: bool):
    """Cut out the band between ``start`` and ``end`` (logical): rows
    across the picture (``rows``), else columns, joining what's either side."""
    d = base.devicePixelRatio()
    size = base.deviceIndependentSize()
    lo, hi = sorted((start, end))
    full = size.height() if rows else size.width()
    lo, hi = max(0.0, lo), min(full, hi)
    a, b = round(lo * d), round(hi * d)
    gone = (b - a) / d
    if rows:
        out = _new(base.width(), base.height() - (b - a), d)
    else:
        out = _new(base.width() - (b - a), base.height(), d)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    if rows:
        p.drawPixmap(QPointF(0, 0), base, QRectF(0, 0, base.width(), a))
        p.drawPixmap(QPointF(0, a / d), base, QRectF(0, b, base.width(), base.height() - b))
    else:
        p.drawPixmap(QPointF(0, 0), base, QRectF(0, 0, a, base.height()))
        p.drawPixmap(QPointF(a / d, 0), base, QRectF(b, 0, base.width() - b, base.height()))
    p.end()

    def move(pt: QPointF) -> QPointF:
        v = pt.y() if rows else pt.x()
        v = v if v <= a / d else (a / d if v < b / d else v - gone)
        return QPointF(pt.x(), v) if rows else QPointF(v, pt.y())

    return out, move


def _transformed(base: QPixmap, t: QTransform, width: int, height: int):
    """The picture through ``t`` (in pixels), as a ``width`` × ``height`` one."""
    d = base.devicePixelRatio()
    image = base.toImage().transformed(t, Qt.TransformationMode.SmoothTransformation)
    out = QPixmap.fromImage(image.copy(0, 0, width, height))
    out.setDevicePixelRatio(d)
    return out


def rotate(base: QPixmap, clockwise: bool):
    """A quarter turn."""
    size = base.deviceIndependentSize()
    w, h = size.width(), size.height()
    out = _transformed(base, QTransform().rotate(90 if clockwise else -90), base.height(), base.width())
    if clockwise:
        return out, lambda pt: QPointF(h - pt.y(), pt.x())
    return out, lambda pt: QPointF(pt.y(), w - pt.x())


def flip(base: QPixmap, horizontal: bool):
    """Mirror left to right (``horizontal``) or top to bottom."""
    size = base.deviceIndependentSize()
    w, h = size.width(), size.height()
    t = QTransform().scale(-1, 1) if horizontal else QTransform().scale(1, -1)
    out = _transformed(base, t, base.width(), base.height())
    if horizontal:
        return out, lambda pt: QPointF(w - pt.x(), pt.y())
    return out, lambda pt: QPointF(pt.x(), h - pt.y())


def resize(base: QPixmap, width: int, height: int):
    """To ``width`` × ``height`` pixels."""
    d = base.devicePixelRatio()
    out = base.scaled(max(1, width), max(1, height), Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(d)
    sx, sy = out.width() / base.width(), out.height() / base.height()
    return out, lambda pt: QPointF(pt.x() * sx, pt.y() * sy)


def move_drawings(drawings: list, fn) -> list:
    """The drawings moved onto the changed picture (each a copy). Text and
    counters keep their size and reading direction: their middle moves."""
    out = []
    for s in drawings:
        if s.hidden or isinstance(s, (shapes.Removed, Step)):
            continue
        if isinstance(s, (shapes.Text, shapes.Counter)):
            c = s.bounds().center()
            moved = s.moved(fn(c) - c)
        else:
            moved = s.mapped(fn)
        moved.replaces = None  # (it stands on its own: undo puts back the picture and all of them)
        out.append(moved)
    return out


class Step(shapes.Shape):
    """A change to the picture, kept in the editor's list of drawings as an
    undo step: undoing it puts back the picture and the drawings as they
    were; redoing it, as they became."""

    resizable = False

    def __init__(self, what: str, before: tuple, after: tuple):
        super().__init__(QColor(0, 0, 0), 0)
        self.what = what  # (for the status line: "Cropped", "Rotated", ...)
        self.before, self.after = before, after  # (picture, drawings)

    def paint(self, p, base):
        pass

    def bounds(self):
        return QRectF()

    def contains(self, pos, slack=4.0):
        return False


def fit_ratio(rect: QRectF, ratio: float) -> QRectF:
    """The biggest ``ratio`` (width / height) box inside ``rect``, centred."""
    w, h = rect.width(), rect.height()
    if w <= 0 or h <= 0:
        return QRectF(rect)
    if w / h > ratio:
        w = h * ratio
    else:
        h = w / ratio
    out = QRectF(0, 0, w, h)
    out.moveCenter(rect.center())
    return out


def size_of(base: QPixmap) -> QSizeF:
    return base.deviceIndependentSize()
