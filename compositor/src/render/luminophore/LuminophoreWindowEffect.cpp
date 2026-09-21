#include "LuminophoreWindowEffect.hpp"
#include "LuminophoreEffectConfig.hpp"
#include "LuminophoreFrameScheduler.hpp"
#include "shaders/LuminophoreInnerGlow.hpp"
#include "../OpenGL.hpp"
#include "../Renderer.hpp"
#include "../Shader.hpp"
#include "../../debug/log/Logger.hpp"
#include "../../desktop/state/FocusState.hpp"
#include "../../desktop/view/Window.hpp"
#include "../../luminophore/LuminophoreCompositionPolicy.hpp"
#include "../../luminophore/LuminophoreSpatialRuntime.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>

using namespace Render;

namespace {
    constexpr float PI           = 3.14159265358979323846F;
    constexpr float ACTIVE_PHASE = 0.23F;
}

CLuminophoreWindowEffect::~CLuminophoreWindowEffect() {
    m_scheduler.reset();

    if (m_shader && Render::GL::g_pHyprOpenGL) {
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        m_shader.reset();
    }
}

SLuminophoreDiagnosticsSnapshot CLuminophoreWindowEffect::diagnostics() const {
    return m_diagnostics.snapshot();
}

std::vector<SLuminophoreFrameEvent> CLuminophoreWindowEffect::frameEvents() const {
    return m_diagnostics.frameEvents();
}

CLuminophoreWindowEffect::SDynamics CLuminophoreWindowEffect::dynamics() const {
    const auto& config     = luminophoreEffectConfig();
    const float elapsed    = std::chrono::duration<float>(std::chrono::steady_clock::now().time_since_epoch()).count();
    const auto  fract      = [](float value) { return value - std::floor(value); };
    const auto  smoothstep = [](float edge0, float edge1, float value) {
        const float x = std::clamp((value - edge0) / (edge1 - edge0), 0.F, 1.F);
        return x * x * (3.F - 2.F * x);
    };
    const float character  = fract(ACTIVE_PHASE * 4.17F + 0.31F);
    const float period     = 4.9F + (6.4F - 4.9F) * fract(ACTIVE_PHASE * 2.73F + 0.17F);
    const float cycle      = fract(elapsed / period + ACTIVE_PHASE);
    const float rise       = 0.32F + (0.44F - 0.32F) * character;
    const float breath     = smoothstep(0.F, rise, cycle) * (1.F - smoothstep(rise, 1.F, cycle));
    const float radiusWave = 0.5F + 0.5F * std::sin(2.F * PI * (elapsed / (period * 1.37F) + ACTIVE_PHASE * 1.37F) + 0.43F);

    return {
        .intensity = config.activeIntensityLow + (config.activeIntensityHigh - config.activeIntensityLow) * breath,
        .radius    = config.activeRadiusLow + (config.activeRadiusHigh - config.activeRadiusLow) * radiusWave,
    };
}

void CLuminophoreWindowEffect::ensureScheduler() {
    if (!m_scheduler)
        m_scheduler = makeUnique<CLuminophoreFrameScheduler>(&m_diagnostics);
}

void CLuminophoreWindowEffect::presented(PHLMONITOR monitor) {
    if (m_scheduler)
        m_scheduler->presented(monitor);
}

CLuminophoreWindowEffectPassElement::SData CLuminophoreWindowEffect::makeData(PHLWINDOW window, PHLMONITOR monitor, const CBox& box, float alpha) {
    const auto& config     = luminophoreEffectConfig();
    const bool  active     = Desktop::focusState()->isWindowActive(window);
    const auto  motion     = active ? dynamics() : SDynamics{.intensity = config.inactiveIntensity, .radius = 1.F};
    const float rounding   = config.roundingLogical * monitor->m_scale;
    const int   bloomRange = std::max(1, static_cast<int>(std::lround(config.bloomRangeLogical * monitor->m_scale * motion.radius)));
    const int   nearRange  = std::max(1, static_cast<int>(std::lround(config.nearRangeLogical * monitor->m_scale * motion.radius)));

    return {
        .owner         = this,
        .box           = box,
        .color         = active ? config.activeColor : config.inactiveColor,
        .rounding      = rounding,
        .roundingPower = config.roundingPower,
        .bloomRange    = bloomRange,
        .nearRange     = nearRange,
        .glowPower     = config.glowPower,
        .bloomAlpha    = std::min(1.F, config.intensityScale * config.bloomAlpha * motion.intensity) * alpha,
        .nearAlpha     = std::min(1.F, config.intensityScale * config.nearAlpha * motion.intensity) * alpha,
        .renderMask    = !window->m_isFloating,
    };
}

void CLuminophoreWindowEffect::enqueue(PHLWINDOW window, PHLMONITOR monitor, const Vector2D& position, const Vector2D& size, float alpha) {
    if (!Luminophore::compositionPolicy()->showsWindowEffect(window) || !monitor || !window->m_isMapped || size.x <= 0.F || size.y <= 0.F || alpha <= 0.F) {
        m_diagnostics.recordSkipped();
        return;
    }

    ensureScheduler();

    CBox box = {position.x - monitor->m_position.x, position.y - monitor->m_position.y, size.x, size.y};
    box.scale(monitor->m_scale).round();
    auto data = makeData(window, monitor, box, alpha);
    if (Luminophore::spatialRuntime()->isBoardRoot(window)) {
        CRegion clip;
        for (auto region : Luminophore::spatialRuntime()->regionsFor(window, monitor))
            clip.add(region.translate(-monitor->m_position).scale(monitor->m_scale).round());
        data.spatialClip = clip;
    }

    if (Desktop::focusState()->isWindowActive(window))
        m_scheduler->track(window, monitor, box.copy().expand(std::max(data.bloomRange, data.nearRange)));

    g_pHyprRenderer->addPassElement(makeUnique<CLuminophoreWindowEffectPassElement>(data));
    m_diagnostics.recordEnqueued();
}

void CLuminophoreWindowEffect::enqueueCapture(PHLWINDOW window, PHLMONITOR monitor, const Vector2D& size) {
    if (!Luminophore::compositionPolicy()->showsWindowEffect(window) || !monitor || size.x <= 0.F || size.y <= 0.F) {
        m_diagnostics.recordSkipped();
        return;
    }

    // Export buffers use capture-local coordinates and must not drive the live
    // presentation scheduler. Monitor/region capture already consumes the
    // compositor's final mirror texture; only window capture needs this pass.
    const CBox box  = {0, 0, size.x, size.y};
    auto       data = makeData(window, monitor, box, 1.F);
    if (Luminophore::spatialRuntime()->isBoardRoot(window)) {
        CRegion    clip;
        const auto entry = Luminophore::spatialRuntime()->presentationFor(window);
        if (entry && entry->clientBox.width > 0 && entry->clientBox.height > 0)
            for (const auto& f : entry->fragments)
                clip.add(CBox{(f.box.x - entry->clientBox.x) * size.x / entry->clientBox.width, (f.box.y - entry->clientBox.y) * size.y / entry->clientBox.height,
                              f.box.width * size.x / entry->clientBox.width, f.box.height * size.y / entry->clientBox.height});
        data.spatialClip = clip;
    }
    g_pHyprRenderer->addPassElement(makeUnique<CLuminophoreWindowEffectPassElement>(data));
    m_diagnostics.recordEnqueued();
}

bool CLuminophoreWindowEffect::ensureShader() {
    if (m_shader)
        return true;
    if (m_shaderAttempted)
        return false;

    m_shaderAttempted = true;
    auto shader       = makeShared<CShader>();
    if (!shader->createProgram(std::string(LuminophoreShader::VERTEX), std::string(LuminophoreShader::FRAGMENT), true, true)) {
        m_diagnostics.recordShaderFailure();
        Log::logger->log(Log::ERR, "[luminophore-compositor] rounded-SDF shader compilation failed; skipping LUMINOPHORE window effect");
        return false;
    }

    m_shader = std::move(shader);
    Log::logger->log(Log::INFO, "[luminophore-compositor] rounded-SDF window effect shader ready");
    return true;
}

void CLuminophoreWindowEffect::renderRoundedPass(const CBox& box, const CHyprColor& color, float rounding, float roundingPower, int range, int glowPower, float alpha, bool mask) {
    CBox renderBox = box;
    g_pHyprRenderer->m_renderData.renderModif.applyToBox(renderBox);
    const auto projection = g_pHyprRenderer->projectBoxToTarget(renderBox);
    const auto converted  = g_pHyprRenderer->getConvertedColor(color.stripA());

    Render::GL::g_pHyprOpenGL->blend(true);
    const auto previousShader = Render::GL::g_pHyprOpenGL->useShader(m_shader);
    m_shader->setUniformMatrix3fv(SHADER_PROJ, 1, GL_TRUE, projection.getMatrix());
    m_shader->setUniformFloat4(SHADER_COLOR, converted.r, converted.g, converted.b, color.a);
    m_shader->setUniformFloat2(SHADER_FULL_SIZE, renderBox.width, renderBox.height);
    m_shader->setUniformFloat(SHADER_RADIUS, rounding);
    m_shader->setUniformFloat(SHADER_ROUNDING_POWER, roundingPower);
    m_shader->setUniformFloat(SHADER_RANGE, static_cast<float>(range));
    m_shader->setUniformFloat(SHADER_SHADOW_POWER, static_cast<float>(glowPower));
    m_shader->setUniformFloat(SHADER_ALPHA, alpha);
    m_shader->setUniformInt(SHADER_DISCARD_OPAQUE, mask ? 1 : 0);
    glBindVertexArray(m_shader->getUniformLocation(SHADER_SHADER_VAO));

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
}

void CLuminophoreWindowEffect::render(const CLuminophoreWindowEffectPassElement::SData& data) {
    if (g_pHyprRenderer->type() != IHyprRenderer::RT_GL || !Render::GL::g_pHyprOpenGL || !ensureShader()) {
        m_diagnostics.recordSkipped();
        return;
    }

    const auto previousDamage = g_pHyprRenderer->m_renderData.damage;
    if (data.spatialClip)
        g_pHyprRenderer->m_renderData.damage.intersect(*data.spatialClip);
    if (data.renderMask)
        renderRoundedPass(data.box, CHyprColor(0.F, 0.F, 0.F, 1.F), data.rounding, data.roundingPower, 1, 1, 1.F, true);
    renderRoundedPass(data.box, data.color, data.rounding, data.roundingPower, data.bloomRange, data.glowPower, data.bloomAlpha, false);
    renderRoundedPass(data.box, data.color, data.rounding, data.roundingPower, data.nearRange, data.glowPower, data.nearAlpha, false);
    g_pHyprRenderer->m_renderData.damage = previousDamage;
    m_diagnostics.recordDrawn();
}
