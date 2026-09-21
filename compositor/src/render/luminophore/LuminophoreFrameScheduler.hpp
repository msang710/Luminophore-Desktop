#pragma once

#include "../../defines.hpp"

#include <chrono>
#include <unordered_map>

class CEventLoopTimer;

namespace Render {

    class CLuminophoreDiagnostics;

    class CLuminophoreFrameScheduler {
      public:
        explicit CLuminophoreFrameScheduler(CLuminophoreDiagnostics* diagnostics);
        ~CLuminophoreFrameScheduler();

        void track(PHLWINDOW window, PHLMONITOR monitor, const CBox& damage);
        void presented(PHLMONITOR monitor);

      private:
        struct SMonitorAnimation {
            PHLMONITORREF                         monitor;
            PHLWINDOWREF                          window;
            CBox                                  damage;
            CBox                                  previousDamage;
            std::chrono::steady_clock::time_point lastSeen;
            SP<CEventLoopTimer>                   timer;
        };

        void                                             requestFrame(MONITORID monitorID);
        static CBox                                      unionBoxes(const CBox& first, const CBox& second);

        CLuminophoreDiagnostics*                                m_diagnostics = nullptr;
        std::unordered_map<MONITORID, SMonitorAnimation> m_monitors;
    };

}
