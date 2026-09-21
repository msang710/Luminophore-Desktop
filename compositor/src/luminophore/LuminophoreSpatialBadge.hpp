#pragma once
#include "../helpers/math/Math.hpp"

namespace Luminophore::SpatialBadge {
    CBox target(const Vector2D& pointer, const Vector2D& size);
    CBox transition(const CBox& source, const CBox& destination, float progress);
}
