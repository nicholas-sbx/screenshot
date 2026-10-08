"""The panel under an area chosen for recording. Loaded only when the
record tool is used, so it costs screenshots nothing."""

from flatshot import icons, screencast
from flatshot.qt import QColor, QFont, QFontMetrics, QHBoxLayout, QPainter, QPen, QRectF, Qt, QWidget
from flatshot.theme import C, font
from flatshot.widgets import Divider, IconButton, _Button


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
