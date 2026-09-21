#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/fullscreen/FullscreenController.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/Shader.hpp>
#include <hyprland/src/render/decorations/IHyprWindowDecoration.hpp>
#include <hyprland/src/render/pass/PassElement.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopManager.hpp>
#include <hyprland/src/state/MonitorState.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <string>
#include <stdexcept>
#include <vector>

using namespace Desktop::View;

static HANDLE                 PHANDLE = nullptr;
static bool                   PDRAWLOGGED = false;
static SP<CEventLoopTimer>    PBREATHTIMER;
static SP<CEventLoopTimer>    PBOOTSTRAPTIMER;
static size_t                 PBOOTSTRAPSTEP = 0;
static SP<CShader>            PLUMINOPHOREINNERSHADER;
static bool                   PSHADERATTEMPTED = false;
static CHyprSignalListener    POPENLISTENER;
static CHyprSignalListener    PCLOSELISTENER;
static CHyprSignalListener    PFOCUSLISTENER;
static CHyprSignalListener    PFULLSCREENLISTENER;
static CHyprSignalListener    PFLOATINGLISTENER;
static CHyprSignalListener    PRENDERSTAGELISTENER;

struct SAttachedGlow {
    PHLWINDOWREF window;
    IHyprWindowDecoration* decoration = nullptr;
};

static std::vector<SAttachedGlow> PATTACHEDGLOWS;

static const std::string LUMINOPHORE_VERTEX_SHADER = R"GLSL(#version 300 es
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

static const std::string LUMINOPHORE_INNER_FRAGMENT_SHADER = R"GLSL(#version 300 es
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

static constexpr float PI = 3.14159265358979323846F;
static constexpr float ACTIVE_PHASE = 0.23F;

struct SLuminophoreGlowDynamics {
    float intensity = 1.0F;
    float radius = 1.0F;
};

static SLuminophoreGlowDynamics luminophoreGlowDynamics() {
    const float elapsed = std::chrono::duration<float>(
        std::chrono::steady_clock::now().time_since_epoch()
    ).count();
    const auto fract = [](float value) { return value - std::floor(value); };
    const auto smoothstep = [](float edge0, float edge1, float value) {
        const float x = std::clamp((value - edge0) / (edge1 - edge0), 0.0F, 1.0F);
        return x * x * (3.0F - 2.0F * x);
    };
    const float character = fract(ACTIVE_PHASE * 4.17F + 0.31F);
    const float period = 4.9F + (6.4F - 4.9F) * fract(ACTIVE_PHASE * 2.73F + 0.17F);
    const float cycle = fract(elapsed / period + ACTIVE_PHASE);
    const float rise = 0.32F + (0.44F - 0.32F) * character;
    const float breath = smoothstep(0.0F, rise, cycle) * (1.0F - smoothstep(rise, 1.0F, cycle));
    const float radiusWave = 0.5F + 0.5F * std::sin(
        2.0F * PI * (elapsed / (period * 1.37F) + ACTIVE_PHASE * 1.37F) + 0.43F
    );
    return {
        .intensity = 0.82F + (1.54F - 0.82F) * breath,
        .radius = 0.96F + (1.06F - 0.96F) * radiusWave,
    };
}

static bool isAttached(const PHLWINDOW& window) {
    return std::ranges::any_of(PATTACHEDGLOWS, [&window](const auto& attached) {
        return attached.window.lock() == window;
    });
}

static bool shouldRenderGlow(const PHLWINDOW& window) {
    // The glow is an inset decoration, so fullscreen does not need the outer
    // allocation that motivated the old exclusion. Keep it visible for every
    // mapped window, including true fullscreen clients.
    return window && window->m_isMapped;
}

static void breathingTick(SP<CEventLoopTimer> self, void*) {
    // Inactive windows remain at the active breathing curve's 0.82 floor.
    // Keep the timer armed, but only ask the compositor for animation frames
    // while this window owns focus.
    const auto window = Desktop::focusState()->window();
    if (shouldRenderGlow(window) && isAttached(window))
        g_pHyprRenderer->damageWindow(window, true);
    self->updateTimeout(std::chrono::milliseconds(14));
}

static void startBreathingTimer() {
    if (PBREATHTIMER)
        return;
    PBREATHTIMER = makeShared<CEventLoopTimer>(std::chrono::milliseconds(14), breathingTick, nullptr);
    g_pEventLoopManager->addTimer(PBREATHTIMER);
}

static constexpr std::array BOOTSTRAP_DAMAGE_DELAYS{
    std::chrono::milliseconds(100),
    std::chrono::milliseconds(200),
    std::chrono::milliseconds(500),
};

static void bootstrapDamageTick(SP<CEventLoopTimer> self, void*) {
    for (const auto& monitor : State::monitorState()->monitors())
        if (monitor)
            g_pHyprRenderer->damageMonitor(monitor);

    PBOOTSTRAPSTEP++;
    if (PBOOTSTRAPSTEP >= BOOTSTRAP_DAMAGE_DELAYS.size()) {
        self->cancel();
        return;
    }
    self->updateTimeout(BOOTSTRAP_DAMAGE_DELAYS[PBOOTSTRAPSTEP]);
}

static void startBootstrapDamageTimer() {
    if (PBOOTSTRAPTIMER)
        return;
    PBOOTSTRAPSTEP = 0;
    PBOOTSTRAPTIMER = makeShared<CEventLoopTimer>(
        BOOTSTRAP_DAMAGE_DELAYS.front(), bootstrapDamageTick, nullptr
    );
    g_pEventLoopManager->addTimer(PBOOTSTRAPTIMER);
}

static bool ensureLuminophoreInnerShader() {
    if (PLUMINOPHOREINNERSHADER)
        return true;
    if (PSHADERATTEMPTED)
        return false;
    PSHADERATTEMPTED = true;
    auto shader = makeShared<CShader>();
    if (!shader->createProgram(LUMINOPHORE_VERTEX_SHADER, LUMINOPHORE_INNER_FRAGMENT_SHADER, true, false)) {
        Log::logger->log(Log::ERR, "[luminophore-window-glow] custom rounded-SDF shader compilation failed; using Hyprland fallback");
        return false;
    }
    PLUMINOPHOREINNERSHADER = shader;
    Log::logger->log(Log::INFO, "[luminophore-window-glow] custom rounded-SDF shader ready");
    return true;
}

static void renderLuminophoreRoundedPass(
    const CBox& box, const CHyprColor& color, float rounding, float roundingPower,
    int range, int glowPower, float alpha, bool renderMask
) {
    if (!ensureLuminophoreInnerShader()) {
        // A failed custom shader must never reintroduce the tiled-only black
        // corner mask on floating windows. The Hyprland fallback is used only
        // for light; masking fails open and remains compositor-owned.
        if (renderMask)
            return;
        Config::CGradientValueData fallback{color};
        Render::GL::g_pHyprOpenGL->renderInnerGlow(
            box, static_cast<int>(std::lround(rounding)), roundingPower,
            range, fallback, glowPower, alpha
        );
        return;
    }

    CBox renderBox = box;
    g_pHyprRenderer->m_renderData.renderModif.applyToBox(renderBox);
    const auto projection = g_pHyprRenderer->projectBoxToTarget(renderBox);
    const auto converted = g_pHyprRenderer->getConvertedColor(color.stripA());

    Render::GL::g_pHyprOpenGL->blend(true);
    const auto previousShader = Render::GL::g_pHyprOpenGL->useShader(PLUMINOPHOREINNERSHADER);
    PLUMINOPHOREINNERSHADER->setUniformMatrix3fv(SHADER_PROJ, 1, GL_TRUE, projection.getMatrix());
    PLUMINOPHOREINNERSHADER->setUniformFloat4(SHADER_COLOR, converted.r, converted.g, converted.b, color.a);
    PLUMINOPHOREINNERSHADER->setUniformFloat2(SHADER_FULL_SIZE, renderBox.width, renderBox.height);
    PLUMINOPHOREINNERSHADER->setUniformFloat(SHADER_RADIUS, rounding);
    PLUMINOPHOREINNERSHADER->setUniformFloat(SHADER_ROUNDING_POWER, roundingPower);
    PLUMINOPHOREINNERSHADER->setUniformFloat(SHADER_RANGE, static_cast<float>(range));
    PLUMINOPHOREINNERSHADER->setUniformFloat(SHADER_SHADOW_POWER, static_cast<float>(glowPower));
    PLUMINOPHOREINNERSHADER->setUniformFloat(SHADER_ALPHA, alpha);
    PLUMINOPHOREINNERSHADER->setUniformInt(SHADER_DISCARD_OPAQUE, renderMask ? 1 : 0);
    glBindVertexArray(PLUMINOPHOREINNERSHADER->getUniformLocation(SHADER_SHADER_VAO));

    auto drawRegion = g_pHyprRenderer->m_renderData.damage;
    const auto& clip = g_pHyprRenderer->m_renderData.clipBox;
    if (clip.width != 0 && clip.height != 0)
        drawRegion.intersect(CRegion{clip.x, clip.y, clip.width, clip.height});
    drawRegion.forEachRect([](const auto& rect) {
        Render::GL::g_pHyprOpenGL->scissor(&rect, g_pHyprRenderer->m_renderData.transformDamage);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    });

    glBindVertexArray(0);
    Render::GL::g_pHyprOpenGL->useShader(previousShader);
}

static void renderTiledCornerMask(
    const CBox& box, float rounding, float roundingPower
) {
    renderLuminophoreRoundedPass(
        box, CHyprColor(0.0F, 0.0F, 0.0F, 1.0F), rounding, roundingPower,
        1, 1, 1.0F, true
    );
}

static void renderLuminophoreInnerGlow(
    const CBox& box, const CHyprColor& color, float rounding, float roundingPower,
    int range, int glowPower, float alpha
) {
    renderLuminophoreRoundedPass(
        box, color, rounding, roundingPower, range, glowPower, alpha, false
    );
}

class CLuminophoreWindowGlowDecoration;

class CLuminophoreGlowPassElement final : public IPassElement {
  public:
    CLuminophoreGlowPassElement(CLuminophoreWindowGlowDecoration* decoration, float alpha) : m_decoration(decoration), m_alpha(alpha) {}

    std::vector<UP<IPassElement>> draw() override;
    bool needsLiveBlur() override { return false; }
    bool needsPrecomputeBlur() override { return false; }
    const char* passName() override { return "CLuminophoreGlowPassElement"; }
    ePassElementType type() override { return EK_CUSTOM; }

  private:
    CLuminophoreWindowGlowDecoration* m_decoration = nullptr;
    float m_alpha = 1.0F;
};

class CLuminophoreWindowGlowDecoration final : public IHyprWindowDecoration {
  public:
    explicit CLuminophoreWindowGlowDecoration(PHLWINDOW window) : IHyprWindowDecoration(window), m_window(window) {}

    SDecorationPositioningInfo getPositioningInfo() override {
        return {
            .policy = DECORATION_POSITION_ABSOLUTE,
            .edges = DECORATION_EDGE_TOP | DECORATION_EDGE_BOTTOM |
                     DECORATION_EDGE_LEFT | DECORATION_EDGE_RIGHT,
            .priority = 10,
            .desiredExtents = {{0, 0}, {0, 0}},
            .reserved = false,
        };
    }

    void onPositioningReply(const SDecorationPositioningReply&) override {}

    void draw(PHLMONITOR, float const& alpha) override {
        g_pHyprRenderer->m_renderPass.add(makeUnique<CLuminophoreGlowPassElement>(this, alpha));
    }

    void drawPass(PHLMONITOR monitor, float const& alpha) {
        const auto window = m_window.lock();
        if (!monitor || !shouldRenderGlow(window))
            return;

        CBox box = window->geometricBox(IGeometric::GEOMETRIC_CURRENT);
        box.translate(-monitor->m_position);
        if (!PDRAWLOGGED) {
            Log::logger->log(Log::INFO, "[luminophore-window-glow] first draw at {}x{} {}x{}", box.x, box.y, box.w, box.h);
            PDRAWLOGGED = true;
        }
        // This is the compositor-owned equivalent of LUMINOPHORE's near + bloom
        // profile. Hyprland's inner-glow shader provides the rounded SDF and
        // clipping; LUMINOPHORE continues to own the palette and breathing curve.
        const bool active = Desktop::focusState()->isWindowActive(window);
        const auto dynamics = active ? luminophoreGlowDynamics() : SLuminophoreGlowDynamics{.intensity = 0.82F, .radius = 1.0F};
        const CHyprColor color{
            active ?
                CHyprColor(0.612F, 0.796F, 0.984F, 1.0F) : // DP-2 raw_primary #9CCBFB
                CHyprColor(1.000F, 0.718F, 0.490F, 1.0F)   // DP-1 raw_primary #FFB77D
        };
        const int bloomRange = std::max(1, static_cast<int>(std::lround(40.0F * dynamics.radius)));
        const int nearRange = std::max(1, static_cast<int>(std::lround(10.0F * dynamics.radius)));
        const float bloomAlpha = std::min(1.0F, 2.6F * 0.070F * dynamics.intensity) * alpha;
        const float nearAlpha = std::min(1.0F, 2.6F * 0.29F * dynamics.intensity) * alpha;
        const float rounding = std::max(0.0F, window->rounding());
        const float roundingPower = std::max(1.0F, window->roundingPower());
        const bool floating = window->m_isFloating;
        if (!floating)
            renderTiledCornerMask(box, rounding, roundingPower);
        renderLuminophoreInnerGlow(box, color, rounding, roundingPower, bloomRange, 3, bloomAlpha);
        renderLuminophoreInnerGlow(box, color, rounding, roundingPower, nearRange, 3, nearAlpha);
    }

    eDecorationType getDecorationType() override {
        return DECORATION_CUSTOM;
    }

    void updateWindow(PHLWINDOW) override {}

    void damageEntire() override {
        const auto window = m_window.lock();
        if (window)
            g_pHyprRenderer->damageWindow(window, true);
    }

    eDecorationLayer getDecorationLayer() override {
        return DECORATION_LAYER_OVER;
    }

    uint64_t getDecorationFlags() override {
        return DECORATION_NON_SOLID;
    }

    std::string getDisplayName() override {
        return "LUMINOPHORE window glow prototype";
    }

  private:
    PHLWINDOWREF m_window;
};

std::vector<UP<IPassElement>> CLuminophoreGlowPassElement::draw() {
    if (m_decoration)
        m_decoration->drawPass(g_pHyprRenderer->m_renderData.pMonitor.lock(), m_alpha);
    return {};
}

static bool attachGlow(const PHLWINDOW& window) {
    if (!window || !window->m_isMapped || isAttached(window))
        return false;

    auto decoration = makeUnique<CLuminophoreWindowGlowDecoration>(window);
    auto* rawDecoration = decoration.get();
    const bool attached = HyprlandAPI::addWindowDecoration(PHANDLE, window, std::move(decoration));
    if (attached) {
        PATTACHEDGLOWS.push_back({.window = window, .decoration = rawDecoration});
        g_pHyprRenderer->damageWindow(window, true);
    }
    return attached;
}

static void detachGlow(const PHLWINDOW& window) {
    const auto attached = std::ranges::find_if(PATTACHEDGLOWS, [&window](const auto& candidate) {
        return candidate.window.lock() == window;
    });
    if (attached == PATTACHEDGLOWS.end())
        return;

    if (attached->decoration)
        HyprlandAPI::removeWindowDecoration(PHANDLE, attached->decoration);
    PATTACHEDGLOWS.erase(attached);
}

static void damageAttachedWindows() {
    for (const auto& attached : PATTACHEDGLOWS) {
        const auto window = attached.window.lock();
        if (window && window->m_isMapped)
            g_pHyprRenderer->damageWindow(window, true);
    }
}

static void enqueueFullscreenGlow() {
    const auto window = g_pHyprRenderer->m_renderData.currentWindow.lock();
    const auto monitor = g_pHyprRenderer->m_renderData.pMonitor.lock();
    if (!window || !monitor ||
        !Fullscreen::controller()->isFullscreen(window, Fullscreen::FSMODE_FULLSCREEN, true))
        return;

    const auto attached = std::ranges::find_if(PATTACHEDGLOWS, [&window](const auto& candidate) {
        return candidate.window.lock() == window;
    });
    if (attached != PATTACHEDGLOWS.end() && attached->decoration)
        // Hyprland forces renderdata.decorate=false for true fullscreen
        // clients. Reinsert only this inset OVER pass after the client pass.
        attached->decoration->draw(monitor, 1.0F);
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    const std::string compositorHash = __hyprland_api_get_hash();
    const std::string clientHash = __hyprland_api_get_client_hash();
    if (compositorHash != clientHash) {
        Log::logger->log(
            Log::ERR,
            "[luminophore-window-glow] ABI mismatch: compositor={} client={}",
            compositorHash,
            clientHash
        );
        throw std::runtime_error(
            "[luminophore-window-glow] Hyprland header version mismatch: compositor=" +
            compositorHash + " client=" + clientHash
        );
    }

    POPENLISTENER = Event::bus()->m_events.window.openLate.listen([](PHLWINDOW window) {
        attachGlow(window);
    });
    PCLOSELISTENER = Event::bus()->m_events.window.close.listen([](PHLWINDOW window) {
        detachGlow(window);
    });
    PFOCUSLISTENER = Event::bus()->m_events.window.active.listen([](PHLWINDOW, Desktop::eFocusReason) {
        damageAttachedWindows();
    });
    PFULLSCREENLISTENER = Event::bus()->m_events.window.fullscreen.listen([](PHLWINDOW window) {
        if (window && window->m_isMapped)
            g_pHyprRenderer->damageWindow(window, true);
    });
    PFLOATINGLISTENER = Event::bus()->m_events.window.floating.listen([](PHLWINDOW window) {
        if (window && window->m_isMapped)
            g_pHyprRenderer->damageWindow(window, true);
    });
    PRENDERSTAGELISTENER = Event::bus()->m_events.render.stage.listen([](eRenderStage stage) {
        if (stage == RENDER_POST_WINDOW)
            enqueueFullscreenGlow();
    });

    for (const auto& window : Desktop::windowState()->windows())
        attachGlow(window);
    startBreathingTimer();
    startBootstrapDamageTimer();

    Log::logger->log(Log::INFO, "[luminophore-window-glow] attached to {} existing windows", PATTACHEDGLOWS.size());
    return {
        "LUMINOPHORE Window Glow",
        "Compositor-owned inner glow for normal Hyprland windows",
        "louise",
        "0.2.0",
    };
}

APICALL EXPORT void PLUGIN_EXIT() {
    POPENLISTENER.reset();
    PCLOSELISTENER.reset();
    PFOCUSLISTENER.reset();
    PFULLSCREENLISTENER.reset();
    PFLOATINGLISTENER.reset();
    PRENDERSTAGELISTENER.reset();

    if (PBREATHTIMER) {
        PBREATHTIMER->cancel();
        g_pEventLoopManager->removeTimer(PBREATHTIMER);
        PBREATHTIMER.reset();
    }
    if (PBOOTSTRAPTIMER) {
        PBOOTSTRAPTIMER->cancel();
        g_pEventLoopManager->removeTimer(PBOOTSTRAPTIMER);
        PBOOTSTRAPTIMER.reset();
    }

    g_pHyprRenderer->m_renderPass.removeAllOfType("CLuminophoreGlowPassElement");
    for (const auto& attached : PATTACHEDGLOWS) {
        if (attached.decoration)
            HyprlandAPI::removeWindowDecoration(PHANDLE, attached.decoration);
    }
    PATTACHEDGLOWS.clear();

    if (PLUMINOPHOREINNERSHADER) {
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        PLUMINOPHOREINNERSHADER.reset();
    }
    PSHADERATTEMPTED = false;
}
