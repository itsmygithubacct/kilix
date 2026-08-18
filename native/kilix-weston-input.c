/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Private, credential-checked input bridge for Kilix's Weston instance. */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <libweston/libweston.h>

/* These are exported libweston backend entry points, but intentionally not
 * part of its installed public header. Keep the declarations ABI-identical
 * to libweston/backend.h from the pinned Weston 14 source. */
void notify_axis(struct weston_seat *, const struct timespec *,
                 struct weston_pointer_axis_event *);
void notify_button(struct weston_seat *, const struct timespec *, int32_t,
                   enum wl_pointer_button_state);
void notify_key(struct weston_seat *, const struct timespec *, uint32_t,
                enum wl_keyboard_key_state, enum weston_key_state_update);
void notify_motion_absolute(struct weston_seat *, const struct timespec *,
                            struct weston_coord_global);
void notify_pointer_frame(struct weston_seat *);
void weston_seat_init(struct weston_seat *, struct weston_compositor *,
                      const char *);
void weston_seat_release(struct weston_seat *);
int weston_seat_init_pointer(struct weston_seat *);
int weston_seat_init_keyboard(struct weston_seat *, struct xkb_keymap *);

struct input_client;
struct input_bridge {
    struct weston_compositor *compositor;
    struct weston_seat seat;
    struct wl_listener destroy_listener;
    struct wl_event_source *listen_source;
    int listen_fd;
    char socket_path[sizeof(((struct sockaddr_un *)0)->sun_path)];
    struct input_client *clients;
};

struct input_client {
    struct input_bridge *bridge;
    struct input_client *next;
    struct wl_event_source *source;
    int fd;
    char buffer[4096];
    size_t used;
    double offset_x, offset_y;
    struct weston_output *output;
};

static struct weston_output *output_at(struct input_bridge *bridge,
                                       double x, double y) {
    struct weston_output *output;
    wl_list_for_each(output, &bridge->compositor->output_list, link) {
        if (x >= output->pos.c.x && y >= output->pos.c.y &&
                x < output->pos.c.x + output->width &&
                y < output->pos.c.y + output->height)
            return output;
    }
    return NULL;
}

static void close_client(struct input_client *client) {
    struct input_bridge *bridge = client->bridge;
    struct input_client **cursor = &bridge->clients;
    while (*cursor && *cursor != client) cursor = &(*cursor)->next;
    if (*cursor) *cursor = client->next;
    if (client->source) wl_event_source_remove(client->source);
    if (client->fd >= 0) close(client->fd);
    free(client);
}

static void focus_pointer_surface(struct input_bridge *bridge) {
    struct weston_pointer *pointer = weston_seat_get_pointer(&bridge->seat);
    if (pointer && pointer->focus && pointer->focus->surface)
        weston_seat_set_keyboard_focus(&bridge->seat,
                                       pointer->focus->surface);
}

static bool parse_line(struct input_client *client, const char *line) {
    struct input_bridge *bridge = client->bridge;
    char type = 0, tail = 0;
    int code = 0, state = 0;
    double first = 0.0, second = 0.0;
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) return false;

    if (sscanf(line, " %c %lf %lf %c", &type, &first, &second, &tail) == 3
            && type == 'm' && first >= 0.0 && second >= 0.0
            && first <= 65535.0 && second <= 65535.0) {
        struct weston_coord_global pos = {
            .c = weston_coord(first + client->offset_x,
                              second + client->offset_y) };
        notify_motion_absolute(&bridge->seat, &now, pos);
        focus_pointer_surface(bridge);
        notify_pointer_frame(&bridge->seat);
        return true;
    }
    if (sscanf(line, " %c %lf %lf %c", &type, &first, &second, &tail) == 3
            && type == 'o' && first >= 0.0 && second >= 0.0
            && first <= 65535.0 && second <= 65535.0) {
        client->offset_x = first;
        client->offset_y = second;
        client->output = output_at(bridge, first, second);
        return true;
    }
    if (sscanf(line, " %c %d %d %c", &type, &code, &state, &tail) != 3)
        return false;
    if (type == 'k' && code >= 0 && code <= KEY_MAX
            && (state == 0 || state == 1)) {
        focus_pointer_surface(bridge);
        notify_key(&bridge->seat, &now, (uint32_t)code,
                   state ? WL_KEYBOARD_KEY_STATE_PRESSED
                         : WL_KEYBOARD_KEY_STATE_RELEASED,
                   STATE_UPDATE_AUTOMATIC);
        return true;
    }
    if (type == 'r' && code >= 1 && code <= 60 && state == 0 &&
            client->output && client->output->current_mode) {
        client->output->current_mode->refresh = code * 1000;
        return true;
    }
    if (type == 'b' && code >= BTN_LEFT && code <= BTN_TASK
            && (state == 0 || state == 1)) {
        notify_button(&bridge->seat, &now, code,
                      state ? WL_POINTER_BUTTON_STATE_PRESSED
                            : WL_POINTER_BUTTON_STATE_RELEASED);
        focus_pointer_surface(bridge);
        notify_pointer_frame(&bridge->seat);
        return true;
    }
    if (type == 'a' && (code == 0 || code == 1)
            && state >= -1200 && state <= 1200) {
        struct weston_pointer_axis_event event = {
            .axis = (uint32_t)code,
            .value = (double)state,
            .has_discrete = true,
            .discrete = state > 0 ? 1 : (state < 0 ? -1 : 0),
        };
        notify_axis(&bridge->seat, &now, &event);
        notify_pointer_frame(&bridge->seat);
        return true;
    }
    return false;
}

static int client_ready(int fd, uint32_t mask, void *data) {
    struct input_client *client = data;
    if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) {
        close_client(client);
        return 0;
    }
    for (;;) {
        ssize_t count = read(fd, client->buffer + client->used,
                             sizeof(client->buffer) - client->used);
        if (count > 0) client->used += (size_t)count;
        else if (count == 0) { close_client(client); return 0; }
        else if (errno == EINTR) continue;
        else if (errno == EAGAIN || errno == EWOULDBLOCK) break;
        else { close_client(client); return 0; }
        if (client->used == sizeof(client->buffer)) {
            close_client(client);
            return 0;
        }
    }
    size_t consumed = 0;
    while (consumed < client->used) {
        char *newline = memchr(client->buffer + consumed, '\n',
                               client->used - consumed);
        if (!newline) break;
        *newline = '\0';
        if (!parse_line(client, client->buffer + consumed)) {
            close_client(client);
            return 0;
        }
        consumed = (size_t)(newline - client->buffer) + 1u;
    }
    if (consumed) {
        memmove(client->buffer, client->buffer + consumed,
                client->used - consumed);
        client->used -= consumed;
    }
    return 0;
}

static int accept_client(int fd, uint32_t mask, void *data) {
    struct input_bridge *bridge = data;
    if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) return 0;
    int client = accept4(fd, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
    if (client < 0) return 0;
    struct ucred credentials = {0};
    socklen_t size = sizeof(credentials);
    if (getsockopt(client, SOL_SOCKET, SO_PEERCRED, &credentials, &size) < 0
            || size != sizeof(credentials) || credentials.uid != geteuid()) {
        close(client);
        return 0;
    }
    struct input_client *input = calloc(1, sizeof(*input));
    if (!input) { close(client); return 0; }
    input->bridge = bridge;
    input->fd = client;
    input->next = bridge->clients;
    bridge->clients = input;
    input->source = wl_event_loop_add_fd(
        wl_display_get_event_loop(bridge->compositor->wl_display), client,
        WL_EVENT_READABLE, client_ready, input);
    if (!input->source) close_client(input);
    return 0;
}

static void destroy_bridge(struct wl_listener *listener, void *data) {
    (void)data;
    struct input_bridge *bridge = wl_container_of(
        listener, bridge, destroy_listener);
    while (bridge->clients) close_client(bridge->clients);
    if (bridge->listen_source) wl_event_source_remove(bridge->listen_source);
    if (bridge->listen_fd >= 0) close(bridge->listen_fd);
    unlink(bridge->socket_path);
    weston_seat_release(&bridge->seat);
    wl_list_remove(&bridge->destroy_listener.link);
    free(bridge);
}

WL_EXPORT int wet_module_init(struct weston_compositor *compositor,
                              int *argc, char *argv[]) {
    (void)argc;
    (void)argv;
    const char *path = getenv("KILIX_WESTON_INPUT_SOCKET");
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    if (!path || !runtime || path[0] != '/' ||
            strncmp(path, runtime, strlen(runtime)) != 0 ||
            path[strlen(runtime)] != '/' || strlen(path) >=
            sizeof(((struct sockaddr_un *)0)->sun_path))
        return -1;

    struct input_bridge *bridge = calloc(1, sizeof(*bridge));
    if (!bridge) return -1;
    bridge->listen_fd = -1;
    bridge->compositor = compositor;
    memcpy(bridge->socket_path, path, strlen(path) + 1u);
    bridge->listen_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC |
                               SOCK_NONBLOCK, 0);
    if (bridge->listen_fd < 0) goto fail;
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    memcpy(address.sun_path, path, strlen(path) + 1u);
    unlink(path);
    mode_t old_mask = umask(0077);
    int bound = bind(bridge->listen_fd, (struct sockaddr *)&address,
                     sizeof(address));
    umask(old_mask);
    if (bound < 0 || listen(bridge->listen_fd, 16) < 0) goto fail;

    weston_seat_init(&bridge->seat, compositor, "kilix-input");
    if (weston_seat_init_keyboard(&bridge->seat, NULL) < 0) goto fail_seat;
    weston_seat_init_pointer(&bridge->seat);
    bridge->listen_source = wl_event_loop_add_fd(
        wl_display_get_event_loop(compositor->wl_display), bridge->listen_fd,
        WL_EVENT_READABLE, accept_client, bridge);
    if (!bridge->listen_source) goto fail_seat;
    if (!weston_compositor_add_destroy_listener_once(
            compositor, &bridge->destroy_listener, destroy_bridge))
        goto fail_source;
    return 0;

fail_source:
    wl_event_source_remove(bridge->listen_source);
fail_seat:
    weston_seat_release(&bridge->seat);
fail:
    if (bridge->listen_fd >= 0) close(bridge->listen_fd);
    unlink(path);
    free(bridge);
    return -1;
}
