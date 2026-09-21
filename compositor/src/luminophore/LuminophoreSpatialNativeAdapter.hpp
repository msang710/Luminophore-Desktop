#pragma once
#include "LuminophoreSpatialWindowRegistry.hpp"
namespace Luminophore::SpatialNative {
    void applyOutputOverrides(SLuminophoreSpatialCommit& commit, eLuminophorePresentationMode mode, std::optional<LuminophoreWindowKey> wideKey, const CLuminophoreSpatialWindowRegistry& registry,
                              bool preserveHidden = false);
    void releaseSpatialInput(const PHLWINDOW& window);
    void focus(const PHLWINDOW& window);
    void clearClientPointerFocus();
    void damageOutputs();
    class CResolvedBatch {
      public:
        static std::optional<CResolvedBatch> prepare(const SLuminophoreSpatialCommit& commit, const CLuminophoreSpatialWindowRegistry& registry);
        bool apply(const std::vector<SLuminophoreSpatialCommitEntry>& entries, uint64_t revision, const std::map<LuminophoreWindowKey, SLuminophorePhysicalBox>& starts = {}) const;

      private:
        struct SResolvedTarget {
            SP<Layout::ITarget> target;
            PHLWINDOW           window;
        };
        std::map<LuminophoreWindowKey, SResolvedTarget> m_targets;
        std::map<uint64_t, PHLMONITOR>           m_monitors;
    };
}
