#pragma once
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <json-c/json.h>
#define BG_MAX_LAYERS 8
struct bg_layer {
  GLuint texture;
  float depth;
  int width, height;
};
struct bg_scene {
  struct bg_layer layers[BG_MAX_LAYERS];
  int count;
  float motion, fx, fy;
  int contain;
  size_t bytes;
};
struct bg_target {
  GLuint texture, framebuffer;
  int width, height;
};
struct bg_renderer {
  EGLDisplay display;
  EGLContext context;
  EGLConfig config;
  EGLSurface scratch;
  GLuint program, buffer;
};
int bg_renderer_init(struct bg_renderer *r, void *display);
int bg_scene_load(struct bg_renderer *r, struct bg_scene *scene,
                  json_object *spec);
void bg_scene_free(struct bg_scene *scene);
void bg_draw(struct bg_renderer *r, struct bg_scene *scene, int width,
             int height, float alpha, double time);
void bg_renderer_finish(struct bg_renderer *r);

int bg_draw_transition(struct bg_renderer *r, struct bg_target *target,
                       struct bg_scene *next, int w, int h, float alpha,
                       double time);
void bg_target_free(struct bg_target *target);
