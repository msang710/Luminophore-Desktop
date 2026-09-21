#pragma once
#include "LuminophoreSpatialCommitter.hpp"
#include "../desktop/DesktopTypes.hpp"
#include "../helpers/memory/Memory.hpp"
#include <map>
#include <set>
namespace Layout {
    class ITarget;
}
namespace Luminophore {
    enum class eSpatialParticipation : uint8_t {
        ABSENT,
        BOARD_ROOT,
        PARENT_ATTACHED,
        EXTERNAL_OVERLAY,
    };

    struct SSpatialParticipationFacts {
        bool mapped           = false;
        bool hasWorkspace     = false;
        bool hasParent        = false;
        bool modal            = false;
        bool pinned           = false;
        bool workspaceOverlay = false;
        bool nativeAuxiliary  = false;
    };

    class CLuminophoreSpatialWindowRegistry {
      public:
        static eSpatialParticipation           classifyParticipation(const SSpatialParticipationFacts& facts);
        static eSpatialParticipation           participationFor(const SP<Layout::ITarget>& target);
        SP<Layout::ITarget>                    targetFor(LuminophoreWindowKey key) const;
        void                                   remember(LuminophoreWindowKey key, const SP<Layout::ITarget>& target);
        void                                   forget(LuminophoreWindowKey key);
        void                                   clearMotion(LuminophoreWindowKey key);
        void                                   beginMotion(LuminophoreWindowKey key, SLuminophorePhysicalBox box);
        bool                                   updateMotion(LuminophoreWindowKey key, SLuminophorePhysicalBox box);
        void                                   endMotion(LuminophoreWindowKey key);
        bool                                   activeMotion(LuminophoreWindowKey key) const;
        bool                                   hasMotion(LuminophoreWindowKey key) const;
        std::optional<SLuminophorePhysicalBox>        takeMotion(LuminophoreWindowKey key);
        void                                   restoreMotion(LuminophoreWindowKey key, SLuminophorePhysicalBox box);
        std::optional<SLuminophoreSpatialCommitEntry> presentation(LuminophoreWindowKey key, SLuminophoreBoardPoint host, const std::vector<SLuminophorePhysicalOutput>& outputs) const;
        uint64_t                               generation() const;

      private:
        std::map<LuminophoreWindowKey, WP<Layout::ITarget>> m_targets;
        std::map<LuminophoreWindowKey, SLuminophorePhysicalBox>    m_motion;
        std::set<LuminophoreWindowKey>                      m_activeMotion;
        uint64_t                                     m_generation = 0;
    };
}
