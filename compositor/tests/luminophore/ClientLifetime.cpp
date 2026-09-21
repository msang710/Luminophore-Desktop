#include "../../src/luminophore/LuminophoreClientLifetime.hpp"
#include <gtest/gtest.h>
#include <sys/socket.h>
#include <unistd.h>
#include <wayland-server-protocol.h>

TEST(LuminophoreClientLifetime, SurfaceReplacementDoesNotDisconnectAndOldClientCannotRebind) {
    auto display = wl_display_create();
    ASSERT_NE(display, nullptr);
    int sockets[2] = {-1, -1};
    ASSERT_EQ(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets), 0);
    auto client = wl_client_create(display, sockets[0]);
    ASSERT_NE(client, nullptr);
    int                   disconnects = 0;
    Luminophore::CClientLifetime lifetime;
    ASSERT_TRUE(lifetime.bind(client, [&] { ++disconnects; }));
    auto surface = wl_resource_create(client, &wl_surface_interface, 1, 0);
    ASSERT_NE(surface, nullptr);
    wl_resource_destroy(surface);
    EXPECT_FALSE(lifetime.lost());
    EXPECT_EQ(disconnects, 0);
    surface = wl_resource_create(client, &wl_surface_interface, 1, 0);
    EXPECT_TRUE(lifetime.bind(client, [&] { ++disconnects; }));
    wl_client_destroy(client);
    EXPECT_TRUE(lifetime.lost());
    EXPECT_EQ(disconnects, 1);
    EXPECT_FALSE(lifetime.accepts(nullptr));
    close(sockets[1]);
    ASSERT_EQ(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets), 0);
    client = wl_client_create(display, sockets[0]);
    ASSERT_NE(client, nullptr);
    EXPECT_FALSE(lifetime.bind(client, [] {}));
    lifetime.reset();
    EXPECT_TRUE(lifetime.bind(client, [&] { ++disconnects; }));
    lifetime.reset();
    wl_client_destroy(client);
    EXPECT_EQ(disconnects, 1);
    close(sockets[1]);
    wl_display_destroy(display);
}
