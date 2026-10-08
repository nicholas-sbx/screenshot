"""Find QR codes and barcodes in a capture, off the UI thread.

Uses zxing-cpp when it is installed; without it, scanning is skipped.
"""

import threading
from dataclasses import dataclass

from flatshot.qt import QImage, QObject, QPointF, Signal

try:
    import zxingcpp
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


def available() -> bool:
    return zxingcpp is not None


def scan(image: QImage) -> list[Code]:
    if zxingcpp is None or image.isNull():
        return []
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    stride = gray.bytesPerLine()
    # Rows are padded to `stride`; the extra columns are harmless blank pixels.
    bits = gray.constBits()
    if hasattr(bits, "setsize"):  # PyQt6 hands back a sip.voidptr
        bits.setsize(gray.sizeInBytes())
    view = memoryview(bits).cast("B", (gray.height(), stride))
    codes = []
    for result in zxingcpp.read_barcodes(view):
        if not getattr(result, "valid", True) or not result.text:
            continue
        pos = result.position
        corners = [QPointF(pt.x, pt.y) for pt in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)]
        kind = str(result.format).replace("BarcodeFormat.", "")
        codes.append(Code(result.text, kind, corners))
    return codes


class Scanner(QObject):
    finished = Signal(list)

    def start(self, image: QImage) -> None:
        image = image.copy()  # own a copy across the thread boundary
        threading.Thread(target=lambda: self.finished.emit(scan(image)), daemon=True).start()
