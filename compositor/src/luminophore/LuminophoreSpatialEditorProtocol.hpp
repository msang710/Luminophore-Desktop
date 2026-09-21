#pragma once

#include "LuminophoreSpatialTransaction.hpp"

#include <optional>
#include <string>
#include <string_view>

class CLuminophoreSpatialEditorProtocol {
  public:
    static std::optional<SLuminophoreSpatialCommand> parsePreview(std::string_view request);
    static std::string                        statusName(eLuminophoreSpatialTransactionStatus status);
    static std::string                        serialize(const SLuminophoreSpatialTransactionResult& result);
};
