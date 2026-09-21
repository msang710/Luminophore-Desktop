#pragma once

#include "../desktop/DesktopTypes.hpp"
#include "../helpers/memory/Memory.hpp"

namespace Luminophore {

    class CLuminophoreWindowGestureController {
      public:
        bool minimizeOthers(const PHLWINDOW& anchor);
        bool minimize(const PHLWINDOW& window);
        bool restore(const PHLWINDOW& window);
    };

    UP<CLuminophoreWindowGestureController>& windowGestureController();
}
