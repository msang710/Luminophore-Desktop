#include "LuminophoreSpatialNativeAdapter.hpp"
#include "LuminophoreSpatialTopology.hpp"
#include "LuminophoreMonitorTransaction.hpp"
#include "../layout/target/Target.hpp"
#include "../desktop/view/Window.hpp"
#include "../desktop/state/FocusState.hpp"
#include "../desktop/state/ViewState.hpp"
#include "../managers/SeatManager.hpp"
#include "../render/Renderer.hpp"
#include "../state/MonitorState.hpp"
#include "../output/Monitor.hpp"
using namespace Luminophore;
void Luminophore::SpatialNative::applyOutputOverrides(SLuminophoreSpatialCommit& commit, eLuminophorePresentationMode mode, std::optional<LuminophoreWindowKey> wideKey,
                                               const CLuminophoreSpatialWindowRegistry& registry, bool preserveHidden) {
    std::map<uint64_t, LuminophoreWindowKey> occupied;
    for (auto& entry : commit.entries) {
        if ((preserveHidden && !entry.visible) || mode == eLuminophorePresentationMode::DESKTOP || (mode == eLuminophorePresentationMode::WIDE && (wideKey == entry.key || !entry.visible)))
            continue;
        const auto target = registry.targetFor(entry.key);
        const auto window = target ? target->window() : nullptr;
        if (!window)
            continue;
        const auto presentation = monitorTransaction()->windowState(window);
        const auto monitor      = presentation.monitor.lock();
        if (presentation.mode != WINDOW_PRESENTATION_OCCUPY_OUTPUT || !monitor)
            continue;
        const auto box                  = monitor->logicalBox();
        entry.visible                   = true;
        entry.primaryOutputID           = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get()));
        entry.clientBox                 = {.x = static_cast<int>(box.x), .y = static_cast<int>(box.y), .width = static_cast<int>(box.w), .height = static_cast<int>(box.h)};
        entry.fragments                 = {{.key = entry.key, .outputID = entry.primaryOutputID, .box = entry.clientBox}};
        occupied[entry.primaryOutputID] = entry.key;
    }
    for (auto& entry : commit.entries) {
        const auto target = registry.targetFor(entry.key);
        const auto window = target ? target->window() : nullptr;
        if (window && window->isAllowedOverFullscreen())
            continue;
        std::erase_if(entry.fragments, [&](const auto& f) { return occupied.contains(f.outputID) && occupied.at(f.outputID) != entry.key; });
        entry.visible = !entry.fragments.empty();
        if (!entry.visible) {
            entry.primaryOutputID = 0;
            entry.clientBox       = {};
        }
    }
}
std::optional<SpatialNative::CResolvedBatch> SpatialNative::CResolvedBatch::prepare(const SLuminophoreSpatialCommit& commit, const CLuminophoreSpatialWindowRegistry& registry) {
    CResolvedBatch batch;
    for (const auto& entry : commit.entries) {
        const auto target = registry.targetFor(entry.key);
        const auto window = target ? target->window() : nullptr;
        if (!window)
            return std::nullopt;
        batch.m_targets.emplace(entry.key, SResolvedTarget{.target = target, .window = window});
        if (entry.visible) {
            const auto monitor = SpatialTopology::monitorForOutput(entry.primaryOutputID);
            if (!monitor)
                return std::nullopt;
            batch.m_monitors.emplace(entry.primaryOutputID, monitor);
        }
    }
    return batch;
}
bool SpatialNative::CResolvedBatch::apply(const std::vector<SLuminophoreSpatialCommitEntry>& entries, uint64_t revision, const std::map<LuminophoreWindowKey, SLuminophorePhysicalBox>& starts) const {
    for (const auto& entry : entries) {
        const auto& resolved = m_targets.at(entry.key);
        const auto& target   = resolved.target;
        const auto& window   = resolved.window;

        if (g_pHyprRenderer)
            g_pHyprRenderer->damageWindow(window, true);

        if (entry.visible) {
            const auto monitor        = m_monitors.at(entry.primaryOutputID);
            const bool monitorChanged = window->m_monitor.lock() != monitor;
            window->m_monitor         = monitor;
            if (monitorChanged)
                window->updateSurfaceScaleTransformDetails();
            monitorTransaction()->commitSpatialState(window, monitor, revision);
        }

        window->setInputBlocked(Desktop::View::INPUT_BLOCK_BELOW_FULLSCREEN, false);
        window->alpha(Desktop::View::WINDOW_ALPHA_FULLSCREEN)->setValueAndWarp(1.F);
        window->setSpatiallySuppressed(!entry.visible);
        window->setInputBlocked(Desktop::View::INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW, !entry.visible);
        if (starts.contains(entry.key)) {
            const auto& start = starts.at(entry.key);
            window->positionAnimation()->setValueAndWarp({start.x, start.y});
        }
        if (entry.visible)
            target->setPositionGlobal(CBox{entry.clientBox.x, entry.clientBox.y, entry.clientBox.width, entry.clientBox.height});
    }
    return true;
}
void Luminophore::SpatialNative::releaseSpatialInput(const PHLWINDOW& window) {
    if (!window)
        return;
    window->setSpatiallySuppressed(false);
    window->setInputBlocked(Desktop::View::INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW, false);
}
void Luminophore::SpatialNative::focus(const PHLWINDOW& window) {
    Desktop::focusState()->fullWindowFocus(window, Desktop::FOCUS_REASON_DESKTOP_STATE_CHANGE);
}
void Luminophore::SpatialNative::clearClientPointerFocus() {
    if (g_pSeatManager && g_pSeatManager->m_state.pointerFocus &&
        Desktop::viewState()->query().type(Desktop::View::VIEW_TYPE_WINDOW).surface(g_pSeatManager->m_state.pointerFocus.lock()).runWindow())
        g_pSeatManager->setPointerFocus(nullptr, {});
}
void Luminophore::SpatialNative::damageOutputs() {
    if (!g_pHyprRenderer)
        return;
    for (const auto& monitor : State::monitorState()->monitors())
        g_pHyprRenderer->damageMonitor(monitor);
}
