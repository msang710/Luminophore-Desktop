#pragma once

#include "../../defines.hpp"

namespace Render {

    struct SLuminophoreEffectConfig {
        CHyprColor activeColor   = CHyprColor(0.612F, 0.796F, 0.984F, 1.F);
        CHyprColor inactiveColor = CHyprColor(1.F, 0.718F, 0.490F, 1.F);

        float      roundingLogical     = 14.F;
        float      roundingPower       = 2.F;
        float      inactiveIntensity   = 0.82F;
        float      activeIntensityLow  = 0.82F;
        float      activeIntensityHigh = 1.54F;
        float      activeRadiusLow     = 0.96F;
        float      activeRadiusHigh    = 1.06F;
        float      bloomRangeLogical   = 40.F;
        float      nearRangeLogical    = 10.F;
        float      bloomAlpha          = 0.070F;
        float      nearAlpha           = 0.29F;
        float      intensityScale      = 2.6F;
        int        glowPower           = 3;
    };

    const SLuminophoreEffectConfig& luminophoreEffectConfig();

}
