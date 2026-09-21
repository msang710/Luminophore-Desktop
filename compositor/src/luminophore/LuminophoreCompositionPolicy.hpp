#pragma once

#include "../desktop/DesktopTypes.hpp"

namespace Luminophore {

    enum eShellProjectionPlane : uint8_t;

    class CLuminophoreCompositionPolicy {
      public:
        bool                  blocksDirectScanout(PHLMONITOR monitor) const;
        eShellProjectionPlane effectiveShellPlane(PHLMONITOR monitor, eShellProjectionPlane requested) const;
        bool                  showsWindowEffect(PHLWINDOW window) const;
    };

    UP<CLuminophoreCompositionPolicy>& compositionPolicy();

}
