/* SPDX-License-Identifier: GPL-3.0-or-later */
/* DMA-BUF -> VAAPI H.264 encoder for Kilix's shared GPU host. */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <libavcodec/avcodec.h>
#include <libavfilter/avfilter.h>
#include <libavfilter/buffersink.h>
#include <libavfilter/buffersrc.h>
#include <libavutil/buffer.h>
#include <libavutil/error.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_drm.h>
#include <libavutil/imgutils.h>
#include <libavutil/opt.h>
#include <libavutil/pixdesc.h>

#define KILIX_DMABUF_MAGIC 0x4b444d41u
#define KILIX_DMABUF_VERSION 1u
struct dmabuf_frame {
    uint32_t magic, version, width, height, stride, offset, fourcc;
    uint32_t modifier_hi, modifier_lo;
};

struct encoder {
    AVBufferRef *drm_device, *drm_frames;
    AVFilterGraph *graph;
    AVFilterContext *source, *sink;
    AVCodecContext *codec;
    int64_t pts, submitted;
};

static void fail_av(const char *what, int error) {
    char detail[AV_ERROR_MAX_STRING_SIZE];
    av_strerror(error, detail, sizeof(detail));
    fprintf(stderr, "kilix-dmabuf-encode: %s: %s\n", what, detail);
}

static int write_all(int fd, const uint8_t *data, size_t size) {
    while (size) {
        ssize_t count = write(fd, data, size);
        if (count > 0) { data += count; size -= (size_t)count; continue; }
        if (count < 0 && errno == EINTR) continue;
        return -1;
    }
    return 0;
}

static void free_drm_descriptor(void *opaque, uint8_t *data) {
    (void)opaque;
    AVDRMFrameDescriptor *descriptor = (AVDRMFrameDescriptor *)data;
    for (int i = 0; i < descriptor->nb_objects; i++)
        if (descriptor->objects[i].fd >= 0) close(descriptor->objects[i].fd);
    av_free(descriptor);
}

static int init_encoder(struct encoder *encoder, uint32_t width, uint32_t height,
                        int fps, int keyint, int bitrate, const char *device) {
    int result = av_hwdevice_ctx_create(
        &encoder->drm_device, AV_HWDEVICE_TYPE_DRM, device, NULL, 0);
    if (result < 0) return result;
    encoder->drm_frames = av_hwframe_ctx_alloc(encoder->drm_device);
    if (!encoder->drm_frames) return AVERROR(ENOMEM);
    AVHWFramesContext *frames = (AVHWFramesContext *)encoder->drm_frames->data;
    frames->format = AV_PIX_FMT_DRM_PRIME;
    frames->sw_format = AV_PIX_FMT_BGR0;
    frames->width = (int)width;
    frames->height = (int)height;
    result = av_hwframe_ctx_init(encoder->drm_frames);
    if (result < 0) return result;

    encoder->graph = avfilter_graph_alloc();
    if (!encoder->graph) return AVERROR(ENOMEM);
    const AVFilter *buffer = avfilter_get_by_name("buffer");
    const AVFilter *hwmap = avfilter_get_by_name("hwmap");
    const AVFilter *scale = avfilter_get_by_name("scale_vaapi");
    const AVFilter *buffersink = avfilter_get_by_name("buffersink");
    if (!buffer || !hwmap || !scale || !buffersink) return AVERROR_FILTER_NOT_FOUND;
    char arguments[256];
    snprintf(arguments, sizeof(arguments),
             "video_size=%ux%u:pix_fmt=%d:time_base=1/%d:pixel_aspect=1/1",
             width, height, AV_PIX_FMT_DRM_PRIME, fps);
    result = avfilter_graph_create_filter(
        &encoder->source, buffer, "kilix_dmabuf", arguments, NULL, encoder->graph);
    if (result < 0) return result;
    AVBufferSrcParameters *parameters = av_buffersrc_parameters_alloc();
    if (!parameters) return AVERROR(ENOMEM);
    parameters->format = AV_PIX_FMT_DRM_PRIME;
    parameters->width = (int)width;
    parameters->height = (int)height;
    parameters->time_base = (AVRational){1, fps};
    parameters->frame_rate = (AVRational){fps, 1};
    parameters->color_space = AVCOL_SPC_BT709;
    parameters->color_range = AVCOL_RANGE_JPEG;
    parameters->hw_frames_ctx = av_buffer_ref(encoder->drm_frames);
    result = av_buffersrc_parameters_set(encoder->source, parameters);
    av_free(parameters);
    if (result < 0) return result;
    AVFilterContext *map = NULL, *convert = NULL;
    result = avfilter_graph_create_filter(
        &map, hwmap, "kilix_vaapi_map", "derive_device=vaapi:mode=read", NULL,
        encoder->graph);
    if (result < 0) return result;
    result = avfilter_graph_create_filter(
        &convert, scale, "kilix_vaapi_nv12", "format=nv12:mode=fast", NULL,
        encoder->graph);
    if (result < 0) return result;
    result = avfilter_graph_create_filter(
        &encoder->sink, buffersink, "kilix_encoded_frames", NULL, NULL,
        encoder->graph);
    if (result < 0) return result;
    enum AVPixelFormat formats[] = {AV_PIX_FMT_VAAPI, AV_PIX_FMT_NONE};
    result = av_opt_set_int_list(encoder->sink, "pix_fmts", formats,
                                 AV_PIX_FMT_NONE, AV_OPT_SEARCH_CHILDREN);
    if (result < 0) return result;
    if ((result = avfilter_link(encoder->source, 0, map, 0)) < 0 ||
            (result = avfilter_link(map, 0, convert, 0)) < 0 ||
            (result = avfilter_link(convert, 0, encoder->sink, 0)) < 0)
        return result;
    result = avfilter_graph_config(encoder->graph, NULL);
    if (result < 0) return result;

    const AVCodec *codec = avcodec_find_encoder_by_name("h264_vaapi");
    if (!codec) return AVERROR_ENCODER_NOT_FOUND;
    encoder->codec = avcodec_alloc_context3(codec);
    if (!encoder->codec) return AVERROR(ENOMEM);
    encoder->codec->width = (int)width;
    encoder->codec->height = (int)height;
    encoder->codec->time_base = (AVRational){1, fps};
    encoder->codec->framerate = (AVRational){fps, 1};
    encoder->codec->pix_fmt = AV_PIX_FMT_VAAPI;
    encoder->codec->bit_rate = 0;
    encoder->codec->gop_size = keyint;
    encoder->codec->max_b_frames = 0;
    AVBufferRef *sink_frames = av_buffersink_get_hw_frames_ctx(encoder->sink);
    if (!sink_frames) return AVERROR(EINVAL);
    encoder->codec->hw_frames_ctx = av_buffer_ref(sink_frames);
    (void)bitrate; /* CQP is the only rate-control mode on some Intel nodes. */
    av_opt_set(encoder->codec->priv_data, "rc_mode", "CQP", 0);
    av_opt_set(encoder->codec->priv_data, "qp", "24", 0);
    av_opt_set(encoder->codec->priv_data, "async_depth", "1", 0);
    return avcodec_open2(encoder->codec, codec, NULL);
}

static int encode_packets(struct encoder *encoder, AVFrame *frame) {
    int result = avcodec_send_frame(encoder->codec, frame);
    if (result < 0) return result;
    if (frame) encoder->submitted++;
    AVPacket *packet = av_packet_alloc();
    if (!packet) return AVERROR(ENOMEM);
    while ((result = avcodec_receive_packet(encoder->codec, packet)) >= 0) {
        if (write_all(STDOUT_FILENO, packet->data, packet->size) < 0) {
            av_packet_free(&packet);
            return AVERROR(EPIPE);
        }
        av_packet_unref(packet);
    }
    av_packet_free(&packet);
    return result == AVERROR(EAGAIN) || result == AVERROR_EOF ? 0 : result;
}

static int encode_frame(struct encoder *encoder, const struct dmabuf_frame *info,
                        int fd) {
    AVDRMFrameDescriptor *descriptor = av_mallocz(sizeof(*descriptor));
    AVFrame *input = av_frame_alloc(), *mapped = av_frame_alloc();
    if (!descriptor || !input || !mapped) {
        av_free(descriptor);
        av_frame_free(&input);
        av_frame_free(&mapped);
        close(fd);
        return AVERROR(ENOMEM);
    }
    descriptor->nb_objects = 1;
    descriptor->objects[0].fd = fd;
    descriptor->objects[0].size = info->offset + (size_t)info->stride * info->height;
    descriptor->objects[0].format_modifier =
        ((uint64_t)info->modifier_hi << 32u) | info->modifier_lo;
    descriptor->nb_layers = 1;
    descriptor->layers[0].format = info->fourcc;
    descriptor->layers[0].nb_planes = 1;
    descriptor->layers[0].planes[0].object_index = 0;
    descriptor->layers[0].planes[0].offset = info->offset;
    descriptor->layers[0].planes[0].pitch = info->stride;
    input->format = AV_PIX_FMT_DRM_PRIME;
    input->width = (int)info->width;
    input->height = (int)info->height;
    input->pts = encoder->pts++;
    input->color_range = AVCOL_RANGE_JPEG;
    input->colorspace = AVCOL_SPC_BT709;
    input->color_primaries = AVCOL_PRI_BT709;
    input->color_trc = AVCOL_TRC_BT709;
    input->data[0] = (uint8_t *)descriptor;
    input->buf[0] = av_buffer_create(
        (uint8_t *)descriptor, sizeof(*descriptor), free_drm_descriptor, NULL, 0);
    input->hw_frames_ctx = av_buffer_ref(encoder->drm_frames);
    if (!input->buf[0]) {
        free_drm_descriptor(NULL, (uint8_t *)descriptor);
        input->data[0] = NULL;
    }
    if (!input->buf[0] || !input->hw_frames_ctx) {
        av_frame_free(&input);
        av_frame_free(&mapped);
        return AVERROR(ENOMEM);
    }
    int result = av_buffersrc_add_frame_flags(
        encoder->source, input, AV_BUFFERSRC_FLAG_KEEP_REF);
    if (result >= 0) result = av_buffersink_get_frame(encoder->sink, mapped);
    if (result >= 0) {
        mapped->pts = input->pts;
        result = encode_packets(encoder, mapped);
    }
    av_frame_free(&input);
    av_frame_free(&mapped);
    return result;
}

static int receive_frame(const char *path, struct dmabuf_frame *info,
                         int *transfer_socket) {
    int sock = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    if (sock < 0) return -1;
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(address.sun_path)) { close(sock); return -1; }
    memcpy(address.sun_path, path, strlen(path) + 1);
    if (connect(sock, (struct sockaddr *)&address, sizeof(address)) < 0) {
        close(sock); return -1;
    }
    char control[CMSG_SPACE(sizeof(int))] = {0};
    struct iovec iov = {.iov_base = info, .iov_len = sizeof(*info)};
    struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control, .msg_controllen = sizeof(control)};
    if (recvmsg(sock, &message, 0) != (ssize_t)sizeof(*info)) {
        close(sock); return -1;
    }
    struct cmsghdr *header = CMSG_FIRSTHDR(&message);
    if (!header || header->cmsg_level != SOL_SOCKET ||
            header->cmsg_type != SCM_RIGHTS) { close(sock); return -1; }
    int fd = -1;
    memcpy(&fd, CMSG_DATA(header), sizeof(fd));
    *transfer_socket = sock;
    return fd;
}

int main(int argc, char **argv) {
    if (argc < 7 || argc > 8) {
        fprintf(stderr, "usage: kilix-dmabuf-encode SOCKET WIDTH HEIGHT FPS KEYINT VAAPI_DEVICE [BITRATE]\n");
        return 2;
    }
    int width = atoi(argv[2]), height = atoi(argv[3]), fps = atoi(argv[4]);
    int keyint = atoi(argv[5]);
    int bitrate = argc == 8 ? atoi(argv[7]) : 4000000;
    if (width < 1 || height < 1 || fps < 1 || fps > 240 || keyint < 1 ||
            bitrate < 10000)
        return 2;
    struct encoder encoder = {0};
    int result = init_encoder(&encoder, width, height, fps, keyint, bitrate,
                              argv[6]);
    if (result < 0) { fail_av("initialization failed", result); return 1; }
    for (;;) {
        struct dmabuf_frame info = {0};
        int transfer = -1;
        int fd = receive_frame(argv[1], &info, &transfer);
        if (fd < 0) break;
        uint8_t ack = 0;
        if (info.magic == KILIX_DMABUF_MAGIC &&
                info.version == KILIX_DMABUF_VERSION &&
                info.width == (uint32_t)width && info.height == (uint32_t)height) {
            result = encode_frame(&encoder, &info, fd);
            if (result >= 0) ack = 1;
            else fail_av("frame failed", result);
        } else close(fd);
        write_all(transfer, &ack, 1);
        close(transfer);
        if (result < 0) break;
    }
    if (encoder.submitted) encode_packets(&encoder, NULL);
    avcodec_free_context(&encoder.codec);
    avfilter_graph_free(&encoder.graph);
    av_buffer_unref(&encoder.drm_frames);
    av_buffer_unref(&encoder.drm_device);
    return result < 0 ? 1 : 0;
}
