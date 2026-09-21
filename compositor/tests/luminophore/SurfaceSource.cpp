#include "../../src/luminophore/LuminophoreSurfaceSource.hpp"
#include "../../src/luminophore/LuminophoreSpatialCommitter.hpp"
#include "../../src/protocols/types/SurfaceState.hpp"
#include <gtest/gtest.h>
#include <limits>

using namespace Luminophore;

TEST(LuminophoreSurfaceSource, PresentationHideDoesNotShrinkCommittedContent) {
    SSurfaceState committed;
    committed.bufferSize = {706, 830};
    SLuminophoreSpatialCommitEntry visible{
        .key = 1, .visible = true, .clientBox = {.x = -1920, .y = 10, .width = 706, .height = 830}, .fragments = {{.key = 1, .outputID = 1, .box = {.width = 706, .height = 830}}}};
    ASSERT_TRUE(visible.presentationBox());
    auto before    = CLuminophoreSurfaceSource::describe(7, 1, true, true, true, committed);
    auto hidden    = visible;
    hidden.visible = false;
    hidden.fragments.clear();
    hidden.clientBox = {};
    EXPECT_FALSE(hidden.presentationBox());
    auto outside = CLuminophoreSurfaceSource::describe(7, 1, true, true, true, committed);
    EXPECT_EQ(outside.extent, before.extent);
    EXPECT_EQ(outside.extent, (SSourceExtent{706, 830}));
    EXPECT_EQ(outside.token, before.token);
    EXPECT_TRUE(visible.presentationBox());
}

TEST(LuminophoreSurfaceSource, PendingResizeIsNotCommittedExtent) {
    SSurfaceState current, pending;
    current.bufferSize = {706, 830};
    pending.size       = {320, 240};
    pending.bufferSize = {320, 240};
    pending.ackedSize  = {1200, 900};
    EXPECT_EQ(CLuminophoreSurfaceSource::describe(1, 1, true, true, true, current).extent, (SSourceExtent{706, 830}));
    pending.updated.bits.buffer = true;
    current.updateFrom(pending);
    EXPECT_EQ(CLuminophoreSurfaceSource::describe(1, 2, true, true, true, current).extent, (SSourceExtent{320, 240}));
}

TEST(LuminophoreSurfaceSource, UsesSurfaceLogicalUnitsAfterTransformAndViewport) {
    for (int transform = 0; transform < 8; ++transform) {
        SSurfaceState current;
        current.bufferSize = {1920, 1080};
        current.scale      = 2;
        current.transform  = static_cast<wl_output_transform>(transform);
        // These are the final sizes committed by wl_surface, including rotated buffers.
        current.size      = transform % 2 ? Vector2D{540, 960} : Vector2D{960, 540};
        const auto extent = CLuminophoreSurfaceSource::describe(1, 1, true, true, true, current).extent;
        ASSERT_TRUE(extent);
        EXPECT_EQ(extent->width, current.size.x);
        EXPECT_EQ(extent->height, current.size.y);
        current.viewport.hasSource = current.viewport.hasDestination = true;
        current.viewport.source                                      = CBox{20.5, 30.25, 400.5, 200.25};
        current.viewport.destination                                 = {301, 151};
        current.size                                                 = current.viewport.destination;
        EXPECT_EQ(CLuminophoreSurfaceSource::describe(1, 2, true, true, true, current).extent, (SSourceExtent{301, 151}));
    }
}

TEST(LuminophoreSurfaceSource, UnmapNullBufferAndDestroyAreDifferentFromOffView) {
    SSurfaceState current;
    current.bufferSize = {706, 830};
    auto unmapped      = CLuminophoreSurfaceSource::describe(1, 2, true, false, true, current);
    EXPECT_TRUE(unmapped.alive);
    EXPECT_FALSE(unmapped.extent);
    auto noBuffer = CLuminophoreSurfaceSource::describe(1, 3, true, true, false, current);
    EXPECT_TRUE(noBuffer.mapped);
    EXPECT_FALSE(noBuffer.extent);
    auto destroyed = CLuminophoreSurfaceSource::describe(1, 4, false, true, true, current);
    EXPECT_FALSE(destroyed.alive);
    EXPECT_FALSE(destroyed.mapped);
    EXPECT_FALSE(destroyed.hasBuffer);
    EXPECT_FALSE(destroyed.extent);
}

TEST(LuminophoreSurfaceSource, InvalidCommittedExtentNeverBecomesAvailable) {
    for (double value : {0., -1., std::numeric_limits<double>::infinity(), std::numeric_limits<double>::quiet_NaN()}) {
        SSurfaceState current;
        current.bufferSize = {value, 830.0};
        EXPECT_FALSE(CLuminophoreSurfaceSource::describe(1, 1, true, true, true, current).extent);
    }
}

// Protocol constructors install process-global listeners. Isolate this fixture.
#include "../../src/Compositor.hpp"
#include "../../src/protocols/core/Compositor.hpp"
#include "../../src/protocols/PresentationTime.hpp"
#include <sys/socket.h>
#include <unistd.h>

class CSourceTestBuffer : public IHLBuffer {
  public:
    Aquamarine::eBufferCapability caps() override {
        return Aquamarine::BUFFER_CAPABILITY_DATAPTR;
    }
    Aquamarine::eBufferType type() override {
        return Aquamarine::BUFFER_TYPE_SHM;
    }
    void update(const CRegion&) override {
        ;
    }
    bool isSynchronous() override {
        return true;
    }
    bool good() override {
        return true;
    }
    void sendRelease() override {
        ;
    }
};

TEST(LuminophoreSurfaceSource, SurfaceLifetimeAndCommitSignalsCannotRebindOldHandle) {
    EXPECT_EXIT(([]() {
                    g_pCompositor              = makeUnique<CCompositor>(true);
                    g_pCompositor->m_wlDisplay = wl_display_create();
                    if (!g_pCompositor->m_wlDisplay)
                        _exit(1);
                    PROTO::presentation = makeUnique<CPresentationProtocol>(&wp_presentation_interface, 1, "presentation-test");
                    int sockets[2]      = {-1, -1};
                    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) != 0)
                        _exit(2);
                    auto client = wl_client_create(g_pCompositor->m_wlDisplay, sockets[0]);
                    if (!client)
                        _exit(3);
                    auto surface          = makeShared<CWLSurfaceResource>(makeShared<CWlSurface>(client, 1, 0));
                    surface->m_self       = surface;
                    surface->m_luminophoreSource = CLuminophoreSurfaceSource::create(surface);
                    auto       handle     = surface->m_luminophoreSource;
                    const auto token      = handle->snapshot().token;
                    if (CLuminophoreSurfaceSource::create(surface)->snapshot().token != token)
                        _exit(9);
                    if (!token || !handle->snapshot().alive || handle->snapshot().extent)
                        _exit(4);
                    surface->m_events.commit.emit();
                    if (handle->snapshot().revision != 2)
                        _exit(5);
                    surface->m_current.buffer     = CHLBufferReference(makeShared<CSourceTestBuffer>());
                    surface->m_current.bufferSize = {706, 830};
                    surface->m_mapped             = true;
                    surface->m_events.commit.emit();
                    const auto initial = handle->snapshot();
                    if (!initial.extent || initial.extent->width != 706 || initial.extentRevision != 2)
                        _exit(10);
                    surface->m_events.commit.emit();
                    if (handle->snapshot().revision != initial.revision + 1 || handle->snapshot().extentRevision != initial.extentRevision)
                        _exit(11);
                    surface->m_pending.bufferSize = {320, 240};
                    if (handle->snapshot().extent != initial.extent)
                        _exit(12);
                    surface->m_current.viewport.hasDestination = true;
                    surface->m_current.viewport.destination    = {320, 240};
                    surface->m_events.commit.emit();
                    if (handle->snapshot().extent->width != 320 || handle->snapshot().extentRevision != initial.extentRevision + 1)
                        _exit(13);
                    surface->m_mapped = false;
                    if (handle->snapshot().extent || !handle->snapshot().alive)
                        _exit(14);
                    surface->m_current.buffer = {};
                    surface->m_events.commit.emit();
                    if (handle->snapshot().hasBuffer || handle->snapshot().extentRevision != initial.extentRevision + 2)
                        _exit(15);
                    surface.reset();
                    if (handle->surface() || handle->snapshot().alive || handle->snapshot().extent)
                        _exit(6);
                    auto replacement          = makeShared<CWLSurfaceResource>(makeShared<CWlSurface>(client, 1, 0));
                    replacement->m_self       = replacement;
                    replacement->m_luminophoreSource = CLuminophoreSurfaceSource::create(replacement);
                    if (replacement->m_luminophoreSource->snapshot().token == token || handle->surface())
                        _exit(7);
                    replacement->m_events.destroy.emit();
                    const auto revision = replacement->m_luminophoreSource->snapshot().revision;
                    replacement->m_events.destroy.emit();
                    if (replacement->m_luminophoreSource->snapshot().alive || replacement->m_luminophoreSource->snapshot().revision != revision)
                        _exit(8);
                    replacement.reset();
                    wl_client_destroy(client);
                    close(sockets[1]);
                    _exit(0);
                }()),
                ::testing::ExitedWithCode(0), "");
}

TEST(LuminophoreSurfaceSource, ViewportAndScaleOnlyCommitDoNotReadStaleCachedSize) {
    SSurfaceState current, pending;
    current.bufferSize              = {1920, 1080};
    current.size                    = {1920, 1080};
    pending.updated.bits.viewport   = true;
    pending.viewport.hasDestination = true;
    pending.viewport.destination    = {640, 360};
    current.updateFrom(pending);
    EXPECT_EQ(current.size, (Vector2D{1920, 1080}));
    EXPECT_EQ(CLuminophoreSurfaceSource::describe(1, 2, true, true, true, current).extent, (SSourceExtent{640, 360}));
    pending.viewport.hasDestination = false;
    pending.updated.bits.scale      = true;
    pending.scale                   = 2;
    current.updateFrom(pending);
    EXPECT_EQ(CLuminophoreSurfaceSource::describe(1, 3, true, true, true, current).extent, (SSourceExtent{960, 540}));
}
