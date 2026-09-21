#include "tests.hpp"
#include "../../hyprctlCompat.hpp"
#include "../../shared.hpp"

TEST_CASE(retiredWorkspaceDispatchers) {
    for (const auto* command : {"/dispatch workspace 1", "/dispatch movetoworkspace 2", "/dispatch renameworkspace 1 code", "/dispatch swapactiveworkspaces 0 1"})
        ASSERT(getFromSocket(command) != "ok", true);
}
