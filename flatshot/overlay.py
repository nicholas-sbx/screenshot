"""One fullscreen overlay per monitor showing the frozen capture."""

import math
import os
import sys
import threading
import time

from flatshot.qt import (
    QColor, QEvent, QFont, QFontMetricsF, QGuiApplication, QImage, QInputDevice, QPainter, QPainterPath, QPen, QPixmap,
    QPoint, QPointF, QPolygonF, QRect, QRectF, QSizeF, Qt, QTimer, QWidget,
)

from flatshot import layershell, selection, shapes, timing
from flatshot.qt import keyval
from flatshot.theme import C, font
from flatshot.widgets import CodeChip, Toolbar

HINTS = {
    "region": "Drag to capture  ·  Click for whole screen  ·  Esc to cancel",
    "codes": "Drag to capture  ·  {codes} hides detected codes  ·  Esc to cancel",
    "windows": "Drag to capture  ·  Click a window to capture it  ·  Esc to cancel",
    "text": "Click to place text, or on text to change it  ·  Drag text to move it  ·  Enter to finish",
    "shape": "Shift: square  ·  Ctrl: from the middle  ·  Alt: move it",
    "select": "Click a drawing to select it  ·  Drag to move  ·  Handles resize  ·  Delete removes it",
    "selected": "Drag to move (Shift: straight)  ·  Handles: Shift keeps its shape, Ctrl from the middle  ·  "
                "Arrows nudge  ·  Delete",
}
PIN_HINT = "Drag to pin a region  ·  Click a window to pin it  ·  {pin} to save instead"
EYEDROPPER_HINT = "Click a pixel to make it your colour  ·  Esc to stop"
RECORD_HINTS = {
    "pick": "Drag the area to record  ·  Click for whole screen  ·  Esc to cancel",
    "windows": "Drag the area to record  ·  Click a window  ·  Enter for whole screen  ·  Esc to cancel",
    "armed": "Drag the edges to adjust  ·  Enter to start  ·  Esc to go back",
    "countdown": "Recording starts in {n}  ·  Esc to cancel",
}
HINT_NEAR = 60  # the hint fades while the pointer is this close to it (logical px)
HANDLE = 10  # size of the resize handles on an area chosen for recording
# Snapping a selection to edges in the picture. How far an edge pulls is a
# setting (snap_distance); so is the sensitivity, which sets how strong an
# edge must be and how long a straight run of it. An edge is looked at this
# far (logical px) either side of the pointer along its length.
SNAP_REACH = 40
# A vertical and a horizontal edge meeting: both ending there (an L, a box's
# corner), one ending on the other (a T), or crossing.
CORNER_BONUS, TEE_BONUS, CROSS_BONUS = 1.6, 1.25, 1.1
# A box's corner may be rounded: its sides then stop short of the corner, by
# the same amount, with a curve between them. Up to this radius (logical px).
ROUNDED_UP_TO = 24
RAINBOW_SECONDS = 4  # one trip round the colours
MODIFIER_KEYS = {keyval(getattr(Qt.Key, f"Key_{k}")) for k in ("Shift", "Control", "Alt", "AltGr", "Meta")}


def _buffer(image: QImage) -> memoryview:
    """An image's bytes, without copying them (PySide6 and PyQt6 differ)."""
    bits = image.constBits()
    if hasattr(bits, "setsize"):
        bits.setsize(image.sizeInBytes())
    return memoryview(bits).cast("B")
DRAW_HINT = "Draw on the screen  ·  {tool.region} then drag to capture  ·  Enter for whole screen"
LOUPE_ZOOM = (3.0, 40.0)  # magnifier zoom range, screen px per captured pixel
LOUPE_EDGE = QColor(0, 0, 0, 120)  # the thin dark edge of the magnifier's square and crosshair


_fallback_reported = False


def _report_fallback():
    """Say once, on stderr (the journal for the tray), why KWin will animate the overlay."""
    global _fallback_reported
    if not _fallback_reported and "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() \
            and QGuiApplication.platformName().startswith("wayland"):
        _fallback_reported = True
        print(f"flatshot: overlay is a normal window, so KWin animates it: {layershell.status}", file=sys.stderr)


_HANDLE_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


class Overlay(QWidget):
    def __init__(self, ctl, screen, base: QPixmap, origin: QPoint):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.target_screen = screen
        self.base = base
        self.origin = origin  # top-left of this screen in the full capture, physical px
        self.desktop_scale: float | None = None  # the full capture's pixels per logical px
        self._pixels: QImage | None = None
        self._plain: QPixmap | None = None  # the picture without the pointer, while it's shown
        self._with_pointer: QPixmap | None = None
        self.annotations: list[shapes.Shape] = []
        self.undone: list[shapes.Shape] = []
        self.active: shapes.Shape | None = None
        self.sel_origin: QPointF | None = None
        self.sel_rect: QRectF | None = None
        self.cursor_pos: QPointF | None = None
        self.codes: list[tuple[object, QPolygonF]] = []
        self.windows: list[tuple[QRectF, object]] = []  # topmost first
        self.hover_window: tuple[QRectF, object] | None = None
        self.chips: list[CodeChip] = []
        self._code_hover: CodeChip | None = None  # a code's card on top while the pointer is on the code
        self.toolbar: Toolbar | None = None
        # Recording: the chosen area (logical), how it was chosen, and a drag
        # that moves or resizes it: (handle, press position, rect at press).
        self.rec_rect: QRectF | None = None
        self.rec_window = None  # the window clicked, when the area is one
        self._rec_drag: tuple[str, QPointF, QRectF] | None = None
        self.panel = None  # recordpanel.RecordPanel, made when first needed
        self.picker = None  # colorpicker.ColorPicker, made when first opened
        # A selection made on another monitor that reaches this one (local
        # coordinates), the pointer when it's here during that drag, and a
        # window hovered on another monitor that is partly here.
        self.span_rect: QRectF | None = None
        self.span_pointer: QPointF | None = None
        self.span_hover: tuple[QRectF, object] | None = None
        # Snapping: edge maps of the picture (built only once snapping is on)
        # and where the pointer snaps to.
        self._edge_maps = None
        self._edge_thread: threading.Thread | None = None
        self._snap_point: QPointF | None = None
        self._loupe_box = QRect()
        # The text tool: a text being dragged to a new place (press position, its position then).
        self._text_drag: tuple[QPointF, QPointF] | None = None
        self.selection = selection.Selection(self)  # the select tool's drawing, on this monitor
        self._rainbow: QTimer | None = None
        if ctl.cfg.rainbow:
            self._rainbow = QTimer(self)
            self._rainbow.setInterval(50)
            self._rainbow.timeout.connect(self._rainbow_tick)

        self.setWindowTitle("Flatshot")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)  # (accents, compose, CJK in text)
        self.update_cursor()

    # -- window ------------------------------------------------------------

    def show_on_screen(self):
        screen = self.target_screen
        if hasattr(self, "setScreen"):
            self.setScreen(screen)
        self.setGeometry(screen.geometry())
        if layershell.apply(self, screen):
            # An overlay layer surface: no window animations, above panels.
            self.show()
        else:
            _report_fallback()
            if not hasattr(self, "setScreen"):
                self.create()
                self.windowHandle().setScreen(screen)
            self.showFullScreen()
        self.raise_()
        self.activateWindow()
        if self._rainbow:
            self._rainbow.start()
        if self.ctl.snap_edges:
            QTimer.singleShot(0, self.snap_changed)

    def add_toolbar(self):
        self.toolbar = Toolbar(self.ctl, self)
        self.toolbar.hide()
        self._track(self.toolbar)
        self._place_floating()

    def _track(self, widget: QWidget):
        """Keep the crosshair following the pointer over ``widget`` (the
        toolbar, a card, the recording panel) and its buttons."""
        for w in [widget] + widget.findChildren(QWidget):
            w.setMouseTracking(True)
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Move and obj is self.toolbar and self.picker_open():
            self.show_picker()  # (the toolbar was dragged: the picker stays under it)
        if event.type() == QEvent.Type.MouseMove and isinstance(obj, QWidget) and not _by_touch(event):
            # (A finger on a button isn't a pointer: the crosshair and the
            # magnifier stay where they were.)
            self.cursor_pos = QPointF(obj.mapTo(self, event.position().toPoint()))
            self._snap_point = None
            self.ctl.activate(self)
            self._update_hover()
            self.update()
        return False

    def resizeEvent(self, event):
        self._place_floating()

    def _place_floating(self):
        if self.toolbar and not self.toolbar.placed and self.width() > self.toolbar.width():
            self.toolbar.place()
        self._place_chips()
        self._place_panel()

    def _place_chips(self):
        """Each code's card on its code, moved down (or up) clear of the
        cards already placed, so none covers another."""
        placed: list[QRectF] = []
        bounds = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        order = sorted(zip(self.chips, self.codes), key=lambda cc: (cc[1][1].boundingRect().center().y(),
                                                                    cc[1][1].boundingRect().center().x()))
        for chip, (_, poly) in order:
            c = poly.boundingRect().center()
            home = QRectF(c.x() - chip.width() / 2, c.y() - chip.height() / 2, chip.width(), chip.height())
            home.moveLeft(max(bounds.left(), min(home.left(), bounds.right() - home.width())))
            step = chip.height() + 4
            spot = home
            for n in range(1, 2 * len(order) + 2):
                if not any(spot.adjusted(-2, -2, 2, 2).intersects(r) for r in placed) and bounds.contains(spot):
                    break
                shift = (n + 1) // 2 * step * (1 if n % 2 else -1)  # down 1, up 1, down 2, ...
                spot = home.translated(0, shift)
            else:
                spot = home
            placed.append(spot)
            chip.move(round(spot.x()), round(spot.y()))

    def _update_code_hover(self):
        """The code under the pointer (its green box or its card) and its
        card go on top of the others while it's there."""
        hovered = None
        if self.cursor_pos is not None and self.chips and self.chips[0].isVisible():
            pt = self.cursor_pos
            hovered = next((chip for chip in reversed(self.chips) if chip.geometry().contains(pt.toPoint())), None)
            if hovered is None:
                hovered = next((chip for chip, (_, poly) in zip(self.chips, self.codes)
                                if _code_box(poly).contains(pt)), None)
        if hovered is self._code_hover:
            return
        self._code_hover = hovered
        for chip in self.chips:  # back to the usual order, then the hovered one on top
            chip.raise_()
        if hovered is not None:
            hovered.raise_()
        for w in (self.toolbar, self.panel, self.picker):
            if w is not None and w.isVisible():
                w.raise_()
        self.update()

    def _place_panel(self):
        """Under the area chosen for recording, else above it, else inside."""
        show = self.rec_rect is not None and self._rec_drag is None and self.ctl.countdown is None
        if not show:
            if self.panel:
                self.panel.hide()
            return
        if self.panel is None:
            from flatshot.recordpanel import RecordPanel

            self.panel = RecordPanel(self.ctl, self)
            self._track(self.panel)
        self.panel.refresh()
        r, w, h = self.rec_rect, self.panel.width(), self.panel.height()
        x = max(8.0, min(r.center().x() - w / 2, self.width() - w - 8))
        if r.bottom() + 16 + h <= self.height() - 8:
            y = r.bottom() + 16
        elif r.top() - 16 - h >= 8:
            y = r.top() - 16 - h
        else:
            y = r.bottom() - h - 60
        self.panel.move(round(x), round(y))
        self.panel.show()
        self.panel.raise_()

    def show_picker(self):
        """The picker for your own colour, under its swatch on the toolbar."""
        if self.picker is None:
            from flatshot.colorpicker import ColorPicker

            self.picker = ColorPicker(self.ctl, self)
            self._track(self.picker)
        self.picker.set_color(self.ctl.custom_color)
        swatch, bar = self.toolbar.custom, self.toolbar.geometry()
        w, h = self.picker.width(), self.picker.height()
        x = swatch.mapTo(self, QPoint(swatch.width() // 2, 0)).x() - w // 2
        y = bar.bottom() + 8 if bar.bottom() + 8 + h <= self.height() - 8 else bar.top() - 8 - h
        self.picker.move(max(8, min(x, self.width() - w - 8)), max(8, y))
        self.picker.anchor = self.toolbar.custom
        self.picker.show()
        self.picker.raise_()

    def hide_picker(self):
        if self.picker_open():
            self.picker.hide()
            self.setFocus()

    def picker_open(self) -> bool:
        return self.picker is not None and self.picker.isVisible()

    def release(self):
        """Let go of the pictures: the overlay is closing, and its Python
        side can outlive it (held by a closure or an undo list)."""
        self.base = self._plain = self._with_pointer = QPixmap()
        self._pixels = None
        self._edge_maps = None
        self.annotations, self.undone, self.active = [], [], None
        self.codes, self.windows, self.chips = [], [], []
        self._code_hover = self.hover_window = self.span_hover = None

    def dpr(self) -> float:
        return self.base.devicePixelRatio()

    def show_pointer(self, desktop: QImage, show: bool, own: QImage | None = None):
        """Show this screen's part of ``desktop`` (the capture with the mouse
        pointer in it), or this screen's ``own`` picture with it, or go back
        to the picture without it."""
        if self._plain is None:
            self._plain = self.base
        if show and self._with_pointer is None:
            pm = QPixmap.fromImage(own if own is not None else desktop.copy(QRect(self.origin, self._plain.size())))
            pm.setDevicePixelRatio(self._plain.devicePixelRatio())
            self._with_pointer = pm
        self.base = self._with_pointer if show else self._plain
        self._pixels = None
        self.update()

    def pixels(self) -> QImage:
        if self._pixels is None:
            self._pixels = self.base.toImage()
        return self._pixels

    # -- codes -------------------------------------------------------------

    def set_codes(self, codes):
        """Keep codes whose centre lies on this screen, in logical coords.
        (They're found in the whole desktop's picture, whose scale may not
        be this screen's own.)"""
        dpr = self.desktop_scale or self.dpr()
        size = self.target_screen.geometry().size()
        phys = QRectF(QPointF(self.origin), QSizeF(size.width() * dpr, size.height() * dpr))
        for code in codes:
            poly = QPolygonF(code.corners)
            if not phys.contains(poly.boundingRect().center()):
                continue
            local = QPolygonF([(pt - QPointF(self.origin)) / dpr for pt in code.corners])
            self.codes.append((code, local))
            chip = CodeChip(self.ctl, code, self)
            self._track(chip)
            self.chips.append(chip)
        self._place_floating()

    def set_windows(self, found):
        """Window bounds from KWin (global logical coords) on this screen."""
        g = self.target_screen.geometry()
        bounds = QRectF(0, 0, g.width(), g.height())
        self.windows = []
        for w in found:
            r = QRectF(w.rect.translated(-g.topLeft())).intersected(bounds)
            if r.width() >= 8 and r.height() >= 8:
                self.windows.append((r, w))
        self._update_hover()

    def _update_hover(self):
        if self.chips:
            self._update_code_hover()
        hover = None
        if (self.ctl.tool in ("region", "record") and self.sel_rect is None and self.rec_rect is None
                and self.cursor_pos is not None and not self._over_floating()):
            hover = next((item for item in self.windows if item[0].contains(self.cursor_pos)), None)
        if hover is not self.hover_window:
            self.hover_window = hover
            self.ctl.hover_changed(self, hover[1] if hover else None)
            self.update()

    def set_span_hover(self, window):
        """``window`` is hovered on another monitor: highlight its part here."""
        part = next(((r, w) for r, w in self.windows if w is window), None) if window is not None else None
        if part != self.span_hover:
            self.span_hover = part
            self.update()

    def _drag_bounds(self) -> QRectF:
        """Where a selection may reach: this monitor, or the whole desktop."""
        if self.ctl.spans():
            from flatshot import capture

            return QRectF(capture.virtual_geometry().translated(-self.target_screen.geometry().topLeft()))
        return QRectF(self.rect())

    def dismiss_code(self, code):
        for i, (c, _) in enumerate(self.codes):
            if c is code:
                del self.codes[i]
                chip = self.chips.pop(i)
                if chip is self._code_hover:
                    self._code_hover = None
                chip.deleteLater()
                break
        self.update()

    # -- state -------------------------------------------------------------

    def refresh(self):
        show_chips = self.ctl.tool == "region" and self.sel_rect is None and self.ctl.codes_visible
        for chip in self.chips:
            chip.setVisible(show_chips)
        if self.toolbar:
            self.toolbar.refresh()
        if self.ctl.tool != "record":
            self.rec_rect = self.rec_window = self._rec_drag = None
        self._place_panel()
        if self.cursor_pos is not None:
            self._update_hover()
        self.update_cursor()
        self.update()

    def update_cursor(self):
        text = self.ctl.tool == "text" and not self.ctl.eyedropper
        if self.ctl.tool == "select" and not self.ctl.eyedropper:
            shape = self.selection.cursor(self.cursor_pos) if self.cursor_pos is not None else Qt.CursorShape.ArrowCursor
        else:
            shape = Qt.CursorShape.IBeamCursor if text else Qt.CursorShape.CrossCursor
        self.setCursor(shape)

    def commit(self, shape: shapes.Shape):
        self.annotations.append(shape)
        self.undone.clear()
        self.ctl.record(self)
        self.update()

    def undo(self) -> bool:
        if not self.annotations:
            return False
        shape = self.annotations.pop()
        if getattr(shape, "replaces", None) is not None:
            shape.replaces.hidden = False  # the text as it was before it was edited
        self.undone.append(shape)
        self.update()
        return True

    def redo(self) -> bool:
        if not self.undone:
            return False
        shape = self.undone.pop()
        if getattr(shape, "replaces", None) is not None:
            shape.replaces.hidden = True
        self.annotations.append(shape)
        self.update()
        return True

    def text_at(self, pos: QPointF):
        """The text drawn at ``pos`` (topmost), if any."""
        return next((s for s in reversed(self.annotations)
                     if isinstance(s, shapes.Text) and not s.hidden and s.contains(pos)), None)

    def cancel_gesture(self) -> bool:
        if self.selection.clear():
            return True
        if self.sel_rect is not None or self.active is not None or self._rec_drag is not None:
            spanned = self.sel_rect is not None and self.ctl.spans()
            self.sel_origin = self.sel_rect = self.active = self._rec_drag = self._text_drag = None
            if spanned:
                self.ctl.selection_moved(self, None)
            self.ctl.refresh()
            return True
        if self.rec_rect is not None:
            self.rec_rect = self.rec_window = None
            self.ctl.area_moved(self)
            self.ctl.refresh()
            return True
        return False

    def arm(self, rect: QRectF, window=None):
        """Choose ``rect`` (logical, this screen's coordinates) to record. It
        may cross onto other monitors where the recorder can take that."""
        self.rec_rect = QRectF(rect).intersected(self._drag_bounds())
        self.rec_window = window
        if self.ctl.spans():
            self.ctl.selection_moved(self, None)  # (the drag that chose it is over)
        self.ctl.area_moved(self)
        self.ctl.refresh()

    # -- snapping to edges in the picture -------------------------------------

    def snap_changed(self):
        """Snapping was turned on or off. When it's on, start building the
        edge maps (once per overlay), in the background so nothing waits."""
        self._snap_point = None
        if self.ctl.snap_edges and self._edge_maps is None and self._edge_thread is None:
            image = self.pixels()  # (from the pixmap: GUI thread only)
            self._edge_thread = threading.Thread(target=self._build_edges, args=(image,), daemon=True)
            self._edge_thread.start()
        self.update()

    def _build_edges(self, image: QImage):
        started = time.monotonic()
        self._edge_maps = edge_maps(image)
        timing.log(f"snapping: edge maps for {image.width()} × {image.height()} in "
                   f"{(time.monotonic() - started) * 1000:.0f} ms")

    def _snapped(self, pos: QPointF, modifiers) -> QPointF:
        """``pos`` moved onto nearby edges in the picture, if any (Ctrl: as
        is)."""
        if not self.ctl.snap_edges or modifiers & Qt.KeyboardModifier.ControlModifier:
            return pos
        maps = self._edge_maps
        if maps is None:  # still being built: place freely until then
            self.snap_changed()
            return pos
        return snap_to_edges(maps, pos, self.dpr(), self.ctl.cfg)

    def _handle_at(self, pos: QPointF) -> str | None:
        """Which handle of the recording area is at ``pos``: "tl", "t", ...,
        "move" inside it, or None."""
        r = self.rec_rect
        if r is None:
            return None
        xs = {"l": r.left(), "c": r.center().x(), "r": r.right()}
        ys = {"t": r.top(), "c": r.center().y(), "b": r.bottom()}
        grab = HANDLE / 2 + 4
        for yk, y in ys.items():
            for xk, x in xs.items():
                if (xk, yk) != ("c", "c") and abs(pos.x() - x) <= grab and abs(pos.y() - y) <= grab:
                    return (yk if yk != "c" else "") + (xk if xk != "c" else "")
        # Nothing to move when it's the whole screen: a drag picks a new area.
        return "move" if r.contains(pos) and r != QRectF(self.rect()) else None

    def _drag_area(self, pos: QPointF, snapped: QPointF | None = None, center: bool = False):
        """Move or resize the recording area; a resized edge goes to
        ``snapped`` when snapping found an edge there. ``center`` (Ctrl):
        the opposite edge moves the other way, so the area stays centred."""
        handle, start, r0 = self._rec_drag
        d = pos - start
        r = QRectF(r0)
        bounds = self._drag_bounds()
        if handle == "move":
            r.translate(d)
            r.moveLeft(max(bounds.left(), min(r.left(), bounds.right() - r.width())))
            r.moveTop(max(bounds.top(), min(r.top(), bounds.bottom() - r.height())))
        else:
            sx = snapped.x() if snapped is not None and snapped.x() != pos.x() else None
            sy = snapped.y() if snapped is not None and snapped.y() != pos.y() else None
            if center:
                # The dragged edge follows the pointer as without Ctrl; the other mirrors it.
                c = r0.center()
                if "l" in handle or "r" in handle:
                    edge = (r0.left() if "l" in handle else r0.right()) + d.x()
                    half = min(abs(edge - c.x()), c.x() - bounds.left(), bounds.right() - c.x())
                    r.setLeft(c.x() - half)
                    r.setRight(c.x() + half)
                if "t" in handle or "b" in handle:
                    edge = (r0.top() if "t" in handle else r0.bottom()) + d.y()
                    half = min(abs(edge - c.y()), c.y() - bounds.top(), bounds.bottom() - c.y())
                    r.setTop(c.y() - half)
                    r.setBottom(c.y() + half)
            else:
                if "l" in handle:
                    r.setLeft(sx if sx is not None else r0.left() + d.x())
                if "r" in handle:
                    r.setRight(sx if sx is not None else r0.right() + d.x())
                if "t" in handle:
                    r.setTop(sy if sy is not None else r0.top() + d.y())
                if "b" in handle:
                    r.setBottom(sy if sy is not None else r0.bottom() + d.y())
                r = r.normalized().intersected(bounds)
        self.rec_rect = r
        self.rec_window = None
        self.ctl.area_moved(self)

    # -- output ------------------------------------------------------------

    def render(self, rect: QRectF | None) -> QImage:
        """The capture plus annotations, cropped to ``rect`` (logical)."""
        dpr = self.dpr()
        if rect is None:
            phys = self.base.rect()
        else:
            phys = QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                         round(rect.width() * dpr), round(rect.height() * dpr)).intersected(self.base.rect())
        image = self.pixels().copy(phys).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        p = QPainter(image)
        p.translate(-phys.x() / dpr, -phys.y() / dpr)
        for shape in self.annotations:
            if not shape.hidden:
                shape.paint(p, self.base)
        p.end()
        image.setDevicePixelRatio(1.0)
        return image.convertToFormat(QImage.Format.Format_RGB32)

    # -- input -------------------------------------------------------------

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            if not (self.ctl.cancel_countdown() or self.ctl.close_picker() or self.cancel_gesture()):
                self.ctl.cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.ctl.eyedropper:
            self.ctl.take_color(self, pos)
            return
        self.ctl.close_picker()  # a click elsewhere closes it, and does what it would anyway
        editing = self.ctl.text_edit
        if editing and editing[0] is self and editing[1].contains(pos):
            # In the text being typed: the caret goes there (Shift: selects
            # to there); a drag moves the text.
            shape = editing[1]
            shape.place(shape.index_at(pos), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self._text_drag = (pos, QPointF(shape.pos))
            self.update()
            return
        if self.ctl.commit_text() and self.ctl.tool == "text" and self.text_at(pos) is None:
            return  # first click just finishes the text being typed
        tool = self.ctl.tool
        if tool == "record" and self.ctl.countdown is not None:
            return
        handle = self._handle_at(pos) if tool == "record" else None
        if handle:
            self._rec_drag = (handle, pos, QRectF(self.rec_rect))
            self.ctl.refresh()
        elif tool in ("region", "record"):
            self.ctl.disarm()
            pos = self._snapped(pos, event.modifiers())
            self.sel_origin = pos
            self.sel_rect = QRectF(pos, pos)
            self.ctl.refresh()
        elif tool == "text":
            old = self.text_at(pos)
            if old is not None:  # edit it again (a copy, so undo brings back the original)
                shape = old.copy_for_editing()
                shape.place(shape.index_at(pos))
                self._text_drag = (pos, QPointF(shape.pos))
            else:
                shape = shapes.Text(pos, self.ctl.color, self.ctl.size)
            self.ctl.begin_text(self, shape)
        elif tool == "counter":
            self.commit(shapes.Counter(pos, self.ctl.color, self.ctl.size, self.ctl.next_number()))
        elif tool == "select":
            self.ctl.select_on(self)
            self.selection.press(pos, event.modifiers())
            self.ctl.refresh()
        else:
            self.active = shapes.create(tool, pos, self.ctl.color, self.ctl.size)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        self.cursor_pos = pos
        self.ctl.activate(self)
        selecting = self.ctl.tool in ("region", "record") and self.active is None
        # Where the selection would start or end (the crosshair shows it).
        self._snap_point = self._snapped(pos, event.modifiers()) if selecting and self.ctl.snap_edges else None
        if self.sel_origin is not None:
            end = self._snap_point or pos
            self.sel_rect = QRectF(self.sel_origin, end).normalized().intersected(self._drag_bounds())
            self.hover_window = None
            if self.ctl.spans():
                self.ctl.selection_moved(self, pos)
        elif self._rec_drag is not None:
            self._drag_area(pos, self._snap_point, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        elif self._text_drag is not None and self.ctl.text_edit and self.ctl.text_edit[0] is self:
            start, origin = self._text_drag
            if (pos - start).manhattanLength() > 3 or self.ctl.text_edit[1].pos != origin:
                self.ctl.text_edit[1].pos = origin + (pos - start)
                self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self.active is not None:
            self.active.extend(pos, **shapes.modifiers(event.modifiers()))
        elif self.selection.move(pos, event.modifiers()):
            pass
        else:
            self._update_hover()
            if self.ctl.tool == "select":
                self.update_cursor()
            if self.ctl.tool == "record":
                self.setCursor(_HANDLE_CURSORS.get(self._handle_at(pos), Qt.CursorShape.CrossCursor))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._text_drag is not None:
            self._text_drag = None
            self.update_cursor()
            return
        if self.sel_origin is not None:
            rect = self.sel_rect
            self.sel_origin = self.sel_rect = None
            window = None
            if rect is None or rect.width() < 3 or rect.height() < 3:
                # A click captures the window under the pointer, else the screen.
                self.cursor_pos = event.position()
                self._update_hover()
                rect, window = self.hover_window if self.hover_window else (None, None)
            g = self.target_screen.geometry()
            if self.ctl.tool == "record":
                if window is not None and self.ctl.spans():  # all of a window that crosses monitors
                    rect = QRectF(window.rect.translated(-g.topLeft()))
                self.arm(rect if rect is not None else QRectF(self.rect()), window)
            elif self.ctl.spans() and window is not None and not g.contains(window.rect):
                self.ctl.capture_span(window.rect, window)  # all of a window that crosses monitors
            elif self.ctl.spans() and rect is not None and not QRectF(self.rect()).contains(rect):
                self.ctl.capture_span(rect.translated(QPointF(g.topLeft())).toAlignedRect())
            else:
                self.ctl.capture(self, rect, window)
        elif self._rec_drag is not None:
            self._rec_drag = None
            if self.rec_rect.width() < 8 or self.rec_rect.height() < 8:
                self.rec_rect = None
            self.ctl.refresh()
        elif self.active is not None:
            shape, self.active = self.active, None
            if shape.is_valid():
                self.commit(shape)
            self.update()
        elif self.selection.release():
            self.ctl.refresh()

    def wheelEvent(self, event):
        """Scrolling zooms the magnifier in (up) and out (down)."""
        steps = event.angleDelta().y() / 120
        if not steps or not (self.ctl.tool in ("region", "record") and self.ctl.cfg.show_loupe):
            return
        self.ctl.loupe_zoom = max(LOUPE_ZOOM[0], min(self.ctl.loupe_zoom * 1.25 ** steps, LOUPE_ZOOM[1]))
        self.update()

    def enterEvent(self, event):
        self.ctl.activate(self)
        # This may be the overlay appearing under a pointer that hasn't moved:
        # show the crosshair and magnifier there now, not on the first move.
        self.point_at(event.position())

    def point_at(self, pos: QPointF):
        """The pointer is at ``pos`` (local), without a move to say so."""
        if self.cursor_pos is not None or not QRectF(self.rect()).contains(pos):
            return
        self.cursor_pos = QPointF(pos)
        self.ctl.activate(self)
        self._update_hover()
        self.update()

    def leaveEvent(self, event):
        self.cursor_pos = None
        self.hover_window = None
        self.update()

    def keyPressEvent(self, event):
        if not self._modifier_changed(event):
            self.ctl.key(self, event)

    def keyReleaseEvent(self, event):
        if not self._modifier_changed(event):
            self.ctl.key_release(self, event)

    def _modifier_changed(self, event) -> bool:
        """Shift, Ctrl or Alt pressed or let go while drawing a shape: it
        changes at once, without waiting for the pointer to move."""
        if self.active is None or keyval(event.key()) not in MODIFIER_KEYS:
            return False
        if hasattr(self.active, "pointer"):
            mods = shapes.modifiers(QGuiApplication.queryKeyboardModifiers())
            mods["move"] = False  # (moving needs the pointer to move)
            self.active.extend(self.active.pointer, **mods)
            self.update()
        return True

    def inputMethodEvent(self, event):
        """Text from an input method (compose, dead keys, CJK, emoji)."""
        if self.ctl.text_edit and self.ctl.text_edit[0] is self and event.commitString():
            self.ctl.text_edit[1].insert(event.commitString())
            self.update()
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.InputMethodQuery.ImEnabled:
            return bool(self.ctl.text_edit)
        if query == Qt.InputMethodQuery.ImCursorRectangle and self.ctl.text_edit:
            return self.ctl.text_edit[1].caret_rect().toAlignedRect()
        return super().inputMethodQuery(query)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        if not QRectF(QPointF(0, 0), self.base.deviceIndependentSize()).contains(QRectF(self.rect())):
            p.fillRect(self.rect(), C.INK)
        p.drawPixmap(0, 0, self.base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for shape in self.annotations:
            if not shape.hidden:
                shape.paint(p, self.base)
        if self.active is not None:
            self.active.paint(p, self.base)
        if self.ctl.text_edit and self.ctl.text_edit[0] is self:
            self.ctl.text_edit[1].paint(p, self.base)
        if self.ctl.tool == "select":
            self.selection.paint(p, self.base)

        region = self.ctl.tool in ("region", "record")
        picking = region and self.rec_rect is None  # still choosing an area
        hover = self.hover_window or (self.span_hover if self.ctl.spans() else None)
        selection = self.sel_rect if self.sel_rect is not None else self.span_rect
        if region:
            focus = selection if selection is not None else self.rec_rect if self.rec_rect is not None else (
                hover[0] if hover else None)
            if self.ctl.dim.alpha():
                # Plain rectangles around the hole: far cheaper than filling
                # an anti-aliased path over the whole (possibly 4K) screen.
                full = QRectF(self.rect())
                if focus is None:
                    p.fillRect(full, self.ctl.dim)
                else:
                    f = focus.intersected(full)
                    for r in (QRectF(full.left(), full.top(), full.width(), f.top() - full.top()),
                              QRectF(full.left(), f.bottom(), full.width(), full.bottom() - f.bottom()),
                              QRectF(full.left(), f.top(), f.left() - full.left(), f.height()),
                              QRectF(f.right(), f.top(), full.right() - f.right(), f.height())):
                        if r.width() > 0 and r.height() > 0:
                            p.fillRect(r, self.ctl.dim)
            if selection is None and hover:
                self._paint_window(p, *hover)
            # Codes sit above window highlights.
            if self.sel_rect is None and self.ctl.codes_visible and self.ctl.tool == "region":
                self._paint_codes(p)
        if selection is not None:
            self._paint_selection(p, selection, label=self.sel_rect is not None)
        elif self.rec_rect is not None:
            self._paint_selection(p, self.rec_rect)
            if self.ctl.countdown is not None:
                self._paint_countdown(p, self.rec_rect, self.ctl.countdown)
            elif self._rec_drag is None:
                self._paint_handles(p, self.rec_rect)
        elif picking and self.cursor_pos is not None and self.ctl.cfg.show_crosshair:
            self._paint_crosshair(p, self._snap_point or self.cursor_pos)
        if self.span_rect is not None and self.span_pointer is not None and self.ctl.cfg.show_loupe:
            self._paint_loupe(p, self.span_pointer)  # a drag from another monitor is here now
        elif ((picking and self.ctl.cfg.show_loupe or self.ctl.eyedropper) and self.cursor_pos is not None
              and self._loupe_allowed() and self.rect().contains(self.cursor_pos.toPoint())):
            self._paint_loupe(p, self.cursor_pos)
        if self.sel_rect is None and self is self.ctl.pointer_overlay and self.ctl.cfg.show_hint:
            self._paint_hint(p)

    def _over_floating(self, margin: int = 0) -> bool:
        pt = self.cursor_pos.toPoint()
        floating = [w for w in (self.toolbar, self.panel, self.picker) if w is not None]
        return any(w.isVisible() and w.geometry().adjusted(-margin, -margin, margin, margin).contains(pt)
                   for w in floating + self.chips)

    def _loupe_allowed(self) -> bool:
        return not self._over_floating(24)

    def _paint_window(self, p: QPainter, r: QRectF, window):
        """Quiet highlight: a thin translucent outline plus a small dark label."""
        edge = QColor(C.TEXT)
        edge.setAlpha(110)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        dpr = self.dpr()
        size = f"{round(r.width() * dpr)} × {round(r.height() * dpr)}"
        title = window.title if len(window.title) <= 40 else window.title[:39] + "…"
        self._pill(p, f"{title}  ·  {size}" if title else size, QPointF(r.left() + 8, r.top() + 8),
                   small=True)

    def _paint_codes(self, p: QPainter):
        """A padded rounded box around each detected code (under its card)."""
        p.save()
        pen = QPen(C.CODE, 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(C.CODE_SOFT)
        hovered = None
        for chip, (_, poly) in zip(self.chips, self.codes):
            if chip is self._code_hover:
                hovered = poly
                continue
            box = _code_box(poly)
            p.drawRoundedRect(box, min(8.0, box.height() / 3), min(8.0, box.height() / 3))
        if hovered is not None:  # on top of the others, looking the same (it isn't a button)
            box = _code_box(hovered)
            p.drawRoundedRect(box, min(8.0, box.height() / 3), min(8.0, box.height() / 3))
        p.restore()

    def _paint_selection(self, p: QPainter, r: QRectF, label: bool = True):
        p.setPen(QPen(C.ACCENT, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(-1, -1, 1, 1))
        if not label:
            return
        dpr = self.dpr()
        label = f"{round(r.width() * dpr)} × {round(r.height() * dpr)}"
        if self.ctl.tool == "record":
            label = f"Record  ·  {label}"
        elif self.ctl.pin_mode:
            label = f"Pin  ·  {label}"
        here = r.intersected(QRectF(self.rect()))  # (a selection may reach other monitors)
        anchor = here if not here.isEmpty() else r
        self._pill(p, label, QPointF(anchor.left(), anchor.top() - 10), anchor_bottom=True,
                   bg=C.ACCENT, fg=C.ON_ACCENT, keep_inside=True)

    def _paint_handles(self, p: QPainter, r: QRectF):
        p.save()
        p.setPen(QPen(C.ACCENT, 1.5))
        p.setBrush(C.TEXT)
        for x in (r.left(), r.center().x(), r.right()):
            for y in (r.top(), r.center().y(), r.bottom()):
                if (x, y) != (r.center().x(), r.center().y()):
                    p.drawRoundedRect(QRectF(x - HANDLE / 2, y - HANDLE / 2, HANDLE, HANDLE), 3, 3)
        p.restore()

    def _paint_countdown(self, p: QPainter, r: QRectF, n: int):
        size = max(56.0, min(140.0, r.width() * 0.6, r.height() * 0.6))
        box = QRectF(r.center().x() - size / 2, r.center().y() - size / 2, size, size)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C.BASE)
        p.drawEllipse(box)
        p.setPen(QPen(C.ACCENT, 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        total = max(1, self.ctl.cfg.record_countdown)
        p.drawArc(box.adjusted(-6, -6, 6, 6), 90 * 16, round(-360 * 16 * n / total))
        p.setPen(C.TEXT)
        p.setFont(font(round(size / 2), QFont.Weight.Bold))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(n))
        p.restore()

    def _mark_color(self) -> QColor:
        """The crosshair's and the magnifier square's colour: the accent, or
        a colour going round the rainbow."""
        if self.ctl.cfg.rainbow:
            return QColor.fromHsvF((time.monotonic() / RAINBOW_SECONDS) % 1.0, 0.75, 1.0)
        return QColor(C.ACCENT)

    def _rainbow_tick(self):
        """Repaint just the crosshair and the magnifier, so the colour moves."""
        if self.cursor_pos is None or not self.isVisible():
            return
        pt = (self._snap_point or self.cursor_pos).toPoint()
        if self.ctl.cfg.show_crosshair:
            self.update(QRect(pt.x() - 2, 0, 5, self.height()))
            self.update(QRect(0, pt.y() - 2, self.width(), 5))
        if not self._loupe_box.isEmpty():
            self.update(self._loupe_box.adjusted(-2, -2, 2, 2))

    def _paint_crosshair(self, p: QPainter, pos: QPointF):
        color = self._mark_color()
        color.setAlpha(110)
        p.setPen(QPen(color, 1))
        x, y = round(pos.x()) + 0.5, round(pos.y()) + 0.5
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))

    def _paint_loupe(self, p: QPainter, pos: QPointF):
        size = float(self.ctl.cfg.loupe_size)
        cap = self.dpr()  # captured pixels per logical pixel
        dev = self.devicePixelRatioF()  # screen pixels per logical pixel
        px, py = int(pos.x() * cap), int(pos.y() * cap)
        x = pos.x() + 24 if pos.x() + 24 + size < self.width() else pos.x() - 24 - size
        y = pos.y() + 24 if pos.y() + 24 + size + 34 < self.height() else pos.y() - 24 - size - 34
        # Drawn in screen pixels, a captured pixel an even number of them
        # across: the grid, the square and the lines all land on whole pixels,
        # and the pointer's pixel has a middle for the lines to go through.
        u = max(1, round(dev))  # the width of a line
        bx, by, side = round(x * dev), round(y * dev), round(size * dev)
        radius = 12 * dev
        cell = max(4 * u, 2 * round(self.ctl.loupe_zoom * dev / 2))
        lead = (side - cell) // 2  # from the edge to the pointer's pixel, which is in the middle
        before = -(-lead // cell)  # pixels shown before it (left and above), the first maybe in part
        count = before + 1 + -(-(side - lead - cell) // cell)
        a, b = bx + lead, by + lead  # the pointer's pixel's top left
        box = QRect(bx, by, side, side)
        p.save()
        p.scale(1 / dev, 1 / dev)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(box), radius, radius)
        p.setClipPath(clip)
        p.fillRect(box, C.INK)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(QRect(a - before * cell, b - before * cell, count * cell, count * cell), self.base,
                     QRect(px - before, py - before, count, count))
        p.restore()
        # Lines go on after the rounded clip is gone (lines through a clip path
        # are slow), as whole-pixel rectangles.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if cell >= 6 * u:
            p.drawPixmap(bx, by, self._loupe_grid(side, cell, lead % cell, radius, u))
        # The square around the pointer's pixel, on the grid lines round it,
        # and, while choosing an area, the crosshair: through the middle of
        # that pixel and joined to the square, or, where it snaps, on the
        # boundary between pixels (exactly where the edge is). All of it in
        # the crosshair's colour with a thin dark edge, which shows it on any
        # pixel, even one of its own colour.
        # Rectangles as (x, y, width, height). The edges don't overlap, so
        # none is darker where they meet.
        ring = (a - u, b - u, cell + 3 * u, cell + 3 * u)
        hole = (a + u, b + u, cell - u, cell - u)  # the pixel, inside the grid lines
        around = (a - 2 * u, b - 2 * u, cell + 5 * u, cell + 5 * u)  # the ring and its edge
        marks = _cut(ring, hole)
        edges = _cut(around, hole)
        if self.ctl.tool in ("region", "record"):
            snap = self._snap_point
            lx = ly = None
            if snap is not None and snap.x() != pos.x():
                lx = a + (round(snap.x() * cap) - px) * cell
            if snap is not None and snap.y() != pos.y():
                ly = b + (round(snap.y() * cap) - py) * cell
            lx = a + cell // 2 if lx is None else lx
            ly = b + cell // 2 if ly is None else ly
            across = None
            if bx + 2 * u <= lx <= bx + side - 3 * u:
                inset = max(_corner_inset(lx - u - bx, side, radius, u),
                            _corner_inset(lx + 2 * u - 1 - bx, side, radius, u))
                marks += _cut((lx, by + inset, u, side - 2 * inset), ring)
                across = (lx - u, by + inset, 3 * u, side - 2 * inset)
                edges += _cut(across, around)
            if by + 2 * u <= ly <= by + side - 3 * u:
                inset = max(_corner_inset(ly - u - by, side, radius, u),
                            _corner_inset(ly + 2 * u - 1 - by, side, radius, u))
                marks += _cut((bx + inset, ly, side - 2 * inset, u), ring)
                for part in _cut((bx + inset, ly - u, side - 2 * inset, 3 * u), around):
                    edges += _cut(part, across) if across else [part]
        for x0, y0, w0, h0 in edges:
            p.fillRect(x0, y0, w0, h0, LOUPE_EDGE)
        mark = self._mark_color()
        for x0, y0, w0, h0 in marks:
            p.fillRect(x0, y0, w0, h0, mark)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, u))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(box).adjusted(u / 2, u / 2, -u / 2, -u / 2), radius - u / 2, radius - u / 2)
        p.restore()
        self._loupe_box = QRectF(bx / dev, by / dev, side / dev, side / dev).toAlignedRect()
        img = self.pixels()
        color = img.pixelColor(min(max(px, 0), img.width() - 1), min(max(py, 0), img.height() - 1)).name().upper()
        self._pill(p, f"{px}, {py}   {color}", QPointF(x, y + size + 6), mono=True)

    _grids: dict = {}

    def _loupe_grid(self, side: int, cell: int, offset: int, radius: float, u: int) -> QPixmap:
        """The magnifier's pixel grid (in screen pixels), thin lines with its
        rounded corners cut off, made once per size and zoom."""
        key = (side, cell, offset, radius, u)
        grid = Overlay._grids.get(key)
        if grid is None:
            grid = QPixmap(side, side)
            grid.fill(Qt.GlobalColor.transparent)
            g = QPainter(grid)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, side, side), radius, radius)
            g.setClipPath(clip)
            color = QColor(128, 128, 128, 70)
            for at in range(offset, side, cell):
                if at > 0:
                    g.fillRect(at, 0, u, side, color)
                    g.fillRect(0, at, side, u, color)
            g.end()
            if len(Overlay._grids) > 32:
                Overlay._grids.clear()
            Overlay._grids[key] = grid
        return grid

    def _paint_hint(self, p: QPainter):
        tool = self.ctl.tool
        if tool == "region" and self.codes and self.ctl.codes_visible:
            tool = "codes"
        elif tool == "region" and self.windows:
            tool = "windows"
        if self.ctl.pin_mode and self.ctl.tool == "region":
            tool = "pin"
        if self.active is not None and hasattr(self.active, "pointer"):
            tool = "shape"  # (while dragging one out)
        if tool == "select" and self.selection.shape is not None:
            tool = "selected"
        if self.ctl.tool == "record":
            if self.ctl.countdown is not None:
                text = RECORD_HINTS["countdown"].format(n=self.ctl.countdown)
            else:
                armed = any(o.rec_rect is not None for o in self.ctl.overlays)
                text = self.ctl.hint or self.ctl.record_problem() or RECORD_HINTS[
                    "armed" if armed else "windows" if self.windows else "pick"]
        elif self.ctl.eyedropper:
            text = self.ctl.hint or EYEDROPPER_HINT
        else:
            text = self.ctl.hint or _with_keys(PIN_HINT if tool == "pin" else HINTS.get(tool, DRAW_HINT),
                                               self.ctl.keymap)
        # In the middle of the screen, faded while the pointer is near it.
        fm = QFontMetricsF(font(12, QFont.Weight.Medium))
        width = fm.horizontalAdvance(text) + 28
        at = QPointF((self.width() - width) / 2, (self.height() - 28) / 2)
        near = self.cursor_pos is not None and QRectF(at, QSizeF(width, 28)).adjusted(
            -HINT_NEAR, -HINT_NEAR, HINT_NEAR, HINT_NEAR).contains(self.cursor_pos)
        self._pill(p, text, at, opacity=0.3 if near else 1.0)

    def _pill(self, p, text, at: QPointF, anchor_bottom=False, bg=None, fg=None, mono=False, keep_inside=False,
              small=False, opacity=1.0):
        f = font(11 if small else 12, QFont.Weight.DemiBold if bg is not None else QFont.Weight.Medium, mono=mono)
        fm = QFontMetricsF(f)
        box = QRectF(0, 0, fm.horizontalAdvance(text) + (18 if small else 28), 22 if small else 28)
        box.moveTopLeft(at - QPointF(0, box.height()) if anchor_bottom else at)
        if keep_inside and box.top() < 4:
            box.moveTop(at.y() + 20)  # no room above the selection: tuck inside
        p.save()
        p.setOpacity(opacity)
        p.setPen(QPen(C.LINE, 1) if bg is None else Qt.PenStyle.NoPen)
        p.setBrush(bg or C.BASE)
        p.drawRoundedRect(box, 6 if small else 8, 6 if small else 8)
        p.setPen(fg or C.SOFT)
        p.setFont(f)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()


def edge_maps(image: QImage):
    """Two maps of the picture, one byte per pixel: how much each pixel
    differs from the one to its left, and from the one above. Qt does
    the work (a difference blend and a greyscale conversion), about
    150 ms for a 4K screen. (For snap_to_edges; any thread.)"""
    img = image.convertToFormat(QImage.Format.Format_RGB32)
    img.setDevicePixelRatio(1.0)
    maps = []
    for dx, dy in ((1, 0), (0, 1)):
        diff = img.copy()
        p = QPainter(diff)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
        p.drawImage(dx, dy, img)
        p.end()
        grey = diff.convertToFormat(QImage.Format.Format_Grayscale8)
        maps.append((grey, _buffer(grey), grey.bytesPerLine()))
    return maps


def snap_to_edges(maps, pos: QPointF, dpr: float, cfg) -> QPointF:
    """``pos`` (logical) moved onto nearby edges in the picture the maps
    (edge_maps) are of, if any. Edges that look like parts of boxes win:
    long straight runs, and above all a vertical and a horizontal one that
    meet. ``cfg``: snap_distance and snap_sensitivity."""
    (vgrey, vbuf, vstride), (hgrey, hbuf, hstride) = maps
    w, h = vgrey.width(), vgrey.height()
    x, y = int(pos.x() * dpr), int(pos.y() * dpr)
    if not (0 <= x < w and 0 <= y < h):
        return pos
    sens = min(max(cfg.snap_sensitivity, 1), 10)
    radius = max(2, round(cfg.snap_distance * dpr))
    reach = max(8, round(SNAP_REACH * dpr))
    step = max(1, round(dpr))  # sample about one logical pixel apart along an edge
    rounding = round(ROUNDED_UP_TO * dpr)
    strong = 10 + (10 - sens) * 4  # how much a pixel must differ across the edge
    min_run = max(3, round((8 + (10 - sens) * 2.2) * dpr / step))  # in samples
    # Vertical edges near x: each candidate column, sampled down the rows around y.
    c0, c1 = max(1, x - radius), min(w, x + radius + 1)
    rows = range(max(0, y - reach), min(h, y + reach + 1), step)
    columns = zip(*(vbuf[r * vstride + c0:r * vstride + c1] for r in rows))
    xs = _candidates(columns, c0, x, rows, y, radius, strong, min_run, rounding // step)
    # Horizontal edges near y: each candidate row, sampled along the columns around x.
    r0, r1 = max(1, y - radius), min(h, y + radius + 1)
    cols = range(max(0, x - reach), min(w, x + reach + 1), step)
    lines = (hbuf[r * hstride + cols.start:r * hstride + cols.stop:step] for r in range(r0, r1))
    ys = _candidates(lines, r0, y, cols, x, radius, strong, min_run, rounding // step)

    def edge_at(cx: float, cy: float, maps=(0, 1), spread: int = 1, faint: bool = False) -> bool:
        """Is there an edge at (cx, cy), or within ``spread``? In the
        vertical-edge map (0), the horizontal one (1) or either."""
        cx, cy = round(cx), round(cy)
        need = strong // 2 if faint else strong
        for row in range(max(0, cy - spread), min(h, cy + spread + 1)):
            for i in maps:
                buf, stride = maps_bufs[i]
                if max(buf[row * stride + max(0, cx - spread):row * stride + min(w, cx + spread + 1)],
                       default=0) >= need:
                    return True
        return False

    maps_bufs = ((vbuf, vstride), (hbuf, hstride))
    sx, sy = _pick(xs, ys, tolerance=max(2, step * 2), rounding=rounding, edge_at=edge_at)
    return QPointF(sx / dpr if sx is not None else pos.x(), sy / dpr if sy is not None else pos.y())


def _candidates(lines, first: int, at: int, along: range, at_along: int, radius: int, strong: int,
                min_run: int, rounding: int = 0) -> list[tuple]:
    """Edge candidates across the pointer: ``lines`` are the edge strengths
    along each candidate position (``first``, ``first`` + 1, ...), sampled
    at ``along``. A candidate needs a straight run of strong samples, at
    least ``min_run`` long, through or near the pointer, or (``loose``)
    stopping up to ``rounding`` samples further short of it: the side of a
    box with a rounded corner there, if _pick finds the corner. Returns the
    best few as (score, position, run start, run end, start is an end, end
    is an end, loose): the run in pixels along, and whether it really stops
    there rather than running out of the window looked at."""
    found = []
    at_index = min(range(len(along)), key=lambda i: abs(along[i] - at_along)) if len(along) else 0
    near = max(1, radius // max(1, along.step))  # a run may end this many samples short of the pointer
    for i, values in enumerate(lines):
        best = None
        start = end = None
        total = count = gap = 0
        for j, v in enumerate(list(values) + [0, 0]):  # (two zeros close the last run)
            if v >= strong:
                if start is None:
                    start, total, count = j, 0, 0
                end, gap = j, 0
                total += v
                count += 1
            elif start is not None:
                gap += 1
                if gap > 1:  # a one-sample gap doesn't break a run
                    length = end - start + 1
                    reaches = start - near <= at_index <= end + near
                    loose = not reaches and start - near - rounding <= at_index <= end + near + rounding
                    if length >= min_run and (reaches or loose):
                        score = total / count * (0.5 + 0.5 * min(1.0, length / len(along)))
                        if best is None or (not loose, score) > (not best[-1], best[0]):
                            best = (score, along[start], along[end], start > 0, end < len(along) - 1, loose)
                    start = None
        if best is not None:
            pos = first + i
            score = best[0] * (1 - 0.4 * abs(pos - at) / (radius + 1))
            found.append((score, pos) + best[1:])
    found.sort(reverse=True)
    return found[:3]


def _pick(xs, ys, tolerance: int, rounding: int = 0, edge_at=None) -> tuple[int | None, int | None]:
    """The best vertical and horizontal edge together: the pair with the
    highest score, boosted where the two meet, most where both end there
    (the corner of a box, sharp or rounded)."""

    def meets(edge, at) -> tuple[bool, bool]:
        """Does ``edge`` reach ``at`` along its length, and does it end there?"""
        _, _, start, end, start_ends, end_ends, _ = edge
        reaches = start - tolerance <= at <= end + tolerance
        ends = (start_ends and abs(start - at) <= tolerance) or (end_ends and abs(end - at) <= tolerance)
        return reaches, ends

    def short(edge, at) -> tuple[int, int]:
        """How far ``edge`` ends short of ``at`` (0 if it doesn't), and which
        way it goes on from there (+1: on to higher x or y)."""
        _, _, start, end, start_ends, end_ends, _ = edge
        if start_ends and at < start:
            return start - at, 1
        if end_ends and at > end:
            return at - end, -1
        return 0, 0

    def rounded(vx, hy) -> bool:
        """Do they make a box's rounded corner: both ending short of where
        they'd meet by about the same, with a curve between them and
        nothing carrying on past the corner (that's a line through it)?"""
        x, y = vx[1], hy[1]
        down, dy = short(vx, y)
        across, dx = short(hy, x)
        if min(down, across) <= tolerance or max(down, across) > rounding:
            return False
        if abs(down - across) > max(tolerance, (down + across) / 6):
            return False
        if edge_at is None:
            return True
        r = (down + across) / 2
        past = range(tolerance + 1, tolerance + 1 + max(2, round(r / 2)))
        hits = sum(edge_at(x - dx * k, y, maps=(1,), spread=0) for k in past) + \
            sum(edge_at(x, y - dy * k, maps=(0,), spread=0) for k in past)
        if hits * 3 > 2 * len(past):
            return False
        inset = r * (1 - 0.5 ** 0.5)  # the middle of the curve, in from the corner
        return edge_at(x + dx * inset, y + dy * inset, faint=True)

    best, best_score = (None, None), 0.0
    for vx in xs + [None]:
        for hy in ys + [None]:
            round_corner = bool(vx and hy and rounding and rounded(vx, hy))
            if not round_corner and ((vx and vx[-1]) or (hy and hy[-1])):
                continue  # one that stops short counts only as part of a rounded corner
            score = (vx[0] if vx else 0.0) + (hy[0] if hy else 0.0)
            if round_corner:
                score *= CORNER_BONUS
            elif vx and hy:
                reach_v, end_v = meets(vx, hy[1])
                reach_h, end_h = meets(hy, vx[1])
                if reach_v and reach_h:
                    score *= CORNER_BONUS if end_v and end_h else TEE_BONUS if end_v or end_h else CROSS_BONUS
            if score > best_score:
                best, best_score = (vx[1] if vx else None, hy[1] if hy else None), score
    return best


def _by_touch(event) -> bool:
    """Did a finger (a touchscreen) make this mouse event, not a mouse?"""
    device = event.pointingDevice() if hasattr(event, "pointingDevice") else None
    if device is not None:
        return device.type() == QInputDevice.DeviceType.TouchScreen
    return event.source() != Qt.MouseEventSource.MouseEventNotSynthesized


def _with_keys(text: str, keymap) -> str:
    """A hint with its {action} keys filled in; a part whose key is unset goes."""
    parts = []
    for part in text.split("  ·  "):
        if "{" in part:
            action = part[part.index("{") + 1:part.index("}")]
            key = keymap.label(action)
            if not key:
                continue
            part = part.replace("{" + action + "}", key)
        parts.append(part)
    return "  ·  ".join(parts)


def _code_box(poly: QPolygonF) -> QRectF:
    """The green box round a code: padded in proportion to its size, so a
    small code isn't swamped and a big one isn't cramped."""
    r = poly.boundingRect()
    pad = min(max(min(r.width(), r.height()) * 0.08, 3.0), 16.0)
    return r.adjusted(-pad, -pad, pad, pad)


def _corner_inset(at: int, size: int, radius: float, border: int) -> int:
    """Where a line across the magnifier, ``at`` pixels in from one side,
    starts and ends (pixels in from each end): inside the border and its
    rounded corners."""
    d = max(0, min(at, size - 1 - at)) + 0.5  # the middle of the pixel, from the nearer side
    if d >= radius:
        return border
    return math.ceil(radius - math.sqrt(radius * radius - (radius - d) ** 2)) + border


def _cut(r: tuple, hole: tuple) -> list[tuple]:
    """The parts of rectangle ``r`` outside ``hole``, as (x, y, w, h)."""
    x, y, w, h = r
    hx, hy, hw, hh = hole
    if x >= hx + hw or hx >= x + w or y >= hy + hh or hy >= y + h:
        return [r]
    out = []
    if y < hy:
        out.append((x, y, w, hy - y))
    if y + h > hy + hh:
        out.append((x, hy + hh, w, y + h - hy - hh))
    top, bottom = max(y, hy), min(y + h, hy + hh)
    if x < hx:
        out.append((x, top, hx - x, bottom - top))
    if x + w > hx + hw:
        out.append((hx + hw, top, x + w - hx - hw, bottom - top))
    return out
