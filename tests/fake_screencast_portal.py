"""A stand-in for xdg-desktop-portal's ScreenCast interface, for testing
Flatshot's portal client without a desktop. Shares one 1280 x 800 monitor at
(0, 0) as PipeWire node 42, hands out /dev/null as the PipeWire connection,
and remembers what it was asked (org.flatshot.Test.Log returns it as JSON).
org.flatshot.Test.CancelNext makes the next Start answer "cancelled", as when
the user closes the desktop's screen-sharing dialog.
"""

import json
import os

from jeepney import DBusAddress, MessageType, new_error, new_method_return, new_signal
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

DESKTOP = "/org/freedesktop/portal/desktop"


def main():
    conn = open_dbus_connection(bus="SESSION", enable_fds=True)
    conn.send_and_get_reply(message_bus.RequestName("org.freedesktop.portal.Desktop"))
    print("fake ScreenCast portal ready", flush=True)
    log = []
    cancel_next = False
    sessions = 0
    while True:
        msg = conn.receive()
        h = msg.header
        if h.message_type != MessageType.method_call:
            continue
        iface, member = h.fields.get(2), h.fields.get(3)  # HeaderFields.interface, .member
        sender = h.fields.get(7, "")  # HeaderFields.sender

        def respond(options, code, results):
            token = options["handle_token"][1]
            path = f"{DESKTOP}/request/{sender.lstrip(':').replace('.', '_')}/{token}"
            conn.send(new_method_return(msg, "o", (path,)))
            conn.send(new_signal(DBusAddress(path, interface="org.freedesktop.portal.Request"), "Response",
                                 "ua{sv}", (code, results)))

        if iface == "org.freedesktop.DBus.Properties" and member == "Get":
            value = {"version": ("u", 5), "AvailableCursorModes": ("u", 7)}.get(msg.body[1])
            conn.send(new_method_return(msg, "v", (value,)) if value else
                      new_error(msg, "org.freedesktop.DBus.Error.UnknownProperty"))
        elif member == "CreateSession":
            sessions += 1
            log.append(["CreateSession"])
            respond(msg.body[0], 0, {"session_handle": ("s", f"{DESKTOP}/session/fake/s{sessions}")})
        elif member == "SelectSources":
            session, options = msg.body
            log.append(["SelectSources", {k: v[1] for k, v in options.items() if k != "handle_token"}])
            respond(options, 0, {})
        elif member == "Start":
            session, parent, options = msg.body
            log.append(["Start"])
            if cancel_next:
                cancel_next = False
                respond(options, 1, {})
                continue
            stream = (42, {"position": ("(ii)", (0, 0)), "size": ("(ii)", (1280, 800)), "source_type": ("u", 1)})
            respond(options, 0, {"streams": ("a(ua{sv})", [stream]),
                                 "restore_token": ("s", f"token-{sessions}")})
        elif member == "OpenPipeWireRemote":
            log.append(["OpenPipeWireRemote"])
            fd = os.open("/dev/null", os.O_RDWR)
            conn.send(new_method_return(msg, "h", (fd,)))
            os.close(fd)
        elif member == "Close":
            log.append(["Close", h.fields.get(1)])  # HeaderFields.path
            conn.send(new_method_return(msg))
        elif member == "CancelNext":
            cancel_next = True
            conn.send(new_method_return(msg))
        elif member == "Log":
            conn.send(new_method_return(msg, "s", (json.dumps(log),)))
        else:
            conn.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod"))


if __name__ == "__main__":
    main()
