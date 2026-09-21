#pragma once

#include "LuminophoreSpatialModel.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

struct SLuminophorePhysicalBox {
    int  x      = 0;
    int  y      = 0;
    int  width  = 0;
    int  height = 0;

    bool operator==(const SLuminophorePhysicalBox&) const = default;
};

struct SLuminophorePhysicalOutput {
    uint64_t         id         = 0;
    std::string      name       = {};
    SLuminophorePhysicalBox box        = {};
    int              scaleMilli = 1000;
    int              transform  = 0;
    SLuminophorePhysicalBox logicalBox = {};

    bool             operator==(const SLuminophorePhysicalOutput&) const = default;
};

struct SLuminophoreProjectedCell {
    SLuminophoreBoardPoint  point    = {};
    uint64_t         outputID = 0;
    SLuminophorePhysicalBox box      = {};

    bool             operator==(const SLuminophoreProjectedCell&) const = default;
};

struct SLuminophoreProjectedFragment {
    LuminophoreWindowKey    key      = 0;
    SLuminophoreBoardPoint  point    = {};
    uint64_t         outputID = 0;
    SLuminophorePhysicalBox box      = {};

    bool             operator==(const SLuminophoreProjectedFragment&) const = default;
};

struct SLuminophoreProjectedWindow {
    LuminophoreWindowKey                       key             = 0;
    SLuminophoreBoardPoint                     point           = {};
    std::vector<SLuminophoreProjectedFragment> fragments       = {};
    bool                                visible         = false;
    bool                                floating        = false;
    std::optional<SLuminophorePhysicalBox>     clientBox       = std::nullopt;
    uint64_t                            primaryOutputID = 0;

    bool                                operator==(const SLuminophoreProjectedWindow&) const = default;
};

struct SLuminophoreProjectionPlan {
    uint64_t                          modelRevision    = 0;
    uint64_t                          topologyRevision = 0;
    std::vector<SLuminophoreProjectedCell>   cells            = {};
    std::vector<SLuminophoreProjectedWindow> windows          = {};

    eLuminophorePresentationMode             presentationMode = eLuminophorePresentationMode::NORMAL;

    bool                              operator==(const SLuminophoreProjectionPlan&) const = default;
};

class CLuminophoreSpatialProjection {
  public:
    static std::optional<SLuminophorePhysicalBox>    hostBox(const SLuminophoreSpatialSnapshot& snapshot, SLuminophoreBoardPoint point, const SLuminophorePhysicalOutput& output);
    static SLuminophoreBoardExtent                   boardExtentFor(const std::vector<SLuminophorePhysicalOutput>& outputs);
    static std::vector<SLuminophoreProjectedCell>    project(const SLuminophoreViewRect& view, const std::vector<SLuminophorePhysicalOutput>& outputs);
    static std::optional<SLuminophoreProjectionPlan> plan(const SLuminophoreSpatialSnapshot& snapshot, uint64_t topologyRevision, const std::vector<SLuminophorePhysicalOutput>& outputs);
    static std::optional<SLuminophoreNormalizedBox>  normalize(const SLuminophorePhysicalBox& box, const SLuminophorePhysicalBox& host);
    static std::optional<SLuminophorePhysicalBox>    denormalize(const SLuminophoreNormalizedBox& box, const SLuminophorePhysicalBox& host);

  private:
    static std::optional<SLuminophoreProjectionPlan> planIndependent(const SLuminophoreSpatialSnapshot&, uint64_t, const std::vector<SLuminophorePhysicalOutput>&);
    static std::vector<int>                   columnsPerOutput(int columns, const std::vector<SLuminophorePhysicalOutput>& outputs);
};
