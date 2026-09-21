#define _POSIX_C_SOURCE 200809L
#include "background/render.h"
#include "presentation-time.h"
#include "wlr-layer-shell.h"
#include <errno.h>
#include <math.h>
#include <png.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <wayland-egl.h>
#ifndef BG_BUILD
#define BG_BUILD "development"
#endif
struct app;
struct output {
  struct app *app;
  uint32_t id;
  struct wl_output *output;
  char name[256];
  int scale, width, height, selected, configured;
  struct wl_surface *surface;
  struct zwlr_layer_surface_v1 *layer;
  struct wl_egl_window *window;
  EGLSurface egl;
  struct wl_callback *frame;
  struct wp_presentation_feedback *feedback;
  unsigned long feedback_generation;
  struct bg_scene current, next;
  struct bg_target target;
  int presented;
};
struct app {
  struct wl_display *display;
  struct wl_registry *registry;
  struct wl_compositor *compositor;
  struct zwlr_layer_shell_v1 *shell;
  struct wp_presentation *presentation;
  struct bg_renderer renderer;
  struct output outputs[16];
  int count, prepared, transition, stopped;
  unsigned long generation;
  double start, duration;
};
static volatile sig_atomic_t stopping;
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec / 1e9;
}
static void signal_stop(int signum) {
  (void)signum;
  stopping = 1;
}
static void event(struct app *a, const char *type, const char *detail) {
  json_object *j = json_object_new_object();
  json_object_object_add(j, "event", json_object_new_string(type));
  json_object_object_add(j, "generation", json_object_new_int64(a->generation));
  if (detail)
    json_object_object_add(j, "detail", json_object_new_string(detail));
  puts(json_object_to_json_string_ext(j, JSON_C_TO_STRING_PLAIN));
  fflush(stdout);
  json_object_put(j);
}
static void render(struct output *o);
static void feedback_sync(void *data, struct wp_presentation_feedback *f,
                          struct wl_output *out) {
  (void)data;
  (void)f;
  (void)out;
}
static void feedback_presented(void *data, struct wp_presentation_feedback *f,
                               uint32_t hi, uint32_t lo, uint32_t ns,
                               uint32_t refresh, uint32_t shi, uint32_t slo,
                               uint32_t flags) {
  (void)hi;
  (void)lo;
  (void)ns;
  (void)refresh;
  (void)shi;
  (void)slo;
  (void)flags;
  struct output *o = data;
  wp_presentation_feedback_destroy(f);
  o->feedback = NULL;
  if (o->feedback_generation != o->app->generation)
    return;
  o->presented = 1;
  event(o->app, "PRESENTED", o->name);
  int all = 1;
  for (int i = 0; i < o->app->count; i++)
    if (o->app->outputs[i].selected && !o->app->outputs[i].presented)
      all = 0;
  if (all) {
    o->app->transition = 0;
    o->app->prepared = 0;
    event(o->app, "COMMITTED", NULL);
  }
}
static void feedback_discarded(void *data, struct wp_presentation_feedback *f) {
  struct output *o = data;
  wp_presentation_feedback_destroy(f);
  o->feedback = NULL;
  event(o->app, "FAILED", "presentation_discarded");
  o->app->stopped = 1;
}
static const struct wp_presentation_feedback_listener feedback_listener = {
    .sync_output = feedback_sync,
    .presented = feedback_presented,
    .discarded = feedback_discarded};
static void frame_done(void *data, struct wl_callback *cb, uint32_t stamp) {
  (void)stamp;
  struct output *o = data;
  wl_callback_destroy(cb);
  o->frame = NULL;
  if (o->app->transition || o->current.motion > 0)
    render(o);
}
static const struct wl_callback_listener frame_listener = {.done = frame_done};
static void render(struct output *o) {
  struct app *a = o->app;
  if (!o->configured || o->frame || (!o->current.count && !a->transition))
    return;
  if (!eglMakeCurrent(a->renderer.display, o->egl, o->egl,
                      a->renderer.context)) {
    a->stopped = 1;
    return;
  }
  int w = o->width * o->scale, h = o->height * o->scale;
  glViewport(0, 0, w, h);
  glClearColor(0, 0, 0, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  double t = now(),
         progress = a->duration > 0
                        ? fmin(1, fmax(0, (t - a->start) / a->duration))
                        : 1;
  bg_draw(&a->renderer, &o->current, w, h, 1, t);
  if (a->transition && o->next.count &&
      !bg_draw_transition(&a->renderer, &o->target, &o->next, w, h,
                          (float)progress, t)) {
    event(a, "FAILED", "render_target_failed");
    a->stopped = 1;
    return;
  }
  if (a->transition && progress >= 1 && o->next.count) {
    bg_scene_free(&o->current);
    o->current = o->next;
    memset(&o->next, 0, sizeof(o->next));
  }
  if (a->transition && progress >= 1 && !o->presented && !o->feedback) {
    o->feedback = wp_presentation_feedback(a->presentation, o->surface);
    o->feedback_generation = a->generation;
    wp_presentation_feedback_add_listener(o->feedback, &feedback_listener, o);
  }
  o->frame = wl_surface_frame(o->surface);
  wl_callback_add_listener(o->frame, &frame_listener, o);
  if (!eglSwapBuffers(a->renderer.display, o->egl)) {
    event(a, "FAILED", "swap_failed");
    a->stopped = 1;
  }
}
static void ready(struct app *a) {
  if (a->prepared != 1)
    return;
  for (int i = 0; i < a->count; i++)
    if (a->outputs[i].selected && !a->outputs[i].configured)
      return;
  /* Allocate and validate composition targets before authorizing COMMIT. */
  if (!eglMakeCurrent(a->renderer.display, a->renderer.scratch,
                      a->renderer.scratch, a->renderer.context)) {
    event(a, "FAILED", "prepare_context_failed");
    a->prepared = 0;
    return;
  }
  for (int i = 0; i < a->count; i++) {
    struct output *o = &a->outputs[i];
    if (!o->selected)
      continue;
    int w = o->width * o->scale, h = o->height * o->scale;
    glViewport(0, 0, w, h);
    if (!bg_draw_transition(&a->renderer, &o->target, &o->next, w, h, 0, 0)) {
      event(a, "FAILED", "prepare_target_failed");
      a->prepared = 0;
      return;
    }
  }
  glFinish();
  event(a, "READY", NULL);
  a->prepared = 2;
}
static void configure(void *data, struct zwlr_layer_surface_v1 *layer,
                      uint32_t serial, uint32_t w, uint32_t h) {
  struct output *o = data;
  zwlr_layer_surface_v1_ack_configure(layer, serial);
  if (!w || !h || w > 16384 || h > 16384) {
    o->app->stopped = 1;
    return;
  }
  if (o->configured && (o->width != (int)w || o->height != (int)h)) {
    event(o->app, "FAILED", "topology_changed");
    o->app->stopped = 1;
    return;
  }
  o->width = w;
  o->height = h;
  if (!o->window) {
    o->window = wl_egl_window_create(o->surface, w * o->scale, h * o->scale);
    o->egl = eglCreateWindowSurface(o->app->renderer.display,
                                    o->app->renderer.config,
                                    (EGLNativeWindowType)o->window, NULL);
  } else
    wl_egl_window_resize(o->window, w * o->scale, h * o->scale, 0, 0);
  wl_surface_set_buffer_scale(o->surface, o->scale);
  o->configured = 1;
  ready(o->app);
  render(o);
}
static void closed(void *data, struct zwlr_layer_surface_v1 *layer) {
  (void)layer;
  struct output *o = data;
  event(o->app, "FAILED", "output_closed");
  o->app->stopped = 1;
}
static const struct zwlr_layer_surface_v1_listener layer_listener = {
    .configure = configure, .closed = closed};
static void geometry(void *d, struct wl_output *o, int32_t x, int32_t y,
                     int32_t w, int32_t h, int32_t sub, const char *make,
                     const char *model, int32_t transform) {
  (void)d;
  (void)o;
  (void)x;
  (void)y;
  (void)w;
  (void)h;
  (void)sub;
  (void)make;
  (void)model;
  (void)transform;
}
static void mode(void *d, struct wl_output *o, uint32_t f, int32_t w, int32_t h,
                 int32_t rate) {
  (void)d;
  (void)o;
  (void)f;
  (void)w;
  (void)h;
  (void)rate;
}
static void output_done(void *d, struct wl_output *o) {
  (void)d;
  (void)o;
}
static void scale(void *d, struct wl_output *o, int32_t scale) {
  (void)o;
  struct output *out = d;
  if (scale < 1 || scale > 8) {
    out->app->stopped = 1;
    return;
  }
  if (out->configured && out->scale != scale) {
    event(out->app, "FAILED", "topology_changed");
    out->app->stopped = 1;
  }
  out->scale = scale;
}
static void name(void *d, struct wl_output *o, const char *value) {
  (void)o;
  struct output *out = d;
  snprintf(out->name, sizeof(out->name), "%s", value);
}
static void description(void *d, struct wl_output *o, const char *s) {
  (void)d;
  (void)o;
  (void)s;
}
static const struct wl_output_listener output_listener = {.geometry = geometry,
                                                          .mode = mode,
                                                          .done = output_done,
                                                          .scale = scale,
                                                          .name = name,
                                                          .description =
                                                              description};
static void clock_id(void *d, struct wp_presentation *p, uint32_t clock) {
  (void)d;
  (void)p;
  (void)clock;
}
static const struct wp_presentation_listener presentation_listener = {
    .clock_id = clock_id};
static void global(void *data, struct wl_registry *reg, uint32_t id,
                   const char *iface, uint32_t version) {
  struct app *a = data;
  if (!strcmp(iface, "wl_compositor"))
    a->compositor = wl_registry_bind(reg, id, &wl_compositor_interface, 4);
  else if (!strcmp(iface, "zwlr_layer_shell_v1"))
    a->shell = wl_registry_bind(reg, id, &zwlr_layer_shell_v1_interface,
                                version < 4 ? version : 4);
  else if (!strcmp(iface, "wp_presentation")) {
    a->presentation = wl_registry_bind(reg, id, &wp_presentation_interface, 1);
    wp_presentation_add_listener(a->presentation, &presentation_listener, a);
  } else if (!strcmp(iface, "wl_output") && version >= 4 && a->count < 16) {
    struct output *o = &a->outputs[a->count++];
    o->id = id;
    o->app = a;
    o->scale = 1;
    o->output = wl_registry_bind(reg, id, &wl_output_interface, 4);
    wl_output_add_listener(o->output, &output_listener, o);
    if (a->generation) {
      event(a, "FAILED", "topology_changed");
      a->stopped = 1;
    }
  }
}
static void removed(void *data, struct wl_registry *reg, uint32_t id) {
  (void)reg;
  struct app *a = data;
  for (int i = 0; i < a->count; i++)
    if (a->outputs[i].id == id) {
      event(a, "FAILED", "topology_changed");
      a->stopped = 1;
    }
}
static const struct wl_registry_listener registry_listener = {
    .global = global, .global_remove = removed};
static void create_surface(struct output *o) {
  struct app *a = o->app;
  o->surface = wl_compositor_create_surface(a->compositor);
  struct wl_region *empty = wl_compositor_create_region(a->compositor);
  wl_surface_set_input_region(o->surface, empty);
  wl_region_destroy(empty);
  o->layer = zwlr_layer_shell_v1_get_layer_surface(
      a->shell, o->surface, o->output, ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND,
      "luminophore-background-layer");
  zwlr_layer_surface_v1_add_listener(o->layer, &layer_listener, o);
  zwlr_layer_surface_v1_set_anchor(o->layer, 15);
  zwlr_layer_surface_v1_set_size(o->layer, 0, 0);
  zwlr_layer_surface_v1_set_exclusive_zone(o->layer, -1);
  zwlr_layer_surface_v1_set_keyboard_interactivity(
      o->layer, ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE);
  wl_surface_commit(o->surface);
}
static void command(struct app *a, const char *line) {
  json_object *j = json_tokener_parse(line);
  if (!j) {
    event(a, "FAILED", "invalid_json");
    return;
  }
  const char *cmd =
      json_object_get_string(json_object_object_get(j, "command"));
  unsigned long generation =
      json_object_get_int64(json_object_object_get(j, "generation"));
  if (!cmd ||
      !json_object_is_type(json_object_object_get(j, "generation"),
                           json_type_int) ||
      !generation || generation > 9007199254740991UL) {
    event(a, "FAILED", "invalid_command");
    goto end;
  }
  if (!strcmp(cmd, "PREPARE") || !strcmp(cmd, "FALLBACK")) {
    if (a->transition || generation <= a->generation) {
      event(a, "FAILED", "stale_or_busy");
      goto end;
    }
    a->generation = generation;
    a->prepared = 0;
    for (int i = 0; i < a->count; i++) {
      bg_scene_free(&a->outputs[i].next);
      a->outputs[i].selected = 0;
    }
    json_object *rows = json_object_object_get(j, "outputs");
    if (!rows || !json_object_is_type(rows, json_type_array) ||
        (json_object_array_length(rows) != 2 && strcmp(cmd, "FALLBACK")) ||
        json_object_array_length(rows) < 1 ||
        json_object_array_length(rows) > 16) {
      event(a, "FAILED", "output_count");
      goto end;
    }
    for (size_t k = 0; k < json_object_array_length(rows); k++) {
      json_object *row = json_object_array_get_idx(rows, k);
      const char *connector =
          json_object_get_string(json_object_object_get(row, "connector"));
      struct output *found = NULL;
      for (int i = 0; i < a->count; i++)
        if (connector && !strcmp(connector, a->outputs[i].name))
          found = &a->outputs[i];
      if (!found || found->selected ||
          !bg_scene_load(&a->renderer, &found->next, row)) {
        event(a, "FAILED", "asset_or_output_invalid");
        for (int i = 0; i < a->count; i++)
          bg_scene_free(&a->outputs[i].next);
        goto end;
      }
      found->selected = 1;
    }
    a->prepared = 1;
    for (int i = 0; i < a->count; i++)
      if (a->outputs[i].selected && !a->outputs[i].surface)
        create_surface(&a->outputs[i]);
    ready(a);
  } else if (!strcmp(cmd, "COMMIT") && generation == a->generation &&
             a->prepared == 2 && !a->transition) {
    double duration =
        json_object_get_double(json_object_object_get(j, "duration_ms"));
    if (!isfinite(duration) || duration < 0 || duration > 5000) {
      event(a, "FAILED", "duration");
      goto end;
    }
    a->duration = duration / 1000;
    a->start = now();
    a->transition = 1;
    for (int i = 0; i < a->count; i++)
      if (a->outputs[i].selected) {
        a->outputs[i].presented = 0;
        render(&a->outputs[i]);
      }
  } else if (!strcmp(cmd, "ABORT") && generation == a->generation &&
             !a->transition) {
    a->prepared = 0;
    for (int i = 0; i < a->count; i++)
      bg_scene_free(&a->outputs[i].next);
    event(a, "ABORTED", NULL);
  } else
    event(a, "FAILED", "invalid_phase");
end:
  json_object_put(j);
}
static int preview(const char *input, const char *output) {
  struct bg_renderer r;
  if (!bg_renderer_init(&r, NULL))
    return 2;
  json_object *j = json_object_from_file(input);
  struct bg_scene scene;
  if (!j || !bg_scene_load(&r, &scene, j))
    return 3;
  const int w = 640, h = 360;
  GLuint tex, fb;
  glGenTextures(1, &tex);
  glBindTexture(GL_TEXTURE_2D, tex);
  glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE,
               NULL);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glGenFramebuffers(1, &fb);
  glBindFramebuffer(GL_FRAMEBUFFER, fb);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                         tex, 0);
  if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
    return 4;
  glViewport(0, 0, w, h);
  glClearColor(0, 0, 0, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  double phase =
      json_object_get_double(json_object_object_get(j, "preview_time"));
  if (!isfinite(phase))
    return 3;
  json_object *previous = json_object_object_get(j, "previous");
  if (previous) {
    struct bg_scene old;
    struct bg_target target = {0};
    if (!bg_scene_load(&r, &old, previous))
      return 3;
    glBindFramebuffer(GL_FRAMEBUFFER, fb);
    bg_draw(&r, &old, w, h, 1, phase);
    float progress =
        json_object_get_double(json_object_object_get(j, "progress"));
    if (!isfinite(progress) || progress < 0 || progress > 1 ||
        !bg_draw_transition(&r, &target, &scene, w, h, progress, phase))
      return 4;
    bg_target_free(&target);
    bg_scene_free(&old);
  } else
    bg_draw(&r, &scene, w, h, 1, phase);
  unsigned char *pixels = malloc(w * h * 4), *flipped = malloc(w * h * 4);
  if (!pixels || !flipped)
    return 5;
  glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
  for (int y = 0; y < h; y++)
    memcpy(flipped + y * w * 4, pixels + (h - 1 - y) * w * 4, w * 4);
  png_image image;
  memset(&image, 0, sizeof(image));
  image.version = PNG_IMAGE_VERSION;
  image.width = w;
  image.height = h;
  image.format = PNG_FORMAT_RGBA;
  int ok = png_image_write_to_file(&image, output, 0, flipped, 0, NULL);
  free(pixels);
  free(flipped);
  glDeleteFramebuffers(1, &fb);
  glDeleteTextures(1, &tex);
  bg_scene_free(&scene);
  json_object_put(j);
  bg_renderer_finish(&r);
  return ok ? 0 : 6;
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--version")) {
    puts(BG_BUILD);
    return 0;
  }
  if (argc == 4 && !strcmp(argv[1], "--preview"))
    return preview(argv[2], argv[3]);
  struct app a;
  memset(&a, 0, sizeof(a));
  signal(SIGTERM, signal_stop);
  signal(SIGINT, signal_stop);
  signal(SIGPIPE, SIG_IGN);
  a.display = wl_display_connect(NULL);
  if (!a.display)
    return 2;
  a.registry = wl_display_get_registry(a.display);
  wl_registry_add_listener(a.registry, &registry_listener, &a);
  wl_display_roundtrip(a.display);
  wl_display_roundtrip(a.display);
  if (!a.compositor || !a.shell || !a.presentation ||
      !bg_renderer_init(&a.renderer, a.display))
    return 3;
  printf("{\"event\":\"HELLO\",\"schema\":1,\"build\":\"%s\",\"pid\":%d}\n",
         BG_BUILD, getpid());
  fflush(stdout);
  char buffer[65536];
  size_t used = 0;
  while (!stopping && !a.stopped) {
    while (wl_display_prepare_read(a.display) != 0)
      if (wl_display_dispatch_pending(a.display) < 0) {
        a.stopped = 1;
        break;
      }
    if (a.stopped)
      break;
    wl_display_flush(a.display);
    struct pollfd fds[] = {{wl_display_get_fd(a.display), POLLIN, 0},
                           {STDIN_FILENO, POLLIN, 0}};
    int n = poll(fds, 2, 500);
    if (n < 0) {
      wl_display_cancel_read(a.display);
      if (errno == EINTR)
        continue;
      break;
    }
    if (fds[0].revents & POLLIN) {
      if (wl_display_read_events(a.display) < 0)
        break;
    } else
      wl_display_cancel_read(a.display);
    if (fds[0].revents & (POLLERR | POLLHUP))
      break;
    wl_display_dispatch_pending(a.display);
    if (fds[1].revents & POLLIN) {
      ssize_t count =
          read(STDIN_FILENO, buffer + used, sizeof(buffer) - used - 1);
      if (count <= 0)
        break;
      used += count;
      buffer[used] = 0;
      char *line = buffer, *end;
      while ((end = strchr(line, '\n'))) {
        *end = 0;
        command(&a, line);
        line = end + 1;
      }
      used = strlen(line);
      memmove(buffer, line, used);
      if (used == sizeof(buffer) - 1)
        break;
    }
    if (fds[1].revents & (POLLHUP | POLLERR))
      break;
  }
  for (int i = 0; i < a.count; i++) {
    struct output *o = &a.outputs[i];
    eglMakeCurrent(a.renderer.display, a.renderer.scratch, a.renderer.scratch,
                   a.renderer.context);
    bg_scene_free(&o->current);
    bg_scene_free(&o->next);
    bg_target_free(&o->target);
    if (o->feedback)
      wp_presentation_feedback_destroy(o->feedback);
    if (o->frame)
      wl_callback_destroy(o->frame);
    if (o->egl)
      eglDestroySurface(a.renderer.display, o->egl);
    if (o->window)
      wl_egl_window_destroy(o->window);
    if (o->layer)
      zwlr_layer_surface_v1_destroy(o->layer);
    if (o->surface)
      wl_surface_destroy(o->surface);
    wl_output_destroy(o->output);
  }
  bg_renderer_finish(&a.renderer);
  wp_presentation_destroy(a.presentation);
  zwlr_layer_shell_v1_destroy(a.shell);
  wl_compositor_destroy(a.compositor);
  wl_registry_destroy(a.registry);
  wl_display_disconnect(a.display);
  return a.stopped ? 1 : 0;
}
