/*
 * flatshot-kwin-grab — capture the whole desktop through KWin's
 * org.kde.KWin.ScreenShot2 D-Bus API.
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

/*
 * Ask KWin for the whole workspace. KWin replies with the picture's size
 * and then writes its pixels into ``fd`` from a thread of its own, closing
 * it when done. Returns 0 with info = width, height, stride, format; else
 * an exit code, with the reason in ``why``.
 */
static int capture(DBusConnection *conn, dbus_bool_t cursor, int fd, uint64_t info[4], char *why, size_t why_len)
{
    DBusError err;
    dbus_error_init(&err);
    DBusMessage *msg = dbus_message_new_method_call(SERVICE, PATH, IFACE, "CaptureWorkspace");
    DBusMessageIter args, dict;
    dbus_message_iter_init_append(msg, &args);
    dbus_message_iter_open_container(&args, DBUS_TYPE_ARRAY, "{sv}", &dict);
    add_bool(&dict, "native-resolution", TRUE);
    add_bool(&dict, "include-cursor", cursor);
    dbus_message_iter_close_container(&args, &dict);
    dbus_message_iter_append_basic(&args, DBUS_TYPE_UNIX_FD, &fd);

    DBusMessage *reply = dbus_connection_send_with_reply_and_block(conn, msg, 10000, &err);
    dbus_message_unref(msg); /* (it held its own copy of fd) */
    if (!reply) {
        snprintf(why, why_len, "%s: %s", err.name, err.message);
        dbus_error_free(&err);
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

/*
 * --serve: stay connected to the bus and capture on request, so a capture
 * costs neither starting a process nor connecting to D-Bus. fd 0 is a
 * SOCK_SEQPACKET socket from Flatshot. It says "FLATSHOT-SERVE 3"; then
 * each request is "grab" or "grab cursor" with the write end of a pipe
 * attached (SCM_RIGHTS), handed straight to KWin, so the pixels go from
 * KWin to Flatshot without passing through here. The answer is
 * "FLATSHOT-RAW <width> <height> <stride> <format>" (the pixels follow in
 * the pipe) or "ERR <why>". Ends when Flatshot closes the socket.
 */
static int serve(DBusConnection *conn)
{
    const int sock = 0;
    const char hello[] = "FLATSHOT-SERVE 3\n";
    if (send(sock, hello, sizeof hello - 1, MSG_NOSIGNAL) < 0) {
        perror("flatshot-kwin-grab: --serve needs a socket on stdin");
        return 8;
    }
    for (;;) {
        char buf[64];
        union { char bytes[CMSG_SPACE(sizeof(int))]; struct cmsghdr align; } ctrl;
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
        int fd = -1;
        for (struct cmsghdr *c = CMSG_FIRSTHDR(&mh); c; c = CMSG_NXTHDR(&mh, c))
            if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS && c->cmsg_len >= CMSG_LEN(sizeof(int)))
                memcpy(&fd, CMSG_DATA(c), sizeof fd);
        char out[512], why[400];
        uint64_t info[4];
        if (fd < 0 || strncmp(buf, "grab", 4) != 0) {
            snprintf(out, sizeof out, "ERR bad request\n");
        } else if (capture(conn, strstr(buf, "cursor") != NULL, fd, info, why, sizeof why) == 0) {
            snprintf(out, sizeof out, "FLATSHOT-RAW %llu %llu %llu %llu\n", (unsigned long long)info[0],
                     (unsigned long long)info[1], (unsigned long long)info[2], (unsigned long long)info[3]);
        } else {
            snprintf(out, sizeof out, "ERR %s\n", why);
        }
        if (fd >= 0)
            close(fd); /* EOF comes when KWin closes its copy */
        if (send(sock, out, strlen(out), MSG_NOSIGNAL) < 0)
            return 0;
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        puts("flatshot-kwin-grab 3");
        return 0;
    }
    dbus_bool_t cursor = argc > 1 && strcmp(argv[1], "--cursor") == 0;

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
    int failed = capture(conn, cursor, fds[1], info, why, sizeof why);
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
