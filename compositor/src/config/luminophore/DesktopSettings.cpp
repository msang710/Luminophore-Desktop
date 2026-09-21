#include "DesktopSettings.hpp"
#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>

using namespace Luminophore::Settings;

static void keys(const toml::table& table, const std::set<std::string>& allowed) {
    for (const auto& [key, value] : table) {
        if (!allowed.contains(std::string(key.str())))
            throw std::runtime_error("unknown desktop field: " + std::string(key.str()));
    }
}

static std::string text(const toml::node& node, bool empty = false) {
    const auto value = node.value<std::string>();
    if (!node.is_string() || !value || (!empty && value->empty()) || value->size() > 8192 || std::ranges::any_of(*value, [](unsigned char c) { return c < 32; }))
        throw std::runtime_error("invalid desktop string");
    return *value;
}

static bool boolean(const toml::node_view<const toml::node>& value, bool fallback) {
    if (!value)
        return fallback;
    if (!value.is_boolean())
        throw std::runtime_error("expected desktop boolean");
    return *value.value<bool>();
}

static toml::table document(const std::string& value) {
    auto table = toml::parse(value.empty() ? "schema_version=1" : value);
    if (!table["schema_version"].is_integer() || table["schema_version"].value<int64_t>() != 1)
        throw std::runtime_error("unsupported desktop document version");
    table.erase("schema_version");
    return table;
}

void Luminophore::Settings::validateShellField(const std::string& path, const toml::node& value) {
    const auto it = SHELL_FIELD_TYPES.find(path);
    if (it == SHELL_FIELD_TYPES.end()) {
        if (!value.is_table())
            throw std::runtime_error("unknown Shell setting: " + path);
        for (const auto& [name, child] : *value.as_table())
            validateShellField(path + "." + std::string(name.str()), child);
        if (std::ranges::none_of(SHELL_FIELD_TYPES, [&](const auto& entry) { return entry.first.starts_with(path + "."); }))
            throw std::runtime_error("unknown Shell section: " + path);
        return;
    }
    const auto& kind  = it->second;
    bool        valid = (kind == "str" && value.is_string()) || (kind == "bool" && value.is_boolean()) || (kind == "int" && value.is_integer()) ||
        (kind == "float" && (value.is_floating_point() || value.is_integer()) && std::isfinite(*value.value<double>()));
    if (kind == "strings") {
        valid = value.is_array();
        std::set<std::string> seen;
        if (valid) {
            for (const auto& item : *value.as_array()) {
                if (!seen.insert(text(item)).second)
                    throw std::runtime_error("duplicate Shell collection value");
            }
        }
    } else if (kind == "string_table" || kind == "palette_table") {
        valid = value.is_table();
        if (valid) {
            for (const auto& [key, item] : *value.as_table()) {
                if (key.str().empty())
                    throw std::runtime_error("empty Shell collection key");
                if (kind == "string_table")
                    text(item);
                else {
                    if (!item.is_array() || item.as_array()->size() != 2)
                        throw std::runtime_error("palette requires two colors");
                    for (const auto& color : *item.as_array())
                        text(color);
                }
            }
        }
    }
    if (!valid)
        throw std::runtime_error("invalid Shell setting type: " + path);
}

SDesktopSettings Luminophore::Settings::decodeDesktopSettings(const std::array<std::string, 5>& documents) {
    SDesktopSettings result{.bindings = BINDING_DECLARATIONS};
    const auto       settings = document(documents[0]);
    if (settings.contains("native")) {
        if (!settings["native"].is_table())
            throw std::runtime_error("native settings require a table");
        result.native = *settings["native"].as_table();
    }
    keys(result.native, {"profile", "palette", "environment", "startup", "shutdown", "applications", "gestures", "window_rules", "layer_rules", "workspace_rules"});
    const auto profile = result.native["profile"].value_or(std::string{"desktop"});
    if ((result.native.contains("profile") && !result.native["profile"].is_string()) || (profile != "desktop" && profile != "greeter"))
        throw std::runtime_error("invalid native profile");
    if (const auto palette = result.native["palette"].as_table()) {
        keys(*palette, {"primary", "surface_container", "secondary", "error"});
        for (const auto& [key, node] : *palette) {
            const auto color = text(node);
            if (color.size() != 8 || color.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
                throw std::runtime_error("palette requires AARRGGBB colors");
        }
    } else if (result.native.contains("palette"))
        throw std::runtime_error("palette requires a table");
    for (const auto* name : {"startup", "shutdown"}) {
        if (!result.native.contains(name))
            continue;
        const auto array = result.native[name].as_array();
        if (!array || array->size() > 1024)
            throw std::runtime_error("invalid startup/shutdown list");
        for (const auto& entry : *array)
            text(entry);
    }
    for (const auto* name : {"environment", "applications"}) {
        if (!result.native.contains(name))
            continue;
        const auto table = result.native[name].as_table();
        if (!table || table->size() > 128)
            throw std::runtime_error("invalid environment/applications table");
        if (std::string(name) == "applications")
            keys(*table, {"terminal", "files", "browser", "editor", "calculator", "mission_center"});
        for (const auto& [key, entry] : *table) {
            const auto variable = std::string(key.str());
            if (std::string(name) == "environment" &&
                (variable.empty() || variable.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_") != std::string::npos ||
                 (variable.front() >= '0' && variable.front() <= '9')))
                throw std::runtime_error("invalid environment variable");
            text(entry, std::string(name) == "environment");
        }
    }
    for (const auto* name : {"gestures", "window_rules", "layer_rules", "workspace_rules"}) {
        if (!result.native.contains(name))
            continue;
        const auto array = result.native[name].as_array();
        if (!array || array->size() > 1024)
            throw std::runtime_error("invalid desktop rule list");
        for (const auto& row : *array) {
            if (!row.is_table())
                throw std::runtime_error("desktop rule must be a table");
        }
    }
    const auto bindings = document(documents[2]);
    keys(bindings, {"actions"});
    if (bindings.contains("actions")) {
        const auto actions = bindings["actions"].as_table();
        if (!actions)
            throw std::runtime_error("bindings.actions must be a table");
        for (const auto& [id, node] : *actions) {
            auto action = std::ranges::find(result.bindings, id.str(), &SBindingDeclaration::action);
            if (action == result.bindings.end() || !node.is_table())
                throw std::runtime_error("unknown binding action");
            const auto& row = *node.as_table();
            keys(row, {"chord", "disabled", "flags"});
            const bool disabled = boolean(row["disabled"], false);
            if (disabled && row.contains("chord"))
                throw std::runtime_error("disabled binding has a chord");
            if (row.contains("chord"))
                action->chord = text(*row["chord"].node());
            if (disabled)
                action->chord.clear();
            if (row.contains("flags")) {
                const auto flags = row["flags"].as_table();
                if (!flags)
                    throw std::runtime_error("binding flags require a table");
                keys(*flags, {"locked", "release", "repeating", "non_consuming"});
                action->locked       = boolean((*flags)["locked"], action->locked);
                action->release      = boolean((*flags)["release"], action->release);
                action->repeating    = boolean((*flags)["repeating"], action->repeating);
                action->nonConsuming = boolean((*flags)["non_consuming"], action->nonConsuming);
            }
        }
    }
    bool                                                    recovery = false;
    std::map<std::string, std::vector<SBindingDeclaration>> groups;
    for (const auto& action : result.bindings) {
        if (action.release && action.repeating)
            throw std::runtime_error("release binding cannot repeat");
        recovery |= action.recovery && !action.chord.empty();
        if (!action.group.empty())
            groups[action.group].push_back(action);
    }
    if (!recovery)
        throw std::runtime_error("a recovery binding is required");
    for (const auto& [name, rows] : groups) {
        if (rows.size() != 2 || rows[0].chord != rows[1].chord || rows[0].release == rows[1].release || rows[0].locked != rows[1].locked)
            throw std::runtime_error("inconsistent binding group");
    }
    const auto placements = document(documents[3]);
    keys(placements, {"rules"});
    if (placements.contains("rules")) {
        if (!placements["rules"].is_table())
            throw std::runtime_error("placement.rules must be a table");
        result.placements = *placements["rules"].as_table();
        for (const auto& [app, direction] : result.placements) {
            const auto name = text(direction);
            if (app.str().empty() || (name != "left" && name != "right" && name != "up" && name != "down"))
                throw std::runtime_error("invalid initial placement");
        }
    }
    const auto bundles = document(documents[4]);
    keys(bundles, {"bundles"});
    if (bundles.contains("bundles")) {
        if (!bundles["bundles"].is_array())
            throw std::runtime_error("bundles must be an array");
        result.bundles = *bundles["bundles"].as_array();
    }
    if (profile == "greeter") {
        if (!bindings.empty() || !placements.empty() || !bundles.empty())
            throw std::runtime_error("greeter cannot contain desktop actions");
        result.bindings.clear();
    }
    return result;
}
