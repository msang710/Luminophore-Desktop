#include "InputRuntime.hpp"
#include "InputCurves.hpp"
#include "../../devices/IPointer.hpp"
#include <aquamarine/input/Input.hpp>
#include <libinput.h>
#include "../ConfigManager.hpp"
#include "../supplementary/propRefresher/PropRefresher.hpp"
#include "../../managers/input/InputManager.hpp"
#include "../../devices/IKeyboard.hpp"
#include <xkbcommon/xkbcommon.h>
#include <array>
#include <set>
#include <stdexcept>
using namespace Luminophore::Settings;

static Snapshot effective(const SGeneration& generation, const std::string& name) {
    Snapshot result;
    for (const auto& [key, value] : generation.values)
        if (key.starts_with("input."))
            result[key.substr(6)] = value;
    if (const auto rule = generation.devices.find(name); rule != generation.devices.end())
        for (const auto& [key, value] : rule->second)
            result[key] = value;
    return result;
}
using AccelerationConfig = std::unique_ptr<libinput_config_accel, decltype(&libinput_config_accel_destroy)>;
static AccelerationConfig customAcceleration(const Snapshot& values) {
    const auto         motion = parseInputCurve(std::get<std::string>(values.at("accel_profile")), true);
    const auto         scroll = parseInputCurve(std::get<std::string>(values.at("scroll_points")), false);
    AccelerationConfig config(nullptr, libinput_config_accel_destroy);
    if (motion.points.empty()) {
        if (!scroll.points.empty())
            throw std::runtime_error("scroll curve without custom acceleration");
        return config;
    }
    config.reset(libinput_config_accel_create(LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM));
    if (!config ||
        libinput_config_accel_set_points(config.get(), LIBINPUT_ACCEL_TYPE_MOTION, motion.step, motion.points.size(), motion.points.data()) != LIBINPUT_CONFIG_STATUS_SUCCESS ||
        (!scroll.points.empty() &&
         libinput_config_accel_set_points(config.get(), LIBINPUT_ACCEL_TYPE_SCROLL, scroll.step, scroll.points.size(), scroll.points.data()) != LIBINPUT_CONFIG_STATUS_SUCCESS))
        throw std::runtime_error("libinput rejected acceleration curve");
    return config;
}
static void applyAcceleration(const SGeneration& generation) {
    if (!g_pInputManager)
        throw std::runtime_error("input manager unavailable");
    for (const auto& pointer : g_pInputManager->m_pointers) {
        if (!pointer->aq() || !pointer->aq()->getLibinputHandle())
            continue;
        auto* device = pointer->aq()->getLibinputHandle();
        if (!libinput_device_config_accel_is_available(device))
            continue;
        const auto  values  = effective(generation, pointer->m_hlName);
        const auto& profile = std::get<std::string>(values.at("accel_profile"));
        if (profile.empty() && libinput_device_config_accel_get_profiles(device) == 0)
            continue;
        auto       config   = customAcceleration(values);
        const auto expected = config ? LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM :
            profile == "adaptive"    ? LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE :
            profile == "flat"        ? LIBINPUT_CONFIG_ACCEL_PROFILE_FLAT :
                                       libinput_device_config_accel_get_default_profile(device);
        const auto status   = config ? libinput_device_config_accel_apply(device, config.get()) : libinput_device_config_accel_set_profile(device, expected);
        if (status != LIBINPUT_CONFIG_STATUS_SUCCESS || libinput_device_config_accel_get_profile(device) != expected)
            throw std::runtime_error("pointer acceleration rejected: " + pointer->m_hlName);
    }
}
static std::string importedKeymap(const Snapshot& values) {
    const auto& file     = std::get<std::string>(values.at("kb_file"));
    const auto& snapshot = std::get<std::string>(values.at("kb_snapshot"));
    if (file.empty()) {
        if (!snapshot.empty())
            throw std::runtime_error("keymap snapshot without file");
        return {};
    }
    if (!snapshot.starts_with(file + "\n"))
        throw std::runtime_error("unimported keymap file");
    const auto context = std::unique_ptr<xkb_context, decltype(&xkb_context_unref)>(xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES), xkb_context_unref);
    if (!context)
        throw std::runtime_error("keymap context unavailable");
    const auto keymap = std::unique_ptr<xkb_keymap, decltype(&xkb_keymap_unref)>(
        xkb_keymap_new_from_string(context.get(), snapshot.c_str() + file.size() + 1, XKB_KEYMAP_FORMAT_TEXT_V2, XKB_KEYMAP_COMPILE_NO_FLAGS), xkb_keymap_unref);
    if (!keymap)
        throw std::runtime_error("imported keymap compilation failed");
    const auto text = std::unique_ptr<char, decltype(&std::free)>(xkb_keymap_get_as_string(keymap.get(), XKB_KEYMAP_FORMAT_TEXT_V2), std::free);
    if (!text)
        throw std::runtime_error("keymap serialization failed");
    return text.get();
}
void Luminophore::Settings::validateInputGeneration(const SGeneration& generation) {
    std::set<std::array<std::string, 5>> maps;
    auto                                 collect = [&](const std::string& name) {
        const auto values = effective(generation, name);
        customAcceleration(values);
        if (!importedKeymap(values).empty())
            return;
        maps.insert({std::get<std::string>(values.at("kb_rules")), std::get<std::string>(values.at("kb_model")), std::get<std::string>(values.at("kb_layout")),
                     std::get<std::string>(values.at("kb_variant")), std::get<std::string>(values.at("kb_options"))});
    };
    collect("");
    for (const auto& [name, values] : generation.devices)
        collect(name);
    const auto context = std::unique_ptr<xkb_context, decltype(&xkb_context_unref)>(xkb_context_new(XKB_CONTEXT_NO_FLAGS), xkb_context_unref);
    if (!context)
        throw std::runtime_error("keymap context unavailable");
    for (const auto& map : maps) {
        const xkb_rule_names names{map[0].c_str(), map[1].c_str(), map[2].c_str(), map[3].c_str(), map[4].c_str()};
        const auto           keymap = std::unique_ptr<xkb_keymap, decltype(&xkb_keymap_unref)>(
            xkb_keymap_new_from_names2(context.get(), &names, XKB_KEYMAP_FORMAT_TEXT_V2, XKB_KEYMAP_COMPILE_NO_FLAGS), xkb_keymap_unref);
        if (!keymap)
            throw std::runtime_error("keymap compilation failed");
    }
}
static Config::IConfigManager& manager() {
    if (!Config::mgr() || (Config::mgr()->type() != Config::CONFIG_LUMINOPHORE && Config::mgr()->type() != Config::CONFIG_LUA))
        throw std::runtime_error("transactional device manager unavailable");
    return *Config::mgr();
}
static void refreshInput() {
    using namespace Config::Supplementary;
    if (!refresher())
        throw std::runtime_error("input refresher unavailable");
    refresher()->scheduleRefresh(REFRESH_INPUT_DEVICES);
    if (refresher()->executeScheduledRefreshImmediately() != 0)
        throw std::runtime_error("input refresh not executed");
}
static DeviceSettings comparableDevices(DeviceSettings devices, const Snapshot& globals) {
    for (auto& [name, fields] : devices) {
        for (const auto* field : {"region_position", "region_size", "active_area_position", "active_area_size"}) {
            const auto x = std::string(field) + "_x", y = std::string(field) + "_y";
            if (!fields.contains(x) && !fields.contains(y))
                continue;
            if (!fields.contains(x))
                fields[x] = globals.at("tablet." + x);
            if (!fields.contains(y))
                fields[y] = globals.at("tablet." + y);
        }
        for (auto& [key, value] : fields)
            if (auto* number = std::get_if<double>(&value))
                *number = static_cast<Config::FLOAT>(*number);
    }
    return devices;
}
static void verifyInput(const SGeneration& generation) {
    if (comparableDevices(manager().deviceSettings(), generation.values) != comparableDevices(generation.devices, generation.values) || !g_pInputManager)
        throw std::runtime_error("device settings drift");
    // Check the same typed getter/alias path used when a device connects.
    // This verifies configured values, not absent hardware capabilities.
    for (const auto& [name, fields] : generation.devices) {
        for (const auto* key : {"region_position", "region_size", "active_area_size", "active_area_position"}) {
            const auto x = std::string(key) + "_x", y = std::string(key) + "_y";
            const auto expectedX =
                fields.contains(x) ? std::get<double>(fields.at(x)) : static_cast<double>(static_cast<Config::FLOAT>(std::get<double>(generation.values.at("tablet." + x))));
            const auto expectedY =
                fields.contains(y) ? std::get<double>(fields.at(y)) : static_cast<double>(static_cast<Config::FLOAT>(std::get<double>(generation.values.at("tablet." + y))));
            const auto actual = manager().getDeviceVec(name, key, std::string("input:tablet:") + key);
            if (static_cast<Config::FLOAT>(actual.x) != static_cast<Config::FLOAT>(expectedX) || static_cast<Config::FLOAT>(actual.y) != static_cast<Config::FLOAT>(expectedY))
                throw std::runtime_error("device vector drift: " + name);
        }
        for (const auto& [key, expected] : fields) {
            const auto field   = key == "tap_to_click" ? "tap-to-click" : key == "tap_and_drag" ? "tap-and-drag" : key;
            const bool matches = std::visit(
                [&](const auto& value) {
                    using T = std::decay_t<decltype(value)>;
                    if constexpr (std::is_same_v<T, std::string>)
                        return manager().getDeviceString(name, field, "") == value;
                    else if constexpr (std::is_same_v<T, double>) {
                        if (field.ends_with("_x") || field.ends_with("_y")) {
                            const auto base   = field.substr(0, field.size() - 2);
                            const auto actual = manager().getDeviceVec(name, base, "input:tablet:" + base);
                            return static_cast<Config::FLOAT>(field.ends_with("_x") ? actual.x : actual.y) == static_cast<Config::FLOAT>(value);
                        }
                        return manager().getDeviceFloat(name, field, "") == static_cast<float>(value);
                    } else
                        return manager().getDeviceInt(name, field, "") == value;
                },
                expected);
            if (!matches)
                throw std::runtime_error("device getter drift: " + name + ":" + key);
        }
    }
    for (const auto& keyboard : g_pInputManager->m_keyboards) {
        if (keyboard->m_keymapOverridden)
            continue;
        const auto  values   = effective(generation, keyboard->m_hlName);
        const auto& rules    = keyboard->m_currentRules;
        const auto  imported = importedKeymap(values);
        if (keyboard->m_xkbFilePath != std::get<std::string>(values.at("kb_file")) || keyboard->m_xkbSnapshot != std::get<std::string>(values.at("kb_snapshot")) ||
            (!imported.empty() && keyboard->m_xkbKeymapString != imported))
            throw std::runtime_error("imported keyboard map drift: " + keyboard->m_hlName);
        if (keyboard->m_numlockOn != std::get<bool>(values.at("numlock_by_default")) || keyboard->m_resolveBindsBySym != std::get<bool>(values.at("resolve_binds_by_sym")))
            throw std::runtime_error("keyboard policy drift: " + keyboard->m_hlName);

        if (!keyboard->m_xkbKeymap || rules.rules != std::get<std::string>(values.at("kb_rules")) || rules.model != std::get<std::string>(values.at("kb_model")) ||
            rules.layout != std::get<std::string>(values.at("kb_layout")) || rules.variant != std::get<std::string>(values.at("kb_variant")) ||
            rules.options != std::get<std::string>(values.at("kb_options")) || keyboard->m_repeatRate != std::get<int64_t>(values.at("repeat_rate")) ||
            keyboard->m_repeatDelay != std::get<int64_t>(values.at("repeat_delay")))
            throw std::runtime_error("keyboard settings drift: " + keyboard->m_hlName);
    }
}
SInputRuntime Luminophore::Settings::makeInputRuntime(const SGeneration& boot) {
    validateInputGeneration(boot);
    if (manager().type() == Config::CONFIG_LUMINOPHORE)
        manager().setDeviceSettings(boot.devices);
    struct State {
        SGeneration current, before, candidate;
    };
    auto state = std::make_shared<State>(State{boot, boot, boot});
    return {validateInputGeneration,
            [state](const SGeneration& candidate) {
                state->before         = state->current;
                state->before.devices = manager().deviceSettings();
                state->candidate      = candidate;
            },
            [state] {
                manager().setDeviceSettings(state->candidate.devices);
                refreshInput();
                applyAcceleration(state->candidate);
                state->current = state->candidate;
            },
            [state] { verifyInput(state->candidate); },
            [state] {
                manager().setDeviceSettings(state->before.devices);
                refreshInput();
                applyAcceleration(state->before);
                verifyInput(state->before);
                state->current = state->before;
            }};
}
