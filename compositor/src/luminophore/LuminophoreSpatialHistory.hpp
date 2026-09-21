#pragma once
#include "LuminophoreSpatialModel.hpp"
#include <deque>
#include <functional>
#include <set>
#include <string>

// Keys remain the public IPC addresses. A separately observed lifetime prevents
// a recycled address from being mistaken for the window in an older entry.
struct SLuminophoreHistoryNativeWindow {
    bool        floating = false, minimized = false, auxiliary = false, layoutAware = false;
    int8_t      internalFullscreen = 0, clientFullscreen = 0;
    std::string connector;
    double      x = 0, y = 0, width = 0, height = 0;
    bool        operator==(const SLuminophoreHistoryNativeWindow&) const = default;
};
struct SLuminophoreHistoryFrame {
    SLuminophoreSpatialSnapshot                                     snapshot;
    std::map<LuminophoreWindowKey, uint64_t>                        lifetimes;
    std::map<LuminophoreWindowKey, SLuminophoreHistoryNativeWindow> native;
    std::optional<LuminophoreWindowKey>                             focus;
};
enum class eLuminophoreHistoryResult {
    APPLIED,
    EMPTY,
    BUSY,
    STALE,
    UNAVAILABLE,
    COMMIT_FAILED,
    RECOVERY_FAILED,
};
class CLuminophoreSpatialHistory {
  public:
    explicit CLuminophoreSpatialHistory(size_t limit = 100, size_t byteLimit = 32 * 1024 * 1024);
    static bool               equivalent(const SLuminophoreSpatialSnapshot& a, const SLuminophoreSpatialSnapshot& b);
    void                      record(SLuminophoreHistoryFrame before, SLuminophoreHistoryFrame after, const std::string& group = {});
    eLuminophoreHistoryResult replay(bool redo, CLuminophoreSpatialModel& model, const std::map<LuminophoreWindowKey, uint64_t>& lifetimes,
                                     const std::function<bool(const SLuminophoreSpatialSnapshot&)>& commit);
    size_t                    undoCount() const;
    size_t                    redoCount() const;
    size_t                    bytes() const;
    eLuminophoreHistoryResult replayNative(bool redo, CLuminophoreSpatialModel& model, SLuminophoreHistoryFrame current,
                                           const std::function<bool(const SLuminophoreSpatialSnapshot&, const SLuminophoreHistoryFrame&)>& commit);

  private:
    struct SEntry {
        SLuminophoreHistoryFrame before, after;
    };
    std::deque<SEntry>    m_entries;
    size_t                m_cursor = 0;
    size_t                m_limit;
    size_t                m_byteLimit;
    bool                  m_replaying = false;
    void                  trim();
    void                  closeGroup();
    std::string           m_activeGroup;
    std::set<std::string> m_closedGroups;
};

// Keep a pointer gesture's geometry out of intervening automatic entries. On
// release, its before-state uses the latest world with only this geometry undone.
class CLuminophoreHistoryGeometryGesture {
  public:
    bool                                                                         begin(const SLuminophoreHistoryFrame& frame, LuminophoreWindowKey key);
    bool                                                                         active() const;
    bool                                                                         owns(LuminophoreWindowKey key) const;
    void                                                                         mask(SLuminophoreHistoryFrame& frame) const;
    std::optional<std::pair<SLuminophoreHistoryFrame, SLuminophoreHistoryFrame>> finish(SLuminophoreHistoryFrame current);

  private:
    std::optional<LuminophoreWindowKey> m_key;
    uint64_t                            m_lifetime = 0;
    SLuminophoreHistoryNativeWindow     m_start;
};

class CLuminophoreHistoryRequests {
  public:
    eLuminophoreHistoryResult run(bool redo, uint64_t revision, std::optional<uint64_t> expected, const std::string& id,
                                  const std::function<eLuminophoreHistoryResult()>& operation);

  private:
    struct SReceipt {
        std::string               id;
        bool                      redo     = false;
        uint64_t                  expected = 0;
        eLuminophoreHistoryResult result   = eLuminophoreHistoryResult::EMPTY;
    };
    std::deque<SReceipt> m_receipts;
    bool                 m_pending = false;
};

enum class eLuminophoreHistoryApplyResult {
    APPLIED,
    ROLLED_BACK,
    DEGRADED
};
eLuminophoreHistoryApplyResult luminophoreApplyHistoryBatch(const std::function<bool()>& apply, const std::function<bool()>& restore);
