#pragma once
#include "LuminophoreSpatialProjection.hpp"
#include "../desktop/DesktopTypes.hpp"
namespace Luminophore::SpatialTopology {
    std::vector<SLuminophorePhysicalOutput> physicalOutputs();
    PHLMONITOR                       monitorForOutput(uint64_t outputID);
    std::optional<uint64_t>          selectedOutputID(const std::vector<SLuminophoreOutputView>& views);
}
