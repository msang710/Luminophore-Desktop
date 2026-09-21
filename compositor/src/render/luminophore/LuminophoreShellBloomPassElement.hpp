#pragma once

#include "../pass/PassElement.hpp"
#include <string>

namespace Render {

    class CLuminophoreShellBloom;

    class CLuminophoreShellBloomPassElement final : public IPassElement {
      public:
        struct SData {
            CLuminophoreShellBloom* owner = nullptr;
            std::string      key;
            CBox             tile;
            CBox             panel;
            CHyprColor       color;
            float            radius  = 0.F;
            float            outline = 0.F;
            float            extent  = 0.F;
            float            alpha   = 0.F;
            bool             fadeIn  = true;
        };

        explicit CLuminophoreShellBloomPassElement(const SData& data);

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
