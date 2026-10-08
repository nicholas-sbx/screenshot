"""The desktop at capture time: window bounds (for hover-and-click window
capture), the active window and the pointer position (for the instant
"active window" and "current monitor" captures).

Wayland gives clients no window list, so on KDE we ask KWin: a small KWin
script (loaded over org.kde.KWin's scripting D-Bus API) reads the stacking
order and calls back into this process with every visible window's frame
geometry. Sway and Hyprland answer the same questions through their IPC
tools. Coordinates are global logical pixels, like QScreen geometry.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flatshot import dbus
from flatshot.qt import QObject, QPoint, QRect, QTimer, Signal

KWIN = "org.kde.KWin"
SCRIPTING_PATH = "/Scripting"
SCRIPTING_IFACE = "org.kde.kwin.Scripting"
REPORT_PATH = "/flatshot/windows"
REPORT_IFACE = "org.flatshot.Windows"

# Plasma 5 and 6 differ in how desktops are exposed; handle both.
SCRIPT = r"""
(function () {
    var cur = workspace.currentDesktop;
    var list = workspace.stackingOrder;  // bottom to top
    var out = [];
    function onCurrent(w) {
        if (w.onAllDesktops) return true;
        if (w.desktops !== undefined && w.desktops !== null && w.desktops.length !== undefined) {
            for (var j = 0; j < w.desktops.length; j++) {
                var d = w.desktops[j];
                if (d === cur || (d && cur && d.id !== undefined && d.id === cur.id)) return true;
            }
            return w.desktops.length === 0;
        }
        if (w.desktop !== undefined) return w.desktop === cur || w.desktop === -1;
        return true;
    }
    var act = workspace.activeWindow || workspace.activeClient;  // Plasma 6 / 5
    var active = -1;
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        try {
            if (!(w.normalWindow || w.dialog) || w.minimized || w.deleted || w.hidden) continue;
            if (!onCurrent(w)) continue;
            var g = w.frameGeometry;
            if (!g || g.width < 8 || g.height < 8) continue;
            if (act && (w === act || String(w.internalId) === String(act.internalId))) active = out.length;
            out.push([Math.round(g.x), Math.round(g.y), Math.round(g.width), Math.round(g.height),
                      String(w.caption || ""), String(w.resourceClass || "")]);
        } catch (e) {}
    }
    var cursor = null;
    try { cursor = [Math.round(workspace.cursorPos.x), Math.round(workspace.cursorPos.y)]; } catch (e) {}
    callDBus("%SERVICE%", "%PATH%", "%IFACE%", "report",
             JSON.stringify({windows: out, active: active, cursor: cursor}));
})();
"""


class Window:
    def __init__(self, rect: QRect, title: str, app: str):
        self.rect, self.title, self.app = rect, title, app

    def __repr__(self):
        return f"Window({self.title!r}, {self.rect})"


class Desktop:
    def __init__(self, windows=None, active: Window | None = None, cursor: QPoint | None = None):
        self.windows: list[Window] = windows or []  # topmost first
        self.active = active
        self.cursor = cursor


def parse(report: str) -> Desktop:
    """KWin script output (bottom-to-top rows) -> Desktop."""
    try:
        data = json.loads(report)
    except ValueError:
        return Desktop()
    if isinstance(data, list):
        data = {"windows": data}
    if not isinstance(data, dict):
        return Desktop()
    rows = data.get("windows") or []
    active_index = data.get("active", -1)
    out, active = [], None
    for i, row in enumerate(rows):
        try:
            x, y, w, h, title, app = row
            window = Window(QRect(int(x), int(y), int(w), int(h)), str(title), str(app))
        except (TypeError, ValueError):
            continue
        out.append(window)
        if i == active_index:
            active = window
    out.reverse()
    cursor = None
    try:
        cx, cy = data.get("cursor")
        cursor = QPoint(int(cx), int(cy))
    except (TypeError, ValueError):
        pass
    return Desktop(out, active, cursor)


def _json_from(argv: list[str]):
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=2, stdin=subprocess.DEVNULL)
        return json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _hyprland() -> Desktop | None:
    win = _json_from(["hyprctl", "-j", "activewindow"])
    pos = _json_from(["hyprctl", "-j", "cursorpos"])
    active = None
    try:
        (x, y), (w, h) = win["at"], win["size"]
        active = Window(QRect(int(x), int(y), int(w), int(h)), str(win.get("title", "")), str(win.get("class", "")))
    except (TypeError, KeyError, ValueError):
        pass
    cursor = None
    try:
        cursor = QPoint(int(pos["x"]), int(pos["y"]))
    except (TypeError, KeyError, ValueError):
        pass
    return Desktop([active] if active else [], active, cursor)


def _sway() -> Desktop | None:
    tree = _json_from(["swaymsg", "-t", "get_tree"])
    if not isinstance(tree, dict):
        return None
    active, output, stack = None, None, [(tree, None)]
    while stack:
        node, out = stack.pop()
        if node.get("type") == "output":
            out = node
        if node.get("focused"):
            r = node.get("rect") or {}
            if node.get("type") in ("con", "floating_con") and r.get("width"):
                app = node.get("app_id") or (node.get("window_properties") or {}).get("class") or ""
                active = Window(QRect(r["x"], r["y"], r["width"], r["height"]), str(node.get("name") or ""),
                                str(app))
            output = out
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            stack.append((child, out))
    cursor = None
    if output and output.get("rect"):  # sway has no pointer query: use the focused output's centre
        r = output["rect"]
        cursor = QPoint(r["x"] + r["width"] // 2, r["y"] + r["height"] // 2)
    return Desktop([active] if active else [], active, cursor)


def query_compositor() -> Desktop | None:
    """Desktop state from Hyprland or Sway, if that's what is running."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return _hyprland()
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        return _sway()
    return None


class WindowFinder(QObject):
    found = Signal(object)  # Desktop

    def __init__(self):
        super().__init__()
        self._listener = None
        self._script_name = ""
        self._script_file = ""

    @staticmethod
    def supported() -> bool:
        return dbus.available() and dbus.has_owner(KWIN)

    def start(self, background: bool = True) -> bool:
        """Ask the compositor about the desktop; ``found`` fires later if True.

        background (the overlay): only KWin is asked, off the main thread, so
        the overlay appears first. Otherwise (instant captures) Sway and
        Hyprland are asked too, and False means nothing can answer."""
        if not background:
            desktop = query_compositor()
            if desktop is not None:
                QTimer.singleShot(0, lambda: self.found.emit(desktop))
                return True
            return dbus.available() and self._start()
        if not dbus.available():
            return False
        import threading

        threading.Thread(target=self._start, daemon=True).start()
        return True

    def _start(self) -> bool:
        if not self.supported():
            return False
        self._listener = dbus.SignalListener([])
        self._listener.received.connect(self._on_message)
        if not self._listener.start():
            return False
        script = (SCRIPT.replace("%SERVICE%", self._listener.unique_name)
                  .replace("%PATH%", REPORT_PATH).replace("%IFACE%", REPORT_IFACE))
        fd, self._script_file = tempfile.mkstemp(prefix="flatshot-windows-", suffix=".js")
        with os.fdopen(fd, "w") as f:
            f.write(script)
        self._script_name = f"flatshot-windows-{os.getpid()}"
        try:
            self._kwin("unloadScript", "s", self._script_name)
            self._kwin("loadScript", "ss", self._script_file, self._script_name)
            self._kwin("start")
        except dbus.DBusError:
            self.stop()
            return False
        return True

    @staticmethod
    def _kwin(method, sig=None, *args):
        return dbus.call(KWIN, SCRIPTING_PATH, SCRIPTING_IFACE, method, sig, *args)

    def _on_message(self, iface, member, body):
        if iface == REPORT_IFACE and member == "report" and body:
            desktop = parse(str(body[0]))
            self.stop()
            self.found.emit(desktop)

    def stop(self):
        if self._script_name:
            try:
                self._kwin("unloadScript", "s", self._script_name)
            except dbus.DBusError:
                pass
            self._script_name = ""
        if self._script_file:
            Path(self._script_file).unlink(missing_ok=True)
            self._script_file = ""
        if self._listener:
            self._listener.stop()
            self._listener = None


KEEP_ABOVE = r"""
(function () {
    var list = workspace.windowList ? workspace.windowList() : workspace.clientList();  // Plasma 6 / 5
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        if (w.pid === %PID% && w.caption === %CAPTION%) {
            w.keepAbove = true;
            w.skipTaskbar = true;
            w.skipPager = true;
            w.skipSwitcher = true;
            var g = %GEOMETRY%;
            if (g) {
                try { w.frameGeometry = g; } catch (e) { try { w.geometry = g; } catch (e2) {} }
            }
        }
    }
})();
"""


def keep_above(caption: str, geometry: QRect | None = None) -> bool:
    """Ask KWin to keep this process's window titled ``caption`` above others,
    and optionally place it (Wayland has no protocol for either). False when
    not on KDE."""
    if not WindowFinder.supported():
        return False
    g = "null" if geometry is None else json.dumps(
        {"x": geometry.x(), "y": geometry.y(), "width": geometry.width(), "height": geometry.height()})
    script = (KEEP_ABOVE.replace("%PID%", str(os.getpid())).replace("%CAPTION%", json.dumps(caption))
              .replace("%GEOMETRY%", g))
    fd, path = tempfile.mkstemp(prefix="flatshot-pin-", suffix=".js")
    with os.fdopen(fd, "w") as f:
        f.write(script)
    name = f"flatshot-pin-{os.getpid()}-{caption.rsplit(' ', 1)[-1]}"

    def cleanup():
        try:
            WindowFinder._kwin("unloadScript", "s", name)
        except dbus.DBusError:
            pass
        Path(path).unlink(missing_ok=True)

    try:
        WindowFinder._kwin("loadScript", "ss", path, name)
        WindowFinder._kwin("start")
    except dbus.DBusError:
        cleanup()
        return False
    QTimer.singleShot(3000, cleanup)  # KWin reads and runs the script asynchronously
    return True
