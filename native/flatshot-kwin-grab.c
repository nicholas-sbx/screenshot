/*
 * flatshot-kwin-grab — capture the whole desktop through KWin's
 * org.kde.KWin.ScreenShot2 D-Bus API and write it raw to stdout.
 *
 * KWin only answers callers whose executable is named, with
 * X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2, in an
 * installed .desktop file. A Python program can't qualify (its executable
 * is python), so this tiny helper does the call and Flatshot reads its
 * output. It skips Spectacle's startup and the PNG round trip.
 *
 * Usage: flatshot-kwin-grab [--cursor]   (--cursor draws the mouse pointer in)
 *
 * Output: one ASCII line "FLATSHOT-RAW <width> <height> <stride> <qimage-format>\n"
 * followed by stride * height bytes of pixel data.
 *
 * Build: cc -O2 -o flatshot-kwin-grab flatshot-kwin-grab.c $(pkg-config --cflags --libs dbus-1)
 */
#include <dbus/dbus.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        puts("flatshot-kwin-grab 2");
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

    int fds[2];
    if (pipe(fds) != 0) {
        perror("flatshot-kwin-grab: pipe");
        return 2;
    }

    DBusMessage *msg = dbus_message_new_method_call(SERVICE, PATH, IFACE, "CaptureWorkspace");
    DBusMessageIter args, dict;
    dbus_message_iter_init_append(msg, &args);
    dbus_message_iter_open_container(&args, DBUS_TYPE_ARRAY, "{sv}", &dict);
    add_bool(&dict, "native-resolution", TRUE);
    add_bool(&dict, "include-cursor", cursor);
    dbus_message_iter_close_container(&args, &dict);
    dbus_message_iter_append_basic(&args, DBUS_TYPE_UNIX_FD, &fds[1]);

    /* KWin replies first and writes the pixels from another thread. */
    DBusMessage *reply = dbus_connection_send_with_reply_and_block(conn, msg, 10000, &err);
    dbus_message_unref(msg);
    close(fds[1]); /* the message held its own copy; EOF comes when KWin closes its end */
    if (!reply) {
        fprintf(stderr, "flatshot-kwin-grab: %s: %s\n", err.name, err.message);
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
        fprintf(stderr, "flatshot-kwin-grab: unexpected reply (%llu x %llu, stride %llu)\n",
                (unsigned long long)width, (unsigned long long)height, (unsigned long long)stride);
        return 4;
    }

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
