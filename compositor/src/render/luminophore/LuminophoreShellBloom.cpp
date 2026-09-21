#include "LuminophoreShellBloom.hpp"
#include "../../luminophore/LuminophoreVisualSettings.hpp"
#include "shaders/LuminophoreShellBloom.hpp"
#include "../OpenGL.hpp"
#include "../Renderer.hpp"
#include "../Shader.hpp"
#include "../../debug/log/Logger.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>

using namespace Render;

CLuminophoreShellBloom::~CLuminophoreShellBloom() {
    if (Render::GL::g_pHyprOpenGL) {
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        for (auto& [key, cache] : m_caches) {
            if (cache.initialized)
                luminophore_bloom_destroy(&cache.bloom);
        }
        if (m_quad)
            glDeleteBuffers(1, &m_quad);
        m_compositeShader.reset();
    }
}

void CLuminophoreShellBloom::enqueue(const std::string& key, PHLMONITOR monitor, const CHyprColor& color, float radius, float outline, const Luminophore::SVisualBundle& visual, float phase,
                              const CBox& globalPanel, float opacity) {
    if (!monitor || globalPanel.empty() || visual.glowIntensity <= 0.0)
        return;

    CBox panel = globalPanel.copy().translate(-monitor->m_position);
    panel.scale(monitor->m_scale).round();

    const double                      elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    const float                       gain    = Luminophore::CLuminophoreVisualSettings::gainAt(visual, elapsed, phase) * std::clamp(opacity, 0.F, 1.F);
    const float                       extent  = visual.glowExtent;
    const float                       padding = std::ceil(std::max(128.F, extent * 2.F) * monitor->m_scale);
    CBox                              tile    = panel.copy().expand(padding).round();
    CLuminophoreShellBloomPassElement::SData data{
        .owner   = this,
        .key     = key,
        .tile    = tile,
        .panel   = panel,
        .color   = color,
        .radius  = radius * monitor->m_scale,
        .outline = outline * monitor->m_scale,
        .extent  = extent * monitor->m_scale,
        .alpha   = gain,
        .fadeIn  = visual.animated,
    };
    g_pHyprRenderer->addPassElement(makeUnique<CLuminophoreShellBloomPassElement>(data));
}

bool CLuminophoreShellBloom::ensureCompositeShader() {
    if (m_compositeShader)
        return true;
    if (m_shaderAttempted)
        return false;
    m_shaderAttempted = true;

    auto shader = makeShared<CShader>();
    if (!shader->createProgram(std::string(LuminophoreShellShader::VERTEX), std::string(LuminophoreShellShader::FRAGMENT), true, true)) {
        Log::logger->log(Log::ERR, "[luminophore-compositor] Shell bloom shader compilation failed; retaining legacy bloom fallback");
        return false;
    }
    m_compositeShader = std::move(shader);
    Log::logger->log(Log::INFO, "[luminophore-compositor] Shell FBO bloom composite shader ready");
    return true;
}

bool CLuminophoreShellBloom::ensureQuad() {
    if (m_quad)
        return true;
    static constexpr float VERTICES[] = {-1.F, -1.F, 1.F, -1.F, -1.F, 1.F, 1.F, 1.F};
    glGenBuffers(1, &m_quad);
    glBindBuffer(GL_ARRAY_BUFFER, m_quad);
    glBufferData(GL_ARRAY_BUFFER, sizeof(VERTICES), VERTICES, GL_STATIC_DRAW);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    return m_quad != 0;
}

CLuminophoreShellBloom::SCache* CLuminophoreShellBloom::cacheFor(const std::string& key) {
    auto& cache = m_caches[key];
    if (!cache.initialized) {
        cache.initialized = luminophore_bloom_init(&cache.bloom);
        if (!cache.initialized) {
            Log::logger->log(Log::ERR, "[luminophore-compositor] Shell bloom FBO unavailable for {}: {}", key, cache.bloom.error);
            return nullptr;
        }
        cache.createdAt = std::chrono::steady_clock::now();
    }
    return &cache;
}

void CLuminophoreShellBloom::discard(const std::string& key) {
    const auto it = m_caches.find(key);
    if (it == m_caches.end())
        return;
    if (it->second.initialized && Render::GL::g_pHyprOpenGL) {
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        luminophore_bloom_destroy(&it->second.bloom);
    }
    m_caches.erase(it);
}

SLuminophoreShellBloomDiagnostics CLuminophoreShellBloom::diagnostics() const {
    SLuminophoreShellBloomDiagnostics result{.caches = m_caches.size(), .rendered = m_rendered};
    for (const auto& [key, cache] : m_caches) {
        result.regenerations += cache.bloom.generation;
        result.allocations += cache.bloom.allocation_count;
        result.fallbacks += cache.bloom.fallbacks;
    }
    return result;
}

void CLuminophoreShellBloom::render(const CLuminophoreShellBloomPassElement::SData& data) {
    if (g_pHyprRenderer->type() != IHyprRenderer::RT_GL || !Render::GL::g_pHyprOpenGL || !ensureCompositeShader() || !ensureQuad())
        return;

    auto* cache = cacheFor(data.key);
    if (!cache)
        return;

    const auto PANEL_WIDTH         = std::max(1, (int)std::round(data.panel.width));
    const auto PANEL_HEIGHT        = std::max(1, (int)std::round(data.panel.height));
    GLint      previousFramebuffer = 0;
    GLint      previousViewport[4] = {};
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &previousFramebuffer);
    glGetIntegerv(GL_VIEWPORT, previousViewport);
    // Allocation binds its own FBOs too; capture the output target before it.
    const bool prepared = luminophore_bloom_prepare_capacity(&cache->bloom, PANEL_WIDTH, PANEL_HEIGHT, 0.F, 0.F, PANEL_WIDTH, PANEL_HEIGHT, data.radius, data.outline, data.extent);
    glBindFramebuffer(GL_FRAMEBUFFER, previousFramebuffer);
    glViewport(previousViewport[0], previousViewport[1], previousViewport[2], previousViewport[3]);
    if (!prepared) {
        Log::logger->log(Log::ERR, "[luminophore-compositor] Shell bloom allocation failed for {}: {}", data.key, cache->bloom.error);
        return;
    }

    if (cache->bloom.dirty && !luminophore_bloom_generate(&cache->bloom, m_quad, (float)cache->bloom.padding, data.radius, data.outline)) {
        glBindFramebuffer(GL_FRAMEBUFFER, previousFramebuffer);
        glViewport(previousViewport[0], previousViewport[1], previousViewport[2], previousViewport[3]);
        Log::logger->log(Log::ERR, "[luminophore-compositor] Shell bloom generation failed for {}: {}", data.key, cache->bloom.error);
        return;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, previousFramebuffer);
    glViewport(previousViewport[0], previousViewport[1], previousViewport[2], previousViewport[3]);

    // The cache retains its largest allocation while the panel animates. Map
    // that complete texture back to the output; using the current smaller tile
    // here would rescale the cached bloom and make its outline drift.
    CBox tile             = {data.panel.x - cache->bloom.padding, data.panel.y - cache->bloom.padding, (double)cache->bloom.widths[0], (double)cache->bloom.heights[0]};
    CBox transformedPanel = data.panel;
    g_pHyprRenderer->m_renderData.renderModif.applyToBox(tile);
    g_pHyprRenderer->m_renderData.renderModif.applyToBox(transformedPanel);
    const auto projection = g_pHyprRenderer->projectBoxToTarget(tile);
    const auto converted  = g_pHyprRenderer->getConvertedColor(data.color.stripA());
    const CBox panel      = {transformedPanel.x - tile.x, transformedPanel.y - tile.y, transformedPanel.width, transformedPanel.height};

    Render::GL::g_pHyprOpenGL->blend(true);
    const auto previousShader = Render::GL::g_pHyprOpenGL->useShader(m_compositeShader);
    m_compositeShader->setUniformMatrix3fv(SHADER_PROJ, 1, GL_TRUE, projection.getMatrix());
    m_compositeShader->setUniformFloat4(SHADER_COLOR, converted.r, converted.g, converted.b, data.color.a);
    m_compositeShader->setUniformFloat2(SHADER_FULL_SIZE, tile.width, tile.height);
    m_compositeShader->setUniformFloat2(SHADER_TOP_LEFT, panel.x, panel.y);
    m_compositeShader->setUniformFloat2(SHADER_BOTTOM_RIGHT, panel.x + panel.width, panel.y + panel.height);
    m_compositeShader->setUniformFloat(SHADER_RADIUS, data.radius);
    const float age      = std::chrono::duration<float>(std::chrono::steady_clock::now() - cache->createdAt).count();
    const float ageRamp  = std::clamp(age / 0.20F, 0.F, 1.F);
    const float easedAge = ageRamp * ageRamp * (3.F - 2.F * ageRamp);
    m_compositeShader->setUniformFloat(SHADER_ALPHA, data.alpha * (data.fadeIn ? easedAge : 1.F));
    m_compositeShader->setUniformInt(SHADER_TEX, 0);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, cache->bloom.textures[LUMINOPHORE_BLOOM_HALF]);
    glBindVertexArray(m_compositeShader->getUniformLocation(SHADER_SHADER_VAO));

    auto        drawRegion = g_pHyprRenderer->m_renderData.damage;
    const auto& clip       = g_pHyprRenderer->m_renderData.clipBox;
    if (clip.width != 0 && clip.height != 0)
        drawRegion.intersect(CRegion{clip.x, clip.y, clip.width, clip.height});
    drawRegion.forEachRect([](const auto& rect) {
        Render::GL::g_pHyprOpenGL->scissor(&rect, g_pHyprRenderer->m_renderData.transformDamage);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    });

    glBindVertexArray(0);
    Render::GL::g_pHyprOpenGL->useShader(previousShader);
    m_rendered++;
}
