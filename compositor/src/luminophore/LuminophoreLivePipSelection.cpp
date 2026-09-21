#include "LuminophoreLivePipSelection.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "LuminophoreShellProjection.hpp"
#include "LuminophoreSpatialGrabController.hpp"
#include "../render/pass/SurfacePassElement.hpp"
#include "../desktop/Workspace.hpp"
#include "LuminophoreLivePipController.hpp"
#include "../desktop/state/ViewState.hpp"
#include "../desktop/view/Window.hpp"
#include "../desktop/view/Popup.hpp"
#include "../desktop/view/LayerSurface.hpp"
#include "../protocols/core/Compositor.hpp"
#include "../protocols/LayerShell.hpp"
#include "../state/MonitorState.hpp"
#include "../output/Monitor.hpp"
#include <algorithm>
#include <format>
#include <cmath>

using namespace Luminophore;

std::vector<CBox> CLuminophoreLivePipSelection::partition(const CBox& bounds, const std::vector<CBox>& boundaries) {
    std::vector<double> xs{bounds.x, bounds.x + bounds.width}, ys{bounds.y, bounds.y + bounds.height};
    for (const auto& box : boundaries) {
        for (auto x : {box.x, box.x + box.width})
            if (x > bounds.x && x < bounds.x + bounds.width)
                xs.push_back(x);
        for (auto y : {box.y, box.y + box.height})
            if (y > bounds.y && y < bounds.y + bounds.height)
                ys.push_back(y);
    }
    const auto unique = [](auto& values) {
        std::sort(values.begin(), values.end());
        values.erase(std::unique(values.begin(), values.end()), values.end());
    };
    unique(xs);
    unique(ys);
    if (xs.size() * ys.size() > 16384)
        return {};
    std::vector<CBox> result;
    for (size_t x = 1; x < xs.size(); ++x)
        for (size_t y = 1; y < ys.size(); ++y)
            result.emplace_back(xs[x - 1], ys[y - 1], xs[x] - xs[x - 1], ys[y] - ys[y - 1]);
    return result;
}

SPipSelectionScene CLuminophoreLivePipSelection::snapshot() {
    SPipSelectionScene result;
    std::vector<CBox>  boundaries;
    std::vector<CBox>  blockers;
    auto               signatureBox = [&](const CBox& b) {
        result.signature += std::format("/{},{},{},{}", b.x, b.y, b.width, b.height);
        boundaries.push_back(b);
    };
    for (const auto& window : Desktop::viewState()->windows()) {
        if (!window->m_isMapped || window->isDesktopSuppressed() || !window->wlSurface()->resource())
            continue;
        const auto root      = window->wlSurface()->resource();
        const auto rootState = root->m_luminophoreSource->snapshot();
        if (!rootState.extent)
            continue;
        auto box = window->getWindowMainSurfaceBox();
        if (!window->m_pinned && window->m_workspace)
            box.translate(window->m_workspace->m_renderOffset->value());
        box.translate(window->m_floatingOffset);
        signatureBox(box);
        // Selection during an animated geometry transition is rejected, not guessed.
        const bool animated = window->sizeAnimation()->isBeingAnimated() || window->positionAnimation()->isBeingAnimated() ||
            spatialGrabController()->visualBox(window).has_value() || (window->m_workspace && window->m_workspace->m_renderOffset->isBeingAnimated());
        if (animated) {
            blockers.push_back(window->getFullWindowBoundingBox());
            continue;
        }
        const auto output = window->m_monitor.lock();
        if (!output)
            continue;
        root->breadthfirst(
            [&](SP<CWLSurfaceResource> surface, const Vector2D& offset, void*) {
                if (!surface->m_luminophoreSource)
                    return;
                const auto state = surface->m_luminophoreSource->snapshot();
                if (!state.extent)
                    return;
                CSurfacePassElement::SRenderData data;
                data.pMonitor        = output;
                data.pWindow         = window;
                data.surface         = surface;
                data.mainSurface     = surface == root;
                data.squishOversized = false;
                data.pos             = box.pos();
                data.localPos        = offset;
                data.w               = box.width;
                data.h               = box.height;
                CSurfacePassElement element(data);
                const CBox          shown = element.getTexBox().translate(output->m_position);
                if (shown.empty())
                    return;
                result.sources.push_back({surface->m_luminophoreSource, state, shown});
                signatureBox(shown);
                result.signature += std::format("/s{}:{}", state.token, state.extentRevision);
            },
            nullptr);
        if (window->m_popupHead)
            window->m_popupHead->breadthfirst(
                [&](SP<Desktop::View::CPopup> popup, void*) {
                    if (!popup->wlSurface() || !popup->wlSurface()->resource())
                        return;
                    const auto source = popup->wlSurface()->resource()->m_luminophoreSource;
                    if (!source)
                        return;
                    const auto state = source->snapshot();
                    if (!state.extent)
                        return;
                    CBox shown{popup->coordsGlobal(), popup->size()};
                    result.sources.push_back({source, state, shown});
                    signatureBox(shown);
                    result.signature += std::format("/p{}:{}", state.token, state.extentRevision);
                },
                nullptr);
        for (const auto& monitor : State::monitorState()->monitors())
            for (const auto& fragment : spatialRuntime()->regionsFor(window, monitor))
                signatureBox(fragment);
    }
    for (const auto& layer : Desktop::viewState()->layers()) {
        if (!layer->m_mapped)
            continue;
        if (layer->m_namespace == "luminophore-capture-selector" || layer->m_namespace == "hyprpicker")
            continue;
        const auto monitor   = layer->m_monitor.lock();
        const auto projected = monitor ? shellProjection()->projectedOverlaySurfaces(monitor) : std::vector<PHLLSREF>{};
        if (layer->m_layer < ZWLR_LAYER_SHELL_V1_LAYER_TOP && std::find(projected.begin(), projected.end(), layer) == projected.end())
            continue;
        if (auto box = layer->logicalBox()) {
            signatureBox(*box);
            blockers.push_back(*box);
        }
    }
    for (const auto& box : livePipController()->selectionBlockers()) {
        signatureBox(box);
        blockers.push_back(box);
    }
    for (const auto& monitor : State::monitorState()->monitors()) {
        const auto bounds = monitor->logicalBox();
        signatureBox(bounds);
        result.signature += "/output:" + monitor->m_name;
        const auto cells = partition(bounds, boundaries);
        for (const auto& cell : cells) {
            const auto point = cell.middle();
            if (std::any_of(blockers.begin(), blockers.end(), [&](const auto& b) { return b.containsPoint(point); }))
                continue;
            // Native PiP has no source surface; never select content underneath it.
            if (livePipController()->pointerAt(point))
                continue;
            const auto window = Desktop::viewState()->hitTest().windowAt(point, Desktop::View::ALLOW_FLOATING);
            if (!window)
                continue;
            if (spatialRuntime()->isBoardRoot(window)) {
                const auto regions = spatialRuntime()->regionsFor(window, monitor);
                if (std::none_of(regions.begin(), regions.end(), [&](const auto& box) { return box.containsPoint(point); }))
                    continue;
            }
            Vector2D   local;
            const auto surface = window->m_isX11 ? window->wlSurface()->resource() : Desktop::viewState()->hitTest().windowSurfaceAt(point, window, local);
            if (!surface || !surface->m_luminophoreSource)
                continue;
            for (size_t i = 0; i < result.sources.size(); ++i)
                if (result.sources[i].state.token == surface->m_luminophoreSource->snapshot().token && result.sources[i].box.containsPoint(point)) {
                    result.tiles.push_back({cell, i});
                    result.signature += std::format("/t{}:{},{},{},{}", result.sources[i].state.token, cell.x, cell.y, cell.width, cell.height);
                    break;
                }
        }
    }
    return result;
}

std::optional<SPipSelectionResult> CLuminophoreLivePipSelection::resolve(const SPipSelectionScene& scene, const CBox& box) {
    if (!CLuminophoreLivePipModel::validRect({box.x, box.y, box.width, box.height}))
        return std::nullopt;
    std::optional<size_t> selected;
    double                covered = 0;
    for (const auto& tile : scene.tiles) {
        const auto overlap = tile.box.intersection(box);
        if (overlap.empty())
            continue;
        if (selected && *selected != tile.source)
            return std::nullopt;
        selected = tile.source;
        covered += overlap.width * overlap.height;
    }
    if (!selected || std::abs(covered - box.width * box.height) > std::max(1e-6, box.width * box.height * 1e-9))
        return std::nullopt;
    const auto&   view   = scene.sources.at(*selected);
    const auto&   extent = *view.state.extent;
    const auto    scale  = Vector2D{extent.width, extent.height} / view.box.size();
    const auto    origin = (box.pos() - view.box.pos()) * scale;
    const auto    size   = box.size() * scale;
    SLivePipRect  crop{origin.x, origin.y, size.x, size.y};
    SLivePipEntry candidate{.sourceToken = view.state.token, .crop = crop};
    if (!CLuminophoreLivePipModel::sampleable(candidate, view.state))
        return std::nullopt;
    return SPipSelectionResult{view, crop};
}
