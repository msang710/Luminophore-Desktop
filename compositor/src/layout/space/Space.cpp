#include "Space.hpp"

#include "../target/Target.hpp"
#include "../algorithm/Algorithm.hpp"

#include "../../debug/log/Logger.hpp"
#include "../../desktop/Workspace.hpp"
#include "../../config/shared/workspace/WorkspaceRuleManager.hpp"
#include "../../config/ConfigValue.hpp"
#include "../../event/EventBus.hpp"
#include "../../output/Monitor.hpp"
#include "../../luminophore/LuminophoreSpatialRuntime.hpp"

#include <format>

using namespace Layout;

SP<CSpace> CSpace::create(PHLWORKSPACE w) {
    auto space    = SP<CSpace>(new CSpace(w));
    space->m_self = space;
    return space;
}

CSpace::CSpace(PHLWORKSPACE parent) : m_parent(parent) {
    recheckWorkArea();
    Luminophore::spatialRuntime()->bootstrap();

    // NOLINTNEXTLINE
    m_geomUpdateCallback = Event::bus()->m_events.monitor.layoutChanged.listen([this] {
        // During monitor disconnect/reconnect (e.g. sleep/wake), some workspaces
        // may have stale or null monitors. Guard against that to avoid crashing
        // when recalculating layout for workspaces mid-migration.
        if (!m_parent || !m_parent->m_monitor)
            return;

        recheckWorkArea();
        Luminophore::spatialRuntime()->topologyChanged();

        if (usesLuminophoreSpatialGeometry())
            Luminophore::spatialRuntime()->commitCurrent();
        else if (m_algorithm) {
            m_algorithm->invalidateVacant();
            m_algorithm->recalculate();
        }
    });
}

void CSpace::add(SP<ITarget> t) {
    m_targets.emplace_back(t);

    recheckWorkArea();

    if (m_algorithm) {
        if (usesLuminophoreSpatialGeometry() && !t->floating())
            m_algorithm->trackSpatialTiledTarget(t);
        else
            m_algorithm->addTarget(t);
    }

    m_parent->updateWindows();
}

void CSpace::move(SP<ITarget> t, std::optional<Vector2D> focalPoint) {
    m_targets.emplace_back(t);

    recheckWorkArea();

    if (m_algorithm) {
        if (usesLuminophoreSpatialGeometry() && !t->floating())
            m_algorithm->trackSpatialTiledTarget(t);
        else
            m_algorithm->moveTarget(t, focalPoint);
    }

    m_parent->updateWindows();
}

void CSpace::remove(SP<ITarget> t) {
    std::erase_if(m_targets, [&t](const auto& e) { return !e || e == t; });

    recheckWorkArea();

    if (m_algorithm) {
        if (usesLuminophoreSpatialGeometry() && !t->floating())
            m_algorithm->untrackSpatialTiledTarget(t);
        else
            m_algorithm->removeTarget(t);
    }

    if (m_parent) // can be null if the workspace is gone
        m_parent->updateWindows();
}

void CSpace::setAlgorithmProvider(SP<CAlgorithm> algo) {
    if (m_algorithm)
        m_algorithm->invalidateVacant();
    m_algorithm = algo;
    if (m_algorithm && usesLuminophoreSpatialGeometry()) {
        for (const auto& weak : m_targets) {
            const auto target = weak.lock();
            if (target && !target->floating())
                m_algorithm->trackSpatialTiledTarget(target);
        }
    }
}

void CSpace::recheckWorkArea() {
    if (!m_parent || !m_parent->m_monitor) {
        Log::logger->log(Log::ERR, "CSpace: recheckWorkArea on no parent / mon?!");
        return;
    }

    const auto  WORKSPACERULE = Config::workspaceRuleMgr()->getWorkspaceRuleFor(m_parent.lock()).value_or(Config::CWorkspaceRule{});

    auto        workArea = m_parent->m_monitor->logicalBoxMinusReserved();

    static auto PGAPSOUTDATA   = CConfigValue<Config::IComplexConfigValue>("general:gaps_out");
    static auto PFLOATGAPSDATA = CConfigValue<Config::IComplexConfigValue>("general:float_gaps");
    auto* const PGAPSOUT       = sc<Config::CCssGapData*>(PGAPSOUTDATA.ptr());
    auto*       PFLOATGAPS     = sc<Config::CCssGapData*>(PFLOATGAPSDATA.ptr());
    if (PFLOATGAPS->m_bottom < 0 || PFLOATGAPS->m_left < 0 || PFLOATGAPS->m_right < 0 || PFLOATGAPS->m_top < 0)
        PFLOATGAPS = PGAPSOUT;

    auto                   gapsOut   = WORKSPACERULE.m_gapsOut.value_or(*PGAPSOUT);
    auto                   gapsFloat = WORKSPACERULE.m_floatGaps.value_or(*PFLOATGAPS);

    Desktop::CReservedArea reservedGaps{gapsOut.m_top, gapsOut.m_right, gapsOut.m_bottom, gapsOut.m_left};
    Desktop::CReservedArea reservedFloatGaps{gapsFloat.m_top, gapsFloat.m_right, gapsFloat.m_bottom, gapsFloat.m_left};

    auto                   floatWorkArea = workArea;

    reservedFloatGaps.applyip(floatWorkArea);
    reservedGaps.applyip(workArea);

    m_workArea         = workArea;
    m_floatingWorkArea = floatWorkArea;
}

const CBox& CSpace::workArea(bool floating) const {
    return floating ? m_floatingWorkArea : m_workArea;
}

PHLWORKSPACE CSpace::workspace() const {
    return m_parent.lock();
}

bool CSpace::usesLuminophoreSpatialGeometry() const {
    return !!m_parent;
}

void CSpace::toggleTargetFloating(SP<ITarget> t) {
    if (m_algorithm)
        m_algorithm->invalidateVacant();

    std::optional<CBox> desiredFloatingBox;
    if (usesLuminophoreSpatialGeometry() && !t->floating()) {
        const auto current = t->position();
        auto       size    = t->lastFloatingSize();
        if (size.x <= 0 || size.y <= 0)
            size = current.size() * 0.8489;
        desiredFloatingBox = CBox{current.middle() - size / 2.F, size};
    }

    t->setWasTiling(true);
    if (usesLuminophoreSpatialGeometry())
        m_algorithm->setFloatingForSpatialTarget(t, !t->floating());
    else
        m_algorithm->setFloating(t, !t->floating());
    t->setWasTiling(false);
    Luminophore::spatialRuntime()->observeTarget(t, desiredFloatingBox);

    m_parent->updateWindows();

    recalculate();
}

CBox CSpace::targetPositionLocal(SP<ITarget> t) const {
    return t->position().translate(-m_workArea.pos());
}

void CSpace::resizeTarget(const Vector2D& Δ, SP<ITarget> target, eRectCorner corner) {
    if (!m_algorithm)
        return;

    if (usesLuminophoreSpatialGeometry() && target && !target->floating()) {
        Luminophore::spatialRuntime()->resizeTiled(target, Δ, corner & CORNER_LEFT, corner & CORNER_TOP);
        return;
    }

    m_algorithm->resizeTarget(Δ, target, corner);
}

void CSpace::moveTarget(const Vector2D& Δ, SP<ITarget> target) {
    if (!m_algorithm)
        return;

    m_algorithm->moveTarget(Δ, target);
}

SP<CAlgorithm> CSpace::algorithm() const {
    return m_algorithm;
}

void CSpace::recalculate(eRecalculateReason reason) {
    recheckWorkArea();

    if (usesLuminophoreSpatialGeometry()) {
        if (reason == RECALCULATE_REASON_INVALIDATE_MONITOR_GEOMETRIES)
            Luminophore::spatialRuntime()->topologyChanged();
        else if (reason != RECALCULATE_REASON_RENDER_MONITOR)
            Luminophore::spatialRuntime()->commitCurrent();
        return;
    }

    if (m_algorithm &&
        (reason == RECALCULATE_REASON_TOGGLE_DEFAULT_HANDLED_FULLSCREEN || reason == RECALCULATE_REASON_TOGGLE_LAYOUT_HANDLED_FULLSCREEN ||
         reason == RECALCULATE_REASON_INVALIDATE_MONITOR_GEOMETRIES))
        m_algorithm->invalidateVacant();

    if (m_algorithm)
        m_algorithm->recalculate(reason);
}

std::optional<Vector2D> CSpace::predictSizeForNewTiledTarget() {
    if (m_algorithm)
        return m_algorithm->predictSizeForNewTiledTarget();

    return std::nullopt;
}

void CSpace::swap(SP<ITarget> a, SP<ITarget> b) {
    for (auto& t : m_targets) {
        if (t == a)
            t = b;
        else if (t == b)
            t = a;
    }

    if (m_algorithm && !usesLuminophoreSpatialGeometry())
        m_algorithm->swapTargets(a, b);
}

eDirectionalMoveResult CSpace::moveTargetInDirection(SP<ITarget> t, Math::eDirection dir, bool silent) {
    if (usesLuminophoreSpatialGeometry()) {
        eLuminophoreSpatialDirection spatialDirection;
        switch (dir) {
            case Math::DIRECTION_UP: spatialDirection = eLuminophoreSpatialDirection::UP; break;
            case Math::DIRECTION_RIGHT: spatialDirection = eLuminophoreSpatialDirection::RIGHT; break;
            case Math::DIRECTION_DOWN: spatialDirection = eLuminophoreSpatialDirection::DOWN; break;
            case Math::DIRECTION_LEFT: spatialDirection = eLuminophoreSpatialDirection::LEFT; break;
            default: return DIRECTIONAL_MOVE_INVALID_TARGET;
        }
        return Luminophore::spatialRuntime()->dispatch(Luminophore::eSpatialAction::MOVE_WINDOW, spatialDirection, t ? std::optional<PHLWINDOW>{t->window()} : std::nullopt) ?
            DIRECTIONAL_MOVE_MOVED :
            DIRECTIONAL_MOVE_BLOCKED_POLICY;
    }

    if (!m_algorithm)
        return DIRECTIONAL_MOVE_INVALID_TARGET;

    return m_algorithm->moveTargetInDirection(t, dir, silent);
}

void CSpace::setTargetGeom(const CBox& box, SP<ITarget> target) {
    if (m_algorithm)
        m_algorithm->setTargetGeom(box, target);
}

SP<ITarget> CSpace::getNextCandidate(SP<ITarget> old) {
    return !m_algorithm ? nullptr : m_algorithm->getNextCandidate(old);
}

bool Layout::isHardRecalculateReason(eRecalculateReason reason) {
    return reason != RECALCULATE_REASON_WORKSPACE_CHANGE && reason != RECALCULATE_REASON_SPECIAL_WORKSPACE_TOGGLE &&
        reason != RECALCULATE_REASON_TOGGLE_LAYOUT_HANDLED_FULLSCREEN && reason != RECALCULATE_REASON_TOGGLE_DEFAULT_HANDLED_FULLSCREEN &&
        reason != RECALCULATE_REASON_INVALIDATE_MONITOR_GEOMETRIES && reason != RECALCULATE_REASON_RENDER_MONITOR;
}

const std::vector<WP<ITarget>>& CSpace::targets() const {
    return m_targets;
}

eRecalculateReason Layout::recalcMonitorReasonToRecalcReason(CLayoutManager::eRecalculateMonitorReason reason) {
    // If eRecalculateMonitorReason doesn't have a eRecalculateReason pair, it'll return nullopt
    switch (reason) {
        case CLayoutManager::RECALCULATE_MONITOR_REASON_TOGGLE_SPECIAL_WORKSPACE: return RECALCULATE_REASON_SPECIAL_WORKSPACE_TOGGLE;
        case CLayoutManager::RECALCULATE_MONITOR_REASON_WORKSPACE_CHANGE: return RECALCULATE_REASON_WORKSPACE_CHANGE;
        case CLayoutManager::RECALCULATE_MONITOR_REASON_TOGGLE_FULLSCREEN: return RECALCULATE_REASON_TOGGLE_DEFAULT_HANDLED_FULLSCREEN;
        default: return RECALCULATE_REASON_UNKNOWN;
    }
}
