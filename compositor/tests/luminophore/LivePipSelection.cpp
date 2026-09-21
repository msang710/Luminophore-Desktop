#include "../../src/luminophore/LuminophoreLivePipSelection.hpp"
#include "../../src/luminophore/LuminophoreLivePipProtocol.hpp"
#include <gtest/gtest.h>
using namespace Luminophore;
static SPipSourceView view(uint64_t token) {
    return {.state = {.token = token, .revision = 1, .extentRevision = 1, .alive = true, .mapped = true, .hasBuffer = true, .extent = SSourceExtent{200, 100}},
            .box   = {-100, 50, 400, 200}};
}
TEST(LuminophoreLivePipSelection, FractionalScaledSourceLocalCrop) {
    SPipSelectionScene scene{.sources = {view(1)}, .tiles = {{{-100, 50, 400, 200}, 0}}};
    const auto         result = CLuminophoreLivePipSelection::resolve(scene, {-80, 70, 120, 80});
    ASSERT_TRUE(result);
    EXPECT_EQ(result->crop, (SLivePipRect{10, 10, 60, 40}));
}
TEST(LuminophoreLivePipSelection, RejectsInteriorOccluderAndHoleNotJustCorners) {
    SPipSelectionScene scene{.sources = {view(1), view(2)}};
    const CBox         occluder{50, 100, 20, 20};
    for (const auto& box : CLuminophoreLivePipSelection::partition({-100, 50, 400, 200}, {occluder}))
        scene.tiles.push_back({box, occluder.containsPoint(box.middle()) ? 1U : 0U});
    EXPECT_FALSE(CLuminophoreLivePipSelection::resolve(scene, {-100, 50, 400, 200}));
    std::erase_if(scene.tiles, [](const auto& t) { return t.source == 1; });
    EXPECT_FALSE(CLuminophoreLivePipSelection::resolve(scene, {-100, 50, 400, 200}));
    EXPECT_TRUE(CLuminophoreLivePipSelection::resolve(scene, {-90, 60, 30, 30}));
}
TEST(LuminophoreLivePipProtocol, ExactTokensAndFiniteGeometry) {
    EXPECT_TRUE(CLuminophoreLivePipProtocol::parse("begin request instance"));
    EXPECT_TRUE(CLuminophoreLivePipProtocol::parse("resolve request instance -100.5 20 30 40"));
    EXPECT_TRUE(CLuminophoreLivePipProtocol::parse("create request instance DP-1 100 100 360 200 24"));
    EXPECT_TRUE(CLuminophoreLivePipProtocol::parse("place 1 instance 18446744073709551615 DP-1 -10 20 30 40"));
    for (const auto& raw : {"begin request instance extra", "resolve request instance nan 0 20 20", "create request instance DP-1 0 0 20 20 -1",
                            "place 1 instance -1 DP-1 0 0 20 20", "remove x instance 1", "resolve request instance 0 0 0 10"})
        EXPECT_FALSE(CLuminophoreLivePipProtocol::parse(raw)) << raw;
}
TEST(LuminophoreLivePipModel, InvalidSourceCropDoesNotPreventMovingPlaceholder) {
    CLuminophoreLivePipModel model;
    auto              state = view(1).state;
    const auto        id    = model.create(state, 1, {10, 10, 100, 80}, "DP-1", {0, 0, 100, 80});
    ASSERT_TRUE(id);
    state.extent = SSourceExtent{50, 50};
    EXPECT_FALSE(CLuminophoreLivePipModel::sampleable(model.entries().at(*id), state));
    EXPECT_TRUE(model.place(*id, model.revision(), "DP-2", {100, 100, 200, 160}));
    EXPECT_EQ(model.entries().at(*id).crop, (SLivePipRect{10, 10, 100, 80}));
    EXPECT_FALSE(model.place(*id, 0, "DP-1", {0, 0, 100, 80}));
}
