#include "LuminophoreLivePipRenderer.hpp"
#include "../Renderer.hpp"
#include "LuminophoreShellBloom.hpp"
#include "LuminophoreEffectConfig.hpp"
#include "../../luminophore/LuminophoreShellProjection.hpp"
#include "../../luminophore/LuminophoreVisualSettings.hpp"
#include "../pass/SurfacePassElement.hpp"
#include "../pass/RectPassElement.hpp"
#include "../Texture.hpp"
#include <map>
#include "../../protocols/core/Compositor.hpp"
#include "../../output/Monitor.hpp"

static Render::CLuminophoreShellBloom& pipBloom() {
    static Render::CLuminophoreShellBloom bloom;
    return bloom;
}

CBox Render::CLuminophoreLivePipRenderer::decorationBounds(const Luminophore::SLivePipRect& d) {
    const auto padding = std::ceil(std::max(128.0, Luminophore::visualSettings()->current().bundle.glowExtent * 2.0));
    return CBox{d.x - 3, d.y - 3, d.width + 6, d.height + 6}.expand(padding);
}

void Render::CLuminophoreLivePipRenderer::discard(uint64_t id) {
    pipBloom().discard("pip-" + std::to_string(id));
}

void Render::CLuminophoreLivePipRenderer::enqueue(const Luminophore::SLivePipEntry& entry, const SP<Luminophore::CLuminophoreSurfaceSource>& source, PHLMONITOR monitor, const Time::steady_tp& time,
                                           Vector2D pointer, bool hovered) {
    const auto local  = CBox{entry.destination.x, entry.destination.y, entry.destination.width, entry.destination.height}.translate(-monitor->m_position);
    const auto accent = Luminophore::shellProjection()->accentForMonitor(monitor).value_or(luminophoreEffectConfig().activeColor);
    const auto rect   = [&](CBox box, CHyprColor color, int radius = 0) {
        g_pHyprRenderer->m_renderPass.add(
            makeUnique<CRectPassElement>(CRectPassElement::SRectData{.box = box.scale(monitor->m_scale), .color = color, .round = int(radius * monitor->m_scale)}));
    };
    auto visual = Luminophore::visualSettings()->current().bundle;
    // A still source must not require an extra animation loop or frozen breathing phase.
    visual.animated           = false;
    visual.settings.breathing = false;
    const CBox frame{local.x - 3, local.y - 3, local.width + 6, local.height + 6};
    pipBloom().enqueue("pip-" + std::to_string(entry.id), monitor, accent, 8, 1, visual, 0, frame.copy().translate(monitor->m_position), hovered ? 0.9F : 0.6F);
    rect(frame, CHyprColor{accent.r, accent.g, accent.b, hovered ? .85F : .55F}, 8);
    rect(frame.copy().expand(-1), CHyprColor{.035F, .045F, .055F, 1.F}, 7);
    rect({local.x, local.y - 1, local.width, 1}, CHyprColor{accent.r, accent.g, accent.b, .45F});
    rect(local, CHyprColor{0.035F, 0.045F, 0.055F, 1.F});
    const auto text = [&](const std::string& value, double x, double y, double maxWidth) {
        static std::map<std::pair<std::string, int>, SP<Render::ITexture>> cache;
        const int                                                          font = std::clamp(int(12 * monitor->m_scale), 8, 64);
        const auto                                                         key  = std::pair{value, font};
        if (!cache.contains(key))
            cache[key] = g_pHyprRenderer->renderText(value, CHyprColor{1.F, 1.F, 1.F, 1.F}, font, false, "sans-serif");
        const auto texture = cache[key];
        if (!texture)
            return;
        CTexPassElement::SRenderData data;
        data.tex     = texture;
        data.box     = CBox{x * monitor->m_scale, y * monitor->m_scale, texture->m_size.x, texture->m_size.y};
        data.clipBox = CBox{x * monitor->m_scale, y * monitor->m_scale, maxWidth * monitor->m_scale, 22 * monitor->m_scale};
        g_pHyprRenderer->m_renderPass.add(makeUnique<CTexPassElement>(data));
    };
    const bool resizeHovered = hovered && pointer.x >= entry.destination.x + entry.destination.width - 12 && pointer.y >= entry.destination.y + entry.destination.height - 12;
    // Outside the content rectangle, so the source never paints over the grip.
    rect({local.x + local.width - 10, local.y + local.height, 10, 3}, CHyprColor{accent.r, accent.g, accent.b, resizeHovered ? 1.F : .5F});
    const auto surface = source->surface();
    const auto mapping = surface ? Luminophore::CLuminophoreLivePipMapping::sample(entry, source->snapshot(), surface->m_current) : std::nullopt;
    if (!mapping || !surface->m_current.texture) {
        text("원본 영역 변경", local.x + 8, local.y + std::max(0.0, (local.height - 18) / 2), std::max(1.0, local.width - 16));
    } else {
        CSurfacePassElement::SRenderData data;
        data.pMonitor        = monitor;
        data.when            = time;
        data.surface         = surface;
        data.texture         = surface->m_current.texture;
        data.sourceSampleBox = mapping->destination.copy().translate(-monitor->m_position);
        data.sourceSampleUV  = mapping->uv;
        data.spatialClip     = CRegion{data.sourceSampleBox->copy().scale(monitor->m_scale)};
        data.squishOversized = false;
        // Preserve the surface pass's color conversion, async buffer release tracking
        // and presentation feedback; desktop renderWindow/capture is never involved.
        g_pHyprRenderer->m_renderPass.add(makeUnique<CSurfacePassElement>(data));
    }
    for (const bool close : {false, true}) {
        const auto global = controlBox(entry.destination, close);
        const auto box    = global.copy().translate(-monitor->m_position);
        rect(box, CHyprColor{.025F, .035F, .045F, .94F}, 5);
        if (hovered && global.containsPoint(pointer))
            rect(box, CHyprColor{accent.r, accent.g, accent.b, .4F}, 5);
        text(close ? "×" : "↗", box.x + (box.width - 12) / 2, box.y + (box.height - 16) / 2, box.width);
    }
}

CBox Render::CLuminophoreLivePipRenderer::controlBox(const Luminophore::SLivePipRect& d, bool close) {
    const double size = std::max(1.0, std::min({24.0, (d.width - 6) / 2, d.height - 4}));
    return {d.x + d.width - 2 - size - (close ? 0 : size + 2), d.y + 2, size, size};
}
