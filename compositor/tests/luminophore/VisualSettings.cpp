#include <gtest/gtest.h>
#include "src/luminophore/LuminophoreVisualSettings.hpp"
#include "src/render/pass/SurfacePassElement.hpp"

#include <limits>

using namespace Luminophore;

TEST(LuminophoreVisualSettings, NativeWirePreservesExactReceiptAndRejectsMalformedInput) {
    const auto parsed = parseVisualCommand("1 balanced true false 0.42 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa req-1 9007199254740993");
    ASSERT_TRUE(parsed);
    ASSERT_TRUE(parsed->receipt);
    EXPECT_EQ(parsed->receipt->serial, 9007199254740993ULL);
    EXPECT_FALSE(parsed->settings.breathing);
    for (const auto wire : {"1 balanced true true nan", "1 balanced 1 true 0.42", "1 balanced true true 0.42 extra",
                           "1 balanced true true 0.42 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa req -1", "hl.dispatch(os.exit())"})
        EXPECT_FALSE(parseVisualCommand(wire));
}

TEST(LuminophoreVisualSettings, BalancedDefaultsAndIntensityClampPreserveKernelParameters) {
    const auto defaults = CLuminophoreVisualSettings::resolve(SVisualSettings{});
    EXPECT_EQ(defaults.recovery, eVisualRecovery::NONE);
    EXPECT_EQ(defaults.bundle, SVisualBundle{});
    for (const auto intensity : {-100.0, 0.0, 0.42, 3.0, 100.0}) {
        SVisualSettings request;
        request.intensity = intensity;
        const auto result = CLuminophoreVisualSettings::resolve(request);
        EXPECT_GE(result.bundle.glowIntensity, 0.0);
        EXPECT_LE(result.bundle.glowIntensity, 3.0);
        EXPECT_EQ(result.bundle.blurSize, 2);
        EXPECT_EQ(result.bundle.blurPasses, 2);
        EXPECT_EQ(result.bundle.blurParameters(), (Render::SBlurParameters{.size = 2, .passes = 2}));
        EXPECT_EQ(result.bundle.glowExtent, 64.0);
        EXPECT_EQ(result.recovery, eVisualRecovery::NONE);
    }
}

TEST(LuminophoreVisualSettings, InvalidRequestRestoresWholeDefaultBundleAfterPriorApply) {
    CLuminophoreVisualSettings model;
    SVisualSettings     disabled;
    disabled.enabled   = false;
    disabled.breathing = false;
    disabled.intensity = 2.0;
    model.apply(disabled);
    for (int variant = 0; variant < 4; ++variant) {
        auto invalid = disabled;
        if (variant == 0)
            invalid.schemaVersion = 0;
        else if (variant == 1)
            invalid.preset = "unknown";
        else if (variant == 2)
            invalid.intensity = std::numeric_limits<double>::quiet_NaN();
        else
            invalid.intensity = std::numeric_limits<double>::infinity();
        const auto& result = model.apply(invalid);
        EXPECT_NE(result.recovery, eVisualRecovery::NONE);
        EXPECT_EQ(result.bundle, SVisualBundle{});
        model.apply(disabled);
    }
    EXPECT_EQ(model.apply(std::nullopt).recovery, eVisualRecovery::MALFORMED);
    EXPECT_EQ(model.current().bundle, SVisualBundle{});
}

TEST(LuminophoreVisualSettings, DisableAndStaticModeHaveNoBreathingAndGainIsBounded) {
    SVisualSettings request;
    request.enabled = false;
    auto bundle     = CLuminophoreVisualSettings::resolve(request).bundle;
    EXPECT_FALSE(bundle.blurEnabled);
    EXPECT_FALSE(bundle.animated);
    EXPECT_EQ(CLuminophoreVisualSettings::gainAt(bundle, 5.0, 0.5), 0.0);
    request.enabled   = true;
    request.breathing = false;
    bundle            = CLuminophoreVisualSettings::resolve(request).bundle;
    EXPECT_TRUE(bundle.blurEnabled);
    EXPECT_FALSE(bundle.animated);
    EXPECT_EQ(CLuminophoreVisualSettings::gainAt(bundle, 5.0, 0.5), request.intensity);
    request.breathing = true;
    bundle            = CLuminophoreVisualSettings::resolve(request).bundle;
    for (int i = -100; i < 1000; ++i) {
        const auto gain = CLuminophoreVisualSettings::gainAt(bundle, i * 0.123, 0.31);
        EXPECT_GE(gain, bundle.minimumGain * bundle.glowIntensity);
        EXPECT_LE(gain, bundle.maximumGain * bundle.glowIntensity);
    }
    EXPECT_EQ(CLuminophoreVisualSettings::gainAt(bundle, std::numeric_limits<double>::infinity(), 0.5), bundle.glowIntensity);
}

TEST(LuminophoreVisualSettings, RevisionChangesOnlyWhenNormalizedBundleChanges) {
    CLuminophoreVisualSettings model;
#if LUMINOPHORE_TEST_EFFECTS
    EXPECT_NE(model.json().find("\"rendererSchema\":1"), std::string::npos);
#else
    EXPECT_NE(model.json().find("\"rendererSchema\":0"), std::string::npos);
#endif
    EXPECT_FALSE(model.configured());
    EXPECT_EQ(model.revision(), 0U);
    model.apply(SVisualSettings{});
    EXPECT_TRUE(model.configured());
    EXPECT_EQ(model.revision(), 1U);
    model.apply(SVisualSettings{});
    EXPECT_EQ(model.revision(), 1U);
    model.apply(std::nullopt);
    EXPECT_EQ(model.revision(), 1U);
    EXPECT_EQ(model.current().recovery, eVisualRecovery::MALFORMED);
    SVisualSettings request;
    request.enabled = false;
    model.apply(request);
    EXPECT_EQ(model.revision(), 2U);
}

TEST(LuminophoreVisualSettings, CapturedFrameBundleKeepsGainWhenNextSettingsChange) {
    CLuminophoreVisualSettings model;
    SVisualSettings     request;
    request.breathing = false;
    request.intensity = 1.5;
    const auto frame  = model.apply(request).bundle;
    request.enabled   = false;
    model.apply(request);
    EXPECT_DOUBLE_EQ(CLuminophoreVisualSettings::gainAt(frame, 0.0, 0.2), 1.5);
    EXPECT_DOUBLE_EQ(CLuminophoreVisualSettings::gainAt(frame, 1000.0, 0.8), 1.5);
    EXPECT_DOUBLE_EQ(CLuminophoreVisualSettings::gainAt(model.current().bundle, 0.0, 0.2), 0.0);
    EXPECT_FALSE(frame.animated);
}

TEST(LuminophoreVisualSettings, ExplicitKernelRequestsLiveBlurAndNeverGlobalPrecompute) {
    CSurfacePassElement::SRenderData data{};
    data.blur            = true;
    data.forceBlurRegion = true;
    data.blurParameters  = SVisualBundle{}.blurParameters();
    CSurfacePassElement enabled(data);
    EXPECT_TRUE(enabled.needsLiveBlur());
    EXPECT_EQ(enabled.blurDamageRadius(), 8.F);
    EXPECT_FALSE(enabled.needsPrecomputeBlur());
    data.blur = false;
    CSurfacePassElement disabled(data);
    EXPECT_FALSE(disabled.needsLiveBlur());
    EXPECT_EQ(disabled.blurDamageRadius(), std::nullopt);
    EXPECT_FALSE(disabled.needsPrecomputeBlur());
}

TEST(LuminophoreVisualSettings, ReceiptsFenceLateRequestsEvenWhenBundleDoesNotChange) {
    CLuminophoreVisualSettings   model;
    const auto            source = model.source();
    SVisualReceiptRequest original{.source = source, .token = "original", .serial = 0};
    model.apply(SVisualSettings{}, original);
    EXPECT_EQ(model.serial(), 1U);
    EXPECT_EQ(model.revision(), 1U);
    EXPECT_NE(model.json().find("\"token\":\"original\""), std::string::npos);
    EXPECT_THROW(model.apply(SVisualSettings{}, original), std::invalid_argument);
    model.apply(SVisualSettings{}, SVisualReceiptRequest{.source = source, .token = "fence", .serial = 1});
    EXPECT_EQ(model.serial(), 2U);
    EXPECT_EQ(model.revision(), 1U);
    SVisualSettings late;
    late.enabled = false;
    EXPECT_THROW(model.apply(late, SVisualReceiptRequest{.source = source, .token = "late", .serial = 1}), std::invalid_argument);
    EXPECT_TRUE(model.current().bundle.settings.enabled);
    CLuminophoreVisualSettings restarted;
    EXPECT_NE(restarted.source(), source);
    EXPECT_THROW(restarted.apply(late, original), std::invalid_argument);
    EXPECT_FALSE(restarted.configured());
    model.apply(late);
    EXPECT_EQ(model.serial(), 3U);
    EXPECT_NE(model.json().find("\"token\":\"\""), std::string::npos);
}
