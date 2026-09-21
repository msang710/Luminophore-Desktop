#include "../../src/config/luminophore/GeneralSettings.hpp"
#include "../../src/config/luminophore/GeneratedSettings.hpp"
#include <gtest/gtest.h>

using namespace Luminophore::Settings;
TEST(GeneralSettings, ShadowRoundTripPreservesAlphaStopsAndAngle) {
    for (int angle = 0; angle < 360; ++angle) {
        const auto value  = "ee1a1a1a 00224466 " + std::to_string(angle) + "deg";
        const auto parsed = parseShadowGradient(value);
        ASSERT_EQ(parsed.m_colors.size(), 2U);
        EXPECT_EQ(parsed.m_colorsOkLabA.size(), 8U);
        EXPECT_EQ(shadowGradient(parsed), value);
    }
    EXPECT_EQ(shadowGradient(parseShadowGradient("ffffffff 90deg")), "ffffffff 90deg");
    EXPECT_EQ(shadowGradient(parseShadowGradient("ffffffff 0deg")), "ffffffff 0deg");
    EXPECT_EQ(generalColor(parseGeneralColor("00112233")), "00112233");
}
TEST(GeneralSettings, RejectsMalformedAndOutOfRangeValues) {
    EXPECT_FALSE(validate({{"compositor.shadow_color", std::string{"ff000000 360deg"}}}).empty());
    EXPECT_FALSE(validate({{"compositor.font_family", std::string{}}}).empty());
    EXPECT_FALSE(validate({{"compositor.float_gap_left", int64_t{-1}}}).empty());
    EXPECT_FALSE(validate({{"compositor.cursor_start_output", std::string{"DP-1\n"}}}).empty());
    EXPECT_TRUE(validate({{"compositor.shadow_color", std::string{"ff000000 80223344 270deg"}}}).empty());
    EXPECT_THROW(parseShadowGradient("ff000000 30deg extra"), std::invalid_argument);
}

#include "../../src/config/luminophore/RuntimeSettingsAdapter.hpp"

TEST(GeneralSettings, ParticipantDistinguishesCompensationFromOrdinaryApply) {
    const auto canonical = defaults();
    auto values = canonical;
    const auto native = [](const std::string& key, const Value& v) -> Value {
        if (const auto* d = std::get_if<double>(&v); d && key != "visual.intensity") return double(float(*d));
        return v;
    };
    for (auto& [key, value] : values) value = native(key, value);
    std::map<std::string, SRuntimeSlot> slots;
    for (const auto& [key, value] : values)
        slots[key] = {[&, key] { return values.at(key); }, [&, key](const Value& v) { values[key] = native(key, v); }};
    int                     applies = 0, restores = 0;
    CRuntimeSettingsAdapter adapter(canonical, std::move(slots), [&] { ++applies; }, [&] { ++restores; });
    const std::string       epoch(32, 'a'), initial(64, 'b');
    CSettingsParticipant    participant(
        epoch, initial, canonical, [&](const Snapshot& v) { adapter.apply(v); }, [&] { return adapter.read(); }, [&](const Snapshot& v) { adapter.restore(v); });
    auto candidate                   = canonical;
    candidate["compositor.auto_hdr"] = int64_t{2};
    ASSERT_EQ(participant.prepare({epoch, 1, 0, std::string(64, 'c'), candidate}), eResult::OK);
    ASSERT_EQ(participant.apply(epoch, 1), eResult::OK);
    ASSERT_EQ(participant.restore(epoch, 1), eResult::OK);
    EXPECT_EQ(applies, 1);
    EXPECT_EQ(restores, 1);
    EXPECT_EQ(std::get<int64_t>(adapter.read().at("compositor.auto_hdr")), 1);
    adapter.apply(candidate);
    adapter.apply(defaults());
    EXPECT_EQ(applies, 3);
    EXPECT_EQ(restores, 1);
}
