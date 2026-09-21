#ifndef LUMINOPHORE_AUDIO_CAPTURE_H
#define LUMINOPHORE_AUDIO_CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

#include <pipewire/pipewire.h>

#include "luminophore_spectrum.h"

struct luminophore_audio_capture {
    struct pw_thread_loop *loop;
    struct pw_stream *stream;
    struct spa_hook stream_listener;
    struct luminophore_pcm_ring *ring;
    bool loop_started;
    _Atomic bool connected;
    _Atomic bool failed;
    _Atomic uint32_t sample_rate;
    _Atomic uint32_t channels;
};

bool luminophore_audio_capture_start(struct luminophore_audio_capture *capture, struct luminophore_pcm_ring *ring);
void luminophore_audio_capture_stop(struct luminophore_audio_capture *capture);

#endif
