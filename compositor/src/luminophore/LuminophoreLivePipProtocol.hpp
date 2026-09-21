#pragma once
#include "LuminophoreLivePipSelection.hpp"
#include <map>

namespace Luminophore {
    struct SPipCommand {
        std::string action, request, instance, output;
        uint64_t    revision = 0;
        CBox        rect;
        double      margin = 0;
    };
    class CLuminophoreLivePipProtocol {
      public:
        static std::optional<SPipCommand> parse(const std::string&);
        std::string                       execute(const SPipCommand&);

      private:
        struct SSession {
            SPipSelectionScene                 scene;
            Time::steady_tp                    started;
            std::optional<SPipSelectionResult> selection;
            std::optional<CBox>                selectedBox;
            std::string                        result;
        };
        std::map<std::string, SSession> m_sessions;
    };
    UP<CLuminophoreLivePipProtocol>& livePipProtocol();
}
