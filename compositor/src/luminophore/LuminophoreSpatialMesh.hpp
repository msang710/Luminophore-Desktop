#pragma once
#include "LuminophoreSpatialFill.hpp"

namespace Luminophore::Spatial {
    using FaceID = uint64_t;
    struct SBox {
        int64_t x = 0, y = 0, width = 1, height = 1;
        bool    valid() const;
        bool    operator==(const SBox&) const = default;
    };
    struct SFace {
        FaceID id                             = 0;
        SRect  provenance                     = {};
        SBox   box                            = {};
        bool   core                           = false;
        bool   operator==(const SFace&) const = default;
    };
    struct SMesh {
        SRect                    view  = {};
        int64_t                  width = 1, height = 1;
        uint64_t                 revision                       = 0;
        std::vector<SFace>       faces                          = {};
        std::optional<WindowKey> fillFocus                      = {};
        bool                     operator==(const SMesh&) const = default;
    };
    struct SRegion {
        WindowKey         owner = 0;
        std::vector<SBox> boxes = {};
    };
    enum class eMeshStatus {
        OK,
        INVALID,
        RESOURCE_LIMIT,
        UNREPRESENTABLE,
    };
    struct SMeshResult {
        eMeshStatus status = eMeshStatus::INVALID;
        SMesh       mesh   = {};
        bool        reset  = false;
    };
    bool                                validateMesh(const SMesh& mesh);
    SMeshResult                         buildMesh(const SBoard& board, int64_t width, int64_t height, size_t budget = 1'000'000, std::optional<WindowKey> focus = std::nullopt);
    std::optional<std::vector<SRegion>> regionsForOwnership(const SMesh& mesh, const SFill& fill);
    // Preserves exact provenance components when possible. A reset is explicit.
    SMeshResult reconcileMesh(const SMesh& mesh, const SBoard& board, int64_t width, int64_t height, size_t budget = 1'000'000, std::optional<WindowKey> focus = std::nullopt);
}
