#include "LuminophoreMonitorTransaction.hpp"

#include "LuminophoreShellProjection.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "../desktop/view/Window.hpp"
#include "../output/Monitor.hpp"
#include "../render/Renderer.hpp"
#include "../render/luminophore/LuminophoreWindowEffect.hpp"

using namespace Luminophore;

uint64_t CLuminophoreMonitorTransaction::requestFrame(PHLMONITOR monitor) {
    if (!monitor)
        return 0;

    auto& state = m_monitors[monitor->m_id];
    ++state.requestedRevision;
    monitor->scheduleFrame(Aquamarine::IOutput::AQ_SCHEDULE_DAMAGE);
    return state.requestedRevision;
}

void CLuminophoreMonitorTransaction::beginFrame(PHLMONITOR monitor) {
    if (!monitor)
        return;

    auto& state = m_monitors[monitor->m_id];
    shellProjection()->applyPendingForMonitor(monitor);
    shellProjection()->resolveFrameForMonitor(monitor);
    state.committedRevision = state.requestedRevision;
}

void CLuminophoreMonitorTransaction::presented(PHLMONITOR monitor) {
    if (!monitor)
        return;

    auto& state             = m_monitors[monitor->m_id];
    state.presentedRevision = state.committedRevision;
    shellProjection()->presentedForMonitor(monitor);
#ifdef LUMINOPHORE_EFFECTS
    if (g_pHyprRenderer && g_pHyprRenderer->m_luminophoreWindowEffect)
        g_pHyprRenderer->m_luminophoreWindowEffect->presented(monitor);
#endif
}

void CLuminophoreMonitorTransaction::syncWindowState(PHLWINDOW window, int8_t internalMode, int8_t clientMode, bool layoutManaged) {
    if (!window)
        return;

    auto& state = m_windows[window];
    if (internalMode != 0 && state.mode != WINDOW_PRESENTATION_OCCUPY_OUTPUT)
        state.restoreBox = window->geometricBox(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
    state.monitor       = window->m_monitor;
    state.clientMode    = clientMode;
    state.layoutManaged = layoutManaged;
    state.mode          = internalMode != 0 ? WINDOW_PRESENTATION_OCCUPY_OUTPUT : window->m_isFloating ? WINDOW_PRESENTATION_FLOATING : WINDOW_PRESENTATION_TILED;
    ++state.revision;
}

void CLuminophoreMonitorTransaction::commitSpatialState(PHLWINDOW window, PHLMONITOR monitor, uint64_t revision) {
    if (!window || !monitor)
        return;

    auto& state    = m_windows[window];
    state.monitor  = monitor;
    state.revision = revision;
    if (state.mode != WINDOW_PRESENTATION_OCCUPY_OUTPUT)
        state.mode = window->m_isFloating ? WINDOW_PRESENTATION_FLOATING : WINDOW_PRESENTATION_TILED;
}

SWindowPresentationState CLuminophoreMonitorTransaction::windowState(PHLWINDOW window) const {
    if (!window)
        return {};

    const auto IT = m_windows.find(window);
    if (IT == m_windows.end())
        return SWindowPresentationState{
            .mode    = window->m_isFloating ? WINDOW_PRESENTATION_FLOATING : WINDOW_PRESENTATION_TILED,
            .monitor = window->m_monitor,
        };
    return IT->second;
}

SMonitorFrameTransaction CLuminophoreMonitorTransaction::monitorState(PHLMONITOR monitor) const {
    if (!monitor)
        return {};
    const auto IT = m_monitors.find(monitor->m_id);
    return IT == m_monitors.end() ? SMonitorFrameTransaction{} : IT->second;
}

UP<CLuminophoreMonitorTransaction>& Luminophore::monitorTransaction() {
    static UP<CLuminophoreMonitorTransaction> transaction = makeUnique<CLuminophoreMonitorTransaction>();
    return transaction;
}
