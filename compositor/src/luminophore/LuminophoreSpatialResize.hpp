#pragma once
#include "LuminophoreSpatialMesh.hpp"

namespace Luminophore::Spatial {
    enum class eSide {
        LEFT,
        RIGHT,
        TOP,
        BOTTOM,
    };
    enum class eResizeStatus {
        APPLIED,
        NO_CHANGE,
        STALE,
        INVALID,
        RESOURCE_LIMIT,
    };
    struct SResizeRequest {
        uint64_t expectedRevision = 0;
        FaceID   face             = 0;
        eSide    side             = eSide::RIGHT;
        int64_t  delta            = 0; // Screen axis delta, not outward distance.
        int64_t  minimum          = 100;
        size_t   faceBudget       = 100'000;
    };
    struct SResizeResult {
        eResizeStatus status         = eResizeStatus::INVALID;
        SMesh         mesh           = {};
        int64_t       effectiveDelta = 0;
    };
    // Native resize moves complete rectangular window edges. Internal seams are
    // never handles; connected neighboring edges move only as required.
    SResizeResult solveWindowResize(const SMesh& mesh, const SFill& fill, WindowKey window, const SResizeRequest& request, const std::map<WindowKey, SPoint>& clientMinimums = {});
}
