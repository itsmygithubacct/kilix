/* SPDX-License-Identifier: GPL-3.0-or-later */
/* PipeWire video-node consumer for Kilix's private Weston compositor. */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <drm_fourcc.h>
#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
#include <spa/utils/result.h>

struct lease;
struct state {
    struct pw_main_loop *loop;
    struct pw_stream *stream;
    struct spa_hook listener;
    struct spa_video_info_raw format;
    uint8_t *rgb;
    size_t rgb_size;
    uint64_t frames;
    uint32_t storage_type;
    int listen_fd;
    struct spa_source *listen_source;
    struct pw_buffer *held;
    struct lease *leases;
    const char *socket_path;
    bool trace, trace_all;
    struct {
        uint64_t process, dequeued, coalesced, sequence_changes;
        uint64_t damage_frames, damage_regions, empty_frames;
        uint64_t held, held_busy, ready, accepts, rejected;
        uint64_t sent, send_failed, leases, acked, declined, disconnected;
    } telemetry;
    uint64_t last_sequence;
    bool have_sequence;
};

struct lease {
    struct state *state;
    struct lease *next;
    struct spa_source *source;
    struct pw_buffer *buffer;
    int fd;
};

#define KILIX_DMABUF_MAGIC 0x4b444d41u
#define KILIX_DMABUF_VERSION 2u
struct dmabuf_frame {
    uint32_t magic, version, width, height, stride, offset, fourcc;
    uint32_t modifier_hi, modifier_lo, transform;
};

static bool trace_sample(uint64_t count) {
    return count <= 8u || (count & (count - 1u)) == 0u;
}

static void trace_event(const struct state *state, const char *event,
                        uint64_t count) {
    if (state->trace && (state->trace_all || trace_sample(count))) {
        struct timespec now = {0};
        (void)clock_gettime(CLOCK_MONOTONIC, &now);
        uint64_t monotonic_ns = (uint64_t)now.tv_sec * UINT64_C(1000000000) +
                                (uint64_t)now.tv_nsec;
        fprintf(stderr, "kilix-pw-capture: trace event=%s count=%" PRIu64
                        " monotonic_ns=%" PRIu64 "\n",
                event, count, monotonic_ns);
    }
}

static void print_telemetry(const struct state *state) {
    fprintf(stderr,
        "kilix-pw-capture: telemetry process=%" PRIu64
        " dequeued=%" PRIu64 " coalesced=%" PRIu64
        " sequence-changes=%" PRIu64 " damage-frames=%" PRIu64
        " damage-regions=%" PRIu64 " empty=%" PRIu64
        " held=%" PRIu64 " held-busy=%" PRIu64 " ready=%" PRIu64
        " accepts=%" PRIu64 " rejected=%" PRIu64 " sent=%" PRIu64
        " send-failed=%" PRIu64 " leases=%" PRIu64 " acked=%" PRIu64
        " declined=%" PRIu64 " disconnected=%" PRIu64 "\n",
        state->telemetry.process, state->telemetry.dequeued,
        state->telemetry.coalesced, state->telemetry.sequence_changes,
        state->telemetry.damage_frames, state->telemetry.damage_regions,
        state->telemetry.empty_frames, state->telemetry.held,
        state->telemetry.held_busy, state->telemetry.ready,
        state->telemetry.accepts, state->telemetry.rejected,
        state->telemetry.sent, state->telemetry.send_failed,
        state->telemetry.leases, state->telemetry.acked,
        state->telemetry.declined, state->telemetry.disconnected);
}

static void note_buffer_metadata(struct state *state,
                                 const struct spa_buffer *buffer) {
    const struct spa_meta_header *header = spa_buffer_find_meta_data(
        buffer, SPA_META_Header, sizeof(*header));
    if (header && (!state->have_sequence || header->seq != state->last_sequence)) {
        state->telemetry.sequence_changes++;
        state->last_sequence = header->seq;
        state->have_sequence = true;
    }
    const struct spa_meta *damage = spa_buffer_find_meta(buffer,
                                                         SPA_META_VideoDamage);
    if (damage) {
        uint64_t regions = 0;
        const struct spa_meta_region *region;
        spa_meta_for_each(region, damage) {
            if (!spa_meta_region_is_valid(region)) break;
            regions++;
        }
        if (regions) {
            state->telemetry.damage_frames++;
            state->telemetry.damage_regions += regions;
        }
    }
    if (buffer->n_datas && buffer->datas[0].chunk &&
            (buffer->datas[0].chunk->flags & SPA_CHUNK_FLAG_EMPTY))
        state->telemetry.empty_frames++;
}

static int telemetry_selftest(void) {
    struct state state = {0};
    struct spa_meta_header header = {.seq = 41};
    struct spa_meta_region regions[3] = {
        {.region = {.size = SPA_RECTANGLE(20, 10)}},
        {.region = {.size = SPA_RECTANGLE(5, 4)}},
        {.region = {.size = SPA_RECTANGLE(0, 0)}},
    };
    struct spa_meta metas[2] = {
        {.type = SPA_META_Header, .size = sizeof(header), .data = &header},
        {.type = SPA_META_VideoDamage, .size = sizeof(regions), .data = regions},
    };
    struct spa_chunk chunk = {.flags = SPA_CHUNK_FLAG_EMPTY};
    struct spa_data data = {.chunk = &chunk};
    const struct spa_buffer buffer = {
        .n_metas = 2, .n_datas = 1, .metas = metas, .datas = &data,
    };
    note_buffer_metadata(&state, &buffer);
    note_buffer_metadata(&state, &buffer);
    header.seq++;
    note_buffer_metadata(&state, &buffer);
    if (state.telemetry.sequence_changes != 2 ||
            state.telemetry.damage_frames != 3 ||
            state.telemetry.damage_regions != 6 ||
            state.telemetry.empty_frames != 3 || !trace_sample(8) ||
            !trace_sample(16) || trace_sample(15)) return 1;
    print_telemetry(&state);
    return 0;
}

static void release_held(struct state *state) {
    if (state->held) {
        pw_stream_queue_buffer(state->stream, state->held);
        state->held = NULL;
    }
}

static void client_ack(void *userdata, int fd, uint32_t mask) {
    struct lease *lease = userdata;
    struct state *state = lease->state;
    uint8_t ack = 0;
    ssize_t received = (mask & SPA_IO_IN) ? read(fd, &ack, 1) : -1;
    if (received == 1 && ack == 1) {
        state->frames++;
        state->telemetry.acked++;
        trace_event(state, "ack", state->telemetry.acked);
    } else if (received == 1) {
        state->telemetry.declined++;
        trace_event(state, "decline", state->telemetry.declined);
    } else {
        state->telemetry.disconnected++;
        trace_event(state, "disconnect", state->telemetry.disconnected);
    }
    struct lease **cursor = &state->leases;
    while (*cursor && *cursor != lease) cursor = &(*cursor)->next;
    if (*cursor) *cursor = lease->next;
    if (lease->source)
        pw_loop_destroy_source(pw_main_loop_get_loop(state->loop), lease->source);
    if (lease->fd >= 0) close(lease->fd);
    if (lease->buffer) pw_stream_queue_buffer(state->stream, lease->buffer);
    free(lease);
}

static void send_held_frame(void *userdata, int fd, uint32_t mask) {
    struct state *state = userdata;
    if (!(mask & SPA_IO_IN)) return;
    int client = accept4(fd, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
    if (client < 0) return;
    state->telemetry.accepts++;
    struct ucred credentials = {0};
    socklen_t credentials_size = sizeof(credentials);
    if (getsockopt(client, SOL_SOCKET, SO_PEERCRED, &credentials,
                   &credentials_size) < 0 || credentials_size != sizeof(credentials)
            || credentials.uid != geteuid() || !state->held) {
        state->telemetry.rejected++;
        trace_event(state, "reject", state->telemetry.rejected);
        close(client); return;
    }
    struct spa_buffer *buffer = state->held->buffer;
    if (buffer->n_datas != 1 || buffer->datas[0].type != SPA_DATA_DmaBuf ||
            !buffer->datas[0].chunk || buffer->datas[0].fd < 0) {
        close(client); release_held(state); return;
    }
    const struct spa_data *plane = &buffer->datas[0];
    const struct spa_meta_videotransform *video_transform =
        spa_buffer_find_meta_data(buffer, SPA_META_VideoTransform,
                                  sizeof(*video_transform));
    const struct dmabuf_frame frame = {
        .magic = KILIX_DMABUF_MAGIC, .version = KILIX_DMABUF_VERSION,
        .width = state->format.size.width, .height = state->format.size.height,
        .stride = plane->chunk->stride > 0 ? (uint32_t)plane->chunk->stride :
                  state->format.size.width * 4u,
        .offset = plane->chunk->offset,
        .fourcc = DRM_FORMAT_XRGB8888,
        .modifier_hi = (uint32_t)(state->format.modifier >> 32u),
        .modifier_lo = (uint32_t)state->format.modifier,
        .transform = video_transform ? video_transform->transform :
                     SPA_META_TRANSFORMATION_None,
    };
    char control[CMSG_SPACE(sizeof(int))] = {0};
    struct iovec iov = {.iov_base = (void*)&frame, .iov_len = sizeof(frame)};
    struct msghdr message = {
        .msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control, .msg_controllen = sizeof(control),
    };
    struct cmsghdr *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET; header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int));
    int dma_fd = (int)plane->fd;
    memcpy(CMSG_DATA(header), &dma_fd, sizeof(dma_fd));
    if (sendmsg(client, &message, MSG_NOSIGNAL) != (ssize_t)sizeof(frame)) {
        state->telemetry.send_failed++;
        trace_event(state, "send-failed", state->telemetry.send_failed);
        close(client); release_held(state); return;
    }
    state->telemetry.sent++;
    struct lease *lease = calloc(1, sizeof(*lease));
    if (!lease) { close(client); release_held(state); return; }
    lease->state = state; lease->fd = client; lease->buffer = state->held;
    state->held = NULL;
    lease->next = state->leases; state->leases = lease;
    state->telemetry.leases++;
    trace_event(state, "lease", state->telemetry.leases);
    lease->source = pw_loop_add_io(
        pw_main_loop_get_loop(state->loop), client,
        SPA_IO_IN | SPA_IO_HUP | SPA_IO_ERR, false, client_ack, lease);
    if (!lease->source) {
        client_ack(lease, client, 0);
    }
}

static int start_dmabuf_server(struct state *state, const char *path) {
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    if (!runtime || path[0] != '/' || strncmp(path, runtime, strlen(runtime)) ||
            path[strlen(runtime)] != '/' || strlen(path) >=
            sizeof(((struct sockaddr_un*)0)->sun_path)) return -1;
    state->listen_fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC |
                              SOCK_NONBLOCK, 0);
    if (state->listen_fd < 0) return -1;
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    memcpy(address.sun_path, path, strlen(path) + 1u);
    unlink(path);
    mode_t old_mask = umask(0077);
    int result = bind(state->listen_fd, (struct sockaddr*)&address,
                      sizeof(address));
    umask(old_mask);
    if (result < 0 || listen(state->listen_fd, 1) < 0) return -1;
    state->socket_path = path;
    state->listen_source = pw_loop_add_io(
        pw_main_loop_get_loop(state->loop), state->listen_fd,
        SPA_IO_IN | SPA_IO_ERR, false, send_held_frame, state);
    return state->listen_source ? 0 : -1;
}

static int write_all(int fd, const uint8_t *data, size_t size) {
    while (size) {
        ssize_t written = write(fd, data, size);
        if (written > 0) {
            data += (size_t)written;
            size -= (size_t)written;
            continue;
        }
        if (written < 0 && errno == EINTR) continue;
        if (written < 0 && (errno == EPIPE || errno == ECONNRESET)) return 1;
        return -1;
    }
    return 0;
}

static void quit_signal(void *userdata, int signal_number) {
    (void)signal_number;
    pw_main_loop_quit(((struct state *)userdata)->loop);
}

static void state_changed(void *userdata, enum pw_stream_state old,
                          enum pw_stream_state state, const char *error) {
    (void)old;
    fprintf(stderr, "kilix-pw-capture: state=%s%s%s\n",
            pw_stream_state_as_string(state), error ? " error=" : "",
            error ? error : "");
    if (state == PW_STREAM_STATE_PAUSED)
        pw_stream_set_active(((struct state *)userdata)->stream, true);
    if (state == PW_STREAM_STATE_ERROR) {
        fprintf(stderr, "kilix-pw-capture: stream error: %s\n",
                error ? error : "unknown");
        pw_main_loop_quit(((struct state *)userdata)->loop);
    }
}

static void param_changed(void *userdata, uint32_t id,
                          const struct spa_pod *param) {
    struct state *state = userdata;
    if (param == NULL || id != SPA_PARAM_Format) return;
    if (spa_format_video_raw_parse(param, &state->format) < 0) return;
    const size_t size = (size_t)state->format.size.width *
                        (size_t)state->format.size.height * 3u;
    if (!size || size > SIZE_MAX / 2u) {
        pw_main_loop_quit(state->loop);
        return;
    }
    uint8_t *replacement = realloc(state->rgb, size);
    if (!replacement) {
        fprintf(stderr, "kilix-pw-capture: frame allocation failed\n");
        pw_main_loop_quit(state->loop);
        return;
    }
    state->rgb = replacement;
    state->rgb_size = size;
    fprintf(stderr, "kilix-pw-capture: format=%u size=%ux%u\n",
            state->format.format, state->format.size.width,
            state->format.size.height);
}

static void process_frame(void *userdata) {
    struct state *state = userdata;
    state->telemetry.process++;
    struct pw_buffer *selected = NULL;
    struct pw_buffer *candidate;
    while ((candidate = pw_stream_dequeue_buffer(state->stream)) != NULL) {
        state->telemetry.dequeued++;
        if (selected) {
            state->telemetry.coalesced++;
            pw_stream_queue_buffer(state->stream, selected);
        }
        selected = candidate;
    }
    if (!selected) return;
    if (state->socket_path) {
        if (state->held) {
            state->telemetry.held_busy++;
            trace_event(state, "held-busy", state->telemetry.held_busy);
            pw_stream_queue_buffer(state->stream, selected);
            return;
        }
        struct spa_buffer *candidate_buffer = selected->buffer;
        if (candidate_buffer->n_datas == 1 &&
                candidate_buffer->datas[0].type == SPA_DATA_DmaBuf) {
            note_buffer_metadata(state, candidate_buffer);
            state->held = selected;
            state->telemetry.held++;
            uint8_t ready = 1;
            if (write_all(STDOUT_FILENO, &ready, 1) != 0)
                pw_main_loop_quit(state->loop);
            else {
                state->telemetry.ready++;
                trace_event(state, "ready", state->telemetry.ready);
            }
            return;
        }
        pw_stream_queue_buffer(state->stream, selected);
        return;
    }
    struct spa_buffer *buffer = selected->buffer;
    if (buffer->n_datas < 1) goto done;
    struct spa_data *plane = &buffer->datas[0];
    if (plane->type != state->storage_type) {
        state->storage_type = plane->type;
        const char *storage = plane->type == SPA_DATA_DmaBuf ? "dmabuf" :
            (plane->type == SPA_DATA_MemFd ? "memfd" :
             (plane->type == SPA_DATA_MemPtr ? "memptr" : "other"));
        fprintf(stderr, "kilix-pw-capture: storage=%s fd=%" PRId64 "\n",
                storage, plane->fd);
    }
    if (!plane->data || !plane->chunk || !state->rgb) goto done;
    const struct spa_chunk *chunk = plane->chunk;
    const uint32_t width = state->format.size.width;
    const uint32_t height = state->format.size.height;
    const uint32_t source_stride = chunk->stride > 0 ?
        (uint32_t)chunk->stride : width * 4u;
    const size_t required = (size_t)source_stride * height;
    if (chunk->offset > plane->maxsize || required > plane->maxsize - chunk->offset)
        goto done;
    const uint8_t *source = SPA_PTROFF(plane->data, chunk->offset, const uint8_t);
    uint8_t *target = state->rgb;
    const int bgr = state->format.format == SPA_VIDEO_FORMAT_BGRx ||
                    state->format.format == SPA_VIDEO_FORMAT_BGRA;
    for (uint32_t y = 0; y < height; y++) {
        const uint8_t *row = source + (size_t)y * source_stride;
        for (uint32_t x = 0; x < width; x++) {
            const uint8_t *pixel = row + (size_t)x * 4u;
            if (bgr) {
                *target++ = pixel[2];
                *target++ = pixel[1];
                *target++ = pixel[0];
            } else {
                *target++ = pixel[0];
                *target++ = pixel[1];
                *target++ = pixel[2];
            }
        }
    }
    if (write_all(STDOUT_FILENO, state->rgb, state->rgb_size) != 0)
        pw_main_loop_quit(state->loop);
    state->frames++;
done:
    pw_stream_queue_buffer(state->stream, selected);
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = state_changed,
    .param_changed = param_changed,
    .process = process_frame,
};

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--telemetry-selftest") == 0)
        return telemetry_selftest();
    bool dmabuf_server = argc >= 6 && strcmp(argv[1], "--dmabuf-server") == 0;
    int first = dmabuf_server ? 3 : 1;
    if ((!dmabuf_server && argc != 4 && argc != 5) ||
            (dmabuf_server && argc != 6 && argc != 7)) {
        fprintf(stderr, "usage: kilix-pw-capture [--dmabuf-server SOCKET] TARGET WIDTH HEIGHT [FPS]\n");
        return 2;
    }
    char *end = NULL;
    unsigned long width = strtoul(argv[first + 1], &end, 10);
    if (!end || *end || width < 1 || width > 16384) return 2;
    unsigned long height = strtoul(argv[first + 2], &end, 10);
    if (!end || *end || height < 1 || height > 16384) return 2;
    unsigned long fps = 60;
    if (argc > first + 3) {
        fps = strtoul(argv[first + 3], &end, 10);
        if (!end || *end || fps < 1 || fps > 240) return 2;
    }

    char *target_end = NULL;
    unsigned long target_value = strtoul(argv[first], &target_end, 10);
    uint32_t target_id = (target_end && !*target_end && target_value <= UINT32_MAX) ?
        (uint32_t)target_value : PW_ID_ANY;
    struct state state = {.listen_fd = -1};
    const char *trace = getenv("KILIX_GPU_CAPTURE_TRACE");
    state.trace = trace && *trace && strcmp(trace, "0") != 0;
    const char *trace_all = getenv("KILIX_GPU_CAPTURE_TRACE_ALL");
    state.trace_all = trace_all && *trace_all && strcmp(trace_all, "0") != 0;
    pw_init(&argc, &argv);
    state.loop = pw_main_loop_new(NULL);
    if (!state.loop) return 1;
    pw_loop_add_signal(pw_main_loop_get_loop(state.loop), SIGINT,
                       quit_signal, &state);
    pw_loop_add_signal(pw_main_loop_get_loop(state.loop), SIGTERM,
                       quit_signal, &state);
    const char *node_name = getenv("KILIX_CAPTURE_NODE_NAME");
    if (!node_name || !*node_name) node_name = "kilix-pw-capture";
    struct pw_properties *properties = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Video",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Screen",
        PW_KEY_TARGET_OBJECT, argv[first],
        PW_KEY_NODE_NAME, node_name, NULL);
    state.stream = pw_stream_new_simple(
        pw_main_loop_get_loop(state.loop), node_name,
        properties, &stream_events, &state);
    if (!state.stream) return 1;

    uint8_t storage[2048];
    struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    const struct spa_rectangle size = SPA_RECTANGLE((uint32_t)width, (uint32_t)height);
    /* Weston publishes a wildcard nominal rate and a bounded maximum. Match
     * that shape: fixing the nominal rate prevents PipeWire from intersecting
     * the otherwise compatible 0/1 producer format with this input. */
    const struct spa_fraction rate = SPA_FRACTION(0, 1);
    const struct spa_fraction max_rate = SPA_FRACTION((uint32_t)fps, 1);
    const struct spa_pod *formats[2];
    struct spa_pod_frame frame;
    spa_pod_builder_push_object(&builder, &frame, SPA_TYPE_OBJECT_Format,
                                SPA_PARAM_EnumFormat);
    spa_pod_builder_add(&builder,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRx), 0);
    spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier,
                         SPA_POD_PROP_FLAG_MANDATORY);
    spa_pod_builder_long(&builder, DRM_FORMAT_MOD_LINEAR);
    spa_pod_builder_add(&builder,
        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&size),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&rate),
        SPA_FORMAT_VIDEO_maxFramerate, SPA_POD_Fraction(&max_rate), 0);
    formats[0] = spa_pod_builder_pop(&builder, &frame);
    formats[1] = spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        /* Weston's PipeWire backend currently exports XRGB8888 as BGRx. */
        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRx),
        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&size),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&rate),
        SPA_FORMAT_VIDEO_maxFramerate, SPA_POD_Fraction(&max_rate));
    int result = pw_stream_connect(
        state.stream, PW_DIRECTION_INPUT, target_id,
        PW_STREAM_FLAG_MAP_BUFFERS,
        formats, 2);
    if (result < 0) {
        fprintf(stderr, "kilix-pw-capture: connect failed: %s\n",
                spa_strerror(result));
        return 1;
    }
    if (dmabuf_server && start_dmabuf_server(&state, argv[2]) < 0) {
        fprintf(stderr, "kilix-pw-capture: DMA-BUF server failed: %s\n",
                strerror(errno));
        return 1;
    }
    pw_main_loop_run(state.loop);
    while (state.leases) client_ack(state.leases, state.leases->fd, 0);
    release_held(&state);
    if (state.listen_source)
        pw_loop_destroy_source(pw_main_loop_get_loop(state.loop),
                               state.listen_source);
    if (state.listen_fd >= 0) close(state.listen_fd);
    if (state.socket_path) unlink(state.socket_path);
    pw_stream_destroy(state.stream);
    pw_main_loop_destroy(state.loop);
    free(state.rgb);
    print_telemetry(&state);
    pw_deinit();
    return state.frames ? 0 : 1;
}
