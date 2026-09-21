#pragma once
#include "LuminophoreSpatialBoard.hpp"
#include "LuminophoreSpatialMesh.hpp"
#include <string>

struct SLuminophoreOutputGeometry {
    uint64_t    id = 0;
    std::string name;
    int         x = 0, y = 0, width = 1, height = 1;
    bool        operator==(const SLuminophoreOutputGeometry&) const = default;
};
struct SLuminophoreIndependentState {
    Luminophore::Spatial::SState                    state;
    std::map<uint64_t, uint64_t>             bindings; // output -> persistent session board
    std::map<std::string, uint64_t>          knownBoards;
    std::map<uint64_t, SLuminophoreOutputGeometry>  outputs;
    std::map<uint64_t, Luminophore::Spatial::SMesh> meshes;
    std::map<uint64_t, uint64_t>             meshResetRevisions; // Last explicit reconcile reset, by board.
    struct SReturn {
        uint64_t              board = 0;
        Luminophore::Spatial::SPoint point;
        bool                  operator==(const SReturn&) const = default;
    };
    std::map<uint64_t, SReturn>  displaced;
    std::map<uint64_t, uint64_t> floatingBoards;
    uint64_t                     selectedOutput = 0;
    uint64_t                     focusRevision  = 0;
    int                          defaultColumns = 2, defaultRows = 2;
    bool                         operator==(const SLuminophoreIndependentState&) const = default;
};
