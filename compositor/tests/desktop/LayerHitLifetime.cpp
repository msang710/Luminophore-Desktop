#include "../../src/desktop/state/ViewStateTracker.hpp"
#include <gtest/gtest.h>

class CEmptyViewTracker final : public Desktop::IViewStateTracker {
  public:
    const std::vector<PHLWINDOW>& windows() const override { return m_windows; }
    const std::vector<PHLLS>& layers() const override { return m_layers; }
    const std::vector<PHLVIEWREF>& otherViews() const override { return m_other; }
  private:
    std::vector<PHLWINDOW> m_windows;
    std::vector<PHLLS> m_layers;
    std::vector<PHLVIEWREF> m_other;
};

TEST(LayerHitLifetime, ExpiredLayerDuringClientTeardownIsNotAnInputTarget) {
    CEmptyViewTracker tracker;
    const auto tester = tracker.hitTest();
    std::vector<PHLLSREF> layers(3);
    Vector2D local{17, 23};
    PHLLS found;
    EXPECT_FALSE(tester.layerSurfaceAt({0,0}, &layers, &local, &found));
    EXPECT_FALSE(tester.layerSurfaceAt({0,0}, &layers, &local, &found, true));
    EXPECT_FALSE(tester.layerPopupSurfaceAt({0,0}, &layers, &local, &found));
    EXPECT_FALSE(found);
    EXPECT_EQ(local, (Vector2D{17,23}));
}
