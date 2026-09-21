#pragma once

namespace Render {
    // An explicit per-pass kernel bypasses the monitor's global blur cache.
    struct SBlurParameters {
        int   size                                     = 5;
        int   passes                                   = 1;
        float noise                                    = 0.0117F;
        float contrast                                 = 0.8916F;
        float brightness                               = 1.F;
        float vibrancy                                 = 0.1696F;
        float vibrancyDarkness                         = 0.F;
        bool  operator==(const SBlurParameters&) const = default;
    };
}
