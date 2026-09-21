#include "WorkspaceState.hpp"

#include "MonitorState.hpp"
#include "WorkspaceQueryCore.hpp"

#include "../config/shared/workspace/WorkspaceRuleManager.hpp"
#include "../desktop/Workspace.hpp"
#include "../debug/log/Logger.hpp"

#include <format>

using namespace State;

UP<CWorkspaceStateTracker>& State::workspaceState() {
    static UP<CWorkspaceStateTracker> p = makeUnique<CWorkspaceStateTracker>();
    return p;
}

const std::vector<PHLWORKSPACEREF>& CWorkspaceStateTracker::workspaceRefs() const {
    return m_workspaces;
}

std::vector<SWorkspaceQueryable> CWorkspaceStateTracker::queryableWorkspaces() const {
    std::vector<SWorkspaceQueryable> queryable;
    queryable.reserve(m_workspaces.size());

    for (const auto& w : m_workspaces) {
        const auto WORKSPACE = w.lock();
        queryable.push_back({
            .id      = WORKSPACE ? WORKSPACE->m_id : WORKSPACE_INVALID,
            .name    = WORKSPACE ? std::string_view{WORKSPACE->m_name} : std::string_view{},
            .inert   = !valid(WORKSPACE),
            .special = WORKSPACE ? WORKSPACE->m_isSpecialWorkspace : false,
        });
    }

    return queryable;
}

std::vector<PHLWORKSPACE> CWorkspaceStateTracker::workspacesCopy() const {
    std::vector<PHLWORKSPACE> wsp;
    auto                      range = workspaces();
    wsp.reserve(std::ranges::distance(range));
    for (auto& r : range) {
        wsp.emplace_back(r.lock());
    }
    return wsp;
}

std::vector<PHLWORKSPACE> CWorkspaceStateTracker::userWorkspaces() const {
    std::vector<PHLWORKSPACE> result;

    for (const auto& ref : workspaces()) {
        const auto workspace = ref.lock();
        if (!workspace || workspace->role() == WORKSPACE_ROLE_SERVICE)
            continue;
        if (workspace->isUserWorkspace())
            result.emplace_back(workspace);
    }

    return result;
}

void CWorkspaceStateTracker::add(PHLWORKSPACE w) {
    m_workspaces.emplace_back(w);
    w->m_events.destroy.listenStatic([this, weak = PHLWORKSPACEREF{w}] { std::erase(m_workspaces, weak); });
}

void CWorkspaceStateTracker::clear() {
    m_workspaces.clear();
}

PHLWORKSPACE CWorkspaceStateTracker::create(const WORKSPACEID& id, const MONITORID& monid, const std::string& name, bool isEmpty) {
    const auto NAME = name.empty() ? std::to_string(id) : name;
    if (CWorkspace::roleFor(NAME, false) == WORKSPACE_ROLE_UNKNOWN) {
        Log::logger->log(Log::ERR, "Removed workspace identity: {}", NAME);
        return nullptr;
    }
    auto monID = monid;

    // check if bound
    if (const auto PMONITOR = Config::workspaceRuleMgr()->getBoundMonitorForWS(NAME); PMONITOR)
        monID = PMONITOR->m_id;

    const bool SPECIAL = CWorkspaceQueryCore::isSpecial(id);

    const auto PMONITOR = State::monitorState()->query().id(monID).run();
    if (!PMONITOR) {
        Log::logger->log(Log::ERR, "BUG THIS: No pMonitor for new workspace in CWorkspaceStateTracker::create");
        return nullptr;
    }

    if (NAME == std::format("luminophore-base-{}", PMONITOR->m_name)) {
        if (const auto existing = baseForMonitor(PMONITOR))
            return existing;
    }
    const auto PWORKSPACE = CWorkspace::create(id, PMONITOR, NAME, SPECIAL, isEmpty);

    if (PWORKSPACE)
        PWORKSPACE->m_alpha->setValueAndWarp(0);

    return PWORKSPACE;
}

PHLWORKSPACE CWorkspaceStateTracker::baseForMonitor(const PHLMONITORREF& monitor) const {
    if (!monitor)
        return nullptr;

    for (const auto& ref : workspaces()) {
        const auto workspace = ref.lock();
        if (workspace && workspace->m_monitor == monitor && workspace->isBaseDesktop())
            return workspace;
    }

    return nullptr;
}

PHLWORKSPACE CWorkspaceStateTracker::ensureBaseForMonitor(const PHLMONITOR& monitor) {
    if (!monitor)
        return nullptr;
    if (const auto existing = baseForMonitor(monitor); existing)
        return existing;

    // During onConnect the output is not yet returned by the active-monitor query.
    // The caller already owns the connecting monitor; do not resolve it again by ID.
    return CWorkspace::create(nextAvailableNamedWorkspace(), monitor, std::format("luminophore-base-{}", monitor->m_name));
}

WORKSPACEID CWorkspaceStateTracker::nextAvailableNamedWorkspace() const {
    std::vector<WORKSPACEID> persistentWorkspaceIDs;

    // Give priority to persistent workspaces to avoid any conflicts between them.
    for (auto const& rule : Config::workspaceRuleMgr()->getAllWorkspaceRules()) {
        if (!rule->isEnabled() || !rule->m_isPersistent.value_or(false))
            continue;
        persistentWorkspaceIDs.push_back(rule->m_workspaceId);
    }

    return CWorkspaceQueryCore::nextAvailableNamedWorkspace(queryableWorkspaces(), persistentWorkspaceIDs);
}

WORKSPACEID CWorkspaceStateTracker::newSpecialID() const {
    return CWorkspaceQueryCore::newSpecialID(queryableWorkspaces());
}

bool CWorkspaceStateTracker::isSpecial(const WORKSPACEID& id) const {
    return CWorkspaceQueryCore::isSpecial(id);
}
