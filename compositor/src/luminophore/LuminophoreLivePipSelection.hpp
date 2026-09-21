#pragma once
#include "LuminophoreLivePipMapping.hpp"
#include "../desktop/DesktopTypes.hpp"
#include "../helpers/time/Time.hpp"
#include <functional>

namespace Luminophore {
    struct SPipSourceView {
        SP<CLuminophoreSurfaceSource> source;
        SSurfaceSourceSnapshot state;
        CBox                   box;
    };
    struct SPipSelectionTile {
        CBox   box;
        size_t source = 0;
    };
    struct SPipSelectionScene {
        std::vector<SPipSourceView>    sources;
        std::vector<SPipSelectionTile> tiles;
        std::string                    signature;
    };
    struct SPipSelectionResult {
        SPipSourceView source;
        SLivePipRect   crop;
    };
    class CLuminophoreLivePipSelection {
      public:
        static SPipSelectionScene                 snapshot();
        static std::optional<SPipSelectionResult> resolve(const SPipSelectionScene&, const CBox&);
        static std::vector<CBox>                  partition(const CBox&, const std::vector<CBox>& boundaries);
    };
}
