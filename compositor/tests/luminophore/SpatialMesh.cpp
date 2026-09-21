#include "../../src/luminophore/LuminophoreSpatialMesh.hpp"
#include "../../src/luminophore/LuminophoreSpatialResize.hpp"
#include <gtest/gtest.h>
#include <algorithm>
using namespace Luminophore::Spatial;
TEST(LuminophoreSpatialMesh, DiagonalFillRemainsRectangularAndFocusPreservesMesh) {
    SBoard b{.view = {{0, 0}, 2, 2}};
    b.tiled          = {{{0, 0}, 1}, {{1, 1}, 2}};
    const auto built = buildMesh(b, 800, 600);
    ASSERT_EQ(built.status, eMeshStatus::OK);
    ASSERT_TRUE(validateMesh(built.mesh));
    const auto regions = regionsForOwnership(built.mesh, computeOwnership(b, 1));
    ASSERT_TRUE(regions);
    ASSERT_EQ(regions->size(), 2);
    EXPECT_EQ(regions->at(0).boxes.size(), 1);
    const auto changed = regionsForOwnership(built.mesh, computeOwnership(b, 2));
    EXPECT_EQ(built.mesh.revision, 0);
    ASSERT_TRUE(changed);
}
TEST(LuminophoreSpatialMesh, ExpansionAndCropPreserveLocalRatios) {
    SBoard b{.view = {{0, 0}, 2, 1}};
    b.tiled    = {{{0, 0}, 1}, {{1, 0}, 2}};
    auto built = buildMesh(b, 600, 300);
    ASSERT_EQ(built.status, eMeshStatus::OK);
    auto resized = solveWindowResize(built.mesh, computeOwnership(b, built.mesh.fillFocus), 1, {0, 1, eSide::RIGHT, 60});
    ASSERT_EQ(resized.status, eResizeStatus::APPLIED);
    b.view.columns      = 3;
    const auto expanded = reconcileMesh(resized.mesh, b, 900, 300);
    ASSERT_EQ(expanded.status, eMeshStatus::OK);
    EXPECT_FALSE(expanded.reset);
    ASSERT_TRUE(validateMesh(expanded.mesh));
    EXPECT_EQ(expanded.mesh.faces.at(1).box.width, 360);
    b.view.columns     = 2;
    const auto cropped = reconcileMesh(expanded.mesh, b, 600, 300);
    ASSERT_EQ(cropped.status, eMeshStatus::OK);
    EXPECT_FALSE(cropped.reset);
    ASSERT_TRUE(validateMesh(cropped.mesh));
    EXPECT_EQ(cropped.mesh.faces.at(0).box.width, 360);
}
TEST(LuminophoreSpatialMesh, DetectsHolesOverlapsDuplicateIDsAndRoundingFailure) {
    SBoard b{.view = {{0, 0}, 2, 1}};
    b.tiled     = {{{0, 0}, 1}, {{1, 0}, 2}};
    auto result = buildMesh(b, 600, 300);
    auto mesh   = result.mesh;
    mesh.faces[0].box.width += 1;
    EXPECT_FALSE(validateMesh(mesh));
    mesh = result.mesh;
    mesh.faces[0].box.width -= 1;
    EXPECT_FALSE(validateMesh(mesh));
    mesh             = result.mesh;
    mesh.faces[0].id = mesh.faces[1].id;
    EXPECT_FALSE(validateMesh(mesh));
    EXPECT_EQ(buildMesh(b, 1, 300).status, eMeshStatus::UNREPRESENTABLE);
}
TEST(LuminophoreSpatialMesh, FocusAndSwapDoNotResetEditedGeometry) {
    SBoard b{.view = {{0, 0}, 2, 2}};
    b.tiled      = {{{0, 0}, 1}, {{1, 0}, 2}, {{0, 1}, 3}};
    auto mesh    = buildMesh(b, 800, 600).mesh;
    auto resized = solveWindowResize(mesh, computeOwnership(b, mesh.fillFocus), 1, {0, 1, eSide::RIGHT, 50});
    ASSERT_EQ(resized.status, eResizeStatus::APPLIED);
    auto a     = regionsForOwnership(resized.mesh, computeOwnership(b, 3));
    auto other = regionsForOwnership(resized.mesh, computeOwnership(b, 2));
    ASSERT_TRUE(a);
    EXPECT_FALSE(other); // This focus change would bend the edited right-hand window.
    const auto originalFaces = resized.mesh.faces;
    std::swap(b.tiled.at({0, 0}), b.tiled.at({1, 0}));
    const auto reconciled = reconcileMesh(resized.mesh, b, 800, 600);
    ASSERT_EQ(reconciled.status, eMeshStatus::OK);
    EXPECT_FALSE(reconciled.reset);
    for (const auto& f : originalFaces) {
        const auto it = std::ranges::find_if(reconciled.mesh.faces, [&](const auto& next) { return next.provenance == f.provenance; });
        ASSERT_NE(it, reconciled.mesh.faces.end());
        EXPECT_EQ(it->box, f.box);
    }
}

TEST(LuminophoreSpatialMesh, IncompatibleFocusKeepsRectanglesAndEditedRatios) {
    SBoard board{.view = {{0, 0}, 2, 2}};
    board.tiled        = {{{0, 0}, 1}, {{1, 0}, 2}, {{0, 1}, 3}};
    auto       mesh    = buildMesh(board, 800, 600).mesh;
    const auto resized = solveWindowResize(mesh, computeOwnership(board, mesh.fillFocus), 1, {mesh.revision, 1, eSide::RIGHT, 100});
    ASSERT_EQ(resized.status, eResizeStatus::APPLIED);
    const auto changed = reconcileMesh(resized.mesh, board, 800, 600, 1000, 2);
    ASSERT_EQ(changed.status, eMeshStatus::OK);
    EXPECT_FALSE(changed.reset);
    EXPECT_EQ(changed.mesh.faces, resized.mesh.faces);
    EXPECT_EQ(changed.mesh.fillFocus, resized.mesh.fillFocus);
    ASSERT_TRUE(regionsForOwnership(changed.mesh, computeOwnership(board, changed.mesh.fillFocus)));
}
