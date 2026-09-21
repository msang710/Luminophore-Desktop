#include "DesktopSettings.hpp"
#include "SettingsGeneration.hpp"
#include "GeneralSettings.hpp"
#include "../ConfigManager.hpp"
#include "../shared/actions/ConfigActions.hpp"
#include "../shared/workspace/WorkspaceRuleManager.hpp"
#include "../supplementary/executor/Executor.hpp"
#include "../supplementary/propRefresher/PropRefresher.hpp"
#include "../../desktop/rule/Engine.hpp"
#include "../../desktop/rule/windowRule/WindowRule.hpp"
#include "../../desktop/rule/layerRule/LayerRule.hpp"
#include "../../managers/KeybindManager.hpp"
#include "../../managers/input/InputManager.hpp"
#include "../../managers/input/trackpad/TrackpadGestures.hpp"
#include "../../managers/input/trackpad/gestures/CloseGesture.hpp"
#include "../../managers/input/trackpad/gestures/FloatGesture.hpp"
#include "../../managers/input/trackpad/gestures/FullscreenGesture.hpp"
#include "../../managers/input/trackpad/gestures/MoveGesture.hpp"
#include "../../managers/input/trackpad/gestures/ResizeGesture.hpp"
#include "../../managers/input/trackpad/gestures/CursorZoomGesture.hpp"
#include "../../luminophore/LuminophoreOccupyOutputController.hpp"
#include "../../luminophore/LuminophoreSpatialRuntime.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <format>
#include <set>
#include <sstream>
#include <stdexcept>
#include <re2/re2.h>

using namespace Luminophore::Settings;
namespace CA = Config::Actions;
namespace DR = Desktop::Rule;

static SDesktopSettings                                                 activeDesktop;
static toml::table                                                      sessionSettings;
static std::vector<SP<DR::IRule>>                                       ownedRules;
static std::vector<SP<SKeybind>>                                        ownedBindings;

static const std::map<std::string, std::pair<std::string, std::string>> colors = {
    {"general:col.active_border", {"primary", "ff545a92"}},
    {"general:col.inactive_border", {"surface_container", "fffbf8ff"}},
};

static std::string scalar(const toml::node& node) {
    if (node.is_string())
        return *node.value<std::string>();
    if (node.is_boolean())
        return *node.value<bool>() ? "true" : "false";
    if (node.is_integer())
        return std::to_string(*node.value<int64_t>());
    if (node.is_floating_point())
        return std::to_string(*node.value<double>());
    throw std::runtime_error("expected scalar rule field");
}

static void allowed(const toml::table& row, std::set<std::string> fields) {
    for (const auto& [name, value] : row) {
        if (!fields.contains(std::string(name.str())))
            throw std::runtime_error("unknown native domain field: " + std::string(name.str()));
    }
}

static SKeybind keybind(const SBindingDeclaration& item) {
    SKeybind result;
    result.handler      = "luminophore-action";
    result.arg          = item.action;
    result.description  = item.action;
    result.locked       = item.locked;
    result.release      = item.release;
    result.repeat       = item.repeating;
    result.nonConsuming = item.nonConsuming;
    std::istringstream input(item.chord);
    std::string        piece;
    bool               seenKey = false;
    while (std::getline(input, piece, '+')) {
        const auto first = piece.find_first_not_of(' '), last = piece.find_last_not_of(' ');
        if (first == std::string::npos)
            throw std::runtime_error("empty binding token");
        piece                                           = piece.substr(first, last - first + 1);
        const std::map<std::string, uint32_t> modifiers = {{"SUPER", 64}, {"META", 64}, {"CTRL", 4}, {"CONTROL", 4}, {"ALT", 8}, {"SHIFT", 1}};
        if (modifiers.contains(piece)) {
            if (seenKey)
                throw std::runtime_error("modifiers must precede binding key");
            result.modmask |= modifiers.at(piece);
            continue;
        }
        if (seenKey)
            throw std::runtime_error("binding requires one key");
        seenKey = true;
        if (piece.starts_with("mouse:") || piece.starts_with("code:")) {
            const auto start  = piece.find(':') + 1;
            uint32_t   number = 0;
            auto       parsed = std::from_chars(piece.data() + start, piece.data() + piece.size(), number);
            if (parsed.ec != std::errc{} || parsed.ptr != piece.data() + piece.size() || number == 0)
                throw std::runtime_error("invalid binding keycode");
            if (piece.starts_with("mouse:"))
                result.key = piece;
            else
                result.sMkKeys.emplace_back(XKB_KEY_NoSymbol, number);
        } else {
            const auto symbol = xkb_keysym_from_name(piece.c_str(), XKB_KEYSYM_CASE_INSENSITIVE);
            if (symbol == XKB_KEY_NoSymbol)
                throw std::runtime_error("unknown binding key: " + piece);
            result.sMkKeys.emplace_back(symbol, 0);
            result.key = piece;
        }
    }
    if (!seenKey)
        throw std::runtime_error("missing binding key");
    return result;
}

template <typename Rule>
static SP<DR::IRule> compileRule(const toml::table& row, const std::string& fallback) {
    allowed(row, {"name", "enabled", "match", "effects"});
    const auto match = row["match"].as_table(), effects = row["effects"].as_table();
    if (!match || match->empty() || !effects || effects->empty() || (row.contains("enabled") && !row["enabled"].is_boolean()))
        throw std::runtime_error("rule requires match and effects");
    auto rule = makeShared<Rule>(row["name"].value_or(fallback));
    rule->setEnabled(row["enabled"].value_or(true));
    for (const auto& [key, value] : *match) {
        const auto property = DR::matchPropFromString(key.str());
        if (!property || (std::is_same_v<Rule, DR::CLayerRule> && key.str() != "namespace"))
            throw std::runtime_error("unknown rule match");
        const std::set<std::string_view> regexKeys = {"class", "title", "initial_class", "initial_title", "content", "xdg_tag", "namespace"};
        const std::set<std::string_view> boolKeys  = {"float", "xwayland", "fullscreen", "pin", "focus", "modal"};
        if (regexKeys.contains(key.str())) {
            if (!value.is_string())
                throw std::runtime_error("rule regular expression must be a string");
            auto pattern = *value.value<std::string>();
            if (pattern.starts_with("negative:"))
                pattern.erase(0, 9);
            if (!re2::RE2(pattern).ok())
                throw std::runtime_error("invalid rule regular expression");
        } else if (boolKeys.contains(key.str()) && !value.is_boolean())
            throw std::runtime_error("rule match must be boolean");
        else if (key.str().starts_with("fullscreen_state_") && (!value.is_integer() || *value.value<int64_t>() < 0 || *value.value<int64_t>() > 3))
            throw std::runtime_error("invalid fullscreen state match");
        rule->registerMatch(*property, scalar(value));
    }
    for (const auto& [key, value] : *effects) {
        std::optional<uint16_t> effect;
        if constexpr (std::is_same_v<Rule, DR::CWindowRule>)
            effect = DR::windowEffects()->get(key.str());
        else
            effect = DR::layerEffects()->get(key.str());
        if (!effect || !*effect || key.str().starts_with("__internal"))
            throw std::runtime_error("unknown rule effect");
        if constexpr (std::is_same_v<Rule, DR::CWindowRule>) {
            if (value.is_array()) {
                const auto& parts = *value.as_array();
                if (parts.size() != 2 || (key.str() != "size" && key.str() != "move" && key.str() != "min_size" && key.str() != "max_size"))
                    throw std::runtime_error("invalid expression pair");
                auto result = rule->addEffect(*effect, Math::SExpressionVec2{scalar(parts[0]), scalar(parts[1])});
                if (!result)
                    throw std::runtime_error(result.error());
                continue;
            }
        }
        const auto result = rule->addEffect(*effect, scalar(value));
        if (!result)
            throw std::runtime_error(result.error());
    }
    return SP<DR::IRule>(rule);
}

static UP<ITrackpadGesture> gesture(const toml::table& row) {
    allowed(row, {"fingers", "direction", "action", "modifiers", "scale", "disable_inhibit"});
    if (!row["fingers"].is_integer() || row["fingers"].value_or<int64_t>(0) < 2 || row["fingers"].value_or<int64_t>(0) > 5 || !row["direction"].is_string() ||
        (row.contains("disable_inhibit") && !row["disable_inhibit"].is_boolean()))
        throw std::runtime_error("invalid gesture fields");
    const auto direction = g_pTrackpadGestures->dirForString(row["direction"].value_or(std::string{}));
    const auto scale     = row["scale"].value_or(1.0);
    if (row.contains("scale") && !row["scale"].is_integer() && !row["scale"].is_floating_point())
        throw std::runtime_error("gesture scale must be numeric");
    if (row.contains("modifiers") && !row["modifiers"].is_string())
        throw std::runtime_error("gesture modifiers must be a string");
    std::istringstream modifiers(row["modifiers"].value_or(std::string{}));
    std::string        modifier;
    while (modifiers >> modifier) {
        if (modifier != "SUPER" && modifier != "CTRL" && modifier != "ALT" && modifier != "SHIFT")
            throw std::runtime_error("unknown gesture modifier");
    }
    if (direction == TRACKPAD_GESTURE_DIR_NONE || !std::isfinite(scale) || scale <= 0 || scale > 100)
        throw std::runtime_error("invalid gesture direction/scale");
    const auto action = row["action"].value_or(std::string{});
    if (action == "close")
        return makeUnique<CCloseTrackpadGesture>();
    if (action == "float")
        return makeUnique<CFloatTrackpadGesture>("toggle");
    if (action == "fullscreen" || action == "maximize")
        return makeUnique<CFullscreenTrackpadGesture>(action);
    if (action == "move")
        return makeUnique<CMoveTrackpadGesture>();
    if (action == "resize")
        return makeUnique<CResizeTrackpadGesture>();
    if (action == "zoom")
        return makeUnique<CCursorZoomTrackpadGesture>("1", "live");
    throw std::runtime_error("unsupported gesture action");
}

struct SPreparedDesktop {
    std::vector<SKeybind>               bindings;
    std::vector<SP<DR::IRule>>          rules;
    std::vector<Config::CWorkspaceRule> workspaces;
};

static SPreparedDesktop prepare(const SDesktopSettings& settings) {
    SPreparedDesktop                                  result;
    std::set<std::tuple<uint32_t, std::string, bool>> triggers;
    auto                                              addBinding = [&](const SBindingDeclaration& row) {
        if (row.chord.empty())
            return;
        auto       binding = keybind(row);
        const auto key     = !binding.sMkKeys.empty() ? std::format("{}:{}", binding.sMkKeys[0].first, binding.sMkKeys[0].second) : binding.key;
        if (!triggers.emplace(binding.modmask, key, binding.release).second)
            throw std::runtime_error("duplicate binding trigger");
        result.bindings.push_back(std::move(binding));
    };
    for (const auto& row : settings.bindings)
        addBinding(row);
    std::set<std::string> bundleIDs;
    for (const auto& entry : settings.bundles) {
        if (!entry.is_table())
            throw std::runtime_error("invalid app bundle");
        const auto& row = *entry.as_table();
        allowed(row, {"id", "name", "chord", "items"});
        const auto id = row["id"].value_or(std::string{});
        if (id.empty() || id.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789-") != std::string::npos || !bundleIDs.insert(id).second)
            throw std::runtime_error("invalid bundle identity");
        const auto apps = row["items"].as_array();
        if (!apps || apps->empty() || apps->size() > 32)
            throw std::runtime_error("invalid bundle applications");
        for (const auto& app : *apps) {
            if (!app.is_table())
                throw std::runtime_error("invalid bundle application");
            allowed(*app.as_table(), {"desktop_id", "new_instance"});
            const auto& appTable = *app.as_table();
            const auto  desktop  = appTable["desktop_id"].value_or(std::string{});
            if (!desktop.ends_with(".desktop") || desktop.starts_with('-') || desktop.find_first_of("/\\:\n\r") != std::string::npos || !appTable["new_instance"].is_boolean())
                throw std::runtime_error("invalid bundle application identity");
        }
        addBinding({"bundle:" + id, row["chord"].value_or(std::string{}), "", false, false, false, false, false});
    }
    for (const auto* name : {"window_rules", "layer_rules"}) {
        const auto array = settings.native[name].as_array();
        if (!array)
            continue;
        for (const auto& row : *array) {
            const auto fallback = std::format("luminophore-{}-{}", name, result.rules.size());
            result.rules.push_back(std::string(name) == "window_rules" ? compileRule<DR::CWindowRule>(*row.as_table(), fallback) :
                                                                         compileRule<DR::CLayerRule>(*row.as_table(), fallback));
        }
    }
    for (const auto& [app, direction] : settings.placements) {
        auto        rule    = makeShared<DR::CWindowRule>("luminophore-placement-" + std::string(app.str()));
        std::string pattern = "^";
        for (const char c : app.str()) {
            if (std::string_view(R"(\.^$|?*+()[]{})").contains(c))
                pattern += '\\';
            pattern += c;
        }
        rule->registerMatch(DR::RULE_PROP_CLASS, pattern + "$");
        const auto added = rule->addEffect(DR::WINDOW_RULE_EFFECT_INITIAL_PLACEMENT, scalar(direction));
        if (!added)
            throw std::runtime_error(added.error());
        result.rules.emplace_back(SP<DR::IRule>(rule));
    }
    if (const auto rows = settings.native["gestures"].as_array()) {
        std::set<std::tuple<int64_t, std::string, uint32_t>> seen;
        for (const auto& node : *rows) {
            const auto& row = *node.as_table();
            gesture(row);
            const auto mods = g_pKeybindManager->stringToModMask(row["modifiers"].value_or(std::string{}));
            if (!seen.emplace(*row["fingers"].value<int64_t>(), *row["direction"].value<std::string>(), mods).second)
                throw std::runtime_error("duplicate gesture");
        }
    }
    if (const auto rows = settings.native["workspace_rules"].as_array()) {
        for (const auto& entry : *rows) {
            const auto& row = *entry.as_table();
            allowed(row, {"workspace", "monitor", "persistent"});
            Config::CWorkspaceRule rule;
            rule.m_workspaceString = row["workspace"].value_or(std::string{});
            rule.m_monitor         = row["monitor"].value_or(std::string{});
            if (!rule.m_workspaceString.starts_with("name:") || rule.m_monitor.empty() || (row.contains("persistent") && !row["persistent"].is_boolean()))
                throw std::runtime_error("workspace requires an explicit name and monitor");
            rule.m_workspaceName = rule.m_workspaceString.substr(5);
            rule.m_isPersistent  = row["persistent"].value_or(false);
            result.workspaces.push_back(rule);
        }
    }
    return result;
}

void Luminophore::Settings::installDesktopSettings(const SDesktopSettings& settings, bool startup) {
    auto next = prepare(settings);
    for (const auto& binding : ownedBindings)
        binding->enabled = false;
    std::erase_if(g_pKeybindManager->m_keybinds, [](const auto& binding) { return std::ranges::find(ownedBindings, binding) != ownedBindings.end(); });
    ownedBindings.clear();
    g_pKeybindManager->m_dispatchers["luminophore-action"] = [](std::string action) {
        const auto error = desktopAction(action);
        return SDispatchResult{.success = error.empty(), .error = error};
    };
    for (auto& binding : next.bindings)
        ownedBindings.push_back(g_pKeybindManager->addKeybind(std::move(binding)));
    for (const auto& rule : ownedRules)
        DR::ruleEngine()->unregisterRule(rule);
    ownedRules = next.rules;
    for (auto& rule : next.rules)
        DR::ruleEngine()->registerRule(std::move(rule));
    Config::workspaceRuleMgr()->clear();
    for (auto& rule : next.workspaces)
        Config::workspaceRuleMgr()->add(std::move(rule));
    g_pTrackpadGestures->clearGestures();
    if (const auto rows = settings.native["gestures"].as_array()) {
        for (const auto& node : *rows) {
            const auto& row   = *node.as_table();
            const auto  added = g_pTrackpadGestures->addGesture(
                gesture(row), *row["fingers"].value<int64_t>(), g_pTrackpadGestures->dirForString(*row["direction"].value<std::string>()),
                g_pKeybindManager->stringToModMask(row["modifiers"].value_or(std::string{})), row["scale"].value_or(1.0), row["disable_inhibit"].value_or(false));
            if (!added)
                throw std::runtime_error(added.error());
        }
    }
    activeDesktop = settings;
    for (const auto& [option, token] : colors) {
        const auto color = settings.native["palette"][token.first].value_or(token.second);
        auto       slot  = Config::mgr()->getConfigValue(option);
        if (!slot.dataptr || !slot.type)
            throw std::runtime_error("palette consumer unavailable: " + option);
        if (*slot.type == typeid(Config::CGradientValueData))
            *static_cast<Config::CGradientValueData*>(*slot.dataptr) = parseShadowGradient(color + " 0deg");
        else if (*slot.type == typeid(Config::INTEGER))
            *static_cast<Config::INTEGER*>(*slot.dataptr) = parseGeneralColor(color);
        else
            throw std::runtime_error("palette consumer type mismatch: " + option);
    }
    DR::ruleEngine()->updateAllRules();
    if (!startup)
        return;
    sessionSettings = settings.native;
    if (const auto environment = settings.native["environment"].as_table()) {
        for (const auto& [key, value] : *environment)
            setenv(std::string(key.str()).c_str(), value.value_or(std::string{}).c_str(), 1);
    }
    if (const auto commands = settings.native["startup"].as_array()) {
        for (const auto& command : *commands)
            Config::Supplementary::executor()->addExecOnce({.exec = *command.value<std::string>()});
    }
    if (const auto commands = settings.native["shutdown"].as_array()) {
        for (const auto& command : *commands)
            Config::Supplementary::executor()->addExecShutdown({.exec = *command.value<std::string>()});
    }
    if (settings.native["profile"].value_or(std::string{"desktop"}) == "greeter")
        return;
    if (const auto ready = std::getenv("LUMINOPHORE_SESSION_READY_COMMAND"); ready && *ready)
        Config::Supplementary::executor()->addExecOnce({.exec = ready});
    else if (const auto shell = std::getenv("LUMINOPHORE_SHELL_COMMAND"); shell && *shell)
        Config::Supplementary::executor()->addExecOnce({.exec = shell});
}

bool Luminophore::Settings::desktopSessionRestartRequired() {
    for (const auto key : {"environment", "startup", "shutdown"}) {
        if (std::string_view(key) == "environment") {
            const auto before = sessionSettings[key].as_table(), after = activeDesktop.native[key].as_table();
            if ((before ? *before : toml::table{}) != (after ? *after : toml::table{}))
                return true;
        } else {
            const auto before = sessionSettings[key].as_array(), after = activeDesktop.native[key].as_array();
            if ((before ? *before : toml::array{}) != (after ? *after : toml::array{}))
                return true;
        }
    }
    return false;
}

SInputRuntime Luminophore::Settings::makeDesktopRuntime(const SGeneration& boot) {
    installDesktopSettings(decodeDesktopSettings(boot.documents));
    auto input = makeInputRuntime(boot);
    struct SChange {
        SDesktopSettings before, after;
        bool             applied          = false;
        uint64_t         gesturesRevision = 0;
    };
    auto change = std::make_shared<SChange>();
    return {
        [input](const SGeneration& candidate) {
            input.validate(candidate);
            decodeDesktopSettings(candidate.documents);
        },
        [input, change](const SGeneration& candidate) {
            input.prepare(candidate);
            change->before = activeDesktop;
            change->after  = decodeDesktopSettings(candidate.documents);
            if (change->before.native["profile"].value_or(std::string{"desktop"}) != change->after.native["profile"].value_or(std::string{"desktop"}))
                throw std::runtime_error("session profile cannot change during a session");
            prepare(change->after);
            change->applied = false;
        },
        [input, change] {
            change->applied = true;
            input.apply();
            installDesktopSettings(change->after);
            change->gesturesRevision = g_pTrackpadGestures->configurationRevision();
        },
        [input, change] {
            input.verify();
            if (!change->applied || std::ranges::any_of(ownedBindings, [](const auto& binding) {
                    return !binding->enabled || std::ranges::find(g_pKeybindManager->m_keybinds, binding) == g_pKeybindManager->m_keybinds.end();
                }))
                throw std::runtime_error("native binding readback failed");
            const auto expected = prepare(change->after);
            if (expected.bindings.size() != ownedBindings.size())
                throw std::runtime_error("native binding count mismatch");
            for (size_t i = 0; i < expected.bindings.size(); ++i) {
                const auto& want = expected.bindings[i];
                const auto& got  = *ownedBindings[i];
                if (want.key != got.key || want.sMkKeys != got.sMkKeys || want.modmask != got.modmask || want.handler != got.handler || want.arg != got.arg ||
                    want.locked != got.locked || want.release != got.release || want.repeat != got.repeat || want.nonConsuming != got.nonConsuming)
                    throw std::runtime_error("native binding value mismatch");
            }
            const auto& workspaces = Config::workspaceRuleMgr()->getAllWorkspaceRules();
            if (workspaces.size() != expected.workspaces.size())
                throw std::runtime_error("native workspace rule count mismatch");
            for (size_t i = 0; i < workspaces.size(); ++i) {
                const auto& want = expected.workspaces[i];
                const auto& got  = *workspaces[i];
                if (!got.isEnabled() || got.m_workspaceString != want.m_workspaceString || got.m_monitor != want.m_monitor || got.m_isPersistent != want.m_isPersistent)
                    throw std::runtime_error("native workspace rule readback failed");
            }
            if (change->gesturesRevision != g_pTrackpadGestures->configurationRevision())
                throw std::runtime_error("native gesture registry changed");
            for (const auto& [option, token] : colors) {
                const auto color = change->after.native["palette"][token.first].value_or(token.second);
                const auto slot  = Config::mgr()->getConfigValue(option);
                if (!slot.dataptr || !slot.type)
                    throw std::runtime_error("palette readback unavailable");
                if (*slot.type == typeid(Config::CGradientValueData)) {
                    if (*static_cast<Config::CGradientValueData*>(*slot.dataptr) != parseShadowGradient(color + " 0deg"))
                        throw std::runtime_error("palette gradient readback mismatch");
                } else if (*slot.type != typeid(Config::INTEGER) || *static_cast<Config::INTEGER*>(*slot.dataptr) != parseGeneralColor(color))
                    throw std::runtime_error("palette color readback mismatch");
            }
            for (const auto& rule : ownedRules) {
                if (std::ranges::find(DR::ruleEngine()->rules(), rule) == DR::ruleEngine()->rules().end())
                    throw std::runtime_error("native rule readback failed");
            }
        },
        [input, change] {
            input.restore();
            if (change->applied)
                installDesktopSettings(change->before);
            change->applied = false;
        },
    };
}

std::string Luminophore::Settings::desktopAction(const std::string& action) {
    auto result = [](const CA::ActionResult& value) { return value ? std::string{} : value.error().message; };
    auto spawn  = [](const std::string& command) { return Config::Supplementary::executor()->spawn(command) ? std::string{} : std::string{"application launch failed"}; };
    if (action == "window.close")
        return result(CA::closeWindow());
    if (action == "window.kill_active")
        return result(CA::killWindow());
    if (action == "window.float")
        return result(CA::floatWindow(CA::TOGGLE_ACTION_TOGGLE));
    if (action == "window.fullscreen")
        return result(CA::occupyOutput(Luminophore::OCCUPY_OUTPUT_TOGGLE));
    if (action == "window.cycle")
        return result(CA::cycleNext(true, std::nullopt, std::nullopt));
    if (action == "view.desktop.toggle")
        return result(CA::spatialToggleDesktop());
    if (action == "view.wide.toggle")
        return result(CA::spatialToggleWide());
    if (action == "spatial.undo" || action == "spatial.redo") {
        const auto outcome = Luminophore::spatialRuntime()->historyRequest(action == "spatial.redo", std::nullopt, "", "");
        return outcome == eLuminophoreHistoryResult::APPLIED || outcome == eLuminophoreHistoryResult::EMPTY ? std::string{} : "history operation unavailable";
    }
    if (action == "window.drag.begin" || action == "window.resize") {
        if (g_pKeybindManager->m_currentKeybind)
            g_pKeybindManager->m_currentKeybind->releasePending = true;
        return result(CA::mouse(action == "window.resize" ? "resizewindow" : "movewindow"));
    }
    if (action.starts_with("view.move.") || action.starts_with("view.adjust.") || action.starts_with("board.move.") || action.starts_with("window.focus.")) {
        const auto name      = action.substr(action.rfind('.') + 1);
        const auto direction = name == "left" ? Math::DIRECTION_LEFT : name == "right" ? Math::DIRECTION_RIGHT : name == "up" ? Math::DIRECTION_UP : Math::DIRECTION_DOWN;
        if (name != "left" && name != "right" && name != "up" && name != "down")
            return "invalid direction";
        if (action.starts_with("view.move."))
            return result(CA::spatialMoveView(direction));
        if (action.starts_with("view.adjust."))
            return result(CA::spatialAdjustView(direction));
        if (action.starts_with("window.focus."))
            return result(CA::spatialFocusDirection(direction));
        return result(CA::spatialMoveWindow(direction));
    }
    if (action == "shell.outside_click") {
        const auto position = g_pInputManager->getMouseCoordsInternal();
        return result(CA::event(std::format("luminophore-shell-click:{},{}", position.x, position.y)));
    }
    if (action == "cursor.zoom_in" || action == "cursor.zoom_out" || action == "cursor.zoom_in_keypad" || action == "cursor.zoom_out_keypad") {
        auto reply = Config::mgr()->getConfigValue("cursor:zoom_factor");
        if (!reply.dataptr || !reply.type || *reply.type != typeid(Config::FLOAT))
            return "zoom unavailable";
        auto& zoom = *static_cast<Config::FLOAT*>(*reply.dataptr);
        zoom       = std::clamp(zoom + (action.starts_with("cursor.zoom_in") ? 0.3F : -0.3F), 1.F, 3.F);
        Config::Supplementary::refresher()->scheduleRefresh(Config::Supplementary::REFRESH_CURSOR_ZOOMS);
        return {};
    }
    if (action.starts_with("app.")) {
        auto name = action.substr(4);
        if (name == "calculator_hardware")
            name = "calculator";
        static const std::map<std::string, std::string> defaults = {{"terminal", "ghostty"},
                                                                    {"files", "dolphin"},
                                                                    {"browser", "google-chrome-stable"},
                                                                    {"editor", "gnome-text-editor --new-window"},
                                                                    {"calculator", "gnome-calculator"},
                                                                    {"mission_center", "missioncenter"}};
        if (!defaults.contains(name))
            return "unknown application action";
        return spawn("/usr/bin/luminophore-shell launch -- " + activeDesktop.native["applications"][name].value_or(defaults.at(name)));
    }
    if (action.starts_with("bundle:")) {
        const auto id = action.substr(7);
        if (id.empty() || id.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789-") != std::string::npos)
            return "invalid bundle";
        return spawn("/usr/bin/luminophore-shell launch-bundle " + id);
    }
    static const std::map<std::string, std::string> commands = {
        {"shell.launcher", "ctl open launcher"},
        {"shell.settings", "ctl open system --provider settings"},
        {"shell.spatial_editor", "ctl toggle spatial-editor"},
        {"shell.overview", "ctl toggle overview"},
        {"shell.emoji", "ctl open launcher --provider emoji"},
        {"shell.appearance", "ctl open system --provider palette"},
        {"shell.wallpaper", "ctl wallpaper open"},
        {"shell.clipboard", "ctl open launcher --provider clip"},
        {"shell.notifications", "ctl open notifications"},
        {"capture.region", "capture region --freeze --clipboard-only"},
        {"capture.region_save", "capture region --freeze --save"},
        {"hardware.volume_up", "ctl hardware volume-up"},
        {"hardware.volume_down", "ctl hardware volume-down"},
        {"hardware.volume_mute", "ctl hardware volume-mute"},
        {"hardware.microphone_mute", "ctl hardware mic-mute"},
        {"hardware.media_toggle", "ctl hardware media-toggle"},
        {"hardware.media_pause", "ctl hardware media-toggle"},
        {"hardware.media_next", "ctl hardware media-next"},
        {"hardware.media_previous", "ctl hardware media-previous"},
        {"hardware.brightness_up.preview", "ctl hardware brightness-preview-up"},
        {"hardware.brightness_down.preview", "ctl hardware brightness-preview-down"},
        {"hardware.brightness_up.commit", "ctl hardware brightness-commit"},
        {"hardware.brightness_down.commit", "ctl hardware brightness-commit"},
    };
    if (commands.contains(action))
        return spawn("/usr/bin/luminophore-shell " + commands.at(action));
    if (action == "utility.color_picker")
        return spawn("hyprpicker -a -n");
    return "unknown desktop action";
}
