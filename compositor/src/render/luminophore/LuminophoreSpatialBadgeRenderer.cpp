#include "LuminophoreSpatialBadgeRenderer.hpp"
#include "shaders/LuminophoreShellBloom.hpp"
#include "../OpenGL.hpp"
#include "../Renderer.hpp"
#include "../../output/Monitor.hpp"
#include <algorithm>

using namespace Render;

static constexpr std::string_view FRAGMENT = R"GLSL(#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec4 color;
uniform float alpha;
layout(location=0) out vec4 fragColor;
float sampleMask(vec2 p) {
    if (any(lessThan(p,vec2(0.0))) || any(greaterThan(p,vec2(1.0)))) return 0.0;
    vec2 q=abs(p-vec2(0.5))-vec2(0.45-0.065);
    float d=length(max(q,vec2(0.0)))+min(max(q.x,q.y),0.0)-0.065;
    float frame=1.0-smoothstep(0.012,0.035,abs(d));
    float bar=(1.0-smoothstep(0.012,0.035,abs(p.y-0.25)))*step(0.065,p.x)*step(p.x,0.935);
    vec2 icon=(p-vec2(0.22,0.34))/vec2(0.56,0.56);
    float logo=0.0;
    if (all(greaterThanEqual(icon,vec2(0.0))) && all(lessThanEqual(icon,vec2(1.0)))) logo=texture(tex,icon).r;
    return max(max(frame,bar),logo);
}
void main() {
    vec2 p=(v_texcoord-0.5)*2.0+0.5;
    float core=sampleMask(p), energy=0.0, weights=0.0;
    for(int y=-4;y<=4;y++) for(int x=-4;x<=4;x++) {
        vec2 offset=vec2(float(x),float(y));
        float weight=exp(-dot(offset,offset)/7.0);
        energy+=sampleMask(p+offset/24.0)*weight;
        weights+=weight;
    }
    float a=clamp(core+energy/weights*1.4,0.0,1.0)*alpha;
    fragColor=vec4(color.rgb*a,a);
}
)GLSL";

class CBadgePass final : public IPassElement {
  public:
    CBadgePass(CLuminophoreSpatialBadgeRenderer* owner, CBox box, CHyprColor color, float alpha) : m_owner(owner), m_box(box), m_color(color), m_alpha(alpha) {}
    std::vector<UP<IPassElement>> draw() override {
        m_owner->draw(m_box, m_color, m_alpha);
        return {};
    }
    bool needsLiveBlur() override {
        return false;
    }
    bool needsPrecomputeBlur() override {
        return false;
    }
    const char* passName() override {
        return "LuminophoreSpatialBadge";
    }
    ePassElementType type() override {
        return EK_CUSTOM;
    }
    std::optional<CBox> boundingBox() override {
        return m_box.copy().scale(1.0 / g_pHyprRenderer->m_renderData.pMonitor->m_scale);
    }

  private:
    CLuminophoreSpatialBadgeRenderer* m_owner;
    CBox                       m_box;
    CHyprColor                 m_color;
    float                      m_alpha;
};

CLuminophoreSpatialBadgeRenderer::~CLuminophoreSpatialBadgeRenderer() {
    reset();
}
void CLuminophoreSpatialBadgeRenderer::reset() {
    if (GL::g_pHyprOpenGL) {
        GL::g_pHyprOpenGL->makeEGLCurrent();
        if (m_texture)
            glDeleteTextures(1, &m_texture);
        m_shader.reset();
    }
    m_texture = 0;
    m_mask.clear();
    m_dirty = true;
}
void CLuminophoreSpatialBadgeRenderer::setMask(std::vector<uint8_t> mask) {
    if (mask.size() != 64 * 64)
        return;
    m_mask  = std::move(mask);
    m_dirty = true;
}
void CLuminophoreSpatialBadgeRenderer::enqueue(PHLMONITOR monitor, const CBox& globalBox, const CHyprColor& color, float alpha) {
    const auto tile = globalBox.copy().expand(globalBox.w / 2);
    if (tile.intersection(monitor->logicalBox()).empty())
        return;
    g_pHyprRenderer->addPassElement(makeUnique<CBadgePass>(this, tile.copy().translate(-monitor->m_position).scale(monitor->m_scale), color, alpha));
}
void CLuminophoreSpatialBadgeRenderer::draw(const CBox& box, const CHyprColor& color, float alpha) {
    if (!m_shader) {
        auto shader = makeShared<CShader>();
        if (!shader->createProgram(std::string(LuminophoreShellShader::VERTEX), std::string(FRAGMENT), true, true))
            return;
        m_shader = std::move(shader);
    }
    GLint      active = 0, texture = 0, vao = 0, unpack = 0, scissor[4];
    const bool scissored = glIsEnabled(GL_SCISSOR_TEST), blended = glIsEnabled(GL_BLEND);
    glGetIntegerv(GL_SCISSOR_BOX, scissor);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
    glActiveTexture(GL_TEXTURE0);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture);
    glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vao);
    glGetIntegerv(GL_UNPACK_ALIGNMENT, &unpack);
    if (!m_texture)
        glGenTextures(1, &m_texture);
    glBindTexture(GL_TEXTURE_2D, m_texture);
    if (m_dirty) {
        if (m_mask.empty()) {
            m_mask.resize(64 * 64);
            for (int y = 12; y < 52; ++y)
                for (int x = 12; x < 52; ++x)
                    m_mask[y * 64 + x] = (x < 15 || x >= 49 || y < 15 || y >= 49 || y == 23) ? 255 : 0;
        }
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, 64, 64, 0, GL_RED, GL_UNSIGNED_BYTE, m_mask.data());
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        m_dirty = false;
    }
    auto tile = box;
    g_pHyprRenderer->m_renderData.renderModif.applyToBox(tile);
    const auto projection = g_pHyprRenderer->projectBoxToTarget(tile);
    const auto converted  = g_pHyprRenderer->getConvertedColor(color.stripA());
    GL::g_pHyprOpenGL->blend(true);
    const auto previous = GL::g_pHyprOpenGL->useShader(m_shader);
    m_shader->setUniformMatrix3fv(SHADER_PROJ, 1, GL_TRUE, projection.getMatrix());
    m_shader->setUniformFloat4(SHADER_COLOR, converted.r, converted.g, converted.b, 1);
    m_shader->setUniformFloat(SHADER_ALPHA, std::clamp(alpha, 0.F, 1.F));
    m_shader->setUniformInt(SHADER_TEX, 0);
    glBindVertexArray(m_shader->getUniformLocation(SHADER_SHADER_VAO));
    auto        damage = g_pHyprRenderer->m_renderData.damage;
    const auto& clip   = g_pHyprRenderer->m_renderData.clipBox;
    if (!clip.empty())
        damage.intersect(CRegion{clip});
    damage.forEachRect([](const auto& rect) {
        GL::g_pHyprOpenGL->scissor(&rect, g_pHyprRenderer->m_renderData.transformDamage);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    });
    GL::g_pHyprOpenGL->useShader(previous);
    glBindVertexArray(vao);
    glBindTexture(GL_TEXTURE_2D, texture);
    glPixelStorei(GL_UNPACK_ALIGNMENT, unpack);
    glActiveTexture(active);
    glScissor(scissor[0], scissor[1], scissor[2], scissor[3]);
    if (!scissored)
        glDisable(GL_SCISSOR_TEST);
    GL::g_pHyprOpenGL->blend(blended);
}
