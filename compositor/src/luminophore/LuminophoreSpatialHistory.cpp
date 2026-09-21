#include "LuminophoreSpatialHistory.hpp"
#include <algorithm>
#include <limits>

static SLuminophoreSpatialSnapshot comparable(SLuminophoreSpatialSnapshot s) {
    s.revision = s.outputTopologyRevision = 0;
    // These are caches and input routing, not independent user state.
    s.extent = {};
    s.view   = {};
    s.outputViews.clear();
    if (s.independent) {
        auto& i          = *s.independent;
        i.state.revision = i.focusRevision = i.selectedOutput = 0;
        i.state.focus.reset();
        i.meshResetRevisions.clear();
        for (auto& [id, mesh] : i.meshes) {
            mesh.revision = 0;
            mesh.fillFocus.reset();
        }
    }
    return s;
}
bool CLuminophoreSpatialHistory::equivalent(const SLuminophoreSpatialSnapshot& a, const SLuminophoreSpatialSnapshot& b) {
    return comparable(a) == comparable(b);
}
CLuminophoreSpatialHistory::CLuminophoreSpatialHistory(size_t limit, size_t byteLimit) : m_limit(limit), m_byteLimit(byteLimit) {}
static size_t frameBytes(const SLuminophoreHistoryFrame& f) {
    size_t size = f.native.size() * 192 + sizeof(f) + f.lifetimes.size() * 64 + f.snapshot.tiled.size() * sizeof(SLuminophoreTiledPlacement) +
        f.snapshot.floating.size() * sizeof(SLuminophoreFloatingPlacement);
    size += f.snapshot.outputViews.capacity() * sizeof(SLuminophoreOutputView);
    for (const auto& [key, native] : f.native)
        size += native.connector.capacity() + 1;
    if (!f.snapshot.independent)
        return size;
    const auto& i = *f.snapshot.independent;
    size += i.bindings.size() * 64 + i.outputs.size() * 192 + i.knownBoards.size() * 128 + i.displaced.size() * 96 + i.floatingBoards.size() * 64;
    for (const auto& [id, o] : i.outputs)
        size += o.name.capacity() + 1;
    for (const auto& [name, id] : i.knownBoards)
        size += name.capacity() + 1;
    size += i.meshResetRevisions.size() * 64;
    for (const auto& [id, b] : i.state.boards)
        size += sizeof(b) + 64 + b.tiled.size() * 80;
    for (const auto& [id, mesh] : i.meshes)
        size += sizeof(mesh) + 64 + mesh.faces.capacity() * sizeof(Luminophore::Spatial::SFace);
    return size;
}
size_t CLuminophoreSpatialHistory::bytes() const {
    size_t result = 0;
    for (const auto& e : m_entries)
        result += frameBytes(e.before) + frameBytes(e.after);
    return result;
}
size_t CLuminophoreSpatialHistory::undoCount() const {
    return m_cursor;
}
size_t CLuminophoreSpatialHistory::redoCount() const {
    return m_entries.size() - m_cursor;
}
void CLuminophoreSpatialHistory::trim() {
    while (!m_entries.empty() && (m_entries.size() > m_limit || bytes() > m_byteLimit)) {
        m_entries.pop_front();
        if (m_cursor)
            --m_cursor;
    }
}
void CLuminophoreSpatialHistory::closeGroup() {
    if (!m_activeGroup.empty()) {
        // Once the bounded tombstone budget is exhausted, grouping remains off
        // until session restart. Never accidentally reopen an old launch group.
        if (m_closedGroups.size() < 4096)
            m_closedGroups.insert(m_activeGroup);
        m_activeGroup.clear();
    }
}
void CLuminophoreSpatialHistory::record(SLuminophoreHistoryFrame before, SLuminophoreHistoryFrame after, const std::string& group) {
    if (m_replaying || (equivalent(before.snapshot, after.snapshot) && before.native == after.native))
        return;
    const bool eligible = !group.empty() && group.size() <= 64 && m_closedGroups.size() < 4096 && !m_closedGroups.contains(group);
    if (eligible && group == m_activeGroup && m_cursor == m_entries.size() && !m_entries.empty()) {
        m_entries.back().after = std::move(after);
        trim();
        return;
    }
    closeGroup();
    if (eligible)
        m_activeGroup = group;
    m_entries.erase(m_entries.begin() + m_cursor, m_entries.end());
    m_entries.push_back({std::move(before), std::move(after)});
    m_cursor = m_entries.size();
    trim();
}
eLuminophoreHistoryResult CLuminophoreSpatialHistory::replay(bool redo, CLuminophoreSpatialModel& model, const std::map<LuminophoreWindowKey, uint64_t>& lifetimes,
                                                             const std::function<bool(const SLuminophoreSpatialSnapshot&)>& commit) {
    return replayNative(redo, model, {model.snapshot(), lifetimes, {}}, [&](const auto& snapshot, const auto&) { return commit(snapshot); });
}
eLuminophoreHistoryResult CLuminophoreSpatialHistory::replayNative(bool redo, CLuminophoreSpatialModel& model, SLuminophoreHistoryFrame currentFrame,
                                                                   const std::function<bool(const SLuminophoreSpatialSnapshot&, const SLuminophoreHistoryFrame&)>& commit) {
    const auto& lifetimes = currentFrame.lifetimes;
    if (m_replaying)
        return eLuminophoreHistoryResult::BUSY;
    if (redo ? !redoCount() : !undoCount())
        return eLuminophoreHistoryResult::EMPTY;
    closeGroup();
    const auto  index       = redo ? m_cursor : m_cursor - 1;
    const auto& destination = redo ? m_entries[index].after : m_entries[index].before;
    // A newly mapped unmanaged/modal window has no board coordinate in which
    // it can be parked. Reject before native writes instead of claiming that
    // every new window was moved outside the restored view.
    for (const auto& [key, state] : currentFrame.native) {
        const auto old  = destination.lifetimes.find(key);
        const auto live = currentFrame.lifetimes.find(key);
        if (state.auxiliary && !state.minimized && live != currentFrame.lifetimes.end() && (old == destination.lifetimes.end() || old->second != live->second))
            return eLuminophoreHistoryResult::UNAVAILABLE;
    }
    std::set<LuminophoreWindowKey> retained;
    for (const auto& [key, lifetime] : destination.lifetimes)
        if (const auto current = lifetimes.find(key); current != lifetimes.end() && current->second == lifetime)
            retained.insert(key);
    auto                           candidate = model;
    std::set<LuminophoreWindowKey> excluded;
    for (const auto& [key, state] : destination.native)
        if (retained.contains(key) && state.minimized)
            excluded.insert(key);
    if (!candidate.restoreSpatial(destination.snapshot, retained, excluded))
        return eLuminophoreHistoryResult::UNAVAILABLE;
    // Capture the actual opposite state, including relocation of later windows.
    SLuminophoreHistoryFrame opposite = std::move(currentFrame);
    m_replaying                       = true;
    bool accepted                     = false;
    try {
        accepted = commit(candidate.snapshot(), destination);
    } catch (...) {
        m_replaying = false;
        throw;
    }
    m_replaying = false;
    if (!accepted)
        return eLuminophoreHistoryResult::COMMIT_FAILED;
    model = std::move(candidate);
    if (redo) {
        m_entries[index].before = std::move(opposite);
        ++m_cursor;
    } else {
        m_entries[index].after = std::move(opposite);
        --m_cursor;
    }
    trim();
    return eLuminophoreHistoryResult::APPLIED;
}
bool CLuminophoreHistoryGeometryGesture::begin(const SLuminophoreHistoryFrame& frame, LuminophoreWindowKey key) {
    if (m_key || !frame.lifetimes.contains(key) || !frame.native.contains(key) || !frame.native.at(key).auxiliary)
        return false;
    m_key      = key;
    m_lifetime = frame.lifetimes.at(key);
    m_start    = frame.native.at(key);
    return true;
}
bool CLuminophoreHistoryGeometryGesture::active() const {
    return m_key.has_value();
}
void CLuminophoreHistoryGeometryGesture::mask(SLuminophoreHistoryFrame& frame) const {
    if (!m_key || !frame.lifetimes.contains(*m_key) || frame.lifetimes.at(*m_key) != m_lifetime || !frame.native.contains(*m_key))
        return;
    auto& window     = frame.native.at(*m_key);
    window.x         = m_start.x;
    window.y         = m_start.y;
    window.width     = m_start.width;
    window.height    = m_start.height;
    window.connector = m_start.connector;
}
std::optional<std::pair<SLuminophoreHistoryFrame, SLuminophoreHistoryFrame>> CLuminophoreHistoryGeometryGesture::finish(SLuminophoreHistoryFrame current) {
    if (!m_key)
        return std::nullopt;
    auto before = current;
    mask(before);
    m_key.reset();
    return std::pair{std::move(before), std::move(current)};
}

bool CLuminophoreHistoryGeometryGesture::owns(LuminophoreWindowKey key) const {
    return m_key == key;
}
eLuminophoreHistoryResult CLuminophoreHistoryRequests::run(bool redo, uint64_t revision, std::optional<uint64_t> expected, const std::string& id,
                                                           const std::function<eLuminophoreHistoryResult()>& operation) {
    if (id.empty())
        return expected ? eLuminophoreHistoryResult::UNAVAILABLE : operation();
    if (!expected)
        return eLuminophoreHistoryResult::UNAVAILABLE;
    const auto old = std::ranges::find(m_receipts, id, &SReceipt::id);
    if (old != m_receipts.end())
        return old->redo == redo && old->expected == *expected ? old->result : eLuminophoreHistoryResult::UNAVAILABLE;
    if (m_pending)
        return eLuminophoreHistoryResult::BUSY;
    m_pending   = true;
    auto result = eLuminophoreHistoryResult::STALE;
    try {
        if (*expected == revision)
            result = operation();
    } catch (...) {
        m_pending = false;
        throw;
    }
    m_pending = false;
    m_receipts.push_back({id, redo, *expected, result});
    if (m_receipts.size() > 128)
        m_receipts.pop_front();
    return result;
}
eLuminophoreHistoryApplyResult luminophoreApplyHistoryBatch(const std::function<bool()>& apply, const std::function<bool()>& restore) {
    try {
        if (apply())
            return eLuminophoreHistoryApplyResult::APPLIED;
    } catch (...) {
        // The caller retains the logical cursor until the complete batch succeeds.
    }
    try {
        if (restore())
            return eLuminophoreHistoryApplyResult::ROLLED_BACK;
    } catch (...) {
        // Recovery failure must disable further replay rather than claim rollback.
    }
    return eLuminophoreHistoryApplyResult::DEGRADED;
}
