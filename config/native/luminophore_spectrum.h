#ifndef LUMINOPHORE_SPECTRUM_H
#define LUMINOPHORE_SPECTRUM_H

#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define LUMINOPHORE_PCM_RING_CAPACITY 16384u
#define LUMINOPHORE_SPECTRUM_FFT_SIZE 2048u
#define LUMINOPHORE_SPECTRUM_BANDS 64u

struct luminophore_pcm_ring {
    float samples[LUMINOPHORE_PCM_RING_CAPACITY];
    _Atomic uint64_t write_sequence;
};

void luminophore_pcm_ring_init(struct luminophore_pcm_ring *ring);
void luminophore_pcm_ring_write(struct luminophore_pcm_ring *ring, const float *samples, size_t count);
bool luminophore_pcm_ring_latest(const struct luminophore_pcm_ring *ring, float *output, size_t count);
void luminophore_spectrum_analyze(const float *samples, uint32_t sample_rate,
                           float output[LUMINOPHORE_SPECTRUM_BANDS]);

#endif
