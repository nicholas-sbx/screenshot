"""File dialogs from the desktop: KDE's (or GNOME's) own, through the XDG
desktop portal's FileChooser, asked over D-Bus without blocking, so a tray
app never hangs waiting on it. Where there's no portal (or no jeepney), Qt's
dialog opens instead, also without blocking.

Each function returns at once and later calls ``on_chosen(path)`` on the Qt
main thread with the chosen path, or doesn't call it at all if the dialog
was cancelled."""

import secrets
import threading
from pathlib import Path

from flatshot import dbus
from flatshot.qt import QFileDialog, QGuiApplication, QObject, Qt, QUrl, Signal

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
CHOOSER = "org.freedesktop.portal.FileChooser"
REQUEST = "org.freedesktop.portal.Request"

# A filter: (name, ["*.png", ...]).
Filter = tuple[str, list[str]]

_pending: set = set()  # requests waiting for an answer (kept alive until then)


def save_file(parent, title: str, path: Path, filters: list[Filter], on_chosen, current: int = 0) -> None:
    """Where to save, starting at ``path``; ``current`` is the filter shown first."""
    path = Path(path)
    options = {"current_name": ("s", path.name), "accept_label": ("s", "Save")}
    if path.parent.is_dir():
        options["current_folder"] = ("ay", _bytes(path.parent))
    if filters:
        options["filters"] = ("a(sa(us))", _filters(filters))
        options["current_filter"] = ("(sa(us))", _filters(filters)[min(max(current, 0), len(filters) - 1)])

    def fallback():
        dialog = _dialog(parent, title, str(path), filters)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        if filters:
            dialog.selectNameFilter(_qt_filters(filters)[min(max(current, 0), len(filters) - 1)])
        return dialog

    _ask("SaveFile", parent, title, options, on_chosen, fallback)


def open_file(parent, title: str, folder: str | Path, filters: list[Filter], on_chosen) -> None:
    """An existing file to open, starting in ``folder``."""
    options = {"multiple": ("b", False), "accept_label": ("s", "Open")}
    if folder and Path(folder).is_dir():
        options["current_folder"] = ("ay", _bytes(Path(folder)))
    if filters:
        options["filters"] = ("a(sa(us))", _filters(filters))

    def fallback():
        dialog = _dialog(parent, title, str(folder or ""), filters)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        return dialog

    _ask("OpenFile", parent, title, options, on_chosen, fallback)


def choose_folder(parent, title: str, folder: str | Path, on_chosen) -> None:
    """A folder, starting in ``folder``."""
    options = {"multiple": ("b", False), "directory": ("b", True), "accept_label": ("s", "Choose")}
    if folder and Path(folder).is_dir():
        options["current_folder"] = ("ay", _bytes(Path(folder)))

    def fallback():
        dialog = _dialog(parent, title, str(folder or ""), [])
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly)
        return dialog

    _ask("OpenFile", parent, title, options, on_chosen, fallback)


# -- the portal ------------------------------------------------------------------


class _Request(QObject):
    """One question to the portal, asked from a thread. The answer comes
    back to ``done(ok, path)`` on the main thread (where this object lives):
    ok False if the portal couldn't be asked at all."""

    answered = Signal(bool, str)

    def __init__(self, method: str, parent_window: str, title: str, options: dict, done):
        super().__init__()
        self.args = (method, parent_window, title, options)
        self.done = done
        self.answered.connect(self._answered, Qt.ConnectionType.QueuedConnection)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            path = _portal(*self.args)
        except Exception:  # noqa: BLE001 — no portal, no bus, an old portal: Qt's dialog then
            self.answered.emit(False, "")
            return
        self.answered.emit(True, path or "")

    def _answered(self, ok: bool, path: str):
        _pending.discard(self)
        self.done(ok, path)


def _portal(method: str, parent_window: str, title: str, options: dict) -> str | None:
    """Ask, wait (as long as the user takes) and return the chosen path."""
    from jeepney import DBusAddress, MatchRule, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import unwrap_msg

    conn = open_dbus_connection(bus="SESSION")
    try:
        token = "flatshot" + secrets.token_hex(6)
        sender = conn.unique_name.lstrip(":").replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface=REQUEST, member="Response", path=path)
        conn.send_and_get_reply(message_bus.AddMatch(rule), timeout=5)
        options = {**options, "handle_token": ("s", token)}
        call = new_method_call(DBusAddress(PORTAL_PATH, bus_name=PORTAL, interface=CHOOSER), method, "ssa{sv}",
                               (parent_window, title, options))
        with conn.filter(rule) as queue:
            unwrap_msg(conn.send_and_get_reply(call, timeout=10))
            reply = conn.recv_until_filtered(queue)  # (no timeout: the user may take a while)
        code, results = reply.body
        if code != 0:
            return None  # cancelled
        uris = results.get("uris", ("as", []))[1]
        return QUrl(uris[0]).toLocalFile() if uris else None
    finally:
        conn.close()


def _ask(method: str, parent, title: str, options: dict, on_chosen, fallback) -> None:
    if not dbus.available():  # (the portal itself starts when asked; failing that, Qt's)
        _qt(fallback(), on_chosen)
        return

    def done(ok: bool, path: str):
        if not ok:
            _qt(fallback(), on_chosen)
        elif path:
            on_chosen(path)

    request = _Request(method, _window_handle(parent), title, options, done)
    _pending.add(request)
    request.start()


def _window_handle(parent) -> str:
    """The parent window as the portal names it (X11 only: on Wayland it
    would need the window exported, and the dialog is fine on its own)."""
    if parent is None or QGuiApplication.platformName() != "xcb":
        return ""
    try:
        return f"x11:{int(parent.window().winId()):x}"
    except (RuntimeError, TypeError, ValueError):
        return ""


def _bytes(path: Path) -> bytes:
    return str(path).encode() + b"\0"


def _filters(filters: list[Filter]) -> list:
    return [(name, [(0, pattern) for pattern in patterns]) for name, patterns in filters]


# -- Qt's dialog, where there's no portal -------------------------------------------


def _qt_filters(filters: list[Filter]) -> list[str]:
    return [f"{name} ({' '.join(patterns)})" for name, patterns in filters]


def _dialog(parent, title: str, start: str, filters: list[Filter]) -> QFileDialog:
    dialog = QFileDialog(parent, title, start)
    # (Qt's own: a "native" one could go through the portal, blocking.)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
    if filters:
        dialog.setNameFilters(_qt_filters(filters))
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return dialog


def _qt(dialog: QFileDialog, on_chosen) -> None:
    _pending.add(dialog)
    dialog.fileSelected.connect(lambda chosen: chosen and on_chosen(chosen))
    dialog.finished.connect(lambda _: _pending.discard(dialog))
    dialog.open()
