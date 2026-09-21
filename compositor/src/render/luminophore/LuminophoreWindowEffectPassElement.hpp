#pragma once

#include "../pass/PassElement.hpp"

namespace Render {

    class CLuminophoreWindowEffect;

    class CLuminophoreWindowEffectPassElement final : public IPassElement {
      public:
        struct SData {
            CLuminophoreWindowEffect*     owner = nullptr;
            CBox                   box;
            CHyprColor             color;
            float                  rounding      = 0.F;
            float                  roundingPower = 2.F;
            int                    bloomRange    = 1;
            int                    nearRange     = 1;
            int                    glowPower     = 3;
            float                  bloomAlpha    = 0.F;
            float                  nearAlpha     = 0.F;
            bool                   renderMask    = false;
            std::optional<CRegion> spatialClip;
        };

        explicit CLuminophoreWindowEffectPassElement(const SData& data);

        std::vector<UP<IPassElement>> draw() override;
        bool                          needsLiveBlur() override;
        bool                          needsPrecomputeBlur() override;
        const char*                   passName() override;
        ePassElementType              type() override;
        std::optional<CBox>           boundingBox() override;

      private:
        SData m_data;
    };

}
