"""A stand-in for KWin's org.kde.KWin.ScreenShot2 service, for testing
flatshot-kwin-grab without KWin. Answers CaptureWorkspace like KWin does:
reply with the image metadata, then write raw pixels into the given pipe
from another thread.

The image is 1500 x 900, QImage::Format_RGB32 (4), stride 6000; pixel
(x, y) is the colour 0xFF000000 | (x % 256) << 16 | (y % 256) << 8 | 0x5A.

CaptureScreen answers for the screens in $FAKE_KWIN_SCREENS ("A:800x600,
B:960x720": name and native size), in ARGB32 (5): the i-th screen's pixel
(x, y) is 0xFF000000 | (x % 256) << 16 | (y % 256) << 8 | (0x10 * (i + 1)),
plus 0x80 in the low byte when the pointer was asked for.

CaptureActiveWindow answers with a 300 x 200 window, plus 20 px each side
for its frame (include-decoration) and 30 px each side for its shadow
(include-shadow), in ARGB32_Premultiplied (6): the window's pixels are
0xFF3366AA, its frame 0xFF202020, and the shadow transparent (0).
"""

import os
import sys
import threading

from jeepney import MessageType, new_error, new_method_return
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

W, H = 1500, 900
STRIDE = W * 4


def pixels() -> bytes:
    rows = []
    for y in range(H):
        row = bytearray(STRIDE)
        for x in range(W):
            # little-endian 0xAARRGGBB
            row[x * 4:x * 4 + 4] = bytes((0x5A, y % 256, x % 256, 0xFF))
        rows.append(bytes(row))
    return b"".join(rows)


def screen_pixels(w: int, h: int, tag: int) -> bytes:
    row = bytearray(w * 4)
    for x in range(w):
        row[x * 4:x * 4 + 4] = bytes((tag, 0, x % 256, 0xFF))
    rows = []
    for y in range(h):
        row[1::4] = bytes([y % 256]) * w
        rows.append(bytes(row))
    return b"".join(rows)


def window_pixels(decoration: bool, shadow: bool) -> tuple[int, int, bytes]:
    frame, margin = 20 * decoration, 30 * shadow
    w, h = 300 + 2 * (frame + margin), 200 + 2 * (frame + margin)
    out = bytearray(w * h * 4)
    for y in range(margin, h - margin):
        for x in range(margin, w - margin):
            inside = margin + frame <= x < w - margin - frame and margin + frame <= y < h - margin - frame
            out[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((0xAA, 0x66, 0x33, 0xFF) if inside
                                                            else (0x20, 0x20, 0x20, 0xFF))
    return w, h, bytes(out)


def main():
    data = pixels()
    screens = {}
    for i, spec in enumerate(filter(None, os.environ.get("FAKE_KWIN_SCREENS", "").split(","))):
        name, size = spec.split(":")
        w, h = (int(v) for v in size.split("x"))
        screens[name] = (w, h, {c: screen_pixels(w, h, 0x10 * (i + 1) + (0x80 if c else 0)) for c in (False, True)})
    conn = open_dbus_connection(bus="SESSION", enable_fds=True)
    conn.send_and_get_reply(message_bus.RequestName("org.kde.KWin"))
    print("fake KWin ScreenShot2 ready", flush=True)
    while True:
        msg = conn.receive()
        h = msg.header
        if h.message_type != MessageType.method_call:
            continue
        member = h.fields.get(3)  # HeaderFields.member
        if member == "CaptureScreen" and msg.body[0] in screens:
            name, options, fd = msg.body
            w, h_, pictures = screens[name]
            picture = pictures[options.get("include-cursor", ("b", False))[1]]
            results = {"type": ("s", "raw"), "width": ("u", w), "height": ("u", h_),
                       "stride": ("u", w * 4), "format": ("u", 5), "scale": ("d", 1.0)}
        elif member == "CaptureActiveWindow":
            options, fd = msg.body
            w, h_, picture = window_pixels(options.get("include-decoration", ("b", False))[1],
                                           options.get("include-shadow", ("b", False))[1])
            results = {"type": ("s", "raw"), "width": ("u", w), "height": ("u", h_),
                       "stride": ("u", w * 4), "format": ("u", 6), "scale": ("d", 1.0)}
        elif member == "CaptureWorkspace":
            options, fd = msg.body
            picture = data
            results = {"type": ("s", "raw"), "width": ("u", W), "height": ("u", H),
                       "stride": ("u", STRIDE), "format": ("u", 4), "scale": ("d", 1.0)}
        else:
            conn.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod"))
            continue
        assert options.get("native-resolution") == ("b", True), options
        conn.send(new_method_return(msg, "a{sv}", (results,)))
        out = fd.to_file("wb")

        def write(out=out, picture=picture):
            with out:
                out.write(picture)

        threading.Thread(target=write, daemon=True).start()
        if "--once" in sys.argv:
            threading.Event().wait(2)
            return


if __name__ == "__main__":
    main()
