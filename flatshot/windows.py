"""Window bounds at capture time, for hover-and-click window capture.

Wayland gives clients no window list, so on KDE we ask KWin: a small KWin
script (loaded over org.kde.KWin's scripting D-Bus API) reads the stacking
order and calls back into this process with every visible window's frame
geometry. Coordinates are global logical pixels, like QScreen geometry.
"""

import json
import os
import tempfile
from pathlib import Path

from flatshot import dbus
from flatshot.qt import QObject, QRect, Signal

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
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        try {
            if (!(w.normalWindow || w.dialog) || w.minimized || w.deleted || w.hidden) continue;
            if (!onCurrent(w)) continue;
            var g = w.frameGeometry;
            if (!g || g.width < 8 || g.height < 8) continue;
            out.push([Math.round(g.x), Math.round(g.y), Math.round(g.width), Math.round(g.height),
                      String(w.caption || ""), String(w.resourceClass || "")]);
        } catch (e) {}
    }
    callDBus("%SERVICE%", "%PATH%", "%IFACE%", "report", JSON.stringify(out));
})();
"""


class Window:
    def __init__(self, rect: QRect, title: str, app: str):
        self.rect, self.title, self.app = rect, title, app

    def __repr__(self):
        return f"Window({self.title!r}, {self.rect})"


def parse(report: str) -> list[Window]:
    """KWin script output -> windows, topmost first."""
    try:
        rows = json.loads(report)
    except ValueError:
        return []
    out = []
    for row in rows:
        try:
            x, y, w, h, title, app = row
            out.append(Window(QRect(int(x), int(y), int(w), int(h)), str(title), str(app)))
        except (TypeError, ValueError):
            continue
    out.reverse()
    return out


class WindowFinder(QObject):
    found = Signal(list)  # list[Window], topmost first

    def __init__(self):
        super().__init__()
        self._listener = None
        self._script_name = ""
        self._script_file = ""

    @staticmethod
    def supported() -> bool:
        return dbus.available() and dbus.has_owner(KWIN)

    def start(self) -> bool:
        """Kick off the query in the background; ``found`` fires later."""
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
            windows = parse(str(body[0]))
            self.stop()
            self.found.emit(windows)

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
