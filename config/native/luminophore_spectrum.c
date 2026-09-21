#include "luminophore_spectrum.h"

#include <math.h>
#include <string.h>

struct complex_sample { float real, imaginary; };

void luminophore_pcm_ring_init(struct luminophore_pcm_ring *ring) {
    memset(ring->samples, 0, sizeof(ring->samples));
    atomic_init(&ring->write_sequence, 0);
}

void luminophore_pcm_ring_write(struct luminophore_pcm_ring *ring, const float *samples, size_t count) {
    uint64_t sequence = atomic_load_explicit(&ring->write_sequence, memory_order_relaxed);
    for (size_t index = 0; index < count; index++)
        ring->samples[(sequence + index) % LUMINOPHORE_PCM_RING_CAPACITY] = samples[index];
    atomic_store_explicit(&ring->write_sequence, sequence + count, memory_order_release);
}

bool luminophore_pcm_ring_latest(const struct luminophore_pcm_ring *ring, float *output, size_t count) {
    if (!count || count > LUMINOPHORE_PCM_RING_CAPACITY) return false;
    for (unsigned attempt = 0; attempt < 3; attempt++) {
        uint64_t end = atomic_load_explicit(&ring->write_sequence, memory_order_acquire);
        if (end < count) return false;
        uint64_t start = end - count;
        for (size_t index = 0; index < count; index++)
            output[index] = ring->samples[(start + index) % LUMINOPHORE_PCM_RING_CAPACITY];
        if (atomic_load_explicit(&ring->write_sequence, memory_order_acquire) == end) return true;
    }
    return false;
}

static void fft(struct complex_sample *values) {
    for (size_t i = 1, j = 0; i < LUMINOPHORE_SPECTRUM_FFT_SIZE; i++) {
        size_t bit = LUMINOPHORE_SPECTRUM_FFT_SIZE >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { struct complex_sample temporary = values[i]; values[i] = values[j]; values[j] = temporary; }
    }
    for (size_t length = 2; length <= LUMINOPHORE_SPECTRUM_FFT_SIZE; length <<= 1) {
        float angle = -2.0f * (float)M_PI / (float)length;
        struct complex_sample root = {cosf(angle), sinf(angle)};
        for (size_t base = 0; base < LUMINOPHORE_SPECTRUM_FFT_SIZE; base += length) {
            struct complex_sample factor = {1.0f, 0.0f};
            for (size_t offset = 0; offset < length / 2; offset++) {
                struct complex_sample even = values[base + offset];
                struct complex_sample odd_source = values[base + offset + length / 2];
                struct complex_sample odd = {
                    odd_source.real * factor.real - odd_source.imaginary * factor.imaginary,
                    odd_source.real * factor.imaginary + odd_source.imaginary * factor.real,
                };
                values[base + offset] = (struct complex_sample){even.real + odd.real, even.imaginary + odd.imaginary};
                values[base + offset + length / 2] = (struct complex_sample){even.real - odd.real, even.imaginary - odd.imaginary};
                factor = (struct complex_sample){
                    factor.real * root.real - factor.imaginary * root.imaginary,
                    factor.real * root.imaginary + factor.imaginary * root.real,
                };
            }
        }
    }
}

void luminophore_spectrum_analyze(const float *samples, uint32_t sample_rate,
                           float output[LUMINOPHORE_SPECTRUM_BANDS]) {
    struct complex_sample values[LUMINOPHORE_SPECTRUM_FFT_SIZE];
    memset(output, 0, sizeof(float) * LUMINOPHORE_SPECTRUM_BANDS);
    if (!sample_rate) return;
    for (size_t index = 0; index < LUMINOPHORE_SPECTRUM_FFT_SIZE; index++) {
        float window = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)index /
                                         (float)(LUMINOPHORE_SPECTRUM_FFT_SIZE - 1));
        values[index] = (struct complex_sample){samples[index] * window, 0.0f};
    }
    fft(values);
    float nyquist = (float)sample_rate * 0.5f;
    float upper = fminf(20000.0f, nyquist);
    if (upper <= 20.0f) return;
    for (size_t band = 0; band < LUMINOPHORE_SPECTRUM_BANDS; band++) {
        float low = 20.0f * powf(upper / 20.0f, (float)band / LUMINOPHORE_SPECTRUM_BANDS);
        float high = 20.0f * powf(upper / 20.0f, (float)(band + 1) / LUMINOPHORE_SPECTRUM_BANDS);
        size_t first = (size_t)ceilf(low * LUMINOPHORE_SPECTRUM_FFT_SIZE / sample_rate);
        size_t last = (size_t)floorf(high * LUMINOPHORE_SPECTRUM_FFT_SIZE / sample_rate);
        if (last < first) last = first;
        if (last >= LUMINOPHORE_SPECTRUM_FFT_SIZE / 2) last = LUMINOPHORE_SPECTRUM_FFT_SIZE / 2 - 1;
        float peak = 0.0f;
        for (size_t bin = first; bin <= last; bin++)
            peak = fmaxf(peak, hypotf(values[bin].real, values[bin].imaginary));
        output[band] = peak * (2.0f / LUMINOPHORE_SPECTRUM_FFT_SIZE);
    }
}
