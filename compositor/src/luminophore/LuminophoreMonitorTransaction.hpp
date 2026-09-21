#pragma once

#include "../desktop/DesktopTypes.hpp"
#include "../SharedDefs.hpp"
#include "../helpers/math/Math.hpp"

#include <cstdint>
#include <unordered_map>

namespace Fullscreen {
    struct SFullscreenMode;
}

namespace Luminophore {

    enum eWindowPresentationMode : uint8_t {
        WINDOW_PRESENTATION_TILED = 0,
        WINDOW_PRESENTATION_FLOATING,
        WINDOW_PRESENTATION_OCCUPY_OUTPUT,
    };

    struct SWindowPresentationState {
        eWindowPresentationMode mode = WINDOW_PRESENTATION_TILED;
        PHLMONITORREF           monitor;
        CBox                    restoreBox;
        uint64_t                revision      = 0;
        int8_t                  clientMode    = 0;
        bool                    layoutManaged = false;
    };

    struct SMonitorFrameTransaction {
        uint64_t requestedRevision = 0;
        uint64_t committedRevision = 0;
        uint64_t presentedRevision = 0;
    };

    class CLuminophoreMonitorTransaction {
      public:
        uint64_t                 requestFrame(PHLMONITOR monitor);
        void                     beginFrame(PHLMONITOR monitor);
        void                     presented(PHLMONITOR monitor);

        void                     syncWindowState(PHLWINDOW window, int8_t internalMode, int8_t clientMode, bool layoutManaged);
        void                     commitSpatialState(PHLWINDOW window, PHLMONITOR monitor, uint64_t revision);
        SWindowPresentationState windowState(PHLWINDOW window) const;
        SMonitorFrameTransaction monitorState(PHLMONITOR monitor) const;

      private:
        std::unordered_map<MONITORID, SMonitorFrameTransaction>    m_monitors;
        std::unordered_map<PHLWINDOWREF, SWindowPresentationState> m_windows;
    };

    UP<CLuminophoreMonitorTransaction>& monitorTransaction();

}
