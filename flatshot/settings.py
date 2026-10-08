"""Settings window: a sidebar of pages, each a stack of quiet grouped cards.
Changes are saved as they're made and apply from the next capture."""

from flatshot import __version__, autostart, config, output, theme
from flatshot.qt import (
    QAbstractButton, QApplication, QColor, QComboBox, QFileDialog, QFont, QFontMetrics, QHBoxLayout, QIcon,
    QKeySequence, QLabel, QLineEdit, QPainter, QPen, QPointF, QPolygonF, QRectF, QScrollArea, QSize, QStackedWidget, Qt, QVBoxLayout,
    QWidget, Signal, keyval,
)
from flatshot.shortcuts import ACTIONS, GlobalShortcuts
from flatshot.theme import C, ICON_PATH, font

MODIFIER_KEYS = {keyval(getattr(Qt.Key, f"Key_{k}")) for k in
                 ("Control", "Shift", "Alt", "Meta", "AltGr", "Super_L", "Super_R", "Hyper_L", "Hyper_R")}


def _style() -> str:
    return f"""
QWidget#window, QWidget#page {{ background: {C.BASE.name()}; }}
QLabel {{ color: {C.TEXT.name()}; background: transparent; font-size: 13px; font-weight: 400; }}
QLabel[role="hint"] {{ color: {C.MUTED.name()}; font-size: 12px; }}
QLabel[role="error"] {{ color: {C.ACCENT.name()}; font-size: 12px; }}
QLabel[role="group"] {{ color: {C.MUTED.name()}; font-size: 12px; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 600; }}
QLineEdit, QComboBox {{
    background: {C.INK.name()}; color: {C.TEXT.name()}; border: 1px solid {C.LINE.name()};
    border-radius: 6px; padding: 6px 9px; font-size: 13px; font-weight: 400;
    selection-background-color: {C.HOVER.name()}; selection-color: {C.TEXT.name()};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {C.MUTED.name()}; }}
QLineEdit:disabled {{ color: {C.MUTED.name()}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {C.RAISED.name()}; color: {C.TEXT.name()}; border: 1px solid {C.LINE.name()};
    padding: 4px; outline: 0; selection-background-color: {C.HOVER.name()}; selection-color: {C.TEXT.name()};
}}
QScrollArea {{ border: none; background: {C.BASE.name()}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {C.LINE.name()}; border-radius: 2px; min-height: 32px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


def _label(text: str, role: str | None = None, wrap=False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


# -- controls ------------------------------------------------------------------

class Toggle(QAbstractButton):
    def __init__(self, on: bool, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        on = self.isChecked()
        p.setBrush(C.ACCENT if on else C.LINE)
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        p.setBrush(C.KNOB if on else C.TEXT)
        x = self.width() - 18 if on else 2
        p.drawEllipse(QRectF(x, 2, 16, 16))


class Segmented(QWidget):
    """A row of options, one selected. ``dots`` maps an option to (outer,
    inner) colours for a small dot before its label (the theme picker)."""

    changed = Signal(str)
    PAD = 3  # between the frame and the cells
    GAP = 2  # between cells

    def __init__(self, options: list[tuple[str, str]], value: str, dots: dict | None = None, parent=None):
        super().__init__(parent)
        self.options = options  # (value, label)
        self.value = value
        self.dots = dots or {}
        self._font = font(13, QFont.Weight.Medium)
        self._bold = font(13, QFont.Weight.DemiBold)
        fm = QFontMetrics(self._bold)
        self._widths = [fm.horizontalAdvance(label) + 32 + (20 if key in self.dots else 0)
                        for key, label in options]
        self.setFixedSize(sum(self._widths) + self.GAP * (len(options) - 1) + 2 * self.PAD, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1

    def _cells(self):
        x = float(self.PAD)
        for w in self._widths:
            yield QRectF(x, self.PAD, w, self.height() - 2 * self.PAD)
            x += w + self.GAP

    def _index_at(self, pos) -> int:
        return next((i for i, r in enumerate(self._cells()) if r.contains(pos)), -1)

    def mouseMoveEvent(self, event):
        hover = self._index_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        i = self._index_at(event.position())
        if i >= 0 and self.options[i][0] != self.value:
            self.value = self.options[i][0]
            self.update()
            self.changed.emit(self.value)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.INK)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        for i, (r, (value, label)) in enumerate(zip(self._cells(), self.options)):
            selected = value == self.value
            if selected or i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(C.HOVER if selected else C.RAISED)
                p.drawRoundedRect(r, 6, 6)
            text = QRectF(r)
            if value in self.dots:
                outer, inner = self.dots[value]
                dot = QRectF(r.left() + 13, r.center().y() - 6, 12, 12)
                p.setPen(QPen(C.LINE, 1))
                p.setBrush(outer)
                p.drawEllipse(dot)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(inner)
                p.drawEllipse(dot.adjusted(3, 3, -3, -3))
                text.setLeft(r.left() + 18)
            p.setFont(self._bold if selected else self._font)
            p.setPen(C.TEXT if selected else (C.SOFT if i == self._hover else C.MUTED))
            p.drawText(text, Qt.AlignmentFlag.AlignCenter, label)


class Slider(QWidget):
    changed = Signal(int)

    def __init__(self, value: int, minimum: int, maximum: int, step: int, parent=None):
        super().__init__(parent)
        self.value, self.minimum, self.maximum, self.step = value, minimum, maximum, step
        self.setFixedSize(180, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def _track(self) -> QRectF:
        return QRectF(7, self.height() / 2 - 2, self.width() - 14, 4)

    def _x(self, value) -> float:
        t = self._track()
        return t.left() + t.width() * (value - self.minimum) / (self.maximum - self.minimum)

    def _set(self, value: int):
        value = int(max(self.minimum, min(self.maximum, round(value / self.step) * self.step)))
        if value != self.value:
            self.value = value
            self.update()
            self.changed.emit(value)

    def _set_from(self, x: float):
        t = self._track()
        self._set(self.minimum + (x - t.left()) / t.width() * (self.maximum - self.minimum))

    def mousePressEvent(self, event):
        self._set_from(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from(event.position().x())

    def keyPressEvent(self, event):
        k = keyval(event.key())
        if k in (keyval(Qt.Key.Key_Left), keyval(Qt.Key.Key_Down)):
            self._set(self.value - self.step)
        elif k in (keyval(Qt.Key.Key_Right), keyval(Qt.Key.Key_Up)):
            self._set(self.value + self.step)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        t = self._track()
        p.setBrush(C.LINE)
        p.drawRoundedRect(t, 2, 2)
        x = self._x(self.value)
        p.setBrush(C.SOFT)
        p.drawRoundedRect(QRectF(t.left(), t.top(), x - t.left(), t.height()), 2, 2)
        p.setBrush(C.TEXT)
        p.drawEllipse(QRectF(x - 7, self.height() / 2 - 7, 14, 14))


class ValueSlider(QWidget):
    """A Slider with its value printed beside it."""

    changed = Signal(int)

    def __init__(self, value, minimum, maximum, step, fmt, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.slider = Slider(value, minimum, maximum, step)
        self.label = _label("", "hint")
        self.label.setFixedWidth(34)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fmt = fmt
        self.label.setText(fmt(value))
        self.slider.changed.connect(lambda v: (self.label.setText(fmt(v)), self.changed.emit(v)))
        row.addWidget(self.slider)
        row.addWidget(self.label)


class ShortcutField(QWidget):
    """Shows a shortcut; click to record a new one, × to clear."""

    chosen = Signal(str)

    def __init__(self, sequence: str, enabled: bool, on_record=None, parent=None):
        super().__init__(parent)
        self.sequence = sequence
        self.recording = False
        self.on_record = on_record  # called with True/False around recording
        self.setEnabled(enabled)
        self.setFixedSize(200, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._font = font(13, QFont.Weight.Medium)

    def _clear_rect(self) -> QRectF:
        return QRectF(self.width() - 26, 4, 22, 22)

    def set_sequence(self, sequence: str):
        self.sequence = sequence
        self.update()

    def _stop(self):
        if self.recording:
            self.recording = False
            if self.on_record:
                self.on_record(False)
            self.update()

    def mousePressEvent(self, event):
        if self.sequence and not self.recording and self._clear_rect().contains(event.position()):
            self.chosen.emit("")
            return
        if self.recording:
            self._stop()
            return
        self.recording = True
        if self.on_record:
            self.on_record(True)
        self.setFocus()
        self.update()

    def focusOutEvent(self, event):
        self._stop()

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        k = keyval(event.key())
        if k in MODIFIER_KEYS:
            return
        if k == keyval(Qt.Key.Key_Escape) and not event.modifiers():
            self._stop()
            return
        if k in (keyval(Qt.Key.Key_Backspace), keyval(Qt.Key.Key_Delete)) and not event.modifiers():
            self._stop()
            self.chosen.emit("")
            return
        text = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
        self._stop()
        self.chosen.emit(text)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.MUTED if self.recording else C.LINE, 1))
        p.setBrush(C.INK)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setFont(self._font)
        text_rect = QRectF(10, 0, self.width() - 36, self.height())
        if self.recording:
            p.setPen(C.SOFT)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, "Press a shortcut…")
        elif self.sequence:
            p.setPen(C.TEXT)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                       QKeySequence(self.sequence).toString(QKeySequence.SequenceFormat.NativeText))
            c = self._clear_rect().center()
            p.setPen(QPen(C.MUTED, 1.4))
            p.drawLine(c + QPointF(-3.5, -3.5), c + QPointF(3.5, 3.5))
            p.drawLine(c + QPointF(3.5, -3.5), c + QPointF(-3.5, 3.5))
        else:
            p.setPen(C.MUTED)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, "None")


class Combo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.MUTED, 1.4))
        c = QPointF(self.width() - 14, self.height() / 2)
        p.drawPolyline(QPolygonF([c + QPointF(-3.5, -1.5), c + QPointF(0, 2), c + QPointF(3.5, -1.5)]))


class Button(QAbstractButton):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self._font = font(12, QFont.Weight.Medium)
        self.setFixedSize(QFontMetrics(self._font).horizontalAdvance(text) + 24, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.HOVER if self.underMouse() and self.isEnabled() else C.RAISED)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setPen(C.TEXT if self.isEnabled() else C.MUTED)
        p.setFont(self._font)
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text())


class NavItem(QAbstractButton):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._font = font(13, QFont.Weight.Medium)

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.isChecked() or self.underMouse():
            p.setBrush(C.RAISED if self.isChecked() else C.BASE)
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setPen(C.TEXT if self.isChecked() else C.MUTED)
        p.setFont(self._font)
        p.drawText(QRectF(self.rect()).adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.text())


class Card(QWidget):
    """A rounded group of rows separated by hairlines."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(0)
        self.rows: list[QWidget] = []

    def add(self, row: QWidget) -> QWidget:
        self.rows.append(row)
        self.col.addWidget(row)
        return row

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.RAISED)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(QPen(C.LINE, 1))
        visible = [r for r in self.rows if r.isVisible()]
        for row in visible[1:]:
            y = row.geometry().top() + 0.5
            p.drawLine(QPointF(14, y), QPointF(self.width() - 14, y))


class Row(QWidget):
    """Title (+ optional hint) on the left, a control on the right, and an
    optional full-width widget underneath."""

    def __init__(self, title: str, control: QWidget | None = None, hint: str = "", below: QWidget | None = None):
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(_label(title))
        if hint:
            text.addWidget(_label(hint, "hint", wrap=True))
        top.addLayout(text, 1)
        if control is not None:
            top.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(top)
        self.error = _label("", "error", wrap=True)
        self.error.hide()
        col.addWidget(self.error)
        if below is not None:
            col.addWidget(below)
        self.setMinimumHeight(48)


# -- window -------------------------------------------------------------------

class SettingsWindow(QWidget):
    closed = Signal()

    def __init__(self, cfg: config.Config, shortcuts: GlobalShortcuts | None):
        super().__init__()
        self.cfg = cfg
        if shortcuts is None:
            shortcuts = GlobalShortcuts()
            shortcuts.start()
        self.shortcuts = shortcuts
        self.setWindowTitle("Flatshot Settings")
        self.setWindowIcon(QIcon(ICON_PATH))
        self.setObjectName("window")
        self.setStyleSheet(_style())
        self.resize(760, 560)
        self.setMinimumSize(640, 440)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav = QWidget()
        nav.setFixedWidth(184)
        nav.setAutoFillBackground(True)
        nav_col = QVBoxLayout(nav)
        nav_col.setContentsMargins(12, 18, 12, 14)
        nav_col.setSpacing(2)
        self.stack = QStackedWidget()
        root.addWidget(nav)
        root.addWidget(self.stack, 1)
        self.nav = nav
        nav.setStyleSheet(f"background: {C.INK.name()};")

        pages = [("General", self._general), ("Capture", self._capture),
                 ("After capture", self._after), ("Recording", self._recording), ("Shortcuts", self._shortcuts)]
        self.nav_items = []
        for i, (name, build) in enumerate(pages):
            item = NavItem(name)
            item.clicked.connect(lambda _=False, i=i: self.stack.setCurrentIndex(i))
            nav_col.addWidget(item)
            self.nav_items.append(item)
            self.stack.addWidget(self._page(name, build))
        nav_col.addStretch(1)
        nav_col.addWidget(_label(f"Flatshot {__version__}", "hint"))
        self.nav_items[0].setChecked(True)

        self.shortcuts.changed.connect(self._reload_shortcuts)

    def _page(self, title: str, build) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("page")
        self._col = QVBoxLayout(page)
        self._col.setContentsMargins(28, 22, 28, 24)
        self._col.setSpacing(8)
        self._col.addWidget(_label(title, "title"))
        self._col.addSpacing(6)
        build()
        self._col.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _card(self, heading: str = "") -> Card:
        if heading:
            self._col.addSpacing(8)
            self._col.addWidget(_label(heading, "group"))
        card = Card()
        self._col.addWidget(card)
        return card

    def _save(self, **changes):
        for key, value in changes.items():
            setattr(self.cfg, key, value)
        self.cfg.save()

    def _toggle(self, key: str) -> Toggle:
        t = Toggle(getattr(self.cfg, key))
        t.toggled.connect(lambda on: self._save(**{key: on}))
        return t

    def _line(self, key: str, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit(getattr(self.cfg, key))
        edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(lambda: self._save(**{key: edit.text().strip()}))
        return edit

    def _set_theme(self, name: str):
        self._save(theme=name)
        theme.use(name)
        theme.apply(QApplication.instance())
        self.setStyleSheet(_style())
        self.nav.setStyleSheet(f"background: {C.INK.name()};")
        for w in self.findChildren(QWidget):
            w.update()

    # -- pages ---------------------------------------------------------------

    def _general(self):
        card = self._card()
        look = Segmented([(key, t[0]) for key, t in theme.THEMES.items()],
                         self.cfg.theme if self.cfg.theme in theme.THEMES else "ember",
                         dots={key: (QColor(t[2]), QColor(t[9])) for key, t in theme.THEMES.items()})
        look.changed.connect(self._set_theme)
        picker = QWidget()
        line = QHBoxLayout(picker)
        line.setContentsMargins(0, 2, 0, 0)
        line.addWidget(look)
        line.addStretch(1)
        card.add(Row("Theme", None, "For the toolbar, the settings and the other panels. "
                                    "Your drawing colours stay the same.", below=picker))
        start = Toggle(autostart.enabled())
        start.toggled.connect(autostart.set_enabled)
        card.add(Row("Start at login", start, "Keeps Flatshot in the system tray so shortcuts work."))
        open_config = Button("Open")

        def open_folder():
            folder = config.config_path().parent
            folder.mkdir(parents=True, exist_ok=True)
            output.open_file(folder)

        open_config.clicked.connect(open_folder)
        card.add(Row("Configuration folder", open_config, str(config.config_path().parent)))

    def _capture(self):
        card = self._card()
        shade = ValueSlider(self.cfg.dim_opacity, 0, 90, 5, lambda v: f"{v}%" if v else "Off")
        shade.changed.connect(lambda v: self._save(dim_opacity=v))
        card.add(Row("Screen shading", shade))
        card.add(Row("Toolbar follows the mouse", self._toggle("toolbar_follows_mouse"),
                     "Show the toolbar on the monitor the pointer is on."))
        card.add(Row("Detect windows", self._toggle("detect_windows"),
                     "Hover a window and click to capture just that window. KDE Plasma."))
        card.add(Row("Include the mouse pointer", self._toggle("include_pointer"),
                     "In instant captures: active window, monitor, all screens, last region."))

        card = self._card("Magnifier and crosshair")
        loupe = self._toggle("show_loupe")
        card.add(Row("Magnifier", loupe, "Zoomed pixels, coordinates and colour beside the pointer."))
        lo, hi = config.LOUPE_SIZES
        size = ValueSlider(self.cfg.loupe_size, lo, hi, 20, lambda v: f"{v}px")
        size.changed.connect(lambda v: self._save(loupe_size=v))
        size_row = card.add(Row("Magnifier size", size))
        loupe.toggled.connect(lambda on: (size_row.setVisible(on), card.update()))
        size_row.setVisible(self.cfg.show_loupe)
        card.add(Row("Crosshair lines", self._toggle("show_crosshair"),
                     "Lines across the screen through the pointer."))

        card = self._card("QR codes and barcodes")
        card.add(Row("Scan the screen", self._toggle("scan_codes")))
        card.add(Row("Show results", self._toggle("show_codes"), "Press Q while capturing to show or hide them."))

        card = self._card("Advanced")
        combo = Combo()
        labels = {"auto": "Automatic", "kwin": "KWin", "spectacle": "Spectacle", "grim": "grim",
                  "gnome-screenshot": "GNOME Screenshot", "qt": "Qt (X11)"}
        for key in config.BACKENDS:
            combo.addItem(labels[key], key)
        combo.setCurrentIndex(config.BACKENDS.index(self.cfg.backend))
        combo.setFixedWidth(180)
        combo.currentIndexChanged.connect(lambda i: self._save(backend=config.BACKENDS[i]))
        card.add(Row("Capture method", combo))

    def _after(self):
        card = self._card("Save")
        save = self._toggle("save_to_disk")
        card.add(Row("Save to a folder", save))
        folder = self._line("save_dir")
        browse = Button("Choose…")
        folder_box = QWidget()
        fb = QHBoxLayout(folder_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(8)
        fb.addWidget(folder, 1)
        fb.addWidget(browse)
        card.add(Row("Folder", None, below=folder_box))

        def pick():
            path = QFileDialog.getExistingDirectory(self, "Screenshot folder", folder.text())
            if path:
                folder.setText(path)
                self._save(save_dir=path)

        browse.clicked.connect(pick)
        card.add(Row("File name", None,
                     "Date and time: %Y %m %d %H %M %S. Window: {app} {title}. Also {mode} (region, window, "
                     "monitor, desktop), {monitor}, {w} {h} for the size and {n} for a counter ({n:4} pads it). "
                     "A / makes subfolders, and the folder takes the same codes, like ~/Pictures/Screenshots/%Y-%m. "
                     "The extension is added for you.",
                     below=self._line("filename")))

        formats = output.writable_formats()
        if self.cfg.format not in [f[0] for f in formats]:
            self.cfg.format = "png"
        fmt = Segmented([(f[0], f[1]) for f in formats], self.cfg.format)
        card.add(Row("Format", fmt))
        quality = ValueSlider(self.cfg.quality, 10, 100, 5, str)
        quality.changed.connect(lambda v: self._save(quality=v))
        quality_row = card.add(Row("Quality", quality, "Higher is sharper and larger."))
        lossy = {f[0] for f in formats if f[3]}

        def format_changed(value):
            self._save(format=value)
            quality_row.setVisible(value in lossy)
            card.update()

        fmt.changed.connect(format_changed)
        quality_row.setVisible(self.cfg.format in lossy)

        def sync(on):
            for w in (folder, browse):
                w.setEnabled(on)

        save.toggled.connect(sync)
        sync(self.cfg.save_to_disk)

        card = self._card("Then")
        clip = Segmented([("image", "Image"), ("path", "File path"), ("none", "Nothing")], self.cfg.clipboard)
        clip.changed.connect(lambda v: self._save(clipboard=v))
        card.add(Row("Copy to clipboard", clip))
        card.add(Row("Show a notification", self._toggle("notify"), "With Open, Show in folder, Annotate and Pin."))
        then = Segmented([("none", "Nothing"), ("image", "Image"), ("folder", "Folder")], self.cfg.open_after)
        then.changed.connect(lambda v: self._save(open_after=v))
        card.add(Row("Open", then))

        sound_on = self._toggle("sound")
        card.add(Row("Play a sound", sound_on))
        sound_file = self._line("sound_file", "The desktop's screenshot sound")
        choose = Button("Choose…")
        test = Button("Play")
        box = QWidget()
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        for w, stretch in ((sound_file, 1), (choose, 0), (test, 0)):
            line.addWidget(w, stretch)
        sound_row = card.add(Row("Sound file", None, "Leave empty for the desktop's own. OGG, WAV, FLAC or MP3.",
                                 below=box))

        def pick_sound():
            path, _ = QFileDialog.getOpenFileName(self, "Sound", sound_file.text() or "/usr/share/sounds",
                                                  "Sounds (*.oga *.ogg *.wav *.flac *.mp3 *.opus)")
            if path:
                sound_file.setText(path)
                self._save(sound_file=path)

        def try_sound():
            from flatshot import sound

            self._save(sound_file=sound_file.text().strip())
            sound_row.error.setVisible(not sound.play(self.cfg.sound_file))
            sound_row.error.setText("Couldn't play it: no such file, or no player (pw-play, paplay, "
                                    "ffplay or canberra-gtk-play) installed.")

        choose.clicked.connect(pick_sound)
        test.clicked.connect(try_sound)
        sound_on.toggled.connect(lambda on: (sound_row.setVisible(on), card.update()))
        sound_row.setVisible(self.cfg.sound)
        card.add(Row("Run a command", None, "{path} is replaced with the image file.",
                     below=self._line("run_command", "curl -F file=@{path} https://example.com/upload")))

    def _recording(self):
        from flatshot import screencast

        card = self._card()
        how = screencast.method()
        problem = screencast.problem()
        status = card.add(Row("Recorder", None, screencast.METHOD_LABELS.get(how, how)))
        if problem:
            status.error.setText(problem)
            status.error.show()
        folder = self._line("record_dir")
        browse = Button("Choose…")
        box = QWidget()
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(folder, 1)
        line.addWidget(browse)
        card.add(Row("Folder", None, below=box))

        def pick():
            path = QFileDialog.getExistingDirectory(self, "Recordings folder", folder.text())
            if path:
                folder.setText(path)
                self._save(record_dir=path)

        browse.clicked.connect(pick)
        card.add(Row("File name", None, "The same codes as for screenshots. The extension is added for you.",
                     below=self._line("record_filename")))
        countdown = ValueSlider(self.cfg.record_countdown, 0, 10, 1, lambda v: f"{v} s" if v else "Off")
        countdown.changed.connect(lambda v: self._save(record_countdown=v))
        card.add(Row("Countdown", countdown, "Before recording starts."))

        card = self._card("Defaults")
        formats = screencast.formats_available() or screencast.FORMATS[:1]
        if self.cfg.record_format not in [key for key, _ in formats]:
            self.cfg.record_format = formats[0][0]
        fmt = Segmented(formats, self.cfg.record_format)
        fmt.changed.connect(lambda v: self._save(record_format=v))
        card.add(Row("Format", fmt, "GIFs have no sound."))
        fps = Segmented([(str(n), f"{n} fps") for n in config.RECORD_FPS], str(self.cfg.record_fps))
        fps.changed.connect(lambda v: self._save(record_fps=int(v)))
        card.add(Row("Frame rate", fps))
        card.add(Row("Microphone", self._toggle("record_mic")))
        card.add(Row("Computer sound", self._toggle("record_system_audio")))
        card.add(Row("Mouse pointer", self._toggle("record_cursor")))
        self._col.addWidget(_label("You can also change these under the area before you start. After a recording, "
                                   "the clipboard, notification, open and command settings from After capture "
                                   "apply; Copy to clipboard copies the video file.", "hint", wrap=True))

    def _shortcuts(self):
        supported = self.shortcuts.active or self.shortcuts.start()
        card = self._card()
        self.shortcut_fields = {}
        self.shortcut_rows = {}
        for action, (label, _) in ACTIONS.items():
            field = ShortcutField(self.shortcuts.get(action) if supported else "", supported,
                                  on_record=self.shortcuts.block)
            field.chosen.connect(lambda seq, a=action: self._assign(a, seq))
            self.shortcut_fields[action] = field
            self.shortcut_rows[action] = card.add(Row(label, field))
        if supported:
            note = "These are KDE global shortcuts; they also appear in System Settings → Shortcuts → Flatshot."
        else:
            note = ("Global shortcuts need KDE Plasma (" + (self.shortcuts.error or "unavailable") + "). "
                    "Elsewhere, bind the command  flatshot  in your desktop's keyboard settings.")
        self._col.addWidget(_label(note, "hint", wrap=True))

    def _assign(self, action: str, sequence: str):
        error = self.shortcut_rows[action].error
        owner = self.shortcuts.owner_of(sequence, action) if sequence else ""
        if owner:
            error.setText(f"{sequence} is already used by {owner}.")
            error.show()
            return
        ok, now = self.shortcuts.assign(action, sequence)
        self.shortcut_fields[action].set_sequence(now if ok else self.shortcuts.get(action))
        error.setVisible(not ok)
        if not ok:
            error.setText(f"KDE didn't accept {sequence or 'clearing the shortcut'}.")

    def _reload_shortcuts(self):
        for action, field in self.shortcut_fields.items():
            if not field.recording:
                field.set_sequence(self.shortcuts.get(action))

    def show_page(self, index: int):
        self.nav_items[index].setChecked(True)
        self.stack.setCurrentIndex(index)

    def closeEvent(self, event):
        for field in self.shortcut_fields.values():
            field._stop()
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit):
            focused.editingFinished.emit()  # flush the field being edited
        super().closeEvent(event)
        self.closed.emit()

    def sizeHint(self):
        return QSize(760, 560)
