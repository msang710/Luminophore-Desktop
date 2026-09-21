#pragma once
#include "../pass/PassElement.hpp"
#include "../Shader.hpp"
#include <vector>

namespace Render {
    class CLuminophoreSpatialBadgeRenderer {
      public:
        ~CLuminophoreSpatialBadgeRenderer();
        void setMask(std::vector<uint8_t> mask);
        void reset();
        void enqueue(PHLMONITOR monitor, const CBox& globalBox, const CHyprColor& color, float alpha);
        void draw(const CBox& box, const CHyprColor& color, float alpha);

      private:
        SP<CShader>          m_shader;
        GLuint               m_texture = 0;
        bool                 m_dirty   = true;
        std::vector<uint8_t> m_mask;
    };
}
