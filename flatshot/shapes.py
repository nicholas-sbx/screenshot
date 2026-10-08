"""Annotations. Geometry is kept in the overlay's logical coordinates and
painted with whatever QPainter is handed in — the live overlay or the
high-DPI output image — so what you see is what gets saved."""

import math

from flatshot.qt import (
    QColor, QFont, QFontMetricsF, QLineF, QPainter, QPainterPath, QPen, QPixmap, QPointF, QPolygonF, QRect,
    QRectF, Qt,
)

from flatshot.theme import C, OUTLINE_DARK, OUTLINE_LIGHT, SIZES, font, is_light


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class Shape:
    def __init__(self, color: QColor, size: int):
        self.color = QColor(color)
        self.size = size

    @property
    def width(self) -> float:
        return SIZES[self.size]

    def extend(self, pos: QPointF, constrain: bool) -> None:
        pass

    def is_valid(self) -> bool:
        return True

    def paint(self, p: QPainter, base: QPixmap) -> None:
        raise NotImplementedError


class Stroke(Shape):
    """Freehand pen, or a translucent highlighter when ``marker`` is set."""

    def __init__(self, pos, color, size, marker=False):
        super().__init__(color, size)
        self.points = [QPointF(pos)]
        self.marker = marker

    def extend(self, pos, constrain):
        if QLineF(self.points[-1], pos).length() >= 1.0:
            self.points.append(QPointF(pos))

    def paint(self, p, base):
        color = QColor(self.color)
        width = self.width
        if self.marker:
            color.setAlpha(105)
            width = width * 3 + 10
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(color, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if len(self.points) == 1:
            p.drawPoint(self.points[0])
        else:
            # Smooth through midpoints with quadratic segments.
            path = QPainterPath(self.points[0])
            for a, b in zip(self.points[1:], self.points[2:]):
                path.quadTo(a, (a + b) / 2)
            path.lineTo(self.points[-1])
            p.drawPath(path)
        p.restore()


class Drag(Shape):
    """Any shape defined by a start and end point."""

    def __init__(self, pos, color, size):
        super().__init__(color, size)
        self.start = QPointF(pos)
        self.end = QPointF(pos)

    def extend(self, pos, constrain):
        self.end = QPointF(pos)
        if constrain:
            self._constrain()

    def _constrain(self):
        d = self.end - self.start
        side = max(abs(d.x()), abs(d.y()))
        self.end = self.start + QPointF(math.copysign(side, d.x() or 1), math.copysign(side, d.y() or 1))

    def rect(self) -> QRectF:
        return QRectF(self.start, self.end).normalized()

    def is_valid(self):
        return QLineF(self.start, self.end).length() > 2


class Line(Drag):
    def _constrain(self):
        # Snap to 45 degree steps.
        line = QLineF(self.start, self.end)
        line.setAngle(round(line.angle() / 45) * 45)
        self.end = line.p2()

    def paint(self, p, base):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(self.color, self.width))
        p.drawLine(self.start, self.end)
        p.restore()


class Arrow(Line):
    def paint(self, p, base):
        line = QLineF(self.start, self.end)
        length = line.length()
        if length < 1:
            return
        head = min(10 + self.width * 3.2, length * 0.6)
        unit = (self.end - self.start) / length
        normal = QPointF(-unit.y(), unit.x())
        back = self.end - unit * head
        tip = QPolygonF([self.end, back + normal * head * 0.55, back - normal * head * 0.55])
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(self.color, self.width))
        p.drawLine(self.start, self.end - unit * head * 0.7)
        p.setBrush(self.color)
        p.setPen(_pen(self.color, max(1.5, self.width * 0.6)))
        p.drawPolygon(tip)
        p.restore()


class Box(Drag):
    def paint(self, p, base):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(self.color, self.width))
        p.drawRoundedRect(self.rect(), 4, 4)
        p.restore()


class Ellipse(Drag):
    def paint(self, p, base):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(self.color, self.width))
        p.drawEllipse(self.rect())
        p.restore()


class Pixelate(Drag):
    """Mosaic over whatever is underneath in the original capture."""

    def __init__(self, pos, color, size):
        super().__init__(pos, color, size)
        self._cache = None

    def paint(self, p, base):
        r = self.rect()
        if r.width() < 2 or r.height() < 2:
            return
        dpr = base.devicePixelRatio()
        src = QRect(round(r.x() * dpr), round(r.y() * dpr), round(r.width() * dpr), round(r.height() * dpr))
        src = src.intersected(base.rect())
        if src.isEmpty():
            return
        key = (src.x(), src.y(), src.width(), src.height(), self.size)
        if self._cache is None or self._cache[0] != key:
            block = max(4, round((6 + self.size * 5) * dpr))
            crop = base.copy(src)
            crop.setDevicePixelRatio(1)
            small = crop.scaled(max(1, src.width() // block), max(1, src.height() // block),
                                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            big = small.scaled(src.width(), src.height(),
                               Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            self._cache = (key, big)
        big = self._cache[1]
        target = QRectF(src.x() / dpr, src.y() / dpr, src.width() / dpr, src.height() / dpr)
        p.drawPixmap(target, big, QRectF(big.rect()))


class Text(Shape):
    SIZES_PX = [18, 26, 38]

    def __init__(self, pos, color, size):
        super().__init__(color, size)
        self.pos = QPointF(pos)
        self.text = ""
        self.editing = True

    def is_valid(self):
        return bool(self.text.strip())

    def _font(self) -> QFont:
        return font(self.SIZES_PX[self.size], QFont.Weight.Bold)

    def paint(self, p, base):
        f = self._font()
        fm = QFontMetricsF(f)
        lines = self.text.split("\n")
        path = QPainterPath()
        for i, line in enumerate(lines):
            path.addText(self.pos + QPointF(0, fm.ascent() + i * fm.lineSpacing()), f, line)
        outline = QColor(OUTLINE_LIGHT if not is_light(self.color) else OUTLINE_DARK)
        outline.setAlpha(210)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(outline, 3.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawPath(path)
        if self.editing:
            x = self.pos.x() + fm.horizontalAdvance(lines[-1]) + 2
            y = self.pos.y() + (len(lines) - 1) * fm.lineSpacing()
            p.fillRect(QRectF(x, y, 2, fm.height()), C.ACCENT)
        p.restore()


class Counter(Shape):
    RADII = [11, 14, 18]

    def __init__(self, pos, color, size, number):
        super().__init__(color, size)
        self.pos = QPointF(pos)
        self.number = number

    def paint(self, p, base):
        r = self.RADII[self.size]
        box = QRectF(self.pos.x() - r, self.pos.y() - r, 2 * r, 2 * r)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(box)
        p.setPen(OUTLINE_DARK if is_light(self.color) else OUTLINE_LIGHT)
        p.setFont(font(round(r * 1.05), QFont.Weight.Bold))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(self.number))
        p.restore()


DRAG_TOOLS = {"line": Line, "arrow": Arrow, "rect": Box, "ellipse": Ellipse, "pixelate": Pixelate}


def create(tool: str, pos: QPointF, color: QColor, size: int) -> Shape | None:
    if tool == "pen":
        return Stroke(pos, color, size)
    if tool == "marker":
        return Stroke(pos, color, size, marker=True)
    if tool in DRAG_TOOLS:
        return DRAG_TOOLS[tool](pos, color, size)
    return None
