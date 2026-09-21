#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <stdlib.h>
#include "LuminophoreBloomPyramid.hpp"
static double sample(struct luminophore_bloom_pyramid* b) {
    glBindFramebuffer(GL_FRAMEBUFFER, b->framebuffers[1]);
    int    n = b->widths[1] * b->heights[1] * 4;
    float* p = calloc(n, sizeof(float));
    glReadPixels(0, 0, b->widths[1], b->heights[1], GL_RGBA, GL_FLOAT, p);
    double s = 0;
    for (int i = 3; i < n; i += 4)
        s += p[i];
    free(p);
    printf("read_error=%x ", glGetError());
    return s;
}
int main(void) {
    PFNEGLGETPLATFORMDISPLAYEXTPROC get = (void*)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!get)
        return 2;
    EGLDisplay                      d   = get(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    EGLint                          a, b;
    if (!eglInitialize(d, &a, &b))
        return 2;
    EGLint    attrs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE};
    EGLConfig cfg;
    EGLint    count;
    if (!eglChooseConfig(d, attrs, &cfg, 1, &count) || count != 1)
        return 3;
    EGLint     ctx[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    EGLContext c     = eglCreateContext(d, cfg, EGL_NO_CONTEXT, ctx);
    if (!eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c))
        return 3;
    GLuint q;
    float  v[] = {-1, -1, 1, -1, -1, 1, 1, 1};
    glGenBuffers(1, &q);
    glBindBuffer(GL_ARRAY_BUFFER, q);
    glBufferData(GL_ARRAY_BUFFER, sizeof(v), v, GL_STATIC_DRAW);
    struct luminophore_bloom_pyramid p = {0};
    if (!luminophore_bloom_init(&p)) {
        puts(p.error);
        return 4;
    }
    if (!luminophore_bloom_prepare_capacity(&p, 280, 48, 0, 0, 280, 48, 14, 2, 64)) {
        puts(p.error);
        return 5;
    }
    glEnable(GL_SCISSOR_TEST);
    glScissor(0, 900, 1920, 180);
    if (!luminophore_bloom_generate(&p, q, p.padding, 14, 2)) {
        puts(p.error);
        return 6;
    }
    GLint box[4];
    glGetIntegerv(GL_SCISSOR_BOX, box);
    if (!glIsEnabled(GL_SCISSOR_TEST) || box[0] != 0 || box[1] != 900 || box[2] != 1920 || box[3] != 180)
        return 9;
    double clipped = sample(&p);
    printf("clipped=%f\n", clipped);
    glDisable(GL_SCISSOR_TEST);
    p.dirty = true;
    if (!luminophore_bloom_generate(&p, q, p.padding, 14, 2))
        return 7;
    double full = sample(&p);
    printf("full=%f\n", full);
    int result = full > 0 && fabs(full - clipped) < 0.01 && !glIsEnabled(GL_SCISSOR_TEST) ? 0 : 8;
    luminophore_bloom_destroy(&p);
    glDeleteBuffers(1, &q);
    eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(d, c);
    eglTerminate(d);
    return result;
}
