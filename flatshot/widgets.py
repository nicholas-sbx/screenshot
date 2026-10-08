"""Custom-painted controls. Nothing here uses the Qt style for drawing."""

from flatshot.qt import (
    QAbstractButton, QFont, QFontMetrics, QHBoxLayout, QPainter, QPen, QPoint, QRectF, Qt, QWidget,
)

from flatshot import icons
from flatshot.theme import C, SWATCHES, font, is_light

# (tool id, label, shortcut key)
TOOLS = [
    ("region", "Capture region", "R"),
    ("pen", "Pen", "P"),
    ("line", "Line", "L"),
    ("arrow", "Arrow", "A"),
    ("rect", "Rectangle", "B"),
    ("ellipse", "Ellipse", "E"),
    ("marker", "Highlighter", "H"),
    ("text", "Text", "T"),
    ("pixelate", "Pixelate", "X"),
    ("counter", "Counter", "N"),
]


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
        self.setFixedSize(size, size)

    def set_toggle(self, on: bool, icon: str, hint: str):
        self.toggled_on, self.icon, self.hint = on, icon, hint
        self.update()

    def set_active(self, active: bool):
        if active != self.active:
            self.active = active
            self.update()

    def paintEvent(self, event):
        p = self._painter()
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fg = C.SOFT
        if self.active:
            p.setBrush(C.ACCENT)
            fg = C.ON_ACCENT
        elif self.toggled_on and not self.isDown():
            p.setBrush(C.HOVER if self.underMouse() else C.RAISED)
            fg = C.CODE
        elif self.isDown():
            p.setBrush(C.LINE)
            fg = C.TEXT
        elif self.underMouse():
            p.setBrush(C.HOVER)
            fg = C.TEXT
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        radius = min(9.0, box.width() / 2)
        p.drawRoundedRect(box, radius, radius)
        inset = self.width() * 0.22
        icons.paint(p, self.icon, QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), fg)


class Swatch(_Button):
    def __init__(self, ctl, index: int, parent=None):
        super().__init__(ctl, f"Colour  ·  {index + 1}", parent)
        self.index = index
        self.selected = False
        self.setFixedSize(26, 36)

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self.update()

    def paintEvent(self, event):
        p = self._painter()
        c = QRectF(self.rect()).center()
        if self.selected or self.underMouse():
            ring = QPen(C.TEXT if self.selected else C.MUTED, 2)
            p.setPen(ring)
            p.drawRoundedRect(QRectF(c.x() - 11, c.y() - 11, 22, 22), 7.5, 7.5)
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(SWATCHES[self.index])
        if is_light(SWATCHES[self.index]) == is_light(C.BASE):  # e.g. ink on a dark toolbar needs an edge
            p.setPen(QPen(C.LINE, 1.2))
        p.drawRoundedRect(QRectF(c.x() - 7, c.y() - 7, 14, 14), 4.5, 4.5)


class SizeButton(_Button):
    DOTS = [4.0, 7.0, 11.0]

    def __init__(self, ctl, parent=None):
        super().__init__(ctl, "Stroke size  ·  [ ]", parent)
        self.size = 1
        self.setFixedSize(36, 36)

    def set_size(self, size: int):
        self.size = size
        self.update()

    def paintEvent(self, event):
        p = self._painter()
        if self.underMouse():
            p.setBrush(C.HOVER)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)
        c = QRectF(self.rect()).center()
        for i, d in enumerate(self.DOTS):
            x = c.x() - 10 + i * 10
            p.setBrush(C.TEXT if i == self.size else C.LINE)
            p.drawRoundedRect(QRectF(x - d / 2, c.y() - d / 2, d, d), d / 3, d / 3)


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
            b = IconButton(ctl, name, f"{label}  ·  {key}", self)
            b.clicked.connect(lambda _=False, n=name: ctl.set_tool(n))
            self.tools[name] = b
            row.addWidget(b)
            if name == "region":
                row.addWidget(Divider(self))
        row.addWidget(Divider(self))

        self.swatches = []
        for i in range(len(SWATCHES)):
            s = Swatch(ctl, i, self)
            s.clicked.connect(lambda _=False, i=i: ctl.set_color(i))
            self.swatches.append(s)
            row.addWidget(s)
        self.size_button = SizeButton(ctl, self)
        self.size_button.clicked.connect(ctl.cycle_size)
        row.addWidget(self.size_button)
        row.addWidget(Divider(self))

        self.codes_button = IconButton(ctl, "codes", "", self)
        self.codes_button.clicked.connect(ctl.toggle_codes)
        row.addWidget(self.codes_button)

        for icon, hint, slot in [
            ("undo", "Undo  ·  Ctrl+Z", ctl.undo),
            ("redo", "Redo  ·  Ctrl+Shift+Z", ctl.redo),
        ]:
            b = IconButton(ctl, icon, hint, self)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addWidget(Divider(self))

        self.pin_button = IconButton(ctl, "pin", "", self)
        self.pin_button.clicked.connect(ctl.toggle_pin)
        row.addWidget(self.pin_button)
        screen = IconButton(ctl, "screen", "Capture whole screen  ·  Enter", self)
        screen.clicked.connect(lambda: ctl.capture(self.parentWidget(), None))
        row.addWidget(screen)
        close = IconButton(ctl, "close", "Cancel  ·  Esc", self)
        close.clicked.connect(ctl.cancel)
        row.addWidget(close)

        self.adjustSize()
        self.refresh()

    def refresh(self):
        for name, b in self.tools.items():
            b.set_active(name == self.ctl.tool)
        for s in self.swatches:
            s.set_selected(s.index == self.ctl.color_index)
        self.size_button.set_size(self.ctl.size)
        self.pin_button.set_active(self.ctl.pin_mode)
        self.pin_button.hint = ("Pinning: the capture stays on screen, not saved  ·  K" if self.ctl.pin_mode
                                else "Pin the capture to the screen instead of saving it  ·  K")
        n = self.ctl.code_count()
        found = f"{n} code{'s' if n != 1 else ''} found" if n else "No codes found"
        if self.ctl.codes_visible:
            self.codes_button.set_toggle(True, "codes", f"Hide QR / barcodes  ·  Q  ·  {found}")
        else:
            self.codes_button.set_toggle(False, "codes-off", f"Show QR / barcodes  ·  Q  ·  {found}")

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
