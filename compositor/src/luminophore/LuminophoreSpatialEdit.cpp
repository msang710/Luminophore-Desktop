#include "LuminophoreSpatialEdit.hpp"

SLuminophoreSpatialTransactionResult Luminophore::applyEditorCandidate(CLuminophoreSpatialModel& candidate, const SLuminophoreSpatialCommand& command) {
    if (!std::holds_alternative<SMoveWindowToCommand>(command.payload) && !std::holds_alternative<SMoveOutputViewToCommand>(command.payload) &&
        !std::holds_alternative<SResizeOutputViewCommand>(command.payload) && !std::holds_alternative<SUpdateFloatingCommand>(command.payload))
        return {.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = candidate.snapshot()};
    return candidate.transact(command);
}
