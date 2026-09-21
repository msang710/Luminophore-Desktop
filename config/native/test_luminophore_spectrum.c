#include "luminophore_spectrum.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>

static size_t strongest(const float bands[LUMINOPHORE_SPECTRUM_BANDS]) {
    size_t result = 0;
    for (size_t index = 1; index < LUMINOPHORE_SPECTRUM_BANDS; index++)
        if (bands[index] > bands[result]) result = index;
    return result;
}

static void test_ring(void) {
    struct luminophore_pcm_ring ring;
    float input[LUMINOPHORE_SPECTRUM_FFT_SIZE], output[LUMINOPHORE_SPECTRUM_FFT_SIZE];
    luminophore_pcm_ring_init(&ring);
    assert(!luminophore_pcm_ring_latest(&ring, output, LUMINOPHORE_SPECTRUM_FFT_SIZE));
    for (size_t index = 0; index < LUMINOPHORE_SPECTRUM_FFT_SIZE; index++) input[index] = (float)index;
    luminophore_pcm_ring_write(&ring, input, LUMINOPHORE_SPECTRUM_FFT_SIZE);
    assert(luminophore_pcm_ring_latest(&ring, output, LUMINOPHORE_SPECTRUM_FFT_SIZE));
    for (size_t index = 0; index < LUMINOPHORE_SPECTRUM_FFT_SIZE; index++) assert(output[index] == input[index]);
}

static size_t sine_band(float frequency) {
    float input[LUMINOPHORE_SPECTRUM_FFT_SIZE], bands[LUMINOPHORE_SPECTRUM_BANDS];
    for (size_t index = 0; index < LUMINOPHORE_SPECTRUM_FFT_SIZE; index++)
        input[index] = sinf(2.0f * (float)M_PI * frequency * index / 48000.0f);
    luminophore_spectrum_analyze(input, 48000, bands);
    return strongest(bands);
}

int main(void) {
    test_ring();
    size_t low = sine_band(60.0f), middle = sine_band(1000.0f), high = sine_band(10000.0f);
    assert(low < middle && middle < high);
    assert(low < 20 && middle > 25 && middle < 45 && high > 50);
    puts("luminophore spectrum tests: PASS");
    return 0;
}
