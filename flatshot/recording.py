"""A screen recording in progress: the recorder, the controls on screen
while it runs, and the finished file (saved, copied, announced).

The controls are two small windows outside the recorded area: a bar with
the time, pause, stop and discard, and corner marks around the area. They
are placed with KWin's help on KDE Plasma (Wayland has no window placement)
and directly on X11; elsewhere the bar opens wherever the desktop puts it.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from flatshot import config, output, screencast, windows
from flatshot.notify import Notifier
from flatshot.qt import (
    QFont, QGuiApplication, QHBoxLayout, QImage, QObject, QPainter, QPainterPath, QPen, QRect, QRectF, QRegion,
    QSize, Qt, QTimer, QWidget, Signal,
)
from flatshot.screencast import Options, RecordError, Target
from flatshot.theme import C, REC, font
from flatshot.widgets import IconButton

STOP_TIMEOUT_S = 10  # for a recorder to finish its file after being asked to stop
FRAME_GAP = 6  # between the recorded area and the corner marks
MARK = 20  # length of a corner mark's arms
MARK_WIDTH = 3

_current: "Recording | None" = None
_when_idle: list = []


def current() -> "Recording | None":
    """The recording in progress, if any (there is at most one)."""
    return _current


def when_idle(callback) -> None:
    """Call ``callback()`` once no recording is in progress."""
    if _current is None:
        callback()
    else:
        _when_idle.append(callback)


def clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02}:{s % 60:02}" if s >= 3600 else f"{s // 60:02}:{s % 60:02}"


def _can_place() -> bool:
    return not QGuiApplication.platformName().startswith("wayland") or windows.WindowFinder.supported()


def _place(widget: QWidget, rect: QRect) -> None:
    widget.setGeometry(rect)
    widget.show()
    if QGuiApplication.platformName().startswith("wayland"):
        QTimer.singleShot(50, lambda: windows.keep_above(widget.windowTitle(), rect))


class Recording(QObject):
    """Records ``target`` once ``start`` is called. ``finished(code,
    holds_clipboard)`` fires once: 0 saved, 1 discarded or cancelled, 2 failed."""

    finished = Signal(int, bool)
    changed = Signal()  # state changed: starting, recording, paused, saving, done
    _saved = Signal(str, str)  # path, error (from the worker thread)

    def __init__(self, cfg: config.Config, target: Target, opts: Options, notifier: Notifier,
                 shot: output.Shot, thumbnail: QImage | None = None, on_action=None, tray: bool = False):
        """``on_action(key, path)`` handles notification buttons (tray only);
        ``tray``: a tray icon can stop the recording, so a full-screen
        recording needs no bar over it."""
        super().__init__()
        global _current
        _current = self
        self.cfg = cfg
        self.target = target
        self.opts = opts
        self.notifier = notifier
        self.shot = shot
        self.thumbnail = thumbnail
        self.on_action = on_action
        self.tray = tray
        self.state = "starting"
        self.how = screencast.method()
        self.portal: screencast.Portal | None = None
        self.proc: subprocess.Popen | None = None
        self.segments: list[str] = []
        self._log = None
        self._stopping_since = 0.0
        self._after_stop = None  # what to do once the current segment has ended
        self._recorded_ms = 0  # before the current segment
        self._segment_start = 0.0
        self._tmp = Path(tempfile.mkdtemp(prefix="flatshot-recording-"))
        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._check)
        self.bar: RecordingBar | None = None
        self.marks: list[CornerMark] = []
        self._saved.connect(self._deliver)

    # -- control -----------------------------------------------------------

    @property
    def can_pause(self) -> bool:
        return screencast.can_join()

    def elapsed_ms(self) -> int:
        if self.state == "recording" and self._segment_start:
            return self._recorded_ms + int((time.monotonic() - self._segment_start) * 1000)
        return self._recorded_ms

    def start(self):
        why = screencast.problem(self.opts.format)
        if why:
            self._fail(why)
            return
        if self.how == "portal":
            self.portal = screencast.Portal(self.target, self.opts.cursor)
            self.portal.ready.connect(self._begin_segment)
            self.portal.failed.connect(self._portal_failed)
            self.portal.start()
        else:
            self._begin_segment()

    def toggle_pause(self):
        if self.state == "recording" and self.can_pause:
            self._set_state("paused")
            self._end_segment(None)
        elif self.state == "paused":
            if self.proc is None:
                self._begin_segment()
            else:
                self._after_stop = self._begin_segment  # once the last segment is written

    def stop(self):
        """Finish and save."""
        if self.state in ("recording", "paused"):
            self._set_state("saving")
            self._end_segment(self._save)
        elif self.state == "starting":
            self.discard()

    def discard(self):
        if self.state in ("saving", "done"):
            return
        self._set_state("saving")
        self._end_segment(lambda: self._done(1, False))

    def set_hint(self, hint):
        """(For the bar's buttons, which report hover hints.)"""

    # -- segments ----------------------------------------------------------

    def _portal_failed(self, message: str, cancelled: bool):
        if self.state != "starting":
            return  # (discarded while the portal was still asking)
        if cancelled:
            self._done(1, False)
        else:
            self._fail(message)

    def _begin_segment(self):
        if self.state in ("saving", "done"):
            return
        path = str(self._tmp / f"part-{len(self.segments) + 1:03}.{screencast.segment_extension(self.opts.format)}")
        try:
            launch = screencast.launch(self.how, self.target, self.opts, path, self.portal)
        except (OSError, RecordError) as e:
            self._fail(str(e))
            return
        try:
            self._log = open(self._tmp / "recorder.log", "ab")
            self.proc = subprocess.Popen(launch.argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=self._log, pass_fds=launch.pass_fds, start_new_session=True)
        except OSError as e:
            self._fail(f"{launch.argv[0]}: {e}")
            return
        finally:
            for fd in launch.pass_fds:
                os.close(fd)  # the recorder has its own copy
        self._stop_signal = getattr(signal, f"SIG{launch.stop_signal}")
        self.segments.append(path)
        self._segment_start = time.monotonic()
        self._poll.start()
        first = self.state == "starting"
        self._set_state("recording")
        if first:
            self._show_controls()

    def _end_segment(self, then):
        """Ask the recorder to finish its file; ``then()`` once it has."""
        if self._segment_start:
            self._recorded_ms += int((time.monotonic() - self._segment_start) * 1000)
            self._segment_start = 0.0
        self._after_stop = then
        if self.proc is None:
            if then:
                then()
            return
        if not self._stopping_since:
            self._stopping_since = time.monotonic()
            try:
                os.killpg(self.proc.pid, self._stop_signal)
            except OSError:
                pass

    def _check(self):
        if self.proc is None:
            return
        code = self.proc.poll()
        if code is None:
            if self._stopping_since and time.monotonic() - self._stopping_since > STOP_TIMEOUT_S:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            return
        stopping = bool(self._stopping_since)
        self.proc = None
        self._stopping_since = 0.0
        if self._log:
            self._log.close()
            self._log = None
        if not stopping:
            # The recorder quit by itself: keep what it recorded, if anything.
            # A segment that ended at once is broken (the recorder failed to start).
            ran = time.monotonic() - self._segment_start if self._segment_start else 0.0
            self._recorded_ms = self.elapsed_ms()
            self._segment_start = 0.0
            self._poll.stop()
            if ran < 1.0 and self.segments:
                Path(self.segments.pop()).unlink(missing_ok=True)
            if self.state == "recording" and self._has_video():
                print(f"flatshot: the recorder stopped by itself: {self._log_tail()}", file=sys.stderr)
                self._set_state("saving")
                self._save()
            else:
                self._fail(self._log_tail() or f"the recorder exited with code {code}")
            return
        self._poll.stop()
        then, self._after_stop = self._after_stop, None
        if then:
            then()

    def _has_video(self) -> bool:
        return any(os.path.exists(p) and os.path.getsize(p) > 0 for p in self.segments)

    def _log_tail(self) -> str:
        try:
            lines = (self._tmp / "recorder.log").read_text(errors="replace").strip().splitlines()
        except OSError:
            return ""
        return lines[-1].strip() if lines else ""

    # -- saving ------------------------------------------------------------

    def _save(self):
        self._hide_controls()
        if self.portal:
            self.portal.close()
        parts = [p for p in self.segments if os.path.exists(p) and os.path.getsize(p) > 0]
        if not parts:
            self._fail(self._log_tail() or "Nothing was recorded.")
            return
        cfg = replace(self.cfg, save_dir=self.cfg.record_dir, filename=self.cfg.record_filename)
        crop = self.target.local_pixels()
        final = output._unique(output.target_path(cfg, self.shot, (crop.width(), crop.height()), self.opts.format))

        def work():
            try:
                final.parent.mkdir(parents=True, exist_ok=True)
                video = parts[0]
                if len(parts) > 1:
                    video = str(self._tmp / f"joined.{screencast.segment_extension(self.opts.format)}")
                    screencast.join(parts, video)
                if self.opts.format == "gif":
                    screencast.to_gif(video, str(final), self.opts.fps)
                else:
                    shutil.move(video, final)
            except (OSError, RecordError, subprocess.SubprocessError) as e:
                self._saved.emit("", str(e))
                return
            self._saved.emit(str(final), "")

        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, path: str, error: str):
        if error:
            self._fail(error)
            return
        final = Path(path)
        print(final, flush=True)
        copied = holds = False
        if self.cfg.clipboard == "image":
            copied, holds = output.copy_file(final)
        elif self.cfg.clipboard == "path":
            copied, holds = output.copy_text(str(final))
        if self.cfg.open_after == "image":
            output.open_file(final)
        elif self.cfg.open_after == "folder":
            output.show_in_folder(final)
        if self.cfg.run_command.strip():
            output.run_command(self.cfg.run_command, final)
        if self.cfg.notify:
            self._notify(final, copied)
        self._done(0, holds)

    def _notify(self, path: Path, copied: bool):
        seconds = screencast.duration(str(path))
        length = clock(round(seconds * 1000) if seconds is not None else self._recorded_ms)
        size = path.stat().st_size
        bits = [path.name, length, f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{max(1, round(size / 1e3))} KB"]
        if copied:
            bits.append("path copied" if self.cfg.clipboard == "path" else "copied to clipboard")
        actions = {}
        on_action = self.on_action  # (not `self`: the notification may outlive the recording by days)
        if on_action:
            actions = {"default": "Open", "open": "Open", "folder": "Show in folder"}
        self.notifier.send("Recording saved", "  ·  ".join(bits), image=self._save_thumbnail(path), file=path,
                           actions=actions, on_action=(lambda key: on_action(key, path)) if actions else None)

    def _save_thumbnail(self, video: Path) -> Path | None:
        """The first frame (the frozen screen), for the notification; the
        newest few are kept."""
        if self.thumbnail is None or self.thumbnail.isNull():
            return None
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        folder = Path(base) / "flatshot" / "thumbnails"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-9]:
                old.unlink(missing_ok=True)
        except OSError:
            return None
        thumb = self.thumbnail
        if thumb.width() > 640 or thumb.height() > 640:
            thumb = thumb.scaled(640, 640, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        path = folder / f"{video.stem}.png"
        return path if thumb.save(str(path)) else None

    # -- the end -----------------------------------------------------------

    def _fail(self, message: str):
        if self.state == "done":
            return
        print(f"flatshot: recording failed: {message}", file=sys.stderr, flush=True)
        self.notifier.send("Recording failed", message, urgent=True)
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
            self.proc = None
        self._done(2, False)

    def _done(self, code: int, holds_clipboard: bool):
        global _current
        if self.state == "done":
            return
        self._poll.stop()
        self._hide_controls()
        if self.portal:
            self.portal.close()
        if self._log:
            self._log.close()
            self._log = None
        shutil.rmtree(self._tmp, ignore_errors=True)
        self._set_state("done")
        if _current is self:
            _current = None
        self.finished.emit(code, holds_clipboard)
        callbacks = list(_when_idle)
        _when_idle.clear()
        for callback in callbacks:
            callback()

    def _set_state(self, state: str):
        if state != self.state:
            self.state = state
            if self.bar:
                self.bar.refresh()
            self.changed.emit()

    # -- controls ----------------------------------------------------------

    def _show_controls(self):
        area = self.target.rect
        screen = self.target.screen.geometry()
        self.bar = RecordingBar(self)
        size = self.bar.sizeHint()
        self.bar.resize(size)
        spot = _bar_spot(area, screen, size)
        if spot is None:
            others = [s.geometry() for s in QGuiApplication.screens() if s is not self.target.screen]
            if others:
                g = others[0]
                spot = QRect(g.center().x() - size.width() // 2, g.bottom() - size.height() - 48, size.width(),
                             size.height())
            elif self.tray:
                # Nowhere outside the recording: the tray icon stops it.
                self.bar.deleteLater()
                self.bar = None
                self.notifier.send("Recording the whole screen",
                                   "Click the Flatshot tray icon, or press the shortcut again, to stop.")
            else:
                spot = QRect(area.center().x() - size.width() // 2, area.bottom() - size.height() - 24,
                             size.width(), size.height())
        if self.bar is not None:
            if _can_place():
                _place(self.bar, spot)
            else:
                self.bar.show()
        if _can_place() and _frame_fits(area, screen):
            # Four small windows wholly outside the area: nothing Flatshot
            # shows can end up in the video, compositor or not.
            out = area.adjusted(-FRAME_GAP - MARK_WIDTH, -FRAME_GAP - MARK_WIDTH,
                                FRAME_GAP + MARK_WIDTH, FRAME_GAP + MARK_WIDTH)
            for corner, (x, y) in {"tl": (out.left(), out.top()), "tr": (out.right() - MARK + 1, out.top()),
                                   "bl": (out.left(), out.bottom() - MARK + 1),
                                   "br": (out.right() - MARK + 1, out.bottom() - MARK + 1)}.items():
                mark = CornerMark(corner)
                self.marks.append(mark)
                _place(mark, QRect(x, y, MARK, MARK))

    def _hide_controls(self):
        for w in [self.bar] + self.marks:
            if w is not None:
                w.hide()
                w.deleteLater()
        self.bar, self.marks = None, []


def _bar_spot(area: QRect, screen: QRect, size: QSize) -> QRect | None:
    """Below the area, else above it, centred; None when neither fits."""
    gap = FRAME_GAP + 12
    x = max(screen.left() + 8, min(area.center().x() - size.width() // 2, screen.right() - size.width() - 8))
    if area.bottom() + gap + size.height() <= screen.bottom() - 8:
        return QRect(x, area.bottom() + gap, size.width(), size.height())
    if area.top() - gap - size.height() >= screen.top() + 8:
        return QRect(x, area.top() - gap - size.height(), size.width(), size.height())
    return None


def _frame_fits(area: QRect, screen: QRect) -> bool:
    """The marks sit outside the area: none when it fills the screen."""
    return area.intersected(screen) != screen


class _Floating(QWidget):
    """A small borderless window above the others that never takes focus."""

    def __init__(self, title: str, extra_flags=Qt.WindowType(0)):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus | extra_flags)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


class StopButton(IconButton):
    """The red circle with a square."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        color = REC.lighter(112) if self.underMouse() else REC
        p.setBrush(color.darker(115) if self.isDown() else color)
        p.drawEllipse(QRectF(self.rect()).adjusted(2, 2, -2, -2))
        s = self.width() * 0.3
        c = QRectF(self.rect()).center()
        p.setBrush(C.TEXT)
        p.drawRoundedRect(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), 2, 2)


class _Clock(QWidget):
    def __init__(self, rec: Recording, parent):
        super().__init__(parent)
        self.rec = rec
        self._font = font(14, QFont.Weight.DemiBold, mono=True)
        self.setFixedSize(76, 36)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(self._font)
        paused = self.rec.state == "paused"
        p.setPen(C.MUTED if paused else C.TEXT)
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, clock(self.rec.elapsed_ms()))


class _MicMark(QWidget):
    """Shows that the microphone is being recorded."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(26, 36)

    def paintEvent(self, event):
        from flatshot import icons

        p = QPainter(self)
        icons.paint(p, "mic", QRectF(4, 9, 18, 18), C.CODE)


class RecordingBar(_Floating):
    """Time, pause / resume, stop and discard."""

    def __init__(self, rec: Recording):
        super().__init__("Flatshot recording")
        self.rec = rec
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 6, 5)
        row.setSpacing(2)
        self.clock = _Clock(rec, self)
        row.addWidget(self.clock)
        if rec.opts.mic and rec.opts.format != "gif":
            row.addWidget(_MicMark(self))
        self.pause = None
        if rec.can_pause:
            self.pause = IconButton(rec, "pause", "Pause", self)
            self.pause.clicked.connect(rec.toggle_pause)
            row.addWidget(self.pause)
        stop = StopButton(rec, "stop", "Stop and save", self)
        stop.clicked.connect(rec.stop)
        stop.setToolTip("Stop and save")
        row.addWidget(stop)
        trash = IconButton(rec, "trash", "Discard", self)
        trash.clicked.connect(rec.discard)
        trash.setToolTip("Discard the recording")
        row.addWidget(trash)
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self.clock.update)
        self._tick.start()
        self.adjustSize()
        if QGuiApplication.platformName() == "xcb":
            # Without a compositor X11 has no translucency: cut the corners.
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self.refresh()

    def refresh(self):
        if self.pause is not None:
            paused = self.rec.state == "paused"
            self.pause.icon = "resume" if paused else "pause"
            self.pause.setToolTip("Resume" if paused else "Pause")
            self.pause.update()
        self.clock.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


class CornerMark(_Floating):
    """One red L at a corner of the recorded area, just outside it. Shaped
    with a mask, so it looks right without a compositor too, and clicks
    pass through."""

    def __init__(self, corner: str):
        super().__init__(f"Flatshot recording area {corner}", Qt.WindowType.WindowTransparentForInput)
        self.corner = corner
        self.setFixedSize(MARK, MARK)
        w = MARK_WIDTH
        x = 0 if "l" in corner else MARK - w
        y = 0 if "t" in corner else MARK - w
        self.arms = [QRect(0, y, MARK, w), QRect(x, 0, w, MARK)]
        shape = QRegion()
        for arm in self.arms:
            shape = shape.united(QRegion(arm))
        self.setMask(shape)

    def paintEvent(self, event):
        p = QPainter(self)
        for arm in self.arms:
            p.fillRect(arm, REC)
