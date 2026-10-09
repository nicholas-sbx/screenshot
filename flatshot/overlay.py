"""One fullscreen overlay per monitor showing the frozen capture."""

import os
import sys
import threading
import time

from flatshot.qt import (
    QColor, QEvent, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap,
    QPoint, QPointF, QPolygonF, QRect, QRectF, Qt, QTimer, QWidget,
)

from flatshot import layershell, shapes
from flatshot.theme import C, font, is_light
from flatshot.widgets import CodeChip, Toolbar

HINTS = {
    "region": "Drag to capture  ·  Click for whole screen  ·  Esc to cancel",
    "codes": "Drag to capture  ·  Q hides detected codes  ·  Esc to cancel",
    "windows": "Drag to capture  ·  Click a window to capture it  ·  Esc to cancel",
    "text": "Click to place text  ·  Enter to finish  ·  R to capture",
}
PIN_HINT = "Drag to pin a region  ·  Click a window to pin it  ·  K to save instead"
RECORD_HINTS = {
    "pick": "Drag the area to record  ·  Click for whole screen  ·  Esc to cancel",
    "windows": "Drag the area to record  ·  Click a window  ·  Enter for whole screen  ·  Esc to cancel",
    "armed": "Drag the edges to adjust  ·  Enter to start  ·  Esc to go back",
    "countdown": "Recording starts in {n}  ·  Esc to cancel",
}
HANDLE = 10  # size of the resize handles on an area chosen for recording
# Snapping a selection to edges in the picture. How far an edge pulls is a
# setting (snap_distance); so is the sensitivity, which sets how strong an
# edge must be and how long a straight run of it. An edge is looked at this
# far (logical px) either side of the pointer along its length.
SNAP_REACH = 40
# A vertical and a horizontal edge meeting: both ending there (an L, a box's
# corner), one ending on the other (a T), or crossing.
CORNER_BONUS, TEE_BONUS, CROSS_BONUS = 1.6, 1.25, 1.1
RAINBOW_SECONDS = 4  # one trip round the colours


def _buffer(image: QImage) -> memoryview:
    """An image's bytes, without copying them (PySide6 and PyQt6 differ)."""
    bits = image.constBits()
    if hasattr(bits, "setsize"):
        bits.setsize(image.sizeInBytes())
    return memoryview(bits).cast("B")
DRAW_HINT = "Draw on the screen  ·  R then drag to capture  ·  Enter for whole screen"
LOUPE_ZOOM = (3.0, 40.0)  # magnifier zoom range, screen px per captured pixel


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
        self._pixels: QImage | None = None
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
        self.toolbar: Toolbar | None = None
        # Recording: the chosen area (logical), how it was chosen, and a drag
        # that moves or resizes it: (handle, press position, rect at press).
        self.rec_rect: QRectF | None = None
        self.rec_window = None  # the window clicked, when the area is one
        self._rec_drag: tuple[str, QPointF, QRectF] | None = None
        self.panel = None  # recordpanel.RecordPanel, made when first needed
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
        self._rainbow: QTimer | None = None
        if ctl.cfg.rainbow:
            self._rainbow = QTimer(self)
            self._rainbow.setInterval(50)
            self._rainbow.timeout.connect(self._rainbow_tick)

        self.setWindowTitle("Flatshot")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
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
        if event.type() == QEvent.Type.MouseMove and isinstance(obj, QWidget):
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
        for chip, (_, poly) in zip(self.chips, self.codes):
            c = poly.boundingRect().center()
            chip.move(round(c.x() - chip.width() / 2), round(c.y() - chip.height() / 2))
        self._place_panel()

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

    def dpr(self) -> float:
        return self.base.devicePixelRatio()

    def pixels(self) -> QImage:
        if self._pixels is None:
            self._pixels = self.base.toImage()
        return self._pixels

    # -- codes -------------------------------------------------------------

    def set_codes(self, codes):
        """Keep codes whose centre lies on this screen, in logical coords."""
        dpr = self.dpr()
        phys = QRectF(QRect(self.origin, self.base.size()))
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
                self.chips.pop(i).deleteLater()
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
        shape = Qt.CursorShape.IBeamCursor if self.ctl.tool == "text" else Qt.CursorShape.CrossCursor
        self.setCursor(shape)

    def commit(self, shape: shapes.Shape):
        self.annotations.append(shape)
        self.undone.clear()
        self.ctl.record(self)
        self.update()

    def undo(self) -> bool:
        if not self.annotations:
            return False
        self.undone.append(self.annotations.pop())
        self.update()
        return True

    def redo(self) -> bool:
        if not self.undone:
            return False
        self.annotations.append(self.undone.pop())
        self.update()
        return True

    def cancel_gesture(self) -> bool:
        if self.sel_rect is not None or self.active is not None or self._rec_drag is not None:
            spanned = self.sel_rect is not None and self.ctl.spans()
            self.sel_origin = self.sel_rect = self.active = self._rec_drag = None
            if spanned:
                self.ctl.selection_moved(self, None)
            self.ctl.refresh()
            return True
        if self.rec_rect is not None:
            self.rec_rect = self.rec_window = None
            self.ctl.refresh()
            return True
        return False

    def arm(self, rect: QRectF, window=None):
        """Choose ``rect`` (logical, this screen) to record."""
        self.rec_rect = QRectF(rect).intersected(QRectF(self.rect()))
        self.rec_window = window
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
        """Two maps of the picture, one byte per pixel: how much each pixel
        differs from the one to its left, and from the one above. Qt does
        the work (a difference blend and a greyscale conversion), about
        150 ms for a 4K screen."""
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
        self._edge_maps = maps

    def _snapped(self, pos: QPointF, modifiers) -> QPointF:
        """``pos`` moved onto nearby edges in the picture, if any (Ctrl: as
        is). Edges that look like parts of boxes win: long straight runs,
        and above all a vertical and a horizontal one that meet."""
        if not self.ctl.snap_edges or modifiers & Qt.KeyboardModifier.ControlModifier:
            return pos
        maps = self._edge_maps
        if maps is None:  # still being built: place freely until then
            self.snap_changed()
            return pos
        dpr = self.dpr()
        (vgrey, vbuf, vstride), (hgrey, hbuf, hstride) = maps
        w, h = vgrey.width(), vgrey.height()
        x, y = int(pos.x() * dpr), int(pos.y() * dpr)
        if not (0 <= x < w and 0 <= y < h):
            return pos
        cfg = self.ctl.cfg
        sens = min(max(cfg.snap_sensitivity, 1), 10)
        radius = max(2, round(cfg.snap_distance * dpr))
        reach = max(8, round(SNAP_REACH * dpr))
        step = max(1, round(dpr))  # sample about one logical pixel apart along an edge
        strong = 10 + (10 - sens) * 4  # how much a pixel must differ across the edge
        min_run = max(3, round((8 + (10 - sens) * 2.2) * dpr / step))  # in samples
        # Vertical edges near x: each candidate column, sampled down the rows around y.
        c0, c1 = max(1, x - radius), min(w, x + radius + 1)
        rows = range(max(0, y - reach), min(h, y + reach + 1), step)
        columns = zip(*(vbuf[r * vstride + c0:r * vstride + c1] for r in rows))
        xs = _candidates(columns, c0, x, rows, y, radius, strong, min_run)
        # Horizontal edges near y: each candidate row, sampled along the columns around x.
        r0, r1 = max(1, y - radius), min(h, y + radius + 1)
        cols = range(max(0, x - reach), min(w, x + reach + 1), step)
        lines = (hbuf[r * hstride + cols.start:r * hstride + cols.stop:step] for r in range(r0, r1))
        ys = _candidates(lines, r0, y, cols, x, radius, strong, min_run)
        sx, sy = _pick(xs, ys, tolerance=max(2, step * 2))
        return QPointF(sx / dpr if sx is not None else pos.x(), sy / dpr if sy is not None else pos.y())

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

    def _drag_area(self, pos: QPointF, snapped: QPointF | None = None):
        """Move or resize the recording area; a resized edge goes to
        ``snapped`` when snapping found an edge there."""
        handle, start, r0 = self._rec_drag
        d = pos - start
        r = QRectF(r0)
        bounds = QRectF(self.rect())
        if handle == "move":
            r.translate(d)
            r.moveLeft(max(bounds.left(), min(r.left(), bounds.right() - r.width())))
            r.moveTop(max(bounds.top(), min(r.top(), bounds.bottom() - r.height())))
        else:
            sx = snapped.x() if snapped is not None and snapped.x() != pos.x() else None
            sy = snapped.y() if snapped is not None and snapped.y() != pos.y() else None
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
            shape.paint(p, self.base)
        p.end()
        image.setDevicePixelRatio(1.0)
        return image.convertToFormat(QImage.Format.Format_RGB32)

    # -- input -------------------------------------------------------------

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            if not (self.ctl.cancel_countdown() or self.cancel_gesture()):
                self.ctl.cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.ctl.commit_text() and self.ctl.tool == "text":
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
            self.ctl.begin_text(self, shapes.Text(pos, self.ctl.color, self.ctl.size))
        elif tool == "counter":
            self.commit(shapes.Counter(pos, self.ctl.color, self.ctl.size, self.ctl.next_number()))
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
            self._drag_area(pos, self._snap_point)
        elif self.active is not None:
            self.active.extend(pos, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        else:
            self._update_hover()
            if self.ctl.tool == "record":
                self.setCursor(_HANDLE_CURSORS.get(self._handle_at(pos), Qt.CursorShape.CrossCursor))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
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

    def wheelEvent(self, event):
        """Scrolling zooms the magnifier in (up) and out (down)."""
        steps = event.angleDelta().y() / 120
        if not steps or not (self.ctl.tool in ("region", "record") and self.ctl.cfg.show_loupe):
            return
        self.ctl.loupe_zoom = max(LOUPE_ZOOM[0], min(self.ctl.loupe_zoom * 1.25 ** steps, LOUPE_ZOOM[1]))
        self.update()

    def enterEvent(self, event):
        self.ctl.activate(self)

    def leaveEvent(self, event):
        self.cursor_pos = None
        self.hover_window = None
        self.update()

    def keyPressEvent(self, event):
        self.ctl.key(self, event)

    def keyReleaseEvent(self, event):
        self.ctl.key_release(self, event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        if not QRectF(QPointF(0, 0), self.base.deviceIndependentSize()).contains(QRectF(self.rect())):
            p.fillRect(self.rect(), C.INK)
        p.drawPixmap(0, 0, self.base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for shape in self.annotations:
            shape.paint(p, self.base)
        if self.active is not None:
            self.active.paint(p, self.base)
        if self.ctl.text_edit and self.ctl.text_edit[0] is self:
            self.ctl.text_edit[1].paint(p, self.base)

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
        elif (picking and self.cursor_pos is not None and self.ctl.cfg.show_loupe and self._loupe_allowed()
              and self.rect().contains(self.cursor_pos.toPoint())):
            self._paint_loupe(p, self.cursor_pos)
        if self.sel_rect is None and self is self.ctl.pointer_overlay:
            self._paint_hint(p)

    def _over_floating(self, margin: int = 0) -> bool:
        pt = self.cursor_pos.toPoint()
        floating = [w for w in (self.toolbar, self.panel) if w is not None]
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
        for _, poly in self.codes:
            p.drawRoundedRect(poly.boundingRect().adjusted(-8, -8, 8, 8), 8, 8)
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
        cells = max(3, round(size / self.ctl.loupe_zoom) | 1)  # odd so one cell is the centre
        dpr = self.dpr()
        px, py = int(pos.x() * dpr), int(pos.y() * dpr)
        x = pos.x() + 24 if pos.x() + 24 + size < self.width() else pos.x() - 24 - size
        y = pos.y() + 24 if pos.y() + 24 + size + 34 < self.height() else pos.y() - 24 - size - 34
        box = QRectF(x, y, size, size)
        clip = QPainterPath()
        clip.addRoundedRect(box, 12, 12)
        p.save()
        p.setClipPath(clip)
        p.fillRect(box, C.INK)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(box, self.base, QRectF(px - cells // 2, py - cells // 2, cells, cells))
        p.restore()
        cell = size / cells
        # Lines go on after the rounded clip is gone (lines through a clip path
        # are slow), on whole pixels, without anti-aliasing.
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if cell >= 5:
            p.drawPixmap(round(x), round(y), self._loupe_grid(round(size), cells))
        # The square around the pixel: a coloured ring inside a black or white
        # one (whichever stands out from that pixel), so it shows on any colour.
        img = self.pixels()
        under = img.pixelColor(min(max(px, 0), img.width() - 1), min(max(py, 0), img.height() - 1))
        contrast = QColor("#000000") if is_light(under) else QColor("#FFFFFF")
        mark = self._mark_color()
        square = QRectF(x + (cells // 2) * cell, y + (cells // 2) * cell, cell, cell)
        if self.ctl.tool in ("region", "record"):
            # The crosshair in the magnifier: through the middle of the
            # pointer's pixel (the square), as on screen, but not inside the
            # square. Where it snaps, a line moves to the boundary between
            # pixels: exactly where the edge is.
            snap = self._snap_point
            first = cells // 2  # the pointer's cell
            if snap is not None and snap.x() != pos.x():
                lx = x + (round(snap.x() * dpr) - (px - first)) * cell
            else:
                lx = square.center().x()
            if snap is not None and snap.y() != pos.y():
                ly = y + (round(snap.y() * dpr) - (py - first)) * cell
            else:
                ly = square.center().y()
            lx, ly = round(lx), round(ly)
            ix, iy, isize = round(x), round(y), round(size)
            gap = square.adjusted(-3, -3, 3, 3)  # the square and its ring stay clear
            vertical = _gapped(iy + _corner_inset(lx - ix, isize), iy + isize - _corner_inset(lx - ix, isize),
                               gap.top(), gap.bottom(), gap.left() <= lx <= gap.right())
            horizontal = _gapped(ix + _corner_inset(ly - iy, isize), ix + isize - _corner_inset(ly - iy, isize),
                                 gap.left(), gap.right(), gap.top() <= ly <= gap.bottom())
            for pen in (QPen(contrast, 3), QPen(mark, 1)):
                p.setPen(pen)
                if ix < lx < ix + isize:
                    for a, b in vertical:
                        p.drawLine(lx, a, lx, b)
                if iy < ly < iy + isize:
                    for a, b in horizontal:
                        p.drawLine(a, ly, b, ly)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(contrast, 3.5))
        p.drawRect(square)
        p.setPen(QPen(mark, 1.5))
        p.drawRect(square)
        p.restore()
        self._loupe_box = box.toAlignedRect()
        p.setPen(QPen(C.LINE, 1))
        p.drawRoundedRect(box, 12, 12)
        img = self.pixels()
        color = img.pixelColor(min(px, img.width() - 1), min(py, img.height() - 1)).name().upper()
        self._pill(p, f"{px}, {py}   {color}", QPointF(x, y + size + 6), mono=True)

    _grids: dict = {}

    def _loupe_grid(self, size: int, cells: int) -> QPixmap:
        """The magnifier's pixel grid, thin lines with its rounded corners cut
        off, made once per size and zoom."""
        key = (size, cells)
        grid = Overlay._grids.get(key)
        if grid is None:
            grid = QPixmap(size, size)
            grid.fill(Qt.GlobalColor.transparent)
            g = QPainter(grid)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, size, size), 12, 12)
            g.setClipPath(clip)
            g.setPen(QPen(QColor(128, 128, 128, 70), 1))
            cell = size / cells
            for i in range(1, cells):
                g.drawLine(round(i * cell), 0, round(i * cell), size)
                g.drawLine(0, round(i * cell), size, round(i * cell))
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
        if self.ctl.tool == "record":
            if self.ctl.countdown is not None:
                text = RECORD_HINTS["countdown"].format(n=self.ctl.countdown)
            else:
                armed = any(o.rec_rect is not None for o in self.ctl.overlays)
                text = self.ctl.hint or self.ctl.record_problem() or RECORD_HINTS[
                    "armed" if armed else "windows" if self.windows else "pick"]
        else:
            text = self.ctl.hint or (PIN_HINT if tool == "pin" else HINTS.get(tool, DRAW_HINT))
        fm = QFontMetricsF(font(12, QFont.Weight.Medium))
        x = (self.width() - fm.horizontalAdvance(text)) / 2 - 14
        self._pill(p, text, QPointF(x, self.height() - 52))

    def _pill(self, p, text, at: QPointF, anchor_bottom=False, bg=None, fg=None, mono=False, keep_inside=False,
              small=False):
        f = font(11 if small else 12, QFont.Weight.DemiBold if bg is not None else QFont.Weight.Medium, mono=mono)
        fm = QFontMetricsF(f)
        box = QRectF(0, 0, fm.horizontalAdvance(text) + (18 if small else 28), 22 if small else 28)
        box.moveTopLeft(at - QPointF(0, box.height()) if anchor_bottom else at)
        if keep_inside and box.top() < 4:
            box.moveTop(at.y() + 20)  # no room above the selection: tuck inside
        p.save()
        p.setPen(QPen(C.LINE, 1) if bg is None else Qt.PenStyle.NoPen)
        p.setBrush(bg or C.BASE)
        p.drawRoundedRect(box, 6 if small else 8, 6 if small else 8)
        p.setPen(fg or C.SOFT)
        p.setFont(f)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()


def _candidates(lines, first: int, at: int, along: range, at_along: int, radius: int, strong: int,
                min_run: int) -> list[tuple]:
    """Edge candidates across the pointer: ``lines`` are the edge strengths
    along each candidate position (``first``, ``first`` + 1, ...), sampled
    at ``along``. A candidate needs a straight run of strong samples, at
    least ``min_run`` long, through or near the pointer. Returns the best
    few as (score, position, run start, run end, start is an end, end is an
    end): the run in pixels along, and whether it really stops there rather
    than running out of the window looked at."""
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
                    if length >= min_run and reaches:
                        score = total / count * (0.5 + 0.5 * min(1.0, length / len(along)))
                        if best is None or score > best[0]:
                            best = (score, along[start], along[end], start > 0, end < len(along) - 1)
                    start = None
        if best is not None:
            pos = first + i
            score = best[0] * (1 - 0.4 * abs(pos - at) / (radius + 1))
            found.append((score, pos) + best[1:])
    found.sort(reverse=True)
    return found[:3]


def _pick(xs, ys, tolerance: int) -> tuple[int | None, int | None]:
    """The best vertical and horizontal edge together: the pair with the
    highest score, boosted where the two meet, most where both end there
    (the corner of a box)."""

    def meets(edge, at) -> tuple[bool, bool]:
        """Does ``edge`` reach ``at`` along its length, and does it end there?"""
        _, _, start, end, start_ends, end_ends = edge
        reaches = start - tolerance <= at <= end + tolerance
        ends = (start_ends and abs(start - at) <= tolerance) or (end_ends and abs(end - at) <= tolerance)
        return reaches, ends

    best, best_score = (None, None), 0.0
    for vx in xs + [None]:
        for hy in ys + [None]:
            score = (vx[0] if vx else 0.0) + (hy[0] if hy else 0.0)
            if vx and hy:
                reach_v, end_v = meets(vx, hy[1])
                reach_h, end_h = meets(hy, vx[1])
                if reach_v and reach_h:
                    score *= CORNER_BONUS if end_v and end_h else TEE_BONUS if end_v or end_h else CROSS_BONUS
            if score > best_score:
                best, best_score = (vx[1] if vx else None, hy[1] if hy else None), score
    return best


def _corner_inset(at: int, size: int, radius: int = 12) -> int:
    """How far a line across the magnifier at ``at`` (from one side) must stop
    short of the ends to stay inside its rounded corners. (A line on or past
    the edge isn't drawn; it counts as on the edge.)"""
    d = max(0, min(at, size - at))
    if d >= radius:
        return 0
    return round(radius - (radius * radius - (radius - d) ** 2) ** 0.5) + 1


def _gapped(start: int, end: int, gap_start: float, gap_end: float, crosses: bool) -> list[tuple[int, int]]:
    """A line from ``start`` to ``end``, broken where it would cross the gap."""
    if not crosses:
        return [(start, end)]
    parts = [(start, min(end, int(gap_start))), (max(start, int(gap_end) + 1), end)]
    return [(a, b) for a, b in parts if b > a]
