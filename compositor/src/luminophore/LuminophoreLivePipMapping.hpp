#pragma once
#include "LuminophoreLivePipModel.hpp"
#include "../helpers/math/Math.hpp"

namespace Luminophore {
    struct SLivePipMapping {
        CBox destination;
        CBox uv;
    };
    // Coordinates are surface-local logical units; UV is in transformed buffer space.
    class CLuminophoreLivePipMapping {
      public:
        static std::optional<SLivePipMapping> sample(const SLivePipEntry&, const SSurfaceSourceSnapshot&, const SSurfaceState&);
        static std::optional<SLivePipRect>    damage(const SLivePipEntry&, const SLivePipRect& sourceDamage);
    };
}
