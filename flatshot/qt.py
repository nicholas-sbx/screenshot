"""Qt binding shim: PySide6 when present (pip / AppImage), else PyQt6
(what most distros package). Modules import Qt names from here."""

import os

_want = os.environ.get("FLATSHOT_QT", "").lower()
_order = ["pyqt6", "pyside6"] if _want == "pyqt6" else ["pyside6", "pyqt6"]

_errors = []
for _name in _order:
    try:
        if _name == "pyside6":
            from PySide6 import __version__ as _bver
            from PySide6.QtCore import *  # noqa: F401,F403
            from PySide6.QtGui import *  # noqa: F401,F403
            from PySide6.QtWidgets import *  # noqa: F401,F403
            from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: F401
            from PySide6.QtCore import Signal, qVersion

            BINDING = f"PySide6 {_bver}"
        else:
            from PyQt6.QtCore import *  # noqa: F401,F403
            from PyQt6.QtGui import *  # noqa: F401,F403
            from PyQt6.QtWidgets import *  # noqa: F401,F403
            from PyQt6.QtNetwork import QLocalServer, QLocalSocket  # noqa: F401
            from PyQt6.QtCore import PYQT_VERSION_STR, pyqtSignal as Signal, qVersion

            BINDING = f"PyQt6 {PYQT_VERSION_STR}"
        break
    except ImportError as e:
        _errors.append(f"{_name}: {e}")
else:
    raise ImportError("flatshot needs PySide6 or PyQt6 (" + "; ".join(_errors) + ")")

QT_VERSION = qVersion()


def keyval(key) -> int:
    """Qt key / enum as a plain int, whatever the binding hands back."""
    return key if isinstance(key, int) else int(getattr(key, "value", key))
