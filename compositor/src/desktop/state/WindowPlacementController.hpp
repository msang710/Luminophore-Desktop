#pragma once

#include "../DesktopTypes.hpp"
#include "../../helpers/math/Math.hpp"
#include "../../helpers/memory/Memory.hpp"

#include <cstdint>
#include <optional>

namespace Desktop {

    enum eWindowPlacement : uint8_t {
        WINDOW_PLACEMENT_TILED = 0,
        WINDOW_PLACEMENT_FLOATING,
        WINDOW_PLACEMENT_MINIMIZED,
    };

    struct SWindowRestorePlacement {
        PHLWORKSPACEREF workspace;
        PHLMONITORREF   monitor;
        CBox            floatingBox;
        bool            wasFloating = false;
        uint64_t        revision    = 0;
    };

    class CWindowPlacementController {
      public:
        bool                                          minimize(const PHLWINDOW& window);
        bool                                          restore(const PHLWINDOW& window, std::optional<Vector2D> focalPoint = std::nullopt);
        bool                                          rehomeMinimized(const PHLWINDOW& window, const PHLWORKSPACE& workspace);

        eWindowPlacement                              placement(const PHLWINDOW& window) const;
        const std::optional<SWindowRestorePlacement>& restorePlacement(const PHLWINDOW& window) const;

      private:
        uint64_t m_revision = 0;
    };

    UP<CWindowPlacementController>& windowPlacementController();
}
