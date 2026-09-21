#include "LuminophoreSpatialProjection.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>
#include <limits>
#include <set>

static std::vector<SLuminophoreProjectedCell> projectOutputView(const SLuminophoreViewRect& view, const SLuminophorePhysicalOutput& output) {
    std::vector<SLuminophoreProjectedCell> result;
    if (view.columns <= 0 || view.rows <= 0 || output.id == 0 || output.box.width <= 0 || output.box.height <= 0)
        return result;
    result.reserve(view.columns * view.rows);
    for (int column = 0; column < view.columns; ++column) {
        const int x0 = output.box.x + output.box.width * column / view.columns;
        const int x1 = output.box.x + output.box.width * (column + 1) / view.columns;
        for (int row = 0; row < view.rows; ++row) {
            const int y0 = output.box.y + output.box.height * row / view.rows;
            const int y1 = output.box.y + output.box.height * (row + 1) / view.rows;
            result.emplace_back(SLuminophoreProjectedCell{
                .point    = {.x = view.origin.x + column, .y = view.origin.y + row},
                .outputID = output.id,
                .box      = {.x = x0, .y = y0, .width = x1 - x0, .height = y1 - y0},
            });
        }
    }
    return result;
}

std::optional<SLuminophoreProjectionPlan> CLuminophoreSpatialProjection::plan(const SLuminophoreSpatialSnapshot& snapshot, uint64_t topologyRevision, const std::vector<SLuminophorePhysicalOutput>& outputs) {
    if (snapshot.independent)
        return planIndependent(snapshot, topologyRevision, outputs);
    if (outputs.empty() || snapshot.extent.columns <= 0 || snapshot.extent.rows <= 0 || snapshot.view.columns <= 0 || snapshot.view.rows <= 0)
        return std::nullopt;

    std::set<uint64_t> outputIDs;
    for (const auto& output : outputs) {
        if (output.id == 0 || output.box.width <= 0 || output.box.height <= 0 || !outputIDs.insert(output.id).second)
            return std::nullopt;
    }

    std::vector<SLuminophoreProjectedCell> cells;
    if (snapshot.presentationMode == eLuminophorePresentationMode::WIDE) {
        if (!snapshot.wideKey)
            return std::nullopt;
        const auto placement = std::ranges::find(snapshot.tiled, *snapshot.wideKey, &SLuminophoreTiledPlacement::key);
        if (placement == snapshot.tiled.end())
            return std::nullopt;
        cells.reserve(outputs.size());
        for (const auto& output : outputs)
            cells.emplace_back(SLuminophoreProjectedCell{.point = placement->point, .outputID = output.id, .box = output.box});
    } else if (!snapshot.outputViews.empty()) {
        if (snapshot.outputTopologyRevision != topologyRevision || snapshot.outputViews.size() != outputs.size())
            return std::nullopt;
        for (const auto& outputView : snapshot.outputViews) {
            const auto output = std::ranges::find(outputs, outputView.outputID, &SLuminophorePhysicalOutput::id);
            if (output == outputs.end())
                return std::nullopt;
            const auto projected = projectOutputView(outputView.rect, *output);
            cells.insert(cells.end(), projected.begin(), projected.end());
        }
    } else
        cells = project(snapshot.view, outputs);

    SLuminophoreProjectionPlan result = {
        .modelRevision    = snapshot.revision,
        .topologyRevision = topologyRevision,
        .cells            = std::move(cells),
        .presentationMode = snapshot.presentationMode,
    };
    std::ranges::sort(result.cells, [](const auto& lhs, const auto& rhs) {
        if (lhs.point != rhs.point)
            return lhs.point < rhs.point;
        return lhs.outputID < rhs.outputID;
    });

    auto placements = snapshot.tiled;
    std::ranges::sort(placements, {}, &SLuminophoreTiledPlacement::key);
    result.windows.reserve(placements.size() + snapshot.floating.size());
    for (const auto& placement : placements) {
        SLuminophoreProjectedWindow window = {.key = placement.key, .point = placement.point};
        for (const auto& cell : result.cells) {
            if (snapshot.presentationMode != eLuminophorePresentationMode::DESKTOP && cell.point == placement.point)
                window.fragments.emplace_back(SLuminophoreProjectedFragment{.key = placement.key, .point = placement.point, .outputID = cell.outputID, .box = cell.box});
        }
        window.visible = !window.fragments.empty();
        result.windows.emplace_back(std::move(window));
    }

    auto floating = snapshot.floating;
    std::ranges::sort(floating, {}, &SLuminophoreFloatingPlacement::key);
    for (const auto& placement : floating) {
        SLuminophoreProjectedWindow            window = {.key = placement.key, .point = placement.host, .floating = true};
        std::vector<SLuminophoreProjectedCell> hostCells;
        for (const auto& cell : result.cells) {
            if (cell.point == placement.host)
                hostCells.emplace_back(cell);
        }
        if (snapshot.presentationMode == eLuminophorePresentationMode::NORMAL && !hostCells.empty()) {
            int left = hostCells.front().box.x, top = hostCells.front().box.y;
            int right = left + hostCells.front().box.width, bottom = top + hostCells.front().box.height;
            for (const auto& cell : hostCells) {
                left   = std::min(left, cell.box.x);
                top    = std::min(top, cell.box.y);
                right  = std::max(right, cell.box.x + cell.box.width);
                bottom = std::max(bottom, cell.box.y + cell.box.height);
            }
            const auto physical = denormalize(placement.localBox, {.x = left, .y = top, .width = right - left, .height = bottom - top});
            if (!physical)
                return std::nullopt;
            window.clientBox       = *physical;
            window.primaryOutputID = hostCells.front().outputID;
            for (const auto& output : outputs) {
                const int left   = std::max(physical->x, output.box.x);
                const int top    = std::max(physical->y, output.box.y);
                const int right  = std::min(physical->x + physical->width, output.box.x + output.box.width);
                const int bottom = std::min(physical->y + physical->height, output.box.y + output.box.height);
                if (right <= left || bottom <= top)
                    continue;
                window.fragments.emplace_back(SLuminophoreProjectedFragment{
                    .key = placement.key, .point = placement.host, .outputID = output.id, .box = {.x = left, .y = top, .width = right - left, .height = bottom - top}});
            }
        }
        window.visible = !window.fragments.empty();
        result.windows.emplace_back(std::move(window));
    }
    std::ranges::sort(result.windows, {}, &SLuminophoreProjectedWindow::key);
    return result;
}

std::optional<SLuminophoreNormalizedBox> CLuminophoreSpatialProjection::normalize(const SLuminophorePhysicalBox& box, const SLuminophorePhysicalBox& host) {
    if (host.width <= 0 || host.height <= 0 || box.width <= 0 || box.height <= 0)
        return std::nullopt;

    const auto scaled = [](int64_t value, int extent) -> std::optional<int> {
        const auto result = std::llround(static_cast<double>(value) * SLuminophoreNormalizedBox::BASIS / extent);
        if (result < std::numeric_limits<int>::min() || result > std::numeric_limits<int>::max())
            return std::nullopt;
        return static_cast<int>(result);
    };
    const auto x      = scaled(static_cast<int64_t>(box.x) - host.x, host.width);
    const auto y      = scaled(static_cast<int64_t>(box.y) - host.y, host.height);
    const auto width  = scaled(box.width, host.width);
    const auto height = scaled(box.height, host.height);
    if (!x || !y || !width || !height)
        return std::nullopt;
    SLuminophoreNormalizedBox result{.x = *x, .y = *y, .width = std::max(1, *width), .height = std::max(1, *height), .logicalWidth = box.width, .logicalHeight = box.height};
    return result.valid() ? std::optional<SLuminophoreNormalizedBox>{result} : std::nullopt;
}

std::optional<SLuminophorePhysicalBox> CLuminophoreSpatialProjection::denormalize(const SLuminophoreNormalizedBox& box, const SLuminophorePhysicalBox& host) {
    if (!box.valid() || host.width <= 0 || host.height <= 0)
        return std::nullopt;
    const auto x = static_cast<int64_t>(host.x) + std::llround(static_cast<double>(host.width) * box.x / SLuminophoreNormalizedBox::BASIS);
    const auto y = static_cast<int64_t>(host.y) + std::llround(static_cast<double>(host.height) * box.y / SLuminophoreNormalizedBox::BASIS);
    const auto width =
        box.logicalWidth > 0 ? int64_t{box.logicalWidth} : std::max<int64_t>(1, std::llround(static_cast<double>(host.width) * box.width / SLuminophoreNormalizedBox::BASIS));
    const auto height =
        box.logicalHeight > 0 ? int64_t{box.logicalHeight} : std::max<int64_t>(1, std::llround(static_cast<double>(host.height) * box.height / SLuminophoreNormalizedBox::BASIS));
    if (x < std::numeric_limits<int>::min() || y < std::numeric_limits<int>::min() || x + width > std::numeric_limits<int>::max() || y + height > std::numeric_limits<int>::max() ||
        width > std::numeric_limits<int>::max() || height > std::numeric_limits<int>::max())
        return std::nullopt;
    return SLuminophorePhysicalBox{.x = static_cast<int>(x), .y = static_cast<int>(y), .width = static_cast<int>(width), .height = static_cast<int>(height)};
}

SLuminophoreBoardExtent CLuminophoreSpatialProjection::boardExtentFor(const std::vector<SLuminophorePhysicalOutput>& outputs) {
    if (outputs.empty())
        return {};

    int left   = outputs.front().box.x;
    int top    = outputs.front().box.y;
    int right  = outputs.front().box.x + outputs.front().box.width;
    int bottom = outputs.front().box.y + outputs.front().box.height;
    for (const auto& output : outputs) {
        if (output.box.width <= 0 || output.box.height <= 0)
            continue;
        left   = std::min(left, output.box.x);
        top    = std::min(top, output.box.y);
        right  = std::max(right, output.box.x + output.box.width);
        bottom = std::max(bottom, output.box.y + output.box.height);
    }

    return SLuminophoreBoardExtent::fromPhysicalExtent(right - left, bottom - top);
}

std::vector<SLuminophoreProjectedCell> CLuminophoreSpatialProjection::project(const SLuminophoreViewRect& view, const std::vector<SLuminophorePhysicalOutput>& outputs) {
    if (outputs.empty() || view.columns <= 0 || view.rows <= 0)
        return {};

    auto ordered = outputs;
    std::ranges::sort(ordered, [](const auto& lhs, const auto& rhs) {
        if (lhs.box.x != rhs.box.x)
            return lhs.box.x < rhs.box.x;
        if (lhs.box.y != rhs.box.y)
            return lhs.box.y < rhs.box.y;
        return lhs.id < rhs.id;
    });
    std::erase_if(ordered, [](const auto& output) { return output.id == 0 || output.box.width <= 0 || output.box.height <= 0; });
    if (ordered.empty())
        return {};

    std::vector<SLuminophoreProjectedCell> result;

    // Legacy compatibility until PR4 activates outputViews as the only NORMAL
    // presentation source. New model snapshots use the explicit mode branch in
    // plan(); direct callers of this helper retain the previous allocation.
    // The same logical cell is emitted once per physical output rather than as
    // one cross-output rectangle, so every render target remains output-local.
    if (view.columns < static_cast<int>(ordered.size())) {
        for (int viewColumn = 0; viewColumn < view.columns; ++viewColumn) {
            const size_t outputBegin = ordered.size() * viewColumn / view.columns;
            const size_t outputEnd   = ordered.size() * (viewColumn + 1) / view.columns;
            for (size_t outputIndex = outputBegin; outputIndex < outputEnd; ++outputIndex) {
                const auto& output = ordered[outputIndex];
                for (int row = 0; row < view.rows; ++row) {
                    const int y0 = output.box.y + output.box.height * row / view.rows;
                    const int y1 = output.box.y + output.box.height * (row + 1) / view.rows;
                    result.emplace_back(SLuminophoreProjectedCell{
                        .point    = {.x = view.origin.x + viewColumn, .y = view.origin.y + row},
                        .outputID = output.id,
                        .box      = {.x = output.box.x, .y = y0, .width = output.box.width, .height = y1 - y0},
                    });
                }
            }
        }
        return result;
    }

    const auto allocations = columnsPerOutput(view.columns, ordered);
    int        boardColumn = 0;
    for (size_t outputIndex = 0; outputIndex < ordered.size(); ++outputIndex) {
        const auto& output        = ordered[outputIndex];
        const int   outputColumns = allocations[outputIndex];
        for (int localColumn = 0; localColumn < outputColumns; ++localColumn) {
            const int x0 = output.box.x + output.box.width * localColumn / outputColumns;
            const int x1 = output.box.x + output.box.width * (localColumn + 1) / outputColumns;
            for (int row = 0; row < view.rows; ++row) {
                const int y0 = output.box.y + output.box.height * row / view.rows;
                const int y1 = output.box.y + output.box.height * (row + 1) / view.rows;
                result.emplace_back(SLuminophoreProjectedCell{
                    .point    = {.x = view.origin.x + boardColumn, .y = view.origin.y + row},
                    .outputID = output.id,
                    .box      = {.x = x0, .y = y0, .width = x1 - x0, .height = y1 - y0},
                });
            }
            ++boardColumn;
        }
    }
    return result;
}

std::vector<int> CLuminophoreSpatialProjection::columnsPerOutput(int columns, const std::vector<SLuminophorePhysicalOutput>& outputs) {
    std::vector<int> result(outputs.size(), 1);
    if (columns <= static_cast<int>(outputs.size()))
        return result;

    const int totalWidth = std::accumulate(outputs.begin(), outputs.end(), 0, [](int sum, const auto& output) { return sum + output.box.width; });
    int       remaining  = columns - outputs.size();

    struct SFraction {
        size_t index     = 0;
        double remainder = 0.0;
    };
    std::vector<SFraction> fractions;
    fractions.reserve(outputs.size());

    int assigned = 0;
    for (size_t index = 0; index < outputs.size(); ++index) {
        const double exact = totalWidth > 0 ? static_cast<double>(remaining) * outputs[index].box.width / totalWidth : 0.0;
        const int    whole = static_cast<int>(std::floor(exact));
        result[index] += whole;
        assigned += whole;
        fractions.emplace_back(SFraction{.index = index, .remainder = exact - whole});
    }

    std::ranges::sort(fractions, [](const auto& lhs, const auto& rhs) {
        if (lhs.remainder != rhs.remainder)
            return lhs.remainder > rhs.remainder;
        return lhs.index < rhs.index;
    });
    for (int index = 0; index < remaining - assigned; ++index)
        ++result[fractions[index].index];

    return result;
}
