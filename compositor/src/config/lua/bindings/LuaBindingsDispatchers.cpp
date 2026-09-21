#include "../../../luminophore/LuminophoreLivePipProtocol.hpp"
#include "LuaBindingsInternal.hpp"

#include <hyprutils/string/String.hpp>
#include <lua.h>

#include <cmath>
#include "../../../managers/EventManager.hpp"
#include <charconv>
#include <limits>

#include "Check.hpp"

#include "../../supplementary/executor/Executor.hpp"

#include "../../../managers/SeatManager.hpp"
#include "../../../managers/fullscreen/FullscreenController.hpp"
#include "../../../state/MonitorState.hpp"
#include "../../../state/WorkspaceState.hpp"
#include "../../../devices/IKeyboard.hpp"
#include "../../../desktop/rule/windowRule/WindowRule.hpp"
#include "config/shared/actions/ConfigActions.hpp"
#include "../../../luminophore/LuminophoreOccupyOutputController.hpp"
#include "../../../luminophore/LuminophoreShellProjection.hpp"
#include "../../../luminophore/LuminophoreVisualSettings.hpp"
#include "../../../luminophore/LuminophoreSpatialRuntime.hpp"
#include "../../../luminophore/LuminophoreSpatialEditorProtocol.hpp"
#include "../../../luminophore/LuminophoreSpatialGrabController.hpp"

using namespace Config;
using namespace Config::Lua;
using namespace Config::Lua::Bindings;
using namespace Hyprutils::String;

namespace CA = Config::Actions;

static constexpr auto ERR        = CA::eActionErrorLevel::ERROR;
static constexpr auto WARN       = CA::eActionErrorLevel::WARNING;
static constexpr auto INFO       = CA::eActionErrorLevel::INFO;
static constexpr auto C_UNKNOWN  = CA::eActionErrorCode::UNKNOWN;
static constexpr auto C_INVARG   = CA::eActionErrorCode::INVALID_ARGUMENT;
static constexpr auto C_NOTFOUND = CA::eActionErrorCode::NOT_FOUND;
static constexpr auto C_NOTARGET = CA::eActionErrorCode::NO_TARGET;
static constexpr auto C_UNAVAIL  = CA::eActionErrorCode::UNAVAILABLE;
static constexpr auto C_EXECFAIL = CA::eActionErrorCode::EXECUTION_FAILED;

static int            dsp_moveCursorToCorner(lua_State* L) {
    return Internal::checkResult(L, CA::moveCursorToCorner((int)lua_tonumber(L, lua_upvalueindex(1)), Internal::windowFromUpval(L, 2)));
}

static int dsp_moveCursor(lua_State* L) {
    return Internal::checkResult(L, CA::moveCursor(Vector2D{lua_tonumber(L, lua_upvalueindex(1)), lua_tonumber(L, lua_upvalueindex(2))}));
}

static int hlCursorMoveToCorner(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.cursor.move_to_corner: expected a table { corner, window? }");

    lua_pushnumber(L, Internal::requireTableFieldNum(L, 1, "corner", "hl.cursor.move_to_corner"));
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_moveCursorToCorner, 2);
    return 1;
}

static int hlCursorMove(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.cursor.move: expected a table { x, y }");

    lua_pushnumber(L, Internal::requireTableFieldNum(L, 1, "x", "hl.cursor.move"));
    lua_pushnumber(L, Internal::requireTableFieldNum(L, 1, "y", "hl.cursor.move"));
    lua_pushcclosure(L, dsp_moveCursor, 2);
    return 1;
}

static int dsp_execCmd(lua_State* L) {
    auto proc = lua_tostring(L, lua_upvalueindex(1));

    if (std::string_view{proc}.empty())
        return Internal::dispatcherError(L, "Invalid process string", ERR, C_INVARG);

    std::optional<uint64_t> pid;
    auto                    ruleRet = Internal::buildRuleFromTable(L, lua_upvalueindex(2));

    if (!ruleRet)
        return ruleRet.error();

    if (*ruleRet)
        pid = Config::Supplementary::executor()->spawn(Config::Supplementary::SExecRequest{.exec = proc, .rule = std::move(*ruleRet)});
    else
        pid = Config::Supplementary::executor()->spawn(proc);

    if (!pid.has_value())
        return Internal::dispatcherError(L, "Failed to start process", ERR, C_EXECFAIL);
    return Internal::pushSuccessResult(L);
}

static int dsp_execRaw(lua_State* L) {
    auto proc = Config::Supplementary::executor()->spawnRaw(lua_tostring(L, lua_upvalueindex(1)));
    if (!proc || !*proc)
        return Internal::dispatcherError(L, "Failed to start process", ERR, C_EXECFAIL);
    return Internal::pushSuccessResult(L);
}

static int dsp_exit(lua_State* L) {
    return Internal::checkResult(L, CA::exit());
}

static int dsp_submap(lua_State* L) {
    return Internal::checkResult(L, CA::setSubmap(lua_tostring(L, lua_upvalueindex(1))));
}

static int dsp_pass(lua_State* L) {
    const auto PWINDOW = Desktop::viewState()->query().selector(lua_tostring(L, lua_upvalueindex(1))).runWindow();
    if (!PWINDOW)
        return Internal::dispatcherError(L, "hl.pass: window not found", WARN, C_NOTFOUND);

    if (g_pKeybindManager->m_currentKeybind)
        g_pKeybindManager->m_currentKeybind->releasePending = true;

    return Internal::checkResult(L, CA::pass(PWINDOW));
}

static int dsp_dpms(lua_State* L) {
    auto                      action = sc<CA::eTogglableAction>((int)lua_tonumber(L, lua_upvalueindex(1)));
    std::optional<PHLMONITOR> mon    = std::nullopt;

    if (!lua_isnil(L, lua_upvalueindex(2))) {
        auto m = State::monitorState()->query().relativeTo(Desktop::focusState()->monitor()).configString(lua_tostring(L, lua_upvalueindex(2))).run();
        if (m)
            mon = m;
    }

    return Internal::checkResult(L, CA::dpms(action, mon));
}

static int dsp_event(lua_State* L) {
    return Internal::checkResult(L, CA::event(lua_tostring(L, lua_upvalueindex(1))));
}

static int dsp_global(lua_State* L) {
    if (g_pKeybindManager->m_currentKeybind)
        g_pKeybindManager->m_currentKeybind->releasePending = true;

    return Internal::checkResult(L, CA::global(lua_tostring(L, lua_upvalueindex(1))));
}

static int dsp_forceRendererReload(lua_State* L) {
    return Internal::checkResult(L, CA::forceRendererReload());
}

static int dsp_forceIdle(lua_State* L) {
    return Internal::checkResult(L, CA::forceIdle((float)lua_tonumber(L, lua_upvalueindex(1))));
}

static int dsp_releaseInputCapture(lua_State* L) {
    return Internal::checkResult(L, CA::releaseInputCapture());
}

static int hlExecCmd(lua_State* L) {
    const auto proc = Check::string(L, 1);

    if (!proc)
        return Internal::configError(L, std::format("exec_cmd: bad argument 1: {}", proc.error()));

    const bool hasRuleArg = !lua_isnoneornil(L, 2);

    lua_pushstring(L, proc->c_str());

    if (hasRuleArg)
        lua_pushvalue(L, 2);
    else
        lua_pushnil(L);

    lua_pushcclosure(L, dsp_execCmd, 2);
    return 1;
}

static int hlExecRaw(lua_State* L) {
    auto proc = Check::string(L, 1);
    if (!proc)
        return Internal::configError(L, std::format("exec_raw: bad argument 1: {}", proc.error()));

    lua_pushstring(L, proc->c_str());
    lua_pushcclosure(L, dsp_execRaw, 1);
    return 1;
}

static int hlExit(lua_State* L) {
    lua_pushcclosure(L, dsp_exit, 0);
    return 1;
}

static int hlSubmap(lua_State* L) {
    auto str = Check::string(L, 1);
    if (!str)
        return Internal::configError(L, std::format("submap: bad argument 1: {}", str.error()));

    lua_pushstring(L, str->c_str());
    lua_pushcclosure(L, dsp_submap, 1);
    return 1;
}

static int hlPass(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.pass: expected a table { window }");

    const auto w = Internal::requireTableFieldWindowSelector(L, 1, "window", "hl.pass");
    lua_pushstring(L, w.c_str());
    lua_pushcclosure(L, dsp_pass, 1);
    return 1;
}

static int hlDpms(lua_State* L) {
    CA::eTogglableAction       action = Internal::tableToggleAction(L, 1);
    std::optional<std::string> monStr;

    if (lua_istable(L, 1))
        monStr = Internal::tableOptMonitorSelector(L, 1, "monitor", "hl.dpms");

    lua_pushnumber(L, (int)action);
    if (monStr)
        lua_pushstring(L, monStr->c_str());
    else
        lua_pushnil(L);
    lua_pushcclosure(L, dsp_dpms, 2);
    return 1;
}

static int hlEvent(lua_State* L) {
    auto str = Check::string(L, 1);
    if (!str)
        return Internal::configError(L, std::format("event: bad argument 1: {}", str.error()));

    lua_pushstring(L, str->c_str());
    lua_pushcclosure(L, dsp_event, 1);
    return 1;
}

static int hlGlobal(lua_State* L) {
    auto str = Check::string(L, 1);
    if (!str)
        return Internal::configError(L, std::format("global: bad argument 1: {}", str.error()));

    lua_pushstring(L, str->c_str());
    lua_pushcclosure(L, dsp_global, 1);
    return 1;
}

static int hlForceRendererReload(lua_State* L) {
    lua_pushcclosure(L, dsp_forceRendererReload, 0);
    return 1;
}

static int hlForceIdle(lua_State* L) {
    auto timeout = Check::number(L, 1);
    if (!timeout)
        return Internal::configError(L, std::format("force_idle: bad argument 1: {}", timeout.error()));

    lua_pushnumber(L, *timeout);
    lua_pushcclosure(L, dsp_forceIdle, 1);
    return 1;
}

static int hlReleaseInputCapture(lua_State* L) {
    lua_pushcclosure(L, dsp_releaseInputCapture, 1);
    return 1;
}

static std::expected<uint32_t, std::string> resolveKeycode(const std::string& key) {
    if (isNumber(key) && std::stoi(key) > 9)
        return (uint32_t)std::stoi(key);

    if (key.starts_with("code:") && isNumber(key.substr(5)))
        return (uint32_t)std::stoi(key.substr(5));

    if (key.starts_with("mouse:") && isNumber(key.substr(6))) {
        uint32_t code = std::stoi(key.substr(6));
        if (code < 272)
            return std::unexpected("invalid mouse button");
        return code;
    }

    const auto KEYSYM = xkb_keysym_from_name(key.c_str(), XKB_KEYSYM_CASE_INSENSITIVE);

    const auto KB = g_pSeatManager->m_keyboard;
    if (!KB)
        return std::unexpected("no keyboard");

    const auto KEYPAIRSTRING = std::format("{}{}", rc<uintptr_t>(KB.get()), key);

    if (g_pKeybindManager->m_keyToCodeCache.contains(KEYPAIRSTRING))
        return g_pKeybindManager->m_keyToCodeCache[KEYPAIRSTRING];

    xkb_keymap*   km          = KB->m_xkbKeymap;
    xkb_state*    ks          = xkb_state_new(km);
    xkb_keycode_t keycode_min = xkb_keymap_min_keycode(km);
    xkb_keycode_t keycode_max = xkb_keymap_max_keycode(km);
    uint32_t      keycode     = 0;

    xkb_state_update_mask(ks, 0, 0, 0, 0, 0, KB->m_modifiersState.group);

    for (xkb_keycode_t kc = keycode_min; kc <= keycode_max; ++kc) {
        xkb_keysym_t sym = xkb_state_key_get_one_sym(ks, kc);
        if (sym == KEYSYM) {
            keycode                                            = kc;
            g_pKeybindManager->m_keyToCodeCache[KEYPAIRSTRING] = keycode;
        }
    }

    xkb_state_unref(ks);

    if (!keycode)
        return std::unexpected("key not found");

    return keycode;
}

static int dsp_sendShortcut(lua_State* L) {
    const uint32_t    modMask = g_pKeybindManager->stringToModMask(lua_tostring(L, lua_upvalueindex(1)));
    const std::string key     = lua_tostring(L, lua_upvalueindex(2));

    auto              keycodeResult = resolveKeycode(key);
    if (!keycodeResult)
        return Internal::dispatcherError(L, std::format("send_shortcut: {}", keycodeResult.error()), ERR, C_INVARG);

    PHLWINDOW window = nullptr;
    if (!lua_isnil(L, lua_upvalueindex(3))) {
        window = Desktop::viewState()->query().selector(lua_tostring(L, lua_upvalueindex(3))).runWindow();
        if (!window)
            return Internal::dispatcherError(L, "send_shortcut: window not found", WARN, C_NOTFOUND);
    }

    if (g_pKeybindManager->m_currentKeybind)
        g_pKeybindManager->m_currentKeybind->releasePending = true;

    return Internal::checkResult(L, CA::pass(modMask, *keycodeResult, window));
}

static int dsp_sendKeyState(lua_State* L) {
    const uint32_t    modMask  = g_pKeybindManager->stringToModMask(lua_tostring(L, lua_upvalueindex(1)));
    const std::string key      = lua_tostring(L, lua_upvalueindex(2));
    const uint32_t    keyState = (uint32_t)lua_tonumber(L, lua_upvalueindex(3));

    auto              keycodeResult = resolveKeycode(key);
    if (!keycodeResult)
        return Internal::dispatcherError(L, std::format("send_key_state: {}", keycodeResult.error()), ERR, C_INVARG);

    PHLWINDOW window = nullptr;
    if (!lua_isnil(L, lua_upvalueindex(4))) {
        window = Desktop::viewState()->query().selector(lua_tostring(L, lua_upvalueindex(4))).runWindow();
        if (!window)
            return Internal::dispatcherError(L, "send_key_state: window not found", WARN, C_NOTFOUND);
    }

    return Internal::checkResult(L, CA::sendKeyState(modMask, *keycodeResult, keyState, window));
}

static int hlSendShortcut(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "send_shortcut: expected a table { mods, key, window? }");

    const auto mods = Internal::requireTableFieldStr(L, 1, "mods", "hl.send_shortcut");
    const auto key  = Internal::requireTableFieldStr(L, 1, "key", "hl.send_shortcut");

    lua_pushstring(L, mods.c_str());
    lua_pushstring(L, key.c_str());
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_sendShortcut, 3);
    return 1;
}

static int hlSendKeyState(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "send_key_state: expected a table { mods, key, state, window? }");

    const auto mods     = Internal::requireTableFieldStr(L, 1, "mods", "hl.send_key_state");
    const auto key      = Internal::requireTableFieldStr(L, 1, "key", "hl.send_key_state");
    const auto stateStr = Internal::requireTableFieldStr(L, 1, "state", "hl.send_key_state");

    uint32_t   keyState = 0;
    if (stateStr == "down")
        keyState = 1;
    else if (stateStr == "repeat")
        keyState = 2;
    else if (stateStr != "up")
        return Internal::configError(L, "send_key_state: 'state' must be \"down\", \"up\", or \"repeat\"");

    lua_pushstring(L, mods.c_str());
    lua_pushstring(L, key.c_str());
    lua_pushnumber(L, keyState);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_sendKeyState, 4);
    return 1;
}

static int dsp_moveToMonitor(lua_State* L) {
    auto mon = Internal::resolveMonitorStr(lua_tostring(L, lua_upvalueindex(1)));
    if (!mon)
        return Internal::dispatcherError(L, "Invalid monitor / monitor doesn't exist", ERR, C_INVARG);

    bool silent = lua_toboolean(L, lua_upvalueindex(2));
    return Internal::checkResult(L, CA::moveToWorkspace(mon->m_activeWorkspace, silent, Internal::windowFromUpval(L, 3)));
}

static int dsp_closeWindow(lua_State* L) {
    return Internal::checkResult(L, CA::closeWindow(Internal::windowFromUpval(L, 1)));
}

static int dsp_killWindow(lua_State* L) {
    return Internal::checkResult(L, CA::killWindow(Internal::windowFromUpval(L, 1)));
}

static int dsp_signalWindow(lua_State* L) {
    return Internal::checkResult(L, CA::signalWindow((int)lua_tonumber(L, lua_upvalueindex(1)), Internal::windowFromUpval(L, 2)));
}

static int dsp_floatWindow(lua_State* L) {
    return Internal::checkResult(L, CA::floatWindow(sc<CA::eTogglableAction>((int)lua_tonumber(L, lua_upvalueindex(1))), Internal::windowFromUpval(L, 2)));
}

static int dsp_fullscreenWindow(lua_State* L) {
    return Internal::checkResult(L,
                                 CA::fullscreenWindow(sc<Fullscreen::eFullscreenMode>((int)lua_tonumber(L, lua_upvalueindex(1))), (bool)lua_toboolean(L, lua_upvalueindex(2)),
                                                      Internal::windowFromUpval(L, 3)));
}

static int dsp_luminophoreOccupyOutput(lua_State* L) {
    const auto action = sc<Luminophore::eOccupyOutputAction>((int)lua_tonumber(L, lua_upvalueindex(1)));
    const auto serial = lua_isnil(L, lua_upvalueindex(2)) ? std::string{} : std::string{lua_tostring(L, lua_upvalueindex(2))};
    return Internal::checkResult(L, CA::occupyOutput(action, serial, Internal::windowFromUpval(L, 3)));
}

static int dsp_luminophorePlacement(lua_State* L) {
    const bool restore = lua_toboolean(L, lua_upvalueindex(1));
    return Internal::checkResult(L, restore ? CA::restoreWindow(Internal::windowFromUpval(L, 2)) : CA::minimizeWindow(Internal::windowFromUpval(L, 2)));
}

static int dsp_luminophoreShellProjection(lua_State* L) {
    const auto                               surfaceNamespace = std::string{lua_tostring(L, lua_upvalueindex(1))};
    const auto                               generation       = std::string{lua_tostring(L, lua_upvalueindex(2))};
    const auto                               revision         = (uint64_t)lua_tonumber(L, lua_upvalueindex(3));
    const auto                               contentRevision  = (uint64_t)lua_tonumber(L, lua_upvalueindex(4));
    const auto                               phase            = sc<Luminophore::eShellProjectionPhase>((int)lua_tonumber(L, lua_upvalueindex(5)));
    const auto                               requestedPlane   = sc<Luminophore::eShellProjectionPlane>((int)lua_tonumber(L, lua_upvalueindex(6)));
    const auto                               role             = sc<Luminophore::eShellWidgetRole>((int)lua_tonumber(L, lua_upvalueindex(7)));
    const Luminophore::SShellProjectionStyle style{
        .color          = CHyprColor((float)lua_tonumber(L, lua_upvalueindex(8)), (float)lua_tonumber(L, lua_upvalueindex(9)), (float)lua_tonumber(L, lua_upvalueindex(10)), 1.F),
        .radius         = (float)lua_tonumber(L, lua_upvalueindex(11)),
        .outline        = (float)lua_tonumber(L, lua_upvalueindex(12)),
        .extent         = (float)lua_tonumber(L, lua_upvalueindex(13)),
        .intensity      = (float)lua_tonumber(L, lua_upvalueindex(14)),
        .phase          = (float)lua_tonumber(L, lua_upvalueindex(15)),
        .revealFrom     = (float)lua_tonumber(L, lua_upvalueindex(16)),
        .revealTo       = (float)lua_tonumber(L, lua_upvalueindex(17)),
        .revealStarted  = lua_tonumber(L, lua_upvalueindex(18)),
        .revealDuration = (float)lua_tonumber(L, lua_upvalueindex(19)),
        .revealOffset   = {(float)lua_tonumber(L, lua_upvalueindex(20)), (float)lua_tonumber(L, lua_upvalueindex(21))},
        .panel          = {(float)lua_tonumber(L, lua_upvalueindex(22)), (float)lua_tonumber(L, lua_upvalueindex(23)), (float)lua_tonumber(L, lua_upvalueindex(24)),
                           (float)lua_tonumber(L, lua_upvalueindex(25))},
        .bloom          = (bool)lua_toboolean(L, lua_upvalueindex(26)),
        .blurPanel      = {(float)lua_tonumber(L, lua_upvalueindex(27)), (float)lua_tonumber(L, lua_upvalueindex(28)), (float)lua_tonumber(L, lua_upvalueindex(29)),
                           (float)lua_tonumber(L, lua_upvalueindex(30))},
    };
    return Internal::checkResult(L, CA::shellProjection(surfaceNamespace, generation, revision, contentRevision, phase, requestedPlane, role, style));
}

static int dsp_fullscreenWindowWithAction(lua_State* L) {
    const auto mode        = sc<Fullscreen::eFullscreenMode>((int)lua_tonumber(L, lua_upvalueindex(1)));
    bool       layoutAware = lua_toboolean(L, lua_upvalueindex(2));
    const int  actionRaw   = (int)lua_tonumber(L, lua_upvalueindex(3));
    auto       maybeW      = Internal::windowFromUpval(L, 4);
    if (actionRaw == 0)
        return Internal::checkResult(L, CA::fullscreenWindow(mode, layoutAware, maybeW));

    const auto target = maybeW.value_or(Desktop::focusState()->window());
    if (!target)
        return Internal::dispatcherError(L, "hl.window.fullscreen: no target", WARN, C_NOTARGET);

    const bool currentlyMode = Fullscreen::controller()->isFullscreen(target, mode);

    if (actionRaw == 1) {
        if (!currentlyMode)
            return Internal::checkResult(L, CA::fullscreenWindow(mode, layoutAware, maybeW));
        return Internal::pushSuccessResult(L);
    }

    if (actionRaw == 2) {
        if (currentlyMode)
            return Internal::checkResult(L, CA::fullscreenWindow(mode, layoutAware, maybeW));
        return Internal::pushSuccessResult(L);
    }

    return Internal::dispatcherError(L, "hl.window.fullscreen: invalid action", ERR, C_INVARG);
}

static int dsp_fullscreenState(lua_State* L) {
    const auto desiredInternal = sc<Fullscreen::eFullscreenMode>((int)lua_tonumber(L, lua_upvalueindex(1)));
    const auto desiredClient   = sc<Fullscreen::eFullscreenMode>((int)lua_tonumber(L, lua_upvalueindex(2)));
    const int  actionRaw       = (int)lua_tonumber(L, lua_upvalueindex(3)); // 0: toggle, 1: set, 2: unset
    bool       layoutAware     = lua_toboolean(L, lua_upvalueindex(4));
    auto       maybeW          = Internal::windowFromUpval(L, 5);

    const auto target = maybeW.value_or(Desktop::focusState()->window());
    if (!target)
        return Internal::pushSuccessResult(L);

    const auto CURRENT        = Fullscreen::controller()->getFullscreenModes(target);
    const bool atDesiredState = CURRENT.internal == desiredInternal && CURRENT.client == desiredClient;

    if (actionRaw == 0)
        return Internal::checkResult(L,
                                     CA::fullscreenWindow(CURRENT.internal == desiredInternal ? Fullscreen::FSMODE_NONE : desiredInternal,
                                                          CURRENT.client == desiredClient ? Fullscreen::FSMODE_NONE : desiredClient, layoutAware, maybeW));

    if (actionRaw == 1) {
        if (!atDesiredState)
            return Internal::checkResult(L, CA::fullscreenWindow(desiredInternal, desiredClient, layoutAware, maybeW));
        return Internal::pushSuccessResult(L);
    }

    if (actionRaw == 2) {
        if (atDesiredState)
            return Internal::checkResult(L, CA::fullscreenWindow(desiredInternal, desiredClient, layoutAware, maybeW));
        return Internal::pushSuccessResult(L);
    }

    return Internal::dispatcherError(L, "hl.window.fullscreen_state: invalid action", ERR, C_INVARG);
}

static int dsp_pseudoWindow(lua_State* L) {
    return Internal::checkResult(L, CA::pseudoWindow(sc<CA::eTogglableAction>((int)lua_tonumber(L, lua_upvalueindex(1))), Internal::windowFromUpval(L, 2)));
}

static int dsp_moveInDirection(lua_State* L) {
    return Internal::checkResult(L, CA::moveInDirection(sc<Math::eDirection>((int)lua_tonumber(L, lua_upvalueindex(1))), Internal::windowFromUpval(L, 2)));
}

static int dsp_swapInDirection(lua_State* L) {
    return Internal::checkResult(L, CA::swapInDirection(sc<Math::eDirection>((int)lua_tonumber(L, lua_upvalueindex(1))), Internal::windowFromUpval(L, 2)));
}

static int dsp_center(lua_State* L) {
    return Internal::checkResult(L, CA::center(Internal::windowFromUpval(L, 1)));
}

static int dsp_cycleNext(lua_State* L) {
    bool                next        = lua_toboolean(L, lua_upvalueindex(1));
    int                 tiledRaw    = (int)lua_tonumber(L, lua_upvalueindex(2));
    int                 floatingRaw = (int)lua_tonumber(L, lua_upvalueindex(3));
    std::optional<bool> tiled       = tiledRaw < 0 ? std::nullopt : std::optional(tiledRaw > 0);
    std::optional<bool> floating    = floatingRaw < 0 ? std::nullopt : std::optional(floatingRaw > 0);
    return Internal::checkResult(L, CA::cycleNext(next, tiled, floating, Internal::windowFromUpval(L, 4)));
}

static int dsp_swapNext(lua_State* L) {
    return Internal::checkResult(L, CA::swapNext(lua_toboolean(L, lua_upvalueindex(1)), Internal::windowFromUpval(L, 2)));
}

static int dsp_swapWithWindow(lua_State* L) {
    auto       source = Internal::windowFromUpval(L, 1);

    const auto targetSelector = lua_tostring(L, lua_upvalueindex(2));
    const auto target         = Desktop::viewState()->query().selector(targetSelector).runWindow();
    if (!target)
        return Internal::dispatcherError(L, "hl.window.swap: target window not found", WARN, C_NOTFOUND);

    return Internal::checkResult(L, CA::swapWith(target, source));
}

static int dsp_tagWindow(lua_State* L) {
    return Internal::checkResult(L, CA::tag(lua_tostring(L, lua_upvalueindex(1)), Internal::windowFromUpval(L, 2)));
}

static int dsp_clearTags(lua_State* L) {
    return Internal::checkResult(L, CA::clearTags(Internal::windowFromUpval(L, 1)));
}

static int dsp_toggleSwallow(lua_State* L) {
    return Internal::checkResult(L, CA::toggleSwallow());
}

static int dsp_resize(lua_State* L) {
    Vector2D value{lua_tonumber(L, lua_upvalueindex(1)), lua_tonumber(L, lua_upvalueindex(2))};
    return Internal::checkResult(L, CA::resize(value, lua_toboolean(L, lua_upvalueindex(3)), Internal::windowFromUpval(L, 4)));
}

static int dsp_move(lua_State* L) {
    Vector2D value{lua_tonumber(L, lua_upvalueindex(1)), lua_tonumber(L, lua_upvalueindex(2))};
    return Internal::checkResult(L, CA::move(value, lua_toboolean(L, lua_upvalueindex(3)), Internal::windowFromUpval(L, 4)));
}

static int dsp_pinWindow(lua_State* L) {
    return Internal::checkResult(L, CA::pinWindow(sc<CA::eTogglableAction>((int)lua_tonumber(L, lua_upvalueindex(1))), Internal::windowFromUpval(L, 2)));
}

static int dsp_bringToTop(lua_State* L) {
    return Internal::checkResult(L, CA::alterZOrder("top"));
}

static int dsp_alterZOrder(lua_State* L) {
    return Internal::checkResult(L, CA::alterZOrder(lua_tostring(L, lua_upvalueindex(1)), Internal::windowFromUpval(L, 2)));
}

static int dsp_setProp(lua_State* L) {
    return Internal::checkResult(L, CA::setProp(lua_tostring(L, lua_upvalueindex(1)), lua_tostring(L, lua_upvalueindex(2)), Internal::windowFromUpval(L, 3)));
}

static int dsp_luminophoreSpatial(lua_State* L) {
    const auto action = sc<Luminophore::eSpatialAction>((int)lua_tonumber(L, lua_upvalueindex(1)));
    const auto dir    = sc<Math::eDirection>((int)lua_tonumber(L, lua_upvalueindex(2)));
    switch (action) {
        case Luminophore::eSpatialAction::MOVE_VIEW: return Internal::checkResult(L, CA::spatialMoveView(dir));
        case Luminophore::eSpatialAction::ADJUST_VIEW: return Internal::checkResult(L, CA::spatialAdjustView(dir, Internal::windowFromUpval(L, 3)));
        case Luminophore::eSpatialAction::MOVE_WINDOW: return Internal::checkResult(L, CA::spatialMoveWindow(dir, Internal::windowFromUpval(L, 3)));
        case Luminophore::eSpatialAction::FOCUS_DIRECTION: return Internal::checkResult(L, CA::spatialFocusDirection(dir, Internal::windowFromUpval(L, 3)));
        case Luminophore::eSpatialAction::TOGGLE_DESKTOP: return Internal::checkResult(L, CA::spatialToggleDesktop());
        case Luminophore::eSpatialAction::TOGGLE_WIDE: return Internal::checkResult(L, CA::spatialToggleWide(Internal::windowFromUpval(L, 3)));
    }
    return Internal::dispatcherError(L, "Unknown LUMINOPHORE spatial action", ERR, C_INVARG);
}

static int dsp_mouseDrag(lua_State* L) {
    if (g_pKeybindManager->m_currentKeybind)
        g_pKeybindManager->m_currentKeybind->releasePending = true;

    return Internal::checkResult(L, CA::mouse("movewindow"));
}

static int dsp_mouseResize(lua_State* L) {
    if (g_pKeybindManager->m_currentKeybind)
        g_pKeybindManager->m_currentKeybind->releasePending = true;

    auto keepAspectRatio = Check::string(L, lua_upvalueindex(1));
    if (!keepAspectRatio)
        return Internal::configError(L, std::format("resize: bad argument 1: {}", keepAspectRatio.error()));

    return Internal::checkResult(L, CA::mouse("resizewindow " + *keepAspectRatio));
}

static int hlWindowClose(lua_State* L) {
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_closeWindow, 1);
    return 1;
}

static int hlWindowKill(lua_State* L) {
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_killWindow, 1);
    return 1;
}

static int hlWindowSignal(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.signal: expected a table { signal, window? }");

    lua_pushnumber(L, Internal::requireTableFieldNum(L, 1, "signal", "hl.window.signal"));
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_signalWindow, 2);
    return 1;
}

static int hlWindowFloat(lua_State* L) {
    const auto action = Internal::tableToggleAction(L, 1);

    lua_pushnumber(L, (int)action);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_floatWindow, 2);
    return 1;
}

static int hlWindowFullscreen(lua_State* L) {
    Fullscreen::eFullscreenMode mode        = Fullscreen::FSMODE_FULLSCREEN;
    int                         action      = 0; // 0: toggle, 1: set, 2: unset
    bool                        layoutAware = true;
    if (lua_istable(L, 1)) {
        auto m = Internal::tableOptStr(L, 1, "mode");
        if (m) {
            if (*m == "maximized" || *m == "1")
                mode = Fullscreen::FSMODE_MAXIMIZED;
            else if (*m == "fullscreen" || *m == "0")
                mode = Fullscreen::FSMODE_FULLSCREEN;
            else
                return Internal::configError(L, "hl.window.fullscreen: invalid mode \"{}\" (expected fullscreen/maximized)", *m);
        }

        auto a = Internal::tableOptStr(L, 1, "action");
        if (a) {
            if (*a == "toggle")
                action = 0;
            else if (*a == "set")
                action = 1;
            else if (*a == "unset")
                action = 2;
            else
                return Internal::configError(L, "hl.window.fullscreen: invalid action \"{}\" (expected toggle/set/unset)", *a);
        }

        auto la = Internal::tableOptBool(L, 1, "layout_aware");
        if (la) {
            if (*la)
                layoutAware = true;
            else if (!*la)
                layoutAware = false;
            else
                return Internal::configError(L, "hl.window.fullscreen: invalid action \"{}\" (expected true/false)", *la);
        }
    }
    lua_pushnumber(L, (int)mode); // 1

    lua_pushboolean(L, layoutAware); // 2

    if (action == 0) {
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_fullscreenWindow, 3);
    } else {
        lua_pushnumber(L, action);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_fullscreenWindowWithAction, 4);
    }
    return 1;
}

static int hlLuminophoreOccupyOutput(lua_State* L) {
    Luminophore::eOccupyOutputAction action = Luminophore::OCCUPY_OUTPUT_TOGGLE;
    std::optional<std::string>       serial;

    if (lua_istable(L, 1)) {
        if (auto a = Internal::tableOptStr(L, 1, "action"); a) {
            if (*a == "toggle")
                action = Luminophore::OCCUPY_OUTPUT_TOGGLE;
            else if (*a == "set")
                action = Luminophore::OCCUPY_OUTPUT_SET;
            else if (*a == "unset")
                action = Luminophore::OCCUPY_OUTPUT_UNSET;
            else
                return Internal::configError(L, "hl.luminophore.occupy_output: invalid action (expected toggle/set/unset)");
        }
        serial = Internal::tableOptStr(L, 1, "serial");
    }

    lua_pushnumber(L, (int)action);
    if (serial)
        lua_pushstring(L, serial->c_str());
    else
        lua_pushnil(L);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_luminophoreOccupyOutput, 3);
    return 1;
}

static int hlLuminophorePlacement(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.luminophore.placement: expected a table");
    const auto action = Internal::requireTableFieldStr(L, 1, "action", "hl.luminophore.placement");
    if (action != "minimize" && action != "restore")
        return Internal::configError(L, "hl.luminophore.placement: invalid action (expected minimize/restore)");

    lua_pushboolean(L, action == "restore");
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_luminophorePlacement, 2);
    return 1;
}

static int hlLuminophoreShellProjection(lua_State* L) {
    luaL_checkstack(L, 32, "LUMINOPHORE Shell projection fields");
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.luminophore.shell_projection: expected a table");

    const auto version = Internal::requireTableFieldNum(L, 1, "version", "hl.luminophore.shell_projection");
    if (version != 4)
        return Internal::configError(L, "hl.luminophore.shell_projection: unsupported protocol version");
    const auto                         surfaceNamespace = Internal::requireTableFieldStr(L, 1, "surface", "hl.luminophore.shell_projection");
    const auto                         generation       = Internal::requireTableFieldStr(L, 1, "generation", "hl.luminophore.shell_projection");
    const auto                         revision         = Internal::requireTableFieldNum(L, 1, "revision", "hl.luminophore.shell_projection");
    const auto                         contentRevision  = Internal::requireTableFieldNum(L, 1, "content_revision", "hl.luminophore.shell_projection");
    const auto                         phaseName        = Internal::requireTableFieldStr(L, 1, "phase", "hl.luminophore.shell_projection");
    const auto                         planeName        = Internal::requireTableFieldStr(L, 1, "requested_plane", "hl.luminophore.shell_projection");
    const auto                         roleName         = Internal::requireTableFieldStr(L, 1, "role", "hl.luminophore.shell_projection");
    const auto                         red              = Internal::tableOptNum(L, 1, "red").value_or(0.612);
    const auto                         green            = Internal::tableOptNum(L, 1, "green").value_or(0.796);
    const auto                         blue             = Internal::tableOptNum(L, 1, "blue").value_or(0.984);
    const auto                         radius           = Internal::tableOptNum(L, 1, "radius").value_or(14.0);
    const auto                         outline          = Internal::tableOptNum(L, 1, "outline").value_or(2.0);
    const auto                         extent           = Internal::tableOptNum(L, 1, "extent").value_or(64.0);
    const auto                         intensity        = Internal::tableOptNum(L, 1, "intensity").value_or(1.0);
    const auto                         glowPhase        = Internal::tableOptNum(L, 1, "glow_phase").value_or(0.0);
    const auto                         revealFrom       = Internal::tableOptNum(L, 1, "reveal_from").value_or(1.0);
    const auto                         revealTo         = Internal::tableOptNum(L, 1, "reveal_to").value_or(1.0);
    const auto                         revealStarted    = Internal::tableOptNum(L, 1, "reveal_started").value_or(0.0);
    const auto                         revealDuration   = Internal::tableOptNum(L, 1, "reveal_duration").value_or(0.0);
    const auto                         revealOffsetX    = Internal::tableOptNum(L, 1, "reveal_offset_x").value_or(0.0);
    const auto                         revealOffsetY    = Internal::tableOptNum(L, 1, "reveal_offset_y").value_or(0.0);
    const auto                         panelX           = Internal::tableOptNum(L, 1, "panel_x").value_or(-1.0);
    const auto                         panelY           = Internal::tableOptNum(L, 1, "panel_y").value_or(-1.0);
    const auto                         panelWidth       = Internal::tableOptNum(L, 1, "panel_width").value_or(0.0);
    const auto                         panelHeight      = Internal::tableOptNum(L, 1, "panel_height").value_or(0.0);
    const auto                         bloom            = Internal::tableOptBool(L, 1, "bloom").value_or(false);
    Luminophore::eShellProjectionPhase phase;
    Luminophore::eShellProjectionPlane requestedPlane;
    Luminophore::eShellWidgetRole      role;

    constexpr double                   MAX_EXACT_LUA_INTEGER = 9007199254740991.0;
    if (!std::isfinite(revision) || revision < 1.0 || revision > MAX_EXACT_LUA_INTEGER || std::trunc(revision) != revision)
        return Internal::configError(L, "hl.luminophore.shell_projection: revision must be a positive integer");
    if (!std::isfinite(contentRevision) || contentRevision < 0.0 || contentRevision > MAX_EXACT_LUA_INTEGER || std::trunc(contentRevision) != contentRevision)
        return Internal::configError(L, "hl.luminophore.shell_projection: content_revision must be a non-negative integer");

    if (phaseName == "prepare")
        phase = Luminophore::SHELL_PROJECTION_PREPARE;
    else if (phaseName == "commit")
        phase = Luminophore::SHELL_PROJECTION_COMMIT;
    else if (phaseName == "abort")
        phase = Luminophore::SHELL_PROJECTION_ABORT;
    else
        return Internal::configError(L, "hl.luminophore.shell_projection: invalid phase (expected prepare/commit/abort)");

    if (planeName == "bottom")
        requestedPlane = Luminophore::SHELL_PROJECTION_BOTTOM;
    else if (planeName == "overlay")
        requestedPlane = Luminophore::SHELL_PROJECTION_OVERLAY;
    else
        return Internal::configError(L, "hl.luminophore.shell_projection: invalid requested_plane (expected bottom/overlay)");

    if (roleName == "passive")
        role = Luminophore::SHELL_WIDGET_PASSIVE;
    else if (roleName == "launcher-drop" || roleName == "occupy-output-drop")
        return Internal::configError(L, "hl.luminophore.shell_projection: drop roles are retired; use passive");
    else if (roleName == "osd")
        role = Luminophore::SHELL_WIDGET_OSD;
    else
        return Internal::configError(L, "hl.luminophore.shell_projection: invalid role");

    lua_pushstring(L, surfaceNamespace.c_str());
    lua_pushstring(L, generation.c_str());
    lua_pushnumber(L, revision);
    lua_pushnumber(L, contentRevision);
    lua_pushnumber(L, (int)phase);
    lua_pushnumber(L, requestedPlane);
    lua_pushnumber(L, role);
    lua_pushnumber(L, red);
    lua_pushnumber(L, green);
    lua_pushnumber(L, blue);
    lua_pushnumber(L, radius);
    lua_pushnumber(L, outline);
    lua_pushnumber(L, extent);
    lua_pushnumber(L, intensity);
    lua_pushnumber(L, glowPhase);
    lua_pushnumber(L, revealFrom);
    lua_pushnumber(L, revealTo);
    lua_pushnumber(L, revealStarted);
    lua_pushnumber(L, revealDuration);
    lua_pushnumber(L, revealOffsetX);
    lua_pushnumber(L, revealOffsetY);
    lua_pushnumber(L, panelX);
    lua_pushnumber(L, panelY);
    lua_pushnumber(L, panelWidth);
    lua_pushnumber(L, panelHeight);
    lua_pushboolean(L, bloom);
    lua_pushnumber(L, Internal::tableOptNum(L, 1, "blur_x").value_or(0.0));
    lua_pushnumber(L, Internal::tableOptNum(L, 1, "blur_y").value_or(0.0));
    lua_pushnumber(L, Internal::tableOptNum(L, 1, "blur_width").value_or(0.0));
    lua_pushnumber(L, Internal::tableOptNum(L, 1, "blur_height").value_or(0.0));
    lua_pushcclosure(L, dsp_luminophoreShellProjection, 30);
    return 1;
}

static int hlWindowFullscreenState(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.fullscreen_state: expected a table { internal, client, action?, window? }");

    int action = 1; // default to set semantics
    if (auto a = Internal::tableOptStr(L, 1, "action"); a) {
        if (*a == "toggle")
            action = 0;
        else if (*a == "set")
            action = 1;
        else if (*a == "unset")
            action = 2;
        else
            return Internal::configError(L, "hl.window.fullscreen_state: invalid action \"{}\" (expected toggle/set/unset)", *a);
    }

    auto im = Internal::tableOptNum(L, 1, "internal");
    auto cm = Internal::tableOptNum(L, 1, "client");
    if (!im || !cm)
        return Internal::configError(L, "hl.window.fullscreen_state: 'internal' and 'client' are required");

    auto ls          = Internal::tableOptBool(L, 1, "layout_aware");
    bool layoutAware = ls ? *ls : true;

    lua_pushnumber(L, (int)*im);
    lua_pushnumber(L, (int)*cm);
    lua_pushnumber(L, action);
    lua_pushboolean(L, layoutAware);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_fullscreenState, 5);
    return 1;
}

static int hlWindowPseudo(lua_State* L) {
    const auto action = Internal::tableToggleAction(L, 1);

    lua_pushnumber(L, (int)action);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_pseudoWindow, 2);
    return 1;
}

static int hlWindowMove(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.move: expected a table, e.g. { direction = \"left\" }");

    for (const auto key : {"group_aware", "into_group", "into_or_create_group", "out_of_group"}) {
        lua_getfield(L, 1, key);
        const bool retired = !lua_isnil(L, -1);
        lua_pop(L, 1);
        if (retired)
            return Internal::configError(L, "Window grouping has been removed");
    }

    lua_getfield(L, 1, "workspace");
    const bool retiredWorkspace = !lua_isnil(L, -1);
    lua_pop(L, 1);
    if (retiredWorkspace)
        return Internal::configError(L, "Workspace navigation was removed; use Luminophore spatial actions");

    auto dirStr = Internal::tableOptStr(L, 1, "direction");
    if (dirStr) {
        auto dir = Internal::parseDirectionStr(*dirStr);
        if (dir == Math::DIRECTION_DEFAULT)
            return Internal::configError(L, "hl.window.move: invalid direction \"{}\" (expected left/right/up/down)", *dirStr);

        lua_pushnumber(L, (int)dir);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_moveInDirection, 2);
        return 1;
    }

    auto x = Internal::tableOptNum(L, 1, "x");
    auto y = Internal::tableOptNum(L, 1, "y");
    if (x && y) {
        bool relative = Internal::tableOptBool(L, 1, "relative").value_or(false);
        lua_pushnumber(L, *x);
        lua_pushnumber(L, *y);
        lua_pushboolean(L, relative);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_move, 4);
        return 1;
    }

    auto mon = Internal::tableOptMonitorSelector(L, 1, "monitor", "hl.window.move");
    if (mon) {
        auto follow = Internal::tableOptBool(L, 1, "follow");
        bool silent = follow.has_value() && !*follow;
        lua_pushstring(L, mon->c_str());
        lua_pushboolean(L, silent);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_moveToMonitor, 3);
        return 1;
    }

    return Internal::configError(L, "hl.window.move: unrecognized arguments. Expected one of: direction, x+y(+relative), monitor");
}

// The public API is a typed table. Capture a validated, immutable wire payload
// so later changes to the caller's table cannot retarget a pending dispatcher.
static int dsp_luminophoreLivePip(lua_State* L) {
    const auto command = Luminophore::CLuminophoreLivePipProtocol::parse(lua_tostring(L, lua_upvalueindex(1)));
    if (!command)
        return Internal::configError(L, "Invalid PiP request");
    const auto result = Luminophore::livePipProtocol()->execute(*command);
    lua_pushlstring(L, result.data(), result.size());
    return 1;
}
static int hlLuminophoreLivePip(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "PiP expects a table");
    std::string request, action;
    const auto  token = [&](const char* field) {
        lua_getfield(L, 1, field);
        if (lua_type(L, -1) != LUA_TSTRING) {
            lua_pop(L, 1);
            return false;
        }
        size_t            size  = 0;
        const char*       value = lua_tolstring(L, -1, &size);
        const std::string word(value, size);
        lua_pop(L, 1);
        if (word.empty() || word.size() > 256 || word.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:") != std::string::npos)
            return false;
        if (!request.empty())
            request += ' ';
        request += word;
        if (std::string_view(field) == "action")
            action = word;
        return true;
    };
    const auto number = [&](const char* field) {
        lua_getfield(L, 1, field);
        const bool valid = lua_type(L, -1) == LUA_TNUMBER;
        const auto value = lua_tonumber(L, -1);
        lua_pop(L, 1);
        if (!valid || !std::isfinite(value))
            return false;
        request += std::format(" {}", value);
        return true;
    };
    bool valid = token("action") && token("request") && token("instance");
    if (valid && (action == "remove" || action == "place"))
        valid = token("revision");
    if (valid && (action == "create" || action == "place"))
        valid = token("output");
    if (valid && (action == "resolve" || action == "create" || action == "place"))
        valid = number("x") && number("y") && number("width") && number("height");
    if (valid && action == "create")
        valid = number("margin");
    if (!valid || !Luminophore::CLuminophoreLivePipProtocol::parse(request))
        return Internal::configError(L, "Invalid typed PiP fields");
    lua_pushlstring(L, request.data(), request.size());
    lua_pushcclosure(L, dsp_luminophoreLivePip, 1);
    return 1;
}

static int dsp_luminophoreEditor(lua_State* L) {
    const auto command = CLuminophoreSpatialEditorProtocol::parsePreview(lua_tostring(L, lua_upvalueindex(1)));
    if (!command)
        return Internal::dispatcherError(L, "Invalid captured spatial edit", ERR, C_INVARG);
    const auto result   = Luminophore::spatialRuntime()->edit(*command, false);
    const bool accepted = result.status == eLuminophoreSpatialTransactionStatus::APPLIED || result.status == eLuminophoreSpatialTransactionStatus::NO_CHANGE;
    lua_newtable(L);
    lua_pushboolean(L, accepted);
    lua_setfield(L, -2, "ok");
    lua_pushstring(L, CLuminophoreSpatialEditorProtocol::statusName(result.status).c_str());
    lua_setfield(L, -2, "status");
    lua_pushstring(L, CLuminophoreSpatialEditorProtocol::serialize(result).c_str());
    lua_setfield(L, -2, "result_json");
    return 1;
}

static int hlLuminophoreEditor(lua_State* L, std::string_view action) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "LUMINOPHORE editor action requires a table");
    std::string request{action};
    const auto  appendString = [&](const char* field) {
        lua_getfield(L, 1, field);
        if (lua_type(L, -1) != LUA_TSTRING) {
            lua_pop(L, 1);
            return false;
        }
        size_t                 length = 0;
        const auto             value  = lua_tolstring(L, -1, &length);
        const std::string_view token{value, length};
        // Exact decimal/hex identifiers must not pass through Lua doubles.
        const bool valid = !token.empty() && token.find_first_not_of("0123456789abcdefABCDEFx") == std::string_view::npos;
        if (valid) {
            request += ' ';
            request.append(token);
        }
        lua_pop(L, 1);
        return valid;
    };
    const auto appendCoordinate = [&](const char* field) {
        lua_getfield(L, 1, field);
        const bool valid = lua_isinteger(L, -1);
        const auto value = lua_tointeger(L, -1);
        lua_pop(L, 1);
        if (!valid)
            return false;
        request += std::format(" {}", value);
        return true;
    };
    if (!appendString("expected_revision") || !appendString("topology_revision") || !appendString("output") || (action == "move-window" && !appendString("window")) ||
        !appendCoordinate("x") || !appendCoordinate("y") || (action == "resize-view" && (!appendCoordinate("columns") || !appendCoordinate("rows"))) ||
        !CLuminophoreSpatialEditorProtocol::parsePreview(request))
        return Internal::configError(L, "Invalid LUMINOPHORE editor fields: exact string revisions/output/window and integer coordinates required");
    lua_pushlstring(L, request.data(), request.size());
    lua_pushcclosure(L, dsp_luminophoreEditor, 1);
    return 1;
}

static int dsp_luminophoreHistory(lua_State* L) {
    const bool              redo     = lua_toboolean(L, lua_upvalueindex(1));
    const std::string       request  = lua_tostring(L, lua_upvalueindex(2));
    const std::string       revision = lua_tostring(L, lua_upvalueindex(3));
    const std::string       source   = lua_tostring(L, lua_upvalueindex(4));
    std::optional<uint64_t> expected;
    if (!revision.empty()) {
        uint64_t   value  = 0;
        const auto parsed = std::from_chars(revision.data(), revision.data() + revision.size(), value);
        if (parsed.ec != std::errc{} || parsed.ptr != revision.data() + revision.size())
            return Internal::configError(L, "Invalid spatial history revision");
        expected = value;
    }
    const auto  result   = Luminophore::spatialRuntime()->historyRequest(redo, expected, request, source);
    const char* status   = result == eLuminophoreHistoryResult::APPLIED ? "applied" :
        result == eLuminophoreHistoryResult::EMPTY                      ? "empty" :
        result == eLuminophoreHistoryResult::BUSY                       ? "busy" :
        result == eLuminophoreHistoryResult::STALE                      ? "stale" :
        result == eLuminophoreHistoryResult::UNAVAILABLE                ? "unavailable" :
        result == eLuminophoreHistoryResult::RECOVERY_FAILED            ? "recovery-failed" :
                                                                          "commit-failed";
    const auto  response = std::format(R"({{"action":"{}","status":"{}","undo":{},"redo":{},"revision":{},"source":"{}"}})", redo ? "redo" : "undo", status,
                                       Luminophore::spatialRuntime()->undoCount(), Luminophore::spatialRuntime()->redoCount(), Luminophore::spatialRuntime()->revision(),
                                       escapeJSONStrings(g_pCompositor->m_instanceSignature));
    if (g_pEventManager)
        g_pEventManager->postEvent(SHyprIPCEvent{"luminophorespatialhistory", response});
    lua_pushlstring(L, response.data(), response.size());
    return 1;
}
static int hlLuminophoreHistory(lua_State* L, bool redo) {
    std::string request, revision, source;
    if (!lua_isnoneornil(L, 1)) {
        if (!lua_istable(L, 1))
            return Internal::configError(L, "Spatial history expects a table or no arguments");
        lua_getfield(L, 1, "request_id");
        if (lua_type(L, -1) != LUA_TSTRING)
            return Internal::configError(L, "Spatial history requires request_id and expected_revision strings");
        size_t     size = 0;
        const auto data = lua_tolstring(L, -1, &size);
        request.assign(data, size);
        lua_pop(L, 1);
        lua_getfield(L, 1, "expected_revision");
        if (lua_type(L, -1) != LUA_TSTRING)
            return Internal::configError(L, "Spatial history requires expected_revision string");
        const auto rev = lua_tolstring(L, -1, &size);
        revision.assign(rev, size);
        lua_pop(L, 1);
        lua_getfield(L, 1, "source");
        if (lua_type(L, -1) != LUA_TSTRING)
            return Internal::configError(L, "Spatial history requires compositor source identity");
        const auto instance = lua_tolstring(L, -1, &size);
        source.assign(instance, size);
        lua_pop(L, 1);
        uint64_t   value  = 0;
        const auto parsed = std::from_chars(revision.data(), revision.data() + revision.size(), value);
        if (source.empty() || source.size() > 256 || source.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != std::string::npos ||
            request.empty() || request.size() > 96 || request.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != std::string::npos ||
            revision.empty() || parsed.ec != std::errc{} || parsed.ptr != revision.data() + revision.size())
            return Internal::configError(L, "Invalid spatial history request identity or revision");
    }
    lua_pushboolean(L, redo);
    lua_pushlstring(L, request.data(), request.size());
    lua_pushlstring(L, revision.data(), revision.size());
    lua_pushlstring(L, source.data(), source.size());
    lua_pushcclosure(L, dsp_luminophoreHistory, 4);
    return 1;
}
static int hlLuminophoreUndo(lua_State* L) {
    return hlLuminophoreHistory(L, false);
}
static int hlLuminophoreRedo(lua_State* L) {
    return hlLuminophoreHistory(L, true);
}

static int hlLuminophoreMoveWindowTo(lua_State* L) {
    return hlLuminophoreEditor(L, "move-window");
}

static int hlLuminophoreMoveOutputViewTo(lua_State* L) {
    return hlLuminophoreEditor(L, "move-view");
}

static int hlLuminophoreResizeOutputView(lua_State* L) {
    return hlLuminophoreEditor(L, "resize-view");
}

static constexpr const char*   LUMINOPHORE_GRAB_LAYOUT_MT = "LUMINOPHORE.GrabLayout";

static std::optional<uint64_t> luminophoreExactID(lua_State* L, int table, const char* field) {
    lua_getfield(L, table, field);
    if (lua_type(L, -1) != LUA_TSTRING) {
        lua_pop(L, 1);
        return std::nullopt;
    }
    size_t     length       = 0;
    const auto token        = lua_tolstring(L, -1, &length);
    uint64_t   value        = 0;
    const auto [end, error] = std::from_chars(token, token + length, value);
    const bool valid        = length > 0 && error == std::errc{} && end == token + length;
    lua_pop(L, 1);
    return valid ? std::optional{value} : std::nullopt;
}

static std::optional<double> luminophoreCellNumber(lua_State* L, const char* field) {
    lua_getfield(L, -1, field);
    const bool   numeric = lua_type(L, -1) == LUA_TNUMBER;
    const double value   = lua_tonumber(L, -1);
    lua_pop(L, 1);
    return numeric && std::isfinite(value) ? std::optional{value} : std::nullopt;
}

static std::optional<Luminophore::SSpatialGrabLayoutRegistration> luminophoreGrabLayout(lua_State* L) {
    if (!lua_istable(L, 1))
        return std::nullopt;
    const auto generation    = luminophoreExactID(L, 1, "generation");
    const auto revision      = luminophoreExactID(L, 1, "expected_revision");
    const auto topology      = luminophoreExactID(L, 1, "topology_revision");
    const auto frameRevision = luminophoreExactID(L, 1, "frame_revision");
    if (!generation || !*generation || !revision || !topology || !frameRevision || !*frameRevision)
        return std::nullopt;
    lua_getfield(L, 1, "frame_generation");
    if (lua_type(L, -1) != LUA_TSTRING) {
        lua_pop(L, 1);
        return std::nullopt;
    }
    size_t                                      length = 0;
    const auto                                  token  = lua_tolstring(L, -1, &length);
    Luminophore::SSpatialGrabLayoutRegistration request{.generation       = *generation,
                                                        .revision         = *revision,
                                                        .topologyRevision = *topology,
                                                        .frameGeneration  = std::string{token, length},
                                                        .frameRevision    = *frameRevision,
                                                        .targetEpoch      = luminophoreExactID(L, 1, "target_epoch").value_or(0)};
    lua_pop(L, 1);
    if (request.frameGeneration.empty() || request.frameGeneration.size() > 256)
        return std::nullopt;
    lua_getfield(L, 1, "cells");
    if (!lua_istable(L, -1) || lua_rawlen(L, -1) > 4096) {
        lua_pop(L, 1);
        return std::nullopt;
    }
    const auto count = lua_rawlen(L, -1);
    for (size_t index = 1; index <= count; ++index) {
        lua_rawgeti(L, -1, index);
        if (!lua_istable(L, -1)) {
            lua_pop(L, 2);
            return std::nullopt;
        }
        const auto coordinate = [&](const char* field) -> std::optional<int64_t> {
            lua_getfield(L, -1, field);
            const bool valid = lua_isinteger(L, -1);
            const auto value = lua_tointeger(L, -1);
            lua_pop(L, 1);
            return valid ? std::optional<int64_t>{value} : std::nullopt;
        };
        const auto column = coordinate("column"), row = coordinate("row");
        const auto cellOutput = luminophoreExactID(L, -1, "output").value_or(0);
        const auto x = luminophoreCellNumber(L, "x"), y = luminophoreCellNumber(L, "y"), width = luminophoreCellNumber(L, "width"), height = luminophoreCellNumber(L, "height");
        lua_pop(L, 1);
        if (!column || !row || !x || !y || !width || !height) {
            lua_pop(L, 1);
            return std::nullopt;
        }
        request.cells.emplace_back(SLuminophoreEditorCell{.point = {*column, *row}, .x = *x, .y = *y, .width = *width, .height = *height, .outputID = cellOutput});
    }
    lua_pop(L, 1);
    return request;
}

static int luminophoreGrabLayoutGc(lua_State* L) {
    auto* request = static_cast<Luminophore::SSpatialGrabLayoutRegistration*>(luaL_checkudata(L, 1, LUMINOPHORE_GRAB_LAYOUT_MT));
    request->~SSpatialGrabLayoutRegistration();
    return 0;
}

static int dsp_luminophoreGrabLayout(lua_State* L) {
    const auto* request  = static_cast<Luminophore::SSpatialGrabLayoutRegistration*>(lua_touserdata(L, lua_upvalueindex(1)));
    const bool  accepted = Luminophore::spatialGrabController()->bindLayout(request->generation, request->revision, request->topologyRevision, request->frameGeneration,
                                                                            request->frameRevision, request->cells, request->targetEpoch);
    lua_pushboolean(L, accepted);
    return 1;
}

static int dsp_luminophoreBadge(lua_State* L) {
    const auto        generation = std::stoull(lua_tostring(L, lua_upvalueindex(1)));
    const auto        epoch      = std::stoull(lua_tostring(L, lua_upvalueindex(2)));
    const std::string mask       = lua_tostring(L, lua_upvalueindex(3));
    const CHyprColor  color{lua_tonumber(L, lua_upvalueindex(4)), lua_tonumber(L, lua_upvalueindex(5)), lua_tonumber(L, lua_upvalueindex(6)), 1.F};
    lua_pushboolean(L, Luminophore::spatialGrabController()->badgeAsset(generation, epoch, mask, color));
    return 1;
}
static int hlLuminophoreBadge(lua_State* L) {
    const auto generation = luminophoreExactID(L, 1, "generation"), epoch = luminophoreExactID(L, 1, "target_epoch");
    const auto mask = Internal::tableOptStr(L, 1, "mask").value_or("");
    const auto r = Internal::tableOptNum(L, 1, "red").value_or(0.4), g = Internal::tableOptNum(L, 1, "green").value_or(0.8), b = Internal::tableOptNum(L, 1, "blue").value_or(1.0);
    if (!generation || !*generation || !epoch || (!mask.empty() && mask.size() != 8192) || !std::isfinite(r) || !std::isfinite(g) || !std::isfinite(b) || std::min({r, g, b}) < 0 ||
        std::max({r, g, b}) > 1)
        return Internal::configError(L, "invalid LUMINOPHORE badge asset");
    lua_pushstring(L, std::to_string(*generation).c_str());
    lua_pushstring(L, std::to_string(*epoch).c_str());
    lua_pushlstring(L, mask.data(), mask.size());
    lua_pushnumber(L, r);
    lua_pushnumber(L, g);
    lua_pushnumber(L, b);
    lua_pushcclosure(L, dsp_luminophoreBadge, 6);
    return 1;
}

static int dsp_luminophoreGrabBegin(lua_State* L) {
    const auto id = [L](int i) { return std::stoull(lua_tostring(L, lua_upvalueindex(i))); };
    lua_pushboolean(L, Luminophore::spatialGrabController()->beginFromEditor(id(1), id(2), id(3), id(4), static_cast<uint32_t>(id(5))));
    return 1;
}
static int hlLuminophoreGrabBegin(lua_State* L) {
    const char* fields[] = {"window_id", "expected_revision", "topology_revision", "output", "press_time"};
    for (const auto field : fields) {
        const auto value = luminophoreExactID(L, 1, field);
        if (!value || (std::string_view(field) == "press_time" && (!*value || *value > UINT32_MAX)))
            return Internal::configError(L, "invalid LUMINOPHORE editor drag identity");
        lua_pushstring(L, std::to_string(*value).c_str());
    }
    lua_pushcclosure(L, dsp_luminophoreGrabBegin, 5);
    return 1;
}
static int dsp_luminophoreGrabCancel(lua_State* L) {
    lua_pushboolean(L, Luminophore::spatialGrabController()->cancelFromEditor(static_cast<uint32_t>(std::stoull(lua_tostring(L, lua_upvalueindex(1))))));
    return 1;
}
static int hlLuminophoreGrabCancel(lua_State* L) {
    const auto time = luminophoreExactID(L, 1, "press_time");
    if (!time || !*time || *time > UINT32_MAX)
        return Internal::configError(L, "invalid LUMINOPHORE editor drag cancellation");
    lua_pushstring(L, std::to_string(*time).c_str());
    lua_pushcclosure(L, dsp_luminophoreGrabCancel, 1);
    return 1;
}

static int hlLuminophoreGrabLayout(lua_State* L) {
    auto request = luminophoreGrabLayout(L);
    if (!request)
        return Internal::configError(L, "LUMINOPHORE grab layout requires exact string identities and a bounded array of finite cell rectangles");
    new (lua_newuserdata(L, sizeof(Luminophore::SSpatialGrabLayoutRegistration))) Luminophore::SSpatialGrabLayoutRegistration(std::move(*request));
    if (luaL_newmetatable(L, LUMINOPHORE_GRAB_LAYOUT_MT)) {
        lua_pushcfunction(L, luminophoreGrabLayoutGc);
        lua_setfield(L, -2, "__gc");
        lua_pushstring(L, LUMINOPHORE_GRAB_LAYOUT_MT);
        lua_setfield(L, -2, "__metatable");
    }
    lua_setmetatable(L, -2);
    lua_pushcclosure(L, dsp_luminophoreGrabLayout, 1);
    return 1;
}

struct SLuminophoreVisualRequest {
    std::optional<Luminophore::SVisualSettings>       settings;
    std::optional<Luminophore::SVisualReceiptRequest> receipt;
};
static constexpr const char*                       LUMINOPHORE_VISUAL_SETTINGS_MT = "LUMINOPHORE.VisualSettings";

static std::optional<Luminophore::SVisualSettings> luminophoreVisualRequest(lua_State* L) {
    if (!lua_istable(L, 1))
        return std::nullopt;
    lua_pushnil(L);
    while (lua_next(L, 1)) {
        size_t                 length = 0;
        const char*            key    = lua_type(L, -2) == LUA_TSTRING ? lua_tolstring(L, -2, &length) : nullptr;
        const std::string_view field  = key ? std::string_view{key, length} : std::string_view{};
        const bool             known  = field == "schema_version" || field == "preset" || field == "enabled" || field == "breathing" || field == "intensity";
        lua_pop(L, 1);
        if (!known) {
            lua_pop(L, 1);
            return std::nullopt;
        }
    }
    const auto read = [L](const char* field, int type) {
        lua_pushstring(L, field);
        lua_rawget(L, 1);
        if (lua_type(L, -1) == type)
            return true;
        lua_pop(L, 1);
        return false;
    };
    Luminophore::SVisualSettings request;
    if (!read("schema_version", LUA_TNUMBER))
        return std::nullopt;
    const double schema = lua_tonumber(L, -1);
    lua_pop(L, 1);
    if (!std::isfinite(schema) || schema < 0 || schema > std::numeric_limits<uint32_t>::max() || std::trunc(schema) != schema)
        return std::nullopt;
    request.schemaVersion = static_cast<uint32_t>(schema);
    if (!read("preset", LUA_TSTRING))
        return std::nullopt;
    size_t      length = 0;
    const char* preset = lua_tolstring(L, -1, &length);
    if (length > 256) {
        lua_pop(L, 1);
        return std::nullopt;
    }
    request.preset.assign(preset, length);
    lua_pop(L, 1);
    if (!read("enabled", LUA_TBOOLEAN))
        return std::nullopt;
    request.enabled = lua_toboolean(L, -1);
    lua_pop(L, 1);
    if (!read("breathing", LUA_TBOOLEAN))
        return std::nullopt;
    request.breathing = lua_toboolean(L, -1);
    lua_pop(L, 1);
    if (!read("intensity", LUA_TNUMBER))
        return std::nullopt;
    request.intensity = lua_tonumber(L, -1);
    lua_pop(L, 1);
    return request;
}

static int luminophoreVisualSettingsGc(lua_State* L) {
    auto* request = static_cast<SLuminophoreVisualRequest*>(luaL_checkudata(L, 1, LUMINOPHORE_VISUAL_SETTINGS_MT));
    request->~SLuminophoreVisualRequest();
    return 0;
}

static int dsp_luminophoreVisualState(lua_State* L) {
    const auto json = Luminophore::visualSettings()->json();
    lua_pushlstring(L, json.data(), json.size());
    return 1;
}

static int dsp_luminophoreVisualSettings(lua_State* L) {
    const auto* request = static_cast<SLuminophoreVisualRequest*>(lua_touserdata(L, lua_upvalueindex(1)));
    try {
        const auto revision = Luminophore::visualSettings()->revision();
        Luminophore::visualSettings()->apply(request->settings, request->receipt);
        if (revision != Luminophore::visualSettings()->revision())
            Luminophore::shellProjection()->visualSettingsChanged();
    } catch (const std::exception& error) { return Internal::dispatcherError(L, error.what()); }
    return dsp_luminophoreVisualState(L);
}

static int hlLuminophoreVisualSettings(lua_State* L) {
    SLuminophoreVisualRequest request{.settings = luminophoreVisualRequest(L)};
    if (lua_gettop(L) > 1) {
        if (lua_gettop(L) != 4 || lua_type(L, 2) != LUA_TSTRING || lua_type(L, 3) != LUA_TSTRING || lua_type(L, 4) != LUA_TSTRING)
            return Internal::configError(L, "visual receipt requires source, token and exact serial strings");
        size_t                             sourceLength = 0, tokenLength = 0, serialLength = 0;
        const auto*                        source = lua_tolstring(L, 2, &sourceLength);
        const auto*                        token  = lua_tolstring(L, 3, &tokenLength);
        const auto*                        serial = lua_tolstring(L, 4, &serialLength);
        Luminophore::SVisualReceiptRequest receipt{.source = std::string(source, sourceLength), .token = std::string(token, tokenLength)};
        const auto                         parsed = std::from_chars(serial, serial + serialLength, receipt.serial);
        if (sourceLength != 32 || receipt.source.find_first_not_of("0123456789abcdef") != std::string::npos || tokenLength == 0 || tokenLength > 128 ||
            receipt.token.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != std::string::npos || serialLength == 0 ||
            parsed.ec != std::errc{} || parsed.ptr != serial + serialLength)
            return Internal::configError(L, "invalid visual receipt identity");
        request.receipt = std::move(receipt);
    }
    new (lua_newuserdata(L, sizeof(SLuminophoreVisualRequest))) SLuminophoreVisualRequest(std::move(request));
    if (luaL_newmetatable(L, LUMINOPHORE_VISUAL_SETTINGS_MT)) {
        lua_pushcfunction(L, luminophoreVisualSettingsGc);
        lua_setfield(L, -2, "__gc");
        lua_pushstring(L, LUMINOPHORE_VISUAL_SETTINGS_MT);
        lua_setfield(L, -2, "__metatable");
    }
    lua_setmetatable(L, -2);
    lua_pushcclosure(L, dsp_luminophoreVisualSettings, 1);
    return 1;
}

static int hlLuminophoreVisualState(lua_State* L) {
    lua_pushcfunction(L, dsp_luminophoreVisualState);
    return 1;
}

static int hlLuminophoreSpatialAction(lua_State* L, Luminophore::eSpatialAction action) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.luminophore spatial action expects { direction = \"left|right|up|down\" }");
    const auto dirStr = Internal::tableOptStr(L, 1, "direction");
    if (!dirStr)
        return Internal::configError(L, "hl.luminophore spatial action requires direction");
    const auto dir = Internal::parseDirectionStr(*dirStr);
    if (dir == Math::DIRECTION_DEFAULT)
        return Internal::configError(L, "hl.luminophore spatial action: invalid direction");
    lua_pushnumber(L, (int)action);
    lua_pushnumber(L, (int)dir);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_luminophoreSpatial, 3);
    return 1;
}

static int hlLuminophoreViewMove(lua_State* L) {
    return hlLuminophoreSpatialAction(L, Luminophore::eSpatialAction::MOVE_VIEW);
}

static int hlLuminophoreViewAdjust(lua_State* L) {
    return hlLuminophoreSpatialAction(L, Luminophore::eSpatialAction::ADJUST_VIEW);
}

static int hlLuminophoreBoardMove(lua_State* L) {
    return hlLuminophoreSpatialAction(L, Luminophore::eSpatialAction::MOVE_WINDOW);
}

static int hlLuminophoreFocusDirection(lua_State* L) {
    return hlLuminophoreSpatialAction(L, Luminophore::eSpatialAction::FOCUS_DIRECTION);
}

static int hlLuminophoreDesktopToggle(lua_State* L) {
    lua_pushnumber(L, (int)Luminophore::eSpatialAction::TOGGLE_DESKTOP);
    lua_pushnumber(L, (int)Math::DIRECTION_DEFAULT);
    lua_pushnil(L);
    lua_pushcclosure(L, dsp_luminophoreSpatial, 3);
    return 1;
}

static int hlLuminophoreWideToggle(lua_State* L) {
    lua_pushnumber(L, (int)Luminophore::eSpatialAction::TOGGLE_WIDE);
    lua_pushnumber(L, (int)Math::DIRECTION_DEFAULT);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_luminophoreSpatial, 3);
    return 1;
}

static int hlWindowSwap(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.swap: expected a table, e.g. { direction = \"left\" }");

    auto dirStr = Internal::tableOptStr(L, 1, "direction");
    if (dirStr) {
        auto dir = Internal::parseDirectionStr(*dirStr);
        if (dir == Math::DIRECTION_DEFAULT)
            return Internal::configError(L, "hl.window.swap: invalid direction \"{}\" (expected left/right/up/down)", *dirStr);
        lua_pushnumber(L, (int)dir);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_swapInDirection, 2);
        return 1;
    }

    auto target = Internal::tableOptWindowSelector(L, 1, "target", "hl.window.swap");
    if (!target)
        target = Internal::tableOptWindowSelector(L, 1, "with", "hl.window.swap");
    if (!target)
        target = Internal::tableOptWindowSelector(L, 1, "other", "hl.window.swap");

    if (target) {
        Internal::pushWindowUpval(L, 1);
        lua_pushstring(L, target->c_str());
        lua_pushcclosure(L, dsp_swapWithWindow, 2);
        return 1;
    }

    auto next = Internal::tableOptBool(L, 1, "next");
    if (next && *next) {
        lua_pushboolean(L, true);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_swapNext, 2);
        return 1;
    }

    auto prev = Internal::tableOptBool(L, 1, "prev");
    if (prev && *prev) {
        lua_pushboolean(L, false);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_swapNext, 2);
        return 1;
    }

    return Internal::configError(L, "hl.window.swap: unrecognized arguments. Expected one of: direction, target/with/other, next, prev");
}

static int hlWindowCenter(lua_State* L) {
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_center, 1);
    return 1;
}

static int hlWindowCycleNext(lua_State* L) {
    bool next     = true;
    int  tiled    = -1;
    int  floating = -1;
    if (lua_istable(L, 1)) {
        auto n = Internal::tableOptBool(L, 1, "next");
        if (n)
            next = *n;
        auto t = Internal::tableOptBool(L, 1, "tiled");
        if (t)
            tiled = *t ? 1 : 0;
        auto f = Internal::tableOptBool(L, 1, "floating");
        if (f)
            floating = *f ? 1 : 0;
    }
    lua_pushboolean(L, next);
    lua_pushnumber(L, tiled);
    lua_pushnumber(L, floating);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_cycleNext, 4);
    return 1;
}

static int hlWindowTag(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.tag: expected a table { tag, window? }");

    const auto tag = Internal::requireTableFieldStr(L, 1, "tag", "hl.window.tag");
    lua_pushstring(L, tag.c_str());
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_tagWindow, 2);
    return 1;
}

static int hlWindowClearTags(lua_State* L) {
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_clearTags, 1);
    return 1;
}

static int hlWindowToggleSwallow(lua_State* L) {
    lua_pushcclosure(L, dsp_toggleSwallow, 0);
    return 1;
}
static int hlWindowPin(lua_State* L) {
    const auto action = Internal::tableToggleAction(L, 1);

    lua_pushnumber(L, (int)action);
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_pinWindow, 2);
    return 1;
}

static int hlWindowBringToTop(lua_State* L) {
    lua_pushcclosure(L, dsp_bringToTop, 0);
    return 1;
}

static int hlWindowAlterZOrder(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.alter_zorder: expected a table { mode, window? }");

    const auto mode = Internal::requireTableFieldStr(L, 1, "mode", "hl.window.alter_zorder");
    lua_pushstring(L, mode.c_str());
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_alterZOrder, 2);
    return 1;
}

static int hlWindowSetProp(lua_State* L) {
    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.set_prop: expected a table { prop, value, window? }");

    const auto prop  = Internal::requireTableFieldStr(L, 1, "prop", "hl.window.set_prop");
    const auto value = Internal::requireTableFieldStr(L, 1, "value", "hl.window.set_prop");
    lua_pushstring(L, prop.c_str());
    lua_pushstring(L, value.c_str());
    Internal::pushWindowUpval(L, 1);
    lua_pushcclosure(L, dsp_setProp, 3);
    return 1;
}

static int hlWindowDrag(lua_State* L) {
    lua_pushcclosure(L, dsp_mouseDrag, 0);
    return 1;
}

static int hlWindowResize(lua_State* L) {
    if (lua_gettop(L) == 0 || lua_isnil(L, 1)) {
        lua_pushnumber(L, 0);
        lua_pushcclosure(L, dsp_mouseResize, 1);
        return 1;
    }

    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.window.resize: expected no args, a table { x, y, relative?, window? }, or a table { keep_aspect_ratio }");

    auto x = Internal::tableOptNum(L, 1, "x");
    auto y = Internal::tableOptNum(L, 1, "y");
    if (x && y) {
        bool relative = Internal::tableOptBool(L, 1, "relative").value_or(false);
        lua_pushnumber(L, *x);
        lua_pushnumber(L, *y);
        lua_pushboolean(L, relative);
        Internal::pushWindowUpval(L, 1);
        lua_pushcclosure(L, dsp_resize, 4);
        return 1;
    }

    auto keepAspectRatio = Internal::tableOptBool(L, 1, "keep_aspect_ratio");
    if (keepAspectRatio) {
        lua_pushnumber(L, *keepAspectRatio ? 1 : 2);
        lua_pushcclosure(L, dsp_mouseResize, 1);
        return 1;
    }

    return Internal::configError(L, "hl.focus: unrecognized arguments. Expected positions (x & y) or keep_aspect_ratio");
}

static int dsp_moveFocus(lua_State* L) {
    return Internal::checkResult(L, CA::moveFocus(sc<Math::eDirection>((int)lua_tonumber(L, lua_upvalueindex(1)))));
}

static int dsp_focusMonitor(lua_State* L) {
    const auto PMONITOR = State::monitorState()->query().relativeTo(Desktop::focusState()->monitor()).configString(lua_tostring(L, lua_upvalueindex(1))).run();
    if (!PMONITOR)
        return Internal::dispatcherError(L, "hl.focus.monitor: monitor not found", WARN, C_NOTFOUND);
    return Internal::checkResult(L, CA::focusMonitor(PMONITOR));
}

static int dsp_focusWindowBySelector(lua_State* L) {
    const auto PWINDOW = Desktop::viewState()->query().selector(lua_tostring(L, lua_upvalueindex(1))).runWindow();
    if (!PWINDOW)
        return Internal::dispatcherError(L, "hl.focus: window not found", WARN, C_NOTFOUND);
    return Internal::checkResult(L, CA::focus(PWINDOW));
}

static int dsp_focusUrgentOrLast(lua_State* L) {
    return Internal::checkResult(L, CA::focusUrgentOrLast());
}

static int dsp_focusCurrentOrLast(lua_State* L) {
    return Internal::checkResult(L, CA::focusCurrentOrLast());
}

static int hlFocus(lua_State* L) {

    if (!lua_istable(L, 1))
        return Internal::configError(L, "hl.focus: expected a table, e.g. { direction = \"left\" }");

    lua_getfield(L, 1, "workspace");
    const bool retiredWorkspace = !lua_isnil(L, -1);
    lua_pop(L, 1);
    if (retiredWorkspace)
        return Internal::configError(L, "Workspace navigation was removed; use Luminophore spatial actions");

    auto dirStr = Internal::tableOptStr(L, 1, "direction");
    if (dirStr) {
        auto dir = Internal::parseDirectionStr(*dirStr);
        if (dir == Math::DIRECTION_DEFAULT)
            return Internal::configError(L, "hl.focus: invalid direction \"{}\" (expected left/right/up/down)", *dirStr);
        lua_pushnumber(L, (int)dir);
        lua_pushcclosure(L, dsp_moveFocus, 1);
        return 1;
    }

    auto monStr = Internal::tableOptMonitorSelector(L, 1, "monitor", "hl.focus");
    if (monStr) {
        lua_pushstring(L, monStr->c_str());
        lua_pushcclosure(L, dsp_focusMonitor, 1);
        return 1;
    }

    auto winStr = Internal::tableOptWindowSelector(L, 1, "window", "hl.focus");
    if (winStr) {
        lua_pushstring(L, winStr->c_str());
        lua_pushcclosure(L, dsp_focusWindowBySelector, 1);
        return 1;
    }

    auto urgent = Internal::tableOptBool(L, 1, "urgent_or_last");
    if (urgent && *urgent) {
        lua_pushcclosure(L, dsp_focusUrgentOrLast, 0);
        return 1;
    }

    auto last = Internal::tableOptBool(L, 1, "last");
    if (last && *last) {
        lua_pushcclosure(L, dsp_focusCurrentOrLast, 0);
        return 1;
    }

    return Internal::configError(L, "hl.focus: unrecognized arguments. Expected one of: direction, monitor, window, urgent_or_last, last");
}

static int dsp_noop(lua_State* L) {
    return 0;
}

static int hlNoop(lua_State* L) {
    lua_pushcclosure(L, dsp_noop, 0);
    return 1;
}

void Internal::registerDispatcherBindings(lua_State* L) {
    lua_newtable(L);
    Internal::markDispatcherTable(L);

    {
        lua_newtable(L);
        Internal::markDispatcherTable(L);
        Internal::setFn(L, "move_to_corner", hlCursorMoveToCorner);
        Internal::setFn(L, "move", hlCursorMove);
        lua_setfield(L, -2, "cursor");

        lua_newtable(L);
        Internal::markDispatcherTable(L);
        Internal::setFn(L, "close", hlWindowClose);
        Internal::setFn(L, "kill", hlWindowKill);
        Internal::setFn(L, "signal", hlWindowSignal);
        Internal::setFn(L, "float", hlWindowFloat);
        Internal::setFn(L, "fullscreen", hlWindowFullscreen);
        Internal::setFn(L, "fullscreen_state", hlWindowFullscreenState);
        Internal::setFn(L, "pseudo", hlWindowPseudo);
        Internal::setFn(L, "move", hlWindowMove);
        Internal::setFn(L, "swap", hlWindowSwap);
        Internal::setFn(L, "center", hlWindowCenter);
        Internal::setFn(L, "cycle_next", hlWindowCycleNext);
        Internal::setFn(L, "tag", hlWindowTag);
        Internal::setFn(L, "clear_tags", hlWindowClearTags);
        Internal::setFn(L, "toggle_swallow", hlWindowToggleSwallow);
        Internal::setFn(L, "pin", hlWindowPin);
        Internal::setFn(L, "bring_to_top", hlWindowBringToTop);
        Internal::setFn(L, "alter_zorder", hlWindowAlterZOrder);
        Internal::setFn(L, "set_prop", hlWindowSetProp);
        Internal::setFn(L, "drag", hlWindowDrag);
        Internal::setFn(L, "resize", hlWindowResize);
        lua_setfield(L, -2, "window");

        lua_newtable(L);
        Internal::markDispatcherTable(L);
        Internal::setFn(L, "occupy_output", hlLuminophoreOccupyOutput);
        Internal::setFn(L, "placement", hlLuminophorePlacement);
        Internal::setFn(L, "shell_projection", hlLuminophoreShellProjection);
        Internal::setFn(L, "view_move", hlLuminophoreViewMove);
        Internal::setFn(L, "view_adjust", hlLuminophoreViewAdjust);
        Internal::setFn(L, "board_move", hlLuminophoreBoardMove);
        Internal::setFn(L, "focus_direction", hlLuminophoreFocusDirection);
        Internal::setFn(L, "live_pip", hlLuminophoreLivePip);
        Internal::setFn(L, "move_window_to", hlLuminophoreMoveWindowTo);
        Internal::setFn(L, "move_output_view_to", hlLuminophoreMoveOutputViewTo);
        Internal::setFn(L, "resize_output_view", hlLuminophoreResizeOutputView);
        Internal::setFn(L, "spatial_drag_layout", hlLuminophoreGrabLayout);
        Internal::setFn(L, "spatial_drag_begin", hlLuminophoreGrabBegin);
        Internal::setFn(L, "spatial_drag_cancel", hlLuminophoreGrabCancel);
        Internal::setFn(L, "spatial_badge", hlLuminophoreBadge);
        Internal::setFn(L, "visual_settings", hlLuminophoreVisualSettings);
        Internal::setFn(L, "visual_state", hlLuminophoreVisualState);
        Internal::setFn(L, "wide_toggle", hlLuminophoreWideToggle);
        Internal::setFn(L, "desktop_toggle", hlLuminophoreDesktopToggle);
        Internal::setFn(L, "undo", hlLuminophoreUndo);
        Internal::setFn(L, "redo", hlLuminophoreRedo);
        lua_setfield(L, -2, "luminophore");

        Internal::setFn(L, "exec_cmd", hlExecCmd);
        Internal::setFn(L, "exec_raw", hlExecRaw);
        Internal::setFn(L, "exit", hlExit);
        Internal::setFn(L, "submap", hlSubmap);
        Internal::setFn(L, "pass", hlPass);
        Internal::setFn(L, "send_shortcut", hlSendShortcut);
        Internal::setFn(L, "send_key_state", hlSendKeyState);
        Internal::setFn(L, "dpms", hlDpms);
        Internal::setFn(L, "event", hlEvent);
        Internal::setFn(L, "global", hlGlobal);
        Internal::setFn(L, "force_renderer_reload", hlForceRendererReload);
        Internal::setFn(L, "force_idle", hlForceIdle);
        Internal::setFn(L, "release_input_capture", hlReleaseInputCapture);
        Internal::setFn(L, "focus", hlFocus);
        Internal::setFn(L, "no_op", hlNoop);
    }

    lua_setfield(L, -2, "dsp");
}
