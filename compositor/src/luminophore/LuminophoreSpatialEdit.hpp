#pragma once
#include "LuminophoreSpatialTransaction.hpp"

namespace Luminophore {
    // Mutates only the caller's candidate. Runtime owns projection and commit.
    SLuminophoreSpatialTransactionResult applyEditorCandidate(CLuminophoreSpatialModel& candidate, const SLuminophoreSpatialCommand& command);
}
