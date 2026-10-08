"""Custom-painted controls. Nothing here uses the Qt style for drawing."""

from flatshot.qt import (
    QAbstractButton, QColor, QFont, QFontMetrics, QHBoxLayout, QPainter, QPen, QPoint, QRectF, Qt, QWidget,
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
        if not self.isEnabled():
            p.setOpacity(0.3)
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
        if not self.isEnabled():
            p.setOpacity(0.3)
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
        self.size_button = SizeButton(ctl, self)
        self.size_button.clicked.connect(ctl.cycle_size)
        row.addWidget(self.size_button)
        row.addWidget(Divider(self))

        self.codes_button = IconButton(ctl, "codes", "", self)
        self.codes_button.clicked.connect(ctl.toggle_codes)
        row.addWidget(self.codes_button)

        self.history_buttons = []
        for icon, hint, slot in [
            ("undo", "Undo  ·  Ctrl+Z", ctl.undo),
            ("redo", "Redo  ·  Ctrl+Shift+Z", ctl.redo),
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
        for w in self.swatches + [self.size_button, self.codes_button, self.pin_button] + self.history_buttons:
            w.setEnabled(not recording)
        self.screen_button.hint = ("Record whole screen  ·  Enter" if recording
                                   else "Capture whole screen  ·  Enter")
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


class Choice(QWidget):
    """A compact row of options, one selected (the recording format)."""

    def __init__(self, ctl, options: list[tuple[str, str]], available: set[str], hint: str, on_change, parent=None):
        super().__init__(parent)
        self.ctl, self.options, self.available, self.hint = ctl, options, available, hint
        self.on_change = on_change
        self.value = options[0][0]
        self._font = font(12, QFont.Weight.Bold)
        fm = QFontMetrics(self._font)
        self._widths = [fm.horizontalAdvance(label) + 22 for _, label in options]
        self.setFixedSize(sum(self._widths) + 6, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1

    def _cells(self):
        x = 3.0
        for w in self._widths:
            yield QRectF(x, 3, w, self.height() - 6)
            x += w

    def set_value(self, value: str):
        if value != self.value:
            self.value = value
            self.update()

    def enterEvent(self, event):
        self.ctl.set_hint(self.hint)

    def leaveEvent(self, event):
        self._hover = -1
        self.ctl.set_hint(None)
        self.update()

    def mouseMoveEvent(self, event):
        hover = next((i for i, r in enumerate(self._cells()) if r.contains(event.position())), -1)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event):
        for r, (value, _) in zip(self._cells(), self.options):
            if r.contains(event.position()) and value in self.available and value != self.value:
                self.value = value
                self.update()
                self.on_change(value)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C.INK)
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        p.setFont(self._font)
        for i, (r, (value, label)) in enumerate(zip(self._cells(), self.options)):
            selected = value == self.value
            if selected or (i == self._hover and value in self.available):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(C.HOVER if selected else C.RAISED)
                p.drawRoundedRect(r, 6, 6)
            p.setOpacity(1.0 if value in self.available else 0.35)
            p.setPen(C.TEXT if selected else C.MUTED)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
            p.setOpacity(1.0)


class RecordPanel(QWidget):
    """Under the area chosen for recording: start, sound and pointer
    options, format and frame rate."""

    def __init__(self, ctl, parent):
        super().__init__(parent)
        from flatshot import screencast

        self.ctl = ctl
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(4)
        self.start = Chip(ctl, "Record", "Start recording  ·  Enter", self, primary=True, key="Enter")
        self.start.clicked.connect(lambda: ctl.start_countdown(parent))
        row.addWidget(self.start)
        row.addWidget(Divider(self))
        self.mic = Chip(ctl, "Mic", "", self, icon="mic")
        self.mic.clicked.connect(lambda: ctl.set_record_option(mic=not ctl.rec_opts.mic))
        self.system = Chip(ctl, "System", "", self, icon="speaker")
        self.system.clicked.connect(lambda: ctl.set_record_option(system_audio=not ctl.rec_opts.system_audio))
        self.cursor = Chip(ctl, "Cursor", "", self, icon="cursor")
        self.cursor.clicked.connect(lambda: ctl.set_record_option(cursor=not ctl.rec_opts.cursor))
        for chip in (self.mic, self.system, self.cursor):
            row.addWidget(chip)
        row.addWidget(Divider(self))
        available = {key for key, _ in screencast.formats_available()}
        self.format = Choice(ctl, screencast.FORMATS, available, "File type  ·  GIFs have no sound",
                             lambda v: ctl.set_record_option(format=v), self)
        row.addWidget(self.format)
        self.fps = Chip(ctl, "30 fps", "Frames per second  ·  click to change", self)
        self.fps.clicked.connect(self._next_fps)
        row.addWidget(self.fps)
        close = IconButton(ctl, "close", "Choose another area  ·  Esc", self, size=34)
        close.clicked.connect(lambda: parent.cancel_gesture())
        row.addWidget(close)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.adjustSize()
        self.refresh()

    def _next_fps(self):
        from flatshot import screencast

        choices = screencast.FPS_CHOICES
        current = self.ctl.rec_opts.fps
        self.ctl.set_record_option(fps=choices[(choices.index(current) + 1) % len(choices)]
                                   if current in choices else 30)

    def refresh(self):
        opts = self.ctl.rec_opts
        gif = opts.format == "gif"
        self.mic.set_on(opts.mic and not gif)
        self.system.set_on(opts.system_audio and not gif)
        self.cursor.set_on(opts.cursor)
        for chip in (self.mic, self.system):
            chip.setEnabled(not gif)
        self.mic.hint = "Record the microphone" + ("  ·  on" if opts.mic else "  ·  off")
        self.system.hint = "Record the computer's sound" + ("  ·  on" if opts.system_audio else "  ·  off")
        self.cursor.hint = "Show the mouse pointer" + ("  ·  on" if opts.cursor else "  ·  off")
        self.format.set_value(opts.format)
        self.fps.set_text(f"{opts.fps} fps")
        problem = self.ctl.record_problem()
        self.start.setEnabled(problem is None)
        self.start.hint = problem or "Start recording  ·  Enter"
        self.adjustSize()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
