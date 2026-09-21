#pragma once

#include "types/LuaConfigValue.hpp"
#include "../luminophore/GeneratedSettings.hpp"
#include "../../helpers/memory/Memory.hpp"
#include <unordered_map>

extern "C" {
#include <lauxlib.h>
}

namespace Config::Lua {
    using DeviceValues = std::unordered_map<std::string, UP<ILuaConfigValue>>;
    DeviceValues                    makeDeviceValues(const Luminophore::Settings::Snapshot& fields, const Luminophore::Settings::Snapshot& globals);
    Luminophore::Settings::Snapshot readDeviceValues(const DeviceValues& values);
}
