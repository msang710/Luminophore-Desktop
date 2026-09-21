#pragma once
#include "AsyncJointSettings.hpp"
#include <string_view>

namespace Luminophore::Settings {
    struct SSettingsResponse {
        eSettingsReply result = eSettingsReply::UNKNOWN;
        std::string    current;
    };
    std::string                      commandWire(const SSettingsCommand& command);
    std::optional<SSettingsResponse> parseReceipt(const SSettingsCommand& expected, std::string_view wire);
    SSettingsResponse                executeMember(const SSettingsCommand& command, const SSettingsMember& member);
}
