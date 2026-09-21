#pragma once
#include "LuminophoreSpatialCommitter.hpp"
// Motion changes presentation only. Logical placement and ownership stay committed.
class CLuminophoreSpatialMotion {
  public:
    static std::optional<SLuminophoreSpatialCommitEntry> outside(const SLuminophoreSpatialCommitEntry& entry, const SLuminophoreBoardPoint& point, const SLuminophoreViewRect& view,
                                                          const SLuminophorePhysicalBox& output);
};
