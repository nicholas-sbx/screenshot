"""The annotation editor: a normal window for drawing on a screenshot already
taken (or any image), with the capture overlay's tools, Save, Save As and
Copy. The picture can be moved around and zoomed like in a paint program,
and any number of editors can be open at once. Loaded only when one opens."""

import sys
from pathlib import Path

from flatshot import config, output, shapes, theme
from flatshot.qt import (
    QColor, QEvent, QFileDialog, QFont, QFontMetricsF, QHBoxLayout, QImage, QMessageBox, QPainter, QPen, QPixmap,
    QPoint, QPointF, QRectF, QSizeF, Qt, QTimer, QVBoxLayout, QWidget, keyval,
)
from flatshot.theme import C, SWATCHES, font
from flatshot.widgets import TOOLS, Chip, CustomSwatch, Divider, IconButton, SizeButton, Swatch

# The overlay's drawing tools (not capturing or recording).
EDIT_TOOLS = [t for t in TOOLS if t[0] not in ("region", "record")]
TOOL_KEYS = {keyval(getattr(Qt.Key, f"Key_{key}")): name for name, _, key in EDIT_TOOLS}
COLOUR_TOOLS = ("pen", "line", "arrow", "rect", "ellipse", "marker", "text", "counter")
CUSTOM = len(SWATCHES)  # the colour index of your own colour
ZOOM = (0.05, 32.0)
HINT = "Scroll to move around  ·  Ctrl+scroll to zoom  ·  Space+drag or middle-drag to pan"

_open: list["Editor"] = []
_when_all_closed: list = []


def open_file(path: str | Path, save_to: str | Path | None = None) -> "Editor | None":
    """Open ``path`` in a new editor. Save writes back to it (or to
    ``save_to``). None if it isn't a readable image."""
    path = Path(path).expanduser()
    image = QImage(str(path))
    if image.isNull():
        print(f"flatshot: cannot read {path}", file=sys.stderr)
        return None
    editor = Editor(image, path, Path(save_to).expanduser() if save_to else None)
    editor.show()
    editor.raise_()
    editor.activateWindow()
    _open.append(editor)
    return editor


def open_count() -> int:
    return len(_open)


def when_all_closed(callback) -> None:
    _when_all_closed.append(callback)


class Editor(QWidget):
    def __init__(self, image: QImage, path: Path, save_to: Path | None = None):
        super().__init__(None, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.cfg = config.load()
        self.path = save_to or path
        self.dirty = False
        self.alpha = image.hasAlphaChannel()
        # Annotations are kept in the picture's logical pixels, as on the
        # overlay, so a stroke is as thick here as it would be there.
        screen = self.screen()
        self.dpr = screen.devicePixelRatio() if screen is not None else 1.0
        self.base = QPixmap.fromImage(image)
        self.base.setDevicePixelRatio(self.dpr)
        self.annotations: list[shapes.Shape] = []
        self.undone: list[shapes.Shape] = []
        self.active: shapes.Shape | None = None
        self.text_edit: shapes.Text | None = None
        self.tool = self.cfg.default_tool if self.cfg.default_tool in COLOUR_TOOLS else "pen"
        self.colour_tool = self.tool
        self.color_index = min(max(self.cfg.default_color, 0), CUSTOM)
        self.custom_color = QColor(self.cfg.custom_color)
        self.size = min(max(self.cfg.default_size, 0), len(theme.SIZES) - 1)
        self.eyedropper = False
        self.picker = None
        self.hint: str | None = None
        self._status: str | None = None  # a passing message ("Saved to ..."), in place of the hint
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._unsay)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.bar = _Bar(self)
        column.addWidget(self.bar)
        self.canvas = Canvas(self)
        column.addWidget(self.canvas, 1)
        self.status = _Status(self)
        column.addWidget(self.status)
        self._title()

        size = QSizeF(image.width() / self.dpr, image.height() / self.dpr)
        if screen is not None:
            avail = screen.availableGeometry()
            w = max(720, min(avail.width() * 0.85, size.width() + 48))
            h = max(480, min(avail.height() * 0.85, size.height() + 140))
            self.resize(round(max(w, self.bar.sizeHint().width())), round(h))
        self.refresh()

    # -- what the toolbar's widgets ask of their "ctl" ------------------------

    def set_hint(self, text: str | None):
        self.hint = text
        self.status.update()

    @property
    def color(self) -> QColor:
        return self.custom_color if self.color_index == CUSTOM else SWATCHES[self.color_index]

    def set_tool(self, tool: str):
        self.commit_text()
        self.tool = tool
        if tool in COLOUR_TOOLS:
            self.colour_tool = tool
        self.refresh()

    def set_color(self, index: int):
        """A colour; from a tool that has none (pixelate, blur), back to the
        last drawing tool, so the colour is used."""
        if self.tool not in COLOUR_TOOLS:
            self.tool = self.colour_tool
        self.color_index = index
        if self.text_edit:
            self.text_edit.color = self.color
        self.refresh()

    def set_size(self, size: int):
        self.size = min(max(size, 0), len(theme.SIZES) - 1)
        if self.text_edit:
            self.text_edit.size = self.size
        self.refresh()

    def cycle_size(self):
        self.set_size((self.size + 1) % len(theme.SIZES))

    def set_custom_color(self, color: QColor):
        self.custom_color = QColor(color)
        self.set_color(CUSTOM)

    def toggle_picker(self, _parent=None):
        if self.picker is not None and self.picker.isVisible():
            self.close_picker()
            return
        self.set_color(CUSTOM)
        if self.picker is None:
            from flatshot.colorpicker import ColorPicker

            self.picker = ColorPicker(self, self)
        self.picker.set_color(self.custom_color)
        swatch = self.bar.custom
        at = swatch.mapTo(self, QPoint(swatch.width() // 2, swatch.height()))
        x = max(8, min(at.x() - self.picker.width() // 2, self.width() - self.picker.width() - 8))
        self.picker.move(x, self.bar.height() + 6)
        self.picker.show()
        self.picker.raise_()

    def close_picker(self) -> bool:
        was_open = self.eyedropper or (self.picker is not None and self.picker.isVisible())
        self.eyedropper = False
        if self.picker is not None and self.picker.isVisible():
            self.picker.hide()
            self.setFocus()
        name = self.custom_color.name().upper()
        if name != config.load().custom_color:
            saved = config.load()
            saved.custom_color = name
            try:
                saved.save()
            except OSError as e:
                print(f"flatshot: could not save the settings: {e}", file=sys.stderr)
        if was_open:
            self.refresh()
        return was_open

    def start_eyedropper(self):
        if self.picker is not None:
            self.picker.hide()
        self.eyedropper = True
        self.refresh()

    def take_color(self, pos: QPointF):
        img = self.base.toImage()
        x = min(max(int(pos.x() * self.dpr), 0), img.width() - 1)
        y = min(max(int(pos.y() * self.dpr), 0), img.height() - 1)
        self.eyedropper = False
        self.set_custom_color(img.pixelColor(x, y))
        self.toggle_picker()

    # -- drawing -------------------------------------------------------------

    def next_number(self) -> int:
        return 1 + sum(isinstance(s, shapes.Counter) for s in self.annotations)

    def commit(self, shape: shapes.Shape):
        self.annotations.append(shape)
        self.undone.clear()
        self._changed()

    def begin_text(self, shape: shapes.Text):
        self.commit_text()
        self.text_edit = shape
        self.canvas.update()

    def commit_text(self) -> bool:
        if self.text_edit is None:
            return False
        shape, self.text_edit = self.text_edit, None
        shape.editing = False
        if shape.is_valid():
            self.commit(shape)
        self.canvas.update()
        return True

    def undo(self):
        self.commit_text()
        if self.annotations:
            self.undone.append(self.annotations.pop())
            self._changed()

    def redo(self):
        if self.undone:
            self.annotations.append(self.undone.pop())
            self._changed()

    def _changed(self):
        self.dirty = True
        self._title()
        self.refresh()

    def refresh(self):
        self.bar.refresh()
        self.canvas.update_cursor()
        self.canvas.update()
        self.status.update()

    def _title(self):
        self.setWindowTitle(("● " if self.dirty else "") + f"{self.path.name} — Annotate")

    # -- output --------------------------------------------------------------

    def render(self) -> QImage:
        """The picture with the annotations on it, at full resolution."""
        self.commit_text()
        image = self.base.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(self.dpr)
        p = QPainter(image)
        for shape in self.annotations:
            shape.paint(p, self.base)
        p.end()
        image.setDevicePixelRatio(1.0)
        return image.convertToFormat(QImage.Format.Format_ARGB32 if self.alpha else QImage.Format.Format_RGB32)

    def save(self) -> bool:
        return self._save_to(self.path)

    def save_as(self) -> bool:
        kinds = ";;".join(f"{label} (*.{key})" for key, label, _, _ in output.writable_formats())
        chosen, _ = QFileDialog.getSaveFileName(self, "Save as", str(self.path), kinds)
        return bool(chosen) and self._save_to(Path(chosen))

    def _save_to(self, path: Path) -> bool:
        try:
            written = output.save(self.render(), self.cfg, explicit=str(path))
        except OSError as e:
            self._say(f"Couldn't save: {e}")
            return False
        self.path, self.dirty = written, False
        self._title()
        self._say(f"Saved to {written}")
        return True

    def copy(self):
        ok, _ = output.copy_image(self.render())
        self._say("Copied to the clipboard" if ok else "Couldn't copy to the clipboard")

    def _say(self, message: str):
        """Show ``message`` in the status line for a few seconds."""
        self._status = message
        self.status.update()
        self._status_timer.start(4000)

    def _unsay(self):
        self._status = None
        self.status.update()

    # -- keys ------------------------------------------------------------------

    def keyPressEvent(self, event):
        k = keyval(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        K = {name: keyval(getattr(Qt.Key, f"Key_{name}")) for name in (
            "Return", "Enter", "Escape", "Backspace", "Space", "S", "C", "Z", "Y", "W", "0", "1", "Plus", "Equal",
            "Minus", "BracketLeft", "BracketRight")}
        enter = k in (K["Return"], K["Enter"])
        if self.text_edit is not None and not ctrl:
            shape = self.text_edit
            if k == K["Escape"] or (enter and not shift):
                self.commit_text()
            elif enter:
                shape.text += "\n"
            elif k == K["Backspace"]:
                shape.text = shape.text[:-1]
            elif event.text() and event.text().isprintable():
                shape.text += event.text()
            self.canvas.update()
            return
        if k == K["Space"] and not event.isAutoRepeat():
            self.canvas.set_space(True)
        elif k == K["Escape"]:
            if not (self.close_picker() or self.canvas.cancel()):
                self.close()
        elif ctrl and k == K["S"]:
            self.save_as() if shift else self.save()
        elif ctrl and k == K["C"]:
            self.copy()
        elif ctrl and k == K["Z"]:
            self.redo() if shift else self.undo()
        elif ctrl and k == K["Y"]:
            self.redo()
        elif ctrl and k == K["W"]:
            self.close()
        elif ctrl and k == K["0"]:
            self.canvas.fit()
        elif ctrl and k == K["1"]:
            self.canvas.zoom_to(1.0)
        elif ctrl and k in (K["Plus"], K["Equal"]):
            self.canvas.zoom_to(self.canvas.zoom * 1.25)
        elif ctrl and k == K["Minus"]:
            self.canvas.zoom_to(self.canvas.zoom / 1.25)
        elif not ctrl and k in TOOL_KEYS:
            self.set_tool(TOOL_KEYS[k])
        elif not ctrl and keyval(Qt.Key.Key_1) <= k <= keyval(Qt.Key.Key_1) + CUSTOM:
            self.set_color(k - keyval(Qt.Key.Key_1))
        elif k == K["BracketLeft"]:
            self.set_size(self.size - 1)
        elif k == K["BracketRight"]:
            self.set_size(self.size + 1)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if keyval(event.key()) == keyval(Qt.Key.Key_Space) and not event.isAutoRepeat():
            self.canvas.set_space(False)

    # -- closing -------------------------------------------------------------

    def closeEvent(self, event):
        self.commit_text()
        if self.dirty:
            box = QMessageBox(QMessageBox.Icon.Question, "Annotate",
                              f"Save the changes to {self.path.name}?", parent=self)
            box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                   | QMessageBox.StandardButton.Cancel)
            answer = box.exec()
            if answer == QMessageBox.StandardButton.Cancel or (
                    answer == QMessageBox.StandardButton.Save and not self.save()):
                event.ignore()
                return
        event.accept()
        if self in _open:
            _open.remove(self)
        self.base = QPixmap()  # (its Python side may outlive the window a while)
        self.annotations, self.undone = [], []
        if not _open:
            callbacks, _when_all_closed[:] = list(_when_all_closed), []
            for callback in callbacks:
                QTimer.singleShot(0, callback)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), C.BASE)


class _Bar(QWidget):
    """The tools, colours and sizes as on the capture toolbar, then undo,
    redo, zoom to fit, Copy, Save as and Save."""

    def __init__(self, ed: Editor):
        super().__init__(ed)
        self.ed = ed
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(2)
        self.tools = {}
        for name, label, key in EDIT_TOOLS:
            b = IconButton(ed, name, f"{label}  ·  {key}", self)
            b.clicked.connect(lambda _=False, n=name: ed.set_tool(n))
            self.tools[name] = b
            row.addWidget(b)
        row.addWidget(Divider(self))
        self.swatches = []
        for i in range(len(SWATCHES)):
            s = Swatch(ed, i, self)
            s.clicked.connect(lambda _=False, i=i: ed.set_color(i))
            self.swatches.append(s)
            row.addWidget(s)
        self.custom = CustomSwatch(ed, self)
        self.custom.clicked.connect(ed.toggle_picker)
        self.swatches.append(self.custom)
        row.addWidget(self.custom)
        self.size_button = SizeButton(ed, self)
        self.size_button.clicked.connect(ed.cycle_size)
        row.addWidget(self.size_button)
        row.addWidget(Divider(self))
        for icon, hint, slot in (("undo", "Undo  ·  Ctrl+Z", ed.undo), ("redo", "Redo  ·  Ctrl+Shift+Z", ed.redo),
                                 ("fit", "Fit the picture in the window  ·  Ctrl+0", lambda: ed.canvas.fit())):
            b = IconButton(ed, icon, hint, self)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        for text, hint, slot, primary in (("Copy", "Copy the picture  ·  Ctrl+C", ed.copy, False),
                                          ("Save as…", "Save to another file  ·  Ctrl+Shift+S", ed.save_as, False),
                                          ("Save", "", ed.save, True)):
            chip = Chip(ed, text, hint, self, primary=primary)
            chip.clicked.connect(slot)
            row.addWidget(chip)
            if primary:
                self.save_chip = chip
        self.setFixedHeight(48)

    def refresh(self):
        for name, b in self.tools.items():
            b.set_active(name == self.ed.tool)
        for s in self.swatches:
            s.set_selected(s.index == self.ed.color_index)
            s.update()
        self.size_button.set_size(self.ed.size)
        self.save_chip.hint = f"Save to {self.ed.path}  ·  Ctrl+S"

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), C.BASE)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), C.LINE)


class _Status(QWidget):
    """The bottom line: a hint (or what just happened), and the picture's
    size and zoom."""

    def __init__(self, ed: Editor):
        super().__init__(ed)
        self.ed = ed
        self.setFixedHeight(28)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), C.BASE)
        p.fillRect(QRectF(0, 0, self.width(), 1), C.LINE)
        f = font(12)
        p.setFont(f)
        p.setPen(C.SOFT if self.ed._status else C.MUTED)
        text = self.ed._status or self.ed.hint or (
            "Click a pixel to make it your colour  ·  Esc to stop" if self.ed.eyedropper else HINT)
        right = f"{self.ed.base.width()} × {self.ed.base.height()}   {round(self.ed.canvas.zoom * 100)}%"
        rw = QFontMetricsF(f).horizontalAdvance(right)
        p.drawText(QRectF(12, 0, self.width() - rw - 36, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(C.MUTED)
        p.drawText(QRectF(self.width() - rw - 12, 0, rw, self.height()), Qt.AlignmentFlag.AlignVCenter, right)


class Canvas(QWidget):
    """The picture and its annotations, moved and zoomed freely: ``offset``
    is where the picture's top left is, ``zoom`` its scale (1: as big as on
    the screen it was taken on)."""

    def __init__(self, ed: Editor):
        super().__init__(ed)
        self.ed = ed
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self.fitted = True  # follows the window's size until moved or zoomed
        self._pan: QPointF | None = None  # where a pan began (pointer, offset)
        self._pan_offset = QPointF(0, 0)
        self._space = False
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)

    # -- the view ------------------------------------------------------------

    def picture_size(self) -> QSizeF:
        return self.ed.base.deviceIndependentSize()

    def to_picture(self, pos: QPointF) -> QPointF:
        return (pos - self.offset) / self.zoom

    def fit(self):
        """The whole picture in view, no bigger than it really is."""
        size = self.picture_size()
        if size.width() <= 0 or size.height() <= 0:
            return
        self.zoom = min(1.0, (self.width() - 32) / size.width(), (self.height() - 32) / size.height())
        self.zoom = max(ZOOM[0], self.zoom)
        self.offset = QPointF((self.width() - size.width() * self.zoom) / 2,
                              (self.height() - size.height() * self.zoom) / 2)
        self.fitted = True
        self._changed()

    def zoom_to(self, zoom: float, around: QPointF | None = None):
        """Zoom, keeping the point under ``around`` (default: the middle) still."""
        around = around if around is not None else QPointF(self.width() / 2, self.height() / 2)
        zoom = min(max(zoom, ZOOM[0]), ZOOM[1])
        at = self.to_picture(around)
        self.zoom = zoom
        self.offset = around - at * zoom
        self.fitted = False
        self._keep_in_view()
        self._changed()

    def pan_by(self, d: QPointF):
        self.offset += d
        self.fitted = False
        self._keep_in_view()
        self._changed()

    def _keep_in_view(self):
        """At least a bit of the picture stays in the window."""
        size = self.picture_size() * self.zoom
        keep = 48.0
        x = min(max(self.offset.x(), keep - size.width()), self.width() - keep)
        y = min(max(self.offset.y(), keep - size.height()), self.height() - keep)
        self.offset = QPointF(x, y)

    def _changed(self):
        self.update()
        self.ed.status.update()

    def resizeEvent(self, event):
        if self.fitted:
            self.fit()
        else:
            self._keep_in_view()

    def set_space(self, down: bool):
        self._space = down
        self.update_cursor()

    def update_cursor(self):
        if self._pan is not None:
            shape = Qt.CursorShape.ClosedHandCursor
        elif self._space:
            shape = Qt.CursorShape.OpenHandCursor
        elif self.ed.tool == "text" and not self.ed.eyedropper:
            shape = Qt.CursorShape.IBeamCursor
        else:
            shape = Qt.CursorShape.CrossCursor
        self.setCursor(shape)

    def cancel(self) -> bool:
        """Esc: drop the shape being drawn, or finish the text."""
        if self.active_shape() is not None:
            self.ed.active = None
            self.update()
            return True
        return self.ed.commit_text()

    def active_shape(self):
        return self.ed.active

    # -- input ---------------------------------------------------------------

    def mousePressEvent(self, event):
        pos = event.position()
        button = event.button()
        if button == Qt.MouseButton.MiddleButton or (button == Qt.MouseButton.LeftButton and self._space):
            self._pan, self._pan_offset = pos, QPointF(self.offset)
            self.update_cursor()
            return
        if button != Qt.MouseButton.LeftButton:
            return
        ed = self.ed
        at = self.to_picture(pos)
        if ed.eyedropper:
            ed.take_color(at)
            return
        ed.close_picker()
        if ed.commit_text() and ed.tool == "text":
            return  # the first click finishes the text being typed
        if ed.tool == "text":
            ed.begin_text(shapes.Text(at, ed.color, ed.size))
        elif ed.tool == "counter":
            ed.commit(shapes.Counter(at, ed.color, ed.size, ed.next_number()))
        else:
            ed.active = shapes.create(ed.tool, at, ed.color, ed.size)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._pan is not None:
            self.offset = self._pan_offset + (pos - self._pan)
            self.fitted = False
            self._keep_in_view()
            self._changed()
            return
        if self.ed.active is not None:
            self.ed.active.extend(self.to_picture(pos), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self.update()

    def mouseReleaseEvent(self, event):
        if self._pan is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan = None
            self.update_cursor()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.ed.active is not None:
            shape, self.ed.active = self.ed.active, None
            if shape.is_valid():
                self.ed.commit(shape)
            self.update()

    def wheelEvent(self, event):
        """Scrolling moves the picture (Shift: sideways); Ctrl+scroll zooms
        around the pointer. Touchpads move it both ways at once."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120
            if steps:
                self.zoom_to(self.zoom * 1.15 ** steps, event.position())
            return
        pixels = event.pixelDelta()
        d = QPointF(pixels) if not pixels.isNull() else QPointF(event.angleDelta()) / 120 * 48
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not d.x():
            d = QPointF(d.y(), 0)
        self.pan_by(d)

    def event(self, event):
        if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_to(self.zoom * (1 + event.value()), event.position())
            return True
        return super().event(event)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), C.INK)
        size = self.picture_size() * self.zoom
        frame = QRectF(self.offset, size)
        p.setPen(QPen(C.LINE, 1))
        p.drawRect(frame.adjusted(-0.5, -0.5, 0.5, 0.5))
        p.save()
        p.translate(self.offset)
        p.scale(self.zoom, self.zoom)
        # Close up, whole pixels (as a paint program shows them); further out, smooth.
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1.0)
        p.drawPixmap(QPointF(0, 0), self.ed.base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for shape in self.ed.annotations:
            shape.paint(p, self.ed.base)
        if self.ed.active is not None:
            self.ed.active.paint(p, self.ed.base)
        if self.ed.text_edit is not None:
            self.ed.text_edit.paint(p, self.ed.base)
        p.restore()
        if not self.ed.annotations and self.ed.active is None and self.ed.text_edit is None:
            self._paint_tip(p)

    def _paint_tip(self, p: QPainter):
        """Until something is drawn: what to do, quietly, at the bottom."""
        f = font(12, QFont.Weight.Medium)
        text = "Pick a tool and draw  ·  Ctrl+S saves"
        w = QFontMetricsF(f).horizontalAdvance(text) + 28
        box = QRectF((self.width() - w) / 2, self.height() - 44, w, 28)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(C.SOFT)
        p.setFont(f)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
