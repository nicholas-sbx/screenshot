"""Settings window. Every control is custom-painted in the Flatshot style;
changes are saved immediately and apply from the next capture."""

from flatshot import __version__, autostart, config, output
from flatshot.qt import (
    QAbstractButton, QComboBox, QFileDialog, QFont, QFontMetrics, QHBoxLayout, QIcon, QKeySequence, QLabel,
    QLineEdit, QPainter, QPen, QPointF, QPolygonF, QRectF, QScrollArea, QSize, Qt, QVBoxLayout, QWidget, Signal, keyval,
)
from flatshot.shortcuts import ACTIONS, GlobalShortcuts
from flatshot.theme import C, ICON_PATH, font

MODIFIER_KEYS = {keyval(getattr(Qt.Key, f"Key_{k}")) for k in
                 ("Control", "Shift", "Alt", "Meta", "AltGr", "Super_L", "Super_R", "Hyper_L", "Hyper_R")}


def _css(c):
    return c.name()


STYLE = f"""
QWidget#page {{ background: {_css(C.BASE)}; }}
QLabel {{ color: {_css(C.TEXT)}; background: transparent; }}
QLabel[role="desc"] {{ color: {_css(C.MUTED)}; font-size: 12px; }}
QLabel[role="error"] {{ color: {_css(C.ACCENT)}; font-size: 12px; }}
QLabel[role="section"] {{ color: {_css(C.ACCENT)}; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; }}
QLabel[role="title"] {{ font-size: 20px; font-weight: 700; }}
QLineEdit, QComboBox {{
    background: {_css(C.RAISED)}; color: {_css(C.TEXT)}; border: 1px solid {_css(C.LINE)};
    border-radius: 8px; padding: 7px 10px; font-size: 13px;
    selection-background-color: {_css(C.ACCENT)}; selection-color: {_css(C.INK)};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {_css(C.ACCENT)}; }}
QLineEdit:disabled {{ color: {_css(C.MUTED)}; background: {_css(C.BASE)}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {_css(C.RAISED)}; color: {_css(C.TEXT)}; border: 1px solid {_css(C.LINE)};
    padding: 4px; outline: 0; selection-background-color: {_css(C.HOVER)}; selection-color: {_css(C.TEXT)};
}}
QScrollArea {{ border: none; background: {_css(C.BASE)}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 6px 2px; }}
QScrollBar::handle:vertical {{ background: {_css(C.LINE)}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {_css(C.MUTED)}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


class Toggle(QAbstractButton):
    def __init__(self, on: bool, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        on = self.isChecked()
        p.setBrush(C.ACCENT if on else C.LINE)
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        p.setBrush(C.INK if on else C.SOFT)
        x = self.width() - 21 if on else 3
        p.drawRoundedRect(QRectF(x, 3, 18, 18), 6, 6)


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent=None):
        super().__init__(parent)
        self.options = options  # (value, label)
        self.value = value
        self._font = font(12, QFont.Weight.DemiBold)
        fm = QFontMetrics(self._font)
        self._widths = [fm.horizontalAdvance(label) + 26 for _, label in options]
        self.setFixedSize(sum(self._widths) + 8, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1

    def _cells(self):
        x = 4.0
        for w in self._widths:
            yield QRectF(x, 4, w, self.height() - 8)
            x += w

    def _index_at(self, pos):
        return next((i for i, r in enumerate(self._cells()) if r.contains(pos)), -1)

    def mouseMoveEvent(self, event):
        self._hover = self._index_at(event.position())
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
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C.RAISED)
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        p.setFont(self._font)
        for i, (r, (value, label)) in enumerate(zip(self._cells(), self.options)):
            selected = value == self.value
            if selected or i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(C.ACCENT if selected else C.HOVER)
                p.drawRoundedRect(r, 7, 7)
            p.setPen(C.INK if selected else C.SOFT)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)


class Slider(QWidget):
    changed = Signal(int)

    def __init__(self, value: int, maximum: int, step: int, parent=None):
        super().__init__(parent)
        self.value, self.maximum, self.step = value, maximum, step
        self.setFixedSize(200, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def _track(self) -> QRectF:
        return QRectF(9, self.height() / 2 - 3, self.width() - 18, 6)

    def _set_from(self, x: float):
        t = self._track()
        raw = (x - t.left()) / t.width() * self.maximum
        value = int(max(0, min(self.maximum, round(raw / self.step) * self.step)))
        if value != self.value:
            self.value = value
            self.update()
            self.changed.emit(value)

    def mousePressEvent(self, event):
        self._set_from(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from(event.position().x())

    def keyPressEvent(self, event):
        k = keyval(event.key())
        if k in (keyval(Qt.Key.Key_Left), keyval(Qt.Key.Key_Down)):
            self._set_from(self._track().left() + (self.value - self.step) / self.maximum * self._track().width())
        elif k in (keyval(Qt.Key.Key_Right), keyval(Qt.Key.Key_Up)):
            self._set_from(self._track().left() + (self.value + self.step) / self.maximum * self._track().width())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        t = self._track()
        p.setBrush(C.LINE)
        p.drawRoundedRect(t, 3, 3)
        x = t.left() + t.width() * self.value / self.maximum
        p.setBrush(C.ACCENT)
        p.drawRoundedRect(QRectF(t.left(), t.top(), x - t.left(), t.height()), 3, 3)
        p.setBrush(C.TEXT)
        p.drawRoundedRect(QRectF(x - 8, self.height() / 2 - 8, 16, 16), 5, 5)


class ShortcutField(QWidget):
    """Shows a shortcut; click to record a new one, × to clear."""

    chosen = Signal(str)

    def __init__(self, sequence: str, enabled: bool, on_record=None, parent=None):
        super().__init__(parent)
        self.sequence = sequence
        self.recording = False
        self.on_record = on_record  # called with True/False around recording
        self.setEnabled(enabled)
        self.setFixedSize(220, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self._font = font(13, QFont.Weight.DemiBold)

    def _clear_rect(self) -> QRectF:
        return QRectF(self.width() - 30, 5, 24, 24)

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
        combo = event.keyCombination()
        text = QKeySequence(combo).toString(QKeySequence.SequenceFormat.PortableText)
        self._stop()
        self.chosen.emit(text)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.ACCENT if self.recording else C.LINE, 1.2))
        p.setBrush(C.RAISED if self.isEnabled() else C.BASE)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.6, 0.6, -0.6, -0.6), 8, 8)
        p.setFont(self._font)
        text_rect = QRectF(12, 0, self.width() - 44, self.height())
        if self.recording:
            p.setPen(C.ACCENT)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, "Press keys…  (Esc cancels)")
        elif self.sequence:
            p.setPen(C.TEXT)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, QKeySequence(self.sequence).toString(
                QKeySequence.SequenceFormat.NativeText))
            r = self._clear_rect()
            p.setPen(QPen(C.MUTED, 1.6))
            c = r.center()
            p.drawLine(c + QPointF(-4, -4), c + QPointF(4, 4))
            p.drawLine(c + QPointF(4, -4), c + QPointF(-4, 4))
        else:
            p.setPen(C.MUTED)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, "Not set — click to record")


class Combo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.SOFT, 1.6))
        c = QPointF(self.width() - 16, self.height() / 2)
        p.drawPolyline(QPolygonF([c + QPointF(-4, -2), c + QPointF(0, 2), c + QPointF(4, -2)]))


class TextButton(QAbstractButton):
    def __init__(self, text: str, primary=False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.primary = primary
        self._font = font(12, QFont.Weight.DemiBold)
        self.setFixedSize(QFontMetrics(self._font).horizontalAdvance(text) + 30, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        hot = self.underMouse()
        if self.primary:
            p.setBrush(C.TEXT if hot else C.ACCENT)
            fg = C.INK
        else:
            p.setBrush(C.HOVER if hot else C.RAISED)
            fg = C.TEXT
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        p.setPen(fg)
        p.setFont(self._font)
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text())


def _label(text: str, role: str | None = None, wrap=False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


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
        self.setObjectName("page")
        self.setStyleSheet(STYLE)
        self.resize(640, 760)
        self.setMinimumWidth(560)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("page")
        scroll.setWidget(page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.col = QVBoxLayout(page)
        self.col.setContentsMargins(32, 28, 32, 28)
        self.col.setSpacing(6)

        head = QHBoxLayout()
        title = _label("Flatshot", "title")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(_label(f"v{__version__}", "desc"))
        self.col.addLayout(head)
        self.col.addWidget(_label("Changes are saved right away and apply from the next capture.", "desc"))

        self._shortcuts_section()
        self._capture_section()
        self._after_section()
        self._general_section()
        self.col.addStretch(1)

        self.shortcuts.changed.connect(self._reload_shortcuts)

    # -- layout helpers ------------------------------------------------------

    def _section(self, name: str):
        self.col.addSpacing(22)
        self.col.addWidget(_label(name.upper(), "section"))
        self.col.addSpacing(4)

    def _row(self, title: str, control: QWidget | None, desc: str = "") -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label(title))
        if desc:
            text.addWidget(_label(desc, "desc", wrap=True))
        row.addLayout(text, 1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(row)
        self.col.addSpacing(10)
        self.col.addLayout(box)
        return box

    def _save(self, **changes):
        for key, value in changes.items():
            setattr(self.cfg, key, value)
        self.cfg.save()

    def _toggle(self, key: str, title: str, desc: str = "") -> Toggle:
        t = Toggle(getattr(self.cfg, key))
        t.toggled.connect(lambda on: self._save(**{key: on}))
        self._row(title, t, desc)
        return t

    def _line(self, key: str, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit(getattr(self.cfg, key))
        edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(lambda: self._save(**{key: edit.text().strip()}))
        return edit

    # -- sections ------------------------------------------------------------

    def _shortcuts_section(self):
        self._section("Shortcuts")
        supported = self.shortcuts.active or self.shortcuts.start()
        self.shortcut_fields = {}
        self.shortcut_errors = {}
        for action, (label, _) in ACTIONS.items():
            field = ShortcutField(self.shortcuts.get(action) if supported else "", supported,
                                  on_record=self.shortcuts.block)
            field.chosen.connect(lambda seq, a=action: self._assign(a, seq))
            box = self._row(label, field)
            error = _label("", "error", wrap=True)
            error.hide()
            box.addWidget(error)
            self.shortcut_fields[action] = field
            self.shortcut_errors[action] = error
        if supported:
            self.col.addWidget(_label("Also editable in System Settings → Keyboard → Shortcuts → Flatshot. "
                                      "Shortcuts work while Flatshot runs in the tray.", "desc", wrap=True))
        else:
            self.col.addWidget(_label(
                "Global shortcuts need KDE Plasma (" + (self.shortcuts.error or "unavailable") + "). "
                "On other desktops, bind the command  flatshot  in your keyboard settings.", "error", wrap=True))

    def _assign(self, action: str, sequence: str):
        error = self.shortcut_errors[action]
        owner = self.shortcuts.owner_of(sequence, action) if sequence else ""
        if owner:
            error.setText(f"{sequence} is already used by {owner}. Free it in System Settings → Shortcuts first.")
            error.show()
            return
        ok, now = self.shortcuts.assign(action, sequence)
        self.shortcut_fields[action].set_sequence(now if ok else self.shortcuts.get(action))
        error.setVisible(not ok)
        if not ok:
            error.setText(f"KDE refused {sequence or 'clearing the shortcut'}. It may be reserved by the system.")

    def _reload_shortcuts(self):
        for action, field in self.shortcut_fields.items():
            if not field.recording:
                field.set_sequence(self.shortcuts.get(action))

    def _capture_section(self):
        self._section("Capture")
        shade = QWidget()
        row = QHBoxLayout(shade)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        slider = Slider(self.cfg.dim_opacity, 90, 5)
        value = _label("", "desc")
        value.setFixedWidth(36)

        def shown(v):
            value.setText(f"{v}%" if v else "Off")

        shown(self.cfg.dim_opacity)
        slider.changed.connect(shown)
        slider.changed.connect(lambda v: self._save(dim_opacity=v))
        row.addWidget(slider)
        row.addWidget(value)
        self._row("Screen shading", shade, "How much the frozen screen is darkened outside your selection.")

        self._toggle("toolbar_follows_mouse", "Toolbar follows the mouse",
                     "Show the toolbar on the monitor the pointer is on.")
        self._toggle("detect_windows", "Detect windows",
                     "Hover a window and click to capture just that window (KDE Plasma).")
        self._toggle("scan_codes", "Scan for QR codes and barcodes")
        self._toggle("show_codes", "Show detected codes", "Toggle them while capturing with Q or the toolbar.")

        combo = Combo()
        labels = {"auto": "Automatic", "spectacle": "Spectacle (KDE)", "grim": "grim (wlroots)",
                  "gnome-screenshot": "GNOME Screenshot", "qt": "Qt (X11 only)"}
        for key in config.BACKENDS:
            combo.addItem(labels[key], key)
        combo.setCurrentIndex(config.BACKENDS.index(self.cfg.backend))
        combo.setFixedWidth(200)
        combo.currentIndexChanged.connect(lambda i: self._save(backend=config.BACKENDS[i]))
        self._row("Capture helper", combo, "Wayland apps can't read the screen themselves.")

    def _after_section(self):
        self._section("After capture")
        save = self._toggle("save_to_disk", "Save to folder")
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder = self._line("save_dir")
        browse = TextButton("Browse…")
        folder_row.addWidget(folder, 1)
        folder_row.addWidget(browse)
        self.col.addSpacing(6)
        self.col.addLayout(folder_row)

        def pick():
            path = QFileDialog.getExistingDirectory(self, "Screenshot folder", folder.text())
            if path:
                folder.setText(path)
                self._save(save_dir=path)

        browse.clicked.connect(pick)

        def sync(on):
            folder.setEnabled(on)
            browse.setEnabled(on)

        save.toggled.connect(sync)
        sync(self.cfg.save_to_disk)

        self._row("File name", None, "strftime pattern: %Y year, %m month, %d day, %H-%M-%S time.")
        self.col.addSpacing(6)
        self.col.addWidget(self._line("filename"))

        clip = Segmented([("image", "Image"), ("path", "File path"), ("none", "Nothing")], self.cfg.clipboard)
        clip.changed.connect(lambda v: self._save(clipboard=v))
        self._row("Copy to clipboard", clip)

        self._toggle("notify", "Show a notification", "With Open, Show in folder and Annotate buttons.")

        then = Segmented([("none", "Nothing"), ("image", "Image"), ("folder", "Folder")], self.cfg.open_after)
        then.changed.connect(lambda v: self._save(open_after=v))
        self._row("Then open", then)

        self._row("Run a command", None, "Runs after every capture. {path} is replaced by the image file "
                                         "(also in $FLATSHOT_PATH). Leave empty for none.")
        self.col.addSpacing(6)
        self.col.addWidget(self._line("run_command", "e.g.  curl -F file=@{path} https://example.com/upload"))

    def _general_section(self):
        self._section("General")
        start = Toggle(autostart.enabled())
        start.toggled.connect(autostart.set_enabled)
        self._row("Start at login", start, "Runs Flatshot in the system tray so shortcuts work.")

        footer = QHBoxLayout()
        footer.setSpacing(8)
        open_config = TextButton("Open config folder")
        def open_folder():
            folder = config.config_path().parent
            folder.mkdir(parents=True, exist_ok=True)
            output.open_file(folder)

        open_config.clicked.connect(open_folder)
        footer.addWidget(open_config)
        footer.addStretch(1)
        done = TextButton("Done", primary=True)
        done.clicked.connect(self.close)
        footer.addWidget(done)
        self.col.addSpacing(26)
        self.col.addLayout(footer)

    def closeEvent(self, event):
        for field in self.shortcut_fields.values():
            field._stop()
        # Text fields save on editingFinished; flush the focused one too.
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit):
            focused.editingFinished.emit()
        super().closeEvent(event)
        self.closed.emit()

    def sizeHint(self):
        return QSize(640, 760)
