#pragma once
#include "../../luminophore/LuminophoreLivePipMapping.hpp"
#include "../../desktop/DesktopTypes.hpp"
#include "../../helpers/time/Time.hpp"

namespace Render {
    class CLuminophoreLivePipRenderer {
      public:
        static void enqueue(const Luminophore::SLivePipEntry&, const SP<Luminophore::CLuminophoreSurfaceSource>&, PHLMONITOR, const Time::steady_tp&, Vector2D pointer, bool hovered);
        static CBox controlBox(const Luminophore::SLivePipRect&, bool close);
        static CBox decorationBounds(const Luminophore::SLivePipRect&);
        static void discard(uint64_t id);
    };
}
