#pragma once

#include "LuminophoreSpatialTransaction.hpp"

#include <optional>
#include <string>
#include <vector>

struct SLuminophoreEditorCell {
    SLuminophoreBoardPoint point    = {};
    double          x        = 0.0;
    double          y        = 0.0;
    double          width    = 0.0;
    double          height   = 0.0;
    uint64_t        outputID = 0;
};

struct SLuminophoreEditorFrame {
    std::string generation;
    uint64_t    revision                                  = 0;
    uint64_t    outputID                                  = 0;
    double      x                                         = 0.0;
    double      y                                         = 0.0;
    double      width                                     = 0.0;
    double      height                                    = 0.0;
    uint64_t    targetEpoch                               = 0;
    bool        operator==(const SLuminophoreEditorFrame&) const = default;
};

enum class eLuminophoreDragOrigin {
    WINDOW,
    EDITOR
};
enum class eLuminophoreDragPresentation {
    WINDOW,
    BADGE
};
struct SLuminophoreDragOptions {
    eLuminophoreDragOrigin       origin       = eLuminophoreDragOrigin::WINDOW;
    eLuminophoreDragPresentation presentation = eLuminophoreDragPresentation::WINDOW;
};

struct SLuminophoreSpatialGrabState {
    uint64_t      generation       = 0;
    uint64_t      revision         = 0;
    uint64_t      topologyRevision = 0;
    uint64_t      outputID         = 0;
    LuminophoreWindowKey window           = 0;
    bool          floating         = false;
    uint64_t      targetOutputID   = 0;
    uint64_t      targetEpoch      = 0;
};

// Pointer/grab lifetime belongs to the compositor. Shell contributes only the
// cell rectangles of an already committed editor frame; it cannot replay drop.
class CLuminophoreSpatialGrab {
  public:
    std::optional<SLuminophoreSpatialGrabState> begin(const SLuminophoreSpatialSnapshot& snapshot, LuminophoreWindowKey window, uint64_t outputID);
    bool bindLayout(uint64_t generation, const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreEditorFrame& frame, const std::vector<SLuminophoreEditorCell>& cells);
    std::optional<SLuminophoreSpatialCommand>          commandAt(const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreEditorFrame& frame, double x, double y) const;
    std::optional<SLuminophoreSpatialCommand>          release(const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreEditorFrame& frame, double x, double y);
    std::optional<SLuminophoreSpatialCommand>          commandAtPoint(const SLuminophoreSpatialSnapshot& snapshot, uint64_t output, SLuminophoreBoardPoint point) const;
    bool                                        selectTarget(uint64_t outputID);
    void                                        cancel();
    const std::optional<SLuminophoreSpatialGrabState>& state() const;

  private:
    bool                                 current(const SLuminophoreSpatialSnapshot& snapshot) const;
    uint64_t                             m_nextGeneration = 0;
    std::optional<SLuminophoreSpatialGrabState> m_state;
    std::optional<SLuminophoreEditorFrame>      m_frame;
    std::vector<SLuminophoreEditorCell>         m_cells;
};
