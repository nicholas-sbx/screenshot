"""Annotations. Geometry is kept in the overlay's logical coordinates and
painted with whatever QPainter is handed in — the live overlay or the
high-DPI output image — so what you see is what gets saved."""

import copy
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


def modifiers(mods) -> dict:
    """How the keys held while drawing change a shape, as for extend():
    Shift squares it (or snaps a line to 45°), Ctrl draws it out from its
    middle, Alt moves it without changing its size (as in Krita)."""
    return {"constrain": bool(mods & Qt.KeyboardModifier.ShiftModifier),
            "center": bool(mods & Qt.KeyboardModifier.ControlModifier),
            "move": bool(mods & Qt.KeyboardModifier.AltModifier)}


def _to_segment(pos: QPointF, a: QPointF, b: QPointF) -> float:
    """How far ``pos`` is from the segment a–b."""
    d = b - a
    length2 = d.x() ** 2 + d.y() ** 2
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, QPointF.dotProduct(pos - a, d) / length2))
    return QLineF(pos, a + d * t).length()


class Shape:
    """A drawing. A drawing that's moved, resized, restyled or removed is
    replaced by a copy (``replaces`` the one it changes, which is hidden
    meanwhile), so undo brings the old one back by dropping the copy."""

    hidden = False  # replaced by a changed copy, see ``replaces``
    replaces: "Shape | None" = None
    resizable = True  # (text and counters can only be moved)
    ends = False  # resized by its two ends (lines, arrows), not a box

    def __init__(self, color: QColor, size: int):
        self.color = QColor(color)
        self.size = size

    @property
    def width(self) -> float:
        return SIZES[self.size]

    def extend(self, pos: QPointF, constrain: bool = False, center: bool = False, move: bool = False) -> None:
        pass

    def is_valid(self) -> bool:
        return True

    def paint(self, p: QPainter, base: QPixmap) -> None:
        raise NotImplementedError

    # -- picking it up again (the select tool) -----------------------------------

    def bounds(self) -> QRectF:
        """The box the shape's geometry fills (without its stroke)."""
        raise NotImplementedError

    def contains(self, pos: QPointF, slack: float = 4.0) -> bool:
        """Is ``pos`` on the shape: within its stroke (and ``slack``) for
        lines and outlines, anywhere inside for filled ones."""
        return self.bounds().adjusted(-slack, -slack, slack, slack).contains(pos)

    def clone(self) -> "Shape":
        """A copy that replaces this one (see ``replaces``)."""
        c = copy.copy(self)
        c.color = QColor(self.color)
        c._own_points()
        c.replaces, c.hidden = self, False
        return c

    def _own_points(self) -> None:
        """Give a copy its own points (copy.copy shares them)."""

    def _map(self, fn) -> None:
        """Move each point through ``fn`` (QPointF -> QPointF)."""
        raise NotImplementedError

    def mapped(self, fn) -> "Shape":
        c = self.clone()
        c._map(fn)
        return c

    def moved(self, d: QPointF) -> "Shape":
        return self.mapped(lambda pt: pt + d)

    def fitted(self, old: QRectF, new: QRectF) -> "Shape":
        """A copy stretched from the box ``old`` to ``new``."""
        sx = new.width() / old.width() if old.width() else 1.0
        sy = new.height() / old.height() if old.height() else 1.0
        return self.mapped(lambda pt: QPointF(new.left() + (pt.x() - old.left()) * sx,
                                              new.top() + (pt.y() - old.top()) * sy))

    def restyled(self, color: QColor | None = None, size: int | None = None) -> "Shape":
        c = self.clone()
        if color is not None:
            c.color = QColor(color)
        if size is not None:
            c.size = size
        return c


class Removed(Shape):
    """A deleted drawing: nothing, in place of ``shape`` (hidden while this
    is there), so undo brings it back."""

    def __init__(self, shape: Shape):
        super().__init__(shape.color, shape.size)
        self.replaces = shape

    def paint(self, p, base):
        pass

    def bounds(self):
        return QRectF()

    def contains(self, pos, slack=4.0):
        return False


class Stroke(Shape):
    """Freehand pen, or a translucent highlighter when ``marker`` is set."""

    def __init__(self, pos, color, size, marker=False):
        super().__init__(color, size)
        self.points = [QPointF(pos)]
        self.marker = marker

    def extend(self, pos, constrain=False, center=False, move=False):
        if QLineF(self.points[-1], pos).length() >= 1.0:
            self.points.append(QPointF(pos))

    def _reach(self) -> float:
        return (self.width * 3 + 10 if self.marker else self.width) / 2

    def bounds(self):
        return QPolygonF(self.points).boundingRect()

    def contains(self, pos, slack=4.0):
        reach = self._reach() + slack
        if len(self.points) == 1:
            return QLineF(pos, self.points[0]).length() <= reach
        return any(_to_segment(pos, a, b) <= reach for a, b in zip(self.points, self.points[1:]))

    def _own_points(self):
        self.points = [QPointF(pt) for pt in self.points]

    def _map(self, fn):
        self.points = [fn(pt) for pt in self.points]

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
    """Any shape defined by a start and end point, dragged out from where
    the press was (the anchor)."""

    def __init__(self, pos, color, size):
        super().__init__(color, size)
        self.start = QPointF(pos)
        self.end = QPointF(pos)
        self.anchor = QPointF(pos)
        self.pointer = QPointF(pos)  # where the pointer was last

    def extend(self, pos, constrain=False, center=False, move=False):
        """``constrain``: square (Shift); ``center``: the anchor is the
        middle, not a corner (Ctrl); ``move``: the shape follows the pointer
        at its size (Alt), and goes on growing from there once let go."""
        pos = QPointF(pos)
        if move:
            self.anchor += pos - self.pointer
        self.pointer = pos
        d = pos - self.anchor
        if constrain:
            d = self._constrain(d)
        self.start = self.anchor - d if center else QPointF(self.anchor)
        self.end = self.anchor + d

    def _constrain(self, d: QPointF) -> QPointF:
        side = max(abs(d.x()), abs(d.y()))
        return QPointF(math.copysign(side, d.x() or 1), math.copysign(side, d.y() or 1))

    def rect(self) -> QRectF:
        return QRectF(self.start, self.end).normalized()

    def is_valid(self):
        return QLineF(self.start, self.end).length() > 2

    def bounds(self):
        return self.rect()

    def _own_points(self):
        self.start, self.end, self.anchor, self.pointer = (QPointF(self.start), QPointF(self.end),
                                                           QPointF(self.anchor), QPointF(self.pointer))
        if hasattr(self, "_cache"):
            self._cache = None

    def _map(self, fn):
        self.start, self.end = fn(self.start), fn(self.end)
        self.anchor, self.pointer = QPointF(self.start), QPointF(self.end)

    def with_end(self, which: int, pos: QPointF) -> "Drag":
        """A copy with its start (0) or end (1) at ``pos``."""
        c = self.clone()
        if which == 0:
            c.start = QPointF(pos)
        else:
            c.end = QPointF(pos)
        c.anchor, c.pointer = QPointF(c.start), QPointF(c.end)
        return c


class Line(Drag):
    ends = True

    def contains(self, pos, slack=4.0):
        return _to_segment(pos, self.start, self.end) <= self.width / 2 + slack

    def _constrain(self, d):
        # Snap to 45 degree steps.
        line = QLineF(QPointF(0, 0), d)
        line.setAngle(round(line.angle() / 45) * 45)
        return line.p2()

    def paint(self, p, base):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(self.color, self.width))
        p.drawLine(self.start, self.end)
        p.restore()


class Arrow(Line):
    def contains(self, pos, slack=4.0):
        head = min(10 + self.width * 3.2, QLineF(self.start, self.end).length() * 0.6)
        return super().contains(pos, slack) or QLineF(pos, self.end).length() <= head + slack

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


def _on_outline(pos: QPointF, r: QRectF, reach: float) -> bool:
    outer = r.adjusted(-reach, -reach, reach, reach)
    inner = r.adjusted(reach, reach, -reach, -reach)
    return outer.contains(pos) and not (inner.isValid() and inner.contains(pos))


class Box(Drag):
    def contains(self, pos, slack=4.0):
        return _on_outline(pos, self.rect(), self.width / 2 + slack)

    def paint(self, p, base):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_pen(self.color, self.width))
        p.drawRoundedRect(self.rect(), 4, 4)
        p.restore()


class SolidBox(Drag):
    """A filled rectangle: to cover something up, or to mark it boldly."""

    def paint(self, p, base):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(self.rect(), 3, 3)
        p.restore()


class Ellipse(Drag):
    def contains(self, pos, slack=4.0):
        r = self.rect()
        rx, ry = r.width() / 2, r.height() / 2
        if rx < 1 or ry < 1:
            return _on_outline(pos, r, self.width / 2 + slack)
        d = pos - r.center()
        # How far out along its own radius, times the radius there: near the line, about the distance to it.
        k = math.hypot(d.x() / rx, d.y() / ry)
        radius = math.hypot(d.x(), d.y()) / k if k else min(rx, ry)
        return abs(k - 1) * radius <= self.width / 2 + slack

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
        key = (src.x(), src.y(), src.width(), src.height(), self.size, base.cacheKey())
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, self._cover(base, src, dpr))
        big = self._cache[1]
        target = QRectF(src.x() / dpr, src.y() / dpr, src.width() / dpr, src.height() / dpr)
        p.drawPixmap(target, big, QRectF(big.rect()))

    def _cover(self, base: QPixmap, src: QRect, dpr: float) -> QPixmap:
        """What goes over ``src`` (pixels of ``base``)."""
        block = max(4, round((6 + self.size * 5) * dpr))
        crop = base.copy(src)
        crop.setDevicePixelRatio(1)
        small = _scaled(crop, src.width() // block, src.height() // block)
        return small.scaled(src.width(), src.height(),
                            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)


class Blur(Pixelate):
    """Whatever is underneath in the original capture, blurred past reading."""

    STRENGTH = [5, 8, 12]  # logical px averaged together, per size

    def _cover(self, base, src, dpr):
        # Shrinking averages the pixels; growing back smoothly blends them.
        # Two steps down and up again smooth out the joins. It takes a few
        # milliseconds even for a whole screen, so dragging stays smooth.
        k = max(2, round(self.STRENGTH[self.size] * dpr))
        around = src.adjusted(-2 * k, -2 * k, 2 * k, 2 * k).intersected(base.rect())  # blend in what's beside it
        crop = base.copy(around)
        crop.setDevicePixelRatio(1)
        small = _scaled(crop, around.width() // k, around.height() // k)
        softer = _scaled(_scaled(small, small.width() // 2, small.height() // 2), small.width(), small.height())
        big = _scaled(softer, around.width(), around.height())
        return big.copy(src.translated(-around.topLeft()))


def _scaled(pm: QPixmap, w: int, h: int) -> QPixmap:
    return pm.scaled(max(1, w), max(1, h), Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


class Text(Shape):
    """Text typed onto the picture, edited in place: a caret that moves
    (arrows, Home, End, Ctrl for whole words), Shift to select, Ctrl+A,
    cut, copy and paste, and its own undo while it's being typed."""

    SIZES_PX = [18, 26, 38]
    PAD = 6  # the edit box, around the text

    def __init__(self, pos, color, size):
        super().__init__(color, size)
        self.pos = QPointF(pos)
        self.text = ""
        self.editing = True
        self.cursor = 0  # where the caret is, in characters
        self.anchor = 0  # the other end of the selection (== cursor: none)
        self.replaces: "Text | None" = None  # the text this is an edited copy of
        self._undo: list[tuple[str, int]] = []
        self._redo: list[tuple[str, int]] = []
        self._typing = False  # the last change was typing (undone a word at a time)

    def is_valid(self):
        # An edited copy counts even when emptied: that deletes the text, undoably.
        return bool(self.text.strip()) or (self.replaces is not None and self.changed())

    def changed(self) -> bool:
        r = self.replaces
        return r is None or (self.text, self.pos, self.color, self.size) != (r.text, r.pos, r.color, r.size)

    def copy_for_editing(self) -> "Text":
        """A copy to edit in place of this one (hidden meanwhile), so undo
        can bring this one back."""
        t = Text(self.pos, self.color, self.size)
        t.text, t.replaces = self.text, self
        t.cursor = t.anchor = len(self.text)
        return t

    # -- geometry ------------------------------------------------------------

    def _font(self) -> QFont:
        return font(self.SIZES_PX[self.size], QFont.Weight.Bold)

    def _metrics(self) -> QFontMetricsF:
        return QFontMetricsF(self._font())

    def lines(self) -> list[str]:
        return self.text.split("\n")

    def _row_col(self, index: int) -> tuple[int, int]:
        before = self.text[:index].split("\n")
        return len(before) - 1, len(before[-1])

    def _index(self, row: int, col: int) -> int:
        lines = self.lines()
        row = min(max(row, 0), len(lines) - 1)
        return sum(len(line) + 1 for line in lines[:row]) + min(col, len(lines[row]))

    def _x_of(self, row: int, col: int) -> float:
        return self._metrics().horizontalAdvance(self.lines()[row][:col])

    def _col_at(self, row: int, x: float) -> int:
        """The column nearest to ``x`` (from the text's left) on ``row``."""
        fm, line = self._metrics(), self.lines()[row]
        best, best_d = 0, abs(x)
        for col in range(1, len(line) + 1):
            d = abs(fm.horizontalAdvance(line[:col]) - x)
            if d < best_d:
                best, best_d = col, d
        return best

    def index_at(self, pos: QPointF) -> int:
        fm = self._metrics()
        row = int((pos.y() - self.pos.y()) // fm.lineSpacing())
        row = min(max(row, 0), len(self.lines()) - 1)
        return self._index(row, self._col_at(row, pos.x() - self.pos.x()))

    def bounds(self) -> QRectF:
        """The text's box (at least a caret's width when empty)."""
        fm = self._metrics()
        lines = self.lines()
        w = max([fm.horizontalAdvance(line) for line in lines] + [2.0])
        h = fm.height() + (len(lines) - 1) * fm.lineSpacing()
        return QRectF(self.pos.x(), self.pos.y(), w, h)

    def contains(self, pos: QPointF, slack: float = PAD) -> bool:
        return self.bounds().adjusted(-slack, -slack, slack, slack).contains(pos)

    resizable = False

    def clone(self):
        c = super().clone()
        c.editing, c._undo, c._redo, c._typing = False, [], [], False
        return c

    def _own_points(self):
        self.pos = QPointF(self.pos)

    def _map(self, fn):
        self.pos = fn(self.pos)

    def caret_rect(self) -> QRectF:
        fm = self._metrics()
        row, col = self._row_col(self.cursor)
        return QRectF(self.pos.x() + self._x_of(row, col) - 1, self.pos.y() + row * fm.lineSpacing(), 2, fm.height())

    # -- editing ---------------------------------------------------------------

    def selection(self) -> tuple[int, int]:
        return min(self.cursor, self.anchor), max(self.cursor, self.anchor)

    def selected(self) -> str:
        a, b = self.selection()
        return self.text[a:b]

    def _clamp(self):
        n = len(self.text)
        self.cursor, self.anchor = min(max(self.cursor, 0), n), min(max(self.anchor, 0), n)

    def _snapshot(self, typing: bool = False):
        if not (typing and self._typing):
            self._undo.append((self.text, self.cursor))
            del self._undo[:-200]
        self._redo.clear()
        self._typing = typing

    def insert(self, s: str):
        if not s:
            return
        self._clamp()
        self._snapshot(typing=s not in (" ", "\n") and len(s) == 1)
        a, b = self.selection()
        self.text = self.text[:a] + s + self.text[b:]
        self.cursor = self.anchor = a + len(s)

    def _delete(self, a: int, b: int):
        if a == b:
            return
        self._snapshot()
        self.text = self.text[:a] + self.text[b:]
        self.cursor = self.anchor = a

    def place(self, index: int, select: bool = False):
        self._clamp()
        self.cursor = min(max(index, 0), len(self.text))
        if not select:
            self.anchor = self.cursor
        self._typing = False

    def _word_left(self, i: int) -> int:
        t = self.text
        while i > 0 and t[i - 1].isspace():
            i -= 1
        while i > 0 and not t[i - 1].isspace():
            i -= 1
        return i

    def _word_right(self, i: int) -> int:
        t, n = self.text, len(self.text)
        while i < n and t[i].isspace():
            i += 1
        while i < n and not t[i].isspace():
            i += 1
        return i

    def undo_edit(self, redo: bool = False) -> bool:
        """Undo (or redo) the last change to the text being typed; False if none."""
        source, target = (self._redo, self._undo) if redo else (self._undo, self._redo)
        if not source:
            return False
        target.append((self.text, self.cursor))
        self.text, self.cursor = source.pop()
        self.anchor = self.cursor
        self._typing = False
        return True

    def key(self, event) -> bool | str:
        """A key while typing: "commit" (Esc, Ctrl+Enter), True if it was used,
        False if it's for whoever else (a Ctrl shortcut the text has no use
        for: undo, save, ...)."""
        from flatshot.qt import QGuiApplication, keyval

        K = Qt.Key
        k = keyval(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        is_ = lambda *names: k in [keyval(getattr(K, f"Key_{n}")) for n in names]  # noqa: E731
        self._clamp()
        a, b = self.selection()
        row, col = self._row_col(self.cursor)
        if is_("Escape"):
            return "commit"
        if is_("Return", "Enter"):
            if ctrl:
                return "commit"  # (Ctrl+Enter finishes, as Esc or a click elsewhere does)
            self.insert("\n")
            return True
        if is_("Backspace"):
            self._delete(*((a, b) if a != b else (self._word_left(a) if ctrl else max(0, a - 1), a)))
            return True
        if is_("Delete"):
            self._delete(*((a, b) if a != b else (a, self._word_right(a) if ctrl else a + 1)))
            return True
        if is_("Left"):
            to = self._word_left(self.cursor) if ctrl else (a if a != b and not shift else self.cursor - 1)
            self.place(to, shift)
            return True
        if is_("Right"):
            to = self._word_right(self.cursor) if ctrl else (b if a != b and not shift else self.cursor + 1)
            self.place(to, shift)
            return True
        if is_("Home"):
            self.place(0 if ctrl else self._index(row, 0), shift)
            return True
        if is_("End"):
            self.place(len(self.text) if ctrl else self._index(row, len(self.lines()[row])), shift)
            return True
        if is_("Up", "Down"):
            target = row + (-1 if is_("Up") else 1)
            if 0 <= target < len(self.lines()):
                self.place(self._index(target, self._col_at(target, self._x_of(row, col))), shift)
            else:
                self.place(0 if target < 0 else len(self.text), shift)
            return True
        if ctrl and not alt:
            clipboard = QGuiApplication.clipboard()
            if is_("A"):
                self.anchor, self.cursor = 0, len(self.text)
                return True
            if is_("C") and a != b:
                clipboard.setText(self.selected())
                return True
            if is_("X") and a != b:
                clipboard.setText(self.selected())
                self._delete(a, b)
                return True
            if is_("V"):
                self.insert(clipboard.text().replace("\r\n", "\n").replace("\t", "    "))
                return True
            return False
        if is_("Tab"):
            return True  # (not to move the focus away)
        text = event.text()
        if text and text.isprintable():
            self.insert(text)
        return True  # (any other key does nothing while typing)

    # -- painting --------------------------------------------------------------

    def paint(self, p, base):
        f = self._font()
        fm = QFontMetricsF(f)
        lines = self.lines()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.editing:
            # The edit box, and what's selected.
            box = self.bounds().adjusted(-self.PAD, -self.PAD / 2, self.PAD, self.PAD / 2)
            edge = QColor(C.ACCENT)
            edge.setAlpha(170)
            pen = QPen(edge, 1.2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box, 4, 4)
            a, b = self.selection()
            if a != b:
                shade = QColor(C.ACCENT)
                shade.setAlpha(90)
                ra, ca = self._row_col(a)
                rb, cb = self._row_col(b)
                for r in range(ra, rb + 1):
                    x0 = self._x_of(r, ca if r == ra else 0)
                    x1 = self._x_of(r, cb if r == rb else len(lines[r])) + (0 if r == rb else fm.averageCharWidth() / 2)
                    p.fillRect(QRectF(self.pos.x() + x0, self.pos.y() + r * fm.lineSpacing(), x1 - x0, fm.height()),
                               shade)
        path = QPainterPath()
        for i, line in enumerate(lines):
            path.addText(self.pos + QPointF(0, fm.ascent() + i * fm.lineSpacing()), f, line)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawPath(path)
        if self.editing:
            p.fillRect(self.caret_rect(), C.ACCENT)
        p.restore()


class Counter(Shape):
    RADII = [11, 14, 18]
    resizable = False

    def __init__(self, pos, color, size, number):
        super().__init__(color, size)
        self.pos = QPointF(pos)
        self.number = number

    def bounds(self):
        r = self.RADII[self.size]
        return QRectF(self.pos.x() - r, self.pos.y() - r, 2 * r, 2 * r)

    def contains(self, pos, slack=4.0):
        return QLineF(pos, self.pos).length() <= self.RADII[self.size] + slack

    def _own_points(self):
        self.pos = QPointF(self.pos)

    def _map(self, fn):
        self.pos = fn(self.pos)

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


DRAG_TOOLS = {"line": Line, "arrow": Arrow, "rect": Box, "solid": SolidBox, "ellipse": Ellipse, "pixelate": Pixelate,
              "blur": Blur}


def next_number(drawings) -> int:
    """The number for a new counter: one more than the highest shown."""
    return 1 + max((s.number for s in drawings if isinstance(s, Counter) and not s.hidden), default=0)


def create(tool: str, pos: QPointF, color: QColor, size: int) -> Shape | None:
    if tool == "pen":
        return Stroke(pos, color, size)
    if tool == "marker":
        return Stroke(pos, color, size, marker=True)
    if tool in DRAG_TOOLS:
        return DRAG_TOOLS[tool](pos, color, size)
    return None
