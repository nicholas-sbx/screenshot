"""`flatshot --diagnose`: what this Flatshot and the running tray app are
using, and why the overlay is (or isn't) a layer-shell surface."""

import os
import sys
from pathlib import Path

from flatshot import __version__, capture, ipc, layershell, qt
from flatshot.qt import QGuiApplication


def info() -> dict:
    layer = layershell.available()
    return {
        "version": __version__,
        "pid": os.getpid(),
        "code": str(Path(__file__).resolve().parent),
        "python": sys.executable,
        "Qt binding": qt.BINDING,
        "Qt": qt.QT_VERSION,
        "platform": QGuiApplication.platformName(),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
        "layer-shell overlay": f"yes (scope {layershell.SCOPE})" if layer else f"no: {layershell.status}",
        "last overlay": layershell.last_overlay,
        "KWin capture helper": capture.kwin_helper() or "not installed",
        "screen recorder": _recorder(),
    }


def _recorder() -> str:
    from flatshot import screencast

    how = screencast.method()
    problem = screencast.problem()
    return screencast.METHOD_LABELS.get(how, how) + (f" (not usable: {problem})" if problem else "")


def _lines(data: dict) -> list[str]:
    width = max(len(k) for k in data)
    return [f"  {k + ':':<{width + 1}} {v}" for k, v in data.items()]


def report() -> str:
    out = ["This flatshot:"] + _lines(info())
    reply = ipc.query("status")
    if reply is None:
        out.append("Tray app: not running.")
    elif not reply:
        out += ["Tray app: running, but an older version than this one, so captures go to it.",
                "  Restart it: flatshot --quit, then start Flatshot again."]
    else:
        import json

        try:
            out += ["Tray app (handles shortcuts and `flatshot` captures):"] + _lines(json.loads(reply))
        except ValueError:
            out.append(f"Tray app: unreadable reply {reply!r}")
    return "\n".join(out)
