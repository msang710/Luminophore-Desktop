#include "luminophore_audio_capture.h"

#include <spa/param/audio/format-utils.h>
#include <stdio.h>
#include <string.h>

static void stream_state_changed(void *data, enum pw_stream_state old,
                                 enum pw_stream_state state, const char *error) {
    (void)old; (void)error;
    struct luminophore_audio_capture *capture = data;
    atomic_store_explicit(&capture->connected, state == PW_STREAM_STATE_STREAMING,
                          memory_order_release);
    if (state == PW_STREAM_STATE_ERROR)
        atomic_store_explicit(&capture->failed, true, memory_order_release);
    const char *name = state == PW_STREAM_STATE_STREAMING ? "streaming" :
                       state == PW_STREAM_STATE_ERROR ? "error" : "connecting";
    printf("A %s %u %u\n", name,
           atomic_load_explicit(&capture->sample_rate, memory_order_relaxed),
           atomic_load_explicit(&capture->channels, memory_order_relaxed));
    fflush(stdout);
}

static void stream_param_changed(void *data, uint32_t id, const struct spa_pod *param) {
    struct luminophore_audio_capture *capture = data;
    if (!param || id != SPA_PARAM_Format) return;
    struct spa_audio_info_raw info = {0};
    if (spa_format_audio_raw_parse(param, &info) < 0) return;
    atomic_store_explicit(&capture->sample_rate, info.rate, memory_order_release);
    atomic_store_explicit(&capture->channels, info.channels, memory_order_release);
    printf("A %s %u %u\n",
           atomic_load_explicit(&capture->connected, memory_order_relaxed) ? "streaming" : "connecting",
           info.rate, info.channels);
    fflush(stdout);
}

static void stream_process(void *data) {
    struct luminophore_audio_capture *capture = data;
    struct pw_buffer *pw_buffer = pw_stream_dequeue_buffer(capture->stream);
    if (!pw_buffer) return;
    struct spa_buffer *buffer = pw_buffer->buffer;
    if (!buffer->n_datas || !buffer->datas[0].data || !buffer->datas[0].chunk) goto done;
    struct spa_data *audio = &buffer->datas[0];
    uint32_t channels = atomic_load_explicit(&capture->channels, memory_order_relaxed);
    if (!channels) channels = 2;
    size_t frames = audio->chunk->size / (sizeof(float) * channels);
    const float *input = (const float *)((const uint8_t *)audio->data + audio->chunk->offset);
    float mono[1024];
    while (frames) {
        size_t batch = frames < 1024 ? frames : 1024;
        for (size_t frame = 0; frame < batch; frame++) {
            float sum = 0.0f;
            for (uint32_t channel = 0; channel < channels; channel++)
                sum += input[frame * channels + channel];
            mono[frame] = sum / channels;
        }
        luminophore_pcm_ring_write(capture->ring, mono, batch);
        input += batch * channels;
        frames -= batch;
    }
done:
    pw_stream_queue_buffer(capture->stream, pw_buffer);
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = stream_state_changed,
    .param_changed = stream_param_changed,
    .process = stream_process,
};

bool luminophore_audio_capture_start(struct luminophore_audio_capture *capture, struct luminophore_pcm_ring *ring) {
    memset(capture, 0, sizeof(*capture));
    capture->ring = ring;
    atomic_init(&capture->connected, false);
    atomic_init(&capture->failed, false);
    atomic_init(&capture->sample_rate, 48000);
    atomic_init(&capture->channels, 2);
    capture->loop = pw_thread_loop_new("luminophore-spectrum-audio", NULL);
    if (!capture->loop) return false;
    struct pw_properties *properties = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Music",
        PW_KEY_MEDIA_CLASS, "Stream/Input/Audio",
        PW_KEY_STREAM_MONITOR, "true",
        PW_KEY_STREAM_CAPTURE_SINK, "true",
        PW_KEY_NODE_PASSIVE, "true",
        NULL);
    capture->stream = pw_stream_new_simple(pw_thread_loop_get_loop(capture->loop),
                                            "luminophore-spectrum-capture", properties,
                                            &stream_events, capture);
    if (!capture->stream) goto fail;
    uint8_t storage[1024];
    struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    const struct spa_pod *params[1];
    params[0] = spa_format_audio_raw_build(&builder, SPA_PARAM_EnumFormat,
        &SPA_AUDIO_INFO_RAW_INIT(.format = SPA_AUDIO_FORMAT_F32,
                                 .channels = 2, .rate = 48000));
    if (pw_stream_connect(capture->stream, PW_DIRECTION_INPUT, PW_ID_ANY,
                          PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS |
                          PW_STREAM_FLAG_RT_PROCESS, params, 1) < 0) goto fail;
    if (pw_thread_loop_start(capture->loop) < 0) goto fail;
    capture->loop_started = true;
    return true;
fail:
    luminophore_audio_capture_stop(capture);
    return false;
}

void luminophore_audio_capture_stop(struct luminophore_audio_capture *capture) {
    if (!capture) return;
    if (capture->loop && capture->loop_started) pw_thread_loop_stop(capture->loop);
    if (capture->stream) pw_stream_destroy(capture->stream);
    if (capture->loop) pw_thread_loop_destroy(capture->loop);
    capture->stream = NULL;
    capture->loop = NULL;
    capture->loop_started = false;
    atomic_store_explicit(&capture->connected, false, memory_order_release);
}
