#pragma once

#include "../layout/algorithm/TiledAlgorithm.hpp"

namespace Luminophore {
    class CLuminophoreSpatialTiledAlgorithm final : public Layout::ITiledAlgorithm {
      public:
        void                           newTarget(SP<Layout::ITarget> target) override;
        void                           movedTarget(SP<Layout::ITarget> target, std::optional<Vector2D> focalPoint = std::nullopt) override;
        void                           removeTarget(SP<Layout::ITarget> target) override;
        void                           resizeTarget(const Vector2D& delta, SP<Layout::ITarget> target, Layout::eRectCorner corner = Layout::CORNER_NONE) override;
        void                           recalculate(Layout::eRecalculateReason reason = Layout::RECALCULATE_REASON_UNKNOWN) override;
        void                           swapTargets(SP<Layout::ITarget> a, SP<Layout::ITarget> b) override;
        Layout::eDirectionalMoveResult moveTargetInDirection(SP<Layout::ITarget> target, Math::eDirection direction, bool silent) override;
        SP<Layout::ITarget>            getNextCandidate(SP<Layout::ITarget> old) override;
        std::optional<std::string>     layoutName() const override;
    };
}
