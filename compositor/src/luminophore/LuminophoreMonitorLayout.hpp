#pragma once
#include <map>
#include <string>
#include <vector>
#include <optional>
#include <chrono>
#include "../helpers/math/Math.hpp"
#include "../desktop/DesktopTypes.hpp"

namespace Luminophore::MonitorLayout {
    struct SOutput {
        std::string key;
        int         x = 0, y = 0, width = 0, height = 0;
        bool        operator==(const SOutput&) const = default;
    };
    struct SPreviewLease {
        std::string                           id, topology, phase = "idle";
        std::chrono::steady_clock::time_point deadline;
        bool                                  pending() const;
        bool                                  expired(std::chrono::steady_clock::time_point now, const std::string& currentTopology) const;
    };
    using Positions = std::map<std::string, Vector2D>;
    std::optional<Positions> parse(const std::string& text);
    bool                     validate(const std::vector<SOutput>& outputs, const Positions& positions);
    std::string              request(const std::string& text);
    Vector2D                 configuredPosition(PHLMONITOR monitor, Vector2D fallback);
    void                     configReload();
    bool                     applying();
}
