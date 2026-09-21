#include "LuminophoreLivePipMapping.hpp"
#include "../protocols/types/SurfaceState.hpp"
#include <algorithm>
#include <cmath>

using namespace Luminophore;

std::optional<SLivePipMapping> CLuminophoreLivePipMapping::sample(const SLivePipEntry& entry, const SSurfaceSourceSnapshot& source, const SSurfaceState& state) {
    if (!CLuminophoreLivePipModel::sampleable(entry, source) || !CLuminophoreLivePipModel::validRect(entry.destination) || state.scale <= 0 || state.transform < 0 || state.transform > 7)
        return std::nullopt;
    const auto actual = CLuminophoreSurfaceSource::describe(source.token, source.revision, source.alive, source.mapped, source.hasBuffer, state);
    if (!actual.extent || actual.extent != source.extent)
        return std::nullopt;
    auto size = state.bufferSize;
    if (state.transform % 2)
        std::swap(size.x, size.y);
    size /= state.scale;
    if (!std::isfinite(size.x) || !std::isfinite(size.y) || size.x <= 0 || size.y <= 0)
        return std::nullopt;
    const CBox  viewport = state.viewport.hasSource ? state.viewport.source : CBox{{}, size};
    const auto& crop     = entry.crop;
    const auto& extent   = *source.extent;
    const auto  uvOrigin = (viewport.pos() + Vector2D{crop.x / extent.width, crop.y / extent.height} * viewport.size()) / size;
    const auto  uvSize   = Vector2D{crop.width / extent.width, crop.height / extent.height} * viewport.size() / size;
    const auto& dst      = entry.destination;
    CBox        uv{uvOrigin, uvSize};
    uv.transform(Math::wlTransformToHyprutils(Math::invertTransform(state.transform)), 1, 1);
    return SLivePipMapping{.destination = CBox{dst.x, dst.y, dst.width, dst.height}, .uv = uv};
}

std::optional<SLivePipRect> CLuminophoreLivePipMapping::damage(const SLivePipEntry& entry, const SLivePipRect& sourceDamage) {
    if (!CLuminophoreLivePipModel::validRect(entry.crop) || !CLuminophoreLivePipModel::validRect(entry.destination) || !CLuminophoreLivePipModel::validRect(sourceDamage))
        return std::nullopt;
    const auto&  c = entry.crop;
    const auto&  d = entry.destination;
    const double x = std::max(c.x, sourceDamage.x), y = std::max(c.y, sourceDamage.y);
    const double right = std::min(c.x + c.width, sourceDamage.x + sourceDamage.width), bottom = std::min(c.y + c.height, sourceDamage.y + sourceDamage.height);
    if (right <= x || bottom <= y)
        return std::nullopt;
    SLivePipRect result{d.x + (x - c.x) * d.width / c.width, d.y + (y - c.y) * d.height / c.height, (right - x) * d.width / c.width, (bottom - y) * d.height / c.height};
    return CLuminophoreLivePipModel::validRect(result) ? std::optional{result} : std::nullopt;
}
