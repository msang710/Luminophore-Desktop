#include "../../src/Compositor.hpp"
#include "../../src/protocols/XDGActivation.hpp"
#include "../../src/protocols/core/Compositor.hpp"
#include "../../src/protocols/PresentationTime.hpp"
#include "../../src/managers/TokenManager.hpp"
#include <gtest/gtest.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cstring>

// Exercise the generated Wayland dispatchers over a real, isolated socket.
TEST(LuminophoreCausalActivation, DestroyedTokenObjectStillActivatesOnce) {
    EXPECT_EXIT(([] {
                    g_pCompositor              = makeUnique<CCompositor>(true);
                    g_pCompositor->m_wlDisplay = wl_display_create();
                    g_pTokenManager            = makeUnique<CTokenManager>();
                    PROTO::presentation        = makeUnique<CPresentationProtocol>(&wp_presentation_interface, 1, "presentation-test");
                    PROTO::activation          = makeUnique<CXDGActivationProtocol>(&xdg_activation_v1_interface, 1, "activation-test");
                    int sockets[2];
                    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets))
                        _exit(1);
                    auto client = wl_client_create(g_pCompositor->m_wlDisplay, sockets[0]);
                    PROTO::activation->bindManager(client, nullptr, 1, 2);
                    auto pump = [&](std::vector<uint32_t> words) {
                        if (write(sockets[1], words.data(), words.size() * 4) != static_cast<ssize_t>(words.size() * 4))
                            _exit(2);
                        wl_event_loop_dispatch(wl_display_get_event_loop(g_pCompositor->m_wlDisplay), 0);
                        wl_display_flush_clients(g_pCompositor->m_wlDisplay);
                    };
                    pump({2, (12U << 16) | 1U, 3}); // get_activation_token
                    pump({3, (8U << 16) | 3U});     // commit
                    char bytes[256]{};
                    auto n = recv(sockets[1], bytes, sizeof(bytes), MSG_DONTWAIT);
                    if (n < 13)
                        _exit(3);
                    uint32_t id = 0, length = 0;
                    memcpy(&id, bytes, 4);
                    memcpy(&length, bytes + 8, 4);
                    if (id != 3 || length < 2 || length > 128 || 12 + length > static_cast<uint32_t>(n))
                        _exit(4);
                    std::string token(bytes + 12, length - 1);
                    if (!g_pTokenManager->getToken(token))
                        _exit(5);
                    pump({3, (8U << 16) | 4U}); // destroy request object
                    if (!g_pTokenManager->getToken(token))
                        _exit(6);
                    auto surface                 = makeShared<CWLSurfaceResource>(makeShared<CWlSurface>(client, 1, 4));
                    surface->m_self              = surface;
                    const auto            padded = (length + 3) / 4;
                    std::vector<uint32_t> activate(4 + padded);
                    activate[0] = 2;
                    activate[1] = (activate.size() * 4U << 16) | 2U;
                    activate[2] = length;
                    memcpy(activate.data() + 3, token.c_str(), length);
                    activate.back() = 4;
                    pump(activate);
                    if (g_pTokenManager->getToken(token))
                        _exit(7);
                    // Missing source metadata and token replay must not produce a placement origin.
                    if (PROTO::activation->takePlacementSource(surface, "app"))
                        _exit(8);
                    pump(activate);
                    if (PROTO::activation->takePlacementSource(surface, "app"))
                        _exit(9);
                    _exit(0);
                }()),
                ::testing::ExitedWithCode(0), "");
}
