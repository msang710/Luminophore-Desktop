#pragma once
#include "LuminophoreSpatialGrab.hpp"

// Only the native window grab uses this cache. Every host/context check precedes
// lookup; it never authorizes a release or retains a prepared native commit.
struct SLuminophoreSpatialPreviewKey {
    uint64_t                        generation            = 0;
    uint64_t                        revision              = 0;
    uint64_t                        topologyRevision      = 0;
    uint64_t                        observationGeneration = 0;
    uint64_t                        layoutGeneration      = 0;
    LuminophoreWindowKey                   window                = 0;
    uint64_t                        outputID              = 0;
    std::optional<SLuminophoreBoardPoint>  point;
    std::optional<SLuminophoreEditorFrame> frame;
    bool                            operator==(const SLuminophoreSpatialPreviewKey&) const = default;
};
class CLuminophoreSpatialPreviewCache {
  public:
    bool matches(const SLuminophoreSpatialPreviewKey& key) const;
    void remember(const SLuminophoreSpatialPreviewKey& key);
    void clear();

  private:
    std::optional<SLuminophoreSpatialPreviewKey> m_last;
};

// Event-loop-turn coalescing, independent of frame scheduling. The token also
// rejects callbacks already copied out of the event loop when cancel occurs.
class CLuminophoreSpatialPreviewQueue {
  public:
    std::optional<uint64_t>                  push(double x, double y);
    std::optional<std::pair<double, double>> take(uint64_t token);
    void                                     cancel();

  private:
    uint64_t                  m_token   = 0;
    bool                      m_pending = false;
    std::pair<double, double> m_latest  = {};
};
