#pragma once

#include "LuminophoreSpatialModel.hpp"

#include <optional>
#include <vector>

class CLuminophoreOutputViewSolver {
  public:
    static std::optional<std::vector<SLuminophoreOutputView>> place(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                             const SLuminophoreViewRect& candidate);
    static std::optional<std::vector<SLuminophoreOutputView>> reveal(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                              const SLuminophoreBoardPoint& point, eLuminophoreSpatialDirection direction);
    static bool                                        validate(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views);
    static std::optional<std::vector<SLuminophoreOutputView>> move(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                            eLuminophoreSpatialDirection direction);
    static std::optional<std::vector<SLuminophoreOutputView>> adjust(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                              eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor);
    static std::optional<std::vector<SLuminophoreOutputView>> reconcile(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& current, std::vector<uint64_t> outputIDs,
                                                                 const SLuminophoreViewRect& defaultRect);

  private:
    static std::optional<std::vector<SLuminophoreOutputView>> solve(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                             const SLuminophoreViewRect& candidate, eLuminophoreSpatialDirection direction);
};
