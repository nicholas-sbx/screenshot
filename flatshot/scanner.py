"""Find QR codes and barcodes in a capture, off the UI thread.

Two backends, tried in order:
  * zxing-cpp (Python module ``zxingcpp``): QR, Data Matrix, Aztec, PDF417,
    all common 1D codes.
  * zbar (C library ``libzbar.so.0``, loaded with ctypes, so no Python
    package is needed): QR and the common 1D codes.
Without either, scanning is skipped. FLATSHOT_SCANNER=zxing|zbar forces one.
"""

import ctypes
import ctypes.util
import os
import threading
from dataclasses import dataclass

from flatshot.qt import QImage, QObject, QPointF, Signal

try:
    import zxingcpp

    # Need zxing-cpp >= 2.2 (TextMode arrived there): older bindings either
    # can't take raw buffers or, like Debian 12's, abort on some barcodes.
    # Those fall back to zbar.
    if not hasattr(zxingcpp, "TextMode"):
        zxingcpp = None
except ImportError:  # optional dependency
    zxingcpp = None

# Payloads worth handing to the default handler with "Open".
LINK_PREFIXES = ("http://", "https://", "mailto:", "tel:", "sms:", "geo:", "matrix:")


@dataclass
class Code:
    text: str
    kind: str
    corners: list[QPointF]  # physical pixels in the scanned image

    @property
    def is_link(self) -> bool:
        return self.text.strip().lower().startswith(LINK_PREFIXES)


def _gray(image: QImage) -> tuple[QImage, memoryview]:
    """8-bit grayscale copy and a (height, bytesPerLine) view of its pixels."""
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    bits = gray.constBits()
    if hasattr(bits, "setsize"):  # PyQt6 hands back a sip.voidptr
        bits.setsize(gray.sizeInBytes())
    return gray, memoryview(bits).cast("B", (gray.height(), gray.bytesPerLine()))


# -- zxing-cpp ----------------------------------------------------------------

def _scan_zxing(image: QImage) -> list[Code]:
    gray, view = _gray(image)  # keep `gray` alive while `view` is used
    codes = []
    # Rows are padded to bytesPerLine; the extra columns are harmless blank pixels.
    for result in zxingcpp.read_barcodes(view):
        if not getattr(result, "valid", True) or not result.text:
            continue
        pos = result.position
        corners = [QPointF(pt.x, pt.y) for pt in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)]
        kind = str(result.format).replace("BarcodeFormat.", "")
        codes.append(Code(result.text, kind, corners))
    return codes


# -- zbar -----------------------------------------------------------------------

_zbar = None
_zbar_tried = False
_zbar_lock = threading.Lock()  # one zbar scan at a time
_load_lock = threading.Lock()  # the UI and scan threads may both ask first


def _load_zbar():
    with _load_lock:
        return _load_zbar_locked()


def _load_zbar_locked():
    global _zbar, _zbar_tried
    if _zbar_tried:
        return _zbar
    _zbar_tried = True
    for name in ("libzbar.so.0", ctypes.util.find_library("zbar")):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        vp, u, i, c = ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_char_p
        for fn, res, args in [
            ("zbar_image_scanner_create", vp, []),
            ("zbar_image_scanner_destroy", None, [vp]),
            ("zbar_image_scanner_set_config", i, [vp, i, i, i]),
            ("zbar_image_create", vp, []),
            ("zbar_image_destroy", None, [vp]),
            ("zbar_image_set_format", None, [vp, ctypes.c_ulong]),
            ("zbar_image_set_size", None, [vp, u, u]),
            ("zbar_image_set_data", None, [vp, vp, ctypes.c_ulong, vp]),
            ("zbar_scan_image", i, [vp, vp]),
            ("zbar_image_first_symbol", vp, [vp]),
            ("zbar_symbol_next", vp, [vp]),
            ("zbar_symbol_get_type", i, [vp]),
            ("zbar_symbol_get_data", vp, [vp]),
            ("zbar_symbol_get_data_length", u, [vp]),
            ("zbar_symbol_get_loc_size", u, [vp]),
            ("zbar_symbol_get_loc_x", i, [vp, u]),
            ("zbar_symbol_get_loc_y", i, [vp, u]),
            ("zbar_get_symbol_name", c, [i]),
        ]:
            f = getattr(lib, fn)
            f.restype, f.argtypes = res, args
        _zbar = lib
        break
    return _zbar


def _fourcc(code: str) -> int:
    a, b, c, d = (ord(ch) for ch in code)
    return a | (b << 8) | (c << 16) | (d << 24)


def _scan_zbar(image: QImage) -> list[Code]:
    z = _load_zbar()
    gray, view = _gray(image)
    w, h = gray.width(), gray.height()
    # zbar wants tightly packed rows.
    raw, stride = view.tobytes(), gray.bytesPerLine()
    data = raw if stride == w else b"".join(raw[r * stride:r * stride + w] for r in range(h))
    buf = ctypes.create_string_buffer(data, len(data))
    codes = []
    with _zbar_lock:
        scanner = z.zbar_image_scanner_create()
        zimg = z.zbar_image_create()
        try:
            z.zbar_image_scanner_set_config(scanner, 0, 0, 1)  # ZBAR_NONE, ZBAR_CFG_ENABLE, on
            z.zbar_image_set_format(zimg, _fourcc("Y800"))
            z.zbar_image_set_size(zimg, w, h)
            z.zbar_image_set_data(zimg, ctypes.cast(buf, ctypes.c_void_p), len(data), None)
            if z.zbar_scan_image(scanner, zimg) <= 0:
                return []
            sym = z.zbar_image_first_symbol(zimg)
            while sym:
                n = z.zbar_symbol_get_data_length(sym)
                text = ctypes.string_at(z.zbar_symbol_get_data(sym), n).decode("utf-8", errors="replace")
                pts = [(z.zbar_symbol_get_loc_x(sym, k), z.zbar_symbol_get_loc_y(sym, k))
                       for k in range(z.zbar_symbol_get_loc_size(sym))]
                if text and pts:
                    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                    if x1 - x0 < 4:  # 1D codes report a scan line; give the box some body
                        x0, x1 = x0 - 6, x1 + 6
                    if y1 - y0 < 4:
                        y0, y1 = y0 - 6, y1 + 6
                    corners = [QPointF(x0, y0), QPointF(x1, y0), QPointF(x1, y1), QPointF(x0, y1)]
                    kind = (z.zbar_get_symbol_name(z.zbar_symbol_get_type(sym)) or b"Code").decode()
                    codes.append(Code(text, kind.replace("-", ""), corners))
                sym = z.zbar_symbol_next(sym)
        finally:
            # zbar_image_set_data was given no cleanup handler, so `buf` stays ours.
            z.zbar_image_destroy(zimg)
            z.zbar_image_scanner_destroy(scanner)
    return codes


# -- public API -----------------------------------------------------------------

def backend() -> str | None:
    """Which scanner will be used: "zxing", "zbar", or None."""
    forced = os.environ.get("FLATSHOT_SCANNER", "").lower()
    if forced in ("", "zxing") and zxingcpp is not None:
        return "zxing"
    if forced in ("", "zbar") and _load_zbar() is not None:
        return "zbar"
    return None


def available() -> bool:
    return backend() is not None


_scan_lock = threading.Lock()


def scan(image: QImage) -> list[Code]:
    which = backend()
    if which is None or image.isNull():
        return []
    # One scan at a time: some zxing-cpp builds import modules lazily on the
    # first call, and two first calls in parallel deadlock in the import lock.
    with _scan_lock:
        return _scan_zxing(image) if which == "zxing" else _scan_zbar(image)


class Scanner(QObject):
    finished = Signal(list)

    def start(self, image: QImage) -> None:
        image = image.copy()  # own a copy across the thread boundary
        threading.Thread(target=lambda: self.finished.emit(scan(image)), daemon=True).start()
