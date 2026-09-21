#include "../../src/luminophore/LuminophoreSpatialEditorProtocol.hpp"

#include <gtest/gtest.h>

TEST(LuminophoreSpatialEditorProtocol, PreservesExpectedRevisionsAndExactOutputIdentity) {
    const auto command = CLuminophoreSpatialEditorProtocol::parsePreview("move-window 17 9 9007199254740993 0xabc 7 3");
    ASSERT_TRUE(command);
    EXPECT_EQ(command->expectedRevision, 17U);
    const auto* payload = std::get_if<SMoveWindowToCommand>(&command->payload);
    ASSERT_NE(payload, nullptr);
    EXPECT_EQ(payload->key, 0xabcU);
    EXPECT_EQ(payload->outputID, 9007199254740993ULL);
    EXPECT_EQ(payload->expectedTopologyRevision, 9U);
    EXPECT_EQ(payload->point, (SLuminophoreBoardPoint{.x = 7, .y = 3}));
}

TEST(LuminophoreSpatialEditorProtocol, ParsesViewMoveAndResizeWithoutImplicitDefaults) {
    const auto move = CLuminophoreSpatialEditorProtocol::parsePreview("move-view 17 9 10 3 2");
    ASSERT_TRUE(move);
    EXPECT_EQ(std::get<SMoveOutputViewToCommand>(move->payload).origin, (SLuminophoreBoardPoint{.x = 3, .y = 2}));
    const auto resize = CLuminophoreSpatialEditorProtocol::parsePreview("resize-view 18 9 10 3 2 4 2");
    ASSERT_TRUE(resize);
    EXPECT_EQ(std::get<SResizeOutputViewCommand>(resize->payload).rect, (SLuminophoreViewRect{.origin = {.x = 3, .y = 2}, .columns = 4, .rows = 2}));
}

TEST(LuminophoreSpatialEditorProtocol, RejectsMalformedOrAmbiguousRequests) {
    for (const auto request : {
             "",
             "move-window 17 9 10 0xabc 7",
             "move-window 17 9 10 abc 7 3",
             "move-window 17 9 10 0x0 7 3",
             "move-view 17 9 0 3 2",
             "move-view 17 9 -1 3 2",
             "move-view 17 9 10 3 2 extra",
             "move-view 17.1 9 10 3 2",
             "resize-view 17 9 10 3 2 4",
             "resize-view 17 9 10 3 2 4 2 extra",
             "move-view 17 9 10 99999999999999999999 2",
             "move-view 18446744073709551616 9 10 3 2",
             "unknown 17 9 10 3 2",
         }) {
        SCOPED_TRACE(request);
        EXPECT_FALSE(CLuminophoreSpatialEditorProtocol::parsePreview(request));
    }
}

TEST(LuminophoreSpatialEditorProtocol, PreviewRoundTripDoesNotApplyModelMutation) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}}}, 9));
    ASSERT_TRUE(model.addTiled(0xabc, SLuminophoreBoardPoint{}));
    const auto command = CLuminophoreSpatialEditorProtocol::parsePreview("move-window 2 9 10 0xabc 7 1");
    ASSERT_TRUE(command);
    const auto before = model.snapshot();
    const auto result = model.preview(*command);
    EXPECT_EQ(model.snapshot(), before);
    EXPECT_EQ(
        CLuminophoreSpatialEditorProtocol::serialize(result),
        R"({"schema":1,"status":"applied","revision":3,"topologyRevision":9,"windows":[{"address":"0xabc","x":7,"y":1,"mode":"tiled","board":0}],"outputViews":[{"output":10,"x":0,"y":0,"columns":2,"rows":1}]})");
}

TEST(LuminophoreSpatialEditorProtocol, StalePreviewReturnsCurrentRevisionAndCoordinates) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{}));
    const auto command = CLuminophoreSpatialEditorProtocol::parsePreview("move-window 0 9 10 0x1 7 1");
    ASSERT_TRUE(command);
    const auto result = model.preview(*command);
    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::STALE_REVISION);
    EXPECT_EQ(result.snapshot, model.snapshot());
    EXPECT_NE(CLuminophoreSpatialEditorProtocol::serialize(result).find("\"status\":\"stale-revision\""), std::string::npos);
}

TEST(LuminophoreSpatialEditorProtocol, Signed64CoordinatesRemainExact) {
    const auto p = CLuminophoreSpatialEditorProtocol::parsePreview("move-view 17 9 10 -9007199254740993 9007199254740993");
    ASSERT_TRUE(p);
    const auto& move = std::get<SMoveOutputViewToCommand>(p->payload);
    EXPECT_EQ(move.origin.x, -9007199254740993LL);
    EXPECT_EQ(move.origin.y, 9007199254740993LL);
}
