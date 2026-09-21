#pragma once

#include <hyprutils/math/Vector2D.hpp>
#include <optional>

#include <cstdint>
#include <chrono>

namespace Luminophore {

    struct SDragIntentSnapshot {
        uint64_t generation = 0;
        bool     shake      = false;
    };

    class CLuminophoreDragIntentController {
      public:
        void                       begin(std::chrono::steady_clock::time_point now = std::chrono::steady_clock::now());
        void                       update(const Hyprutils::Math::Vector2D& pointer, bool eligible, std::chrono::steady_clock::time_point now = std::chrono::steady_clock::now());
        void                       cancel();

        const SDragIntentSnapshot& snapshot() const;

      private:
        SDragIntentSnapshot                      m_snapshot;
        uint64_t                                 m_nextGeneration = 0;
        std::optional<Hyprutils::Math::Vector2D> m_lastPointer;
        int                                      m_lastHorizontalDirection = 0;
        int                                      m_horizontalReversals     = 0;
        double                                   m_horizontalTravel        = 0.0;
        std::chrono::steady_clock::time_point    m_shakeStarted;
    };

}
