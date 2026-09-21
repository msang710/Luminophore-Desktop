#pragma once
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

namespace Luminophore::Settings {
    struct SMonitorSettings {
        int                                width     = 0;
        int                                height    = 0;
        double                             refresh   = 60.0;
        double                             scale     = -1.0;
        int                                transform = 0;
        std::optional<std::pair<int, int>> position;
        bool                               disabled                                  = false;
        std::optional<int>                  vrr;
        bool                               operator==(const SMonitorSettings&) const = default;
    };
    using MonitorSettings = std::map<std::string, SMonitorSettings>;
    MonitorSettings decodeMonitorSettings(std::string_view document);
}
