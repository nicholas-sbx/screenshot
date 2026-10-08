"""Record an area of the screen to a video file.

As with screenshots, Wayland doesn't let an app read the screen itself, so
the recorder depends on the desktop:

- KDE Plasma, GNOME and other Wayland desktops: the xdg-desktop-portal
  ScreenCast API. The portal hands over a PipeWire stream of the monitor,
  and GStreamer (``gst-launch-1.0`` with ``pipewiresrc``) crops it to the
  area and encodes it. The first time, the desktop asks which screen to
  share; Flatshot keeps the portal's restore token per monitor, so later
  recordings of that monitor start without asking.
- Sway, Hyprland and other wlroots compositors: ``wf-recorder``.
- X11: ``ffmpeg`` with x11grab.

A recording is a list of segments, one per stretch between pauses; ffmpeg
joins them at the end, and turns the result into a GIF when asked.
"""

import os
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass
from functools import cache

from flatshot import config, dbus
from flatshot.qt import QGuiApplication, QObject, QRect, Signal

FORMATS = [("mp4", "MP4"), ("webm", "WebM"), ("gif", "GIF")]
FPS_CHOICES = (24, 30, 60)
# Audio sources, by PulseAudio name (PipeWire's pulse server knows them too).
MIC = "@DEFAULT_SOURCE@"
SYSTEM = "@DEFAULT_MONITOR@"

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
# SelectSources: source types and cursor modes
MONITOR = 1
CURSOR_HIDDEN, CURSOR_EMBEDDED = 1, 2
PERSIST_UNTIL_REVOKED = 2


class RecordError(RuntimeError):
    pass


class Cancelled(RecordError):
    """The user said no in the desktop's screen-sharing dialog."""


@dataclass
class Options:
    format: str = "mp4"  # see FORMATS
    fps: int = 30
    mic: bool = False
    system_audio: bool = False
    cursor: bool = True

    @classmethod
    def from_config(cls, cfg: config.Config) -> "Options":
        return cls(cfg.record_format, cfg.record_fps, cfg.record_mic, cfg.record_system_audio, cfg.record_cursor)

    def audio(self) -> list[str]:
        """The PulseAudio sources to record (none for a GIF)."""
        if self.format == "gif":
            return []
        return [name for name, on in ((MIC, self.mic), (SYSTEM, self.system_audio)) if on]


@dataclass
class Target:
    """What to record: ``rect`` in global logical coordinates, all on ``screen``."""
    rect: QRect
    screen: object  # QScreen

    def local_pixels(self) -> QRect:
        """The area in the screen's own pixels, with an even size (H.264
        and VP8 need one), kept inside the screen."""
        g, dpr = self.screen.geometry(), self.screen.devicePixelRatio()
        full = QRect(0, 0, round(g.width() * dpr), round(g.height() * dpr))
        r = QRect(round((self.rect.x() - g.x()) * dpr), round((self.rect.y() - g.y()) * dpr),
                  round(self.rect.width() * dpr), round(self.rect.height() * dpr)).intersected(full)
        return QRect(r.x(), r.y(), r.width() & ~1, r.height() & ~1)

    def screen_pixels(self) -> tuple[int, int]:
        g, dpr = self.screen.geometry(), self.screen.devicePixelRatio()
        return round(g.width() * dpr), round(g.height() * dpr)


# -- tools ----------------------------------------------------------------------

def have(tool: str) -> bool:
    return shutil.which(tool) is not None


@cache
def gst_has(element: str) -> bool:
    if not have("gst-inspect-1.0"):
        return False
    try:
        return subprocess.run(["gst-inspect-1.0", "--exists", element], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def can_join() -> bool:
    """Pausing records separate segments that ffmpeg joins at the end."""
    return have("ffmpeg")


def can_gif() -> bool:
    return have("ffmpeg")


def method() -> str:
    """How this desktop records: portal, wf-recorder, x11, or the test
    pattern (FLATSHOT_RECORDER overrides it)."""
    forced = os.environ.get("FLATSHOT_RECORDER", "")
    if forced:
        return forced
    if not QGuiApplication.platformName().startswith("wayland"):
        return "x11"
    if (os.environ.get("SWAYSOCK") or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and have("wf-recorder"):
        return "wf-recorder"
    return "portal"


METHOD_LABELS = {
    "portal": "Screen-cast portal and GStreamer",
    "wf-recorder": "wf-recorder",
    "x11": "ffmpeg (X11)",
    "test": "Test pattern (ffmpeg)",
    "test-gst": "Test pattern (GStreamer)",
}


def problem(fmt: str = "mp4") -> str | None:
    """Why recording can't work here (what to install), or None."""
    how = method()
    if how in ("x11", "test") and not have("ffmpeg"):
        return "Recording needs ffmpeg."
    if how == "wf-recorder" and not have("wf-recorder"):
        return "Recording needs wf-recorder."
    if how in ("portal", "test-gst"):
        if how == "portal" and not dbus.available():
            return "Recording needs the python jeepney module."
        if not have("gst-launch-1.0"):
            return "Recording needs GStreamer (gst-launch-1.0)."
        if how == "portal" and not gst_has("pipewiresrc"):
            return "Recording needs GStreamer's PipeWire plugin (gstreamer1.0-pipewire or gst-plugin-pipewire)."
        if _gst_video_encoder("webm" if fmt == "webm" else "mp4") is None:
            return ("Recording needs a GStreamer video encoder: x264enc or openh264enc for MP4, "
                    "vp8enc for WebM (gst-plugins-ugly / -bad / -good).")
        if not all(gst_has(e) for e in ("videoconvert", "videocrop", "videorate",
                                         "webmmux" if fmt == "webm" else "mp4mux")):
            return "Recording needs GStreamer's base and good plugins (gst-plugins-base, gst-plugins-good)."
    if fmt == "gif" and not can_gif():
        return "GIFs need ffmpeg."
    return None


def formats_available() -> list[tuple[str, str]]:
    return [f for f in FORMATS if problem(f[0]) is None]


# -- encoding settings ---------------------------------------------------------------

def _bitrate(size: QRect, fps: int) -> int:
    """A generous screen-content bitrate in kbit/s."""
    return max(1500, min(40000, round(size.width() * size.height() * fps * 0.08 / 1000)))


def _ffmpeg_codecs(fmt: str, size: QRect, fps: int, audio: bool) -> list[str]:
    if fmt == "webm":
        video = ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", f"{_bitrate(size, fps)}k",
                 "-crf", "10", "-auto-alt-ref", "0"]
        sound = ["-c:a", "libopus", "-b:a", "128k"]
    else:  # mp4, and the intermediate file for a GIF
        video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-tune", "zerolatency"]
        sound = ["-c:a", "aac", "-b:a", "160k"]
    return video + ["-pix_fmt", "yuv420p"] + (sound if audio else [])


def _ffmpeg_audio(sources: list[str], first_index: int) -> tuple[list[str], list[str]]:
    """(inputs, mapping) for PulseAudio sources, mixed into one track."""
    inputs = []
    for name in sources:
        inputs += ["-thread_queue_size", "1024", "-f", "pulse", "-i", name]
    if not sources:
        return [], []
    if len(sources) == 1:
        return inputs, ["-map", "0:v", "-map", f"{first_index}:a"]
    mix = "".join(f"[{first_index + i}:a]" for i in range(len(sources)))
    return inputs, ["-filter_complex", f"{mix}amix=inputs={len(sources)}:duration=longest[a]",
                    "-map", "0:v", "-map", "[a]"]


@cache
def _gst_video_encoder(fmt: str) -> str | None:
    if fmt == "webm":
        if gst_has("vp8enc"):
            return "vp8enc deadline=1 cpu-used=8 threads=4 end-usage=cbr target-bitrate={bps} keyframe-max-dist=120"
        return None
    parse = " ! h264parse" if gst_has("h264parse") else ""
    if gst_has("x264enc"):
        return ("x264enc speed-preset=veryfast tune=zerolatency bitrate={kbps} key-int-max=120 "
                "! video/x-h264,profile=high" + parse)
    if gst_has("openh264enc"):
        return "openh264enc bitrate={bps} complexity=low" + parse
    return None


@cache
def _gst_audio_encoder(fmt: str) -> str | None:
    choices = ["opusenc", "vorbisenc"] if fmt == "webm" else ["avenc_aac", "fdkaacenc", "voaacenc", "opusenc"]
    extra = {"opusenc": " bitrate=128000", "avenc_aac": " bitrate=160000", "fdkaacenc": " bitrate=160000",
             "voaacenc": " bitrate=160000"}
    for name in choices:
        if gst_has(name):
            return name + extra.get(name, "")
    return None


def gst_pipeline(source: str, frame: tuple[int, int], crop: QRect, opts: Options, path: str) -> str:
    """The gst-launch-1.0 pipeline: ``source`` (frames of size ``frame``)
    cropped to ``crop``, encoded for ``opts.format``, written to ``path``."""
    fmt = "webm" if opts.format == "webm" else "mp4"
    encoder = _gst_video_encoder(fmt)
    if encoder is None:
        raise RecordError(problem(fmt) or "no GStreamer video encoder")
    kbps = _bitrate(crop, opts.fps)
    w, h = frame
    left, top = crop.x(), crop.y()
    right, bottom = max(0, w - crop.x() - crop.width()), max(0, h - crop.y() - crop.height())
    mux = "webmmux" if fmt == "webm" else "mp4mux fragment-duration=1000"
    # The scale only matters if the stream isn't quite the size Qt reports:
    # the output keeps the even size encoders need.
    parts = [f"{source} ! videoconvert ! videocrop left={left} top={top} right={right} bottom={bottom} "
             f"! videoscale ! video/x-raw,width={crop.width()},height={crop.height()} "
             f"! videorate ! video/x-raw,framerate={opts.fps}/1 ! videoconvert ! video/x-raw,format=I420 "
             f"! queue max-size-buffers=0 max-size-bytes=0 max-size-time=2000000000 "
             f"! {encoder.format(kbps=kbps, bps=kbps * 1000)} ! queue ! {mux} name=mux ! filesink location={_quote(path)}"]
    audio = opts.audio()
    if audio:
        aenc = _gst_audio_encoder(fmt)
        if aenc is None:
            raise RecordError("Recording sound needs a GStreamer audio encoder (opusenc or an AAC encoder).")
        if not gst_has("pulsesrc"):
            raise RecordError("Recording sound needs GStreamer's PulseAudio plugin (pulsesrc).")
        tail = f"audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=2 ! {aenc} ! queue ! mux."
        if len(audio) == 1:
            parts.append(f"pulsesrc device={audio[0]} do-timestamp=true ! queue ! {tail}")
        else:
            parts.append(f"audiomixer name=amix ! {tail}")
            for name in audio:
                parts.append(f"pulsesrc device={name} do-timestamp=true ! queue ! audioconvert ! audioresample "
                             f"! audio/x-raw,rate=48000,channels=2 ! amix.")
    return "  ".join(parts)


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def segment_extension(fmt: str) -> str:
    """The file type segments are recorded as (a GIF is made at the end)."""
    return "webm" if fmt == "webm" else "mp4"


# -- the screen-cast portal ------------------------------------------------------------

class Portal(QObject):
    """One xdg-desktop-portal ScreenCast session for one monitor. ``ready``
    or ``failed`` fires once after ``start``; the session then lasts until
    ``close``. Talks to the portal on its own connection (it must pass a
    file descriptor and stay open for as long as the stream is used)."""

    ready = Signal()
    failed = Signal(str, bool)  # message, cancelled by the user

    def __init__(self, target: Target, cursor: bool):
        super().__init__()
        self.target = target
        self.cursor = cursor
        self.node = 0
        self.stream_rect: QRect | None = None  # the shared monitor, global logical coords
        self._conn = None
        self._session = ""
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._negotiate, daemon=True).start()

    def _negotiate(self):
        try:
            self._open()
        except Cancelled as e:
            self.failed.emit(str(e), True)
        except Exception as e:  # noqa: BLE001 — any D-Bus or portal failure
            self.failed.emit(str(e) or repr(e), False)
        else:
            self.ready.emit()

    def _call(self, method, sig, *args, iface=SCREENCAST, path=PORTAL_PATH, timeout=10.0):
        from jeepney import DBusAddress, new_method_call
        from jeepney.wrappers import unwrap_msg

        msg = new_method_call(DBusAddress(path, bus_name=PORTAL, interface=iface), method, sig, args)
        return unwrap_msg(self._conn.send_and_get_reply(msg, timeout=timeout))

    def _property(self, name: str, default):
        try:
            return self._call("Get", "ss", SCREENCAST, name, iface="org.freedesktop.DBus.Properties")[0][1]
        except Exception:  # noqa: BLE001 — older portals lack some properties
            return default

    def _request(self, method, sig, *args, timeout=10.0) -> dict:
        """Call a portal method that answers later with a Request.Response
        signal; returns its results. ``args`` ends with the options dict."""
        from jeepney import MatchRule
        from jeepney.bus_messages import message_bus

        token = "flatshot_" + secrets.token_hex(6)
        sender = self._conn.unique_name.lstrip(":").replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface=REQUEST, member="Response", path=path)
        self._conn.send_and_get_reply(message_bus.AddMatch(rule), timeout=5)
        options = {**args[-1], "handle_token": ("s", token)}
        with self._conn.filter(rule) as queue:
            self._call(method, sig, *args[:-1], options)
            reply = self._conn.recv_until_filtered(queue, timeout=timeout)
        code, results = reply.body
        if code == 1:
            raise Cancelled("Screen sharing was cancelled.")
        if code != 0:
            raise RecordError(f"the screen-cast portal refused ({method})")
        return results

    def _open(self):
        from jeepney.io.blocking import open_dbus_connection

        try:
            self._conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        except Exception as e:  # noqa: BLE001
            raise RecordError(f"no session bus: {e}") from e
        version = self._property("version", 1)
        cursors = self._property("AvailableCursorModes", CURSOR_HIDDEN)
        results = self._request("CreateSession", "a{sv}",
                                 {"session_handle_token": ("s", "flatshot_" + secrets.token_hex(6))})
        self._session = results["session_handle"][1]
        options = {"types": ("u", MONITOR), "multiple": ("b", False)}
        wanted = CURSOR_EMBEDDED if self.cursor else CURSOR_HIDDEN
        if cursors & wanted:
            options["cursor_mode"] = ("u", wanted)
        name = self.target.screen.name()
        if version >= 4:
            options["persist_mode"] = ("u", PERSIST_UNTIL_REVOKED)
            token = (config.load_state().get("screencast_tokens") or {}).get(name)
            if isinstance(token, str) and token:
                options["restore_token"] = ("s", token)
        self._request("SelectSources", "oa{sv}", self._session, options)
        # Waits for the user when the desktop asks which screen to share.
        results = self._request("Start", "osa{sv}", self._session, "", {}, timeout=600)
        streams = results.get("streams", ("", []))[1]
        if not streams:
            raise RecordError("the screen-cast portal shared no screen")
        node, props = streams[0]
        self.node = int(node)
        pos, size = props.get("position"), props.get("size")
        if pos and size:
            (x, y), (w, h) = pos[1], size[1]
            self.stream_rect = QRect(int(x), int(y), int(w), int(h))
        shared = self._shared_screen()
        if "restore_token" in results and shared is not None:
            tokens = config.load_state().get("screencast_tokens")
            tokens = tokens if isinstance(tokens, dict) else {}
            tokens[shared.name()] = results["restore_token"][1]
            config.update_state(screencast_tokens=tokens)
        if shared is not None and shared is not self.target.screen:
            raise RecordError(f"The shared screen is {shared.name()}, but the area is on {name}. "
                              f"Record again and share {name}.")

    def _shared_screen(self):
        screens = QGuiApplication.screens()
        if self.stream_rect is not None:
            for s in screens:
                if s.geometry().topLeft() == self.stream_rect.topLeft():
                    return s
            return None
        return screens[0] if len(screens) == 1 else self.target.screen

    def pipewire_fd(self) -> int:
        """A new PipeWire connection for the stream (one per segment); the
        caller owns the descriptor."""
        with self._lock:
            fd = self._call("OpenPipeWireRemote", "oa{sv}", self._session, {})[0]
        return fd.to_raw_fd()

    def close(self):
        with self._lock:
            if self._conn is None:
                return
            if self._session:
                try:
                    self._call("Close", None, iface=SESSION, path=self._session, timeout=2)
                except Exception:  # noqa: BLE001 — closing the connection ends it too
                    pass
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None


# -- segments -------------------------------------------------------------------

@dataclass
class Launch:
    argv: list[str]
    pass_fds: tuple[int, ...] = ()
    stop_signal: str = "INT"  # how to make the recorder finish the file cleanly


def launch(how: str, target: Target, opts: Options, path: str, portal: Portal | None = None) -> Launch:
    """The command that records one segment to ``path``."""
    crop = target.local_pixels()
    if crop.width() < 2 or crop.height() < 2:
        raise RecordError("The area is too small to record.")
    audio = opts.audio()
    ext = segment_extension(opts.format)
    if how in ("x11", "test"):
        if how == "x11":
            g = target.screen.geometry()
            # Qt keeps an X11 screen's origin in device pixels.
            x, y = g.x() + crop.x(), g.y() + crop.y()
            display = os.environ.get("DISPLAY", ":0")
            video = ["-f", "x11grab", "-framerate", str(opts.fps), "-video_size", f"{crop.width()}x{crop.height()}",
                     "-draw_mouse", "1" if opts.cursor else "0", "-i", f"{display}+{x},{y}"]
        else:
            video = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={crop.width()}x{crop.height()}:rate={opts.fps}"]
        inputs, mapping = _ffmpeg_audio(audio, 1)
        argv = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-thread_queue_size", "1024"]
                + video + inputs + mapping + _ffmpeg_codecs(ext, crop, opts.fps, bool(audio))
                + ["-r", str(opts.fps), path])
        return Launch(argv)
    if how == "wf-recorder":
        r = target.rect
        argv = ["wf-recorder", "-y", "-g", f"{r.x()},{r.y()} {r.width()}x{r.height()}", "-r", str(opts.fps),
                "-x", "yuv420p", "-f", path]
        if ext == "webm":
            argv += ["-c", "libvpx", "-p", "deadline=realtime", "-p", "cpu-used=8"]
        else:
            argv += ["-c", "libx264", "-p", "preset=veryfast", "-p", "crf=20"]
        if audio:
            argv.append(f"--audio={audio[0]}")  # wf-recorder records one source
        return Launch(argv, stop_signal="INT")
    if how in ("portal", "test-gst"):
        frame = target.screen_pixels()
        if how == "portal":
            fd = portal.pipewire_fd()
            source = f"pipewiresrc fd={fd} path={portal.node} do-timestamp=true keepalive-time=1000"
            fds = (fd,)
        else:
            source = (f"videotestsrc is-live=true pattern=smpte ! video/x-raw,width={frame[0]},height={frame[1]},"
                      f"framerate=30/1")
            fds = ()
        pipeline = gst_pipeline(source, frame, crop, opts, path)
        return Launch(["gst-launch-1.0", "-e", "-q"] + _split(pipeline), pass_fds=fds)
    raise RecordError(f"unknown recorder {how!r}")


def _split(pipeline: str) -> list[str]:
    """gst-launch takes the pipeline as words; keep quoted paths whole."""
    import shlex

    return shlex.split(pipeline)


# -- finishing -----------------------------------------------------------------------

def _ffmpeg(args: list[str], timeout: float) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          timeout=timeout)
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        raise RecordError("ffmpeg: " + (detail[-1] if detail else f"exit {proc.returncode}"))


def join(segments: list[str], out: str) -> None:
    """Concatenate segments recorded with the same settings, without re-encoding."""
    listing = out + ".txt"
    with open(listing, "w") as f:
        for path in segments:
            f.write("file '" + path.replace("'", "'\\''") + "'\n")
    try:
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", out], timeout=600)
    finally:
        os.unlink(listing)


def to_gif(video: str, out: str, fps: int) -> None:
    """A GIF with its own palette (far better than the default one)."""
    rate = min(fps, 30)
    _ffmpeg(["-i", video, "-vf", f"fps={rate},split[a][b];[a]palettegen=stats_mode=diff[p];"
             f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0", out], timeout=1800)


def duration(path: str) -> float | None:
    """Seconds of video, when ffprobe is around to say."""
    if not have("ffprobe"):
        return None
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        return float(proc.stdout.decode().strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
