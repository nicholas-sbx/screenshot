"""The panel under an area chosen for recording. Loaded only when the
record tool is used, so it costs screenshots nothing."""

from flatshot import screencast
from flatshot.qt import QFont, QFontMetrics, QHBoxLayout, QPainter, QPen, QRectF, Qt, QWidget
from flatshot.theme import C, font
from flatshot.widgets import Chip, Divider, IconButton


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
