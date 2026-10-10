/*
 * flatshot-kwin-grab — capture the whole desktop, a screen or the active
 * window through KWin's org.kde.KWin.ScreenShot2 D-Bus API.
 *
 * KWin only answers callers whose executable is named, with
 * X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2, in an
 * installed .desktop file. A Python program can't qualify (its executable
 * is python), so this tiny helper does the call. It skips Spectacle's
 * startup and the PNG round trip.
 *
 * Usage:
 *   flatshot-kwin-grab [--cursor]   one capture, written raw to stdout
 *                                   (--cursor draws the mouse pointer in)
 *   flatshot-kwin-grab --window [--decoration] [--shadow] [--cursor]
 *                                   the active window on its own, with its
 *                                   title bar and borders, and its shadow,
 *                                   if asked (transparent around it)
 *   flatshot-kwin-grab --serve      stays running for Flatshot: see serve()
 *   flatshot-kwin-grab --version
 *
 * One-capture output: one ASCII line
 * "FLATSHOT-RAW <width> <height> <stride> <qimage-format>\n" followed by
 * stride * height bytes of pixel data.
 *
 * Build: cc -O2 -o flatshot-kwin-grab flatshot-kwin-grab.c $(pkg-config --cflags --libs dbus-1)
 */
#include <dbus/dbus.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define SERVICE "org.kde.KWin"
#define PATH "/org/kde/KWin/ScreenShot2"
#define IFACE "org.kde.KWin.ScreenShot2"

static void add_bool(DBusMessageIter *dict, const char *key, dbus_bool_t value)
{
    DBusMessageIter entry, variant;
    dbus_message_iter_open_container(dict, DBUS_TYPE_DICT_ENTRY, NULL, &entry);
    dbus_message_iter_append_basic(&entry, DBUS_TYPE_STRING, &key);
    dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, DBUS_TYPE_BOOLEAN_AS_STRING, &variant);
    dbus_message_iter_append_basic(&variant, DBUS_TYPE_BOOLEAN, &value);
    dbus_message_iter_close_container(&entry, &variant);
    dbus_message_iter_close_container(dict, &entry);
}

/* Read an unsigned integer out of a variant, whatever integer type KWin used. */
static int variant_uint(DBusMessageIter *variant, uint64_t *out)
{
    switch (dbus_message_iter_get_arg_type(variant)) {
    case DBUS_TYPE_UINT32: { dbus_uint32_t v; dbus_message_iter_get_basic(variant, &v); *out = v; return 1; }
    case DBUS_TYPE_INT32:  { dbus_int32_t v;  dbus_message_iter_get_basic(variant, &v); *out = (uint64_t)v; return 1; }
    case DBUS_TYPE_UINT64: { dbus_uint64_t v; dbus_message_iter_get_basic(variant, &v); *out = v; return 1; }
    case DBUS_TYPE_INT64:  { dbus_int64_t v;  dbus_message_iter_get_basic(variant, &v); *out = (uint64_t)v; return 1; }
    default: return 0;
    }
}

/* What to include, for capture_send(). */
#define WITH_CURSOR 1
#define WITH_DECORATION 2 /* (the active window only) */
#define WITH_SHADOW 4 /* (the active window only) */
#define ACTIVE_WINDOW ((const char *)1) /* capture_send()'s ``screen`` for the active window */

/*
 * Ask KWin for the whole workspace, for one screen (``screen``: its name)
 * at that screen's own scale, or for the active window (``screen`` is
 * ACTIVE_WINDOW), without waiting for the answer. KWin replies with the
 * picture's size and then writes its pixels into ``fd`` from a thread of
 * its own, closing it when done. NULL on failure, with the reason in
 * ``why``.
 */
static DBusPendingCall *capture_send(DBusConnection *conn, int with, const char *screen, int fd, char *why,
                                     size_t why_len)
{
    const char *method = screen == ACTIVE_WINDOW ? "CaptureActiveWindow" : screen ? "CaptureScreen" : "CaptureWorkspace";
    DBusMessage *msg = dbus_message_new_method_call(SERVICE, PATH, IFACE, method);
    DBusMessageIter args, dict;
    dbus_message_iter_init_append(msg, &args);
    if (screen && screen != ACTIVE_WINDOW)
        dbus_message_iter_append_basic(&args, DBUS_TYPE_STRING, &screen);
    dbus_message_iter_open_container(&args, DBUS_TYPE_ARRAY, "{sv}", &dict);
    add_bool(&dict, "native-resolution", TRUE);
    add_bool(&dict, "include-cursor", (with & WITH_CURSOR) != 0);
    if (screen == ACTIVE_WINDOW) {
        add_bool(&dict, "include-decoration", (with & WITH_DECORATION) != 0);
        add_bool(&dict, "include-shadow", (with & WITH_SHADOW) != 0);
    }
    dbus_message_iter_close_container(&args, &dict);
    dbus_message_iter_append_basic(&args, DBUS_TYPE_UNIX_FD, &fd);
    DBusPendingCall *pending = NULL;
    if (!dbus_connection_send_with_reply(conn, msg, &pending, 10000) || !pending) {
        snprintf(why, why_len, "could not send the request to KWin");
        pending = NULL;
    }
    dbus_message_unref(msg); /* (it held its own copy of fd) */
    return pending;
}

/* Wait for KWin's answer to capture_send(). Returns 0 with info = width,
 * height, stride, format; else an exit code, with the reason in ``why``. */
static int capture_finish(DBusPendingCall *pending, uint64_t info[4], char *why, size_t why_len)
{
    dbus_pending_call_block(pending);
    DBusMessage *reply = dbus_pending_call_steal_reply(pending);
    dbus_pending_call_unref(pending);
    if (!reply) {
        snprintf(why, why_len, "no reply from KWin");
        return 3;
    }
    if (dbus_message_get_type(reply) == DBUS_MESSAGE_TYPE_ERROR) {
        const char *text = "";
        dbus_message_get_args(reply, NULL, DBUS_TYPE_STRING, &text, DBUS_TYPE_INVALID);
        snprintf(why, why_len, "%s: %s", dbus_message_get_error_name(reply), text);
        dbus_message_unref(reply);
        return 3;
    }

    uint64_t width = 0, height = 0, stride = 0, format = 0;
    DBusMessageIter it, results;
    if (dbus_message_iter_init(reply, &it) && dbus_message_iter_get_arg_type(&it) == DBUS_TYPE_ARRAY) {
        dbus_message_iter_recurse(&it, &results);
        while (dbus_message_iter_get_arg_type(&results) == DBUS_TYPE_DICT_ENTRY) {
            DBusMessageIter entry, variant;
            const char *key = NULL;
            dbus_message_iter_recurse(&results, &entry);
            dbus_message_iter_get_basic(&entry, &key);
            dbus_message_iter_next(&entry);
            dbus_message_iter_recurse(&entry, &variant);
            if (strcmp(key, "width") == 0) variant_uint(&variant, &width);
            else if (strcmp(key, "height") == 0) variant_uint(&variant, &height);
            else if (strcmp(key, "stride") == 0) variant_uint(&variant, &stride);
            else if (strcmp(key, "format") == 0) variant_uint(&variant, &format);
            dbus_message_iter_next(&results);
        }
    }
    dbus_message_unref(reply);
    if (!width || !height || !stride || stride < width || height > 65536 || stride > 4 * 65536) {
        snprintf(why, why_len, "unexpected reply (%llu x %llu, stride %llu)", (unsigned long long)width,
                 (unsigned long long)height, (unsigned long long)stride);
        return 4;
    }
    info[0] = width, info[1] = height, info[2] = stride, info[3] = format;
    return 0;
}

static int capture(DBusConnection *conn, int with, const char *what, int fd, uint64_t info[4], char *why,
                   size_t why_len)
{
    DBusPendingCall *pending = capture_send(conn, with, what, fd, why, why_len);
    return pending ? capture_finish(pending, info, why, why_len) : 3;
}

#define MAX_SCREENS 16

static void raw_line(char *out, size_t len, const uint64_t info[4])
{
    snprintf(out, len, "FLATSHOT-RAW %llu %llu %llu %llu\n", (unsigned long long)info[0], (unsigned long long)info[1],
             (unsigned long long)info[2], (unsigned long long)info[3]);
}

/*
 * --serve: stay connected to the bus and capture on request, so a capture
 * costs neither starting a process nor connecting to D-Bus. fd 0 is a
 * SOCK_SEQPACKET socket from Flatshot. It says "FLATSHOT-SERVE 5"; then
 * each request is "grab" or "grab cursor" with the write end of a pipe
 * attached (SCM_RIGHTS), handed straight to KWin, so the pixels go from
 * KWin to Flatshot without passing through here. The answer is
 * "FLATSHOT-RAW <width> <height> <stride> <format>" (the pixels follow in
 * the pipe) or "ERR <why>". Ends when Flatshot closes the socket.
 *
 * "screens" asks for several screens at once, each at its own scale: one
 * line after it per picture, "<0|1> <screen name>" (1: with the pointer),
 * and as many pipes attached, in the same order. All are asked before any
 * answer is awaited, so they cost about one frame together. The answer has
 * a line per picture, as above.
 *
 * "window", followed by any of " cursor", " decoration" and " shadow",
 * asks for the active window on its own, with one pipe, answered as
 * "grab" is.
 */
static int serve(DBusConnection *conn)
{
    const int sock = 0;
    const char hello[] = "FLATSHOT-SERVE 5\n";
    if (send(sock, hello, sizeof hello - 1, MSG_NOSIGNAL) < 0) {
        perror("flatshot-kwin-grab: --serve needs a socket on stdin");
        return 8;
    }
    for (;;) {
        char buf[2048];
        union { char bytes[CMSG_SPACE(MAX_SCREENS * sizeof(int))]; struct cmsghdr align; } ctrl;
        struct iovec iov = { buf, sizeof buf - 1 };
        struct msghdr mh;
        memset(&mh, 0, sizeof mh);
        mh.msg_iov = &iov;
        mh.msg_iovlen = 1;
        mh.msg_control = ctrl.bytes;
        mh.msg_controllen = sizeof ctrl.bytes;
        ssize_t n = recvmsg(sock, &mh, MSG_CMSG_CLOEXEC);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            return 0; /* Flatshot has gone */
        buf[n] = '\0';
        int fds[MAX_SCREENS], nfds = 0;
        for (struct cmsghdr *c = CMSG_FIRSTHDR(&mh); c; c = CMSG_NXTHDR(&mh, c))
            if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
                int n = (int)((c->cmsg_len - CMSG_LEN(0)) / sizeof(int));
                for (int i = 0; i < n && nfds < MAX_SCREENS; i++)
                    memcpy(&fds[nfds++], CMSG_DATA(c) + i * sizeof(int), sizeof(int));
            }
        int fd = nfds > 0 ? fds[0] : -1;
        char out[MAX_SCREENS * 128 + 512], why[400];
        uint64_t info[4];
        if (strncmp(buf, "screens", 7) == 0) {
            /* Ask for them all, then collect the answers. */
            DBusPendingCall *pending[MAX_SCREENS];
            char *line = strchr(buf, '\n');
            int count = 0;
            size_t used = 0;
            while (line && count < nfds) {
                line++;
                char *end = strchr(line, '\n');
                if (end)
                    *end = '\0';
                if (strlen(line) < 3)
                    break;
                why[0] = '\0';
                pending[count] = capture_send(conn, line[0] == '1' ? WITH_CURSOR : 0, line + 2, fds[count], why,
                                              sizeof why);
                count++;
                line = end;
            }
            for (int i = 0; i < count; i++) {
                char one[512];
                if (!pending[i])
                    snprintf(one, sizeof one, "ERR could not ask KWin\n");
                else if (capture_finish(pending[i], info, why, sizeof why) == 0)
                    raw_line(one, sizeof one, info);
                else
                    snprintf(one, sizeof one, "ERR %s\n", why);
                used += (size_t)snprintf(out + used, sizeof out - used, "%s", one);
            }
            if (count == 0)
                snprintf(out, sizeof out, "ERR bad request\n");
        } else if (fd < 0 || (strncmp(buf, "grab", 4) != 0 && strncmp(buf, "window", 6) != 0)) {
            snprintf(out, sizeof out, "ERR bad request\n");
        } else if (capture(conn,
                           (strstr(buf, "cursor") ? WITH_CURSOR : 0) | (strstr(buf, "decoration") ? WITH_DECORATION : 0)
                               | (strstr(buf, "shadow") ? WITH_SHADOW : 0),
                           buf[0] == 'w' ? ACTIVE_WINDOW : NULL, fd, info, why, sizeof why) == 0) {
            raw_line(out, sizeof out, info);
        } else {
            snprintf(out, sizeof out, "ERR %s\n", why);
        }
        for (int i = 0; i < nfds; i++)
            close(fds[i]); /* EOF comes when KWin closes its copies */
        if (send(sock, out, strlen(out), MSG_NOSIGNAL) < 0)
            return 0;
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        puts("flatshot-kwin-grab 5");
        return 0;
    }
    int with = 0;
    const char *what = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--cursor") == 0)
            with |= WITH_CURSOR;
        else if (strcmp(argv[i], "--decoration") == 0)
            with |= WITH_DECORATION;
        else if (strcmp(argv[i], "--shadow") == 0)
            with |= WITH_SHADOW;
        else if (strcmp(argv[i], "--window") == 0)
            what = ACTIVE_WINDOW;
    }

    DBusError err;
    dbus_error_init(&err);
    DBusConnection *conn = dbus_bus_get(DBUS_BUS_SESSION, &err);
    if (!conn) {
        fprintf(stderr, "flatshot-kwin-grab: no session bus: %s\n", err.message);
        return 2;
    }
    if (argc > 1 && strcmp(argv[1], "--serve") == 0)
        return serve(conn);

    int fds[2];
    if (pipe(fds) != 0) {
        perror("flatshot-kwin-grab: pipe");
        return 2;
    }
    char why[400];
    uint64_t info[4];
    int failed = capture(conn, with, what, fds[1], info, why, sizeof why);
    close(fds[1]); /* EOF comes when KWin closes its end */
    if (failed) {
        fprintf(stderr, "flatshot-kwin-grab: %s\n", why);
        return failed;
    }
    uint64_t width = info[0], height = info[1], stride = info[2], format = info[3];

    size_t size = (size_t)stride * (size_t)height, got = 0;
    unsigned char *pixels = malloc(size);
    if (!pixels) {
        fputs("flatshot-kwin-grab: out of memory\n", stderr);
        return 5;
    }
    while (got < size) {
        ssize_t n = read(fds[0], pixels + got, size - got);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            break;
        got += (size_t)n;
    }
    close(fds[0]);
    if (got != size) {
        fprintf(stderr, "flatshot-kwin-grab: short image data (%zu of %zu bytes)\n", got, size);
        return 6;
    }

    printf("FLATSHOT-RAW %llu %llu %llu %llu\n", (unsigned long long)width, (unsigned long long)height,
           (unsigned long long)stride, (unsigned long long)format);
    fflush(stdout);
    for (size_t off = 0; off < size;) {
        size_t n = fwrite(pixels + off, 1, size - off, stdout);
        if (n == 0) {
            perror("flatshot-kwin-grab: write");
            return 7;
        }
        off += n;
    }
    free(pixels);
    return 0;
}
