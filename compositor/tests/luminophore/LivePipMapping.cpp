#include "../../src/render/luminophore/LuminophoreLivePipRenderer.hpp"
#include "../../src/luminophore/LuminophoreLivePipMapping.hpp"
#include "../../src/protocols/types/SurfaceState.hpp"
#include <gtest/gtest.h>
using namespace Luminophore;

TEST(LuminophoreLivePipMapping, CropUsesCommittedViewportNotHiddenPresentation) {
    SSurfaceState state;
    state.bufferSize              = {1600, 1200};
    state.scale                   = 2;
    state.viewport.hasSource      = true;
    state.viewport.source         = {100, 50, 400, 300};
    state.viewport.hasDestination = true;
    state.viewport.destination    = {800, 600};
    state.size                    = {}; // desktop presentation and stale caches are irrelevant
    const auto    source          = CLuminophoreSurfaceSource::describe(1, 1, true, true, true, state);
    SLivePipEntry entry{.sourceToken = 1, .crop = {200, 100, 400, 300}, .destination = {-900.5, 20.25, 200, 150}};
    const auto    mapped = CLuminophoreLivePipMapping::sample(entry, source, state);
    ASSERT_TRUE(mapped);
    EXPECT_DOUBLE_EQ(mapped->uv.x, .25);
    EXPECT_DOUBLE_EQ(mapped->uv.y, 1.0 / 6);
    EXPECT_DOUBLE_EQ(mapped->uv.width, .25);
    EXPECT_DOUBLE_EQ(mapped->uv.height, .25);
    EXPECT_DOUBLE_EQ(mapped->destination.x, -900.5);
    EXPECT_DOUBLE_EQ(mapped->destination.width, 200);
}

TEST(LuminophoreLivePipMapping, AllBufferTransformsRoundTripSelectedRegion) {
    for (int transform = 0; transform < 8; ++transform) {
        SSurfaceState state;
        state.bufferSize  = {1200, 800};
        state.transform   = static_cast<wl_output_transform>(transform);
        const auto source = CLuminophoreSurfaceSource::describe(1, 1, true, true, true, state);
        ASSERT_TRUE(source.extent);
        SLivePipEntry entry{.sourceToken = 1,
                            .crop        = {source.extent->width * .1, source.extent->height * .2, source.extent->width * .3, source.extent->height * .4},
                            .destination = {1, 2, 150, 100}};
        const auto    mapped = CLuminophoreLivePipMapping::sample(entry, source, state);
        ASSERT_TRUE(mapped);
        auto selected = mapped->uv;
        selected.transform(Math::wlTransformToHyprutils(state.transform), 1, 1);
        EXPECT_NEAR(selected.x, .1, 1e-9);
        EXPECT_NEAR(selected.y, .2, 1e-9);
        EXPECT_NEAR(selected.width, .3, 1e-9);
        EXPECT_NEAR(selected.height, .4, 1e-9);
    }
}

TEST(LuminophoreLivePipMapping, PartialDamageMapsOnlyIntersectionAndOutsideDamageIsEmpty) {
    SLivePipEntry entry{.crop = {100, 200, 400, 200}, .destination = {-800, 50, 200, 100}};
    EXPECT_FALSE(CLuminophoreLivePipMapping::damage(entry, {0, 0, 100, 100}));
    const auto mapped = CLuminophoreLivePipMapping::damage(entry, {50, 250, 100, 300});
    ASSERT_TRUE(mapped);
    EXPECT_EQ(*mapped, (SLivePipRect{-800, 75, 25, 75}));
}

TEST(LuminophoreLivePipMapping, UnavailableSourceNeverProducesRenderPass) {
    SSurfaceState state;
    state.bufferSize     = {640, 400};
    auto          source = CLuminophoreSurfaceSource::describe(1, 1, true, true, true, state);
    SLivePipEntry entry{.sourceToken = 1, .crop = {0, 0, 640, 400}, .destination = {0, 0, 100, 100}};
    source.mapped = false;
    EXPECT_FALSE(CLuminophoreLivePipMapping::sample(entry, source, state));
    source.mapped = true;
    source.extent = SSourceExtent{300, 200};
    EXPECT_FALSE(CLuminophoreLivePipMapping::sample(entry, source, state));
}

#include "../../src/render/pass/SurfacePassElement.hpp"
TEST(LuminophoreLivePipMapping, SurfaceSamplingDoesNotNeedWindowGeometryOrDesktopVisibility) {
    CSurfacePassElement::SRenderData data;
    data.sourceSampleBox = CBox{10.5, 20.25, 100, 75};
    CSurfacePassElement pass(data);
    EXPECT_EQ(pass.getTexBox(), *data.sourceSampleBox);
    bool cancel = false;
    EXPECT_TRUE(pass.visibleRegion(cancel).empty());
    EXPECT_FALSE(cancel);
    EXPECT_TRUE(pass.opaqueRegion().empty());
}

TEST(LuminophoreLivePipMapping, OverlayControlsStayInsideCropAndDoNotOverlap) {
    for (const auto size : {Vector2D{360, 200}, Vector2D{48, 32}}) {
        const SLivePipRect crop{-900, -200, size.x, size.y};
        const auto         reveal = Render::CLuminophoreLivePipRenderer::controlBox(crop, false);
        const auto         close  = Render::CLuminophoreLivePipRenderer::controlBox(crop, true);
        EXPECT_GE(reveal.x, crop.x);
        EXPECT_GE(reveal.y, crop.y);
        EXPECT_LT(reveal.x + reveal.width, close.x);
        EXPECT_LE(close.x + close.width, crop.x + crop.width);
        EXPECT_LE(close.y + close.height, crop.y + crop.height);
    }
}
