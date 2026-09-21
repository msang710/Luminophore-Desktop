#include <luminophore/LuminophoreLaunchOrigin.hpp>
#include <managers/TokenManager.hpp>
#include <config/lua/bindings/LuaBindingsInternal.hpp>
#include <config/lua/ConfigManager.hpp>
#include <luminophore/LuminophoreSpatialGrabController.hpp>
#include <luminophore/LuminophoreVisualSettings.hpp>

#include <Compositor.hpp>
#include <desktop/rule/windowRule/WindowRule.hpp>

#include <config/lua/types/LuaConfigInt.hpp>

#include <gtest/gtest.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <format>
#include <fstream>

extern "C" {
#include <lualib.h>
#include <lauxlib.h>
}

using namespace Config::Lua;
using namespace Config::Lua::Bindings;

namespace Config::Lua {
    class CConfigManagerPluginLuaTestAccessor {
      public:
        static void initializeLuaState(CConfigManager& mgr, lua_State* L) {
            mgr.m_lua = L;
            lua_pushlightuserdata(L, &mgr);
            lua_setfield(L, LUA_REGISTRYINDEX, "hl_lua_manager");
        }

        static void initializeOwnedLuaState(CConfigManager& mgr, const std::filesystem::path& mainConfigPath) {
            mgr.m_mainConfigPath = mainConfigPath.string();
            mgr.m_configPaths.clear();
            mgr.m_configPaths.emplace_back(mgr.m_mainConfigPath);
            mgr.reinitLuaState();
        }

        static lua_State* luaState(CConfigManager& mgr) {
            return mgr.m_lua;
        }
    };
}

namespace {
    class CLuaState {
      public:
        CLuaState() : m_lua(luaL_newstate()) {
            luaL_openlibs(m_lua);
        }

        ~CLuaState() {
            if (m_lua)
                lua_close(m_lua);
        }

        lua_State* get() const {
            return m_lua;
        }

      private:
        lua_State* m_lua = nullptr;
    };

    int testPluginFn(lua_State* L) {
        lua_pushstring(L, "pong");
        return 1;
    }

    class CTempDir {
      public:
        CTempDir() {
            const auto NOW = std::chrono::steady_clock::now().time_since_epoch().count();
            m_path         = std::filesystem::temp_directory_path() / std::format("hyprland-lua-require-{}", NOW);
            std::filesystem::create_directories(m_path);
        }

        ~CTempDir() {
            std::error_code ec;
            std::filesystem::remove_all(m_path, ec);
        }

        const std::filesystem::path& path() const {
            return m_path;
        }

      private:
        std::filesystem::path m_path;
    };

    class CScopedCompositor {
      public:
        CScopedCompositor() : m_prevCompositor(std::move(g_pCompositor)), m_prevKeybindManager(std::move(g_pKeybindManager)) {
            g_pCompositor     = makeUnique<CCompositor>(true);
            g_pKeybindManager = makeUnique<CKeybindManager>();
        }

        ~CScopedCompositor() {
            g_pKeybindManager = std::move(m_prevKeybindManager);
            g_pCompositor     = std::move(m_prevCompositor);
        }

      private:
        UP<CCompositor>     m_prevCompositor;
        UP<CKeybindManager> m_prevKeybindManager;
    };

    std::string luaString(const std::string& value) {
        std::string out = "\"";
        for (const auto& c : value) {
            if (c == '\\' || c == '"')
                out += '\\';
            out += c;
        }
        out += '"';
        return out;
    }

    void writeFile(const std::filesystem::path& path, const std::string& content) {
        std::filesystem::create_directories(path.parent_path());
        std::ofstream file(path);
        file << content;
    }

    std::string normalizedPath(const std::filesystem::path& path) {
        return path.lexically_normal().string();
    }

    std::string packagePath(lua_State* L) {
        lua_getglobal(L, "package");
        lua_getfield(L, -1, "path");

        std::string path;
        if (const auto* value = lua_tostring(L, -1); value)
            path = value;

        lua_pop(L, 2);
        return path;
    }

    void expectTracked(CConfigManager& mgr, const std::filesystem::path& path) {
        const auto& paths = mgr.getConfigPaths();
        EXPECT_NE(std::ranges::find(paths, normalizedPath(path)), paths.end());
    }
}

TEST(ConfigLuaBindingsInternal, parseDirectionAliases) {
    EXPECT_EQ(Internal::parseDirectionStr("left"), Math::DIRECTION_LEFT);
    EXPECT_EQ(Internal::parseDirectionStr("l"), Math::DIRECTION_LEFT);
    EXPECT_EQ(Internal::parseDirectionStr("right"), Math::DIRECTION_RIGHT);
    EXPECT_EQ(Internal::parseDirectionStr("r"), Math::DIRECTION_RIGHT);
    EXPECT_EQ(Internal::parseDirectionStr("up"), Math::DIRECTION_UP);
    EXPECT_EQ(Internal::parseDirectionStr("t"), Math::DIRECTION_UP);
    EXPECT_EQ(Internal::parseDirectionStr("down"), Math::DIRECTION_DOWN);
    EXPECT_EQ(Internal::parseDirectionStr("b"), Math::DIRECTION_DOWN);
    EXPECT_EQ(Internal::parseDirectionStr("???"), Math::DIRECTION_DEFAULT);
}

TEST(ConfigLuaBindingsInternal, parseToggleAliases) {
    EXPECT_EQ(Internal::parseToggleStr(""), Config::Actions::TOGGLE_ACTION_TOGGLE);
    EXPECT_EQ(Internal::parseToggleStr("toggle"), Config::Actions::TOGGLE_ACTION_TOGGLE);
    EXPECT_EQ(Internal::parseToggleStr("enable"), Config::Actions::TOGGLE_ACTION_ENABLE);
    EXPECT_EQ(Internal::parseToggleStr("on"), Config::Actions::TOGGLE_ACTION_ENABLE);
    EXPECT_EQ(Internal::parseToggleStr("disable"), Config::Actions::TOGGLE_ACTION_DISABLE);
    EXPECT_EQ(Internal::parseToggleStr("off"), Config::Actions::TOGGLE_ACTION_DISABLE);
}

TEST(ConfigLuaBindingsInternal, argStrConvertsStringsAndNumbers) {
    CLuaState  S;
    const auto L = S.get();

    lua_pushstring(L, "abc");
    EXPECT_EQ(Internal::argStr(L, -1), "abc");
    lua_pop(L, 1);

    lua_pushnumber(L, 42);
    EXPECT_EQ(Internal::argStr(L, -1), "42");
    lua_pop(L, 1);
}

TEST(ConfigLuaBindingsInternal, tableOptHelpersReadOptionalFields) {
    CLuaState  S;
    const auto L = S.get();

    lua_createtable(L, 0, 5);
    lua_pushstring(L, "value");
    lua_setfield(L, -2, "s");
    lua_pushnumber(L, 5.5);
    lua_setfield(L, -2, "n");
    lua_pushboolean(L, true);
    lua_setfield(L, -2, "b");
    lua_pushstring(L, "not-number");
    lua_setfield(L, -2, "n2");
    lua_pushnil(L);
    lua_setfield(L, -2, "nilv");

    EXPECT_EQ(Internal::tableOptStr(L, -1, "s").value_or(""), "value");
    EXPECT_DOUBLE_EQ(Internal::tableOptNum(L, -1, "n").value_or(0), 5.5);
    EXPECT_EQ(Internal::tableOptBool(L, -1, "b").value_or(false), true);
    EXPECT_FALSE(Internal::tableOptNum(L, -1, "n2").has_value());
    EXPECT_FALSE(Internal::tableOptStr(L, -1, "missing").has_value());
    EXPECT_FALSE(Internal::tableOptBool(L, -1, "nilv").has_value());

    lua_pop(L, 1);
}

TEST(ConfigLuaBindingsInternal, selectorHelpersAcceptStringAndNumberSelectors) {
    CLuaState  S;
    const auto L = S.get();

    lua_createtable(L, 0, 4);
    lua_pushstring(L, "DP-1");
    lua_setfield(L, -2, "monitor");
    lua_pushnumber(L, 7);
    lua_setfield(L, -2, "workspace");
    lua_pushnumber(L, 1337);
    lua_setfield(L, -2, "window");

    EXPECT_EQ(Internal::tableOptMonitorSelector(L, -1, "monitor", "test.fn").value_or(""), "DP-1");
    EXPECT_EQ(Internal::tableOptWorkspaceSelector(L, -1, "workspace", "test.fn").value_or(""), "7");
    EXPECT_EQ(Internal::tableOptWindowSelector(L, -1, "window", "test.fn").value_or(""), "1337");

    EXPECT_FALSE(Internal::tableOptMonitorSelector(L, -1, "missing", "test.fn").has_value());
    EXPECT_FALSE(Internal::tableOptWorkspaceSelector(L, -1, "missing", "test.fn").has_value());
    EXPECT_FALSE(Internal::tableOptWindowSelector(L, -1, "missing", "test.fn").has_value());

    EXPECT_EQ(Internal::requireTableFieldMonitorSelector(L, -1, "monitor", "test.fn"), "DP-1");
    EXPECT_EQ(Internal::requireTableFieldWorkspaceSelector(L, -1, "workspace", "test.fn"), "7");
    EXPECT_EQ(Internal::requireTableFieldWindowSelector(L, -1, "window", "test.fn"), "1337");

    lua_pop(L, 1);
}

TEST(ConfigLuaBindingsInternal, pushWindowUpvalAcceptsNumberAndStringSelectors) {
    CLuaState  S;
    const auto L = S.get();

    lua_createtable(L, 0, 1);
    lua_pushnumber(L, 42);
    lua_setfield(L, -2, "window");

    Internal::pushWindowUpval(L, -1);
    ASSERT_TRUE(lua_isstring(L, -1));
    EXPECT_STREQ(lua_tostring(L, -1), "42");
    lua_pop(L, 1);

    lua_pushstring(L, "0xabc");
    lua_setfield(L, -2, "window");

    Internal::pushWindowUpval(L, -1);
    ASSERT_TRUE(lua_isstring(L, -1));
    EXPECT_STREQ(lua_tostring(L, -1), "0xabc");
    lua_pop(L, 1);

    lua_pushnil(L);
    lua_setfield(L, -2, "window");

    Internal::pushWindowUpval(L, -1);
    EXPECT_TRUE(lua_isnil(L, -1));
    lua_pop(L, 1);

    lua_pop(L, 1);
}

TEST(ConfigLuaBindingsInternal, parseTableFieldMissingFieldAndPrefixedErrors) {
    CLuaState     S;
    const auto    L = S.get();

    CLuaConfigInt parser(0);

    lua_newtable(L);
    auto err = Internal::parseTableField(L, -1, "required", parser);
    EXPECT_EQ(err.errorCode, PARSE_ERROR_BAD_VALUE);
    EXPECT_NE(err.message.find("missing required field"), std::string::npos);
    lua_pop(L, 1);

    lua_createtable(L, 0, 1);
    lua_pushstring(L, "bad");
    lua_setfield(L, -2, "count");

    err = Internal::parseTableField(L, -1, "count", parser);
    EXPECT_EQ(err.errorCode, PARSE_ERROR_BAD_TYPE);
    EXPECT_NE(err.message.find("field \"count\":"), std::string::npos);
    lua_pop(L, 1);
}

TEST(ConfigLuaBindingsInternal, pluginBindingIsTableWithLoadFunction) {
    CLuaState  S;
    const auto L = S.get();

    lua_newtable(L);
    Internal::registerConfigRuleBindings(L, nullptr);

    lua_getfield(L, -1, "plugin");
    ASSERT_TRUE(lua_istable(L, -1));

    lua_getfield(L, -1, "load");
    EXPECT_TRUE(lua_isfunction(L, -1));
    lua_pop(L, 1);

    lua_pop(L, 2);
}

TEST(ConfigLuaBindingsInternal, pluginLuaFnIsUnloadedWithoutDanglingCall) {
    CLuaState  S;
    const auto L = S.get();

    auto       PREVCOMPOSITOR = std::move(g_pCompositor);
    g_pCompositor             = makeUnique<CCompositor>(true);

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeLuaState(mgr, L);

    lua_newtable(L);
    Internal::registerConfigRuleBindings(L, &mgr);
    lua_setglobal(L, "hl");

    const auto HANDLE = reinterpret_cast<void*>(0x1BADB002);

    const auto regResult = mgr.registerPluginLuaFunction(HANDLE, "demo", "ping", testPluginFn);
    ASSERT_TRUE(regResult.has_value()) << regResult.error();

    ASSERT_EQ(luaL_dostring(L, R"(
        local f = hl.plugin.demo.ping
        assert(type(f) == "function")
        captured = f
        local v = f()
        assert(v == "pong")
    )"),
              LUA_OK);

    mgr.onPluginUnload(HANDLE);

    ASSERT_EQ(luaL_dostring(L, R"(
        assert(hl.plugin.demo == nil)
    )"),
              LUA_OK);

    ASSERT_EQ(luaL_dostring(L, R"(
        local ok, err = pcall(captured)
        assert(ok == false)
        assert(type(err) == "string")
        assert(string.find(err, "no longer available", 1, true) ~= nil)
    )"),
              LUA_OK);

    g_pCompositor = std::move(PREVCOMPOSITOR);
}

TEST(ConfigLuaRequire, absolutePathLoadsAndTracksFile) {
    CScopedCompositor compositor;
    CTempDir          tmp;
    const auto        mainConfig = tmp.path() / "hyprland.lua";
    const auto        module     = tmp.path() / "absolute.lua";
    writeFile(mainConfig, "");
    writeFile(module, "return { value = 42 }");

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeOwnedLuaState(mgr, mainConfig);
    const auto L = CConfigManagerPluginLuaTestAccessor::luaState(mgr);

    const auto CODE = "mod = require(" + luaString(module.string()) + ")";
    ASSERT_EQ(luaL_dostring(L, CODE.c_str()), LUA_OK) << lua_tostring(L, -1);

    lua_getglobal(L, "mod");
    ASSERT_TRUE(lua_istable(L, -1));
    lua_getfield(L, -1, "value");
    EXPECT_EQ(lua_tointeger(L, -1), 42);
    lua_pop(L, 2);

    expectTracked(mgr, module);
}

TEST(ConfigLuaRequire, relativePathResolvesFromConfigDirectory) {
    CScopedCompositor compositor;
    CTempDir          tmp;
    const auto        mainConfig = tmp.path() / "hyprland.lua";
    const auto        module     = tmp.path() / "modules" / "relative.lua";
    writeFile(mainConfig, "");
    writeFile(module, "return 'relative-ok'");

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeOwnedLuaState(mgr, mainConfig);
    const auto L = CConfigManagerPluginLuaTestAccessor::luaState(mgr);

    ASSERT_EQ(luaL_dostring(L, R"(
        mod = require("./modules/relative.lua")
    )"),
              LUA_OK)
        << lua_tostring(L, -1);

    lua_getglobal(L, "mod");
    ASSERT_TRUE(lua_isstring(L, -1));
    EXPECT_STREQ(lua_tostring(L, -1), "relative-ok");
    lua_pop(L, 1);

    expectTracked(mgr, module);
}

TEST(ConfigLuaRequire, wildcardLoadsSortedTableAndTracksFilesAndDirectory) {
    CScopedCompositor compositor;
    CTempDir          tmp;
    const auto        mainConfig = tmp.path() / "hyprland.lua";
    const auto        modulesDir = tmp.path() / "modules";
    const auto        moduleA    = modulesDir / "a.lua";
    const auto        moduleB    = modulesDir / "b.lua";
    writeFile(mainConfig, "");
    writeFile(moduleB, "return 'b'");
    writeFile(moduleA, "return 'a'");

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeOwnedLuaState(mgr, mainConfig);
    const auto L = CConfigManagerPluginLuaTestAccessor::luaState(mgr);

    ASSERT_EQ(luaL_dostring(L, R"(
        mods = require("./modules/*")
        assert(#mods == 2)
        assert(mods[1] == "a")
        assert(mods[2] == "b")
    )"),
              LUA_OK)
        << lua_tostring(L, -1);

    expectTracked(mgr, modulesDir);
    expectTracked(mgr, moduleA);
    expectTracked(mgr, moduleB);
}

TEST(ConfigLuaRequire, wildcardNoMatchIsCatchableError) {
    CScopedCompositor compositor;
    CTempDir          tmp;
    const auto        mainConfig = tmp.path() / "hyprland.lua";
    writeFile(mainConfig, "");

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeOwnedLuaState(mgr, mainConfig);
    const auto L = CConfigManagerPluginLuaTestAccessor::luaState(mgr);

    ASSERT_EQ(luaL_dostring(L, R"(
        ok, err = pcall(require, "./missing/*")
        assert(ok == false)
        assert(type(err) == "string")
        assert(string.find(err, "module './missing/*' not found", 1, true) ~= nil)
    )"),
              LUA_OK)
        << lua_tostring(L, -1);
}

TEST(ConfigLuaRequire, normalModuleRequireStillUsesConfigDirectoryPackagePath) {
    CScopedCompositor compositor;
    CTempDir          tmp;
    const auto        mainConfig = tmp.path() / "hyprland.lua";
    const auto        module     = tmp.path() / "colors.lua";
    writeFile(mainConfig, "");
    writeFile(module, "return 'normal-ok'");

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeOwnedLuaState(mgr, mainConfig);
    const auto L = CConfigManagerPluginLuaTestAccessor::luaState(mgr);

    ASSERT_EQ(luaL_dostring(L, R"(
        mod = require("colors")
    )"),
              LUA_OK)
        << lua_tostring(L, -1);

    lua_getglobal(L, "mod");
    ASSERT_TRUE(lua_isstring(L, -1));
    EXPECT_STREQ(lua_tostring(L, -1), "normal-ok");
    lua_pop(L, 1);

    expectTracked(mgr, module);
}

TEST(ConfigLuaRequire, packagePathPreservesLuaDefaultsAfterConfigDirectory) {
    CScopedCompositor compositor;
    CLuaState         defaultState;
    CTempDir          tmp;
    const auto        mainConfig = tmp.path() / "hyprland.lua";
    writeFile(mainConfig, "");

    const auto defaultPath = packagePath(defaultState.get());
    ASSERT_FALSE(defaultPath.empty());

    CConfigManager mgr;
    CConfigManagerPluginLuaTestAccessor::initializeOwnedLuaState(mgr, mainConfig);

    const auto configPath = (tmp.path() / "?.lua").string() + ";" + (tmp.path() / "?/init.lua").string();
    EXPECT_EQ(packagePath(CConfigManagerPluginLuaTestAccessor::luaState(mgr)), configPath + ";" + defaultPath);
}

TEST(ConfigLuaBindingsInternal, spatialEditorCapturesExactImmutableRequest) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "editor_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p = {expected_revision="17", topology_revision="9", output="9007199254740993", window="0xabc", x=7, y=3}
        captured_move = editor_test.dsp.luminophore.move_window_to(p)
        p.x = 99
        p.expected_revision = "18"
        captured_view = editor_test.dsp.luminophore.move_output_view_to(p)
        p.columns, p.rows = 2, 1
        captured_resize = editor_test.dsp.luminophore.resize_output_view(p)
    )"),
              LUA_OK);
    for (const auto& [name, expected] : std::vector<std::pair<const char*, const char*>>{
             {"captured_move", "move-window 17 9 9007199254740993 0xabc 7 3"},
             {"captured_view", "move-view 18 9 9007199254740993 99 3"},
             {"captured_resize", "resize-view 18 9 9007199254740993 99 3 2 1"},
         }) {
        lua_getglobal(L, name);
        ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
        ASSERT_TRUE(lua_isfunction(L, -1));
        ASSERT_NE(lua_getupvalue(L, -1, 1), nullptr);
        EXPECT_STREQ(lua_tostring(L, -1), expected);
        lua_pop(L, 3);
    }
}

TEST(ConfigLuaBindingsInternal, spatialEditorRejectsLossyAndMalformedFields) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "editor_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        for _, change in ipairs({
            {output=9007199254740993}, {expected_revision="-1"},
            {topology_revision="1 2"}, {window="0xabc;bad"},
            {x=0.5}, {y=true}, {x="7"}, {x=math.huge},
            {output="18446744073709551616"},
        }) do
            local p = {expected_revision="17", topology_revision="9", output="10", window="0xabc", x=7, y=3}
            for k,v in pairs(change) do p[k] = v end
            local ok, result = pcall(editor_test.dsp.luminophore.move_window_to, p)
            assert(not ok or (type(result) ~= "function" and type(result) ~= "userdata"))
        end
    )"),
              LUA_OK);
}

TEST(ConfigLuaBindingsInternal, spatialGrabLayoutCapturesExactIDsAndIndependentCells) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "editor_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local cell = {column=2,row=1,x=10.25,y=20.5,width=40,height=35}
        local p = {generation="9007199254740993",expected_revision="7",topology_revision="4",frame_generation="frame-a",frame_revision="5",cells={cell}}
        captured_layout = editor_test.dsp.luminophore.spatial_drag_layout(p)
        p.generation = "1"
        cell.x = 999
        p.cells = {}
        collectgarbage()
    )"),
              LUA_OK);
    lua_getglobal(L, "captured_layout");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_NE(lua_getupvalue(L, -1, 1), nullptr);
    const auto* request = static_cast<Luminophore::SSpatialGrabLayoutRegistration*>(lua_touserdata(L, -1));
    ASSERT_NE(request, nullptr);
    EXPECT_EQ(request->generation, 9007199254740993ULL);
    EXPECT_EQ(request->revision, 7U);
    EXPECT_EQ(request->topologyRevision, 4U);
    EXPECT_EQ(request->frameGeneration, "frame-a");
    EXPECT_EQ(request->frameRevision, 5U);
    ASSERT_EQ(request->cells.size(), 1U);
    EXPECT_EQ(request->cells[0].point, (SLuminophoreBoardPoint{2, 1}));
    EXPECT_DOUBLE_EQ(request->cells[0].x, 10.25);
    lua_pop(L, 3);
    lua_getglobal(L, "captured_layout");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_EQ(lua_pcall(L, 0, 1, 0), LUA_OK);
    ASSERT_TRUE(lua_isboolean(L, -1));
    EXPECT_FALSE(lua_toboolean(L, -1)); // No live grab: explicit rejection, no mutation.
    lua_pop(L, 2);
}

TEST(ConfigLuaBindingsInternal, spatialGrabLayoutRejectsMalformedIdentitiesAndCells) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "editor_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local too_many = {}
        for i=1,4097 do too_many[i]={column=0,row=0,x=0,y=0,width=1,height=1} end
        for _, change in ipairs({
            {generation=9007199254740993}, {generation="0"}, {expected_revision="-1"},
            {frame_revision="18446744073709551616"}, {topology_revision="4 5"},
            {frame_generation=""}, {cells=too_many}, {cells={false}},
            {cells={{column=0.5,row=0,x=0,y=0,width=1,height=1}}},
            {cells={{column=0,row=0,x=math.huge,y=0,width=1,height=1}}},
            {cells={{column=0,row=0,x="0",y=0,width=1,height=1}}},
        }) do
            local p = {generation="1",expected_revision="7",topology_revision="4",frame_generation="frame-a",frame_revision="5",cells={}}
            for k,v in pairs(change) do p[k]=v end
            local ok, value = pcall(editor_test.dsp.luminophore.spatial_drag_layout, p)
            assert(not ok or (type(value)~="userdata" and type(value)~="function"))
        end
    )"),
              LUA_OK);
}

TEST(ConfigLuaBindingsInternal, shellProjectionRetiresDropRolesAndRetainsPresentationRoles) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "projection_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p = {version=4, surface="luminophore-shell-launcher", generation="test", revision=1,
                   content_revision=1, phase="prepare", requested_plane="overlay"}
        for _, role in ipairs({"launcher-drop", "occupy-output-drop"}) do
            p.role = role
            local ok, result = pcall(projection_test.dsp.luminophore.shell_projection, p)
            assert(not ok or result == nil, role)
        end
        for _, role in ipairs({"passive", "osd"}) do
            p.role = role
            assert(type(projection_test.dsp.luminophore.shell_projection(p)) == "userdata", role)
        end
    )"),
              0)
        << (lua_tostring(L, -1) ? lua_tostring(L, -1) : "");
}

TEST(ConfigLuaBindingsInternal, visualSettingsCaptureAndApplyHaveSeparateLifetimes) {
    Luminophore::visualSettings() = makeUnique<Luminophore::CLuminophoreVisualSettings>();
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "visual_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p = {schema_version=1,preset="balanced",enabled=false,breathing=false,intensity=4}
        captured_visual = visual_test.dsp.luminophore.visual_settings(p)
        p.enabled = true
        p.intensity = 0
        collectgarbage()
    )"),
              LUA_OK);
    EXPECT_FALSE(Luminophore::visualSettings()->configured());
    lua_getglobal(L, "captured_visual");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_EQ(lua_pcall(L, 0, 1, 0), LUA_OK);
    ASSERT_TRUE(lua_isstring(L, -1));
    const auto response = std::string{lua_tostring(L, -1)};
    EXPECT_NE(response.find("\"configured\":true"), std::string::npos);
    EXPECT_NE(response.find("\"revision\":\"1\""), std::string::npos);
    EXPECT_FALSE(Luminophore::visualSettings()->current().bundle.settings.enabled);
    EXPECT_DOUBLE_EQ(Luminophore::visualSettings()->current().bundle.settings.intensity, 3.0);
    lua_pop(L, 2);
    ASSERT_EQ(luaL_dostring(L, "captured_read = visual_test.dsp.luminophore.visual_state()"), LUA_OK);
    lua_getglobal(L, "captured_read");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_EQ(lua_pcall(L, 0, 1, 0), LUA_OK);
    EXPECT_EQ(std::string{lua_tostring(L, -1)}, response);
    EXPECT_EQ(Luminophore::visualSettings()->revision(), 1U);
}

TEST(ConfigLuaBindingsInternal, visualSettingsMalformedPayloadRestoresWholeDefault) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "visual_test");
    for (const auto change : {"p.schema_version=0", "p.preset='unknown'", "p.enabled=1", "p.breathing='true'", "p.intensity='2'", "p.intensity=math.huge", "p.intensity=0/0",
                              "p.blur_size=100", "p.enabled=nil", "p={}", "p=false"}) {
        Luminophore::SVisualSettings previous;
        previous.enabled   = false;
        previous.intensity = 2;
        Luminophore::visualSettings()->apply(previous);
        const std::string script = std::string{"local p={schema_version=1,preset='balanced',enabled=true,breathing=true,intensity=1}; "} + change +
            "; invalid_visual=visual_test.dsp.luminophore.visual_settings(p)";
        ASSERT_EQ(luaL_dostring(L, script.c_str()), LUA_OK) << change;
        EXPECT_FALSE(Luminophore::visualSettings()->current().bundle.settings.enabled);
        lua_getglobal(L, "invalid_visual");
        ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
        ASSERT_EQ(lua_pcall(L, 0, 1, 0), LUA_OK) << change;
        EXPECT_EQ(Luminophore::visualSettings()->current().bundle, Luminophore::SVisualBundle{}) << change;
        EXPECT_NE(Luminophore::visualSettings()->current().recovery, Luminophore::eVisualRecovery::NONE) << change;
        lua_pop(L, 2);
    }
}

TEST(ConfigLuaBindingsInternal, visualReceiptFactoryCapturesIdentityAndRejectsStaleExecution) {
    Luminophore::visualSettings() = makeUnique<Luminophore::CLuminophoreVisualSettings>();
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "visual_test");
    lua_pushstring(L, Luminophore::visualSettings()->source().c_str());
    lua_setglobal(L, "source");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p={schema_version=1,preset="balanced",enabled=true,breathing=true,intensity=.42}
        captured_visual=visual_test.dsp.luminophore.visual_settings(p,source,"receipt", "0")
        source="changed"
        p.enabled=false
    )"),
              LUA_OK);
    EXPECT_EQ(Luminophore::visualSettings()->serial(), 0U);
    lua_getglobal(L, "captured_visual");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_EQ(lua_pcall(L, 0, 1, 0), LUA_OK);
    EXPECT_EQ(Luminophore::visualSettings()->serial(), 1U);
    EXPECT_TRUE(Luminophore::visualSettings()->current().bundle.settings.enabled);
    EXPECT_NE(Luminophore::visualSettings()->json().find("\"token\":\"receipt\""), std::string::npos);
    lua_pop(L, 2);
    lua_getglobal(L, "captured_visual");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    // Dispatch failure is represented by the dispatcher error contract; no write.
    lua_pcall(L, 0, 1, 0);
    EXPECT_EQ(Luminophore::visualSettings()->serial(), 1U);
}

TEST(ConfigLuaBindingsInternal, visualReceiptMetadataCannotFallBackToAnUnconditionalWrite) {
    Luminophore::visualSettings() = makeUnique<Luminophore::CLuminophoreVisualSettings>();
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "visual_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p={schema_version=1,preset="balanced",enabled=true,breathing=true,intensity=.42}
        local source=string.rep("a",32)
        for _, call in ipairs({
            function() return visual_test.dsp.luminophore.visual_settings(p,nil,"token","0") end,
            function() return visual_test.dsp.luminophore.visual_settings(p,source,"token",0) end,
            function() return visual_test.dsp.luminophore.visual_settings(p,source,"token","18446744073709551616") end,
            function() return visual_test.dsp.luminophore.visual_settings(p,source,"token","0","extra") end,
            function() return visual_test.dsp.luminophore.visual_settings(p,source,"bad\\0token","0") end,
        }) do
            local ok,value=pcall(call)
            assert(not ok or (type(value)~="userdata" and type(value)~="function"))
        end
    )"),
              LUA_OK);
    EXPECT_EQ(Luminophore::visualSettings()->serial(), 0U);
    EXPECT_FALSE(Luminophore::visualSettings()->configured());
}

TEST(ConfigLuaBindingsInternal, editorGrabFactoriesRequireExactPressIdentity) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "drag_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local n=drag_test.dsp.luminophore
        n.spatial_drag_begin({window_id="18446744073709551615",expected_revision="7",topology_revision="4",output="10",press_time="4294967295"})
        n.spatial_drag_cancel({press_time="1234"})
        for _,v in ipairs({0,"0",-1,"-1","4294967296","x"}) do
            local ok,value=pcall(n.spatial_drag_begin,{window_id="1",expected_revision="7",topology_revision="4",output="10",press_time=v})
            assert(not ok or (type(value)~="userdata" and type(value)~="function"))
        end
    )"),
              LUA_OK);
}

TEST(ConfigLuaBindingsInternal, spatialHistoryRequestCapturesExactRevisionAndIdentity) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "history_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p={request_id="undo-17",expected_revision="18446744073709551615",source="test-instance"}
        saved_undo=history_test.dsp.luminophore.undo(p)
        p.request_id="changed"
        p.expected_revision="0"
        for _,p in ipairs({{request_id="a"},{request_id="a",expected_revision=0},
            {request_id="a",expected_revision="18446744073709551616"},
            {request_id="bad token",expected_revision="0"}}) do
            local ok,value=pcall(history_test.dsp.luminophore.redo,p)
            assert(not ok or (type(value)~="userdata" and type(value)~="function"))
        end
    )"),
              LUA_OK);
    lua_getglobal(L, "saved_undo");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_NE(lua_getupvalue(L, -1, 2), nullptr);
    EXPECT_STREQ(lua_tostring(L, -1), "undo-17");
    lua_pop(L, 1);
    ASSERT_NE(lua_getupvalue(L, -1, 3), nullptr);
    EXPECT_STREQ(lua_tostring(L, -1), "18446744073709551615");
    lua_pop(L, 3);
}

TEST(ConfigLuaBindingsInternal, LivePipCapturesImmutableFiniteRequest) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "pip_test");
    ASSERT_EQ(luaL_dostring(L, R"(
        local p={action="create",request="request",instance="instance",output="DP-2",x=-100.5,y=20,width=360,height=240,margin=24}
        captured=pip_test.dsp.luminophore.live_pip(p)
        p.x=99
        p.request="different"
        for _,change in ipairs({{x=math.huge},{width=0},{request="x y"},{margin=-1},{output=1}}) do
            local q={action="create",request="request",instance="instance",output="DP-2",x=0,y=20,width=360,height=240,margin=24}
            for k,v in pairs(change) do q[k]=v end
            local ok,result=pcall(pip_test.dsp.luminophore.live_pip,q)
            assert(not ok or (type(result)~="function" and type(result)~="userdata"))
        end
    )"),
              LUA_OK);
    lua_getglobal(L, "captured");
    ASSERT_TRUE(Internal::pushDispatcherFunction(L, -1));
    ASSERT_NE(lua_getupvalue(L, -1, 1), nullptr);
    EXPECT_STREQ(lua_tostring(L, -1), "create request instance DP-2 -100.5 20 360 240 24");
    lua_pop(L, 3);
}

TEST(ConfigLuaBindingsInternal, InitialPlacementRuleAcceptsOnlyNamedDirections) {
    using namespace Desktop::Rule;
    for (const auto* direction : {"right", "left", "up", "down", "default"}) {
        CWindowRule rule;
        ASSERT_TRUE(rule.addEffect(WINDOW_RULE_EFFECT_INITIAL_PLACEMENT, std::string(direction)));
        ASSERT_EQ(rule.effects().size(), 1);
        EXPECT_EQ(std::get<std::string>(rule.effects()[0].value), direction);
    }
    CWindowRule invalid;
    EXPECT_FALSE(invalid.addEffect(WINDOW_RULE_EFFECT_INITIAL_PLACEMENT, std::string("(2,3)")));
    EXPECT_TRUE(invalid.effects().empty());
}

TEST(ConfigLuaBindingsInternal, DirectLaunchTokensAreSingleUseAndTypeSafe) {
    using namespace Luminophore::LaunchOrigin;
    struct SRestoreTokens {
        UP<CTokenManager> previous = std::move(g_pTokenManager);
        ~SRestoreTokens() {
            g_pTokenManager = std::move(previous);
        }
    } restore;
    g_pTokenManager = makeUnique<CTokenManager>();
    auto token      = issue();
    ASSERT_FALSE(token.empty());
    EXPECT_TRUE(consume(token));
    EXPECT_FALSE(consume(token));
    EXPECT_FALSE(consume("unknown"));
    const auto other = g_pTokenManager->registerNewToken(42, std::chrono::seconds(30));
    EXPECT_FALSE(consume(other));
    EXPECT_TRUE(g_pTokenManager->getToken(other));
    g_pTokenManager->removeToken(g_pTokenManager->getToken(other));
    EXPECT_TRUE(issue("bad group").empty());
    const auto grouped = issue("bundle-123");
    EXPECT_EQ(take(grouped), std::optional<std::string>{"bundle-123"});
    EXPECT_FALSE(take(grouped));
    const auto groupedAgain = issue("bundle-123");
    EXPECT_EQ(take(groupedAgain), std::optional<std::string>{"bundle-123"});
    const auto oldest = issue();
    for (int i = 0; i < 128; ++i)
        issue();
    EXPECT_FALSE(consume(oldest));
}

TEST(ConfigLuaBindingsInternal, RemovedWorkspaceDispatchersCannotBeConstructed) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "test_api");
    ASSERT_EQ(luaL_dostring(L, R"(
        assert(test_api.dsp.workspace == nil)
        for _, selector in ipairs({"1", "name:code", "previous", "m+1"}) do
            for _, fn in ipairs({test_api.dsp.focus, test_api.dsp.window.move}) do
                local ok, result = pcall(fn, {workspace=selector, direction="left"})
                assert(not ok or (type(result) ~= "function" and type(result) ~= "userdata"))
            end
        end
        assert(test_api.dsp.luminophore.undo ~= nil)
        assert(test_api.dsp.luminophore.redo ~= nil)
    )"),
              LUA_OK);
}

TEST(ConfigLuaBindingsInternal, RemovedWindowGroupsRejectMixedMoveRequests) {
    CLuaState state;
    auto*     L = state.get();
    lua_newtable(L);
    Internal::registerDispatcherBindings(L);
    lua_setglobal(L, "test_api");
    ASSERT_EQ(luaL_dostring(L, R"(
        assert(test_api.dsp.group == nil)
        assert(test_api.dsp.window.deny_from_group == nil)
        for _, key in ipairs({"group_aware", "into_group", "into_or_create_group", "out_of_group"}) do
            local ok,result=pcall(test_api.dsp.window.move,{direction="left",[key]=true})
            assert(not ok or (type(result)~="function" and type(result)~="userdata"))
        end
        local normal=test_api.dsp.window.move({direction="left"})
        assert(type(normal)=="function" or type(normal)=="userdata")
        assert(test_api.dsp.luminophore.undo ~= nil)
        assert(test_api.dsp.luminophore.redo ~= nil)
    )"),
              LUA_OK);
    EXPECT_FALSE(Desktop::Rule::matchPropFromString("group").has_value());
    EXPECT_TRUE(Desktop::Rule::matchPropFromString("modal").has_value());
}
