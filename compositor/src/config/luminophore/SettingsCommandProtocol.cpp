#include "SettingsCommandProtocol.hpp"
#include <algorithm>
#include <stdexcept>
using namespace Luminophore::Settings;

static std::string targetName(eSettingsTarget target) {
    switch (target) {
        case eSettingsTarget::STORE: return "store";
        case eSettingsTarget::NATIVE: return "native";
        case eSettingsTarget::SHELL: return "shell";
    }
    throw std::invalid_argument("invalid settings target");
}
static std::string operationName(eSettingsOperation operation) {
    switch (operation) {
        case eSettingsOperation::CURRENT: return "current";
        case eSettingsOperation::PREPARE: return "prepare";
        case eSettingsOperation::APPLY: return "apply";
        case eSettingsOperation::VERIFY: return "verify";
        case eSettingsOperation::PUBLISH: return "publish";
        case eSettingsOperation::RESTORE: return "restore";
        case eSettingsOperation::CONFIRM: return "confirm";
        case eSettingsOperation::ABANDON: return "abandon";
    }
    throw std::invalid_argument("invalid settings operation");
}
std::string Luminophore::Settings::commandWire(const SSettingsCommand& command) {
    const auto& request = command.request;
    return "1 " + request.epoch + " " + std::to_string(request.sequence) + " " + std::to_string(command.ticket) + " " + request.base + " " + request.candidate + " " +
        targetName(command.target) + " " + operationName(command.operation);
}
std::optional<SSettingsResponse> Luminophore::Settings::parseReceipt(const SSettingsCommand& expected, std::string_view wire) {
    const auto prefix = commandWire(expected) + " ";
    if (wire.size() > 600 || !wire.starts_with(prefix))
        return std::nullopt;
    wire.remove_prefix(prefix.size());
    const auto split = wire.find(' ');
    if (split == std::string_view::npos)
        return std::nullopt;
    const auto     status = wire.substr(0, split), current = wire.substr(split + 1);
    eSettingsReply result;
    if (status == "ok")
        result = eSettingsReply::OK;
    else if (status == "failed")
        result = eSettingsReply::FAILED;
    else if (status == "unknown")
        result = eSettingsReply::UNKNOWN;
    else
        return std::nullopt;
    if (current != "-" && (current.size() != 64 || !std::ranges::all_of(current, [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); })))
        return std::nullopt;
    if (result == eSettingsReply::OK && expected.operation == eSettingsOperation::CURRENT && current == "-")
        return std::nullopt;
    if (expected.operation != eSettingsOperation::CURRENT && current != "-")
        return std::nullopt;
    return SSettingsResponse{result, current == "-" ? "" : std::string(current)};
}
SSettingsResponse Luminophore::Settings::executeMember(const SSettingsCommand& command, const SSettingsMember& member) {
    // Native callbacks are synchronous and run on the owning event loop.
    if (command.target != eSettingsTarget::NATIVE)
        return {eSettingsReply::FAILED, ""};
    try {
        switch (command.operation) {
            case eSettingsOperation::PREPARE: member.prepare(command.request); break;
            case eSettingsOperation::APPLY: member.apply(command.request); break;
            case eSettingsOperation::VERIFY: member.verify(command.request); break;
            case eSettingsOperation::RESTORE: member.restore(command.request); break;
            case eSettingsOperation::CONFIRM: member.confirm(command.request); break;
            default: return {eSettingsReply::FAILED, ""};
        }
        return {eSettingsReply::OK, ""};
    } catch (...) { return {eSettingsReply::FAILED, ""}; }
}
