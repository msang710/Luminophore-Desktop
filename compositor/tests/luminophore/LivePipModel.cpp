#include "../../src/luminophore/LuminophoreLivePipModel.hpp"
#include <gtest/gtest.h>
#include <limits>

using namespace Luminophore;
static SSurfaceSourceSnapshot source() {
    return {.token = 12, .revision = 4, .extentRevision = 4, .alive = true, .mapped = true, .hasBuffer = true, .extent = SSourceExtent{706, 830}};
}
static SLivePipRect destination() {
    return {.x = -1900.5, .y = -500.25, .width = 350, .height = 400};
}

TEST(LuminophoreLivePipModel, LocalCropAndNegativeOutputPositionAreIndependent) {
    CLuminophoreLivePipModel  model;
    const SLivePipRect crop{20.5, 30.25, 150.5, 220.25};
    auto               id = model.create(source(), 4, crop, "DP-2", destination());
    ASSERT_TRUE(id);
    EXPECT_EQ(model.entries().at(*id).crop, crop);
    EXPECT_EQ(model.entries().at(*id).destination, destination());
    EXPECT_EQ(model.revision(), 1U);
    auto updated     = source();
    updated.revision = 100; // new video frames do not change the selected crop
    EXPECT_TRUE(CLuminophoreLivePipModel::sampleable(model.entries().at(*id), updated));
    EXPECT_EQ(model.revision(), 1U);
}

TEST(LuminophoreLivePipModel, CreationRejectsStaleUnavailableAndOutOfBoundsWithoutMutation) {
    CLuminophoreLivePipModel model;
    EXPECT_FALSE(model.create(source(), 3, {0, 0, 10, 10}, "DP-2", destination()));
    for (const auto& crop : {SLivePipRect{-1, 0, 10, 10}, SLivePipRect{700, 0, 10, 10}, SLivePipRect{0, 820, 10, 20}, SLivePipRect{0, 0, 0, 10}})
        EXPECT_FALSE(model.create(source(), 4, crop, "DP-2", destination()));
    auto unavailable = source();
    unavailable.extent.reset();
    EXPECT_FALSE(model.create(unavailable, 4, {0, 0, 10, 10}, "DP-2", destination()));
    EXPECT_FALSE(model.create(source(), 4, {0, 0, 10, 10}, "", destination()));
    EXPECT_EQ(model.revision(), 0U);
    EXPECT_TRUE(model.entries().empty());
    EXPECT_EQ(model.create(source(), 4, {0, 0, 706, 830}, "DP-2", destination()), 1U);
}

TEST(LuminophoreLivePipModel, SourceLossResizeAndReplacementNeverRetargetOrSilentlyClip) {
    CLuminophoreLivePipModel model;
    auto              id = model.create(source(), 4, {600, 700, 100, 100}, "DP-2", destination());
    ASSERT_TRUE(id);
    const auto before = model.entries().at(*id);
    auto       small  = source();
    small.extent      = SSourceExtent{320, 240};
    EXPECT_FALSE(CLuminophoreLivePipModel::sampleable(before, small));
    auto replacement = source();
    ++replacement.token;
    EXPECT_FALSE(CLuminophoreLivePipModel::sampleable(before, replacement));
    auto destroyed  = source();
    destroyed.alive = false;
    EXPECT_FALSE(CLuminophoreLivePipModel::sampleable(before, destroyed));
    auto unmapped   = source();
    unmapped.mapped = false;
    EXPECT_FALSE(CLuminophoreLivePipModel::sampleable(before, unmapped));
    EXPECT_EQ(model.entries().at(*id).crop, before.crop);
    EXPECT_EQ(model.revision(), 1U);
    EXPECT_TRUE(CLuminophoreLivePipModel::sampleable(before, source()));
}

TEST(LuminophoreLivePipModel, CheckedGeometryRejectsNanInfinityOverflowAndPrecisionCollapse) {
    CLuminophoreLivePipModel model;
    const auto        inf = std::numeric_limits<double>::infinity();
    const auto        nan = std::numeric_limits<double>::quiet_NaN();
    const auto        max = std::numeric_limits<double>::max();
    for (const auto& rect : {SLivePipRect{nan, 0, 10, 10}, SLivePipRect{0, 0, inf, 10}, SLivePipRect{max, 0, max, 10}, SLivePipRect{max, 0, 1, 10}}) {
        EXPECT_FALSE(model.create(source(), 4, rect, "DP-2", destination()));
        EXPECT_FALSE(model.create(source(), 4, {0, 0, 10, 10}, "DP-2", rect));
    }
    EXPECT_TRUE(model.entries().empty());
}

TEST(LuminophoreLivePipModel, RemovedIDsAreNotReusedAndRepeatedRemoveIsNoop) {
    CLuminophoreLivePipModel model;
    auto              first = model.create(source(), 4, {0, 0, 10, 10}, "DP-2", destination());
    ASSERT_TRUE(first);
    EXPECT_TRUE(model.remove(*first));
    EXPECT_FALSE(model.remove(*first));
    EXPECT_EQ(model.revision(), 2U);
    auto second = model.create(source(), 4, {0, 0, 10, 10}, "DP-2", destination());
    ASSERT_TRUE(second);
    EXPECT_GT(*second, *first);
}

TEST(LuminophoreLivePipModel, NewContentFramesDoNotInvalidateSelectionButResizeDoes) {
    CLuminophoreLivePipModel model;
    auto              current = source();
    current.revision          = 500;
    EXPECT_TRUE(model.create(current, 4, {0, 0, 100, 100}, "DP-2", destination()));
    ++current.extentRevision;
    // Even if the old crop fits, a genuinely resized source requires a fresh selection.
    EXPECT_FALSE(model.create(current, 4, {0, 0, 100, 100}, "DP-2", destination()));
    EXPECT_EQ(model.entries().size(), 1U);
}

TEST(LuminophoreLivePipModel, FailedOrStaleUpdatePreservesOldCropAndRevision) {
    CLuminophoreLivePipModel model;
    auto              id = model.create(source(), 4, {0, 0, 100, 100}, "DP-2", destination());
    ASSERT_TRUE(id);
    const auto old = model.entries().at(*id);
    EXPECT_FALSE(model.update(*id, 0, source(), 4, {50, 50, 100, 100}, "DP-2", destination()));
    EXPECT_FALSE(model.update(*id, 1, source(), 3, {50, 50, 100, 100}, "DP-2", destination()));
    EXPECT_FALSE(model.update(*id, 1, source(), 4, {700, 50, 100, 100}, "DP-2", destination()));
    EXPECT_EQ(model.entries().at(*id).crop, old.crop);
    EXPECT_EQ(model.revision(), 1U);
    EXPECT_TRUE(model.update(*id, 1, source(), 4, {50, 50, 100, 100}, "DP-1", destination()));
    EXPECT_EQ(model.revision(), 2U);
    EXPECT_EQ(model.entries().at(*id).output, "DP-1");
    EXPECT_FALSE(model.update(*id, 1, source(), 4, old.crop, "DP-2", destination()));
}
