#include "LuminophoreWindowEffectPassElement.hpp"
#include "LuminophoreWindowEffect.hpp"
#include "../Renderer.hpp"

using namespace Render;

CLuminophoreWindowEffectPassElement::CLuminophoreWindowEffectPassElement(const SData& data) : m_data(data) {
    ;
}

std::vector<UP<IPassElement>> CLuminophoreWindowEffectPassElement::draw() {
    if (m_data.owner)
        m_data.owner->render(m_data);
    return {};
}

bool CLuminophoreWindowEffectPassElement::needsLiveBlur() {
    return false;
}

bool CLuminophoreWindowEffectPassElement::needsPrecomputeBlur() {
    return false;
}

const char* CLuminophoreWindowEffectPassElement::passName() {
    return "CLuminophoreWindowEffectPassElement";
}

ePassElementType CLuminophoreWindowEffectPassElement::type() {
    return EK_CUSTOM;
}

std::optional<CBox> CLuminophoreWindowEffectPassElement::boundingBox() {
    return m_data.box.copy().scale(1.F / g_pHyprRenderer->m_renderData.pMonitor->m_scale).round();
}
