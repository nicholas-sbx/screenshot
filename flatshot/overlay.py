"""One fullscreen overlay per monitor showing the frozen capture."""

from flatshot.qt import (
    QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPoint, QPointF, QPolygonF,
    QRect, QRectF, Qt, QWidget,
)

from flatshot import layershell, shapes
from flatshot.theme import C, font
from flatshot.widgets import CodeChip, Toolbar

HINTS = {
    "region": "Drag to capture  ·  Click for whole screen  ·  Esc to cancel",
    "codes": "Drag to capture  ·  Q hides detected codes  ·  Esc to cancel",
    "text": "Click to place text  ·  Enter to finish  ·  R to capture",
}
DRAW_HINT = "Draw on the screen  ·  R then drag to capture  ·  Enter for whole screen"


class Overlay(QWidget):
    def __init__(self, ctl, screen, base: QPixmap, origin: QPoint):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.target_screen = screen
        self.base = base
        self.origin = origin  # top-left of this screen in the full capture, physical px
        self._pixels: QImage | None = None
        self.annotations: list[shapes.Shape] = []
        self.undone: list[shapes.Shape] = []
        self.active: shapes.Shape | None = None
        self.sel_origin: QPointF | None = None
        self.sel_rect: QRectF | None = None
        self.cursor_pos: QPointF | None = None
        self.codes: list[tuple[object, QPolygonF]] = []
        self.chips: list[CodeChip] = []
        self.toolbar: Toolbar | None = None

        self.setWindowTitle("Flatshot")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.update_cursor()

    # -- window ------------------------------------------------------------

    def show_on_screen(self):
        screen = self.target_screen
        if hasattr(self, "setScreen"):
            self.setScreen(screen)
        self.setGeometry(screen.geometry())
        if layershell.apply(self, screen):
            # An overlay layer surface: no window animations, above panels.
            self.show()
        else:
            if not hasattr(self, "setScreen"):
                self.create()
                self.windowHandle().setScreen(screen)
            self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def add_toolbar(self):
        self.toolbar = Toolbar(self.ctl, self)
        self.toolbar.hide()
        self._place_floating()

    def resizeEvent(self, event):
        self._place_floating()

    def _place_floating(self):
        if self.toolbar and not self.toolbar.placed and self.width() > self.toolbar.width():
            self.toolbar.place()
        for chip, (_, poly) in zip(self.chips, self.codes):
            c = poly.boundingRect().center()
            chip.move(round(c.x() - chip.width() / 2), round(c.y() - chip.height() / 2))

    def dpr(self) -> float:
        return self.base.devicePixelRatio()

    def pixels(self) -> QImage:
        if self._pixels is None:
            self._pixels = self.base.toImage()
        return self._pixels

    # -- codes -------------------------------------------------------------

    def set_codes(self, codes):
        """Keep codes whose centre lies on this screen, in logical coords."""
        dpr = self.dpr()
        phys = QRectF(QRect(self.origin, self.base.size()))
        for code in codes:
            poly = QPolygonF(code.corners)
            if not phys.contains(poly.boundingRect().center()):
                continue
            local = QPolygonF([(pt - QPointF(self.origin)) / dpr for pt in code.corners])
            self.codes.append((code, local))
            chip = CodeChip(self.ctl, code, self)
            self.chips.append(chip)
        self._place_floating()

    def dismiss_code(self, code):
        for i, (c, _) in enumerate(self.codes):
            if c is code:
                del self.codes[i]
                self.chips.pop(i).deleteLater()
                break
        self.update()

    # -- state -------------------------------------------------------------

    def refresh(self):
        show_chips = self.ctl.tool == "region" and self.sel_rect is None and self.ctl.codes_visible
        for chip in self.chips:
            chip.setVisible(show_chips)
        if self.toolbar:
            self.toolbar.refresh()
        self.update_cursor()
        self.update()

    def update_cursor(self):
        shape = Qt.CursorShape.IBeamCursor if self.ctl.tool == "text" else Qt.CursorShape.CrossCursor
        self.setCursor(shape)

    def commit(self, shape: shapes.Shape):
        self.annotations.append(shape)
        self.undone.clear()
        self.ctl.record(self)
        self.update()

    def undo(self) -> bool:
        if not self.annotations:
            return False
        self.undone.append(self.annotations.pop())
        self.update()
        return True

    def redo(self) -> bool:
        if not self.undone:
            return False
        self.annotations.append(self.undone.pop())
        self.update()
        return True

    def cancel_gesture(self) -> bool:
        if self.sel_rect is not None or self.active is not None:
            self.sel_origin = self.sel_rect = self.active = None
            self.ctl.refresh()
            return True
        return False

    # -- output ------------------------------------------------------------

    def render(self, rect: QRectF | None) -> QImage:
        """The capture plus annotations, cropped to ``rect`` (logical)."""
        dpr = self.dpr()
        if rect is None:
            phys = self.base.rect()
        else:
            phys = QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                         round(rect.width() * dpr), round(rect.height() * dpr)).intersected(self.base.rect())
        image = self.pixels().copy(phys).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        p = QPainter(image)
        p.translate(-phys.x() / dpr, -phys.y() / dpr)
        for shape in self.annotations:
            shape.paint(p, self.base)
        p.end()
        image.setDevicePixelRatio(1.0)
        return image.convertToFormat(QImage.Format.Format_RGB32)

    # -- input -------------------------------------------------------------

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            if not self.cancel_gesture():
                self.ctl.cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.ctl.commit_text() and self.ctl.tool == "text":
            return  # first click just finishes the text being typed
        tool = self.ctl.tool
        if tool == "region":
            self.sel_origin = pos
            self.sel_rect = QRectF(pos, pos)
            self.ctl.refresh()
        elif tool == "text":
            self.ctl.begin_text(self, shapes.Text(pos, self.ctl.color, self.ctl.size))
        elif tool == "counter":
            self.commit(shapes.Counter(pos, self.ctl.color, self.ctl.size, self.ctl.next_number()))
        else:
            self.active = shapes.create(tool, pos, self.ctl.color, self.ctl.size)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        self.cursor_pos = pos
        self.ctl.activate(self)
        if self.sel_origin is not None:
            self.sel_rect = QRectF(self.sel_origin, pos).normalized().intersected(QRectF(self.rect()))
        elif self.active is not None:
            self.active.extend(pos, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.sel_origin is not None:
            rect = self.sel_rect
            self.sel_origin = self.sel_rect = None
            if rect is None or rect.width() < 3 or rect.height() < 3:
                rect = None  # a click captures the whole screen
            self.ctl.capture(self, rect)
        elif self.active is not None:
            shape, self.active = self.active, None
            if shape.is_valid():
                self.commit(shape)
            self.update()

    def enterEvent(self, event):
        self.ctl.activate(self)

    def leaveEvent(self, event):
        self.cursor_pos = None
        self.update()

    def keyPressEvent(self, event):
        self.ctl.key(self, event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        if not QRectF(QPointF(0, 0), self.base.deviceIndependentSize()).contains(QRectF(self.rect())):
            p.fillRect(self.rect(), C.INK)
        p.drawPixmap(0, 0, self.base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for shape in self.annotations:
            shape.paint(p, self.base)
        if self.active is not None:
            self.active.paint(p, self.base)
        if self.ctl.text_edit and self.ctl.text_edit[0] is self:
            self.ctl.text_edit[1].paint(p, self.base)

        region = self.ctl.tool == "region"
        if region:
            dim = QPainterPath()
            dim.addRect(QRectF(self.rect()))
            if self.sel_rect is not None:
                hole = QPainterPath()
                hole.addRect(self.sel_rect)
                dim = dim.subtracted(hole)
            if self.ctl.dim.alpha():
                p.fillPath(dim, self.ctl.dim)
            if self.sel_rect is None and self.ctl.codes_visible:
                self._paint_codes(p)
        if self.sel_rect is not None:
            self._paint_selection(p, self.sel_rect)
        elif region and self.cursor_pos is not None:
            self._paint_crosshair(p, self.cursor_pos)
        if region and self.cursor_pos is not None and self._loupe_allowed():
            self._paint_loupe(p, self.cursor_pos)
        if self.sel_rect is None and self is self.ctl.pointer_overlay:
            self._paint_hint(p)

    def _loupe_allowed(self) -> bool:
        pt = self.cursor_pos.toPoint()
        floating = [self.toolbar] if self.toolbar else []
        return not any(w.isVisible() and w.geometry().adjusted(-24, -24, 24, 24).contains(pt)
                       for w in floating + self.chips)

    def _paint_codes(self, p: QPainter):
        for _, poly in self.codes:
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.fillPath(path, C.CODE_SOFT)
            pen = QPen(C.CODE, 2.5)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.strokePath(path, pen)

    def _paint_selection(self, p: QPainter, r: QRectF):
        p.setPen(QPen(C.ACCENT, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(-1, -1, 1, 1))
        dpr = self.dpr()
        label = f"{round(r.width() * dpr)} × {round(r.height() * dpr)}"
        self._pill(p, label, QPointF(r.left(), r.top() - 10), anchor_bottom=True,
                   bg=C.ACCENT, fg=C.INK, keep_inside=True)

    def _paint_crosshair(self, p: QPainter, pos: QPointF):
        color = QColor(C.ACCENT)
        color.setAlpha(110)
        p.setPen(QPen(color, 1))
        x, y = round(pos.x()) + 0.5, round(pos.y()) + 0.5
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))

    def _paint_loupe(self, p: QPainter, pos: QPointF):
        cells, size = 15, 120.0
        dpr = self.dpr()
        px, py = int(pos.x() * dpr), int(pos.y() * dpr)
        x = pos.x() + 24 if pos.x() + 24 + size < self.width() else pos.x() - 24 - size
        y = pos.y() + 24 if pos.y() + 24 + size + 34 < self.height() else pos.y() - 24 - size - 34
        box = QRectF(x, y, size, size)
        clip = QPainterPath()
        clip.addRoundedRect(box, 12, 12)
        p.save()
        p.setClipPath(clip)
        p.fillRect(box, C.INK)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(box, self.base, QRectF(px - cells // 2, py - cells // 2, cells, cells))
        cell = size / cells
        p.setPen(QPen(C.ACCENT, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(x + (cells // 2) * cell, y + (cells // 2) * cell, cell, cell))
        p.restore()
        p.setPen(QPen(C.LINE, 1))
        p.drawRoundedRect(box, 12, 12)
        img = self.pixels()
        color = img.pixelColor(min(px, img.width() - 1), min(py, img.height() - 1)).name().upper()
        self._pill(p, f"{px}, {py}   {color}", QPointF(x, y + size + 6), mono=True)

    def _paint_hint(self, p: QPainter):
        tool = "codes" if self.ctl.tool == "region" and self.codes and self.ctl.codes_visible else self.ctl.tool
        text = self.ctl.hint or HINTS.get(tool, DRAW_HINT)
        fm = QFontMetricsF(font(12, QFont.Weight.Medium))
        x = (self.width() - fm.horizontalAdvance(text)) / 2 - 14
        self._pill(p, text, QPointF(x, self.height() - 52))

    def _pill(self, p, text, at: QPointF, anchor_bottom=False, bg=None, fg=None, mono=False, keep_inside=False):
        f = font(12, QFont.Weight.DemiBold if bg is not None else QFont.Weight.Medium, mono=mono)
        fm = QFontMetricsF(f)
        box = QRectF(0, 0, fm.horizontalAdvance(text) + 28, 28)
        box.moveTopLeft(at - QPointF(0, box.height()) if anchor_bottom else at)
        if keep_inside and box.top() < 4:
            box.moveTop(at.y() + 20)  # no room above the selection: tuck inside
        p.save()
        p.setPen(QPen(C.LINE, 1) if bg is None else Qt.PenStyle.NoPen)
        p.setBrush(bg or C.BASE)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(fg or C.SOFT)
        p.setFont(f)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()
