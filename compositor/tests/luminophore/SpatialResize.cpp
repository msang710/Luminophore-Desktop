#include "../../src/luminophore/LuminophoreSpatialResize.hpp"
#include <gtest/gtest.h>
#include <algorithm>
#include <random>
using namespace Luminophore::Spatial;
static SMesh row() {
    return {{{0, 0}, 3, 1},
            850,
            300,
            0,
            {
                {1, {{0, 0}, 1, 1}, {0, 0, 400, 300}, true},
                {2, {{1, 0}, 1, 1}, {400, 0, 150, 300}, true},
                {3, {{2, 0}, 1, 1}, {550, 0, 300, 300}, true},
            }};
}
static SBox boxFor(const SMesh& m, FaceID id) {
    return std::ranges::find(m.faces, id, &SFace::id)->box;
}
// These fixtures model one rectangular client per face. Exercise the native
// owner-aware solver rather than the retired partial-slab implementation.
static SResizeResult resizeFixture(const SMesh& mesh, const SResizeRequest& request) {
    SFill fill{eFillStatus::OK};
    for (const auto& face : mesh.faces)
        fill.rectangles.push_back({face.provenance, face.id, true});
    return solveWindowResize(mesh, fill, request.face, request);
}
TEST(LuminophoreSpatialResize, ConsumesNearestSlackThenPushesNextAndClamps) {
    const auto mesh = row();
    ASSERT_TRUE(validateMesh(mesh));
    auto r = resizeFixture(mesh, {0, 1, eSide::RIGHT, 120});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(boxFor(r.mesh, 1).width, 520);
    EXPECT_EQ(boxFor(r.mesh, 2).width, 100);
    EXPECT_EQ(boxFor(r.mesh, 3).width, 230);
    EXPECT_EQ(r.mesh.view, mesh.view);
    r = resizeFixture(mesh, {0, 1, eSide::RIGHT, 1000});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(r.effectiveDelta, 250);
    EXPECT_EQ(boxFor(r.mesh, 3).width, 100);
}
TEST(LuminophoreSpatialResize, IndependentRowsDoNotMoveTogether) {
    SBoard b{.view = {{0, 0}, 2, 2}};
    b.tiled          = {{{0, 0}, 1}, {{1, 0}, 2}, {{0, 1}, 3}, {{1, 1}, 4}};
    const auto built = buildMesh(b, 800, 600);
    const auto r     = resizeFixture(built.mesh, {0, 1, eSide::RIGHT, 100});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(boxFor(r.mesh, 1).width, 500);
    EXPECT_EQ(boxFor(r.mesh, 3), boxFor(built.mesh, 3));
    EXPECT_EQ(boxFor(r.mesh, 4), boxFor(built.mesh, 4));
}
TEST(LuminophoreSpatialResize, TJunctionBranchesAndMergesWithoutOrderDependence) {
    SMesh m{{{0, 0}, 3, 2},
            850,
            300,
            0,
            {
                {1, {{0, 0}, 1, 2}, {0, 0, 400, 300}, true},
                {2, {{1, 0}, 1, 1}, {400, 0, 150, 100}, true},
                {3, {{1, 1}, 1, 1}, {400, 100, 150, 200}, true},
                {4, {{2, 0}, 1, 2}, {550, 0, 300, 300}, true},
            }};
    ASSERT_TRUE(validateMesh(m));
    const auto r = resizeFixture(m, {0, 1, eSide::RIGHT, 120});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_TRUE(validateMesh(r.mesh));
    for (const auto& f : r.mesh.faces) {
        if (f.provenance.origin.x == 0) {
            EXPECT_EQ(f.box.width, 520);
        }
    }
    std::ranges::reverse(m.faces);
    const auto reversed = resizeFixture(m, {0, 1, eSide::RIGHT, 120});
    ASSERT_EQ(reversed.status, eResizeStatus::APPLIED);
    auto boxes = [](const SMesh& mesh) {
        std::vector<std::tuple<int64_t, int64_t, int64_t, int64_t>> result;
        for (const auto& f : mesh.faces)
            result.emplace_back(f.box.x, f.box.y, f.box.width, f.box.height);
        std::ranges::sort(result);
        return result;
    };
    EXPECT_EQ(boxes(r.mesh), boxes(reversed.mesh));
}
TEST(LuminophoreSpatialResize, BothSignsBothAxesAndUndersizedCore) {
    auto mesh = row();
    auto r    = resizeFixture(mesh, {0, 2, eSide::LEFT, -80});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(boxFor(r.mesh, 1).width, 320);
    EXPECT_EQ(boxFor(r.mesh, 2).width, 230);
    std::swap(mesh.width, mesh.height);
    for (auto& f : mesh.faces) {
        std::swap(f.box.x, f.box.y);
        std::swap(f.box.width, f.box.height);
    }
    r = resizeFixture(mesh, {0, 1, eSide::BOTTOM, 120});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(boxFor(r.mesh, 1).height, 520);
    r = resizeFixture(mesh, {0, 2, eSide::TOP, -80});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(boxFor(r.mesh, 2).height, 230);
    mesh                    = row();
    mesh.faces[1].box.width = 50;
    mesh.faces[2].box.x     = 450;
    mesh.faces[2].box.width = 400;
    r                       = resizeFixture(mesh, {0, 1, eSide::RIGHT, 120});
    ASSERT_EQ(r.status, eResizeStatus::APPLIED);
    EXPECT_EQ(boxFor(r.mesh, 2).width, 50);
}
TEST(LuminophoreSpatialResize, InvalidAndStaleRequestsPreserveOriginal) {
    const auto mesh = row();
    EXPECT_EQ(resizeFixture(mesh, {1, 1, eSide::RIGHT, 12}).status, eResizeStatus::STALE);
    EXPECT_EQ(resizeFixture(mesh, {0, 99, eSide::RIGHT, 12}).mesh, mesh);
    EXPECT_EQ(resizeFixture(mesh, {0, 1, eSide::RIGHT, INT64_MIN}).status, eResizeStatus::INVALID);
    EXPECT_EQ(resizeFixture(mesh, {0, 1, eSide::RIGHT, 120, 100, 1}).status, eResizeStatus::RESOURCE_LIMIT);
    EXPECT_EQ(resizeFixture(mesh, {0, 1, eSide::LEFT, -10}).status, eResizeStatus::NO_CHANGE);
}
TEST(LuminophoreSpatialResize, RandomRectangularTilingsRemainCompleteAndBounded) {
    std::mt19937 rng(42);
    SBoard       b{.view = {{0, 0}, 4, 4}};
    for (int y = 0; y < 4; ++y)
        for (int x = 0; x < 4; ++x)
            b.tiled[{x, y}] = 1 + y * 4 + x;
    auto mesh = buildMesh(b, 1200, 1000).mesh;
    for (int i = 0; i < 1000; ++i) {
        const auto& f    = mesh.faces[rng() % mesh.faces.size()];
        const auto  side = static_cast<eSide>(rng() % 4);
        const auto  r    = resizeFixture(mesh, {mesh.revision, f.id, side, static_cast<int64_t>(rng() % 401) - 200, 100, 10000});
        ASSERT_NE(r.status, eResizeStatus::INVALID) << i;
        ASSERT_NE(r.status, eResizeStatus::RESOURCE_LIMIT) << i;
        if (r.status == eResizeStatus::APPLIED) {
            ASSERT_TRUE(validateMesh(r.mesh)) << i;
            mesh = r.mesh;
        }
    }
}
TEST(LuminophoreSpatialResize, FilledInternalEdgesAreNotWindowHandles) {
    SBoard b{.view = {{0, 0}, 2, 2}};
    b.tiled   = {{{0, 0}, 1}, {{1, 0}, 2}};
    auto mesh = buildMesh(b, 800, 600).mesh;
    auto fill = computeOwnership(b, 1);
    EXPECT_EQ(solveWindowResize(mesh, fill, 1, {0, 1, eSide::BOTTOM, 50}).status, eResizeStatus::INVALID);
    EXPECT_EQ(solveWindowResize(mesh, fill, 2, {0, 1, eSide::RIGHT, 50}).status, eResizeStatus::INVALID);
    EXPECT_EQ(solveWindowResize(mesh, fill, 1, {0, 3, eSide::RIGHT, 50}).status, eResizeStatus::APPLIED);
}
TEST(LuminophoreSpatialResize, SplitAndMergeRepeatedMovesDoNotAccumulateUnchangedSlabs) {
    auto mesh = row();
    for (int i = 0; i < 100; ++i) {
        const auto r = resizeFixture(mesh, {mesh.revision, 1, eSide::RIGHT, i % 2 ? -10 : 10});
        ASSERT_EQ(r.status, eResizeStatus::APPLIED);
        mesh = r.mesh;
        EXPECT_EQ(mesh.faces.size(), 3);
    }
    EXPECT_EQ(boxFor(mesh, 1).width, 400);
}
TEST(LuminophoreSpatialResize, PinwheelAdjacencyDoesNotCreateRecursivePressureCycles) {
    const SMesh mesh{{{0, 0}, 3, 3},
                     300,
                     300,
                     0,
                     {
                         {1, {{0, 0}, 1, 1}, {0, 0, 200, 100}, true},
                         {2, {{2, 0}, 1, 1}, {200, 0, 100, 200}, true},
                         {3, {{2, 2}, 1, 1}, {100, 200, 200, 100}, true},
                         {4, {{0, 2}, 1, 1}, {0, 100, 100, 200}, true},
                         {5, {{1, 1}, 1, 1}, {100, 100, 100, 100}, true},
                     }};
    ASSERT_TRUE(validateMesh(mesh));
    const auto result = resizeFixture(mesh, {0, 1, eSide::BOTTOM, 50, 40});
    ASSERT_EQ(result.status, eResizeStatus::APPLIED);
    EXPECT_EQ(result.effectiveDelta, 50);
    EXPECT_TRUE(validateMesh(result.mesh));
}
TEST(LuminophoreSpatialResize, NoSlackStopsWithoutChangingMeshOrRevision) {
    auto       mesh   = row();
    const auto result = resizeFixture(mesh, {0, 1, eSide::RIGHT, 100, 1000});
    EXPECT_EQ(result.status, eResizeStatus::NO_CHANGE);
    EXPECT_EQ(result.mesh, mesh);
}

TEST(LuminophoreSpatialResize, ClientMinimumClampsBoundingBoxWithoutWorseningExistingUndersize) {
    SBoard board{.view = {{0, 0}, 3, 1}};
    board.tiled     = {{{0, 0}, 1}, {{1, 0}, 2}, {{2, 0}, 3}};
    const auto fill = computeOwnership(board, 1);
    ASSERT_EQ(fill.status, eFillStatus::OK);
    const auto mesh   = row();
    const auto result = solveWindowResize(mesh, fill, 1, {0, 1, eSide::RIGHT, 500}, {{2, {140, 100}}, {3, {250, 100}}});
    ASSERT_EQ(result.status, eResizeStatus::APPLIED);
    EXPECT_EQ(result.effectiveDelta, 60);
    EXPECT_EQ(boxFor(result.mesh, 2).width, 140);
    EXPECT_EQ(boxFor(result.mesh, 3).width, 250);
    const auto undersized = solveWindowResize(mesh, fill, 1, {0, 1, eSide::RIGHT, 500}, {{2, {200, 100}}, {3, {350, 100}}});
    EXPECT_EQ(undersized.status, eResizeStatus::NO_CHANGE);
    EXPECT_EQ(undersized.mesh, mesh);
}

TEST(LuminophoreSpatialResize, FilledColumnsResizeAsWholeRectanglesPastMidpoint) {
    SBoard board{.view = {{0, 0}, 2, 3}};
    board.tiled       = {{{0, 0}, 1}, {{1, 0}, 2}};
    auto       mesh   = buildMesh(board, 1200, 900).mesh;
    const auto fill   = computeOwnership(board, 1);
    const auto result = solveWindowResize(mesh, fill, 1, {mesh.revision, mesh.faces.front().id, eSide::RIGHT, 400});
    ASSERT_EQ(result.status, eResizeStatus::APPLIED);
    EXPECT_EQ(result.effectiveDelta, 400);
    const auto regions = regionsForOwnership(result.mesh, fill);
    ASSERT_TRUE(regions);
    ASSERT_EQ(regions->size(), 2);
    EXPECT_EQ(regions->at(0).boxes, (std::vector<SBox>{{0, 0, 1000, 900}}));
    EXPECT_EQ(regions->at(1).boxes, (std::vector<SBox>{{1000, 0, 200, 900}}));
}

TEST(LuminophoreSpatialResize, WholeBoundaryMovesTJunctionButNotSeparateRows) {
    SBoard board{.view = {{0, 0}, 2, 2}};
    board.tiled       = {{{0, 0}, 1}, {{1, 0}, 2}, {{1, 1}, 3}};
    auto       mesh   = buildMesh(board, 1200, 800).mesh;
    const auto fill   = computeOwnership(board, std::nullopt);
    const auto result = solveWindowResize(mesh, fill, 2, {mesh.revision, 2, eSide::LEFT, -200});
    ASSERT_EQ(result.status, eResizeStatus::APPLIED);
    auto regions = regionsForOwnership(result.mesh, fill);
    ASSERT_TRUE(regions);
    EXPECT_EQ(regions->at(0).boxes, (std::vector<SBox>{{0, 0, 400, 800}}));
    EXPECT_EQ(regions->at(1).boxes, (std::vector<SBox>{{400, 0, 800, 400}}));
    EXPECT_EQ(regions->at(2).boxes, (std::vector<SBox>{{400, 400, 800, 400}}));
    board.tiled[{0, 1}]  = 4;
    mesh                 = buildMesh(board, 1200, 800).mesh;
    const auto splitFill = computeOwnership(board, std::nullopt);
    const auto split     = solveWindowResize(mesh, splitFill, 1, {mesh.revision, 1, eSide::RIGHT, 200});
    ASSERT_EQ(split.status, eResizeStatus::APPLIED);
    regions = regionsForOwnership(split.mesh, splitFill);
    ASSERT_TRUE(regions);
    EXPECT_EQ(regions->at(2).boxes, (std::vector<SBox>{{600, 400, 600, 400}}));
    EXPECT_EQ(regions->at(3).boxes, (std::vector<SBox>{{0, 400, 600, 400}}));
}

TEST(LuminophoreSpatialResize, RandomWindowBoundariesKeepRectanglesInBothAxesAndSigns) {
    std::mt19937 rng(431);
    for (int trial = 0; trial < 100; ++trial) {
        SBoard board{.view = {{0, 0}, 3, 3}};
        for (int y = 0; y < 3; ++y)
            for (int x = 0; x < 3; ++x)
                if (rng() % 2)
                    board.tiled[{x, y}] = y * 3 + x + 1;
        const auto fill    = computeOwnership(board, 1);
        const auto mesh    = buildMesh(board, 900, 900, 1000, 1).mesh;
        const auto regions = regionsForOwnership(mesh, fill);
        ASSERT_TRUE(regions);
        for (const auto& region : *regions) {
            for (auto side : {eSide::LEFT, eSide::RIGHT, eSide::TOP, eSide::BOTTOM}) {
                const bool vertical = side == eSide::TOP || side == eSide::BOTTOM;
                const bool low      = side == eSide::LEFT || side == eSide::TOP;
                const auto edge     = [&](SBox b) { return vertical ? b.y + (low ? 0 : b.height) : b.x + (low ? 0 : b.width); };
                FaceID     handle   = 0;
                for (const auto& face : mesh.faces)
                    for (const auto& claim : fill.rectangles)
                        if (claim.owner == region.owner && claim.logical.contains(face.provenance.origin) && edge(face.box) == edge(region.boxes.front()))
                            handle = face.id;
                ASSERT_NE(handle, 0);
                for (int delta : {-1000, -77, 77, 1000}) {
                    auto result = solveWindowResize(mesh, fill, region.owner, {mesh.revision, handle, side, delta});
                    ASSERT_TRUE(result.status == eResizeStatus::APPLIED || result.status == eResizeStatus::NO_CHANGE) << trial;
                    ASSERT_TRUE(validateMesh(result.mesh));
                    const auto after = regionsForOwnership(result.mesh, fill);
                    ASSERT_TRUE(after);
                    EXPECT_EQ(after->size(), regions->size());
                    for (const auto& r : *after) {
                        EXPECT_EQ(r.boxes.size(), 1);
                        EXPECT_GE(r.boxes.front().width, 100);
                        EXPECT_GE(r.boxes.front().height, 100);
                    }
                }
            }
        }
    }
}
