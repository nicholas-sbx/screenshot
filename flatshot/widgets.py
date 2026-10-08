"""Custom-painted controls. Nothing here uses the Qt style for drawing."""

from flatshot.qt import (
    QAbstractButton, QFont, QFontMetrics, QHBoxLayout, QPainter, QPen, QPoint, QRectF, QSize, Qt, QWidget,
)

from flatshot import icons
from flatshot.theme import C, SWATCHES, font

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
    def __init__(self, ctl, icon: str, hint: str, parent=None):
        super().__init__(ctl, hint, parent)
        self.icon = icon
        self.active = False
        self.setFixedSize(36, 36)

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
            fg = C.INK
        elif self.isDown():
            p.setBrush(C.LINE)
            fg = C.TEXT
        elif self.underMouse():
            p.setBrush(C.HOVER)
            fg = C.TEXT
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 9, 9)
        icons.paint(p, self.icon, QRectF(8, 8, 20, 20), fg)


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
        if self.index == len(SWATCHES) - 1:  # ink on ink needs an edge
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

        for icon, hint, slot in [
            ("undo", "Undo  ·  Ctrl+Z", ctl.undo),
            ("redo", "Redo  ·  Ctrl+Shift+Z", ctl.redo),
        ]:
            b = IconButton(ctl, icon, hint, self)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addWidget(Divider(self))

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


class PillButton(_Button):
    def __init__(self, ctl, icon: str, label: str, hint: str, primary=False, parent=None):
        super().__init__(ctl, hint, parent)
        self.icon = icon
        self.label = label
        self.primary = primary
        self._font = font(12, QFont.Weight.DemiBold)
        width = QFontMetrics(self._font).horizontalAdvance(label)
        self.setFixedSize(QSize(width + 40, 30))

    def paintEvent(self, event):
        p = self._painter()
        hot = self.underMouse() or self.isDown()
        if self.primary:
            bg, fg = (C.TEXT, C.INK) if hot else (C.CODE, C.INK)
        else:
            bg, fg = (C.HOVER, C.TEXT) if hot else (C.RAISED, C.SOFT)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        icons.paint(p, self.icon, QRectF(9, 7, 16, 16), fg)
        p.setPen(fg)
        p.setFont(self._font)
        p.drawText(QRectF(29, 0, self.width() - 29, self.height()), Qt.AlignmentFlag.AlignVCenter, self.label)


class CodeChip(_Draggable):
    """Floating card over a detected QR code / barcode with Copy and Open."""

    MAX_CHARS = 34

    def __init__(self, ctl, code, parent):
        super().__init__(parent)
        self.code = code
        self._title = font(11, QFont.Weight.Bold)
        self._body = font(12, QFont.Weight.Medium, mono=True)
        text = " ".join(code.text.split())
        self.preview = text if len(text) <= self.MAX_CHARS else text[: self.MAX_CHARS - 1] + "…"
        self.kind = "Link" if code.is_link else code.kind

        row = QHBoxLayout(self)
        text_width = max(QFontMetrics(self._body).horizontalAdvance(self.preview),
                         QFontMetrics(self._title).horizontalAdvance(self.kind.upper()))
        row.setContentsMargins(14 + text_width + 14, 8, 8, 8)
        row.setSpacing(6)
        full = code.text if len(code.text) < 200 else code.text[:199] + "…"
        copy = PillButton(ctl, "copy", "Copy", f"Copy  ·  {full}", primary=not code.is_link, parent=self)
        copy.clicked.connect(lambda: ctl.copy_code(code))
        row.addWidget(copy)
        if code.is_link:
            opener = PillButton(ctl, "open", "Open", f"Open  ·  {full}", primary=True, parent=self)
            opener.clicked.connect(lambda: ctl.open_code(code))
            row.addWidget(opener)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.adjustSize()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.CODE, 1.5))
        p.setBrush(C.BASE)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75), 12, 12)
        h = self.height()
        p.setPen(C.CODE)
        p.setFont(self._title)
        p.drawText(QRectF(14, 0, self.width(), h / 2 + 1), Qt.AlignmentFlag.AlignBottom, self.kind.upper())
        p.setPen(C.TEXT)
        p.setFont(self._body)
        p.drawText(QRectF(14, h / 2 + 1, self.width(), h / 2), Qt.AlignmentFlag.AlignTop, self.preview)
