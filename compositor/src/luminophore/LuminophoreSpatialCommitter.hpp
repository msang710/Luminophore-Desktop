#pragma once

#include "LuminophoreSpatialProjection.hpp"

#include <cstdint>
#include <functional>
#include <optional>
#include <vector>

struct SLuminophoreSpatialCommitEntry {
    LuminophoreWindowKey                       key             = 0;
    bool                                visible         = false;
    uint64_t                            primaryOutputID = 0;
    SLuminophorePhysicalBox                    clientBox       = {};
    std::vector<SLuminophoreProjectedFragment> fragments       = {};

    // No visible presentation is distinct from a zero-sized application surface.
    std::optional<SLuminophorePhysicalBox> presentationBox() const;
    bool                            operator==(const SLuminophoreSpatialCommitEntry&) const = default;
};

struct SLuminophoreSpatialCommit {
    uint64_t                             modelRevision    = 0;
    uint64_t                             topologyRevision = 0;
    std::vector<SLuminophoreSpatialCommitEntry> entries          = {};

    eLuminophorePresentationMode                presentationMode = eLuminophorePresentationMode::NORMAL;

    bool                                 operator==(const SLuminophoreSpatialCommit&) const = default;
};

class CLuminophoreSpatialCommitter {
  public:
    using TargetExists = std::function<bool(LuminophoreWindowKey)>;
    using OutputExists = std::function<bool(uint64_t)>;
    using TargetWriter = std::function<void(const SLuminophoreSpatialCommitEntry&)>;
    using BatchWriter  = std::function<bool(const std::vector<SLuminophoreSpatialCommitEntry>&)>;

    static std::optional<SLuminophoreSpatialCommit> prepare(const SLuminophoreProjectionPlan& plan);
    static bool                              isApplying();
    bool apply(const SLuminophoreSpatialCommit& commit, const TargetExists& targetExists, const OutputExists& outputExists, const TargetWriter& writer);
    bool applyBatch(const SLuminophoreSpatialCommit& commit, const TargetExists& targetExists, const OutputExists& outputExists, const BatchWriter& writer);
    const std::optional<SLuminophoreSpatialCommit>& presented() const;
    const SLuminophoreSpatialCommit*                effective() const;

  private:
    std::optional<SLuminophoreSpatialCommit> m_presented;
    const SLuminophoreSpatialCommit*         m_applying = nullptr;
};
