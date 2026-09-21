#include "LuminophoreSpatialTiledAlgorithm.hpp"

#include "LuminophoreSpatialRuntime.hpp"
#include "../layout/target/Target.hpp"

using namespace Luminophore;

static std::optional<eLuminophoreSpatialDirection> spatialDirection(Math::eDirection direction) {
    switch (direction) {
        case Math::DIRECTION_UP: return eLuminophoreSpatialDirection::UP;
        case Math::DIRECTION_RIGHT: return eLuminophoreSpatialDirection::RIGHT;
        case Math::DIRECTION_DOWN: return eLuminophoreSpatialDirection::DOWN;
        case Math::DIRECTION_LEFT: return eLuminophoreSpatialDirection::LEFT;
        default: return std::nullopt;
    }
}

void CLuminophoreSpatialTiledAlgorithm::newTarget(SP<Layout::ITarget> target) {
    spatialRuntime()->observeTarget(target);
}

void CLuminophoreSpatialTiledAlgorithm::movedTarget(SP<Layout::ITarget> target, std::optional<Vector2D> focalPoint) {
    (void)focalPoint;
    spatialRuntime()->observeTarget(target);
}

void CLuminophoreSpatialTiledAlgorithm::removeTarget(SP<Layout::ITarget> target) {
    spatialRuntime()->observeTarget(target);
}

void CLuminophoreSpatialTiledAlgorithm::resizeTarget(const Vector2D& delta, SP<Layout::ITarget> target, Layout::eRectCorner corner) {
    spatialRuntime()->resizeTiled(target, delta, corner & Layout::CORNER_LEFT, corner & Layout::CORNER_TOP);
}

void CLuminophoreSpatialTiledAlgorithm::recalculate(Layout::eRecalculateReason reason) {
    (void)reason;
    spatialRuntime()->commitCurrent();
}

void CLuminophoreSpatialTiledAlgorithm::swapTargets(SP<Layout::ITarget> a, SP<Layout::ITarget> b) {
    (void)a;
    (void)b;
    // Spatial movement has a typed push/swap policy; there is no mutable layout tree to swap.
}

Layout::eDirectionalMoveResult CLuminophoreSpatialTiledAlgorithm::moveTargetInDirection(SP<Layout::ITarget> target, Math::eDirection direction, bool silent) {
    (void)silent;
    const auto spatial = spatialDirection(direction);
    if (!target || !spatial)
        return Layout::DIRECTIONAL_MOVE_INVALID_TARGET;

    const auto window = target->window();
    return spatialRuntime()->dispatch(eSpatialAction::MOVE_WINDOW, *spatial, window) ? Layout::DIRECTIONAL_MOVE_MOVED : Layout::DIRECTIONAL_MOVE_BLOCKED_EDGE;
}

SP<Layout::ITarget> CLuminophoreSpatialTiledAlgorithm::getNextCandidate(SP<Layout::ITarget> old) {
    (void)old;
    return nullptr;
}

std::optional<std::string> CLuminophoreSpatialTiledAlgorithm::layoutName() const {
    return "luminophore-spatial";
}
