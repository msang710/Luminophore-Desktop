#pragma once

#include "../desktop/DesktopTypes.hpp"

#include <cstdint>
#include <deque>
#include <string>
#include <unordered_set>

namespace Luminophore {

    enum eOccupyOutputAction : uint8_t {
        OCCUPY_OUTPUT_TOGGLE = 0,
        OCCUPY_OUTPUT_SET,
        OCCUPY_OUTPUT_UNSET,
    };

    class CLuminophoreOccupyOutputController {
      public:
        bool apply(const PHLWINDOW& window, eOccupyOutputAction action, const std::string& serial);

      private:
        static constexpr size_t         MAX_SEEN_REQUESTS = 256;

        std::string                     requestKey(const PHLWINDOW& window, const std::string& serial) const;
        bool                            rememberRequest(const std::string& key);

        std::deque<std::string>         m_seenRequestOrder;
        std::unordered_set<std::string> m_seenRequests;
    };

    UP<CLuminophoreOccupyOutputController>& occupyOutputController();

}
