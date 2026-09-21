#include "LuminophoreSpatialGrab.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <set>

static bool validBox(double x, double y, double width, double height) {
    return std::isfinite(x) && std::isfinite(y) && std::isfinite(width) && std::isfinite(height) && width > 0.0 && height > 0.0 && std::isfinite(x + width) &&
        std::isfinite(y + height);
}

std::optional<SLuminophoreSpatialGrabState> CLuminophoreSpatialGrab::begin(const SLuminophoreSpatialSnapshot& snapshot, LuminophoreWindowKey window, uint64_t outputID) {
    cancel();
    const bool tiled    = std::ranges::any_of(snapshot.tiled, [window](const auto& item) { return item.key == window; });
    const bool floating = std::ranges::any_of(snapshot.floating, [window](const auto& item) { return item.key == window; });
    if (!window || (!tiled && !floating) || snapshot.presentationMode == eLuminophorePresentationMode::DESKTOP || m_nextGeneration == std::numeric_limits<uint64_t>::max() ||
        !std::ranges::any_of(snapshot.outputViews, [outputID](const auto& output) { return output.outputID == outputID; }))
        return std::nullopt;
    m_state = SLuminophoreSpatialGrabState{.generation       = ++m_nextGeneration,
                                    .revision         = snapshot.revision,
                                    .topologyRevision = snapshot.outputTopologyRevision,
                                    .outputID         = outputID,
                                    .window           = window,
                                    .floating         = floating,
                                    .targetOutputID   = outputID};
    return m_state;
}

bool CLuminophoreSpatialGrab::current(const SLuminophoreSpatialSnapshot& snapshot) const {
    if (!m_state || snapshot.revision != m_state->revision || snapshot.outputTopologyRevision != m_state->topologyRevision ||
        snapshot.presentationMode == eLuminophorePresentationMode::DESKTOP)
        return false;
    if (m_state->floating)
        return std::ranges::any_of(snapshot.floating, [this](const auto& item) { return item.key == m_state->window; });
    return std::ranges::any_of(snapshot.tiled, [this](const auto& item) { return item.key == m_state->window; });
}

bool CLuminophoreSpatialGrab::bindLayout(uint64_t generation, const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreEditorFrame& frame, const std::vector<SLuminophoreEditorCell>& cells) {
    if (!m_state || generation != m_state->generation || frame.targetEpoch != m_state->targetEpoch)
        return false;
    // A replacement for this grab invalidates the old geometry even on failure.
    m_frame.reset();
    m_cells.clear();
    if (!current(snapshot) || frame.generation.empty() || !frame.revision || (!m_state->targetOutputID || frame.outputID != m_state->targetOutputID) ||
        !validBox(frame.x, frame.y, frame.width, frame.height) || cells.empty() || cells.size() > 4096)
        return false;
    std::set<std::pair<uint64_t, SLuminophoreBoardPoint>> points;
    for (size_t index = 0; index < cells.size(); ++index) {
        const auto& cell = cells[index];
        if (cell.outputID && !std::ranges::any_of(snapshot.outputViews, [&](const auto& v) { return v.outputID == cell.outputID; }))
            return false;
        if ((!snapshot.independent && !snapshot.extent.contains(cell.point)) || !points.insert({cell.outputID, cell.point}).second ||
            !validBox(cell.x, cell.y, cell.width, cell.height) || cell.x < 0.0 || cell.y < 0.0 || cell.x + cell.width > frame.width || cell.y + cell.height > frame.height)
            return false;
        for (size_t previous = 0; previous < index; ++previous) {
            const auto& other = cells[previous];
            if (cell.x < other.x + other.width && other.x < cell.x + cell.width && cell.y < other.y + other.height && other.y < cell.y + cell.height)
                return false;
        }
    }
    m_frame = frame;
    m_cells = cells;
    return true;
}

std::optional<SLuminophoreSpatialCommand> CLuminophoreSpatialGrab::commandAt(const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreEditorFrame& frame, double x, double y) const {
    if (!current(snapshot) || !m_frame || frame != *m_frame || !std::isfinite(x) || !std::isfinite(y))
        return std::nullopt;
    x -= frame.x;
    y -= frame.y;
    const auto cell = std::ranges::find_if(m_cells, [x, y](const auto& item) { return item.x <= x && x < item.x + item.width && item.y <= y && y < item.y + item.height; });
    if (cell == m_cells.end())
        return std::nullopt;
    return SLuminophoreSpatialCommand{.expectedRevision = m_state->revision,
                               .payload          = SMoveWindowToCommand{.key                      = m_state->window,
                                                                        .point                    = cell->point,
                                                                        .outputID                 = cell->outputID ? cell->outputID : m_state->targetOutputID,
                                                                        .expectedTopologyRevision = m_state->topologyRevision}};
}

std::optional<SLuminophoreSpatialCommand> CLuminophoreSpatialGrab::release(const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreEditorFrame& frame, double x, double y) {
    const auto command = commandAt(snapshot, frame, x, y);
    cancel();
    return command;
}

std::optional<SLuminophoreSpatialCommand> CLuminophoreSpatialGrab::commandAtPoint(const SLuminophoreSpatialSnapshot& snapshot, uint64_t output, SLuminophoreBoardPoint point) const {
    if (!current(snapshot) || m_state->floating || !output || output != m_state->targetOutputID ||
        !std::ranges::any_of(snapshot.outputViews, [output](const auto& view) { return view.outputID == output; }))
        return std::nullopt;
    return SLuminophoreSpatialCommand{.expectedRevision = m_state->revision,
                               .payload = SMoveWindowToCommand{.key = m_state->window, .point = point, .outputID = output, .expectedTopologyRevision = m_state->topologyRevision}};
}

bool CLuminophoreSpatialGrab::selectTarget(uint64_t outputID) {
    if (!m_state || m_state->targetOutputID == outputID)
        return false;
    if (m_state->targetEpoch == std::numeric_limits<uint64_t>::max()) {
        cancel();
        return false;
    }
    m_state->targetOutputID = outputID;
    ++m_state->targetEpoch;
    m_frame.reset();
    m_cells.clear();
    return true;
}

void CLuminophoreSpatialGrab::cancel() {
    m_state.reset();
    m_frame.reset();
    m_cells.clear();
}

const std::optional<SLuminophoreSpatialGrabState>& CLuminophoreSpatialGrab::state() const {
    return m_state;
}
