#pragma once
#include <string>
#include <string_view>

namespace Luminophore::Settings {
    // Read-only scalar validation. Never reports runtime application.
    // Wire: 1 <32 lowercase hex request id> <count> [key type value]...
    // Types i/f/b/s; strings are schema enum tokens, not arbitrary text.
    std::string validateRequest(std::string_view request);
}
