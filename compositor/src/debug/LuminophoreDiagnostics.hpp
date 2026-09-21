#pragma once

#include "../SharedDefs.hpp"

#include <chrono>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

namespace Hyprutils::Math {
    class CBox;
}

namespace Render {

    enum eLuminophoreFrameReason : uint8_t {
        LUMINOPHORE_FRAME_WINDOW_GLOW_ACTIVE = 0,
        LUMINOPHORE_FRAME_WINDOW_MODE_TRANSITION,
        LUMINOPHORE_FRAME_SHELL_OVERLAY_VISIBLE,
        LUMINOPHORE_FRAME_SHELL_TRANSITION,
    };

    struct SLuminophoreFrameEvent {
        MONITORID        monitor       = -1;
        uint64_t         sequence      = 0;
        eLuminophoreFrameReason reason        = LUMINOPHORE_FRAME_WINDOW_GLOW_ACTIVE;
        int64_t          requestedAtUs = 0;
        int64_t          presentedAtUs = 0;
        uint64_t         damageArea    = 0;
        bool             coalesced     = false;
    };

    struct SLuminophoreDiagnosticsSnapshot {
        uint64_t enqueued       = 0;
        uint64_t drawn          = 0;
        uint64_t frameRequests  = 0;
        uint64_t shaderFailures = 0;
        uint64_t skipped        = 0;
    };

    class CLuminophoreDiagnostics {
      public:
        void                         recordEnqueued();
        void                         recordDrawn();
        uint64_t                     recordFrameRequest(MONITORID monitor, eLuminophoreFrameReason reason, const Hyprutils::Math::CBox& damage, bool coalesced = false);
        void                         recordPresented(MONITORID monitor);
        void                         recordShaderFailure();
        void                         recordSkipped();

        SLuminophoreDiagnosticsSnapshot     snapshot() const;
        std::vector<SLuminophoreFrameEvent> frameEvents() const;

      private:
        static constexpr size_t     MAX_FRAME_EVENTS = 256;
        SLuminophoreDiagnosticsSnapshot    m_counters;
        std::deque<SLuminophoreFrameEvent> m_frameEvents;
        uint64_t                    m_nextSequence = 1;
    };

}
