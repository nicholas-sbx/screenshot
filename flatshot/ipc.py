"""Single-instance plumbing: the tray app listens on a local socket and
other invocations (`flatshot`, `flatshot --full`, ...) hand it commands."""

import json
import os

from flatshot.qt import QLocalServer, QLocalSocket, QObject, Signal

NAME = f"flatshot-{os.getuid()}"


def send(command: str, timeout_ms: int = 1500) -> bool:
    """Deliver a command to the running tray app. False if none answers."""
    sock = QLocalSocket()
    sock.connectToServer(NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((command + "\n").encode())
    sock.flush()
    ok = sock.waitForReadyRead(timeout_ms) and bytes(sock.readAll().data()).startswith(b"ok")
    sock.disconnectFromServer()
    return ok


def query(command: str, timeout_ms: int = 1500) -> str | None:
    """Send ``command`` and return the reply after "ok" (None if no tray app answers)."""
    sock = QLocalSocket()
    sock.connectToServer(NAME)
    if not sock.waitForConnected(timeout_ms):
        return None
    sock.write((command + "\n").encode())
    sock.flush()
    reply = b""
    while sock.waitForReadyRead(timeout_ms):
        reply += bytes(sock.readAll().data())
        if reply.endswith(b"\n"):
            break
    sock.disconnectFromServer()
    text = reply.decode(errors="replace").strip()
    return text[2:].strip() if text.startswith("ok") else None


class Server(QObject):
    command = Signal(str)

    def __init__(self, status=None):
        """``status()`` returns a dict the ``status`` command replies with."""
        super().__init__()
        self._status = status
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept)

    def listen(self) -> bool:
        if send("ping", 500):
            return False  # another instance owns the socket
        QLocalServer.removeServer(NAME)  # stale socket from a crash
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        return self._server.listen(NAME)

    def _accept(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock):
        if not sock.canReadLine():
            return
        cmd = bytes(sock.readLine().data()).decode(errors="replace").strip()
        if cmd == "status":
            info = self._status() if self._status else {}
            sock.write(b"ok " + json.dumps(info).encode() + b"\n")
            sock.flush()
            return
        sock.write(b"ok\n")
        sock.flush()
        if cmd and cmd != "ping":
            self.command.emit(cmd)
