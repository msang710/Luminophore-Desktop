#include <config/lua/DeviceSettings.hpp>
#include <gtest/gtest.h>

using namespace Config::Lua;
using namespace Luminophore::Settings;

TEST(LuaDeviceSettings, TypedRoundtripAndActualReadback) {
    Snapshot expected{{"tap_to_click", true}, {"repeat_rate", int64_t{35}}, {"sensitivity", 0.25}, {"kb_snapshot", std::string{"path\nmap"}}};
    auto     values = makeDeviceValues(expected, {});
    EXPECT_EQ(readDeviceValues(values), expected);
    lua_State* state = luaL_newstate();
    lua_pushinteger(state, 42);
    ASSERT_EQ(values.at("repeat_rate")->parse(state).errorCode, PARSE_ERROR_OK);
    EXPECT_EQ(std::get<int64_t>(readDeviceValues(values).at("repeat_rate")), 42);
    lua_close(state);
}

TEST(LuaDeviceSettings, PartialVectorUsesGlobalFallback) {
    auto values = makeDeviceValues({{"region_position_x", 3.0}}, {{"tablet.region_position_x", 1.0}, {"tablet.region_position_y", 2.0}});
    EXPECT_EQ(values.at("region_position")->asVec2().x, 3.F);
    EXPECT_EQ(values.at("region_position")->asVec2().y, 2.F);
    EXPECT_EQ(std::get<double>(readDeviceValues(values).at("region_position_y")), 2.0);
}

TEST(LuaDeviceSettings, ReplacementAndRollbackPreserveTypedValues) {
    auto       values = makeDeviceValues({{"repeat_rate", int64_t{25}}, {"kb_snapshot", std::string{"before"}}}, {});
    const auto before = readDeviceValues(values);
    values            = makeDeviceValues({{"repeat_rate", int64_t{40}}}, {});
    EXPECT_FALSE(readDeviceValues(values).contains("kb_snapshot"));
    values = makeDeviceValues(before, {});
    EXPECT_EQ(readDeviceValues(values), before);
}

TEST(LuaDeviceSettings, ExplicitStateCanBeRestoredWithoutChangingStoredValue) {
    auto  values = makeDeviceValues({{"kb_snapshot", std::string{"preserved"}}}, {});
    auto& value  = values.at("kb_snapshot");
    EXPECT_TRUE(value->setByUser());
    value->setExplicit(false);
    EXPECT_FALSE(value->setByUser());
    EXPECT_EQ(value->asString(), "preserved");
    value->setExplicit(true);
    EXPECT_TRUE(value->setByUser());
    EXPECT_EQ(value->asString(), "preserved");
}
