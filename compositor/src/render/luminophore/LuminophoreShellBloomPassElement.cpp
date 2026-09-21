#include "LuminophoreShellBloomPassElement.hpp"
#include "LuminophoreShellBloom.hpp"
#include "../Renderer.hpp"

using namespace Render;

CLuminophoreShellBloomPassElement::CLuminophoreShellBloomPassElement(const SData& data) : m_data(data) {}

std::vector<UP<IPassElement>> CLuminophoreShellBloomPassElement::draw() {
    if (m_data.owner)
        m_data.owner->render(m_data);
    return {};
}

bool CLuminophoreShellBloomPassElement::needsLiveBlur() {
    return false;
}

bool CLuminophoreShellBloomPassElement::needsPrecomputeBlur() {
    return false;
}

const char* CLuminophoreShellBloomPassElement::passName() {
    return "CLuminophoreShellBloomPassElement";
}

ePassElementType CLuminophoreShellBloomPassElement::type() {
    return EK_CUSTOM;
}

std::optional<CBox> CLuminophoreShellBloomPassElement::boundingBox() {
    return m_data.tile.copy().scale(1.F / g_pHyprRenderer->m_renderData.pMonitor->m_scale).round();
}
