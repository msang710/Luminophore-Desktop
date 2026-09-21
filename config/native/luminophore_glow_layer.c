#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <wayland-egl.h>

#include "wlr-layer-shell-unstable-v1-client-protocol.h"
#include "luminophore_audio_capture.h"
#include "luminophore_glow_bloom.h"
#include "luminophore_glow_shader.h"

struct app;

#define MAX_GLOW_RECTS 16
#define MAX_SPECTRUM_OUTPUTS 8
#define SPECTRUM_BANDS 64
#define WIDGET_FRAME_INTERVAL (1.0 / 72.0)
#define SPECTRUM_FRAME_INTERVAL (1.0 / 90.0)
#define IDLE_AUDIO_INTERVAL (1.0 / 30.0)

struct glow_rect {
    char output[64];
    char id[64];
    float x, y, width, height;
    float radius, outline, extent, intensity, phase;
    float reveal_from, reveal_to;
    double reveal_started, reveal_duration;
    float reveal_offset_x, reveal_offset_y;
    float base[3], core[3], edge[4];
    double activated_at;
    bool top;
    uint64_t revision;
};

struct spectrum_output {
    char output[64];
    float global_x, width, desktop_left, desktop_width;
    float left_color[3], right_color[3];
};

struct widget_bloom_slot {
    char output[64];
    char id[64];
    struct luminophore_bloom_pyramid bloom;
    bool initialized;
    uint64_t reported_revision;
};

struct output_surface {
    struct app *app;
    struct wl_output *output;
    struct wl_surface *surface;
    struct zwlr_layer_surface_v1 *layer_surface;
    struct wl_egl_window *egl_window;
    EGLSurface egl_surface;
    struct wl_callback *frame_callback;
    int32_t width;
    int32_t height;
    bool configured;
    bool closed;
    bool top;
    bool force_pending;
    double last_render_at;
    char name[64];
    struct output_surface *next;
};

struct app {
    struct wl_display *display;
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct zwlr_layer_shell_v1 *layer_shell;
    struct output_surface *outputs;
    EGLDisplay egl_display;
    EGLConfig egl_config;
    EGLContext egl_context;
    GLuint program;
    GLuint spectrum_program;
    GLuint buffer;
    struct widget_bloom_slot widget_blooms[MAX_GLOW_RECTS];
    GLint position;
    GLint viewport;
    GLint time_uniform;
    GLint age_uniform;
    GLint rect_uniform;
    GLint radius_uniform;
    GLint outline_uniform;
    GLint extent_uniform;
    GLint intensity_uniform;
    GLint phase_uniform;
    GLint base_uniform;
    GLint core_uniform;
    GLint edge_uniform;
    GLint spectrum_position;
    GLint spectrum_color;
    GLint spectrum_height;
    struct glow_rect rects[MAX_GLOW_RECTS];
    struct glow_rect pending[MAX_GLOW_RECTS];
    size_t rect_count;
    size_t pending_count;
    struct spectrum_output spectrum_outputs[MAX_SPECTRUM_OUTPUTS];
    struct spectrum_output pending_spectrum_outputs[MAX_SPECTRUM_OUTPUTS];
    size_t spectrum_output_count;
    size_t pending_spectrum_output_count;
    char input_buffer[16384];
    size_t input_length;
    bool running;
    bool widget_bloom;
    bool window_demo;
    bool spectrum_demo;
    bool spectrum_live;
    bool accept_stdin;
    int exit_code;
    uint64_t accepted_revision;
    uint64_t rendered_revision;
    struct luminophore_pcm_ring spectrum_ring;
    struct luminophore_audio_capture audio_capture;
    float spectrum_bands[SPECTRUM_BANDS];
    double last_spectrum_analysis;
    uint64_t last_spectrum_sequence;
    double last_spectrum_audio_at;
    double silence_started_at;
    bool spectrum_silent;
    unsigned audio_reconnect_count;
    double next_audio_reconnect;
    int scheduler_fd;
};

static bool read_commands(struct app *app);

static volatile sig_atomic_t stop_requested;

static const char *spectrum_fragment_source =
    "precision mediump float;"
    "uniform vec4 u_color; uniform float u_height;"
    "void main(){"
    " float edge=1.0-smoothstep(u_height-1.0,u_height,gl_FragCoord.y);"
    " gl_FragColor=u_color*edge;"
    "}";

static double monotonic_seconds(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return value.tv_sec + value.tv_nsec / 1000000000.0;
}

static void fail(struct app *app, const char *message) {
    fprintf(stderr, "luminophore-glow-layer: %s\n", message);
    app->exit_code = 1;
    app->running = false;
}

static GLuint compile_shader(GLenum kind, const char *source) {
    GLuint shader = glCreateShader(kind);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char error[1024] = {0};
        glGetShaderInfoLog(shader, sizeof(error), NULL, error);
        fprintf(stderr, "luminophore-glow-layer: shader compile: %s\n", error);
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

static GLuint link_program(const char *fragment) {
    GLuint vertex = compile_shader(GL_VERTEX_SHADER, luminophore_glow_vertex_source);
    GLuint fragment_shader = compile_shader(GL_FRAGMENT_SHADER, fragment);
    if (!vertex || !fragment_shader) return 0;
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex); glAttachShader(program, fragment_shader);
    glLinkProgram(program); glDeleteShader(vertex); glDeleteShader(fragment_shader);
    GLint ok = GL_FALSE; glGetProgramiv(program, GL_LINK_STATUS, &ok);
    if (!ok) { glDeleteProgram(program); return 0; }
    return program;
}

static bool init_gl(struct app *app) {
    app->egl_display = eglGetDisplay((EGLNativeDisplayType)app->display);
    if (app->egl_display == EGL_NO_DISPLAY || !eglInitialize(app->egl_display, NULL, NULL)) return false;
    const EGLint attributes[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE,
    };
    EGLint count = 0;
    if (!eglChooseConfig(app->egl_display, attributes, &app->egl_config, 1, &count) || count != 1) return false;
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    app->egl_context = eglCreateContext(app->egl_display, app->egl_config, EGL_NO_CONTEXT, context_attributes);
    if (app->egl_context == EGL_NO_CONTEXT) return false;
    if (!eglMakeCurrent(app->egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, app->egl_context)) return false;
    app->program = link_program(luminophore_glow_fragment_source);
    app->spectrum_program = link_program(spectrum_fragment_source);
    if (!app->program || !app->spectrum_program) return false;
    const GLfloat vertices[] = {-1,-1, 1,-1, -1,1, 1,1};
    glGenBuffers(1, &app->buffer); glBindBuffer(GL_ARRAY_BUFFER, app->buffer);
    glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
    app->position = glGetAttribLocation(app->program, "a_position");
    app->viewport = glGetUniformLocation(app->program, "u_viewport");
    app->time_uniform = glGetUniformLocation(app->program, "u_time");
    app->age_uniform = glGetUniformLocation(app->program, "u_age");
    app->rect_uniform = glGetUniformLocation(app->program, "u_rect");
    app->radius_uniform = glGetUniformLocation(app->program, "u_radius");
    app->outline_uniform = glGetUniformLocation(app->program, "u_outline");
    app->extent_uniform = glGetUniformLocation(app->program, "u_extent");
    app->intensity_uniform = glGetUniformLocation(app->program, "u_intensity");
    app->phase_uniform = glGetUniformLocation(app->program, "u_phase");
    app->base_uniform = glGetUniformLocation(app->program, "u_base");
    app->core_uniform = glGetUniformLocation(app->program, "u_core");
    app->edge_uniform = glGetUniformLocation(app->program, "u_edge");
    app->spectrum_position = glGetAttribLocation(app->spectrum_program, "a_position");
    app->spectrum_color = glGetUniformLocation(app->spectrum_program, "u_color");
    app->spectrum_height = glGetUniformLocation(app->spectrum_program, "u_height");
    return true;
}

static void render_output(struct output_surface *output);
static void pump_outputs(struct app *app);

static void frame_done(void *data, struct wl_callback *callback, uint32_t time) {
    (void)time;
    struct output_surface *output = data;
    wl_callback_destroy(callback);
    output->frame_callback = NULL;
    if (output->app->running && !output->closed) pump_outputs(output->app);
}

static const struct wl_callback_listener frame_listener = {.done = frame_done};

static void draw_rect(struct output_surface *output, const struct glow_rect *rect) {
    struct app *app = output->app;
    float center_x = rect->x + rect->width * 0.5f;
    float center_y = output->height - (rect->y + rect->height * 0.5f);
    int left = (int)(rect->x - rect->extent - 8.0f);
    int bottom = (int)(output->height - rect->y - rect->height - rect->extent - 8.0f);
    int width = (int)(rect->width + rect->extent * 2.0f + 16.0f);
    int height = (int)(rect->height + rect->extent * 2.0f + 16.0f);
    if (left < 0) { width += left; left = 0; }
    if (bottom < 0) { height += bottom; bottom = 0; }
    if (left + width > output->width) width = output->width - left;
    if (bottom + height > output->height) height = output->height - bottom;
    if (width <= 0 || height <= 0) return;
    glScissor(left, bottom, width, height);
    glUniform4f(app->rect_uniform, center_x, center_y, rect->width, rect->height);
    glUniform1f(app->radius_uniform, rect->radius);
    glUniform1f(app->outline_uniform, rect->outline);
    glUniform1f(app->extent_uniform, rect->extent);
    glUniform1f(app->intensity_uniform, rect->intensity);
    glUniform1f(app->age_uniform, (float)(monotonic_seconds() - rect->activated_at));
    glUniform1f(app->phase_uniform, rect->phase);
    glUniform3fv(app->base_uniform, 1, rect->base);
    glUniform3fv(app->core_uniform, 1, rect->core);
    glUniform4fv(app->edge_uniform, 1, rect->edge);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
}

static struct luminophore_bloom_pyramid *widget_bloom_for(
    struct app *app, const struct glow_rect *rect
) {
    struct widget_bloom_slot *free_slot = NULL;
    for (size_t index = 0; index < MAX_GLOW_RECTS; index++) {
        struct widget_bloom_slot *slot = &app->widget_blooms[index];
        if (slot->initialized && !strcmp(slot->output, rect->output) &&
            !strcmp(slot->id, rect->id)) return &slot->bloom;
        if (!slot->initialized && free_slot == NULL) free_slot = slot;
    }
    if (free_slot == NULL || !luminophore_bloom_init(&free_slot->bloom)) return NULL;
    snprintf(free_slot->output, sizeof(free_slot->output), "%s", rect->output);
    snprintf(free_slot->id, sizeof(free_slot->id), "%s", rect->id);
    free_slot->initialized = true;
    return &free_slot->bloom;
}

static struct widget_bloom_slot *widget_bloom_slot_for(
    struct app *app, const struct glow_rect *rect
) {
    for (size_t index = 0; index < MAX_GLOW_RECTS; index++) {
        struct widget_bloom_slot *slot = &app->widget_blooms[index];
        if (slot->initialized && !strcmp(slot->output, rect->output) &&
            !strcmp(slot->id, rect->id)) return slot;
    }
    return NULL;
}

static bool draw_bloom_rect(struct output_surface *output, const struct glow_rect *rect) {
    struct app *app = output->app;
    /* Spectrum uses scissoring, while the bloom pyramid renders through
     * differently sized FBOs. Never let the last spectrum band clip a bloom
     * generation or its full-output composite. */
    glDisable(GL_SCISSOR_TEST);
    int panel_width = (int)lroundf(rect->width);
    int panel_height = (int)lroundf(rect->height);
    if (panel_width < 1 || panel_height < 1) return false;
    struct luminophore_bloom_pyramid *bloom = widget_bloom_for(app, rect);
    if (bloom == NULL) return false;
    bool ready = luminophore_bloom_prepare(
        bloom, panel_width, panel_height,
        rect->radius, rect->outline, rect->extent
    );
    if (ready && bloom->dirty)
        ready = luminophore_bloom_generate(
            bloom, app->buffer, (float)bloom->padding,
            rect->radius, rect->outline
        );
    if (!ready) return false;
    double now_seconds = monotonic_seconds();
    float now = (float)now_seconds;
    float fraction = rect->reveal_duration <= 0.0 ? 1.0f :
        (float)fmin(1.0, fmax(0.0, (now_seconds - rect->reveal_started) / rect->reveal_duration));
    float eased = rect->reveal_to >= rect->reveal_from ?
        1.0f - powf(1.0f - fraction, 3.0f) : powf(fraction, 3.0f);
    float reveal = rect->reveal_from + (rect->reveal_to - rect->reveal_from) * eased;
    reveal = fminf(1.0f, fmaxf(0.0f, reveal));
    float hidden = 1.0f - reveal;
    float tile_x = rect->x - bloom->padding + rect->reveal_offset_x * hidden;
    float tile_y = output->height - rect->y - rect->height - bloom->padding -
                   rect->reveal_offset_y * hidden;
    return luminophore_bloom_composite(
        bloom, app->buffer, output->width, output->height,
        tile_x, tile_y, now, (float)(now - rect->activated_at),
        rect->phase, rect->intensity, reveal,
        rect->base[0], rect->base[1], rect->base[2]
    );
}

static float clamp01(float value) { return fminf(1.0f, fmaxf(0.0f, value)); }

static void update_live_spectrum(struct app *app) {
    if (!app->spectrum_live) return;
    double now = monotonic_seconds();
    if (now - app->last_spectrum_analysis < 1.0 / 120.0) return;
    float delta = app->last_spectrum_analysis > 0.0 ?
                  (float)fmin(0.1, now - app->last_spectrum_analysis) : 1.0f / 60.0f;
    app->last_spectrum_analysis = now;
    float samples[LUMINOPHORE_SPECTRUM_FFT_SIZE];
    float raw[SPECTRUM_BANDS] = {0};
    bool available = luminophore_pcm_ring_latest(&app->spectrum_ring, samples, LUMINOPHORE_SPECTRUM_FFT_SIZE);
    uint64_t sequence = atomic_load_explicit(&app->spectrum_ring.write_sequence, memory_order_acquire);
    if (available && sequence != app->last_spectrum_sequence) {
        app->last_spectrum_sequence = sequence;
        app->last_spectrum_audio_at = now;
    }
    bool fresh = available && app->last_spectrum_audio_at > 0.0 &&
                 now - app->last_spectrum_audio_at < 0.15;
    float rms = 0.0f;
    if (available) {
        for (size_t index = 0; index < LUMINOPHORE_SPECTRUM_FFT_SIZE; index++) rms += samples[index] * samples[index];
        rms = sqrtf(rms / LUMINOPHORE_SPECTRUM_FFT_SIZE);
        uint32_t rate = atomic_load_explicit(&app->audio_capture.sample_rate, memory_order_acquire);
        luminophore_spectrum_analyze(samples, rate ? rate : 48000, raw);
    }
    bool below_threshold = !fresh || rms < 0.001f;
    if (below_threshold) {
        if (app->silence_started_at <= 0.0) app->silence_started_at = now;
    } else {
        app->silence_started_at = 0.0;
    }
    bool silent = below_threshold && app->silence_started_at > 0.0 && now - app->silence_started_at >= 0.35;
    if (silent != app->spectrum_silent) {
        app->spectrum_silent = silent;
        printf("Q %d\n", silent ? 1 : 0);
        fflush(stdout);
    }
    float normalized[SPECTRUM_BANDS];
    for (int band = 0; band < SPECTRUM_BANDS; band++) {
        float decibels = 20.0f * log10f(raw[band] + 0.000001f);
        normalized[band] = silent ? 0.0f : sqrtf(clamp01((decibels + 60.0f) / 60.0f));
    }
    for (int band = 0; band < SPECTRUM_BANDS; band++) {
        float target = normalized[band];
        float tau = target > app->spectrum_bands[band] ? 0.090f : 0.280f;
        float blend = 1.0f - expf(-delta / tau);
        app->spectrum_bands[band] += (target - app->spectrum_bands[band]) * blend;
        if (target == 0.0f && app->spectrum_bands[band] < 0.0005f)
            app->spectrum_bands[band] = 0.0f;
    }
}

static void draw_spectrum(struct output_surface *output) {
    struct app *app = output->app;
    if (!app->spectrum_demo && !app->spectrum_live) return;
    const struct spectrum_output *config = NULL;
    for (size_t index = 0; index < app->spectrum_output_count; index++)
        if (!strcmp(app->spectrum_outputs[index].output, output->name)) {
            config = &app->spectrum_outputs[index]; break;
        }
    if (!config || config->desktop_width <= 0.0f || config->width <= 0.0f) return;
    glUseProgram(app->spectrum_program);
    glEnableVertexAttribArray(app->spectrum_position);
    glVertexAttribPointer(app->spectrum_position, 2, GL_FLOAT, GL_FALSE, 0, NULL);
    float now = (float)monotonic_seconds();
    float band_width = config->desktop_width / SPECTRUM_BANDS;
    for (int band = 0; band < SPECTRUM_BANDS; band++) {
        float global_left = config->desktop_left + band * band_width;
        float global_right = config->desktop_left + (band + 1) * band_width;
        float clipped_left = fmaxf(global_left, config->global_x);
        float clipped_right = fminf(global_right, config->global_x + config->width);
        if (clipped_right <= clipped_left) continue;
        float local_left = clipped_left - config->global_x;
        float gap = fminf(2.0f, band_width * 0.18f);
        int left = (int)ceilf(local_left + gap * 0.5f);
        int right = (int)floorf(clipped_right - config->global_x - gap * 0.5f);
        if (right <= left) continue;
        float level;
        if (app->spectrum_demo) {
            float slow = 0.5f + 0.5f * sinf(now * 1.7f + band * 0.31f);
            float sweep_position = fmodf(now * 0.11f, 1.0f);
            float distance = ((band + 0.5f) / SPECTRUM_BANDS) - sweep_position;
            float sweep = expf(-distance * distance * 90.0f);
            level = clamp01(0.12f + 0.58f * slow * slow + 0.30f * sweep);
        } else {
            level = clamp01(app->spectrum_bands[band]);
        }
        float max_height = fmaxf(2.0f, output->height / 3.0f);
        float height = 2.0f + level * (max_height - 2.0f);
        float center = ((clipped_left + clipped_right) * 0.5f - config->global_x) / config->width;
        float color[3];
        for (int channel = 0; channel < 3; channel++)
            color[channel] = config->left_color[channel] +
                             (config->right_color[channel] - config->left_color[channel]) * clamp01(center);
        float alpha = 0.94f;
        glScissor(left, 0, right - left, (int)ceilf(height + 1.0f));
        glUniform4f(app->spectrum_color, color[0] * alpha, color[1] * alpha, color[2] * alpha, alpha);
        glUniform1f(app->spectrum_height, height);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    glDisableVertexAttribArray(app->spectrum_position);
}

static bool rect_is_visible_or_transitioning(const struct glow_rect *rect, double now) {
    bool transitioning = rect->reveal_duration > 0.0 &&
                         now < rect->reveal_started + rect->reveal_duration;
    return transitioning || rect->reveal_to > 0.0005f;
}

static bool output_has_animated_widget(const struct output_surface *output, double now) {
    const struct app *app = output->app;
    for (size_t index = 0; index < app->rect_count; index++)
        if (!strcmp(app->rects[index].output, output->name) &&
            app->rects[index].top == output->top &&
            rect_is_visible_or_transitioning(&app->rects[index], now))
            return true;
    return false;
}

static bool spectrum_has_visible_energy(const struct app *app) {
    if (!app->spectrum_demo && !app->spectrum_live) return false;
    if (app->spectrum_demo || !app->spectrum_silent) return true;
    for (int band = 0; band < SPECTRUM_BANDS; band++)
        if (app->spectrum_bands[band] > 0.0005f) return true;
    return false;
}

static bool output_has_spectrum(const struct output_surface *output) {
    if (output->top) return false;
    const struct app *app = output->app;
    for (size_t index = 0; index < app->spectrum_output_count; index++)
        if (!strcmp(app->spectrum_outputs[index].output, output->name)) return true;
    return false;
}

static double output_frame_interval(const struct output_surface *output, double now) {
    if (output_has_spectrum(output) && spectrum_has_visible_energy(output->app))
        return SPECTRUM_FRAME_INTERVAL;
    if (output_has_animated_widget(output, now))
        return WIDGET_FRAME_INTERVAL;
    return 0.0;
}

static void arm_scheduler(struct app *app, double delay) {
    if (app->scheduler_fd < 0) return;
    if (delay < 0.0005) delay = 0.0005;
    struct itimerspec timer = {0};
    timer.it_value.tv_sec = (time_t)delay;
    timer.it_value.tv_nsec = (long)((delay - timer.it_value.tv_sec) * 1000000000.0);
    timerfd_settime(app->scheduler_fd, 0, &timer, NULL);
}

static void render_output(struct output_surface *output) {
    struct app *app = output->app;
    if (!output->configured || output->egl_surface == EGL_NO_SURFACE || output->frame_callback) return;
    /* Wayland frame traffic can keep the display fd continuously busy.  Drain
     * control updates here as well as in poll() so the newest widget geometry
     * is applied before the next presented frame instead of stalling in the
     * producer pipe behind an old rectangle. */
    if (app->accept_stdin) read_commands(app);
    if (!eglMakeCurrent(app->egl_display, output->egl_surface, output->egl_surface, app->egl_context)) {
        fail(app, "eglMakeCurrent failed"); return;
    }
    glViewport(0, 0, output->width, output->height);
    glClearColor(0, 0, 0, 0); glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA); glEnable(GL_SCISSOR_TEST);
    glUseProgram(app->program); glBindBuffer(GL_ARRAY_BUFFER, app->buffer);
    glEnableVertexAttribArray(app->position);
    glVertexAttribPointer(app->position, 2, GL_FLOAT, GL_FALSE, 0, NULL);
    glUniform2f(app->viewport, output->width, output->height);
    glUniform1f(app->time_uniform, (float)monotonic_seconds());
    if (!output->top) {
        update_live_spectrum(app);
        draw_spectrum(output);
    }
    glUseProgram(app->program);
    glEnableVertexAttribArray(app->position);
    glVertexAttribPointer(app->position, 2, GL_FLOAT, GL_FALSE, 0, NULL);
    glUniform2f(app->viewport, output->width, output->height);
    glUniform1f(app->time_uniform, (float)monotonic_seconds());
    for (size_t i = 0; i < app->rect_count; i++) {
        if (!strcmp(app->rects[i].output, output->name) &&
            app->rects[i].top == output->top) {
            if (app->widget_bloom)
                draw_bloom_rect(output, &app->rects[i]);
            else
                draw_rect(output, &app->rects[i]);
            if (app->rects[i].revision > app->rendered_revision)
                app->rendered_revision = app->rects[i].revision;
            struct widget_bloom_slot *slot = widget_bloom_slot_for(app, &app->rects[i]);
            if (slot != NULL && slot->reported_revision != app->rects[i].revision) {
                slot->reported_revision = app->rects[i].revision;
                printf("G %s %llu %llu\n", app->rects[i].id,
                       (unsigned long long)app->rects[i].revision,
                       (unsigned long long)app->rects[i].revision);
                fflush(stdout);
            }
        }
    }
    glDisable(GL_SCISSOR_TEST);
    glDisableVertexAttribArray(app->position);
    output->frame_callback = wl_surface_frame(output->surface);
    wl_callback_add_listener(output->frame_callback, &frame_listener, output);
    if (!eglSwapBuffers(app->egl_display, output->egl_surface)) fail(app, "eglSwapBuffers failed");
    output->last_render_at = monotonic_seconds();
    output->force_pending = false;
}

static void pump_outputs(struct app *app) {
    double now = monotonic_seconds();
    update_live_spectrum(app);
    double next_delay = app->spectrum_live ? IDLE_AUDIO_INTERVAL : 0.0;
    if (app->scheduler_fd >= 0) {
        struct itimerspec disarmed = {0};
        timerfd_settime(app->scheduler_fd, 0, &disarmed, NULL);
    }

    for (struct output_surface *output = app->outputs; output; output = output->next) {
        if (!output->configured || output->closed) continue;
        double interval = output_frame_interval(output, now);
        bool due = output->force_pending ||
                   (interval > 0.0 && now - output->last_render_at >= interval);
        if (due && output->frame_callback == NULL) {
            render_output(output);
            now = monotonic_seconds();
            interval = output_frame_interval(output, now);
        }
        if (interval > 0.0 && output->frame_callback == NULL) {
            double remaining = interval - (now - output->last_render_at);
            if (remaining < 0.0005) remaining = 0.0005;
            if (next_delay <= 0.0 || remaining < next_delay) next_delay = remaining;
        }
    }

    if (next_delay > 0.0) arm_scheduler(app, next_delay);
}

static void layer_configure(void *data, struct zwlr_layer_surface_v1 *surface,
                            uint32_t serial, uint32_t width, uint32_t height) {
    struct output_surface *output = data;
    zwlr_layer_surface_v1_ack_configure(surface, serial);
    if (!width || !height) return;
    output->width = (int32_t)width; output->height = (int32_t)height;
    if (!output->egl_window) {
        output->egl_window = wl_egl_window_create(output->surface, output->width, output->height);
        output->egl_surface = eglCreateWindowSurface(
            output->app->egl_display, output->app->egl_config,
            (EGLNativeWindowType)output->egl_window, NULL
        );
        if (output->egl_surface == EGL_NO_SURFACE) { fail(output->app, "eglCreateWindowSurface failed"); return; }
    } else {
        wl_egl_window_resize(output->egl_window, output->width, output->height, 0, 0);
    }
    output->configured = true;
    render_output(output);
}

static void layer_closed(void *data, struct zwlr_layer_surface_v1 *surface) {
    (void)surface;
    struct output_surface *output = data;
    output->closed = true;
}

static const struct zwlr_layer_surface_v1_listener layer_listener = {
    .configure = layer_configure,
    .closed = layer_closed,
};

static void output_geometry(void *data, struct wl_output *output, int32_t x, int32_t y,
                            int32_t physical_width, int32_t physical_height, int32_t subpixel,
                            const char *make, const char *model, int32_t transform) {
    (void)data; (void)output; (void)x; (void)y; (void)physical_width; (void)physical_height;
    (void)subpixel; (void)make; (void)model; (void)transform;
}
static void output_mode(void *data, struct wl_output *output, uint32_t flags,
                        int32_t width, int32_t height, int32_t refresh) {
    (void)data; (void)output; (void)flags; (void)width; (void)height; (void)refresh;
}
static void output_done(void *data, struct wl_output *output) { (void)data; (void)output; }
static void output_scale(void *data, struct wl_output *output, int32_t factor) {
    (void)data; (void)output; (void)factor;
}
static void output_name(void *data, struct wl_output *output, const char *name) {
    (void)output;
    struct output_surface *surface = data;
    snprintf(surface->name, sizeof(surface->name), "%s", name);
}
static void output_description(void *data, struct wl_output *output, const char *description) {
    (void)data; (void)output; (void)description;
}
static const struct wl_output_listener output_listener = {
    .geometry = output_geometry, .mode = output_mode, .done = output_done, .scale = output_scale,
    .name = output_name, .description = output_description,
};

static void create_output_surface(struct app *app, struct output_surface *output) {
    output->surface = wl_compositor_create_surface(app->compositor);
    struct wl_region *empty = wl_compositor_create_region(app->compositor);
    wl_surface_set_input_region(output->surface, empty);
    wl_region_destroy(empty);
    output->layer_surface = zwlr_layer_shell_v1_get_layer_surface(
        app->layer_shell, output->surface, output->output,
        output->top ? ZWLR_LAYER_SHELL_V1_LAYER_TOP :
        ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM,
        app->window_demo ?
            (output->top ? "luminophore-window-glow-demo-top" : "luminophore-window-glow-demo") :
        output->top ? "luminophore-native-bloom-top" :
        app->widget_bloom ? "luminophore-native-bloom" : "luminophore-glow-layer"
    );
    zwlr_layer_surface_v1_add_listener(output->layer_surface, &layer_listener, output);
    zwlr_layer_surface_v1_set_anchor(
        output->layer_surface,
        ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT
    );
    zwlr_layer_surface_v1_set_size(output->layer_surface, 0, 0);
    zwlr_layer_surface_v1_set_exclusive_zone(output->layer_surface, -1);
    zwlr_layer_surface_v1_set_keyboard_interactivity(
        output->layer_surface, ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE
    );
    wl_surface_commit(output->surface);
}

static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
                            const char *interface, uint32_t version) {
    struct app *app = data;
    if (!strcmp(interface, wl_compositor_interface.name)) {
        app->compositor = wl_registry_bind(registry, name, &wl_compositor_interface, version < 6 ? version : 6);
    } else if (!strcmp(interface, zwlr_layer_shell_v1_interface.name)) {
        app->layer_shell = wl_registry_bind(registry, name, &zwlr_layer_shell_v1_interface, version < 4 ? version : 4);
    } else if (!strcmp(interface, wl_output_interface.name)) {
        struct output_surface *output = calloc(1, sizeof(*output));
        output->app = app; output->egl_surface = EGL_NO_SURFACE;
        output->output = wl_registry_bind(registry, name, &wl_output_interface, version < 4 ? version : 4);
        wl_output_add_listener(output->output, &output_listener, output);
        output->next = app->outputs; app->outputs = output;
        if (app->widget_bloom) {
            struct output_surface *top = calloc(1, sizeof(*top));
            if (!top) return;
            top->app = app; top->egl_surface = EGL_NO_SURFACE; top->top = true;
            top->output = wl_registry_bind(
                registry, name, &wl_output_interface, version < 4 ? version : 4
            );
            wl_output_add_listener(top->output, &output_listener, top);
            top->next = app->outputs; app->outputs = top;
        }
    }
}

static void registry_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}

static const struct wl_registry_listener registry_listener = {
    .global = registry_global,
    .global_remove = registry_remove,
};

static void destroy_output(struct output_surface *output) {
    if (output->frame_callback) wl_callback_destroy(output->frame_callback);
    if (output->egl_surface != EGL_NO_SURFACE) eglDestroySurface(output->app->egl_display, output->egl_surface);
    if (output->egl_window) wl_egl_window_destroy(output->egl_window);
    if (output->layer_surface) zwlr_layer_surface_v1_destroy(output->layer_surface);
    if (output->surface) wl_surface_destroy(output->surface);
    if (output->output) wl_output_destroy(output->output);
    free(output);
}

static void signal_stop(int signal_number) {
    (void)signal_number; stop_requested = 1;
}

static void parse_command(struct app *app, char *line) {
    if (!strncmp(line, "V ", 2)) {
        if (atoi(line + 2) != 5) fail(app, "unsupported IPC protocol version");
        return;
    }
    if (!strcmp(line, "B")) {
        app->pending_count = 0;
        app->pending_spectrum_output_count = 0;
        return;
    }
    if (!strcmp(line, "C")) {
        double now = monotonic_seconds();
        for (size_t pending = 0; pending < app->pending_count; pending++) {
            app->pending[pending].activated_at = now;
            for (size_t active = 0; active < app->rect_count; active++) {
                if (!strcmp(app->pending[pending].output, app->rects[active].output) &&
                    !strcmp(app->pending[pending].id, app->rects[active].id)) {
                    app->pending[pending].activated_at = app->rects[active].activated_at;
                    break;
                }
            }
        }
        memcpy(app->rects, app->pending, app->pending_count * sizeof(app->rects[0]));
        app->rect_count = app->pending_count;
        app->accepted_revision = 0;
        for (size_t index = 0; index < app->rect_count; index++)
            if (app->rects[index].revision > app->accepted_revision)
                app->accepted_revision = app->rects[index].revision;
        for (size_t index = 0; index < app->rect_count; index++)
            printf("G %s %llu 0\n", app->rects[index].id,
                   (unsigned long long)app->rects[index].revision);
        fflush(stdout);
        memcpy(app->spectrum_outputs, app->pending_spectrum_outputs,
               app->pending_spectrum_output_count * sizeof(app->spectrum_outputs[0]));
        app->spectrum_output_count = app->pending_spectrum_output_count;
        for (struct output_surface *output = app->outputs; output; output = output->next)
            output->force_pending = true;
        return;
    }
    if (line[0] == 'M' && line[1] == ' ' &&
        app->pending_spectrum_output_count < MAX_SPECTRUM_OUTPUTS) {
        struct spectrum_output output = {0};
        int count = sscanf(line + 2, "%63s %f %f %f %f %f %f %f %f %f %f",
            output.output, &output.global_x, &output.width,
            &output.desktop_left, &output.desktop_width,
            &output.left_color[0], &output.left_color[1], &output.left_color[2],
            &output.right_color[0], &output.right_color[1], &output.right_color[2]);
        if (count == 11) app->pending_spectrum_outputs[app->pending_spectrum_output_count++] = output;
        return;
    }
    if (line[0] != 'R' || line[1] != ' ' || app->pending_count >= MAX_GLOW_RECTS) return;
    struct glow_rect rect = {0};
    char layer[8] = {0};
    unsigned long long revision = 0;
    int count = sscanf(
        line + 2,
        "%63s %63s %7s %llu %f %f %f %f %f %f %f %f %f %f %f %lf %lf %f %f %f %f %f %f %f %f %f %f %f %f",
        rect.output, rect.id, layer, &revision,
        &rect.x, &rect.y, &rect.width, &rect.height,
        &rect.radius, &rect.outline, &rect.extent, &rect.intensity, &rect.phase,
        &rect.reveal_from, &rect.reveal_to,
        &rect.reveal_started, &rect.reveal_duration,
        &rect.reveal_offset_x, &rect.reveal_offset_y,
        &rect.base[0], &rect.base[1], &rect.base[2],
        &rect.core[0], &rect.core[1], &rect.core[2],
        &rect.edge[0], &rect.edge[1], &rect.edge[2], &rect.edge[3]
    );
    if (count == 29 && (!strcmp(layer, "bottom") || !strcmp(layer, "top"))) {
        rect.revision = (uint64_t)revision;
        rect.top = !strcmp(layer, "top");
        app->pending[app->pending_count++] = rect;
    }
}

static bool read_commands(struct app *app) {
    ssize_t count = read(STDIN_FILENO, app->input_buffer + app->input_length,
                         sizeof(app->input_buffer) - app->input_length - 1);
    if (count == 0) return false;
    if (count < 0) return errno == EINTR || errno == EAGAIN;
    app->input_length += (size_t)count;
    app->input_buffer[app->input_length] = '\0';
    char *start = app->input_buffer;
    char *newline;
    while ((newline = strchr(start, '\n'))) {
        *newline = '\0'; parse_command(app, start); start = newline + 1;
    }
    size_t remaining = app->input_buffer + app->input_length - start;
    memmove(app->input_buffer, start, remaining); app->input_length = remaining;
    if (app->input_length == sizeof(app->input_buffer) - 1) app->input_length = 0;
    return true;
}

int main(int argc, char **argv) {
    struct app app = {
        .egl_display = EGL_NO_DISPLAY,
        .egl_context = EGL_NO_CONTEXT,
        .running = true,
        .scheduler_fd = -1,
    };
    pw_init(&argc, &argv);
    luminophore_pcm_ring_init(&app.spectrum_ring);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--widget-bloom")) app.widget_bloom = true;
        else if (!strcmp(argv[i], "--window-demo")) app.window_demo = true;
        else if (!strcmp(argv[i], "--spectrum-demo")) app.spectrum_demo = true;
        else if (!strcmp(argv[i], "--spectrum-live")) app.spectrum_live = true;
        else if (!strcmp(argv[i], "--help")) {
            puts("usage: luminophore-glow-layer [--widget-bloom] [--window-demo] [--spectrum-demo] [--spectrum-live]"); return 0;
        } else { fprintf(stderr, "unknown argument: %s\n", argv[i]); return 2; }
    }
    signal(SIGINT, signal_stop); signal(SIGTERM, signal_stop);
    app.display = wl_display_connect(NULL);
    if (!app.display) { perror("wl_display_connect"); return 1; }
    app.registry = wl_display_get_registry(app.display);
    wl_registry_add_listener(app.registry, &registry_listener, &app);
    wl_display_roundtrip(app.display);
    wl_display_roundtrip(app.display);
    if (!app.compositor || !app.layer_shell || !app.outputs) {
        fail(&app, "required Wayland globals unavailable"); goto cleanup;
    }
    if (!init_gl(&app)) { fail(&app, "EGL/GLES initialization failed"); goto cleanup; }
    app.scheduler_fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    if (app.scheduler_fd < 0) { fail(&app, "timerfd_create failed"); goto cleanup; }
    if (app.spectrum_live && !luminophore_audio_capture_start(&app.audio_capture, &app.spectrum_ring))
        fprintf(stderr, "luminophore-glow-layer: PipeWire spectrum capture unavailable\n");
    for (struct output_surface *output = app.outputs; output; output = output->next)
        create_output_surface(&app, output);
    wl_display_flush(app.display);
    int wayland_fd = wl_display_get_fd(app.display);
    app.accept_stdin = !app.spectrum_demo;
    if (app.accept_stdin) {
        int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
        if (flags >= 0) fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
    }
    while (app.running && !stop_requested) {
        if (app.spectrum_live &&
            atomic_load_explicit(&app.audio_capture.failed, memory_order_acquire) &&
            monotonic_seconds() >= app.next_audio_reconnect) {
            luminophore_audio_capture_stop(&app.audio_capture);
            app.audio_reconnect_count++;
            double delay = app.audio_reconnect_count == 1 ? 1.0 :
                           app.audio_reconnect_count == 2 ? 2.0 : 5.0;
            if (luminophore_audio_capture_start(&app.audio_capture, &app.spectrum_ring))
                app.audio_reconnect_count = 0;
            else
                app.next_audio_reconnect = monotonic_seconds() + delay;
        }
        if (wl_display_dispatch_pending(app.display) < 0 || wl_display_flush(app.display) < 0) break;
        struct pollfd descriptors[3] = {
            {.fd = wayland_fd, .events = POLLIN},
            {.fd = app.accept_stdin ? STDIN_FILENO : -1, .events = POLLIN},
            {.fd = app.scheduler_fd, .events = POLLIN},
        };
        int result = poll(descriptors, 3, -1);
        if (result < 0) { if (errno == EINTR) continue; break; }
        if (descriptors[1].revents & (POLLHUP | POLLERR)) break;
        if ((descriptors[1].revents & POLLIN) && !read_commands(&app)) break;
        if ((descriptors[0].revents & POLLIN) && wl_display_dispatch(app.display) < 0) break;
        if (descriptors[2].revents & POLLIN) {
            uint64_t expirations;
            while (read(app.scheduler_fd, &expirations, sizeof(expirations)) < 0 && errno == EINTR) {}
        }
        pump_outputs(&app);
    }

cleanup:
    if (app.scheduler_fd >= 0) close(app.scheduler_fd);
    luminophore_audio_capture_stop(&app.audio_capture);
    if (app.widget_bloom && app.egl_display != EGL_NO_DISPLAY &&
        app.egl_context != EGL_NO_CONTEXT)
        for (size_t index = 0; index < MAX_GLOW_RECTS; index++)
            if (app.widget_blooms[index].initialized)
                luminophore_bloom_destroy(&app.widget_blooms[index].bloom);
    while (app.outputs) {
        struct output_surface *next = app.outputs->next;
        destroy_output(app.outputs); app.outputs = next;
    }
    if (app.program) glDeleteProgram(app.program);
    if (app.spectrum_program) glDeleteProgram(app.spectrum_program);
    if (app.buffer) glDeleteBuffers(1, &app.buffer);
    if (app.egl_context != EGL_NO_CONTEXT) eglDestroyContext(app.egl_display, app.egl_context);
    if (app.egl_display != EGL_NO_DISPLAY) eglTerminate(app.egl_display);
    if (app.layer_shell) zwlr_layer_shell_v1_destroy(app.layer_shell);
    if (app.compositor) wl_compositor_destroy(app.compositor);
    if (app.registry) wl_registry_destroy(app.registry);
    if (app.display) wl_display_disconnect(app.display);
    pw_deinit();
    return app.exit_code;
}
