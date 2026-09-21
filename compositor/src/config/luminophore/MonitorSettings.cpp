#include "MonitorSettings.hpp"
#include <toml++/toml.hpp>
#include <cmath>
#include <regex>
#include <set>
#include <stdexcept>

using namespace Luminophore::Settings;
static void require(bool valid) {
    if (!valid)
        throw std::runtime_error("monitors.toml: invalid or unsupported output rule");
}
static std::pair<int, int> point(const toml::node& node) {
    const auto* a = node.as_array();
    require(a && a->size() == 2);
    require(a->at(0).is_integer() && a->at(1).is_integer());
    auto x = *a->at(0).value<int64_t>(), y = *a->at(1).value<int64_t>();
    require(x >= -100000 && x <= 100000 && y >= -100000 && y <= 100000);
    return {static_cast<int>(x), static_cast<int>(y)};
}
MonitorSettings Luminophore::Settings::decodeMonitorSettings(std::string_view document) {
    const auto raw = toml::parse(document);
    require(raw["schema_version"].is_integer() && raw["schema_version"].value<int64_t>() == 1);
    for (const auto& [key, node] : raw)
        require(key == "schema_version" || ((key == "positions" || key == "outputs") && node.is_table()));
    const toml::table     empty;
    const auto&           positions = raw["positions"].is_table() ? *raw["positions"].as_table() : empty;
    const auto&           outputs   = raw["outputs"].is_table() ? *raw["outputs"].as_table() : empty;
    std::set<std::string> names;
    for (const auto& [key, node] : positions)
        names.emplace(key.str());
    for (const auto& [key, node] : outputs)
        names.emplace(key.str());
    require(names.size() <= 32);
    MonitorSettings  result;
    const std::regex identifier("[A-Za-z0-9_-]{1,1024}");
    const std::regex mode("([1-9][0-9]{0,4})x([1-9][0-9]{0,4})@([1-9][0-9]{0,3}(?:\\.[0-9]{1,3})?)");
    for (const auto& name : names) {
        require(std::regex_match(name, identifier));
        SMonitorSettings rule;
        require(!outputs.contains(name) || outputs[name].is_table());
        const auto& fields = outputs.contains(name) ? *outputs[name].as_table() : empty;
        for (const auto& [key, node] : fields)
            require(key == "mode" || key == "scale" || key == "transform" || key == "position" || key == "disabled" || key == "vrr");
        if (fields.contains("mode")) {
            require(fields["mode"].is_string());
            const auto text = *fields["mode"].value<std::string>();
            if (text != "preferred") {
                std::smatch match;
                require(std::regex_match(text, match, mode));
                rule.width   = std::stoi(match[1]);
                rule.height  = std::stoi(match[2]);
                rule.refresh = std::stod(match[3]);
                require(rule.width <= 32768 && rule.height <= 32768 && rule.refresh <= 1000);
            }
        }
        if (fields.contains("scale")) {
            require(fields["scale"].is_integer() || fields["scale"].is_floating_point());
            rule.scale = *fields["scale"].value<double>();
            require(std::isfinite(rule.scale) && rule.scale >= .25 && rule.scale <= 8);
        }
        if (fields.contains("transform")) {
            require(fields["transform"].is_integer());
            const auto transform = *fields["transform"].value<int64_t>();
            require(transform >= 0 && transform <= 7);
            rule.transform = transform;
        }
        if (fields.contains("disabled")) {
            require(fields["disabled"].is_boolean());
            rule.disabled = *fields["disabled"].value<bool>();
        }
        if (fields.contains("vrr")) {
            require(fields["vrr"].is_integer());
            const auto vrr = *fields["vrr"].value<int64_t>();
            require(vrr >= 0 && vrr <= 3);
            rule.vrr = static_cast<int>(vrr);
        }
        require(!(positions.contains(name) && fields.contains("position")));
        if (positions.contains(name))
            rule.position = point(*positions.get(name));
        else if (fields.contains("position"))
            rule.position = point(*fields.get("position"));
        result.emplace(name, rule);
    }
    return result;
}
