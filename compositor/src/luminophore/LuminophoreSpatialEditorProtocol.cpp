#include "LuminophoreSpatialEditorProtocol.hpp"

#include <charconv>
#include <format>
#include <sstream>
#include <vector>

static bool parseInteger(std::string_view token, auto& value, int base = 10) {
    if (token.empty())
        return false;
    const auto [end, error] = std::from_chars(token.data(), token.data() + token.size(), value, base);
    return error == std::errc{} && end == token.data() + token.size();
}

std::optional<SLuminophoreSpatialCommand> CLuminophoreSpatialEditorProtocol::parsePreview(std::string_view request) {
    std::istringstream       stream{std::string{request}};
    std::vector<std::string> tokens;
    std::string              token;
    while (stream >> token) {
        tokens.emplace_back(token);
        if (tokens.size() > 8)
            return std::nullopt;
    }
    if (tokens.size() < 6)
        return std::nullopt;
    const auto& action = tokens[0];
    if ((action == "move-window" && tokens.size() != 7) || (action == "move-view" && tokens.size() != 6) || (action == "resize-view" && tokens.size() != 8))
        return std::nullopt;
    uint64_t revision = 0, topology = 0, output = 0;
    if (!parseInteger(tokens[1], revision) || !parseInteger(tokens[2], topology) || !parseInteger(tokens[3], output) || output == 0)
        return std::nullopt;
    SLuminophoreSpatialCommand command{.expectedRevision = revision};
    int64_t             x = 0, y = 0;
    if (action == "move-window") {
        LuminophoreWindowKey          key     = 0;
        const std::string_view address = tokens[4];
        if (!address.starts_with("0x") || !parseInteger(address.substr(2), key, 16) || key == 0 || !parseInteger(tokens[5], x) || !parseInteger(tokens[6], y))
            return std::nullopt;
        command.payload = SMoveWindowToCommand{.key = key, .point = {.x = x, .y = y}, .outputID = output, .expectedTopologyRevision = topology};
    } else {
        if (!parseInteger(tokens[4], x) || !parseInteger(tokens[5], y))
            return std::nullopt;
        if (action == "move-view")
            command.payload = SMoveOutputViewToCommand{.outputID = output, .origin = {.x = x, .y = y}, .expectedTopologyRevision = topology};
        else if (action == "resize-view") {
            int columns = 0, rows = 0;
            if (!parseInteger(tokens[6], columns) || !parseInteger(tokens[7], rows))
                return std::nullopt;
            command.payload =
                SResizeOutputViewCommand{.outputID = output, .rect = {.origin = {.x = x, .y = y}, .columns = columns, .rows = rows}, .expectedTopologyRevision = topology};
        } else
            return std::nullopt;
    }
    return command;
}

std::string CLuminophoreSpatialEditorProtocol::statusName(eLuminophoreSpatialTransactionStatus status) {
    switch (status) {
        case eLuminophoreSpatialTransactionStatus::APPLIED: return "applied";
        case eLuminophoreSpatialTransactionStatus::NO_CHANGE: return "no-change";
        case eLuminophoreSpatialTransactionStatus::STALE_REVISION: return "stale-revision";
        case eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY: return "stale-topology";
        case eLuminophoreSpatialTransactionStatus::INVALID_COMMAND: return "invalid-command";
        case eLuminophoreSpatialTransactionStatus::NO_CAPACITY: return "no-capacity";
        case eLuminophoreSpatialTransactionStatus::COMMIT_FAILED: return "commit-failed";
    }
    return "invalid-command";
}

std::string CLuminophoreSpatialEditorProtocol::serialize(const SLuminophoreSpatialTransactionResult& result) {
    const auto& state = result.snapshot;
    std::string windows;
    const auto  appendWindow = [&](LuminophoreWindowKey key, const SLuminophoreBoardPoint& point, bool floating) {
        if (!windows.empty())
            windows += ',';
        uint64_t board = 0;
        if (state.independent) {
            for (const auto& [id, b] : state.independent->state.boards)
                for (const auto& [p, k] : b.tiled)
                    if (k == key)
                        board = id;
            if (floating && state.independent->floatingBoards.contains(key))
                board = state.independent->floatingBoards.at(key);
        }
        windows += std::format(R"({{"address":"0x{:x}","x":{},"y":{},"mode":"{}","board":{}}})", key, point.x, point.y, floating ? "floating" : "tiled", board);
    };
    for (const auto& placement : state.tiled)
        appendWindow(placement.key, placement.point, false);
    for (const auto& placement : state.floating)
        appendWindow(placement.key, placement.host, true);
    std::string views;
    for (const auto& output : state.outputViews) {
        if (!views.empty())
            views += ',';
        views += std::format(R"({{"output":{},"x":{},"y":{},"columns":{},"rows":{}}})", output.outputID, output.rect.origin.x, output.rect.origin.y, output.rect.columns,
                             output.rect.rows);
    }
    return std::format(R"({{"schema":1,"status":"{}","revision":{},"topologyRevision":{},"windows":[{}],"outputViews":[{}]}})", statusName(result.status), state.revision,
                       state.outputTopologyRevision, windows, views);
}
