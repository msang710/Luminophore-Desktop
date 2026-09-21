#pragma once

#include <string_view>

namespace Render::LuminophoreShellShader {

    inline constexpr std::string_view VERTEX = R"GLSL(#version 300 es
uniform mat3 proj;
in vec2 pos;
in vec2 texcoord;
out vec2 v_texcoord;
void main() {
    gl_Position = vec4(proj * vec3(pos, 1.0), 1.0);
    v_texcoord = texcoord;
}
)GLSL";

    inline constexpr std::string_view FRAGMENT = R"GLSL(#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec4 color;
uniform vec2 fullSize;
uniform vec2 topLeft;
uniform vec2 bottomRight;
uniform float radius;
uniform float alpha;
layout(location = 0) out vec4 fragColor;

float roundedRect(vec2 p, vec2 halfSize, float r) {
    vec2 q = abs(p) - halfSize + vec2(r);
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

void main() {
    vec2 pixel = v_texcoord * fullSize;
    vec2 panelSize = max(vec2(1.0), bottomRight - topLeft);
    vec2 panelCenter = (topLeft + bottomRight) * 0.5;
    float d = roundedRect(pixel - panelCenter, panelSize * 0.5, min(radius, min(panelSize.x, panelSize.y) * 0.5));
    if (d < -12.0)
        discard;

    float edgeGate = smoothstep(-12.0, 4.0, d);
    float energy = texture(tex, v_texcoord).a * edgeGate;
    float outAlpha = (1.0 - exp(-energy * alpha)) * color.a;
    if (outAlpha < 0.001)
        discard;
    fragColor = vec4(color.rgb * outAlpha, outAlpha);
}
)GLSL";

}
