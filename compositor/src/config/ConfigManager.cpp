#include "ConfigManager.hpp"
#include "luminophore/LuminophoreConfigManager.hpp"
#include "luminophore/SettingsGeneration.hpp"
#include "../debug/log/Logger.hpp"
#include "../Compositor.hpp"

#include <hyprutils/path/Path.hpp>
#include <filesystem>
#include <stdexcept>

using namespace Config;

static UP<IConfigManager> g_mgr;

//
bool Config::initConfigManager() {
    if (mgr())
        return true;

    try {
        g_mgr = makeUnique<Luminophore::Settings::CLuminophoreConfigManager>();
        return true;
    } catch (const std::exception& e) {
        Log::logger->log(Log::CRIT, "Native config rejected: {}", e.what());
        if (g_pCompositor->m_onlyConfigVerification || Luminophore::Settings::settingsFixtureEnabled())
            return false;
        try {
            Luminophore::Settings::selectSafeSettings(e.what());
            g_mgr = makeUnique<Luminophore::Settings::CLuminophoreConfigManager>();
            Log::logger->log(Log::ERR, "RECOVERY: isolated temporary native settings selected; original settings preserved");
            return true;
        } catch (const std::exception& recovery) {
            Log::logger->log(Log::CRIT, "Native safe settings unavailable: {}", recovery.what());
            return false;
        }
    }
}

UP<IConfigManager>& Config::mgr() {
    return g_mgr;
}

const char* Config::typeToString(eConfigManagerType t) {
    switch (t) {
        case CONFIG_LUMINOPHORE: return "luminophore-toml";
        case CONFIG_LUA: return "lua";
        case CONFIG_LEGACY: return "hyprlang";
        default: return "error";
    }
}
void Config::IConfigManager::setDeviceSettings(const Luminophore::Settings::DeviceSettings&) {
    throw std::runtime_error("transactional device settings unsupported by active config manager");
}
Luminophore::Settings::DeviceSettings Config::IConfigManager::deviceSettings() const {
    throw std::runtime_error("transactional device settings unsupported by active config manager");
}
