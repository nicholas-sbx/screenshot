"""The annotation editor: a normal window for drawing on a screenshot already
taken (or any image), with the capture overlay's tools, Save, Save As and
Copy. The picture can be moved around and zoomed like in a paint program,
and any number of editors can be open at once. Loaded only when one opens."""

import sys
from pathlib import Path

from flatshot import config, icons, keys, output, picture, selection, shapes, theme, timing
from flatshot.qt import (
    QColor, QCursor, QEvent, QFont, QFontMetricsF, QGuiApplication, QHBoxLayout, QImage, QLabel, QLineEdit,
    QPainter, QPainterPath, QPen, QPixmap, QPoint, QPointF, QRectF, QRegularExpression, QRegularExpressionValidator,
    QSizeF, Qt, QTimer, QVBoxLayout, QWidget, keyval,
)
from flatshot.theme import C, SWATCHES, font
from flatshot.widgets import TOOLS, CustomSwatch, Divider, IconButton, SizeButton, Swatch, _Button

# The overlay's drawing tools (not capturing or recording), and the picture's own.
EDIT_TOOLS = [t for t in TOOLS if t[0] not in ("region", "record")] + [
    ("crop", "Crop, cut out, rotate and resize", "C")]
COLOUR_TOOLS = ("pen", "line", "arrow", "rect", "solid", "ellipse", "marker", "text", "counter")
CUSTOM = len(SWATCHES)  # the colour index of your own colour
ZOOM = (0.05, 32.0)
# A touchpad pinch (older PyQt6 builds, as in Debian 12, don't name these).
_gesture, _zoom = getattr(QEvent.Type, "NativeGesture", None), getattr(Qt.NativeGestureType, "ZoomNativeGesture", None)
PINCH = (_gesture, _zoom) if _gesture is not None and _zoom is not None else None
MODIFIER_KEYS = {keyval(getattr(Qt.Key, f"Key_{k}")) for k in ("Shift", "Control", "Alt", "AltGr", "Meta")}
SHAPE_HINT = "Shift: square  ·  Ctrl: from the middle  ·  Alt: move it"
TEXT_HINT = "Click to place text, or on text to change it  ·  Drag text to move it  ·  Shift+Enter for a new line"
CROP_HINT = ("Drag the edges to crop, past the picture for a margin  ·  Shift keeps the shape  ·  Ctrl: from the "
             "middle, without snapping  ·  Enter crops")
CUT_HINT = "Drag across the {what} to cut out; the rest is joined  ·  Esc to stop"
SELECT_HINT = "Click a drawing to select it (Alt: the one under it)  ·  Drag to move  ·  Handles resize  ·  Delete removes it"
SELECTED_HINT = ("Drag to move (Shift: straight)  ·  Handles: Shift keeps its shape, Ctrl from the middle  ·  "
                 "Arrows nudge  ·  Colours and sizes change it")
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
        # (Not self.screen(): PySide makes what that returns the window's child, so
        # closing the window would break every other use of that screen.)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.dpr = screen.devicePixelRatio() if screen is not None else 1.0
        self.base = QPixmap.fromImage(image)
        self.base.setDevicePixelRatio(self.dpr)
        self.annotations: list[shapes.Shape] = []
        self._saved: list[shapes.Shape] = []  # the annotations as last saved (or opened)
        self._saved_base = self.base  # the picture then (crop, rotate, ... change it)
        self.undone: list[shapes.Shape] = []
        self.active: shapes.Shape | None = None
        self.text_edit: shapes.Text | None = None
        self.tool = self.cfg.default_tool if self.cfg.default_tool in COLOUR_TOOLS else "pen"
        self.colour_tool = self.tool
        self.color_index = min(max(self.cfg.default_color, 0), CUSTOM)
        self.custom_color = QColor(self.cfg.custom_color)
        self.keymap = keys.Keymap(self.cfg.keys)
        self.size = min(max(self.cfg.default_size, 0), len(theme.SIZES) - 1)
        self.eyedropper = False
        self.picker = None
        self.hint: str | None = None
        self._status: str | None = None  # a passing message ("Saved to ..."), in place of the hint
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._unsay)
        self._closing = False  # closing for sure: the changes were saved or let go
        self.ask_save: AskSave | None = None

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.bar = _Bar(self)
        column.addWidget(self.bar)
        self.cropper = Cropper(self)
        self.picture_bar = _PictureBar(self)
        self.picture_bar.hide()
        column.addWidget(self.picture_bar)
        self.canvas = Canvas(self)
        self.selection = selection.Selection(self.canvas)  # the select tool's drawing
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
        if tool != "select":
            self.selection.clear()
        if tool == "crop" and self.tool != "crop":
            self.cropper.start()
        elif tool != "crop":
            self.cropper.stop()
        self.picture_bar.setVisible(tool == "crop")
        self.tool = tool
        if tool in COLOUR_TOOLS:
            self.colour_tool = tool
        self.refresh()

    def set_color(self, index: int):
        """A colour; from a tool that has none (pixelate, blur), back to the
        last drawing tool, so the colour is used. With the select tool, it
        changes the selected drawing."""
        self.color_index = index
        if self.tool == "select" and self.selection.restyle(color=self.color):
            self.refresh()
            return
        if self.tool not in COLOUR_TOOLS:
            self.tool = self.colour_tool
        if self.text_edit:
            self.text_edit.color = self.color
        self.refresh()

    def set_size(self, size: int):
        self.size = min(max(size, 0), len(theme.SIZES) - 1)
        if self.text_edit:
            self.text_edit.size = self.size
        if self.tool == "select":
            self.selection.restyle(size=self.size)
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
        self.picker.anchor = self.bar.custom
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
        return shapes.next_number(self.annotations)

    def commit(self, shape: shapes.Shape):
        self.annotations.append(shape)
        self.undone.clear()
        self._changed()

    def begin_text(self, shape: shapes.Text):
        self.commit_text()
        self.text_edit = shape
        if shape.replaces is not None:
            shape.replaces.hidden = True  # (the copy is shown while it's edited)
        self.bar.refresh()
        self.canvas.update()

    def commit_text(self) -> bool:
        if self.text_edit is None:
            return False
        shape, self.text_edit = self.text_edit, None
        shape.editing = False
        if shape.is_valid() and shape.changed():
            self.commit(shape)
        elif shape.replaces is not None:
            shape.replaces.hidden = False  # edited back to as it was
        self.bar.refresh()
        self.canvas.update()
        return True

    def text_at(self, pos: QPointF):
        """The text drawn at ``pos`` (picture coordinates, topmost), if any."""
        return next((s for s in reversed(self.annotations)
                     if isinstance(s, shapes.Text) and not s.hidden and s.contains(pos)), None)

    def can_undo(self) -> bool:
        return bool(self.annotations) or self.text_edit is not None

    def can_redo(self) -> bool:
        return bool(self.undone)

    def undo(self):
        self.commit_text()
        if self.annotations:
            shape = self.annotations.pop()
            if isinstance(shape, picture.Step):  # the picture and its drawings as they were
                self.base, drawings = shape.before
                self.annotations = list(drawings)
                self._picture_changed()
            elif getattr(shape, "replaces", None) is not None:
                shape.replaces.hidden = False  # the drawing as it was before it was changed
            self.undone.append(shape)
            self._changed()

    def redo(self):
        self.commit_text()
        if self.undone:
            shape = self.undone.pop()
            if isinstance(shape, picture.Step):
                self.base, drawings = shape.after
                self.annotations = list(drawings)
                self._picture_changed()
            elif getattr(shape, "replaces", None) is not None:
                shape.replaces.hidden = True
            self.annotations.append(shape)
            self._changed()

    # -- the picture itself (crop, cut out, rotate, flip, resize) --------------

    def change_picture(self, what: str, changed: tuple):
        """Put in the picture a picture.* function gave, as ``changed``:
        (new picture, how a point moves onto it). The drawings move with
        it. One undo step."""
        self.commit_text()
        base, fn = changed
        drawings = picture.move_drawings(self.annotations, fn)
        step = picture.Step(what, (self.base, list(self.annotations)), (base, drawings))
        self.base = base
        self.annotations = drawings + [step]
        self.undone.clear()
        self._picture_changed()
        self._changed()
        self._say(f"{what}: {base.width()} × {base.height()}")

    def _picture_changed(self):
        self.selection.clear()
        if self.tool == "crop":
            self.cropper.start()
        self.picture_bar.refresh()
        self.canvas.fit()

    def _changed(self):
        # Unsaved only while it differs from what was saved: undoing back
        # to that is saved again.
        self.dirty = self.annotations != self._saved or self.base is not self._saved_base
        self.bar.refresh()
        self._title()
        self.refresh()

    def refresh(self):
        self.bar.refresh()
        if self.picture_bar.isVisible():
            self.picture_bar.refresh()
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
            if not shape.hidden:
                shape.paint(p, self.base)
        p.end()
        image.setDevicePixelRatio(1.0)
        alpha = self.alpha or self.base.hasAlphaChannel()  # (a clear margin, say)
        return image.convertToFormat(QImage.Format.Format_ARGB32 if alpha else QImage.Format.Format_RGB32)

    def save(self) -> bool:
        return self._save_to(self.path)

    def save_as(self, then=None):
        """Ask where to, with the desktop's own file dialog (KDE's, through
        the portal, without blocking), then save there; then ``then()``."""
        from flatshot import filechooser

        formats = output.writable_formats()
        filters = [(label, [f"*.{key}"] + (["*.jpeg"] if key == "jpg" else [])) for key, label, _, _ in formats]
        ext = self.path.suffix.lower().lstrip(".")
        current = next((i for i, f in enumerate(formats) if f[0] == ("jpg" if ext == "jpeg" else ext)), 0)

        def chosen(path: str):
            if self.base.isNull():
                return  # (closed meanwhile)
            if self._save_to(Path(path)) and then is not None:
                then()

        filechooser.save_file(self, "Save as", self.path, filters, chosen, current)

    def _save_to(self, path: Path) -> bool:
        clock = timing.Clock("editor save")
        try:
            image = self.render()
            clock.step("drawn", f"{image.width()} × {image.height()}, {len(self.annotations)} drawings")
            written = output.save(image, self.cfg, explicit=str(path))
            clock.step("saved", str(written))
        except OSError as e:
            self._say(f"Couldn't save: {e}")
            return False
        self.path, self.dirty = written, False
        self._saved = list(self.annotations)
        self._saved_base = self.base
        self._title()
        self.bar.refresh()
        self._say(f"Saved to {written}")
        return True

    def copy(self):
        clock = timing.Clock("editor copy")
        image = self.render()
        clock.step("drawn")
        ok, _ = output.copy_image(image)
        clock.step("copied" if ok else "not copied")
        self._say("Copied to the clipboard" if ok else "Couldn't copy to the clipboard")
        if ok:
            self.bar.copied()

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
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.ask_save is not None and self.ask_save.isVisible():
            self.ask_save.key(event)
            return
        if self.text_edit is not None:
            shape = self.text_edit
            action = self.keymap.action(event, keys.EDITOR) if ctrl else None
            used = action in ("undo", "redo") and shape.undo_edit(redo=action == "redo")
            if not used:
                used = shape.key(event)
                if used == "commit":
                    self.commit_text()
            if used:
                self.canvas.update()
                return
        if self.tool == "select" and self.selection.key(event):
            self.refresh()  # (Delete, or an arrow key nudging it)
            return
        if self.tool == "crop" and k in (keyval(Qt.Key.Key_Return), keyval(Qt.Key.Key_Enter)):
            self.cropper.apply()
            return
        if self.canvas.modifier_changed(event):
            return
        if k == keyval(Qt.Key.Key_Space) and not event.isAutoRepeat():
            self.canvas.set_space(True)
        elif k == keyval(Qt.Key.Key_Escape):
            if self.eyedropper:
                self.close_picker()  # (its hint says Esc stops it)
            else:
                self.close_picker()  # it closes, and Esc still does what it does
                if self.canvas.cancel():
                    pass
                elif self.tool == "crop":
                    self.set_tool(self.colour_tool)  # (leaves the crop tool, not the editor)
                else:
                    self.close()
        elif (action := self.keymap.action(event, keys.EDITOR)) is not None:
            self._do(action)
        elif ctrl and k == keyval(Qt.Key.Key_Y):
            self.redo()  # (besides the Redo key)
        elif ctrl and k == keyval(Qt.Key.Key_Plus):
            self.canvas.zoom_to(self.canvas.zoom * 1.25)  # (Ctrl+= typed with Shift)
        else:
            super().keyPressEvent(event)

    def _do(self, action: str):
        """A key's action (keys.BINDINGS) in the editor."""
        canvas = self.canvas
        simple = {"undo": self.undo, "redo": self.redo, "save": self.save, "save_as": self.save_as,
                  "copy": self.copy, "fit": canvas.fit, "actual_size": lambda: canvas.zoom_to(1.0),
                  "zoom_in": lambda: canvas.zoom_to(canvas.zoom * 1.25),
                  "zoom_out": lambda: canvas.zoom_to(canvas.zoom / 1.25), "close": self.close,
                  "size.down": lambda: self.set_size(self.size - 1), "size.up": lambda: self.set_size(self.size + 1)}
        if action in simple:
            simple[action]()
        elif action.startswith("tool."):
            self.set_tool(action[5:])
        elif action.startswith("color."):
            self.set_color(int(action[6:]) - 1)

    def keyReleaseEvent(self, event):
        if keyval(event.key()) == keyval(Qt.Key.Key_Space) and not event.isAutoRepeat():
            self.canvas.set_space(False)
        self.canvas.modifier_changed(event)

    # -- closing -------------------------------------------------------------

    def closeEvent(self, event):
        self.commit_text()
        if self.dirty and not self._closing:
            # Asked inside the window, not in a dialog of its own.
            event.ignore()
            self.close_picker()
            if self.ask_save is None:
                self.ask_save = AskSave(self)
            self.ask_save.ask()
            return
        event.accept()
        if self in _open:
            _open.remove(self)
        self.base = QPixmap()  # (its Python side may outlive the window a while)
        self.annotations, self.undone, self._saved = [], [], []
        self._saved_base = QPixmap()
        if not _open:
            callbacks, _when_all_closed[:] = list(_when_all_closed), []
            for callback in callbacks:
                QTimer.singleShot(0, callback)

    def answer(self, choice: str):
        """The answer to "Save the changes?": save, discard or cancel."""
        self.ask_save.hide()
        self.setFocus()
        if choice == "save":
            if self.save():
                self._closing = True
                self.close()
        elif choice == "discard":
            self._closing = True
            self.close()

    def resizeEvent(self, event):
        if self.ask_save is not None:
            self.ask_save.setGeometry(self.rect())

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), C.BASE)


class AskSave(QWidget):
    """"Save the changes?" over the editor, in its own look: the window
    dimmed, a card in the middle with Don't save, Cancel and Save. Enter
    saves, Esc cancels."""

    def __init__(self, ed: Editor):
        super().__init__(ed)
        self.ed = ed
        self.title_font = font(15, QFont.Weight.DemiBold)
        self.body_font = font(13)
        self.buttons = []
        for text, icon, choice, strong in (("Don't save", "trash", "discard", False),
                                           ("Cancel", "close", "cancel", False), ("Save", "save", "save", True)):
            b = ActionButton(ed, text, icon, "", self)
            b.set_look(text, icon, strong)
            b.clicked.connect(lambda _=False, c=choice: ed.answer(c))
            self.buttons.append(b)
        self.hide()

    def ask(self):
        self.setGeometry(self.ed.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.update()

    def key(self, event):
        k = keyval(event.key())
        if k == keyval(Qt.Key.Key_Escape):
            self.ed.answer("cancel")
        elif k in (keyval(Qt.Key.Key_Return), keyval(Qt.Key.Key_Enter)):
            self.ed.answer("save")
        elif k == keyval(Qt.Key.Key_D) or (k == keyval(Qt.Key.Key_W)
                                            and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.ed.answer("discard")

    def keyPressEvent(self, event):
        self.key(event)

    def _card(self) -> QRectF:
        w = max(380.0, sum(b.width() for b in self.buttons) + 16 * 2 + 8 * 2)
        w = min(w, self.width() - 32.0)
        h = 168.0
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def resizeEvent(self, event):
        card = self._card()
        x = card.right() - 20
        for b in reversed(self.buttons):
            x -= b.width()
            b.move(round(x), round(card.bottom() - 20 - b.height()))
            x -= 8

    def mousePressEvent(self, event):
        event.accept()  # (nothing under it is clicked meanwhile)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QColor(C.DIM)
        shade.setAlpha(150)
        p.fillRect(self.rect(), shade)
        card = self._card()
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.setPen(C.TEXT)
        p.setFont(self.title_font)
        inner = card.adjusted(22, 20, -22, 0)
        name = QFontMetricsF(self.title_font).elidedText(f"Save the changes to {self.ed.path.name}?",
                                                         Qt.TextElideMode.ElideMiddle, inner.width())
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 24), Qt.AlignmentFlag.AlignVCenter, name)
        p.setPen(C.SOFT)
        p.setFont(self.body_font)
        p.drawText(QRectF(inner.left(), inner.top() + 32, inner.width(), 44),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   "Your drawings will be lost if you don't save them.")


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
            b = IconButton(ed, name, ed.keymap.hint(label, f"tool.{name}"), self)
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
        km = ed.keymap
        self.history = []
        for icon, hint, slot in (("undo", km.hint("Undo", "undo"), ed.undo), ("redo", km.hint("Redo", "redo"), ed.redo),
                                 ("fit", km.hint("Zoom to fit the picture in the window", "fit"),
                                  lambda: ed.canvas.fit())):
            b = IconButton(ed, icon, hint, self)
            b.clicked.connect(slot)
            self.history.append(b)
            row.addWidget(b)
        self.history.pop()  # (fit is always there to use)
        row.addStretch(1)
        row.setSpacing(4)
        self.copy_button = copy = ActionButton(ed, "Copy", "copy", km.hint("Copy the picture", "copy"), self,
                                               also=("Copied",))
        copy.clicked.connect(ed.copy)
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.timeout.connect(lambda: copy.set_look("Copy", "copy", False))
        save_as = ActionButton(ed, "Save as", "save-as", km.hint("Save to another file", "save_as"), self)
        save_as.clicked.connect(ed.save_as)
        self.save_button = ActionButton(ed, "Save", "save", "", self, also=("Saved",))
        self.save_button.clicked.connect(ed.save)
        for b in (copy, save_as, self.save_button):
            row.addWidget(b)
        self.setFixedHeight(48)

    def copied(self):
        """Copy says "Copied" for a moment."""
        self.copy_button.set_look("Copied", "check", False)
        self._copied_timer.start(2000)

    def refresh(self):
        self.history[0].setEnabled(self.ed.can_undo())
        self.history[1].setEnabled(self.ed.can_redo())
        for name, b in self.tools.items():
            b.set_active(name == self.ed.tool)
        for s in self.swatches:
            s.set_selected(s.index == self.ed.color_index)
            s.update()
        self.size_button.set_size(self.ed.size)
        # Save stands out only while there's something to save; then it
        # says so (that, and the window's title, are the unsaved mark).
        unsaved = self.ed.dirty
        self.save_button.set_look("Save" if unsaved else "Saved", "save" if unsaved else "check", unsaved)
        self.save_button.hint = self.ed.keymap.hint(f"Save to {self.ed.path}", "save") if unsaved \
            else f"Saved in {self.ed.path}"

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), C.BASE)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), C.LINE)


class ActionButton(_Button):
    """Copy, Save as and Save: an icon and a word, all alike; ``strong``
    fills it with the accent (Save, while there's something to save)."""

    def __init__(self, ed, text: str, icon: str, hint: str, parent=None, also: tuple[str, ...] = ()):
        """``also``: other words it may show; it's as wide as the widest,
        so changing words never moves anything."""
        super().__init__(ed, hint, parent)
        self.text, self.icon, self.strong = text, icon, False
        self._words = (text, *also)
        self._font = font(13, QFont.Weight.DemiBold)
        self._fit()

    def set_look(self, text: str, icon: str, strong: bool):
        if (text, icon, strong) != (self.text, self.icon, self.strong):
            self.text, self.icon, self.strong = text, icon, strong
            self.update()

    def _fit(self):
        fm = QFontMetricsF(self._font)
        self.setFixedSize(round(max(fm.horizontalAdvance(w) for w in self._words)) + 46, 34)

    def paintEvent(self, event):
        p = self._painter()
        hover = self.underMouse()
        if self.strong:
            p.setBrush(C.ACCENT.lighter(108) if hover else C.ACCENT)
            fg = C.ON_ACCENT
        else:
            p.setBrush(C.HOVER if hover else C.RAISED)
            fg = C.TEXT if hover else C.SOFT
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        # The icon and the word together, in the middle.
        text_w = QFontMetricsF(self._font).horizontalAdvance(self.text)
        x = (self.width() - (18 + 7 + text_w)) / 2
        icons.paint(p, self.icon, QRectF(x, (self.height() - 18) / 2, 18, 18), fg)
        p.setFont(self._font)
        p.setPen(fg)
        p.drawText(QRectF(x + 25, 0, text_w + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)


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
            "Click a pixel to make it your colour  ·  Esc to stop" if self.ed.eyedropper else
            SHAPE_HINT if self.ed.active is not None and hasattr(self.ed.active, "pointer") else
            TEXT_HINT if self.ed.tool == "text" else
            CUT_HINT.format(what=self.ed.cropper.cut) if self.ed.tool == "crop" and self.ed.cropper.cut else
            CROP_HINT if self.ed.tool == "crop" else
            (SELECTED_HINT if self.ed.selection.shape is not None else SELECT_HINT) if self.ed.tool == "select"
            else HINT)
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
        self._text_drag: tuple[QPointF, QPointF] | None = None  # a text being moved: (press, its position then)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)  # (accents, compose, CJK in text)

    # -- for the select tool (selection.Selection's host) ----------------------

    @property
    def annotations(self) -> list:
        return self.ed.annotations

    def commit(self, shape: shapes.Shape):
        self.ed.commit(shape)

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
        elif self.ed.tool in ("select", "crop") and not self.ed.eyedropper:
            shape = Qt.CursorShape.ArrowCursor
        else:
            shape = Qt.CursorShape.CrossCursor
        self.setCursor(shape)

    def cancel(self) -> bool:
        """Esc: drop the shape being drawn, finish the text, deselect, or
        put the crop back."""
        if self.ed.tool == "crop" and self.ed.cropper.cancel():
            return True
        if self.ed.selection.clear():
            self.ed.status.update()
            return True
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
        if ed.text_edit is not None and ed.text_edit.contains(at):
            # In the text being typed: the caret goes there (Shift: selects
            # to there); a drag moves the text.
            shape = ed.text_edit
            shape.place(shape.index_at(at), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self._text_drag = (at, QPointF(shape.pos))
            self.update()
            return
        if ed.commit_text() and ed.tool == "text" and ed.text_at(at) is None:
            return  # the first click finishes the text being typed
        if ed.tool == "text":
            old = ed.text_at(at)
            if old is not None:  # edit it again (a copy, so undo brings back the original)
                shape = old.copy_for_editing()
                shape.place(shape.index_at(at))
                self._text_drag = (at, QPointF(shape.pos))
            else:
                shape = shapes.Text(at, ed.color, ed.size)
            ed.begin_text(shape)
        elif ed.tool == "counter":
            ed.commit(shapes.Counter(at, ed.color, ed.size, ed.next_number()))
        elif ed.tool == "select":
            ed.selection.press(at, event.modifiers(), self.zoom)
            ed.refresh()
        elif ed.tool == "crop":
            ed.cropper.press(at, event.modifiers(), self.zoom)
        else:
            ed.active = shapes.create(ed.tool, at, ed.color, ed.size)
        self.update()
        ed.status.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._pan is not None:
            self.offset = self._pan_offset + (pos - self._pan)
            self.fitted = False
            self._keep_in_view()
            self._changed()
            return
        if self._text_drag is not None and self.ed.text_edit is not None:
            start, origin = self._text_drag
            at = self.to_picture(pos)
            if (at - start).manhattanLength() * self.zoom > 3 or self.ed.text_edit.pos != origin:
                self.ed.text_edit.pos = origin + (at - start)
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.update()
        elif self.ed.active is not None:
            self.ed.active.extend(self.to_picture(pos), **shapes.modifiers(event.modifiers()))
            self.update()
        elif self.ed.selection.move(self.to_picture(pos), event.modifiers()):
            pass
        elif self.ed.tool == "crop" and not self._space and not self.ed.eyedropper:
            at = self.to_picture(pos)
            if not self.ed.cropper.move(at, event.modifiers()):
                self.setCursor(self.ed.cropper.cursor(at, self.zoom))
            self.update()
        elif self.ed.tool == "select" and not self._space and not self.ed.eyedropper:
            self.setCursor(self.ed.selection.cursor(self.to_picture(pos), self.zoom))

    def mouseReleaseEvent(self, event):
        if self._text_drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._text_drag = None
            self.update_cursor()
            return
        if self._pan is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan = None
            self.update_cursor()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.ed.active is not None:
            shape, self.ed.active = self.ed.active, None
            if shape.is_valid():
                self.ed.commit(shape)
            self.update()
            self.ed.status.update()
        elif event.button() == Qt.MouseButton.LeftButton and self.ed.selection.release():
            self.ed.refresh()
        elif event.button() == Qt.MouseButton.LeftButton and self.ed.tool == "crop":
            self.ed.cropper.release()
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

    def modifier_changed(self, event) -> bool:
        """Shift, Ctrl or Alt pressed or let go while drawing a shape: it
        changes at once, without waiting for the pointer to move."""
        shape = self.ed.active
        if shape is None or keyval(event.key()) not in MODIFIER_KEYS or not hasattr(shape, "pointer"):
            return False
        mods = shapes.modifiers(QGuiApplication.queryKeyboardModifiers())
        mods["move"] = False  # (moving needs the pointer to move)
        shape.extend(shape.pointer, **mods)
        self.update()
        return True

    def inputMethodEvent(self, event):
        """Text from an input method (compose, dead keys, CJK, emoji)."""
        if self.ed.text_edit is not None and event.commitString():
            self.ed.text_edit.insert(event.commitString())
            self.update()
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.InputMethodQuery.ImEnabled:
            return self.ed.text_edit is not None
        if query == Qt.InputMethodQuery.ImCursorRectangle and self.ed.text_edit is not None:
            r = self.ed.text_edit.caret_rect()
            return QRectF(self.offset + r.topLeft() * self.zoom, r.size() * self.zoom).toAlignedRect()
        return super().inputMethodQuery(query)

    def event(self, event):
        if PINCH is not None and event.type() == PINCH[0] and event.gestureType() == PINCH[1]:
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
        # Only what's on the picture shows: nothing past its edges is saved.
        p.setClipRect(QRectF(QPointF(0, 0), self.picture_size()))
        for shape in self.ed.annotations:
            if not shape.hidden:
                shape.paint(p, self.ed.base)
        if self.ed.active is not None:
            self.ed.active.paint(p, self.ed.base)
        if self.ed.text_edit is not None:
            self.ed.text_edit.paint(p, self.ed.base)
        if self.ed.tool == "select":
            self.ed.selection.paint_moving(p, self.ed.base)
            p.setClipping(False)  # (its handles may stand past the picture's edges)
            self.ed.selection.paint_frame(p, self.zoom)
        p.restore()
        if self.ed.tool == "crop":
            self.ed.cropper.paint(p, self)
        elif not self.ed.annotations and self.ed.active is None and self.ed.text_edit is None:
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


class Cropper:
    """The crop tool: a frame round the part to keep, its edges dragged in
    (or out past the picture, for a margin), at a fixed shape if asked; or,
    when cutting out, a band dragged across the picture to remove."""

    HANDLE = 10.0

    def __init__(self, ed: Editor):
        self.ed = ed
        self.rect: QRectF | None = None  # what to keep (picture coordinates; may reach past it)
        self.ratio = "free"  # see picture.RATIOS
        self.margin_colour = False  # a margin in your colour, else clear
        self.cut: str | None = None  # "rows" or "columns" while cutting out a slice
        self._drag: tuple | None = None  # (handle, "move" or "new"; press position; the frame then)
        self._band: tuple[float, float] | None = None  # a slice being dragged across: (from, to)
        self._maps = None  # (the picture's cache key, its edge maps) for snapping

    def whole(self) -> QRectF:
        return QRectF(QPointF(0, 0), self.ed.base.deviceIndependentSize())

    def _default(self) -> QRectF:
        """The frame to start from: all of the picture (at the crop's shape)."""
        r = self.ratio_value()
        return picture.fit_ratio(self.whole(), r) if r else self.whole()

    def start(self):
        self.rect = self._default()
        self._drag = self._band = None
        self.cut = None

    def stop(self):
        self.rect = self._drag = self._band = None
        self.cut = None

    def changed(self) -> bool:
        if self.rect is None:
            return False
        w = self.whole()
        return any(abs(a - b) > 0.5 for a, b in ((self.rect.left(), w.left()), (self.rect.top(), w.top()),
                                                 (self.rect.right(), w.right()), (self.rect.bottom(), w.bottom())))

    # -- the options -----------------------------------------------------------

    def ratio_value(self) -> float | None:
        value = picture.RATIOS.get(self.ratio)
        if value == "original":
            w = self.whole()
            return w.width() / w.height() if w.height() else None
        return value

    def next_ratio(self):
        names = list(picture.RATIOS)
        self.ratio = names[(names.index(self.ratio) + 1) % len(names)]
        self._fit_ratio()
        self.ed.refresh()

    def _fit_ratio(self):
        r = self.ratio_value()
        if r and self.rect is not None:
            self.rect = picture.fit_ratio(self.rect, r)

    def set_cut(self, what: str | None):
        self.cut = None if self.cut == what else what
        self.ed.refresh()

    # -- snapping (as the capture overlay's magnet) -------------------------------

    def _snap(self, pt: QPointF, mods) -> QPointF:
        if not self.ed.cfg.snap_edges or mods & Qt.KeyboardModifier.ControlModifier:
            return pt
        from flatshot.overlay import edge_maps, snap_to_edges

        key = self.ed.base.cacheKey()
        if self._maps is None or self._maps[0] != key:
            self._maps = (key, edge_maps(self.ed.base.toImage()))
        return snap_to_edges(self._maps[1], pt, self.ed.base.devicePixelRatio(), self.ed.cfg)

    # -- the pointer ---------------------------------------------------------------

    def _handles(self) -> dict:
        r = self.rect
        xs = {"l": r.left(), "": r.center().x(), "r": r.right()}
        ys = {"t": r.top(), "": r.center().y(), "b": r.bottom()}
        return {yk + xk: QPointF(x, y) for yk, y in ys.items() for xk, x in xs.items() if yk + xk}

    def _handle_at(self, pos: QPointF, scale: float) -> str | None:
        if self.rect is None:
            return None
        grab = (self.HANDLE / 2 + 4) / scale
        return next((name for name, at in self._handles().items()
                     if abs(pos.x() - at.x()) <= grab and abs(pos.y() - at.y()) <= grab), None)

    def cursor(self, pos: QPointF, scale: float):
        if self.cut:
            return Qt.CursorShape.SplitVCursor if self.cut == "rows" else Qt.CursorShape.SplitHCursor
        handle = self._handle_at(pos, scale)
        if handle:
            return selection.CURSORS[handle]
        if self.rect is not None and self.rect.contains(pos) and self.changed():
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.CrossCursor

    def press(self, pos: QPointF, mods, scale: float):
        if self.cut:
            v = pos.y() if self.cut == "rows" else pos.x()
            self._band = (v, v)
            return
        handle = self._handle_at(pos, scale)
        if handle:
            self._drag = (handle, QPointF(pos), QRectF(self.rect))
        elif self.rect is not None and self.rect.contains(pos) and self.changed():
            self._drag = ("move", QPointF(pos), QRectF(self.rect))
        else:
            self._drag = ("new", self._snap(pos, mods), None)

    def move(self, pos: QPointF, mods) -> bool:
        if self._band is not None:
            self._band = (self._band[0], pos.y() if self.cut == "rows" else pos.x())
            return True
        if self._drag is None:
            return False
        handle, start, r0 = self._drag
        ratio = self.ratio_value()
        keep = ratio is not None or bool(mods & Qt.KeyboardModifier.ShiftModifier)
        center = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if handle == "new":
            end = self._snap(pos, mods)
            d = end - start
            if keep:
                r = ratio or 1.0
                if abs(d.x()) / r > abs(d.y()):
                    d = QPointF(d.x(), abs(d.x()) / r * (1 if d.y() >= 0 else -1))
                else:
                    d = QPointF(abs(d.y()) * r * (1 if d.x() >= 0 else -1), d.y())
            self.rect = QRectF(start - d, start + d) if center else QRectF(start, start + d)
        elif handle == "move":
            self.rect = r0.translated(pos - start)
        else:
            corner = QPointF(r0.left() if "l" in handle else r0.right() if "r" in handle else r0.center().x(),
                             r0.top() if "t" in handle else r0.bottom() if "b" in handle else r0.center().y())
            # The edge keeps its distance from the pointer, then snaps to an edge in the picture.
            d = self._snap(corner + (pos - start), mods) - corner
            self.rect = selection._resized(r0, handle, d, keep, center)
        self.rect = self.rect.normalized()
        return True

    def release(self):
        if self._band is not None:
            a, b = self._band
            self._band = None
            if abs(b - a) * self.ed.base.devicePixelRatio() >= 1:
                rows = self.cut == "rows"
                self.ed.change_picture("Cut out", picture.cut(self.ed.base, a, b, rows))
            return
        if self._drag is not None:
            self._drag = None
            if self.rect.width() < 2 or self.rect.height() < 2:
                self.rect = self._default()  # (a click: back to all of it)
            self.ed.refresh()

    def cancel(self) -> bool:
        """Esc: stop cutting, or put the frame back round the whole picture."""
        if self._drag is not None or self._band is not None:
            self._drag = self._band = None
        elif self.cut:
            self.cut = None
        elif self.rect is not None and self.rect != self._default():
            self.start()
        else:
            return False
        self.ed.refresh()
        return True

    def apply(self):
        """Crop to the frame (Enter)."""
        if not self.changed():
            return
        margin = QColor(self.ed.color) if self.margin_colour else None
        what = "Cropped" if self.whole().contains(self.rect) else "Margin added"
        self.ed.change_picture(what, picture.crop(self.ed.base, self.rect, margin))

    # -- painting (in the canvas's own coordinates) ----------------------------------

    def paint(self, p: QPainter, canvas: "Canvas"):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(canvas.offset, canvas.picture_size() * canvas.zoom)
        if self._band is not None:
            a, b = sorted(self._band)
            z = canvas.zoom
            band = (QRectF(frame.left(), canvas.offset.y() + a * z, frame.width(), (b - a) * z) if self.cut == "rows"
                    else QRectF(canvas.offset.x() + a * z, frame.top(), (b - a) * z, frame.height()))
            shade = QColor(theme.REC)
            shade.setAlpha(90)
            p.fillRect(band.intersected(frame), shade)
            p.setPen(QPen(theme.REC, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(band.intersected(frame))
        if self.rect is None or self.cut:
            p.restore()
            return
        r = QRectF(canvas.offset + self.rect.topLeft() * canvas.zoom, self.rect.size() * canvas.zoom)
        # The margin, where the frame reaches past the picture.
        outside = QPainterPath()
        outside.addRect(r)
        pic = QPainterPath()
        pic.addRect(frame)
        margin = outside.subtracted(pic)
        if not margin.isEmpty():
            if self.margin_colour:
                p.fillPath(margin, self.ed.color)
            else:
                p.fillPath(margin, _checks())
        # Shade what's cut away, then the frame and its handles.
        if self.changed():
            shade = QColor(C.DIM)
            shade.setAlpha(150)
            everything = QPainterPath()
            everything.addRect(QRectF(canvas.rect()))
            p.fillPath(everything.subtracted(outside), shade)
        p.setPen(QPen(C.ACCENT, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        p.setPen(QPen(C.ACCENT, 1.5))
        p.setBrush(C.TEXT)
        for at in self._handles().values():
            v = canvas.offset + at * canvas.zoom
            p.drawRoundedRect(QRectF(v.x() - self.HANDLE / 2, v.y() - self.HANDLE / 2, self.HANDLE, self.HANDLE), 3, 3)
        # Its size in pixels.
        d = self.ed.base.devicePixelRatio()
        label = f"{round(self.rect.width() * d)} × {round(self.rect.height() * d)}"
        f = font(12, QFont.Weight.DemiBold)
        w = QFontMetricsF(f).horizontalAdvance(label) + 18
        box = QRectF(r.left(), r.top() - 30, w, 24)
        if box.top() < 4:
            box.moveTop(r.top() + 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C.ACCENT)
        p.drawRoundedRect(box, 6, 6)
        p.setPen(C.ON_ACCENT)
        p.setFont(f)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        p.restore()


_checks_cache: list = []


def _checks():
    """A grey checkerboard brush, for what's transparent."""
    from flatshot.qt import QBrush

    if not _checks_cache:
        tile = QPixmap(16, 16)
        tile.fill(QColor("#9A9A9A"))
        p = QPainter(tile)
        p.fillRect(0, 0, 8, 8, QColor("#CFCFCF"))
        p.fillRect(8, 8, 8, 8, QColor("#CFCFCF"))
        p.end()
        _checks_cache.append(QBrush(tile))
    return _checks_cache[0]


class _PictureBar(QWidget):
    """Under the toolbar while cropping: the crop's shape and margin, cutting
    out rows or columns, rotating, flipping, resizing, and Crop."""

    def __init__(self, ed: Editor):
        super().__init__(ed)
        self.ed = ed
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(4)
        crop = ed.cropper
        self.ratio = ActionButton(ed, "Free", "ratio", "The crop's shape: click for the next. Shift keeps the shape "
                                  "while you drag.", self, also=("1:1", "4:3", "16:9", "Original"))
        self.ratio.clicked.connect(crop.next_ratio)
        self.margin = ActionButton(ed, "Clear", "margin", "Past the picture's edges: a clear margin, or one in your "
                                   "colour", self, also=("Colour",))
        self.margin.clicked.connect(self._toggle_margin)
        row.addWidget(self.ratio)
        row.addWidget(self.margin)
        row.addWidget(Divider(self))
        self.cut_rows = IconButton(ed, "cut-rows", "Cut out rows: drag across them, and the rest is joined", self)
        self.cut_rows.clicked.connect(lambda: crop.set_cut("rows"))
        self.cut_cols = IconButton(ed, "cut-cols", "Cut out columns: drag across them, and the rest is joined", self)
        self.cut_cols.clicked.connect(lambda: crop.set_cut("columns"))
        for b in (self.cut_rows, self.cut_cols):
            row.addWidget(b)
        row.addWidget(Divider(self))
        for icon, hint, fn in (
                ("rotate-left", "Rotate left", lambda: ed.change_picture("Rotated", picture.rotate(ed.base, False))),
                ("rotate-right", "Rotate right", lambda: ed.change_picture("Rotated", picture.rotate(ed.base, True))),
                ("flip-h", "Flip left to right", lambda: ed.change_picture("Flipped", picture.flip(ed.base, True))),
                ("flip-v", "Flip upside down", lambda: ed.change_picture("Flipped", picture.flip(ed.base, False)))):
            b = IconButton(ed, icon, hint, self)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addWidget(Divider(self))
        size = QRegularExpressionValidator(QRegularExpression(r"\d{1,5}%?"), self)
        self.w_field, self.h_field = QLineEdit(self), QLineEdit(self)
        for field, hint in ((self.w_field, "Width"), (self.h_field, "Height")):
            field.setValidator(size)
            field.setFixedWidth(64)
            field.setToolTip(f"{hint} in pixels, or a percentage (50%)")
            field.setPlaceholderText(hint)
            field.returnPressed.connect(self._resize)
        self.w_field.textEdited.connect(lambda t: self._follow(t, self.h_field, True))
        self.h_field.textEdited.connect(lambda t: self._follow(t, self.w_field, False))
        times = QLabel("×", self)
        self.lock = IconButton(ed, "ratio", "", self, size=34)
        self.lock.clicked.connect(self._toggle_lock)
        self.locked = True
        self.resize_button = ActionButton(ed, "Resize", "resize", "Resize the picture to this size", self)
        self.resize_button.clicked.connect(self._resize)
        for w in (self.w_field, times, self.h_field, self.lock, self.resize_button):
            row.addWidget(w)
        row.addStretch(1)
        self.apply = ActionButton(ed, "Crop", "crop", "Crop to the frame  ·  Enter", self)
        self.apply.clicked.connect(crop.apply)
        row.addWidget(self.apply)
        self.setFixedHeight(44)
        self.setStyleSheet(f"""
QLineEdit {{ background: {C.INK.name()}; color: {C.TEXT.name()}; border: 1px solid {C.LINE.name()};
    border-radius: 6px; padding: 5px 7px; font-size: 13px; }}
QLineEdit:focus {{ border-color: {C.MUTED.name()}; }}
QLabel {{ color: {C.MUTED.name()}; background: transparent; }}""")
        self.refresh()

    def _toggle_margin(self):
        self.ed.cropper.margin_colour = not self.ed.cropper.margin_colour
        self.ed.refresh()

    def _toggle_lock(self):
        self.locked = not self.locked
        self.refresh()

    def _follow(self, text: str, other: QLineEdit, from_width: bool):
        """With the shape kept, the other side follows the one typed."""
        if not self.locked or not text:
            return
        w, h = self.ed.base.width(), self.ed.base.height()
        if text.endswith("%"):
            other.setText(text)
        elif text.isdigit() and w and h:
            n = int(text)
            other.setText(str(max(1, round(n * h / w if from_width else n * w / h))))

    def _target(self) -> tuple[int, int] | None:
        """The size typed, in pixels."""
        def value(text: str, now: int) -> int | None:
            text = text.strip()
            if text.endswith("%") and text[:-1].isdigit():
                return max(1, round(now * int(text[:-1]) / 100))
            return int(text) if text.isdigit() and int(text) > 0 else None

        w = value(self.w_field.text(), self.ed.base.width())
        h = value(self.h_field.text(), self.ed.base.height())
        return (w, h) if w and h else None

    def _resize(self):
        size = self._target()
        if size is None or size == (self.ed.base.width(), self.ed.base.height()):
            return
        self.ed.change_picture("Resized", picture.resize(self.ed.base, *size))
        self.ed.canvas.setFocus()

    def refresh(self):
        crop = self.ed.cropper
        names = {"free": "Free", "1:1": "1:1", "4:3": "4:3", "16:9": "16:9", "original": "Original"}
        self.ratio.set_look(names[crop.ratio], "ratio", False)
        self.margin.set_look("Colour" if crop.margin_colour else "Clear", "margin", False)
        self.cut_rows.set_active(crop.cut == "rows")
        self.cut_cols.set_active(crop.cut == "columns")
        self.lock.set_toggle(self.locked, "ratio", "Keep the shape when resizing: on" if self.locked
                             else "Keep the shape when resizing: off")
        self.apply.set_look("Crop", "crop", crop.changed())
        for field, now in ((self.w_field, self.ed.base.width()), (self.h_field, self.ed.base.height())):
            if not field.hasFocus():
                field.setText(str(now))

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), C.BASE)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), C.LINE)
