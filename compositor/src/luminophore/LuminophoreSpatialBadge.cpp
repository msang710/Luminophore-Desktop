#include "LuminophoreSpatialBadge.hpp"
#include <algorithm>

CBox Luminophore::SpatialBadge::target(const Vector2D& pointer, const Vector2D& size) {
    return {pointer - size / 2, size};
}

CBox Luminophore::SpatialBadge::transition(const CBox& source, const CBox& destination, float progress) {
    const double t = std::clamp(progress, 0.F, 1.F);
    return {source.pos() + (destination.pos() - source.pos()) * t, source.size() + (destination.size() - source.size()) * t};
}
