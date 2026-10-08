"""Session-bus access through jeepney (pure Python), so argument types are
exact regardless of the Qt binding. Optional: without jeepney, KDE
shortcuts are unavailable and notifications fall back to notify-send."""

import socket
import threading

from flatshot.qt import QObject, Signal

try:
    from jeepney import DBusAddress, MatchRule, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse, unwrap_msg
except ImportError:  # optional dependency
    open_dbus_connection = None

    class DBusErrorResponse(Exception):
        pass


class DBusError(RuntimeError):
    pass


def available() -> bool:
    return open_dbus_connection is not None


_conn = None
_lock = threading.Lock()


def _connection():
    global _conn
    if open_dbus_connection is None:
        raise DBusError("jeepney is not installed")
    if _conn is None:
        try:
            _conn = open_dbus_connection(bus="SESSION")
        except Exception as e:  # noqa: BLE001 — no session bus, bad address, ...
            raise DBusError(f"no session bus: {e}") from e
    return _conn


def call(dest: str, path: str, iface: str, method: str, sig: str | None = None, *args, timeout: float = 3.0):
    """Synchronous method call; returns the reply body tuple."""
    msg = new_method_call(DBusAddress(path, bus_name=dest, interface=iface), method, sig, args)
    with _lock:
        try:
            return unwrap_msg(_connection().send_and_get_reply(msg, timeout=timeout))
        except DBusErrorResponse as e:
            raise DBusError(f"{method}: {e.name}: {' '.join(map(str, e.data))}") from e
        except DBusError:
            raise
        except Exception as e:  # noqa: BLE001 — timeouts, broken connection
            raise DBusError(f"{method}: {e!r}") from e


def has_owner(name: str) -> bool:
    try:
        return bool(call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                         "NameHasOwner", "s", name)[0])
    except DBusError:
        return False


class SignalListener(QObject):
    """Receives bus signals (matching ``rules``) and method calls addressed
    to this connection on a private connection in a background thread, and
    re-emits them on the Qt main thread as (interface, member, body).
    Method calls are answered with an empty reply."""

    received = Signal(str, str, tuple)

    def __init__(self, rules: list[dict]):
        super().__init__()
        self.rules = rules
        self.unique_name = ""
        self._conn = None

    def start(self) -> bool:
        if open_dbus_connection is None:
            return False
        try:
            conn = open_dbus_connection(bus="SESSION")
            for rule in self.rules:
                conn.send_and_get_reply(message_bus.AddMatch(MatchRule(type="signal", **rule)), timeout=3)
        except Exception:  # noqa: BLE001
            return False
        self._conn = conn
        self.unique_name = conn.unique_name
        threading.Thread(target=self._loop, args=(conn,), daemon=True).start()
        return True

    def stop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)  # unblocks receive() in the thread
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _loop(self, conn):
        from jeepney import HeaderFields, MessageType, new_method_return

        while True:
            try:
                msg = conn.receive()
            except Exception:  # noqa: BLE001 — bus went away or stop()
                return
            kind = msg.header.message_type
            if kind not in (MessageType.signal, MessageType.method_call):
                continue
            if kind == MessageType.method_call:
                try:
                    conn.send(new_method_return(msg))
                except Exception:  # noqa: BLE001
                    pass
            fields = msg.header.fields
            self.received.emit(fields.get(HeaderFields.interface, ""), fields.get(HeaderFields.member, ""),
                               tuple(msg.body))
