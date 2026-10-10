"""Custom-painted controls. Nothing here uses the Qt style for drawing."""

from flatshot.qt import (
    QAbstractButton, QColor, QConicalGradient, QFont, QFontMetrics, QHBoxLayout, QPainter, QPainterPath, QPen, QPoint,
    QPointF, QRectF, Qt, QWidget,
)

from flatshot import icons
from flatshot.theme import C, REC, SWATCHES, font, is_light

# (tool id, label, shortcut key)
TOOLS = [
    ("region", "Capture region", "R"),
    ("record", "Record screen", "V"),
    ("pen", "Pen", "P"),
    ("line", "Line", "L"),
    ("arrow", "Arrow", "A"),
    ("rect", "Rectangle", "B"),
    ("solid", "Filled rectangle", "F"),
    ("ellipse", "Ellipse", "E"),
    ("marker", "Highlighter", "H"),
    ("text", "Text", "T"),
    ("pixelate", "Pixelate", "X"),
    ("blur", "Blur", "U"),
    ("counter", "Counter", "N"),
]


def _centred_boxes(widget: QWidget, centre: QPointF):
    """A function giving squares (logical coordinates) round ``centre``
    whose edges all fall on whole screen pixels, centred alike:
    ``box(half, pixels)`` is ``half`` logical px each way, plus ``pixels``
    screen pixels."""
    dpr = widget.devicePixelRatioF()
    at = widget.mapTo(widget.window(), QPoint(0, 0))
    fx, fy = (at.x() * dpr) % 1, (at.y() * dpr) % 1  # where this widget's pixels start on the screen's
    cx, cy = round(centre.x() * dpr + fx), round(centre.y() * dpr + fy)

    def box(half: float, pixels: int = 0) -> QRectF:
        h = max(1, round(half * dpr) + pixels)
        return QRectF((cx - h - fx) / dpr, (cy - h - fy) / dpr, 2 * h / dpr, 2 * h / dpr)

    return box


def _fill_ring(p: QPainter, outer: QRectF, inner: QRectF, radius: float, color: QColor):
    """The band between two rounded squares, filled (no pen to straddle pixels)."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addRoundedRect(outer, radius, radius)
    inset = (outer.width() - inner.width()) / 2
    path.addRoundedRect(inner, max(0.5, radius - inset), max(0.5, radius - inset))
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)
    p.restore()


class _Button(QAbstractButton):
    def __init__(self, ctl, hint: str, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.hint = hint
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self.ctl.set_hint(self.hint)
        self.update()

    def leaveEvent(self, event):
        self.ctl.set_hint(None)
        self.update()

    def _painter(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        return p


class IconButton(_Button):
    def __init__(self, ctl, icon: str, hint: str, parent=None, size: int = 36):
        super().__init__(ctl, hint, parent)
        self.icon = icon
        self.active = False
        self.toggled_on: bool | None = None  # set for on/off toggles
        self.tint = None  # the icon's colour when idle, if not the usual
        self.dimmed = False  # faded: not used by the current tool
        self.setFixedSize(size, size)

    def set_toggle(self, on: bool, icon: str, hint: str):
        self.toggled_on, self.icon, self.hint = on, icon, hint
        self.update()

    def set_active(self, active: bool):
        if active != self.active:
            self.active = active
            self.update()

    def set_dimmed(self, dimmed: bool):
        if dimmed != self.dimmed:
            self.dimmed = dimmed
            self.update()

    def paintEvent(self, event):
        p = self._painter()
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fg = self.tint or C.SOFT
        if not self.isEnabled() or (self.dimmed and not self.underMouse()):
            p.setOpacity(0.3)
        if self.active:
            p.setBrush(C.ACCENT)
            fg = C.ON_ACCENT
        elif self.toggled_on and not self.isDown():
            p.setBrush(C.HOVER if self.underMouse() else C.RAISED)
            fg = C.CODE
        elif self.isDown():
            p.setBrush(C.LINE)
            fg = self.tint or C.TEXT
        elif self.underMouse():
            p.setBrush(C.HOVER)
            fg = self.tint or C.TEXT
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        radius = min(9.0, box.width() / 2)
        p.drawRoundedRect(box, radius, radius)
        inset = self.width() * 0.22
        icons.paint(p, self.icon, QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), fg)


class Swatch(_Button):
    def __init__(self, ctl, index: int, parent=None):
        super().__init__(ctl, ctl.keymap.hint("Colour", f"color.{index + 1}"), parent)
        self.index = index
        self.selected = False
        self.setFixedSize(26, 36)

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self.update()

    def paintEvent(self, event):
        p = self._painter()
        if not self.isEnabled():
            p.setOpacity(0.3)
        # Every edge on a whole screen pixel, the same number of them either
        # side of the middle: the colour sits exactly in the middle of its
        # ring at any scale (at 125 % or 150 % a ring drawn with a pen at
        # logical coordinates comes out a pixel thicker on one side).
        box = _centred_boxes(self, QRectF(self.rect()).center())
        if self.selected or self.underMouse():
            _fill_ring(p, box(11), box(9), 7.5, C.TEXT if self.selected else C.MUTED)
        color = self.color()
        inner = box(7)
        p.setBrush(color)
        p.drawRoundedRect(inner, 4.5, 4.5)
        if is_light(color) == is_light(C.BASE):  # e.g. ink on a dark toolbar needs an edge
            _fill_ring(p, inner, box(7, -1), 4.5, C.LINE)

    def color(self) -> QColor:
        return SWATCHES[self.index]


class CustomSwatch(Swatch):
    """Your own colour, after the others: a click picks it and opens the
    picker. A small rainbow dot in its corner says any colour goes here
    (only the usual ring says it's the one in use)."""

    def __init__(self, ctl, parent=None):
        super().__init__(ctl, len(SWATCHES), parent)
        self.hint = ctl.keymap.hint("Your own colour", f"color.{len(SWATCHES) + 1}") + "  ·  click to choose it"

    def color(self) -> QColor:
        return self.ctl.custom_color

    def paintEvent(self, event):
        super().paintEvent(event)
        p = self._painter()
        if not self.isEnabled():
            p.setOpacity(0.3)
        c = QRectF(self.rect()).center() + QPointF(6, 6)
        wheel = QConicalGradient(c, 90)
        for i in range(7):
            wheel.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 0.8, 1.0))
        p.setPen(QPen(C.BASE, 1.5))
        p.setBrush(wheel)
        p.drawEllipse(c, 3.6, 3.6)


class SizeButton(_Button):
    DOTS = [4.0, 7.0, 11.0]
    GAP = 4.0  # between neighbouring dots, edge to edge

    def __init__(self, ctl, parent=None):
        keys = " ".join(k for k in (ctl.keymap.label("size.down"), ctl.keymap.label("size.up")) if k)
        super().__init__(ctl, f"Stroke size  ·  {keys}" if keys else "Stroke size", parent)
        self.size = 1
        self.setFixedSize(40, 36)

    def set_size(self, size: int):
        self.size = size
        self.update()

    def paintEvent(self, event):
        p = self._painter()
        if not self.isEnabled():
            p.setOpacity(0.3)
        if self.underMouse():
            p.setBrush(C.HOVER)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)
        c = QRectF(self.rect()).center()
        x = c.x() - (sum(self.DOTS) + self.GAP * (len(self.DOTS) - 1)) / 2  # the row of dots, centred
        for i, d in enumerate(self.DOTS):
            p.setBrush(C.TEXT if i == self.size else C.MUTED)
            p.drawRoundedRect(QRectF(x, c.y() - d / 2, d, d), d / 3, d / 3)
            x += d + self.GAP


class Chip(_Button):
    """A labelled button in the recording panel: the primary action, an
    on/off option (``on`` set) or a plain setting."""

    def __init__(self, ctl, text: str, hint: str, parent=None, icon: str | None = None, primary=False,
                 key: str = ""):
        super().__init__(ctl, hint, parent)
        self.text, self.icon, self.primary, self.key = text, icon, primary, key
        self.on: bool | None = None
        self._font = font(13, QFont.Weight.DemiBold)
        self._key_font = font(11, QFont.Weight.Medium)
        self._sync_width()

    def _sync_width(self):
        w = QFontMetrics(self._font).horizontalAdvance(self.text) + 24
        if self.icon or self.primary:
            w += 24
        if self.key:
            w += QFontMetrics(self._key_font).horizontalAdvance(self.key) + 16
        self.setFixedSize(w, 34)

    def set_text(self, text: str):
        if text != self.text:
            self.text = text
            self._sync_width()
            self.update()

    def set_on(self, on: bool):
        if on != self.on:
            self.on = on
            self.update()

    def paintEvent(self, event):
        p = self._painter()
        if not self.isEnabled():
            p.setOpacity(0.35)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = self.underMouse() and self.isEnabled()
        if self.primary:
            p.setBrush(C.ACCENT.lighter(108) if hover else C.ACCENT)
            fg = icon_fg = C.ON_ACCENT
        elif self.on:
            p.setBrush(C.HOVER if hover else C.RAISED)
            fg, icon_fg = C.TEXT, C.CODE
        else:
            p.setBrush(C.HOVER if hover else Qt.BrushStyle.NoBrush)
            fg = icon_fg = C.TEXT if hover else (C.MUTED if self.on is False else C.SOFT)
        p.drawRoundedRect(box, 8, 8)
        x = 12.0
        if self.primary:
            p.setBrush(C.ON_ACCENT)
            p.drawEllipse(QRectF(x, box.center().y() - 5, 10, 10))
            x += 20
        elif self.icon:
            icons.paint(p, self.icon, QRectF(x - 2, box.center().y() - 9, 18, 18), icon_fg)
            x += 22
        p.setFont(self._font)
        p.setPen(fg)
        text_w = QFontMetrics(self._font).horizontalAdvance(self.text)
        p.drawText(QRectF(x, 0, text_w + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)
        if self.key:
            p.setFont(self._key_font)
            kw = QFontMetrics(self._key_font).horizontalAdvance(self.key) + 10
            key_box = QRectF(x + text_w + 8, box.center().y() - 9, kw, 18)
            edge = QColor(fg)
            edge.setAlphaF(0.4)
            p.setPen(QPen(edge, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(key_box, 4, 4)
            p.drawText(key_box, Qt.AlignmentFlag.AlignCenter, self.key)


class Divider(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(13, 36)

    def paintEvent(self, event):
        QPainter(self).fillRect(QRectF(6, 9, 1, 18), C.LINE)


class Grip(QWidget):
    """Drag handle. Mouse events fall through to the toolbar, which moves."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 36)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C.MUTED)
        for col in range(2):
            for row in range(3):
                p.drawEllipse(QRectF(5 + col * 6, 11 + row * 6, 2.6, 2.6))


class _Draggable(QWidget):
    """A floating panel inside the overlay that can be dragged around."""

    def __init__(self, parent):
        super().__init__(parent)
        self._grab: QPoint | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = event.position().toPoint()
            self.raise_()

    def mouseMoveEvent(self, event):
        if self._grab is None:
            return
        target = self.mapToParent(event.position().toPoint()) - self._grab
        bounds = self.parentWidget().rect()
        x = max(8, min(target.x(), bounds.width() - self.width() - 8))
        y = max(8, min(target.y(), bounds.height() - self.height() - 8))
        self.move(x, y)

    def mouseReleaseEvent(self, event):
        self._grab = None


class Toolbar(_Draggable):
    def __init__(self, ctl, parent):
        super().__init__(parent)
        self.ctl = ctl
        self.placed = False
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 8, 6)
        row.setSpacing(2)

        row.addWidget(Grip(self))
        self.tools = {}
        for name, label, key in TOOLS:
            b = IconButton(ctl, name, ctl.keymap.hint(label, f"tool.{name}"), self)
            b.clicked.connect(lambda _=False, n=name: ctl.set_tool(n))
            self.tools[name] = b
            row.addWidget(b)
            if name == "record":
                b.tint = REC
                b.setVisible(ctl.can_record)
                row.addWidget(Divider(self))
        row.addWidget(Divider(self))

        self.swatches = []
        for i in range(len(SWATCHES)):
            s = Swatch(ctl, i, self)
            s.clicked.connect(lambda _=False, i=i: ctl.set_color(i))
            self.swatches.append(s)
            row.addWidget(s)
        self.custom = CustomSwatch(ctl, self)
        self.custom.clicked.connect(lambda: ctl.toggle_picker(self.parentWidget()))
        self.swatches.append(self.custom)
        row.addWidget(self.custom)
        self.size_button = SizeButton(ctl, self)
        self.size_button.clicked.connect(ctl.cycle_size)
        row.addWidget(self.size_button)
        row.addWidget(Divider(self))

        self.codes_button = IconButton(ctl, "codes", "", self)
        self.codes_button.clicked.connect(ctl.toggle_codes)
        row.addWidget(self.codes_button)
        self.snap_button = IconButton(ctl, "magnet", "", self)
        self.snap_button.clicked.connect(ctl.toggle_snap)
        row.addWidget(self.snap_button)
        self.pointer_button = IconButton(ctl, "cursor-off", "", self)
        self.pointer_button.clicked.connect(ctl.toggle_pointer)
        self.pointer_button.setVisible(ctl.pointer_image is not None)
        row.addWidget(self.pointer_button)

        self.history_buttons = []
        for icon, hint, slot in [
            ("undo", ctl.keymap.hint("Undo", "undo"), ctl.undo),
            ("redo", ctl.keymap.hint("Redo", "redo"), ctl.redo),
        ]:
            b = IconButton(ctl, icon, hint, self)
            b.clicked.connect(slot)
            self.history_buttons.append(b)
            row.addWidget(b)
        row.addWidget(Divider(self))

        self.pin_button = IconButton(ctl, "pin", "", self)
        self.pin_button.clicked.connect(ctl.toggle_pin)
        row.addWidget(self.pin_button)
        self.screen_button = IconButton(ctl, "screen", "", self)
        self.screen_button.clicked.connect(lambda: ctl.whole_screen(self.parentWidget()))
        row.addWidget(self.screen_button)
        close = IconButton(ctl, "close", "Cancel  ·  Esc", self)
        close.clicked.connect(ctl.cancel)
        row.addWidget(close)

        self.adjustSize()
        self.refresh()

    def refresh(self):
        recording = self.ctl.tool == "record"
        for name, b in self.tools.items():
            b.set_active(name == self.ctl.tool)
            b.set_dimmed(recording and name not in ("region", "record"))
        # Colours, sizes, codes, undo and pinning don't apply to a recording.
        for w in self.swatches + [self.size_button, self.codes_button, self.pin_button, self.pointer_button]:
            w.setEnabled(not recording)
        self.history_buttons[0].setEnabled(not recording and self.ctl.can_undo())
        self.history_buttons[1].setEnabled(not recording and self.ctl.can_redo())
        self.screen_button.hint = ("Record whole screen  ·  Enter" if recording
                                   else "Capture whole screen  ·  Enter")
        for s in self.swatches:
            s.set_selected(s.index == self.ctl.color_index)
        self.size_button.set_size(self.ctl.size)
        self.pin_button.set_active(self.ctl.pin_mode)
        km = self.ctl.keymap
        self.pin_button.hint = km.hint("Pinning: the capture stays on screen, not saved" if self.ctl.pin_mode
                                       else "Pin the capture to the screen instead of saving it", "pin")
        self.snap_button.set_toggle(self.ctl.snap_edges, "magnet",
                                    km.hint("Snap selections to edges in the picture", "snap") + "  ·  "
                                    + ("on (hold Ctrl to place freely)" if self.ctl.snap_edges else "off"))
        shown = self.ctl.show_pointer
        self.pointer_button.set_toggle(shown, "cursor" if shown else "cursor-off",
                                       km.hint("Hide the mouse pointer" if shown else "Show the mouse pointer",
                                               "pointer") + ("  ·  shown in the capture" if shown else ""))
        n = self.ctl.code_count()
        if self.ctl.scanning:
            found = "Looking for codes…"
        elif self.ctl.codes_unscanned():
            found = "Not looked for yet"
        else:
            found = f"{n} code{'s' if n != 1 else ''} found" if n else "No codes found"
        if self.ctl.codes_visible:
            self.codes_button.set_toggle(True, "codes", km.hint("Hide QR / barcodes", "codes") + f"  ·  {found}")
        else:
            self.codes_button.set_toggle(False, "codes-off", km.hint("Show QR / barcodes", "codes") + f"  ·  {found}")

    def place(self):
        bounds = self.parentWidget().rect()
        self.move(max(8, (bounds.width() - self.width()) // 2), 20)
        self.placed = True

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)


class CodeChip(_Draggable):
    """Slim card over a detected QR code / barcode: the decoded text plus
    copy, open (links only) and dismiss."""

    MAX_CHARS = 30

    def __init__(self, ctl, code, parent):
        super().__init__(parent)
        self.code = code
        self._font = font(12, QFont.Weight.Medium, mono=True)
        text = " ".join(code.text.split())
        self.preview = text if len(text) <= self.MAX_CHARS else text[: self.MAX_CHARS - 1] + "…"
        full = code.text if len(code.text) < 200 else code.text[:199] + "…"

        row = QHBoxLayout(self)
        text_width = QFontMetrics(self._font).horizontalAdvance(self.preview)
        row.setContentsMargins(10 + text_width + 8, 3, 3, 3)
        row.setSpacing(0)
        buttons = [("copy", f"Copy  ·  {full}", lambda: ctl.copy_code(code))]
        if code.is_link:
            buttons.append(("open", f"Open  ·  {full}", lambda: ctl.open_code(code)))
        buttons.append(("close", "Dismiss this code", lambda: ctl.dismiss_code(parent, code)))
        for icon, hint, slot in buttons:
            b = IconButton(ctl, icon, hint, self, size=24)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.adjustSize()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(C.SOFT)
        p.setFont(self._font)
        p.drawText(QRectF(10, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter, self.preview)
