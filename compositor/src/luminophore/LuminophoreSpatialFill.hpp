#pragma once
#include "LuminophoreSpatialBoard.hpp"
#include <cstddef>

namespace Luminophore::Spatial {
    struct SOwnedRect {
        SRect                    logical                             = {};
        std::optional<WindowKey> owner                               = {};
        bool                     core                                = false;
        bool                     operator==(const SOwnedRect&) const = default;
    };
    enum class eFillStatus {
        OK,
        INVALID,
        RESOURCE_LIMIT,
    };
    struct SFill {
        eFillStatus             status     = eFillStatus::INVALID;
        std::vector<SOwnedRect> rectangles = {};
        size_t                  visited    = 0;
    };
    // Budget is a work limit, never a logical coordinate or board capacity.
    SFill computeOwnership(const SBoard& board, std::optional<WindowKey> focus, size_t budget = 1'000'000);
}
