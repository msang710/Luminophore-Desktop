#include "LuminophoreMonitorLayout.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "../Compositor.hpp"
#include "LuminophoreSpatialGrabController.hpp"
#include "LuminophoreSpatialNativeAdapter.hpp"
#include "LuminophoreSpatialTopology.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include "LuminophoreMonitorTransaction.hpp"
#include "../config/ConfigValue.hpp"
#include <cmath>
#include "../desktop/state/WindowState.hpp"
#include "../desktop/state/WindowPlacementController.hpp"
#include "../desktop/state/FocusState.hpp"
#include "../desktop/view/Window.hpp"
#include "../desktop/Workspace.hpp"
#include "../managers/fullscreen/FullscreenController.hpp"
#include "../layout/LayoutManager.hpp"
#include "../layout/supplementary/DragController.hpp"
#include "../layout/target/Target.hpp"
#include "../output/Monitor.hpp"
#include <hyprutils/utils/ScopeGuard.hpp>
#include <algorithm>
using namespace Luminophore;

SLuminophoreHistoryFrame CLuminophoreSpatialRuntime::historyFrame() {
    SLuminophoreHistoryFrame frame;
    if (!m_model)
        return frame;
    frame.snapshot = m_model->snapshot();
    std::erase_if(m_lifetimes, [](const auto& item) { return item.second.first.expired(); });
    for (const auto& window : Desktop::windowState()->windows()) {
        if (!window || !window->m_isMapped || !window->layoutTarget() || !window->m_workspace)
            continue;
        const auto key   = static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(window.get()));
        const auto found = m_lifetimes.find(key);
        if (found == m_lifetimes.end() || found->second.first.lock() != window)
            m_lifetimes[key] = {window, ++m_nextLifetime};
        frame.lifetimes[key]     = m_lifetimes.at(key).second;
        auto& state              = frame.native[key];
        state.floating           = window->m_isFloating;
        state.minimized          = window->isMinimized();
        state.auxiliary          = !state.minimized && !m_model->boardFor(key);
        const auto fullscreen    = Fullscreen::controller()->getFullscreenModes(window);
        state.internalFullscreen = fullscreen.internal;
        state.clientFullscreen   = fullscreen.client;
        state.layoutAware        = Fullscreen::controller()->layoutManagedFS(window);
        if (state.auxiliary) {
            const auto position = window->positionAnimation()->goal(), size = window->sizeAnimation()->goal();
            state.x      = position.x;
            state.y      = position.y;
            state.width  = size.x;
            state.height = size.y;
            if (const auto monitor = window->m_monitor.lock())
                state.connector = monitor->m_name;
        }
    }
    if (const auto focused = Desktop::focusState()->window()) {
        const auto key = static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(focused.get()));
        if (frame.lifetimes.contains(key))
            frame.focus = key;
    }
    return frame;
}
void CLuminophoreSpatialRuntime::recordHistory(SLuminophoreHistoryFrame before, const std::string& group) {
    if (MonitorLayout::applying() || !before.snapshot.independent || m_restoringHistory || m_resizeStart || m_historyActionDepth || CLuminophoreSpatialCommitter::isApplying())
        return;
    auto after = historyFrame();
    m_auxiliaryGesture.mask(before);
    m_auxiliaryGesture.mask(after);
    m_history.record(std::move(before), std::move(after), group);
}
void CLuminophoreSpatialRuntime::beginAuxiliaryGesture(const SP<Layout::ITarget>& target) {
    if (!target || !target->window() || managesFloatingTarget(target) || managesTiledTarget(target))
        return;
    m_auxiliaryGesture.begin(historyFrame(), static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(target->window().get())));
}
void CLuminophoreSpatialRuntime::endAuxiliaryGesture(bool cancelled) {
    if (!m_auxiliaryGesture.active())
        return;
    auto result = m_auxiliaryGesture.finish(historyFrame());
    if (!result)
        return;
    if (cancelled) {
        m_restoringHistory = true;
        Hyprutils::Utils::CScopeGuard restoring([this] { m_restoringHistory = false; });
        applyHistoryGeometry(result->first);
    } else
        m_history.record(std::move(result->first), std::move(result->second));
}
void CLuminophoreSpatialRuntime::historyActionBegin() {
    if (m_restoringHistory)
        return;
    // A native state change must not become part of an in-progress resize.
    // Compensate the resize first, then record this independent change.
    if (m_resizeStart && !CLuminophoreSpatialCommitter::isApplying())
        resizeGesture(false, true);
    if (m_historyActionDepth++ == 0 && !CLuminophoreSpatialCommitter::isApplying())
        m_historyActionStart = historyFrame();
}
void CLuminophoreSpatialRuntime::historyActionEnd() {
    if (m_restoringHistory || !m_historyActionDepth)
        return;
    if (--m_historyActionDepth == 0 && m_historyActionStart) {
        auto before = std::move(*m_historyActionStart);
        m_historyActionStart.reset();
        recordHistory(std::move(before));
    }
}
size_t CLuminophoreSpatialRuntime::undoCount() const {
    return m_history.undoCount();
}
size_t CLuminophoreSpatialRuntime::redoCount() const {
    return m_history.redoCount();
}

bool CLuminophoreSpatialRuntime::applyHistoryNative(const SLuminophoreHistoryFrame& frame) {
    std::map<LuminophoreWindowKey, PHLWINDOW> targets;
    // Validate the complete native batch before changing any window.
    for (const auto& [key, desired] : frame.native) {
        const auto found = m_lifetimes.find(key);
        if (found == m_lifetimes.end() || !frame.lifetimes.contains(key) || found->second.second != frame.lifetimes.at(key))
            continue;
        const auto window = found->second.first.lock();
        if (!window || !window->m_isMapped)
            continue;
        if (!window->layoutTarget() || !window->m_workspace || !window->m_monitor)
            return false;
        if (window->isMinimized() && !desired.minimized) {
            const auto& restore   = Desktop::windowPlacementController()->restorePlacement(window);
            const auto  workspace = restore ? restore->workspace.lock() : nullptr;
            if (!workspace || !workspace->m_space || !workspace->m_monitor)
                return false;
        }
        if (desired.auxiliary && std::ranges::none_of(m_outputs, [&](const auto& o) { return o.name == desired.connector; }))
            return false;
        targets[key] = window;
    }
    for (const auto& [key, window] : targets) {
        const auto& desired = frame.native.at(key);
        if (window->isMinimized() && !desired.minimized && !Desktop::windowPlacementController()->restore(window))
            return false;
        if (!window->isMinimized() && !desired.minimized && window->m_isFloating != desired.floating) {
            g_layoutManager->changeFloatingMode(window->layoutTarget());
            if (window->m_isFloating != desired.floating)
                return false;
        }
        if (!desired.minimized) {
            const auto fs = Fullscreen::controller()->getFullscreenModes(window);
            if (fs.internal != desired.internalFullscreen || fs.client != desired.clientFullscreen)
                Fullscreen::controller()->setFullscreenMode(window, static_cast<Fullscreen::eFullscreenMode>(desired.internalFullscreen),
                                                            static_cast<Fullscreen::eFullscreenMode>(desired.clientFullscreen), desired.layoutAware);
            const auto actual = Fullscreen::controller()->getFullscreenModes(window);
            if (actual.internal != desired.internalFullscreen || actual.client != desired.clientFullscreen)
                return false;
            if (!desired.auxiliary)
                m_windows.remember(key, window->layoutTarget());
        } else if (!window->isMinimized() && !Desktop::windowPlacementController()->minimize(window))
            return false;
    }
    return true;
}
void CLuminophoreSpatialRuntime::applyHistoryGeometry(const SLuminophoreHistoryFrame& frame) {
    // Parent geometry must settle first; otherwise restoring a child before its
    // parent would apply the parent's translation a second time.
    for (const auto& [key, desired] : frame.native) {
        if (!desired.auxiliary || desired.minimized)
            continue;
        const auto it = m_lifetimes.find(key);
        if (it == m_lifetimes.end() || !frame.lifetimes.contains(key) || it->second.second != frame.lifetimes.at(key))
            continue;
        const auto window = it->second.first.lock();
        if (window && window->m_isMapped && window->layoutTarget()) {
            const auto output  = std::ranges::find(m_outputs, desired.connector, &SLuminophorePhysicalOutput::name);
            const auto monitor = output == m_outputs.end() ? nullptr : SpatialTopology::monitorForOutput(output->id);
            if (monitor && window->m_monitor.lock() != monitor) {
                window->m_monitor = monitor;
                window->updateSurfaceScaleTransformDetails();
            }
            window->layoutTarget()->setPositionGlobal(CBox{desired.x, desired.y, desired.width, desired.height});
        }
    }
}
bool CLuminophoreSpatialRuntime::commitHistory(const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreHistoryFrame& frame) {
    const auto plan     = CLuminophoreSpatialProjection::plan(snapshot, m_topologyRevision, m_outputs);
    const auto prepared = plan ? CLuminophoreSpatialCommitter::prepare(*plan) : std::nullopt;
    if (!prepared) {
        m_historyPreflightRejected = true;
        return false;
    }
    static auto forceZero = CConfigValue<Config::INTEGER>("xwayland:force_zero_scaling");
    for (const auto& entry : prepared->entries) {
        if (!entry.visible)
            continue;
        const auto found  = m_lifetimes.find(entry.key);
        const auto window = found == m_lifetimes.end() ? nullptr : found->second.first.lock();
        if (!window || !window->m_isMapped || !window->layoutTarget()) {
            m_historyPreflightRejected = true;
            return false;
        }
        const auto desired = frame.native.find(entry.key);
        if (snapshot.presentationMode == eLuminophorePresentationMode::WIDE || (desired != frame.native.end() && desired->second.internalFullscreen))
            continue;
        auto minimum = window->layoutTarget()->minSize().value_or(Vector2D{1, 1});
        if (window->m_isX11 && *forceZero && window->m_monitor)
            minimum /= window->m_monitor->m_scale;
        if (!std::isfinite(minimum.x) || !std::isfinite(minimum.y) || entry.clientBox.width < minimum.x || entry.clientBox.height < minimum.y) {
            m_historyPreflightRejected = true;
            return false;
        }
    }
    const auto before = historyFrame();
    const auto result = luminophoreApplyHistoryBatch(
        [&] {
            if (!applyHistoryNative(frame) || !commitSnapshot(snapshot))
                return false;
            applyHistoryOwnership(snapshot);
            applyHistoryGeometry(frame);
            return true;
        },
        [&] {
            if (!applyHistoryNative(before) || !commitSnapshot(before.snapshot))
                return false;
            applyHistoryOwnership(before.snapshot);
            applyHistoryGeometry(before);
            return true;
        });
    if (result == eLuminophoreHistoryApplyResult::DEGRADED) {
        m_historyRecoveryFailed = true;
        SpatialNative::damageOutputs();
    }
    return result == eLuminophoreHistoryApplyResult::APPLIED;
}

eLuminophoreHistoryResult CLuminophoreSpatialRuntime::historyReplay(bool redo) {
    if (m_historyRecoveryFailed)
        return eLuminophoreHistoryResult::RECOVERY_FAILED;
    if (!m_model)
        return eLuminophoreHistoryResult::EMPTY;
    if (m_restoringHistory || m_resizeStart || m_historyActionDepth || m_auxiliaryGesture.active() || spatialGrabController()->active() || m_commandQueue.draining() ||
        m_commandQueue.pending() || CLuminophoreSpatialCommitter::isApplying() || (g_layoutManager && g_layoutManager->dragController()->target()))
        return eLuminophoreHistoryResult::BUSY;
    m_historyPreflightRejected = false;
    m_restoringHistory         = true;
    Hyprutils::Utils::CScopeGuard restoring([this] { m_restoringHistory = false; });
    const auto                    currentFocus = Desktop::focusState()->window();
    PHLWINDOW                     desiredFocus;
    const auto                    result = m_history.replayNative(redo, *m_model, historyFrame(), [&](const auto& snapshot, const auto& frame) {
        if (!commitHistory(snapshot, frame))
            return false;
        if (frame.focus) {
            const auto found = m_lifetimes.find(*frame.focus);
            if (found != m_lifetimes.end() && frame.lifetimes.contains(*frame.focus) && found->second.second == frame.lifetimes.at(*frame.focus))
                desiredFocus = found->second.first.lock();
        }
        return true;
    });
    if (result != eLuminophoreHistoryResult::APPLIED)
        return m_historyRecoveryFailed ? eLuminophoreHistoryResult::RECOVERY_FAILED : m_historyPreflightRejected ? eLuminophoreHistoryResult::UNAVAILABLE : result;
    SpatialNative::clearClientPointerFocus();
    const auto eligible = [](const PHLWINDOW& w) {
        return w && w->m_isMapped && !w->isHidden() && !w->isMinimized() &&
            !w->isInputBlockedReasonAnyOf(
                static_cast<std::underlying_type_t<Desktop::View::eWindowInputBlockReason>>(~static_cast<uint32_t>(Desktop::View::INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW)));
    };
    const auto focus = eligible(desiredFocus) ? desiredFocus : eligible(currentFocus) ? currentFocus : nullptr;
    if (m_model->snapshot().presentationMode == eLuminophorePresentationMode::DESKTOP) {
        m_desktopFocus = focus;
        m_anchorKey.reset();
        m_committedFocusKey.reset();
        SpatialNative::focus(nullptr);
    } else if (focus) {
        m_anchorKey         = static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(focus.get()));
        m_committedFocusKey = m_anchorKey;
        SpatialNative::focus(focus);
    }
    notifyStateChanged();
    return result;
}

bool CLuminophoreSpatialRuntime::captureGeometryHistory(const SP<Layout::ITarget>& target) const {
    if (!m_model || !target || !target->window() || m_restoringHistory || m_historyActionDepth || CLuminophoreSpatialCommitter::isApplying())
        return false;
    const auto key = static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(target->window().get()));
    return !m_auxiliaryGesture.owns(key) && participationFor(target) != eSpatialParticipation::BOARD_ROOT;
}

eLuminophoreHistoryResult CLuminophoreSpatialRuntime::historyRequest(bool redo, std::optional<uint64_t> expected, const std::string& id, const std::string& source) {
    if (!id.empty() && (!g_pCompositor || source != g_pCompositor->m_instanceSignature))
        return eLuminophoreHistoryResult::STALE;
    return m_historyRequests.run(redo, revision(), expected, id, [&] { return historyReplay(redo); });
}

void CLuminophoreSpatialRuntime::applyHistoryOwnership(const SLuminophoreSpatialSnapshot& snapshot) {
    if (!snapshot.independent)
        return;
    const auto& state  = *snapshot.independent;
    const auto  assign = [&](LuminophoreWindowKey key, uint64_t board) {
        const auto output  = std::ranges::find_if(state.bindings, [&](const auto& b) { return b.second == board; });
        const auto monitor = output == state.bindings.end() ? nullptr : SpatialTopology::monitorForOutput(output->first);
        const auto it      = m_lifetimes.find(key);
        const auto window  = it == m_lifetimes.end() ? nullptr : it->second.first.lock();
        if (!window || !window->m_isMapped || !monitor)
            return;
        if (window->m_monitor.lock() != monitor) {
            window->m_monitor = monitor;
            window->updateSurfaceScaleTransformDetails();
        }
        monitorTransaction()->commitSpatialState(window, monitor, snapshot.revision);
    };
    for (const auto& [id, board] : state.state.boards)
        for (const auto& [point, key] : board.tiled)
            assign(key, id);
    for (const auto& [key, id] : state.floatingBoards)
        assign(key, id);
}
