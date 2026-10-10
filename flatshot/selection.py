"""The select tool: pick up a drawing again to move it, resize it (lines by
their ends), restyle or delete it. Shared by the capture overlay and the
annotation editor, each a "host" with:

- ``annotations``: its drawings, bottom to top;
- ``commit(shape)``: add a drawing (an undo step);
- ``update()``: repaint.

Changes never edit a drawing: a changed copy replaces it (shapes.Shape.
replaces), so undo and redo work as for anything drawn.
"""

from flatshot import shapes
from flatshot.qt import QColor, QLineF, QPainter, QPen, QPointF, QRectF, Qt, keyval
from flatshot.theme import C

HANDLE = 9.0  # a handle's size on the screen
NUDGE, BIG_NUDGE = 1.0, 10.0  # arrow keys, with Shift

CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    "end0": Qt.CursorShape.CrossCursor, "end1": Qt.CursorShape.CrossCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


def _resized(r: QRectF, handle: str, d: QPointF, keep_ratio: bool, center: bool) -> QRectF:
    """``r`` with the edges ``handle`` names ("tl", "r", ...) moved by
    ``d`` (as far as the pointer moved: the handle stays under it).
    ``keep_ratio`` (Shift) keeps its shape, ``center`` (Ctrl) moves the
    opposite edges the other way."""
    left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
    c = r.center()
    if "l" in handle:
        left += d.x()
        if center:
            right = 2 * c.x() - left
    if "r" in handle:
        right += d.x()
        if center:
            left = 2 * c.x() - right
    if "t" in handle:
        top += d.y()
        if center:
            bottom = 2 * c.y() - top
    if "b" in handle:
        bottom += d.y()
        if center:
            top = 2 * c.y() - bottom
    if keep_ratio and r.width() > 0 and r.height() > 0:
        ratio = r.width() / r.height()
        w, h = right - left, bottom - top
        if handle in ("l", "r"):
            h = abs(w) / ratio * (1 if h >= 0 else -1)
        elif handle in ("t", "b"):
            w = abs(h) * ratio * (1 if w >= 0 else -1)
        elif abs(w) / ratio > abs(h):
            h = abs(w) / ratio * (1 if h >= 0 else -1)
        else:
            w = abs(h) * ratio * (1 if w >= 0 else -1)
        # Grow away from the edges that stay (or the middle).
        ax = c.x() if center or handle in ("t", "b") else (r.right() if "l" in handle else r.left())
        ay = c.y() if center or handle in ("l", "r") else (r.bottom() if "t" in handle else r.top())
        fx = 0.5 if center or handle in ("t", "b") else (1.0 if "l" in handle else 0.0)
        fy = 0.5 if center or handle in ("l", "r") else (1.0 if "t" in handle else 0.0)
        left, right = ax - w * fx, ax + w * (1 - fx)
        top, bottom = ay - h * fy, ay + h * (1 - fy)
    return QRectF(QPointF(left, top), QPointF(right, bottom))


class Selection:
    def __init__(self, host):
        self.host = host
        self._shape: shapes.Shape | None = None
        # A drag under way: (what: a handle, "move" or an end, press position,
        # the drawing as it was, its box then, the changed copy shown meanwhile).
        self._drag: tuple | None = None

    # -- what is selected ----------------------------------------------------

    @property
    def shape(self) -> shapes.Shape | None:
        """The selected drawing, while it's still there to change (undo
        may have taken it away)."""
        s = self._shape
        if s is not None and (s.hidden or s not in self.host.annotations) and self._drag is None:
            self._shape = None
        return self._shape

    def select(self, shape: shapes.Shape | None):
        self._shape = shape
        self.host.update()

    def clear(self) -> bool:
        """Deselect; True if something was selected (Esc does that first)."""
        had = self.shape is not None or self._drag is not None
        self.cancel()
        self._shape = None
        if had:
            self.host.update()
        return had

    def dragging(self) -> bool:
        return self._drag is not None

    def hits(self, pos: QPointF, scale: float = 1.0) -> list:
        """The drawings at ``pos``, topmost first."""
        slack = 5.0 / scale
        return [s for s in reversed(self.host.annotations)
                if not s.hidden and not isinstance(s, shapes.Removed) and s.contains(pos, slack)]

    # -- handles ---------------------------------------------------------------

    def _handles(self, shape: shapes.Shape, scale: float) -> dict:
        """Handle name -> its middle."""
        if shape.ends:
            return {"end0": QPointF(shape.start), "end1": QPointF(shape.end)}
        if not shape.resizable:
            return {}
        r = self._frame(shape, scale)
        xs = {"l": r.left(), "": r.center().x(), "r": r.right()}
        ys = {"t": r.top(), "": r.center().y(), "b": r.bottom()}
        return {yk + xk: QPointF(x, y) for yk, y in ys.items() for xk, x in xs.items() if yk + xk}

    @staticmethod
    def _frame(shape: shapes.Shape, scale: float) -> QRectF:
        """The dashed box around a selected drawing: its bounds with room
        for its stroke."""
        if isinstance(shape, shapes.Stroke):
            reach = shape._reach()
        elif isinstance(shape, (shapes.Text, shapes.Counter, shapes.SolidBox, shapes.Pixelate)):
            reach = 0.0  # (filled: no stroke past its box)
        else:
            reach = shape.width / 2
        pad = reach + 4 / scale
        return shape.bounds().adjusted(-pad, -pad, pad, pad)

    def _handle_at(self, pos: QPointF, scale: float) -> str | None:
        shape = self.shape
        if shape is None:
            return None
        grab = (HANDLE / 2 + 4) / scale
        for name, at in self._handles(shape, scale).items():
            if abs(pos.x() - at.x()) <= grab and abs(pos.y() - at.y()) <= grab:
                return name
        return None

    def cursor(self, pos: QPointF, scale: float = 1.0):
        """The pointer's shape over ``pos`` with the select tool."""
        if self._drag is not None:
            return CURSORS.get(self._drag[0], Qt.CursorShape.SizeAllCursor)
        handle = self._handle_at(pos, scale)
        if handle:
            return CURSORS[handle]
        return Qt.CursorShape.SizeAllCursor if self.hits(pos, scale) else Qt.CursorShape.ArrowCursor

    # -- the pointer -------------------------------------------------------------

    def press(self, pos: QPointF, mods, scale: float = 1.0) -> bool:
        """A click with the select tool: on a handle, start resizing; on a
        drawing, select it (Alt: the next one under it) and start moving it;
        elsewhere, deselect."""
        handle = self._handle_at(pos, scale)
        if handle is None:
            hits = self.hits(pos, scale)
            if not hits:
                self.clear()
                return False
            current = self.shape
            if mods & Qt.KeyboardModifier.AltModifier and current in hits:
                self._shape = hits[(hits.index(current) + 1) % len(hits)]
            elif current not in hits:
                self._shape = hits[0]
            handle = "move"
        shape = self._shape
        self._drag = (handle, QPointF(pos), shape, shape.bounds(), None)
        self.host.update()
        return True

    def move(self, pos: QPointF, mods) -> bool:
        if self._drag is None:
            return False
        handle, start, shape, box, preview = self._drag
        keep_ratio = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        center = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if preview is None and (pos - start).manhattanLength() < 2:
            return True  # (a click, not a drag yet)
        if handle == "move":
            d = pos - start
            if keep_ratio:  # Shift: straight across or straight down
                d = QPointF(d.x(), 0) if abs(d.x()) >= abs(d.y()) else QPointF(0, d.y())
            preview = shape.moved(d)
        elif handle in ("end0", "end1"):
            which = int(handle[-1])
            other = shape.end if which == 0 else shape.start
            to = QPointF(pos)
            if keep_ratio:  # Shift: 45° steps, as when drawing it
                line = QLineF(other, to)
                line.setAngle(round(line.angle() / 45) * 45)
                to = line.p2()
            preview = shape.with_end(which, to)
        else:
            new = _resized(box, handle, pos - start, keep_ratio, center)
            preview = shape.fitted(box, new)
        shape.hidden = True  # (the copy shows meanwhile)
        self._drag = (handle, start, shape, box, preview)
        self.host.update()
        return True

    def release(self) -> bool:
        """Let go: the changed copy replaces the drawing (one undo step)."""
        if self._drag is None:
            return False
        _, _, shape, _, preview = self._drag
        self._drag = None
        shape.hidden = False
        if preview is not None:
            self._replace(shape, preview)
        else:
            self.host.update()
        return True

    def cancel(self):
        """Drop a drag under way (Esc)."""
        if self._drag is not None:
            self._drag[2].hidden = False
            self._drag = None
            self.host.update()

    def _replace(self, old: shapes.Shape, new: shapes.Shape):
        old.hidden = True
        self.host.commit(new)
        self._shape = None if isinstance(new, shapes.Removed) else new
        self.host.update()

    # -- keys and the toolbar ------------------------------------------------------

    def key(self, event) -> bool:
        """Delete / Backspace removes the selected drawing; the arrow keys
        nudge it (Shift: further). True if the key was used."""
        shape = self.shape
        if shape is None or self._drag is not None:
            return False
        k = keyval(event.key())
        K = Qt.Key
        if k in (keyval(K.Key_Delete), keyval(K.Key_Backspace)):
            self._replace(shape, shapes.Removed(shape))
            return True
        step = BIG_NUDGE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else NUDGE
        d = {keyval(K.Key_Left): QPointF(-step, 0), keyval(K.Key_Right): QPointF(step, 0),
             keyval(K.Key_Up): QPointF(0, -step), keyval(K.Key_Down): QPointF(0, step)}.get(k)
        if d is None:
            return False
        self._replace(shape, shape.moved(d))
        return True

    def restyle(self, color: QColor | None = None, size: int | None = None) -> bool:
        """The toolbar's colour or size, for the selected drawing. True if
        there was one."""
        shape = self.shape
        if shape is None:
            return False
        if isinstance(shape, shapes.Pixelate):
            color = None  # (pixelate and blur have no colour)
        if (color is None or color == shape.color) and (size is None or size == shape.size):
            return True
        self._replace(shape, shape.restyled(color, size))
        return True

    # -- painting -----------------------------------------------------------------

    def paint(self, p: QPainter, base, scale: float = 1.0):
        """The copy being dragged, and the selected drawing's frame and handles.
        ``scale``: screen pixels per unit of the painter (the editor's zoom)."""
        self.paint_moving(p, base)
        self.paint_frame(p, scale)

    def _moving(self) -> shapes.Shape | None:
        return self._drag[4] if self._drag is not None else None

    def paint_moving(self, p: QPainter, base):
        """The changed copy, while dragging."""
        if self._moving() is not None:
            self._moving().paint(p, base)

    def paint_frame(self, p: QPainter, scale: float = 1.0):
        """The selected drawing's dashed frame and its handles."""
        shape = self._moving() or self.shape
        if shape is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge = QColor(C.ACCENT)
        pen = QPen(edge, 1.5 / scale, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if shape.ends:
            p.drawLine(shape.start, shape.end)
        else:
            p.drawRect(self._frame(shape, scale))
        p.setPen(QPen(C.ACCENT, 1.5 / scale))
        p.setBrush(C.TEXT)
        h = HANDLE / scale
        for at in self._handles(shape, scale).values():
            p.drawRoundedRect(QRectF(at.x() - h / 2, at.y() - h / 2, h, h), 3 / scale, 3 / scale)
        p.restore()
