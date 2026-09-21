#pragma once
#include "GeneratedDesktopSettings.hpp"
#include "InputRuntime.hpp"
#include <array>
#include <toml++/toml.hpp>

namespace Luminophore::Settings {
    struct SGeneration;
    struct SDesktopSettings {
        std::vector<SBindingDeclaration> bindings;
        toml::table                      native;
        toml::table                      placements;
        toml::array                      bundles;
    };
    void             validateShellField(const std::string& path, const toml::node& value);
    SDesktopSettings decodeDesktopSettings(const std::array<std::string, 5>& documents);
    void             installDesktopSettings(const SDesktopSettings& settings, bool startup = false);
    SInputRuntime    makeDesktopRuntime(const SGeneration& boot);
    bool             desktopSessionRestartRequired();
    std::string      desktopAction(const std::string& action);
    std::string      desktopCommand(const std::string& wire);
}
