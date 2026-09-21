#include "DeviceSettings.hpp"
#include "types/LuaConfigBool.hpp"
#include "types/LuaConfigFloat.hpp"
#include "types/LuaConfigInt.hpp"
#include "types/LuaConfigString.hpp"
#include "types/LuaConfigVec2.hpp"
#include <algorithm>
#include <set>
#include <stdexcept>

using namespace Config::Lua;
using namespace Luminophore::Settings;

static const std::set<std::string> VECTOR_FIELDS = {"region_position", "region_size", "active_area_position", "active_area_size"};

DeviceValues                       Config::Lua::makeDeviceValues(const Snapshot& fields, const Snapshot& globals) {
    const auto state = std::unique_ptr<lua_State, decltype(&lua_close)>(luaL_newstate(), lua_close);
    if (!state)
        throw std::runtime_error("device Lua state unavailable");
    DeviceValues result;
    for (const auto& [key, value] : fields) {
        const auto base = key.size() > 2 ? key.substr(0, key.size() - 2) : key;
        if ((key.ends_with("_x") || key.ends_with("_y")) && VECTOR_FIELDS.contains(base)) {
            if (result.contains(base))
                continue;
            const auto x = base + "_x", y = base + "_y";
            const auto vx = std::get<double>(fields.contains(x) ? fields.at(x) : globals.at("tablet." + x));
            const auto vy = std::get<double>(fields.contains(y) ? fields.at(y) : globals.at("tablet." + y));
            lua_createtable(state.get(), 2, 0);
            lua_pushnumber(state.get(), vx);
            lua_rawseti(state.get(), -2, 1);
            lua_pushnumber(state.get(), vy);
            lua_rawseti(state.get(), -2, 2);
            auto parsed = makeUnique<CLuaConfigVec2>(Config::VEC2{});
            if (const auto error = parsed->parse(state.get()); error.errorCode != PARSE_ERROR_OK)
                throw std::runtime_error("device vector rejected: " + base + ": " + error.message);
            lua_pop(state.get(), 1);
            result[base] = std::move(parsed);
            continue;
        }
        auto parsed = std::visit(
            [&](const auto& item) -> UP<ILuaConfigValue> {
                using T = std::decay_t<decltype(item)>;
                if constexpr (std::is_same_v<T, bool>) {
                    lua_pushboolean(state.get(), item);
                    return makeUnique<CLuaConfigBool>(false);
                } else if constexpr (std::is_same_v<T, int64_t>) {
                    lua_pushinteger(state.get(), item);
                    return makeUnique<CLuaConfigInt>(0);
                } else if constexpr (std::is_same_v<T, double>) {
                    lua_pushnumber(state.get(), item);
                    return makeUnique<CLuaConfigFloat>(0.F);
                } else {
                    lua_pushlstring(state.get(), item.data(), item.size());
                    return makeUnique<CLuaConfigString>("");
                }
            },
            value);
        if (const auto error = parsed->parse(state.get()); error.errorCode != PARSE_ERROR_OK)
            throw std::runtime_error("device value rejected: " + key + ": " + error.message);
        lua_pop(state.get(), 1);
        auto canonical = key;
        std::ranges::replace(canonical, '-', '_');
        result[canonical] = std::move(parsed);
    }
    return result;
}

Snapshot Config::Lua::readDeviceValues(const DeviceValues& values) {
    Snapshot result;
    for (const auto& [key, value] : values) {
        if (!value->setByUser())
            continue;
        const auto& type = *value->underlying();
        if (type == typeid(Config::BOOL))
            result[key] = static_cast<bool>(value->asInt());
        else if (type == typeid(Config::INTEGER))
            result[key] = static_cast<int64_t>(value->asInt());
        else if (type == typeid(Config::FLOAT))
            result[key] = static_cast<double>(value->asFloat());
        else if (type == typeid(Config::STRING)) {
            const auto text = value->asString();
            result[key]     = text == "[[EMPTY]]" ? "" : text;
        } else if (type == typeid(Config::VEC2)) {
            result[key + "_x"] = static_cast<double>(value->asVec2().x);
            result[key + "_y"] = static_cast<double>(value->asVec2().y);
        } else
            throw std::runtime_error("unsupported device storage type: " + key);
    }
    return result;
}
