#ifndef LUMINOPHORE_GLOW_BLOOM_H
#define LUMINOPHORE_GLOW_BLOOM_H

#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <stdbool.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#define LUMINOPHORE_BLOOM_LEVEL_COUNT 4
#define LUMINOPHORE_BLOOM_TEXTURE_COUNT 5
#define LUMINOPHORE_BLOOM_SOURCE 0
#define LUMINOPHORE_BLOOM_HALF 1
#define LUMINOPHORE_BLOOM_QUARTER 2
#define LUMINOPHORE_BLOOM_EIGHTH 3
#define LUMINOPHORE_BLOOM_SIXTEENTH 4
#define LUMINOPHORE_BLOOM_MAX_DIMENSION 2048

struct luminophore_bloom_pyramid {
    GLuint emission_program;
    GLuint downsample_program;
    GLuint upsample_program;
    GLuint textures[LUMINOPHORE_BLOOM_TEXTURE_COUNT];
    GLuint framebuffers[LUMINOPHORE_BLOOM_TEXTURE_COUNT];
    int widths[LUMINOPHORE_BLOOM_TEXTURE_COUNT];
    int heights[LUMINOPHORE_BLOOM_TEXTURE_COUNT];
    bool half_float_supported;
    bool available;
    bool active;
    bool allocated;
    bool dirty;
    int padding;
    int capacity_width;
    int capacity_height;
    float panel_x;
    float panel_y;
    int panel_width;
    int panel_height;
    float corner_radius;
    float outline_width;
    float extent;
    unsigned generation;
    unsigned allocation_count;
    unsigned fallbacks;
    char error[160];
};

static const char *luminophore_bloom_vertex_source =
    "attribute vec2 a_position;varying vec2 v_uv;"
    "void main(){v_uv=a_position*0.5+0.5;gl_Position=vec4(a_position,0.0,1.0);}";

static const char *luminophore_bloom_emission_source =
    "#extension GL_OES_standard_derivatives : enable\n"
    "precision highp float;varying vec2 v_uv;"
    "uniform vec2 u_size;uniform vec4 u_panel;uniform float u_radius;uniform float u_width;"
    "float rounded_rect(vec2 p,vec2 h,float r){vec2 q=abs(p)-h+vec2(r);"
    "return min(max(q.x,q.y),0.0)+length(max(q,0.0))-r;}"
    "void main(){vec2 panel=max(vec2(1.0),u_panel.zw);"
    "vec2 p=v_uv*u_size-u_panel.xy;float d=rounded_rect(p,panel*0.5,min(u_radius,min(panel.x,panel.y)*0.5));"
    "float aa=max(fwidth(d),0.75);float band=max(1.0,u_width);"
    "float a=1.0-smoothstep(band-aa,band+aa,abs(d));gl_FragColor=vec4(a);}";

/* 13-tap energy-preserving downsample.  The center and box taps deliberately
 * mix neighboring texels before the image reaches the low-resolution levels. */
static const char *luminophore_bloom_downsample_source =
    "precision highp float;varying vec2 v_uv;uniform sampler2D u_source;uniform vec2 u_texel;"
    "void main(){vec2 t=u_texel;vec4 c=texture2D(u_source,v_uv)*0.125;"
    "c+=(texture2D(u_source,v_uv+vec2(-2.0,-2.0)*t)+texture2D(u_source,v_uv+vec2(2.0,-2.0)*t)"
    "+texture2D(u_source,v_uv+vec2(-2.0,2.0)*t)+texture2D(u_source,v_uv+vec2(2.0,2.0)*t))*0.03125;"
    "c+=(texture2D(u_source,v_uv+vec2(-1.0,-1.0)*t)+texture2D(u_source,v_uv+vec2(1.0,-1.0)*t)"
    "+texture2D(u_source,v_uv+vec2(-1.0,1.0)*t)+texture2D(u_source,v_uv+vec2(1.0,1.0)*t))*0.125;"
    "c+=(texture2D(u_source,v_uv+vec2(-2.0,0.0)*t)+texture2D(u_source,v_uv+vec2(2.0,0.0)*t)"
    "+texture2D(u_source,v_uv+vec2(0.0,-2.0)*t)+texture2D(u_source,v_uv+vec2(0.0,2.0)*t))*0.0625;"
    "gl_FragColor=c;}";

/* 9-tap tent upsample; accumulated into the next larger level with additive blending. */
static const char *luminophore_bloom_upsample_source =
    "precision highp float;varying vec2 v_uv;uniform sampler2D u_source;uniform vec2 u_texel;"
    "void main(){vec2 t=u_texel;vec4 c=texture2D(u_source,v_uv)*0.25;"
    "c+=(texture2D(u_source,v_uv+vec2(-1.0,0.0)*t)+texture2D(u_source,v_uv+vec2(1.0,0.0)*t)"
    "+texture2D(u_source,v_uv+vec2(0.0,-1.0)*t)+texture2D(u_source,v_uv+vec2(0.0,1.0)*t))*0.125;"
    "c+=(texture2D(u_source,v_uv+vec2(-1.0,-1.0)*t)+texture2D(u_source,v_uv+vec2(1.0,-1.0)*t)"
    "+texture2D(u_source,v_uv+vec2(-1.0,1.0)*t)+texture2D(u_source,v_uv+vec2(1.0,1.0)*t))*0.0625;"
    "gl_FragColor=c;}";

static inline bool luminophore_bloom_has_extension(const char *extensions, const char *name) {
    if (!extensions || !name || !name[0] || strchr(name, ' ')) return false;
    size_t length = strlen(name);
    const char *match = extensions;
    while ((match = strstr(match, name)) != NULL) {
        if ((match == extensions || match[-1] == ' ') &&
            (match[length] == '\0' || match[length] == ' ')) return true;
        match += length;
    }
    return false;
}

static inline GLuint luminophore_bloom_compile(GLenum kind, const char *source, char *error, size_t size) {
    GLuint shader = glCreateShader(kind);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        GLchar log[128] = {0};
        glGetShaderInfoLog(shader, sizeof(log) - 1, NULL, log);
        snprintf(error, size, "bloom shader compile failed: %.110s", log);
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

static inline GLuint luminophore_bloom_link(const char *fragment, char *error, size_t size) {
    GLuint vertex = luminophore_bloom_compile(GL_VERTEX_SHADER, luminophore_bloom_vertex_source, error, size);
    GLuint pixel = luminophore_bloom_compile(GL_FRAGMENT_SHADER, fragment, error, size);
    if (!vertex || !pixel) {
        if (vertex) glDeleteShader(vertex);
        if (pixel) glDeleteShader(pixel);
        return 0;
    }
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex); glAttachShader(program, pixel); glLinkProgram(program);
    glDeleteShader(vertex); glDeleteShader(pixel);
    GLint ok = GL_FALSE; glGetProgramiv(program, GL_LINK_STATUS, &ok);
    if (!ok) {
        snprintf(error, size, "bloom shader link failed");
        glDeleteProgram(program);
        return 0;
    }
    return program;
}

static inline bool luminophore_bloom_init(struct luminophore_bloom_pyramid *bloom) {
    memset(bloom, 0, sizeof(*bloom));
    const char *extensions = (const char *)glGetString(GL_EXTENSIONS);
    bloom->half_float_supported =
        luminophore_bloom_has_extension(extensions, "GL_OES_texture_half_float") &&
        luminophore_bloom_has_extension(extensions, "GL_OES_texture_half_float_linear") &&
        luminophore_bloom_has_extension(extensions, "GL_EXT_color_buffer_half_float");
    bloom->emission_program = luminophore_bloom_link(luminophore_bloom_emission_source, bloom->error, sizeof(bloom->error));
    bloom->downsample_program = luminophore_bloom_link(luminophore_bloom_downsample_source, bloom->error, sizeof(bloom->error));
    bloom->upsample_program = luminophore_bloom_link(luminophore_bloom_upsample_source, bloom->error, sizeof(bloom->error));
    bool shaders = bloom->emission_program && bloom->downsample_program && bloom->upsample_program;
    bloom->available = bloom->half_float_supported && shaders;
    if (!bloom->half_float_supported)
        snprintf(bloom->error, sizeof(bloom->error), "required half-float bloom extensions unavailable");
    return bloom->available;
}

static inline void luminophore_bloom_release_textures(struct luminophore_bloom_pyramid *bloom) {
    glDeleteFramebuffers(LUMINOPHORE_BLOOM_TEXTURE_COUNT, bloom->framebuffers);
    glDeleteTextures(LUMINOPHORE_BLOOM_TEXTURE_COUNT, bloom->textures);
    memset(bloom->framebuffers, 0, sizeof(bloom->framebuffers));
    memset(bloom->textures, 0, sizeof(bloom->textures));
    memset(bloom->widths, 0, sizeof(bloom->widths));
    memset(bloom->heights, 0, sizeof(bloom->heights));
    bloom->allocated = false;
}

static inline void luminophore_bloom_destroy(struct luminophore_bloom_pyramid *bloom) {
    if (!bloom) return;
    luminophore_bloom_release_textures(bloom);
    if (bloom->emission_program) glDeleteProgram(bloom->emission_program);
    if (bloom->downsample_program) glDeleteProgram(bloom->downsample_program);
    if (bloom->upsample_program) glDeleteProgram(bloom->upsample_program);
    memset(bloom, 0, sizeof(*bloom));
}

static inline int luminophore_bloom_required_padding(float extent) {
    return (int)ceilf(fmaxf(128.0f, extent * 2.0f));
}

static inline bool luminophore_bloom_allocate(struct luminophore_bloom_pyramid *bloom, int width, int height) {
    if (!bloom || !bloom->available || width < 1 || height < 1 ||
        width > LUMINOPHORE_BLOOM_MAX_DIMENSION || height > LUMINOPHORE_BLOOM_MAX_DIMENSION) return false;
    luminophore_bloom_release_textures(bloom);
    glGenTextures(LUMINOPHORE_BLOOM_TEXTURE_COUNT, bloom->textures);
    glGenFramebuffers(LUMINOPHORE_BLOOM_TEXTURE_COUNT, bloom->framebuffers);
    for (int level = 0; level < LUMINOPHORE_BLOOM_TEXTURE_COUNT; level++) {
        int divisor = level == 0 ? 1 : 1 << level;
        bloom->widths[level] = width / divisor > 0 ? width / divisor : 1;
        bloom->heights[level] = height / divisor > 0 ? height / divisor : 1;
        glBindTexture(GL_TEXTURE_2D, bloom->textures[level]);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, bloom->widths[level], bloom->heights[level],
                     0, GL_RGBA, GL_HALF_FLOAT_OES, NULL);
        glBindFramebuffer(GL_FRAMEBUFFER, bloom->framebuffers[level]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                               bloom->textures[level], 0);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
            luminophore_bloom_release_textures(bloom);
            snprintf(bloom->error, sizeof(bloom->error), "half-float bloom framebuffer incomplete");
            return false;
        }
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    bloom->allocated = true; bloom->dirty = true; bloom->allocation_count++;
    bloom->error[0] = '\0';
    return true;
}

static inline bool luminophore_bloom_prepare_capacity(
    struct luminophore_bloom_pyramid *bloom, int capacity_width, int capacity_height,
    float panel_x, float panel_y, int panel_width, int panel_height,
    float corner_radius, float outline_width, float extent
) {
    if (!bloom || !bloom->available) return false;
    int required_padding = luminophore_bloom_required_padding(extent);
    int required_capacity_width = capacity_width > 0 ? capacity_width : panel_width;
    int required_capacity_height = capacity_height > 0 ? capacity_height : panel_height;
    int padding = bloom->allocated && bloom->padding > required_padding
        ? bloom->padding : required_padding;
    int retained_capacity_width = bloom->allocated && bloom->capacity_width > required_capacity_width
        ? bloom->capacity_width : required_capacity_width;
    int retained_capacity_height = bloom->allocated && bloom->capacity_height > required_capacity_height
        ? bloom->capacity_height : required_capacity_height;
    int width = retained_capacity_width + padding * 2;
    int height = retained_capacity_height + padding * 2;
    float next_panel_x = padding + panel_x + panel_width * 0.5f;
    float next_panel_y = padding + panel_y + panel_height * 0.5f;
    bool allocation_changed = !bloom->allocated || width != bloom->widths[0] || height != bloom->heights[0];
    bool emission_changed = allocation_changed ||
        fabsf(next_panel_x - bloom->panel_x) >= 0.01f ||
        fabsf(next_panel_y - bloom->panel_y) >= 0.01f ||
        panel_width != bloom->panel_width || panel_height != bloom->panel_height ||
        fabsf(corner_radius - bloom->corner_radius) >= 0.01f ||
        fabsf(outline_width - bloom->outline_width) >= 0.01f ||
        fabsf(extent - bloom->extent) >= 0.01f;
    if (!emission_changed) return true;
    if (allocation_changed && !luminophore_bloom_allocate(bloom, width, height)) return false;
    bloom->padding = padding;
    bloom->capacity_width = retained_capacity_width;
    bloom->capacity_height = retained_capacity_height;
    bloom->panel_x = next_panel_x;
    bloom->panel_y = next_panel_y;
    bloom->panel_width = panel_width;
    bloom->panel_height = panel_height;
    bloom->corner_radius = corner_radius;
    bloom->outline_width = outline_width;
    bloom->extent = extent;
    bloom->dirty = true;
    return true;
}

static inline void luminophore_bloom_quad(GLuint program, GLuint buffer) {
    GLint position = glGetAttribLocation(program, "a_position");
    glBindBuffer(GL_ARRAY_BUFFER, buffer);
    glEnableVertexAttribArray(position);
    glVertexAttribPointer(position, 2, GL_FLOAT, GL_FALSE, 0, NULL);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glDisableVertexAttribArray(position);
}

static inline void luminophore_bloom_filter_pass(
    struct luminophore_bloom_pyramid *bloom, GLuint buffer, GLuint program,
    int source, int target
) {
    glBindFramebuffer(GL_FRAMEBUFFER, bloom->framebuffers[target]);
    glViewport(0, 0, bloom->widths[target], bloom->heights[target]);
    glUseProgram(program);
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, bloom->textures[source]);
    glUniform1i(glGetUniformLocation(program, "u_source"), 0);
    glUniform2f(glGetUniformLocation(program, "u_texel"),
                1.0f / bloom->widths[source], 1.0f / bloom->heights[source]);
    luminophore_bloom_quad(program, buffer);
}

static inline bool luminophore_bloom_generate(
    struct luminophore_bloom_pyramid *bloom, GLuint buffer,
    float padding, float radius, float outline_width
) {
    if (!bloom || !bloom->allocated) return false;
    (void)padding;
    // Output damage coordinates must never clip the smaller offscreen pyramid.
    const GLboolean scissor_enabled = glIsEnabled(GL_SCISSOR_TEST);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    glBindFramebuffer(GL_FRAMEBUFFER, bloom->framebuffers[LUMINOPHORE_BLOOM_SOURCE]);
    glViewport(0, 0, bloom->widths[LUMINOPHORE_BLOOM_SOURCE], bloom->heights[LUMINOPHORE_BLOOM_SOURCE]);
    glClearColor(0, 0, 0, 0); glClear(GL_COLOR_BUFFER_BIT);
    glUseProgram(bloom->emission_program);
    glUniform2f(glGetUniformLocation(bloom->emission_program, "u_size"),
                (float)bloom->widths[0], (float)bloom->heights[0]);
    glUniform4f(glGetUniformLocation(bloom->emission_program, "u_panel"),
                bloom->panel_x, bloom->panel_y,
                (float)bloom->panel_width, (float)bloom->panel_height);
    glUniform1f(glGetUniformLocation(bloom->emission_program, "u_radius"), radius);
    glUniform1f(glGetUniformLocation(bloom->emission_program, "u_width"), outline_width);
    luminophore_bloom_quad(bloom->emission_program, buffer);
    for (int target = LUMINOPHORE_BLOOM_HALF; target <= LUMINOPHORE_BLOOM_SIXTEENTH; target++)
        luminophore_bloom_filter_pass(bloom, buffer, bloom->downsample_program, target - 1, target);
    glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE);
    for (int source = LUMINOPHORE_BLOOM_SIXTEENTH; source > LUMINOPHORE_BLOOM_HALF; source--)
        luminophore_bloom_filter_pass(bloom, buffer, bloom->upsample_program, source, source - 1);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    if (scissor_enabled) glEnable(GL_SCISSOR_TEST);
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        snprintf(bloom->error, sizeof(bloom->error), "bloom generation GL error 0x%x", error);
        bloom->fallbacks++;
        return false;
    }
    bloom->dirty = false; bloom->generation++;
    return true;
}

#endif
