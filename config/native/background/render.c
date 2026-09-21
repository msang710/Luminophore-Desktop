#include "render.h"
#include "image.h"
#include <EGL/eglext.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static GLuint shader(GLenum type, const char *source) {
  GLuint s = glCreateShader(type);
  glShaderSource(s, 1, &source, NULL);
  glCompileShader(s);
  GLint ok;
  glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
  if (!ok) {
    glDeleteShader(s);
    return 0;
  }
  return s;
}
int bg_renderer_init(struct bg_renderer *r, void *display) {
  memset(r, 0, sizeof(*r));
  if (display)
    r->display = eglGetDisplay((EGLNativeDisplayType)display);
  else {
    PFNEGLGETPLATFORMDISPLAYEXTPROC get =
        (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    r->display =
        get ? get(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL)
            : EGL_NO_DISPLAY;
  }
  if (r->display == EGL_NO_DISPLAY || !eglInitialize(r->display, NULL, NULL) ||
      !eglBindAPI(EGL_OPENGL_ES_API))
    return 0;
  EGLint attrs[] = {EGL_SURFACE_TYPE,
                    display ? EGL_WINDOW_BIT | EGL_PBUFFER_BIT
                            : EGL_PBUFFER_BIT,
                    EGL_RENDERABLE_TYPE,
                    EGL_OPENGL_ES2_BIT,
                    EGL_RED_SIZE,
                    8,
                    EGL_GREEN_SIZE,
                    8,
                    EGL_BLUE_SIZE,
                    8,
                    EGL_ALPHA_SIZE,
                    8,
                    EGL_NONE};
  EGLint count;
  if (!eglChooseConfig(r->display, attrs, &r->config, 1, &count) || !count)
    return 0;
  EGLint context[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE},
         size[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
  r->context = eglCreateContext(r->display, r->config, EGL_NO_CONTEXT, context);
  r->scratch = eglCreatePbufferSurface(r->display, r->config, size);
  if (!eglMakeCurrent(r->display, r->scratch, r->scratch, r->context))
    return 0;
  const char *vs =
      "attribute vec2 point; varying vec2 uv; uniform vec2 fit; uniform vec2 "
      "focal; uniform float depth; uniform float motion; uniform float phase; "
      "uniform float flipped; void "
      "main(){uv=vec2(point.x,mix(point.y,1.0-point.y,flipped)); vec2 "
      "p=point*2.0-1.0; p.y=-p.y; "
      "p+=vec2(sin(phase+point.y*2.0),cos(phase*.73+point.x*2.0))*depth*motion;"
      " p*=fit; p+=vec2(.5-focal.x,focal.y-.5)*2.0*(fit-vec2(1.0)); "
      "gl_Position=vec4(p,0.,1.);}";
  const char *fs =
      "precision mediump float; varying vec2 uv; uniform sampler2D image; "
      "uniform float alpha; void main(){vec4 c=texture2D(image,uv); "
      "gl_FragColor=vec4(c.rgb,c.a*alpha);}";
  GLuint v = shader(GL_VERTEX_SHADER, vs), f = shader(GL_FRAGMENT_SHADER, fs);
  if (!v || !f)
    return 0;
  r->program = glCreateProgram();
  glAttachShader(r->program, v);
  glAttachShader(r->program, f);
  glBindAttribLocation(r->program, 0, "point");
  glLinkProgram(r->program);
  glDeleteShader(v);
  glDeleteShader(f);
  GLint linked;
  glGetProgramiv(r->program, GL_LINK_STATUS, &linked);
  if (!linked)
    return 0;
  float mesh[16 * 16 * 6 * 2];
  int at = 0;
  for (int y = 0; y < 16; y++)
    for (int x = 0; x < 16; x++) {
      int dx[] = {0, 1, 0, 1, 1, 0}, dy[] = {0, 0, 1, 0, 1, 1};
      for (int i = 0; i < 6; i++) {
        mesh[at++] = (x + dx[i]) / 16.f;
        mesh[at++] = (y + dy[i]) / 16.f;
      }
    }
  glGenBuffers(1, &r->buffer);
  glBindBuffer(GL_ARRAY_BUFFER, r->buffer);
  glBufferData(GL_ARRAY_BUFFER, sizeof(mesh), mesh, GL_STATIC_DRAW);
  return glGetError() == GL_NO_ERROR;
}
void bg_scene_free(struct bg_scene *s) {
  for (int i = 0; i < s->count; i++)
    if (s->layers[i].texture)
      glDeleteTextures(1, &s->layers[i].texture);
  memset(s, 0, sizeof(*s));
}
static double number(json_object *obj, const char *key, double fallback) {
  json_object *value;
  return json_object_object_get_ex(obj, key, &value)
             ? json_object_get_double(value)
             : fallback;
}
int bg_scene_load(struct bg_renderer *r, struct bg_scene *s,
                  json_object *spec) {
  memset(s, 0, sizeof(*s));
  if (!eglMakeCurrent(r->display, r->scratch, r->scratch, r->context))
    return 0;
  json_object *layers = json_object_object_get(spec, "layers");
  if (!layers || !json_object_is_type(layers, json_type_array))
    return 0;
  int n = json_object_array_length(layers);
  if (n < 1 || n > BG_MAX_LAYERS)
    return 0;
  s->motion = number(spec, "motion", 0);
  s->fx = number(spec, "focal_x", .5);
  s->fy = number(spec, "focal_y", .5);
  const char *fit = json_object_get_string(json_object_object_get(spec, "fit"));
  s->contain = fit && !strcmp(fit, "contain");
  if (!isfinite(s->motion) || s->motion < 0 || s->motion > .05 ||
      !isfinite(s->fx) || !isfinite(s->fy) || s->fx < 0 || s->fx > 1 ||
      s->fy < 0 || s->fy > 1)
    return 0;
  GLint limit;
  glGetIntegerv(GL_MAX_TEXTURE_SIZE, &limit);
  for (int i = 0; i < n; i++) {
    json_object *row = json_object_array_get_idx(layers, i);
    const char *path =
        json_object_get_string(json_object_object_get(row, "path"));
    float depth = number(row, "depth", 0);
    if (!path || !isfinite(depth) || depth < 0 || depth > 1)
      goto fail;
    struct bg_image image;
    if (!bg_image_load(path, &image))
      goto fail;
    size_t bytes = (size_t)image.width * image.height * 4;
    if (image.width > limit || image.height > limit ||
        s->bytes + bytes > 128UL * 1024 * 1024) {
      bg_image_free(&image);
      goto fail;
    }
    struct bg_layer *l = &s->layers[s->count++];
    l->depth = depth;
    l->width = image.width;
    l->height = image.height;
    s->bytes += bytes;
    glGenTextures(1, &l->texture);
    glBindTexture(GL_TEXTURE_2D, l->texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width, image.height, 0,
                 GL_RGBA, GL_UNSIGNED_BYTE, image.pixels);
    bg_image_free(&image);
    if (glGetError() != GL_NO_ERROR)
      goto fail;
  }
  return 1;
fail:
  bg_scene_free(s);
  return 0;
}
void bg_draw(struct bg_renderer *r, struct bg_scene *s, int w, int h,
             float alpha, double time) {
  glUseProgram(r->program);
  glUniform1f(glGetUniformLocation(r->program, "flipped"), 0);
  glBindBuffer(GL_ARRAY_BUFFER, r->buffer);
  glEnableVertexAttribArray(0);
  glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, 0);
  glEnable(GL_BLEND);
  glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE,
                      GL_ONE_MINUS_SRC_ALPHA);
  glUniform1i(glGetUniformLocation(r->program, "image"), 0);
  glUniform1f(glGetUniformLocation(r->program, "alpha"), alpha);
  glUniform1f(glGetUniformLocation(r->program, "motion"), s->motion);
  glUniform1f(glGetUniformLocation(r->program, "phase"),
              (float)fmod(time, 10000));
  glUniform2f(glGetUniformLocation(r->program, "focal"), s->fx, s->fy);
  for (int i = 0; i < s->count; i++) {
    struct bg_layer *l = &s->layers[i];
    float sx = (float)w / l->width, sy = (float)h / l->height;
    float scale = s->contain ? fminf(sx, sy) : fmaxf(sx, sy);
    float extra = s->contain ? 1 : 1 + s->motion * 2;
    glUniform2f(glGetUniformLocation(r->program, "fit"),
                l->width * scale / w * extra, l->height * scale / h * extra);
    glUniform1f(glGetUniformLocation(r->program, "depth"), l->depth);
    glBindTexture(GL_TEXTURE_2D, l->texture);
    glDrawArrays(GL_TRIANGLES, 0, 16 * 16 * 6);
  }
}
void bg_renderer_finish(struct bg_renderer *r) {
  if (r->display == EGL_NO_DISPLAY)
    return;
  eglMakeCurrent(r->display, r->scratch, r->scratch, r->context);
  glDeleteBuffers(1, &r->buffer);
  glDeleteProgram(r->program);
  eglMakeCurrent(r->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
  eglDestroySurface(r->display, r->scratch);
  eglDestroyContext(r->display, r->context);
  eglTerminate(r->display);
}

void bg_target_free(struct bg_target *t) {
  glDeleteFramebuffers(1, &t->framebuffer);
  glDeleteTextures(1, &t->texture);
  memset(t, 0, sizeof(*t));
}
/* Flatten a complete scene first: fading each translucent layer separately
 * leaks the old background through overlapping layers. Target pixels are
 * opaque. */
int bg_draw_transition(struct bg_renderer *r, struct bg_target *t,
                       struct bg_scene *next, int w, int h, float alpha,
                       double time) {
  GLint destination;
  glGetIntegerv(GL_FRAMEBUFFER_BINDING, &destination);
  if (w < 1 || h < 1 || (size_t)w * h > 32000000)
    return 0;
  if (t->width != w || t->height != h) {
    bg_target_free(t);
    t->width = w;
    t->height = h;
    glGenTextures(1, &t->texture);
    glBindTexture(GL_TEXTURE_2D, t->texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE,
                 NULL);
    glGenFramebuffers(1, &t->framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, t->framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                           t->texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
      glBindFramebuffer(GL_FRAMEBUFFER, destination);
      bg_target_free(t);
      return 0;
    }
  } else
    glBindFramebuffer(GL_FRAMEBUFFER, t->framebuffer);
  glClearColor(0, 0, 0, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  bg_draw(r, next, w, h, 1, time);
  glBindFramebuffer(GL_FRAMEBUFFER, destination);
  glUniform1f(glGetUniformLocation(r->program, "flipped"), 1);
  glUniform1f(glGetUniformLocation(r->program, "alpha"), alpha);
  glUniform1f(glGetUniformLocation(r->program, "motion"), 0);
  glUniform1f(glGetUniformLocation(r->program, "depth"), 0);
  glUniform2f(glGetUniformLocation(r->program, "fit"), 1, 1);
  glUniform2f(glGetUniformLocation(r->program, "focal"), .5, .5);
  glBindTexture(GL_TEXTURE_2D, t->texture);
  glDrawArrays(GL_TRIANGLES, 0, 16 * 16 * 6);
  return glGetError() == GL_NO_ERROR;
}
