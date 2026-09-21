#pragma once
#include "LuminophoreSpatialResize.hpp"

namespace Luminophore::Spatial {
    // Captures an exposed edge for one gesture; ownership changes retire it.
    class CResizeGesture {
      public:
        void                          begin(bool active);
        bool                          active() const;
        bool                          terminated() const;
        std::optional<SResizeRequest> select(const SMesh& mesh, const SFill& fill, WindowKey window, double pointerX, double pointerY, eSide side, double delta);

      private:
        bool                  m_active     = false;
        bool                  m_terminated = false;
        std::optional<FaceID> m_horizontal, m_vertical;
    };
}
