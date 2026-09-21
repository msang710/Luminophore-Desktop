#include <hyprutils/utils/ScopeGuard.hpp>
#include "../../luminophore/LuminophoreSpatialRuntime.hpp"
#include "WindowPlacementController.hpp"

#include "FocusState.hpp"
#include "GlobalWindowController.hpp"
#include "../Workspace.hpp"
#include "../view/Window.hpp"
#include "../../layout/space/Space.hpp"
#include "../../layout/target/Target.hpp"
#include "../../managers/EventManager.hpp"
#include "../../render/Renderer.hpp"

#include <format>

using namespace Desktop;

UP<CWindowPlacementController>& Desktop::windowPlacementController() {
    static UP<CWindowPlacementController> controller = makeUnique<CWindowPlacementController>();
    return controller;
}

bool CWindowPlacementController::minimize(const PHLWINDOW& window) {
    Luminophore::spatialRuntime()->historyActionBegin();
    Hyprutils::Utils::CScopeGuard historyAction([] { Luminophore::spatialRuntime()->historyActionEnd(); });
    if (!window || !window->m_isMapped || window->isMinimized() || !window->m_workspace || !window->layoutTarget())
        return false;

    const auto TARGET = window->layoutTarget();
    const auto SPACE  = TARGET->space();
    if (!SPACE)
        return false;

    ++m_revision;
    if (m_revision == 0)
        ++m_revision;

    window->m_restorePlacement = SWindowRestorePlacement{
        .workspace   = window->m_workspace,
        .monitor     = window->m_monitor,
        .floatingBox = TARGET->position(),
        .wasFloating = window->m_isFloating,
        .revision    = m_revision,
    };
    window->m_minimized = true;

    TARGET->setSpaceGhost(SPACE);
    window->setSuspended(true);
    if (focusState()->window() == window)
        focusState()->fullWindowFocus(window->m_workspace->getFocusCandidate(), FOCUS_REASON_DESKTOP_STATE_CHANGE);

    if (g_pHyprRenderer)
        g_pHyprRenderer->damageMonitor(window->m_monitor.lock());
    if (g_pEventManager)
        g_pEventManager->postEvent(SHyprIPCEvent{.event = "luminophoreplacement", .data = std::format("{:x},minimized", rc<uintptr_t>(window.get()))});
    return true;
}

bool CWindowPlacementController::restore(const PHLWINDOW& window, std::optional<Vector2D> focalPoint) {
    Luminophore::spatialRuntime()->historyActionBegin();
    Hyprutils::Utils::CScopeGuard historyAction([] { Luminophore::spatialRuntime()->historyActionEnd(); });
    if (!window || !window->m_isMapped || !window->m_restorePlacement || !window->layoutTarget())
        return false;

    const auto RESTORE   = *window->m_restorePlacement;
    const auto WORKSPACE = RESTORE.workspace.lock();
    if (!WORKSPACE || !WORKSPACE->m_space || !WORKSPACE->m_monitor)
        return false;

    window->m_minimized = false;
    window->layoutTarget()->setFloating(RESTORE.wasFloating);
    window->m_restorePlacement.reset();
    window->layoutTarget()->assignToSpace(WORKSPACE->m_space, focalPoint);
    if (RESTORE.wasFloating)
        window->layoutTarget()->setPositionGlobal(RESTORE.floatingBox);

    window->setSuspended(false);
    focusState()->fullWindowFocus(window, FOCUS_REASON_DESKTOP_STATE_CHANGE);
    globalWindowController()->updateSuspendedStates();
    if (g_pHyprRenderer)
        g_pHyprRenderer->damageMonitor(WORKSPACE->m_monitor.lock());
    if (g_pEventManager)
        g_pEventManager->postEvent(
            SHyprIPCEvent{.event = "luminophoreplacement", .data = std::format("{:x},{}", rc<uintptr_t>(window.get()), RESTORE.wasFloating ? "floating" : "tiled")});
    return true;
}

bool CWindowPlacementController::rehomeMinimized(const PHLWINDOW& window, const PHLWORKSPACE& workspace) {
    if (!window || !workspace || !workspace->m_monitor || !window->isMinimized() || !window->m_restorePlacement)
        return false;

    window->moveToWorkspace(workspace);
    window->m_monitor                     = workspace->m_monitor;
    window->m_restorePlacement->workspace = workspace;
    window->m_restorePlacement->monitor   = workspace->m_monitor;
    window->updateToplevel();
    window->m_ruleApplicator->propertiesChanged(Desktop::Rule::RULE_PROP_ON_WORKSPACE);
    return true;
}

eWindowPlacement CWindowPlacementController::placement(const PHLWINDOW& window) const {
    if (!window)
        return WINDOW_PLACEMENT_TILED;
    if (window->isMinimized())
        return WINDOW_PLACEMENT_MINIMIZED;
    return window->m_isFloating ? WINDOW_PLACEMENT_FLOATING : WINDOW_PLACEMENT_TILED;
}

const std::optional<SWindowRestorePlacement>& CWindowPlacementController::restorePlacement(const PHLWINDOW& window) const {
    static const std::optional<SWindowRestorePlacement> EMPTY;
    return window ? window->m_restorePlacement : EMPTY;
}
