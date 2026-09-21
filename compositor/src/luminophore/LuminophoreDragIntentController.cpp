#include "LuminophoreDragIntentController.hpp"

#include <cmath>

using namespace Luminophore;
using namespace Hyprutils::Math;

void CLuminophoreDragIntentController::begin(std::chrono::steady_clock::time_point now) {
    ++m_nextGeneration;
    if (m_nextGeneration == 0)
        ++m_nextGeneration;
    m_snapshot = {.generation = m_nextGeneration};
    m_lastPointer.reset();
    m_lastHorizontalDirection = 0;
    m_horizontalReversals     = 0;
    m_horizontalTravel        = 0.0;
    m_shakeStarted            = now;
}

void CLuminophoreDragIntentController::update(const Vector2D& pointer, bool eligible, std::chrono::steady_clock::time_point now) {
    if (!m_snapshot.generation)
        return;
    if (!eligible || now - m_shakeStarted > std::chrono::milliseconds{700}) {
        m_shakeStarted            = now;
        m_horizontalReversals     = 0;
        m_horizontalTravel        = 0.0;
        m_lastHorizontalDirection = 0;
        m_lastPointer.reset();
    }
    if (eligible && m_lastPointer) {
        const auto delta = pointer - *m_lastPointer;
        if (std::abs(delta.x) >= 8.0 && std::abs(delta.x) > std::abs(delta.y) * 1.4) {
            const int direction = delta.x > 0 ? 1 : -1;
            m_horizontalTravel += std::abs(delta.x);
            if (m_lastHorizontalDirection != 0 && direction != m_lastHorizontalDirection)
                ++m_horizontalReversals;
            m_lastHorizontalDirection = direction;
        }
        if (m_horizontalReversals >= 3 && m_horizontalTravel >= 320.0)
            m_snapshot.shake = true;
    }
    if (eligible)
        m_lastPointer = pointer;
}

void CLuminophoreDragIntentController::cancel() {
    m_snapshot = {};
    m_lastPointer.reset();
}

const SDragIntentSnapshot& CLuminophoreDragIntentController::snapshot() const {
    return m_snapshot;
}
