#include "LuminophoreFrameScheduler.hpp"
#include "../../debug/LuminophoreDiagnostics.hpp"
#include "../../desktop/state/FocusState.hpp"
#include "../../desktop/view/Window.hpp"
#include "../../managers/eventLoop/EventLoopManager.hpp"
#include "../../output/Monitor.hpp"
#include "../Renderer.hpp"

#include <algorithm>
#include <chrono>

using namespace Render;

CLuminophoreFrameScheduler::CLuminophoreFrameScheduler(CLuminophoreDiagnostics* diagnostics) : m_diagnostics(diagnostics) {
    ;
}

CLuminophoreFrameScheduler::~CLuminophoreFrameScheduler() {
    if (!g_pEventLoopManager)
        return;

    for (auto& [id, animation] : m_monitors) {
        if (!animation.timer)
            continue;
        animation.timer->cancel();
        g_pEventLoopManager->removeTimer(animation.timer);
    }
}

CBox CLuminophoreFrameScheduler::unionBoxes(const CBox& first, const CBox& second) {
    if (first.empty())
        return second;
    if (second.empty())
        return first;

    const auto TOP_LEFT     = Vector2D{std::min(first.x, second.x), std::min(first.y, second.y)};
    const auto BOTTOM_RIGHT = first.extent().getComponentMax(second.extent());
    return {TOP_LEFT, BOTTOM_RIGHT - TOP_LEFT};
}

void CLuminophoreFrameScheduler::track(PHLWINDOW window, PHLMONITOR monitor, const CBox& damage) {
    if (!window || !monitor || !window->m_isMapped || !Desktop::focusState()->isWindowActive(window))
        return;

    auto& animation          = m_monitors[monitor->m_id];
    animation.monitor        = monitor;
    animation.window         = window;
    animation.previousDamage = animation.damage;
    animation.damage         = damage;
    animation.lastSeen       = std::chrono::steady_clock::now();
}

void CLuminophoreFrameScheduler::presented(PHLMONITOR monitor) {
    if (!monitor || !g_pEventLoopManager)
        return;

    if (m_diagnostics)
        m_diagnostics->recordPresented(monitor->m_id);

    const auto IT = m_monitors.find(monitor->m_id);
    if (IT == m_monitors.end())
        return;

    auto&      animation = IT->second;
    const auto WINDOW    = animation.window.lock();
    if (!WINDOW || !WINDOW->m_isMapped || !Desktop::focusState()->isWindowActive(WINDOW) || std::chrono::steady_clock::now() - animation.lastSeen > std::chrono::milliseconds(250))
        return;

    if (!animation.timer) {
        const auto ID   = monitor->m_id;
        animation.timer = makeShared<CEventLoopTimer>(std::chrono::milliseconds(33), [this, ID](SP<CEventLoopTimer>, void*) { requestFrame(ID); }, nullptr);
        g_pEventLoopManager->addTimer(animation.timer);
    } else
        animation.timer->updateTimeout(std::chrono::milliseconds(33));
}

void CLuminophoreFrameScheduler::requestFrame(MONITORID monitorID) {
    const auto IT = m_monitors.find(monitorID);
    if (IT == m_monitors.end())
        return;

    auto&      animation = IT->second;
    const auto MONITOR   = animation.monitor.lock();
    const auto WINDOW    = animation.window.lock();
    if (!MONITOR || !WINDOW || !WINDOW->m_isMapped || !Desktop::focusState()->isWindowActive(WINDOW) ||
        std::chrono::steady_clock::now() - animation.lastSeen > std::chrono::milliseconds(250))
        return;

    const auto DAMAGE = unionBoxes(animation.previousDamage, animation.damage);
    MONITOR->addDamage(DAMAGE);
    if (m_diagnostics)
        m_diagnostics->recordFrameRequest(monitorID, LUMINOPHORE_FRAME_WINDOW_GLOW_ACTIVE, DAMAGE, !animation.previousDamage.empty());
}
