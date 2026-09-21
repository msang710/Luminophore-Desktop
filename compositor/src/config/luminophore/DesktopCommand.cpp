#include "DesktopSettings.hpp"
#include "../shared/actions/ConfigActions.hpp"
#include "../ConfigManager.hpp"
#include "../supplementary/propRefresher/PropRefresher.hpp"
#include "../../desktop/state/ViewState.hpp"
#include "../../luminophore/LuminophoreSpatialGrabController.hpp"
#include "../../luminophore/LuminophoreShellProjection.hpp"
#include <charconv>
#include <cmath>
#include <set>
#include <stdexcept>

using namespace Luminophore;
using namespace Luminophore::Settings;
namespace CA = Config::Actions;

static std::string stringField(const toml::table& table, const char* key, size_t maximum = 256, bool empty = false) {
    const auto field = table[key];
    const auto value = field.value<std::string>();
    if (!field.is_string() || !value || (!empty && value->empty()) || value->size() > maximum || value->find('\0') != std::string::npos)
        throw std::runtime_error(std::string("invalid string: ") + key);
    return *value;
}

static uint64_t identity(const toml::table& table, const char* key, bool nonzero = false) {
    const auto token  = stringField(table, key, 20);
    uint64_t   value  = 0;
    const auto parsed = std::from_chars(token.data(), token.data() + token.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != token.data() + token.size() || (nonzero && !value))
        throw std::runtime_error(std::string("invalid identity: ") + key);
    return value;
}

static double number(const toml::table& table, const char* key) {
    const auto field = table[key];
    const auto value = field.value<double>();
    if ((!field.is_integer() && !field.is_floating_point()) || !value || !std::isfinite(*value))
        throw std::runtime_error(std::string("invalid number: ") + key);
    return *value;
}

static void fields(const toml::table& table, std::set<std::string> allowed) {
    allowed.insert("version");
    allowed.insert("action");
    for (const auto& [key, node] : table) {
        if (!allowed.contains(std::string(key.str())))
            throw std::runtime_error("unknown command field");
    }
}

std::string Luminophore::Settings::desktopCommand(const std::string& wire) {
    try {
        if (wire.size() > 1024 * 1024)
            throw std::runtime_error("command too large");
        const auto table = toml::parse(wire);
        if (!table["version"].is_integer() || table["version"].value<int64_t>() != 1)
            throw std::runtime_error("unsupported command version");
        const auto action  = stringField(table, "action");
        auto       result  = [](bool value) { return value ? "true" : "false"; };
        auto       checked = [](const CA::ActionResult& value) {
            if (!value)
                throw std::runtime_error(value.error().message);
            return std::string{"ok"};
        };
        if (action == "action") {
            fields(table, {"name"});
            const auto error = desktopAction(stringField(table, "name"));
            if (!error.empty())
                throw std::runtime_error(error);
            return "ok";
        }
        if (action == "focus" || action == "minimize" || action == "restore") {
            fields(table, {"window"});
            const auto selector = stringField(table, "window");
            if (!selector.starts_with("address:0x") || selector.substr(10).empty() || selector.substr(10).find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
                throw std::runtime_error("invalid window selector");
            const auto window = Desktop::viewState()->query().selector(selector).runWindow();
            if (!window)
                throw std::runtime_error("window is no longer available");
            return checked(action == "focus" ? CA::focus(window) : action == "minimize" ? CA::minimizeWindow(window) : CA::restoreWindow(window));
        }
        if (action == "grab-begin" || action == "grab-cancel") {
            const auto time = identity(table, "press_time", true);
            if (time > UINT32_MAX)
                throw std::runtime_error("invalid press timestamp");
            if (action == "grab-cancel") {
                fields(table, {"press_time"});
                return result(spatialGrabController()->cancelFromEditor(time));
            }
            fields(table, {"window_id", "revision", "topology", "output", "press_time"});
            return result(spatialGrabController()->beginFromEditor(identity(table, "window_id", true), identity(table, "revision"), identity(table, "topology"),
                                                                   identity(table, "output"), time));
        }
        if (action == "grab-layout") {
            fields(table, {"generation", "revision", "topology", "frame_generation", "frame_revision", "target_epoch", "cells"});
            const auto array = table["cells"].as_array();
            if (!array || array->size() > 4096)
                throw std::runtime_error("invalid editor cells");
            std::vector<SLuminophoreEditorCell>   cells;
            std::set<std::pair<int64_t, int64_t>> points;
            for (const auto& node : *array) {
                if (!node.is_table())
                    throw std::runtime_error("invalid editor cell");
                const auto& row = *node.as_table();
                fields(row, {"column", "row", "x", "y", "width", "height", "output"});
                if (!row["column"].is_integer() || !row["row"].is_integer())
                    throw std::runtime_error("invalid board coordinate");
                const auto column = *row["column"].value<int64_t>(), line = *row["row"].value<int64_t>();
                const auto width = number(row, "width"), height = number(row, "height");
                if (!points.emplace(column, line).second || width <= 0 || height <= 0)
                    throw std::runtime_error("invalid editor cell extent or duplicate");
                cells.push_back({{column, line}, number(row, "x"), number(row, "y"), width, height, identity(row, "output")});
            }
            const auto frame = stringField(table, "frame_generation");
            if (frame.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") != std::string::npos)
                throw std::runtime_error("invalid frame identity");
            return result(spatialGrabController()->bindLayout(identity(table, "generation", true), identity(table, "revision"), identity(table, "topology"), frame,
                                                              identity(table, "frame_revision", true), cells, identity(table, "target_epoch")));
        }
        if (action == "grab-badge") {
            fields(table, {"generation", "target_epoch", "mask", "red", "green", "blue"});
            const auto mask = stringField(table, "mask", 8192, true);
            const auto red = number(table, "red"), green = number(table, "green"), blue = number(table, "blue");
            if ((!mask.empty() && (mask.size() != 8192 || mask.find_first_not_of("0123456789abcdef") != std::string::npos)) || red < 0 || red > 1 || green < 0 || green > 1 ||
                blue < 0 || blue > 1)
                throw std::runtime_error("invalid badge asset");
            return result(spatialGrabController()->badgeAsset(identity(table, "generation", true), identity(table, "target_epoch"), mask, CHyprColor(red, green, blue, 1)));
        }
        if (action == "projection") {
            fields(table, {"surface",      "generation",     "revision",        "content_revision", "phase",           "requested_plane", "role",       "red",
                           "green",        "blue",           "radius",          "outline",          "extent",          "intensity",       "glow_phase", "reveal_from",
                           "reveal_to",    "reveal_started", "reveal_duration", "reveal_offset_x",  "reveal_offset_y", "panel_x",         "panel_y",    "panel_width",
                           "panel_height", "bloom",          "blur_x",          "blur_y",           "blur_width",      "blur_height"});
            const auto phase = stringField(table, "phase"), plane = stringField(table, "requested_plane"), role = stringField(table, "role");
            if ((phase != "prepare" && phase != "commit" && phase != "abort") || (plane != "bottom" && plane != "overlay") || (role != "passive" && role != "osd") ||
                !table["bloom"].is_boolean())
                throw std::runtime_error("invalid projection mode");
            const SShellProjectionStyle style{
                .color          = CHyprColor(number(table, "red"), number(table, "green"), number(table, "blue"), 1),
                .radius         = static_cast<float>(number(table, "radius")),
                .outline        = static_cast<float>(number(table, "outline")),
                .extent         = static_cast<float>(number(table, "extent")),
                .intensity      = static_cast<float>(number(table, "intensity")),
                .phase          = static_cast<float>(number(table, "glow_phase")),
                .revealFrom     = static_cast<float>(number(table, "reveal_from")),
                .revealTo       = static_cast<float>(number(table, "reveal_to")),
                .revealStarted  = number(table, "reveal_started"),
                .revealDuration = static_cast<float>(number(table, "reveal_duration")),
                .revealOffset   = {number(table, "reveal_offset_x"), number(table, "reveal_offset_y")},
                .panel          = {number(table, "panel_x"), number(table, "panel_y"), number(table, "panel_width"), number(table, "panel_height")},
                .bloom          = *table["bloom"].value<bool>(),
                .blurPanel      = {number(table, "blur_x"), number(table, "blur_y"), number(table, "blur_width"), number(table, "blur_height")},
            };
            return checked(
                CA::shellProjection(stringField(table, "surface"), stringField(table, "generation"), identity(table, "revision", true), identity(table, "content_revision"),
                                    phase == "prepare"    ? SHELL_PROJECTION_PREPARE :
                                        phase == "commit" ? SHELL_PROJECTION_COMMIT :
                                                            SHELL_PROJECTION_ABORT,
                                    plane == "bottom" ? SHELL_PROJECTION_BOTTOM : SHELL_PROJECTION_OVERLAY, role == "passive" ? SHELL_WIDGET_PASSIVE : SHELL_WIDGET_OSD, style));
        }
        throw std::runtime_error("unknown native command");
    } catch (const std::exception& error) { return std::string("error: ") + error.what(); }
}
