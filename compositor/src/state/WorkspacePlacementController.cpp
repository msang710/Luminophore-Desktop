#include "WorkspacePlacementController.hpp"

#include "MonitorState.hpp"
#include "WorkspaceState.hpp"

#include "../config/shared/workspace/WorkspaceRuleManager.hpp"
#include "../debug/log/Logger.hpp"
#include "../desktop/Workspace.hpp"
#include "../desktop/state/FocusState.hpp"
#include "../desktop/state/WindowState.hpp"
#include "../desktop/state/ViewState.hpp"
#include "../desktop/state/GlobalWindowController.hpp"
#include "../desktop/state/WindowPlacementController.hpp"
#include "../desktop/view/Window.hpp"
#include "../helpers/MiscFunctions.hpp"
#include "../output/Monitor.hpp"
#include "../layout/target/Target.hpp"
#include "../layout/LayoutManager.hpp"
#include "../layout/space/Space.hpp"
#include "../managers/EventManager.hpp"
#include "../managers/fullscreen/FullscreenController.hpp"
#include "../managers/eventLoop/EventLoopManager.hpp"
#include "../pointer/PointerManager.hpp"
#include "../event/EventBus.hpp"
#include "../animation/WorkspaceAnimationController.hpp"
#include "../render/Renderer.hpp"

#include <ranges>

using namespace State;

UP<CWorkspacePlacementController>& State::workspacePlacementController() {
    static UP<CWorkspacePlacementController> p = makeUnique<CWorkspacePlacementController>();
    return p;
}

void CWorkspacePlacementController::ensurePersistentWorkspacesPresent(PHLWORKSPACE pWorkspace, const FMoveWorkspace& moveWorkspace) const {
    ensurePersistentWorkspacesPresent(Config::workspaceRuleMgr()->getAllWorkspaceRules(), pWorkspace, moveWorkspace);
}

void CWorkspacePlacementController::ensurePersistentWorkspacesPresent(const std::vector<SP<Config::CWorkspaceRule>>& rules, PHLWORKSPACE pWorkspace,
                                                                      const FMoveWorkspace& moveWorkspace) const {
    if (!Desktop::focusState()->monitor())
        return;

    std::vector<PHLWORKSPACE> persistentFound;

    for (const auto& rulePtr : rules) {
        if (!rulePtr->isEnabled() || !rulePtr->m_isPersistent.value_or(false))
            continue;

        const auto&  rule = *rulePtr;

        PHLWORKSPACE PWORKSPACE = nullptr;
        if (pWorkspace) {
            if (pWorkspace->matchesStaticSelector(rule.m_workspaceString))
                PWORKSPACE = pWorkspace;
            else
                continue;
        }

        auto PMONITOR = State::monitorState()->query().relativeTo(Desktop::focusState()->monitor()).configString(rule.m_monitor).run();

        if (!rule.m_monitor.empty() && !PMONITOR)
            continue; // don't do anything yet, as the monitor is not yet present.

        if (!PWORKSPACE) {
            WORKSPACEID id     = rule.m_workspaceId;
            std::string wsname = rule.m_workspaceName;

            if (id == WORKSPACE_INVALID) {
                const auto R = getWorkspaceIDNameFromString(rule.m_workspaceString);
                id           = R.id;
                wsname       = R.name;
            }

            if (id == WORKSPACE_INVALID) {
                Log::logger->log(Log::ERR, "ensurePersistentWorkspacesPresent: couldn't resolve id for workspace {}", rule.m_workspaceString);
                continue;
            }
            PWORKSPACE = State::workspaceState()->query().id(id).run();
            if (!PMONITOR)
                PMONITOR = Desktop::focusState()->monitor();

            if (!PWORKSPACE)
                PWORKSPACE = State::workspaceState()->create(id, PMONITOR->m_id, wsname, false);
        }

        if (!PMONITOR) {
            Log::logger->log(Log::ERR, "ensurePersistentWorkspacesPresent: couldn't resolve monitor for {}, skipping", rule.m_monitor);
            continue;
        }

        if (PWORKSPACE)
            PWORKSPACE->setPersistent(true);

        if (!pWorkspace)
            persistentFound.emplace_back(PWORKSPACE);

        if (PWORKSPACE) {
            if (PWORKSPACE->m_monitor == PMONITOR) {
                Log::logger->log(Log::DEBUG, "ensurePersistentWorkspacesPresent: workspace persistent {} already on {}", rule.m_workspaceString, PMONITOR->m_name);

                continue;
            }

            Log::logger->log(Log::DEBUG, "ensurePersistentWorkspacesPresent: workspace persistent {} not on {}, moving", rule.m_workspaceString, PMONITOR->m_name);
            moveWorkspace(PWORKSPACE, PMONITOR, false);
            continue;
        }
    }

    if (!pWorkspace) {
        // check non-persistent and downgrade if workspace is no longer persistent
        std::vector<PHLWORKSPACEREF> toDowngrade;
        for (auto& w : State::workspaceState()->workspaces()) {
            if (!w->isPersistent())
                continue;

            if (std::ranges::contains(persistentFound, w.lock()))
                continue;

            toDowngrade.emplace_back(w);
        }

        for (auto& ws : toDowngrade) {
            ws->setPersistent(false);
        }
    }
}

void CWorkspacePlacementController::ensureWorkspacesOnAssignedMonitors(const FMoveWorkspace& moveWorkspace) const {
    for (auto const& ws : State::workspaceState()->workspacesCopy()) {
        if (!valid(ws) || ws->m_isSpecialWorkspace)
            continue;

        const auto RULE = Config::workspaceRuleMgr()->getWorkspaceRuleFor(ws);
        if (!RULE || RULE->m_monitor.empty())
            continue;

        const auto PMONITOR = State::monitorState()->query().relativeTo(Desktop::focusState()->monitor()).configString(RULE->m_monitor).run();
        if (!PMONITOR)
            continue;

        if (ws->m_monitor == PMONITOR)
            continue;

        Log::logger->log(Log::DEBUG, "ensureWorkspacesOnAssignedMonitors: moving workspace {} to {}", ws->m_name, PMONITOR->m_name);
        moveWorkspace(ws, PMONITOR, true);
    }
}

void CWorkspacePlacementController::moveWorkspaceToMonitor(PHLWORKSPACE pWorkspace, PHLMONITOR pMonitor, bool /*noWarpCursor*/) const {

    if (!pWorkspace || !pMonitor)
        return;

    if (pWorkspace->m_monitor == pMonitor)
        return;

    if (pWorkspace->role() != WORKSPACE_ROLE_SERVICE) {
        Log::logger->log(Log::WARN, "moveWorkspaceToMonitor: refusing to detach base desktop {} from its monitor", pWorkspace->m_name);
        return;
    }

    Log::logger->log(Log::DEBUG, "moveWorkspaceToMonitor: Moving {} to monitor {}", pWorkspace->m_id, pMonitor->m_id);

    const auto POLDMON = pWorkspace->m_monitor.lock();

    if (pWorkspace->m_isSpecialWorkspace && POLDMON && POLDMON->m_activeSpecialWorkspace == pWorkspace) {
        pMonitor->setSpecialWorkspace(pWorkspace);
        return;
    }

    // move the workspace
    pWorkspace->m_monitor = pMonitor;
    pWorkspace->m_space->recheckWorkArea();
    pWorkspace->m_events.monitorChanged.emit();

    for (auto const& w : Desktop::windowState()->windows()) {
        if (w->m_workspace == pWorkspace) {

            w->m_monitor = pMonitor;

            // additionally, move floating and fs windows manually
            if (w->m_isMapped && !w->isDesktopSuppressed()) {
                if (POLDMON) {
                    if (w->m_isFloating)
                        w->layoutTarget()->setPositionGlobal(w->layoutTarget()->position().translate(-POLDMON->m_position + pMonitor->m_position));

                    if (Fullscreen::controller()->isFullscreen(w))
                        w->setBox({pMonitor->m_position, pMonitor->m_size});
                } else
                    w->layoutTarget()->setPositionGlobal(
                        CBox{Vector2D{
                                 (pMonitor->m_size.x != 0) ? sc<int>(w->position(Desktop::View::IGeometric::GEOMETRIC_GOAL).x) % sc<int>(pMonitor->m_size.x) : 0,
                                 (pMonitor->m_size.y != 0) ? sc<int>(w->position(Desktop::View::IGeometric::GEOMETRIC_GOAL).y) % sc<int>(pMonitor->m_size.y) : 0,
                             },
                             w->layoutTarget()->position().size()});
            }

            w->updateToplevel();
        }
    }

    // finalize
    if (POLDMON) {
        g_layoutManager->recalculateMonitor(POLDMON);
        if (valid(POLDMON->m_activeWorkspace))
            Animation::Workspace::setFullscreenFadeAnimation(POLDMON->m_activeWorkspace,
                                                             Fullscreen::controller()->hasFullscreen(POLDMON->m_activeWorkspace) ? Animation::Workspace::ANIMATION_TYPE_IN :
                                                                                                                                   Animation::Workspace::ANIMATION_TYPE_OUT);
        Desktop::globalWindowController()->updateSuspendedStates();
    }

    Animation::Workspace::setFullscreenFadeAnimation(
        pWorkspace, Fullscreen::controller()->hasFullscreen(pWorkspace) ? Animation::Workspace::ANIMATION_TYPE_IN : Animation::Workspace::ANIMATION_TYPE_OUT);
    Desktop::globalWindowController()->updateSuspendedStates();

    // event
    g_pEventManager->postEvent(SHyprIPCEvent{.event = "moveworkspace", .data = pWorkspace->m_name + "," + pMonitor->m_name});
    g_pEventManager->postEvent(SHyprIPCEvent{.event = "moveworkspacev2", .data = std::format("{},{},{}", pWorkspace->m_id, pWorkspace->m_name, pMonitor->m_name)});

    Event::bus()->m_events.workspace.moveToMonitor.emit(pWorkspace, pMonitor);
}

void CWorkspacePlacementController::recoverWorkspaceOnMonitor(PHLWORKSPACE workspace, PHLMONITOR monitor) const {
    if (!workspace || !monitor)
        return;
    if (!workspace->isBaseDesktop()) {
        moveWorkspaceToMonitor(workspace, monitor, true);
        return;
    }
    const auto destination = workspaceState()->ensureBaseForMonitor(monitor);
    if (!destination || destination == workspace)
        return;
    std::vector<PHLWINDOW> windows;
    for (const auto& window : Desktop::windowState()->windows()) {
        if (window->m_workspace == workspace)
            windows.push_back(window);
    }
    for (const auto& window : windows) {
        if (window->isMinimized()) {
            const auto oldMonitor = window->m_monitor.lock();
            if (Desktop::windowPlacementController()->rehomeMinimized(window, destination) && oldMonitor && window->m_restorePlacement->wasFloating)
                window->m_restorePlacement->floatingBox.translate(-oldMonitor->m_position + monitor->m_position);
        } else
            Desktop::globalWindowController()->moveWindowToWorkspace(window, destination);
    }
}
