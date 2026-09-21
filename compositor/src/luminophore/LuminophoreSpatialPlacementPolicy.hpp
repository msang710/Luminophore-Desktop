#pragma once
#include "LuminophoreSpatialBoard.hpp"

namespace Luminophore::Spatial {
    // Also used by pure tests; callers must provide a validated candidate.
    bool    includePoint(SRect& rect, SPoint point);
    eStatus insertWindow(SState& candidate, const SCreate& command);
    void    shrinkAfterClose(SBoard& board, SPoint point);
}
