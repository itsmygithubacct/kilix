/* SPDX-License-Identifier: GPL-3.0-or-later */
/* PipeWire video-node consumer for Kilix's private Weston compositor. */

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
#include <spa/utils/result.h>

struct state {
    struct pw_main_loop *loop;
    struct pw_stream *stream;
    struct spa_hook listener;
    struct spa_video_info_raw format;
    uint8_t *rgb;
    size_t rgb_size;
    uint64_t frames;
};

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
    struct pw_buffer *selected = NULL;
    struct pw_buffer *candidate;
    while ((candidate = pw_stream_dequeue_buffer(state->stream)) != NULL) {
        if (selected) pw_stream_queue_buffer(state->stream, selected);
        selected = candidate;
    }
    if (!selected) return;
    struct spa_buffer *buffer = selected->buffer;
    if (buffer->n_datas < 1 || !buffer->datas[0].data ||
            !buffer->datas[0].chunk || !state->rgb) goto done;
    struct spa_data *plane = &buffer->datas[0];
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
    if (argc != 4) {
        fprintf(stderr, "usage: kilix-pw-capture TARGET WIDTH HEIGHT\n");
        return 2;
    }
    char *end = NULL;
    unsigned long width = strtoul(argv[2], &end, 10);
    if (!end || *end || width < 1 || width > 16384) return 2;
    unsigned long height = strtoul(argv[3], &end, 10);
    if (!end || *end || height < 1 || height > 16384) return 2;

    char *target_end = NULL;
    unsigned long target_value = strtoul(argv[1], &target_end, 10);
    uint32_t target_id = (target_end && !*target_end && target_value <= UINT32_MAX) ?
        (uint32_t)target_value : PW_ID_ANY;
    struct state state = {0};
    pw_init(&argc, &argv);
    state.loop = pw_main_loop_new(NULL);
    if (!state.loop) return 1;
    pw_loop_add_signal(pw_main_loop_get_loop(state.loop), SIGINT,
                       quit_signal, &state);
    pw_loop_add_signal(pw_main_loop_get_loop(state.loop), SIGTERM,
                       quit_signal, &state);
    struct pw_properties *properties = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Video",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Screen",
        PW_KEY_TARGET_OBJECT, argv[1], NULL);
    state.stream = pw_stream_new_simple(
        pw_main_loop_get_loop(state.loop), "kilix-pw-capture",
        properties, &stream_events, &state);
    if (!state.stream) return 1;

    uint8_t storage[1024];
    struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    const struct spa_rectangle size = SPA_RECTANGLE((uint32_t)width, (uint32_t)height);
    const struct spa_fraction rate = SPA_FRACTION(0, 1);
    const struct spa_pod *format = spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        /* Weston's PipeWire backend currently exports XRGB8888 as BGRx. */
        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRx),
        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&size),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&rate));
    int result = pw_stream_connect(
        state.stream, PW_DIRECTION_INPUT, target_id,
        PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS,
        &format, 1);
    if (result < 0) {
        fprintf(stderr, "kilix-pw-capture: connect failed: %s\n",
                spa_strerror(result));
        return 1;
    }
    pw_main_loop_run(state.loop);
    pw_stream_destroy(state.stream);
    pw_main_loop_destroy(state.loop);
    free(state.rgb);
    pw_deinit();
    return state.frames ? 0 : 1;
}
