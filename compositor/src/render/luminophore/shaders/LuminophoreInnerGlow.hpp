#pragma once

#include <string_view>

namespace Render::LuminophoreShader {

inline constexpr std::string_view VERTEX = R"GLSL(#version 300 es
uniform mat3 proj;
uniform vec4 color;
in vec2 pos;
in vec2 texcoord;
out vec4 v_color;
out vec2 v_texcoord;
void main() {
    gl_Position = vec4(proj * vec3(pos, 1.0), 1.0);
    v_color = color;
    v_texcoord = texcoord;
}
)GLSL";

inline constexpr std::string_view FRAGMENT = R"GLSL(#version 300 es
precision highp float;
in vec4 v_color;
in vec2 v_texcoord;
uniform vec2 fullSize;
uniform float radius;
uniform float roundingPower;
uniform float range;
uniform float shadowPower;
uniform float alpha;
uniform int discardOpaque;
layout(location = 0) out vec4 fragColor;

float modifiedLength(vec2 value, float power) {
    return pow(pow(abs(value.x), power) + pow(abs(value.y), power), 1.0 / power);
}

void main() {
    vec2 pixel = v_texcoord * fullSize;
    vec2 halfSize = fullSize * 0.5;
    vec2 q = abs(pixel - halfSize) - (halfSize - vec2(radius));
    vec2 outside = max(q, vec2(0.0));
    float cornerDistance = (outside.x > 0.0 || outside.y > 0.0) ? modifiedLength(outside, roundingPower) : 0.0;
    float sdfDistance = cornerDistance + min(max(q.x, q.y), 0.0) - radius;
    if (sdfDistance > 0.0) {
        if (discardOpaque == 0)
            discard;
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    if (discardOpaque == 1)
        discard;

    float distanceFromRoundedEdge = max(0.0, -sdfDistance);
    float glow = pow(max(0.0, 1.0 - distanceFromRoundedEdge / max(1.0, range)), shadowPower);
    float outAlpha = v_color.a * alpha * glow;
    if (outAlpha <= 0.0)
        discard;
    fragColor = vec4(v_color.rgb * outAlpha, outAlpha);
}
)GLSL";

}
