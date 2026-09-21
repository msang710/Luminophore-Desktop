#pragma once
#include <string>
#include <optional>

namespace Luminophore::LaunchOrigin {
    constexpr const char*      ENV = "LUMINOPHORE_DIRECT_LAUNCH_TOKEN";
    std::string                issue(const std::string& group = {});
    std::optional<std::string> take(const std::string& token);
    bool                       consume(const std::string& token);
}
