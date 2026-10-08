"""A stand-in for KWin's org.kde.KWin.ScreenShot2 service, for testing
flatshot-kwin-grab without KWin. Answers CaptureWorkspace like KWin does:
reply with the image metadata, then write raw pixels into the given pipe
from another thread.

The image is 1500 x 900, QImage::Format_RGB32 (4), stride 6000; pixel
(x, y) is the colour 0xFF000000 | (x % 256) << 16 | (y % 256) << 8 | 0x5A.
"""

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


def main():
    data = pixels()
    conn = open_dbus_connection(bus="SESSION", enable_fds=True)
    conn.send_and_get_reply(message_bus.RequestName("org.kde.KWin"))
    print("fake KWin ScreenShot2 ready", flush=True)
    while True:
        msg = conn.receive()
        h = msg.header
        if h.message_type != MessageType.method_call:
            continue
        member = h.fields.get(3)  # HeaderFields.member
        if member != "CaptureWorkspace":
            conn.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod"))
            continue
        options, fd = msg.body
        assert options.get("native-resolution") == ("b", True), options
        results = {"type": ("s", "raw"), "width": ("u", W), "height": ("u", H),
                   "stride": ("u", STRIDE), "format": ("u", 4), "scale": ("d", 1.0)}
        conn.send(new_method_return(msg, "a{sv}", (results,)))
        out = fd.to_file("wb")

        def write(out=out):
            with out:
                out.write(data)

        threading.Thread(target=write, daemon=True).start()
        if "--once" in sys.argv:
            threading.Event().wait(2)
            return


if __name__ == "__main__":
    main()
