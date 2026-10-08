"""Open the capture overlay as a wlr-layer-shell overlay surface, the way
Spectacle does, so KWin shows and hides it instantly instead of running
window open/close animations — and it sits above panels and popups.

Uses KDE's LayerShellQt library through ctypes. That library is built
against the system Qt, so this only runs when Flatshot itself is on the
system Qt (the distro packages, via PyQt6); the AppImage bundles its own Qt
and keeps ordinary fullscreen windows. On by default on KDE Plasma;
FLATSHOT_LAYER_SHELL=0 disables it, =1 forces it on other compositors.
"""

import ctypes
import ctypes.util
import os

from flatshot import qt
from flatshot.qt import QGuiApplication, QObject

LAYER_OVERLAY = 3
ANCHOR_ALL = 1 | 2 | 4 | 8
KEYBOARD_EXCLUSIVE = 1

_lib = None
_tried = False
status = "not tried"  # why the last apply() did or didn't use layer-shell


_load_error = ""


def _load():
    global _lib, _tried, _load_error
    if _tried:
        return _lib
    _tried = True
    for name in ("liblayershellqtinterface.so.6", ctypes.util.find_library("layershellqtinterface")):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            lib._ZN12LayerShellQt6Window3getEP7QWindow.restype = ctypes.c_void_p
            lib._ZN12LayerShellQt6Window3getEP7QWindow.argtypes = [ctypes.c_void_p]
            _lib = lib
            break
        except (OSError, AttributeError) as e:
            _load_error = str(e)
            continue
    return _lib


def available() -> bool:
    global status
    mode = os.environ.get("FLATSHOT_LAYER_SHELL", "auto")
    if mode == "0":
        status = "disabled by FLATSHOT_LAYER_SHELL=0"
        return False
    if not QGuiApplication.platformName().startswith("wayland"):
        status = f"platform is {QGuiApplication.platformName()}, not wayland"
        return False
    # The compositor must speak wlr-layer-shell or the window never maps.
    # KWin does; elsewhere (GNOME, Weston) it may not, so only opt in on KDE
    # unless forced with FLATSHOT_LAYER_SHELL=1.
    if mode != "1" and "kde" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
        status = "not KDE (set FLATSHOT_LAYER_SHELL=1 to force)"
        return False
    # Only safe when we share the library's (system) Qt.
    if not qt.BINDING.startswith("PyQt6"):
        status = f"{qt.BINDING} bundles its own Qt"
        return False
    if _load() is None:
        status = f"LayerShellQt library not loadable ({_load_error})"
        return False
    return True


def _set(obj: QObject, ptr: int, prop: str, value, symbol: str | None = None, ctype=ctypes.c_int) -> None:
    if obj.metaObject().indexOfProperty(prop) >= 0 and obj.setProperty(prop, value):
        return
    if symbol:  # fall back to calling the C++ setter directly
        fn = getattr(_lib, symbol)
        fn.argtypes = [ctypes.c_void_p, ctype]
        fn.restype = None
        fn(ptr, value)


def apply(widget, screen) -> bool:
    """Make ``widget``'s window a fullscreen overlay layer on ``screen``.
    Must run before the widget is first shown."""
    global status
    if not available():
        return False
    try:
        from PyQt6 import sip
    except ImportError as e:
        status = f"PyQt6.sip missing: {e}"
        return False

    widget.create()
    handle = widget.windowHandle()
    if handle is None:
        status = "widget has no QWindow"
        return False
    handle.setScreen(screen)
    ptr = _lib._ZN12LayerShellQt6Window3getEP7QWindow(sip.unwrapinstance(handle))
    if not ptr:
        status = "LayerShellQt::Window::get returned null"
        return False
    obj = sip.wrapinstance(ptr, QObject)
    _set(obj, ptr, "layer", LAYER_OVERLAY, "_ZN12LayerShellQt6Window8setLayerENS0_5LayerE")
    _set(obj, ptr, "anchors", ANCHOR_ALL, "_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE")
    _set(obj, ptr, "exclusionZone", -1, "_ZN12LayerShellQt6Window16setExclusiveZoneEi")
    _set(obj, ptr, "keyboardInteractivity", KEYBOARD_EXCLUSIVE,
         "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE")
    obj.setProperty("scope", "flatshot")
    obj.setProperty("activateOnShow", True)
    if obj.metaObject().indexOfProperty("screen") >= 0:
        obj.setProperty("screen", screen)
    widget._layer_shell = obj  # keep the wrapper alive with the widget
    status = "layer-shell overlay"
    return True
