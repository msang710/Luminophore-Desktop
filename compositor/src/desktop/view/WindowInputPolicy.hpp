#pragma once

#include <cstdint>

namespace Desktop::View {
    // Evaluate the remaining reasons, not the reason passed to the last update.
    bool inputBlockRevokesFocus(uint32_t remainingReasons, bool desktopExposed);
}
