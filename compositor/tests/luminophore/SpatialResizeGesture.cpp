#include "../../src/luminophore/LuminophoreSpatialResizeGesture.hpp"
#include <gtest/gtest.h>
#include <limits>
using namespace Luminophore::Spatial;
TEST(LuminophoreSpatialResizeGesture, CapturedOwnershipCannotTransferToAnotherWindow) {
    SBoard board{.view = {{0, 0}, 2, 1}};
    board.tiled         = {{{0, 0}, 1}, {{1, 0}, 2}};
    const auto     mesh = buildMesh(board, 800, 600).mesh;
    CResizeGesture gesture;
    gesture.begin(true);
    const auto request = gesture.select(mesh, computeOwnership(board, 1), 1, 399, 300, eSide::RIGHT, 20);
    ASSERT_TRUE(request);
    std::swap(board.tiled.at({0, 0}), board.tiled.at({1, 0}));
    EXPECT_FALSE(gesture.select(mesh, computeOwnership(board, 1), 1, 399, 300, eSide::RIGHT, 20));
    EXPECT_TRUE(gesture.terminated());
    EXPECT_FALSE(gesture.select(mesh, computeOwnership(board, 2), 2, 399, 300, eSide::RIGHT, 20));
    gesture.begin(true);
    EXPECT_TRUE(gesture.select(mesh, computeOwnership(board, 2), 2, 399, 300, eSide::RIGHT, 20));
}
TEST(LuminophoreSpatialResizeGesture, NonFiniteAndSubpixelEventsDoNotCaptureAnEdge) {
    SBoard board{.view = {{0, 0}, 2, 1}};
    board.tiled         = {{{0, 0}, 1}, {{1, 0}, 2}};
    const auto     mesh = buildMesh(board, 800, 600).mesh;
    CResizeGesture gesture;
    gesture.begin(true);
    EXPECT_FALSE(gesture.select(mesh, computeOwnership(board, 1), 1, 400, 300, eSide::RIGHT, 0.1));
    EXPECT_FALSE(gesture.select(mesh, computeOwnership(board, 1), 1, 400, 300, eSide::RIGHT, std::numeric_limits<double>::infinity()));
    EXPECT_FALSE(gesture.terminated());
    EXPECT_TRUE(gesture.select(mesh, computeOwnership(board, 1), 1, 400, 300, eSide::RIGHT, 20));
}
