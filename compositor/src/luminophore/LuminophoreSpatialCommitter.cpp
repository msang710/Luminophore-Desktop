#include "LuminophoreSpatialCommitter.hpp"

#include <algorithm>
#include <hyprutils/utils/ScopeGuard.hpp>
#include <limits>
#include <set>
#include <tuple>

using Hyprutils::Utils::CScopeGuard;

static thread_local size_t        g_spatialCommitDepth = 0;

std::optional<SLuminophoreSpatialCommit> CLuminophoreSpatialCommitter::prepare(const SLuminophoreProjectionPlan& plan) {
    SLuminophoreSpatialCommit commit{
        .modelRevision    = plan.modelRevision,
        .topologyRevision = plan.topologyRevision,
        .presentationMode = plan.presentationMode,
    };
    std::set<LuminophoreWindowKey> keys;
    commit.entries.reserve(plan.windows.size());

    for (const auto& projected : plan.windows) {
        if (projected.key == 0 || !keys.insert(projected.key).second || projected.visible != !projected.fragments.empty())
            return std::nullopt;

        SLuminophoreSpatialCommitEntry entry{.key = projected.key, .visible = projected.visible};
        if (projected.visible) {
            int left        = std::numeric_limits<int>::max();
            int top         = std::numeric_limits<int>::max();
            int right       = std::numeric_limits<int>::min();
            int bottom      = std::numeric_limits<int>::min();
            entry.fragments = projected.fragments;
            std::ranges::sort(entry.fragments,
                              [](const auto& lhs, const auto& rhs) { return std::tie(lhs.box.x, lhs.box.y, lhs.outputID) < std::tie(rhs.box.x, rhs.box.y, rhs.outputID); });
            std::set<uint64_t> outputIDs;
            for (size_t i = 0; i < entry.fragments.size(); ++i) {
                const auto& fragment = entry.fragments[i];
                if (fragment.key != projected.key || fragment.point != projected.point || fragment.outputID == 0 || fragment.box.width <= 0 || fragment.box.height <= 0)
                    return std::nullopt;
                outputIDs.insert(fragment.outputID);
                for (size_t j = 0; j < i; ++j) {
                    const auto& other    = entry.fragments[j].box;
                    const bool  overlaps = fragment.box.x < other.x + other.width && fragment.box.x + fragment.box.width > other.x && fragment.box.y < other.y + other.height &&
                        fragment.box.y + fragment.box.height > other.y;
                    if (overlaps)
                        return std::nullopt;
                }
                left   = std::min(left, fragment.box.x);
                top    = std::min(top, fragment.box.y);
                right  = std::max(right, fragment.box.x + fragment.box.width);
                bottom = std::max(bottom, fragment.box.y + fragment.box.height);
            }
            entry.primaryOutputID = entry.fragments.front().outputID;
            entry.clientBox       = {.x = left, .y = top, .width = right - left, .height = bottom - top};
            if (projected.clientBox) {
                if (projected.clientBox->width <= 0 || projected.clientBox->height <= 0)
                    return std::nullopt;
                entry.clientBox = *projected.clientBox;
                if (projected.primaryOutputID && outputIDs.contains(projected.primaryOutputID))
                    entry.primaryOutputID = projected.primaryOutputID;
            }
        }
        commit.entries.emplace_back(entry);
    }

    std::ranges::sort(commit.entries, {}, &SLuminophoreSpatialCommitEntry::key);
    return commit;
}

bool CLuminophoreSpatialCommitter::isApplying() {
    return g_spatialCommitDepth > 0;
}

bool CLuminophoreSpatialCommitter::apply(const SLuminophoreSpatialCommit& commit, const TargetExists& targetExists, const OutputExists& outputExists, const TargetWriter& writer) {
    if (!writer)
        return false;
    return applyBatch(commit, targetExists, outputExists, [&writer](const auto& entries) {
        for (const auto& entry : entries)
            writer(entry);
        return true;
    });
}

bool CLuminophoreSpatialCommitter::applyBatch(const SLuminophoreSpatialCommit& commit, const TargetExists& targetExists, const OutputExists& outputExists, const BatchWriter& writer) {
    if (!targetExists || !outputExists || !writer)
        return false;
    if (m_presented && (commit.modelRevision < m_presented->modelRevision || commit.topologyRevision < m_presented->topologyRevision))
        return false;
    if (m_presented == commit)
        return true;

    for (const auto& entry : commit.entries) {
        if (!targetExists(entry.key))
            return false;
        if (!entry.visible)
            continue;
        if (!outputExists(entry.primaryOutputID))
            return false;
        if (std::ranges::any_of(entry.fragments, [&outputExists](const auto& fragment) { return !outputExists(fragment.outputID); }))
            return false;
    }

    ++g_spatialCommitDepth;
    m_applying = &commit;
    CScopeGuard applyGuard([this] {
        m_applying = nullptr;
        --g_spatialCommitDepth;
    });
    if (!writer(commit.entries))
        return false;
    m_presented = commit;
    return true;
}

const std::optional<SLuminophoreSpatialCommit>& CLuminophoreSpatialCommitter::presented() const {
    return m_presented;
}

const SLuminophoreSpatialCommit* CLuminophoreSpatialCommitter::effective() const {
    return m_applying ? m_applying : m_presented ? &*m_presented : nullptr;
}

std::optional<SLuminophorePhysicalBox> SLuminophoreSpatialCommitEntry::presentationBox() const {
    if (!visible || fragments.empty() || clientBox.width <= 0 || clientBox.height <= 0)
        return std::nullopt;
    return clientBox;
}
