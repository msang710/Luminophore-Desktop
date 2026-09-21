#pragma once

#include "../helpers/memory/Memory.hpp"

class CEventLoopTimer;

namespace Luminophore {

    class CLuminophoreDisplayIdleController {
      public:
        CLuminophoreDisplayIdleController();
        ~CLuminophoreDisplayIdleController();

        void settingsChanged();
        void physicalActivity();
        void inhibitorChanged();

      private:
        void                arm();
        void                expire();
        bool                inhibited() const;

        SP<CEventLoopTimer> m_timer;
    };

    UP<CLuminophoreDisplayIdleController>& displayIdleController();

}
