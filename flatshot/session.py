"""One capture: freeze the screens, let the user draw/select, deliver.

Used both for one-shot runs (`flatshot`) and by the tray app, which runs a
new Session per shortcut press.
"""

import subprocess
import sys
from dataclasses import dataclass

from flatshot import capture, config, keys, layershell, output, pin, scanner, shapes, theme, timing, windows
from flatshot.notify import Notifier
from flatshot.overlay import Overlay
from flatshot.qt import (
    QColor, QCursor, QDesktopServices, QGuiApplication, QImage, QPainter, QPixmap, QPoint, QPointF, QRect, QRectF, Qt,
    QTimer, QUrl, keyval,
)
from flatshot.widgets import TOOLS

K = {name: keyval(getattr(Qt.Key, f"Key_{name}")) for name in ["Escape", "Return", "Enter", "Backspace", "Y", "S", "C"]}
# The tools that draw in the chosen colour: picking a colour switches to one.
COLOUR_TOOLS = ("pen", "line", "arrow", "rect", "solid", "ellipse", "marker", "text", "counter")
CUSTOM = len(theme.SWATCHES)  # the colour index of your own colour, after the toolbar's others

# region: the overlay. The rest deliver straight away, without any UI:
# screens (every monitor), monitor (the one under the pointer), window (the
# active one), last (the last region captured), rect (Request.rect).
MODES = ("region", "screens", "monitor", "window", "last", "rect")
# How long an instant capture waits for the compositor to describe the desktop.
DESKTOP_TIMEOUT_MS = 1500


def _screens() -> list:
    """The monitors as they are now: overlays built for others won't do."""
    return [(s, s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]


def last_region() -> QRect | None:
    try:
        x, y, w, h = (int(v) for v in config.load_state()["last_region"])
    except (KeyError, TypeError, ValueError):
        return None
    return QRect(x, y, w, h) if w > 0 and h > 0 else None


@dataclass
class Request:
    mode: str = "region"  # see MODES
    rect: QRect | None = None  # for mode "rect"
    pin: bool = False  # pin the result to the screen instead of saving / copying it
    image: str | None = None  # annotate this file instead of capturing
    output: str | None = None  # explicit output file
    backend: str | None = None
    scan: bool = True
    record: bool = False  # open with the record tool


def give_back_memory():
    """Return freed memory to the system. glibc keeps big freed blocks (a
    screenshot's pictures) for reuse, so without this the tray app stays as
    big as its largest capture made it."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc


class Session:
    def __init__(self, cfg: config.Config, request: Request, notifier: Notifier, on_finished, on_action=None,
                 on_recording=None):
        """``on_finished(exit_code, holds_clipboard)`` is called exactly once.
        ``on_action(key, path)`` handles notification buttons (tray only).
        ``on_recording(recording)`` takes over a screen recording once it
        starts (tray only); without it the session lasts until the
        recording ends."""
        self.cfg = cfg
        self.request = request
        self.notifier = notifier
        self.on_finished = on_finished
        self.on_action = on_action
        self.on_recording = on_recording
        # One recording at a time. (Recording code is only imported once used,
        # so screenshots never pay for it.)
        recording = sys.modules.get("flatshot.recording")
        self.can_record = not request.image and (recording is None or recording.current() is None)
        tools = {name for name, _, _ in TOOLS} - (set() if self.can_record else {"record"})
        self.tool = cfg.default_tool if cfg.default_tool in tools else "region"
        if request.record and self.can_record:
            self.tool = "record"
        self._rec_opts = None
        self._escape_down = False  # Esc pressed here; it acts when released
        self.countdown: int | None = None  # seconds left before recording starts
        self._countdown_overlay: Overlay | None = None
        self._countdown_timer = QTimer()
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick)
        self._problems: dict[str, str | None] = {}
        self.recording = None  # the recording this session started
        self.color_index = min(max(cfg.default_color, 0), CUSTOM)
        self.custom_color = QColor(cfg.custom_color)
        self.keymap = keys.Keymap(cfg.keys)
        self.eyedropper = False  # the next click takes your own colour from the screen
        self.size = min(max(cfg.default_size, 0), len(theme.SIZES) - 1)
        self.dim = QColor(theme.C.DIM)
        self.dim.setAlpha(round(cfg.dim_opacity * 2.55))
        self.codes_visible = cfg.show_codes
        self.snap_edges = cfg.snap_edges
        self.colour_tool = self.tool if self.tool in COLOUR_TOOLS else "pen"  # the last one used
        self.loupe_zoom = 8.0  # magnifier screen px per captured pixel; the wheel changes it
        self.pin_mode = request.pin  # the capture is pinned to the screen instead of saved
        self.overlays: list[Overlay] = []
        self.pointer_overlay: Overlay | None = None  # where the mouse is
        self.toolbar_overlay: Overlay | None = None  # where the toolbar is
        self.history: list[Overlay] = []
        self.redo_stack: list[Overlay] = []
        self.text_edit: tuple[Overlay, shapes.Text] | None = None
        self.hint: str | None = None
        self.scanner = scanner.Scanner()
        self.scanner.finished.connect(self._codes_found)
        self.window_finder: windows.WindowFinder | None = None
        self._desktop: windows.Desktop | None = None
        self.mode = request.mode if request.mode in MODES else "region"
        self._grabbed: QImage | None = None  # an instant capture waiting for the desktop state
        # Selections across monitors: the whole desktop grab, kept while the
        # overlays are up (only with more than one monitor and the setting on).
        self._desktop_image: QImage | None = None
        self.hovered = None  # the window under the pointer, on whichever monitor
        # The same picture with the mouse pointer in it (where the helper
        # could take it), shown while show_pointer is on.
        self.pointer_image: QImage | None = None
        self._plain_image: QImage | None = None
        self._prepared_for = None  # the screens the overlays were built ahead for (prepare())
        self.show_pointer = False
        self.done = False
        self.clock = timing.Clock("capture")  # (started again by start())

    @property
    def color(self):
        return self.custom_color if self.color_index == CUSTOM else theme.SWATCHES[self.color_index]

    # -- startup -----------------------------------------------------------

    def _names_window(self) -> bool:
        return any(t in self.cfg.save_dir + self.cfg.filename for t in ("{app", "{title"))

    def start(self):
        if self.request.image:
            self.mode = "region"
        elif self.mode == "last":
            self.request.rect = last_region()
            # Nothing captured yet: let the user pick the region this time.
            self.mode = "rect" if self.request.rect is not None else "region"
        elif self.mode == "rect" and self.request.rect is None:
            self.mode = "region"
        instant = self.mode != "region"
        self.clock = timing.Clock("image" if self.request.image else "recording" if self.tool == "record"
                                  else f"{self.mode} capture")
        if not self.request.image and (self.mode in ("monitor", "window") or self._names_window()
                                       or (self.mode == "region" and self.cfg.detect_windows)):
            # Ask the compositor about windows now, while they are as captured.
            self.window_finder = windows.WindowFinder()
            self.window_finder.found.connect(self._desktop_found)
            if not self.window_finder.start(background=not instant):
                self.window_finder = None
        try:
            if self.request.image:
                image = QImage(self.request.image)
                if image.isNull():
                    raise capture.CaptureError(f"cannot read {self.request.image}")
            else:
                # The overlay's picture can show the pointer or not (the
                # toolbar's pointer button): both are taken at once.
                # (Unless the settings say always or never: then one, as it's to be.)
                extra = [] if not instant and self.cfg.region_pointer == "toggle" else None
                pointer = self.cfg.include_pointer if instant else self.cfg.region_pointer == "shown"
                image = capture.grab_desktop(self.request.backend or self.cfg.backend, pointer=pointer,
                                             also_pointer=extra)
                if extra and extra[0].size() == image.size():
                    self.pointer_image = extra[0]
        except capture.CaptureError as e:
            self.fail(str(e))
            return
        self.clock.step("screenshot", f"{image.width()} × {image.height()}"
                        + (f" via {capture.last_grab}" if capture.last_grab and not self.request.image else ""))
        if instant:
            self._grabbed = image
            if self._desktop is not None or self.window_finder is None:
                self._finish_instant()
            else:
                QTimer.singleShot(DESKTOP_TIMEOUT_MS, self._finish_instant)
            return
        self._build_overlays(image)
        self.clock.step("overlay shown", f"{len(self.overlays)} monitor{'s' if len(self.overlays) != 1 else ''}; "
                        f"{layershell.last_overlay}")
        if self._may_span():
            self._desktop_image = image
        if self.cfg.scan_codes and self.request.scan:
            # Let the overlay reach the screen before scanning competes for CPU.
            QTimer.singleShot(60, lambda: None if self.done else self._scan(image))

    def _scan(self, image: QImage):
        self._scan_clock = timing.Clock("code scan")
        self.scanner.start(image)

    def _finish_instant(self):
        """Crop an instant capture to its mode's area and deliver it."""
        if self.done or self._grabbed is None:
            return
        image, self._grabbed = self._grabbed, None
        self._close_overlays()  # (there are none; this stops a window query still running)
        desktop = self._desktop or windows.Desktop()
        shot = output.Shot(mode="desktop")
        if desktop.active:
            shot.app, shot.title = desktop.active.app, desktop.active.title
        rect = None
        if self.mode == "rect":
            rect, shot.mode = self.request.rect, "region"
        elif self.mode == "monitor":
            point = desktop.cursor if desktop.cursor is not None else QCursor.pos()
            screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
            rect, shot.mode, shot.monitor = screen.geometry(), "monitor", screen.name()
        elif self.mode == "window":
            if desktop.active is None:
                self.fail("No active window to capture. Active-window capture needs KDE Plasma, Sway or Hyprland.")
                return
            rect, shot.mode = desktop.active.rect, "window"
        if rect is not None:
            pixels = capture.to_pixels(image, rect)
            if pixels.isEmpty():
                self.fail(f"{rect.width()}x{rect.height()}+{rect.x()}+{rect.y()} is outside the screens")
                return
            image = image.copy(pixels)
            self.clock.step("cropped", f"{image.width()} × {image.height()}")
            if self.mode == "rect":
                config.update_state(last_region=[rect.x(), rect.y(), rect.width(), rect.height()])
        self._deliver(image, shot, rect)

    # -- built ahead -----------------------------------------------------

    def prepare(self) -> bool:
        """Build the overlay windows and their toolbars now, before there's
        a picture, so that start() only has to put it in and show them.
        The tray app does this while idle, for its next capture."""
        if self.request.image or self.mode != "region" or self.overlays or self.done:
            return False
        self._prepared_for = _screens()
        for screen in QGuiApplication.screens():
            o = Overlay(self, screen, QPixmap(), QPoint(0, 0))
            o.add_toolbar()
            o.prepare_window()
            self.overlays.append(o)
        return True

    def ready_for(self, cfg: config.Config, request: Request, on_recording) -> bool:
        """Can this prepared session take this capture? Only if nothing it
        was built from has changed since."""
        recording = sys.modules.get("flatshot.recording")
        busy = recording is not None and recording.current() is not None
        return (bool(self.overlays) and not self.done and cfg == self.cfg and request == self.request
                and (on_recording is None) == (self.on_recording is None) and self.can_record == (not busy)
                and self._prepared_for == _screens())

    def discard(self):
        """Let go of a prepared session that won't be used."""
        self.done = True
        for o in self.overlays:
            o.release()
            o.deleteLater()
        self.overlays = []

    def _build_overlays(self, image: QImage):
        if self.overlays and not self.request.image and self._prepared_for == _screens():
            # Built ahead: just the pictures.
            for o in self.overlays:
                phys = capture.to_pixels(image, o.target_screen.geometry())
                pm = QPixmap.fromImage(image.copy(phys))
                pm.setDevicePixelRatio(phys.width() / max(1, o.target_screen.geometry().width()))
                o.set_picture(pm, phys.topLeft())
                if self.pointer_image is not None:
                    o.toolbar.pointer_button.show()
                    o.toolbar.adjustSize()
            self.clock.step("pictures ready", "windows built ahead")
        else:
            for o in self.overlays:  # (built ahead for other monitors)
                o.release()
                o.deleteLater()
            self.overlays = []
            self._build_new_overlays(image)
        self._show_overlays()

    def _build_new_overlays(self, image: QImage):
        if self.request.image:
            # Show the image on the screen under the mouse, scaled to fit.
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            pm = QPixmap.fromImage(image)
            g = screen.geometry()
            pm.setDevicePixelRatio(max(image.width() / g.width(), image.height() / g.height(), 1.0))
            self.overlays.append(Overlay(self, screen, pm, QPoint(0, 0)))
        else:
            pictures = []
            for screen in QGuiApplication.screens():
                g = screen.geometry()
                phys = capture.to_pixels(image, g)
                pm = QPixmap.fromImage(image.copy(phys))
                pm.setDevicePixelRatio(phys.width() / max(1, g.width()))
                pictures.append((screen, pm, phys.topLeft()))
            self.clock.step("pictures ready")
            for screen, pm, origin in pictures:
                self.overlays.append(Overlay(self, screen, pm, origin))
        for o in self.overlays:
            o.add_toolbar()
        self.clock.step("windows built")

    def _show_overlays(self):
        # Best guess until the pointer enters an overlay (on Wayland the
        # cursor position is only known once it does).
        start_screen = QGuiApplication.screenAt(QCursor.pos())
        if self.cfg.toolbar_follows_mouse and start_screen is not None:
            first = next((o for o in self.overlays if o.target_screen is start_screen), None)
        else:
            first = None
        primary = QGuiApplication.primaryScreen()
        first = first or next((o for o in self.overlays if o.target_screen is primary), self.overlays[0])
        self.pointer_overlay = self.toolbar_overlay = first
        self._sync_toolbars()
        for o in self.overlays:
            o.show_on_screen()
        if QGuiApplication.platformName() == "xcb":
            # X11 knows where the pointer is now; Wayland says once it enters
            # an overlay (or KWin / Hyprland do, with the windows).
            self._point_at(QPointF(QCursor.pos()))
        if self._desktop is not None:  # KWin answered before the overlays existed
            self._desktop_found(self._desktop)

    def _desktop_found(self, desktop: windows.Desktop):
        if self._desktop is None:
            timing.log(f"{self.clock.what}: windows known at {self.clock.since_start():.0f} ms, "
                       f"{len(desktop.windows)} window{'s' if len(desktop.windows) != 1 else ''}")
        self._desktop = desktop
        if self.done:
            return
        if self._grabbed is not None:
            self._finish_instant()
            return
        if desktop.cursor is not None and all(o.cursor_pos is None for o in self.overlays):
            self._point_at(QPointF(desktop.cursor))
        if not self.cfg.detect_windows:
            return
        for o in self.overlays:
            o.set_windows(desktop.windows)
        self.refresh()

    def _codes_found(self, codes):
        if hasattr(self, "_scan_clock"):
            self._scan_clock.step("done", f"{len(codes)} code{'s' if len(codes) != 1 else ''} found, "
                                          f"at {self.clock.since_start():.0f} ms into the capture")
        if self.done:
            return
        for o in self.overlays:
            o.set_codes(codes)
        self.refresh()

    # -- which monitor -----------------------------------------------------

    def _point_at(self, point: QPointF):
        """Show the crosshair and magnifier at ``point`` (global, logical)
        before the pointer has moved."""
        for o in self.overlays:
            g = o.target_screen.geometry()
            if QRectF(g).contains(point):
                o.point_at(point - QPointF(g.topLeft()))

    def activate(self, overlay: Overlay):
        """The pointer is now over ``overlay``."""
        if overlay is self.pointer_overlay or self.done:
            return
        previous = self.pointer_overlay
        self.pointer_overlay = overlay
        if self.cfg.toolbar_follows_mouse and not any(o.sel_rect is not None or o.rec_rect is not None
                                                      for o in self.overlays):
            moving = self.toolbar_overlay is not overlay
            picker_open = any(o.picker_open() for o in self.overlays)
            self.toolbar_overlay = overlay
            self._sync_toolbars()
            if moving and picker_open:  # your colour's picker goes along with the toolbar
                for o in self.overlays:
                    if o is not overlay and o.picker is not None:
                        o.picker.hide()
                overlay.show_picker()
        for o in (previous, overlay):
            if o is not None:
                o.update()

    def _sync_toolbars(self):
        dragging = any(o.sel_rect is not None for o in self.overlays)
        for o in self.overlays:
            if o.toolbar:
                o.toolbar.setVisible(o is self.toolbar_overlay and not dragging and self.countdown is None)

    # -- selections across monitors ------------------------------------------

    def _may_span(self) -> bool:
        return self.cfg.span_monitors and not self.request.image and len(self.overlays) > 1

    def spans(self) -> bool:
        """Can a selection (or a clicked window) cross monitors now? For a
        recording, only where the recorder can take that."""
        if self._desktop_image is None or self.tool not in ("region", "record"):
            return False
        if self.tool == "record":
            from flatshot import screencast

            return screencast.can_span()
        return True

    def area_moved(self, origin: Overlay):
        """The area to record, chosen on ``origin``, changed: show its part
        on the other monitors."""
        g = origin.target_screen.geometry()
        area = origin.rec_rect.translated(QPointF(g.topLeft())) if origin.rec_rect is not None else None
        for o in self.overlays:
            if o is not origin:
                og = QRectF(o.target_screen.geometry())
                part = area.translated(-og.topLeft()) if area is not None and area.intersects(og) else None
                if part != o.span_rect:
                    o.span_rect = part
                    o.update()

    def selection_moved(self, origin: Overlay, pointer: QPointF | None):
        """``origin``'s selection changed: show its part on the other
        monitors, and the crosshair and magnifier where the pointer is."""
        g = origin.target_screen.geometry()
        sel = origin.sel_rect.translated(QPointF(g.topLeft())) if origin.sel_rect is not None else None
        at = pointer + QPointF(g.topLeft()) if pointer is not None else None
        for o in self.overlays:
            if o is origin:
                continue
            og = QPointF(o.target_screen.geometry().topLeft())
            part = sel.translated(-og) if sel is not None and sel.intersects(QRectF(o.target_screen.geometry())) \
                else None
            local = at - og if at is not None and o.target_screen.geometry().contains(at.toPoint()) else None
            if part != o.span_rect or local != o.span_pointer:
                o.span_rect, o.span_pointer = part, local
                o.update()

    def hover_changed(self, origin: Overlay, window):
        """The pointer is over ``window`` on ``origin``: highlight its part on
        the other monitors too."""
        self.hovered = window
        if not self.spans():
            return
        for o in self.overlays:
            if o is not origin:
                o.set_span_hover(window)

    def capture_span(self, area: QRect, window: windows.Window | None = None):
        """Capture ``area`` (global logical coordinates), which crosses
        monitors: cut from the whole desktop, with every monitor's drawings."""
        if self.done or self._desktop_image is None:
            return
        self.commit_text()
        self.clock.step("chosen")
        area = area.intersected(capture.virtual_geometry())
        image = self._render_desktop(area)
        self.clock.step("drawn", f"{image.width()} × {image.height()} across monitors")
        shot = output.Shot(mode="window" if window else "region")
        source = window or (self._desktop.active if self._desktop else None)
        if source:
            shot.app, shot.title = source.app, source.title
        self._close_overlays()
        QTimer.singleShot(0, lambda: self._finish_capture(image, shot, area, source))

    def _render_desktop(self, area: QRect) -> QImage:
        image = self._desktop_image
        phys = capture.to_pixels(image, area)
        out = image.copy(phys).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        sx, sy = phys.width() / max(1, area.width()), phys.height() / max(1, area.height())
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for o in self.overlays:
            if o.annotations:
                g = o.target_screen.geometry()
                p.save()
                p.scale(sx, sy)
                p.translate(g.x() - area.x(), g.y() - area.y())
                for shape in o.annotations:
                    if not shape.hidden:
                        shape.paint(p, o.base)
                p.restore()
        p.end()
        return out.convertToFormat(QImage.Format.Format_RGB32)

    # -- tool state --------------------------------------------------------

    def refresh(self):
        self._sync_toolbars()
        for o in self.overlays:
            o.refresh()

    def set_tool(self, tool: str):
        if tool == "record" and not self.can_record:
            return
        self.commit_text()
        if self.tool == "record" and tool != "record":
            self.disarm()
        self.tool = tool
        if tool in COLOUR_TOOLS:
            self.colour_tool = tool
        self.refresh()

    def set_color(self, index: int):
        """Choose a colour; from a tool that has none (capture, pixelate),
        go back to the last drawing tool, so the colour is used."""
        if self.tool == "record":
            return
        if self.tool not in COLOUR_TOOLS:
            self.tool = self.colour_tool
        self.color_index = index
        if self.text_edit:
            self.text_edit[1].color = self.color
        self.refresh()

    # Your own colour: the picker opens under its swatch on the toolbar.

    def set_custom_color(self, color: QColor):
        self.custom_color = QColor(color)
        self.set_color(CUSTOM)

    def toggle_picker(self, overlay: Overlay):
        if overlay.picker_open():
            self.close_picker()
        elif self.tool != "record":
            self.set_color(CUSTOM)
            overlay.show_picker()

    def close_picker(self) -> bool:
        """Close the picker (or stop the eyedropper); True if either was open."""
        was_open = self.eyedropper or any(o.picker_open() for o in self.overlays)
        self.eyedropper = False
        for o in self.overlays:
            o.hide_picker()
        name = self.custom_color.name().upper()
        if name != self.cfg.custom_color:
            saved = config.load()
            saved.custom_color = self.cfg.custom_color = name
            try:
                saved.save()
            except OSError as e:
                print(f"flatshot: could not save the settings: {e}", file=sys.stderr)
        if was_open:
            self.refresh()
        return was_open

    def start_eyedropper(self):
        for o in self.overlays:
            o.hide_picker()
        self.eyedropper = True
        self.refresh()

    def take_color(self, overlay: Overlay, pos: QPointF):
        """The eyedropper's click: the pixel there becomes your own colour,
        and the picker comes back to adjust it."""
        img, dpr = overlay.pixels(), overlay.dpr()
        x = min(max(int(pos.x() * dpr), 0), img.width() - 1)
        y = min(max(int(pos.y() * dpr), 0), img.height() - 1)
        self.eyedropper = False
        self.set_custom_color(img.pixelColor(x, y))
        (self.toolbar_overlay or overlay).show_picker()

    def cycle_size(self):
        self.set_size((self.size + 1) % len(theme.SIZES))

    def set_size(self, size: int):
        self.size = min(max(size, 0), len(theme.SIZES) - 1)
        if self.text_edit:
            self.text_edit[1].size = self.size
        self.refresh()

    def toggle_pin(self):
        self.pin_mode = not self.pin_mode
        self.refresh()

    def toggle_snap(self):
        """Snap selections to edges in the picture; remembered for next time."""
        self.snap_edges = not self.snap_edges
        saved = config.load()
        saved.snap_edges = self.cfg.snap_edges = self.snap_edges
        try:
            saved.save()
        except OSError as e:
            print(f"flatshot: could not save the settings: {e}", file=sys.stderr)
        for o in self.overlays:
            o.snap_changed()
        self.refresh()

    def toggle_pointer(self):
        """Show the mouse pointer in the picture, or not (as captured)."""
        if self.pointer_image is None or self.tool == "record" or self.done:
            return
        self.show_pointer = not self.show_pointer
        for o in self.overlays:
            o.show_pointer(self.pointer_image, self.show_pointer)
        if self._desktop_image is not None:
            if self._plain_image is None:
                self._plain_image = self._desktop_image
            self._desktop_image = self.pointer_image if self.show_pointer else self._plain_image
        self.refresh()

    def toggle_codes(self):
        """Show or hide the codes found; remembered for next time, like snapping."""
        self.codes_visible = not self.codes_visible
        saved = config.load()
        saved.show_codes = self.cfg.show_codes = self.codes_visible
        try:
            saved.save()
        except OSError as e:
            print(f"flatshot: could not save the settings: {e}", file=sys.stderr)
        self.refresh()

    def code_count(self) -> int:
        return sum(len(o.codes) for o in self.overlays)

    def dismiss_code(self, overlay: Overlay, code):
        overlay.dismiss_code(code)
        self.set_hint(None)
        self.refresh()

    def set_hint(self, hint: str | None):
        self.hint = hint
        for o in self.overlays:
            o.update()

    def next_number(self) -> int:
        return 1 + sum(isinstance(s, shapes.Counter) for o in self.overlays for s in o.annotations)

    # -- recording ---------------------------------------------------------

    @property
    def rec_opts(self):
        """The recording options (screencast.Options), from the settings."""
        if self._rec_opts is None:
            from flatshot import screencast

            self._rec_opts = screencast.Options.from_config(self.cfg)
        return self._rec_opts

    def record_problem(self) -> str | None:
        """Why recording in the chosen format can't work here (cached)."""
        from flatshot import screencast

        fmt = self.rec_opts.format
        if fmt not in self._problems:
            self._problems[fmt] = screencast.problem(fmt)
        return self._problems[fmt]

    def set_record_option(self, **changes):
        """Change and remember recording options (format, fps, mic, ...)."""
        keys = {"format": "record_format", "fps": "record_fps", "mic": "record_mic",
                "system_audio": "record_system_audio", "cursor": "record_cursor"}
        saved = config.load()
        for name, value in changes.items():
            setattr(self.rec_opts, name, value)
            setattr(self.cfg, keys[name], value)
            setattr(saved, keys[name], value)
        try:
            saved.save()
        except OSError as e:
            print(f"flatshot: could not save the settings: {e}", file=sys.stderr)
        self.refresh()

    def disarm(self):
        """Forget the area chosen for recording (on every monitor)."""
        for o in self.overlays:
            o.rec_rect = o.rec_window = o.span_rect = None
            o.update()

    def whole_screen(self, overlay: Overlay):
        """The toolbar's screen button: capture, or choose the screen to record."""
        if self.tool == "record":
            self.disarm()
            overlay.arm(QRectF(overlay.rect()))
        else:
            self.capture(overlay, None)

    def start_countdown(self, overlay: Overlay):
        if self.done or overlay.rec_rect is None or self.countdown is not None:
            return
        problem = self.record_problem()
        if problem:
            self.set_hint(problem)
            return
        if self.cfg.record_countdown <= 0:
            self.start_recording(overlay)
            return
        self.countdown = self.cfg.record_countdown
        self._countdown_overlay = overlay
        self._countdown_timer.start()
        self.refresh()

    def cancel_countdown(self) -> bool:
        if self.countdown is None:
            return False
        self._countdown_timer.stop()
        self.countdown = self._countdown_overlay = None
        self.refresh()
        return True

    def _tick(self):
        if self.countdown is None or self.done:
            self._countdown_timer.stop()
            return
        self.countdown -= 1
        if self.countdown > 0:
            self.refresh()
            return
        self._countdown_timer.stop()
        overlay, self._countdown_overlay = self._countdown_overlay, None
        self.countdown = None
        self.start_recording(overlay)

    def start_recording(self, overlay: Overlay):
        """Close the overlays and record the area chosen on ``overlay``."""
        from flatshot import recording, screencast

        rect = overlay.rec_rect
        if self.done or rect is None:
            return
        screen = overlay.target_screen
        g = screen.geometry()
        area = rect.toAlignedRect().translated(g.topLeft())
        window = overlay.rec_window
        full = rect.toAlignedRect() == overlay.rect()
        shot = output.Shot(mode="window" if window else "monitor" if full else "region", monitor=screen.name())
        source = window or (self._desktop.active if self._desktop else None)
        if source:
            shot.app, shot.title = source.app, source.title
        spanning = not g.contains(area)
        thumbnail = self._render_desktop(area) if spanning and self._desktop_image is not None \
            else overlay.render(rect)
        opts = screencast.Options(**vars(self.rec_opts))
        self.clock.step("recording area chosen", f"{area.width()} × {area.height()}, {opts.format}, {opts.fps} fps")
        self._close_overlays()
        # The screen's real scale is the screenshot's: Qt's can be rounded.
        target = screencast.Target(area, screen, overlay.dpr())
        rec = recording.Recording(self.cfg, target, opts, self.notifier, shot, thumbnail,
                                  self.on_action, tray=self.on_recording is not None)
        self.recording = rec
        if self.on_recording is not None:
            self.on_recording(rec)
            self._finish(0, False)
        else:
            rec.finished.connect(self._finish)
        # Let the compositor take the overlays down before the first frame.
        QTimer.singleShot(150, rec.start)

    # -- history -----------------------------------------------------------

    def record(self, overlay: Overlay):
        self.history.append(overlay)
        for o in self.redo_stack:
            o.undone.clear()
        self.redo_stack.clear()
        self._refresh_toolbars()

    def _refresh_toolbars(self):
        for o in self.overlays:
            if o.toolbar:
                o.toolbar.refresh()

    def can_undo(self) -> bool:
        return bool(self.history) or self.text_edit is not None

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self):
        self.commit_text()
        if self.history:
            o = self.history.pop()
            if o.undo():
                self.redo_stack.append(o)
        self.refresh()

    def redo(self):
        self.commit_text()
        if self.redo_stack:
            o = self.redo_stack.pop()
            if o.redo():
                self.history.append(o)
        self.refresh()

    # -- text --------------------------------------------------------------

    def begin_text(self, overlay: Overlay, shape: shapes.Text):
        self.commit_text()
        self.text_edit = (overlay, shape)
        if shape.replaces is not None:
            shape.replaces.hidden = True  # (the copy is shown while it's edited)
        overlay.update()
        self._refresh_toolbars()

    def commit_text(self) -> bool:
        """Finish the text being typed. Returns True if there was one."""
        if not self.text_edit:
            return False
        overlay, shape = self.text_edit
        self.text_edit = None
        shape.editing = False
        if shape.is_valid() and shape.changed():
            overlay.commit(shape)
        elif shape.replaces is not None:
            shape.replaces.hidden = False  # edited back to as it was
        overlay.update()
        self._refresh_toolbars()
        return True

    # -- keys --------------------------------------------------------------

    def key(self, overlay: Overlay, event):
        # With several overlays, keyboard focus can sit on any of them; act
        # on the monitor the pointer is on.
        target = self.pointer_overlay or overlay
        k = keyval(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        enter = k in (K["Return"], K["Enter"])

        if self.text_edit:
            editing, shape = self.text_edit
            action = self.keymap.action(event, keys.CAPTURE) if ctrl else None
            used = action in ("undo", "redo") and shape.undo_edit(redo=action == "redo")
            if not used:
                used = shape.key(event)
                if used == "commit":
                    self.commit_text()
            if used:
                editing.update()
                return

        if k == K["Escape"]:
            # Acted on when the key comes up (key_release): closing on the way
            # down hands the release to the window underneath.
            self._escape_down = not event.isAutoRepeat() or self._escape_down
            return
        if self.countdown is not None:
            return
        if self.tool == "record" and enter:
            armed = next((o for o in self.overlays if o.rec_rect is not None), None)
            if armed is not None:
                self.start_countdown(armed)
            else:
                self.whole_screen(target)
        elif self.tool == "record" and ctrl and k in (K["S"], K["C"]):
            pass
        elif (action := self.keymap.action(event, keys.CAPTURE)) is not None:
            self._do(action, target)
        elif ctrl and k == K["Y"]:
            self.redo()  # (besides the Redo key)
        elif enter or (ctrl and k in (K["S"], K["C"])):
            self.capture(target, None)

    def _do(self, action: str, target: Overlay):
        """A key's action (keys.BINDINGS) while capturing."""
        if action == "undo":
            self.undo()
        elif action == "redo":
            self.redo()
        elif action == "codes":
            self.toggle_codes()
        elif action == "pin":
            self.toggle_pin()
        elif action == "snap":
            self.toggle_snap()
        elif action == "pointer":
            self.toggle_pointer()
        elif action == "copy_color":
            self.copy_color(target)
        elif action.startswith("tool."):
            self.set_tool(action[5:])
        elif action.startswith("color."):
            self.set_color(int(action[6:]) - 1)
        elif action == "size.down":
            self.set_size(self.size - 1)
        elif action == "size.up":
            self.set_size(self.size + 1)

    def key_release(self, overlay: Overlay, event):
        if keyval(event.key()) != K["Escape"] or event.isAutoRepeat() or not self._escape_down:
            return
        self._escape_down = False
        if self.countdown is not None:
            self.cancel_countdown()
        elif self.eyedropper:
            self.close_picker()  # (its hint says Esc stops it)
        else:
            self.close_picker()  # it closes, and Esc still does what it does
            if not any(o.cancel_gesture() for o in self.overlays):
                self.cancel()

    # -- finishing ---------------------------------------------------------

    def _close_overlays(self):
        self.done = True
        self._desktop_image = None
        self._countdown_timer.stop()
        if self.window_finder:
            self.window_finder.stop()
            self.window_finder = None
        for o in self.overlays:
            o.hide()
            o.release()
            o.deleteLater()
        # Nothing here may keep an overlay's pictures: a notification's
        # buttons can keep this session for as long as the notification is
        # in the desktop's history.
        self.overlays = []
        self.pointer_image = self._plain_image = None
        self.pointer_overlay = self.toolbar_overlay = self._countdown_overlay = None
        self.history, self.redo_stack, self.text_edit = [], [], None
        self._grabbed = None
        QTimer.singleShot(1000, give_back_memory)

    def capture(self, overlay: Overlay, rect: QRectF | None, window: windows.Window | None = None):
        """``window`` is set when ``rect`` is a window the user clicked."""
        if self.done:
            return
        self.commit_text()
        self.clock.step("chosen")
        image = overlay.render(rect)
        self.clock.step("drawn", f"{image.width()} × {image.height()}, {len(overlay.annotations)} drawing"
                        + ("s" if len(overlay.annotations) != 1 else ""))
        screen = overlay.target_screen
        shot = output.Shot(mode="window" if window else ("region" if rect is not None else "monitor"),
                           monitor=screen.name())
        source = window or (self._desktop.active if self._desktop else None)
        if source:
            shot.app, shot.title = source.app, source.title
        at = None
        if not self.request.image:
            g = screen.geometry()
            at = (rect.toAlignedRect() if rect is not None else QRect(0, 0, g.width(), g.height())).translated(
                g.topLeft())
        self._close_overlays()
        # Let the compositor drop the overlays before doing slower work.
        QTimer.singleShot(0, lambda: self._finish_capture(image, shot, at, source))

    def _finish_capture(self, image: QImage, shot: output.Shot, at: QRect | None, source):
        if not source and self._names_window() and not self.request.image:
            # Sway / Hyprland aren't asked while the overlay is up; ask now.
            desktop = windows.query_compositor()
            if desktop and desktop.active:
                shot.app, shot.title = desktop.active.app, desktop.active.title
        if at is not None:
            config.update_state(last_region=[at.x(), at.y(), at.width(), at.height()])
        self._deliver(image, shot, at)

    def _deliver(self, image: QImage, shot: output.Shot | None = None, at: QRect | None = None):
        """``at``: where the image came from, in global logical coordinates."""
        self.done = True
        if self.pin_mode:
            pin.show(image, at)
            self.clock.step("pinned")
            self._play_sound()
            self._finish(0, False)
            return
        try:
            result = output.deliver(image, self.cfg, self.request.output, shot, clock=self.clock)
        except OSError as e:
            self.fail(str(e))
            return
        if result.path and result.saved:
            print(result.path, flush=True)
        self._play_sound()
        if self.cfg.notify:
            self._notify(result, at)
            self.clock.step("notified")
        self._finish(0, result.holds_clipboard)

    def _play_sound(self):
        if self.cfg.sound:
            from flatshot import sound

            sound.play(self.cfg.sound_file)

    def _notify(self, result: output.Delivery, at: QRect | None = None):
        """``at``: where the capture was (global logical), for Pin to open
        there, as long as the monitors are still laid out the same."""
        w, h = result.size
        bits = [f"{w} × {h}"]
        if result.copied:
            bits.append("path copied" if self.cfg.clipboard == "path" else "copied to clipboard")
        if result.saved:
            title = "Screenshot saved"
            bits.insert(0, result.path.name)
        else:
            title = "Screenshot copied" if result.copied else "Screenshot taken"
        actions = {}
        if result.path and self.on_action:
            actions = {"default": "Open", "open": "Open", "folder": "Show in folder", "annotate": "Annotate",
                       "pin": "Pin"}
            if not result.saved:
                actions.pop("folder")
        path, layout, on_action = result.path, pin.screen_layout(), self.on_action  # (not `self`: see above)
        self.notifier.send(title, "  ·  ".join(bits), image=path, actions=actions,
                           on_action=(lambda key: on_action(key, path, at=at, layout=layout)) if actions else None)

    def copy_color(self, overlay: Overlay):
        """Copy the hex colour under the pointer (the one the loupe shows)."""
        if overlay.cursor_pos is None:
            return
        img, dpr = overlay.pixels(), overlay.dpr()
        x = min(max(int(overlay.cursor_pos.x() * dpr), 0), img.width() - 1)
        y = min(max(int(overlay.cursor_pos.y() * dpr), 0), img.height() - 1)
        color = img.pixelColor(x, y).name().upper()
        self._close_overlays()
        _, holds = output.copy_text(color)
        if self.cfg.notify:
            self.notifier.send(f"Copied colour {color}", f"rgb({img.pixelColor(x, y).red()}, "
                               f"{img.pixelColor(x, y).green()}, {img.pixelColor(x, y).blue()})")
        print(color, flush=True)
        self._finish(0, holds)

    def copy_code(self, code):
        self._close_overlays()
        _, holds = output.copy_text(code.text)
        if self.cfg.notify:
            self.notifier.send("Copied from code", code.text[:300])
        print(code.text, flush=True)
        self._finish(0, holds)

    def open_code(self, code):
        self._close_overlays()
        url = code.text.strip()
        try:
            subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            QDesktopServices.openUrl(QUrl(url))
        self._finish(0, False)

    def cancel(self):
        self._close_overlays()
        self._finish(1, False)

    def fail(self, message: str):
        print(f"flatshot: {message}", file=sys.stderr, flush=True)
        self.notifier.send("Screenshot failed", message, urgent=True)
        self._close_overlays()
        self._finish(2, False)

    def _finish(self, code: int, holds_clipboard: bool):
        callback, self.on_finished = self.on_finished, None
        if callback:
            QTimer.singleShot(0, lambda: callback(code, holds_clipboard))
