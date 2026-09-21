#include "LuminophoreShellProjection.hpp"
#include "LuminophoreCompositionPolicy.hpp"
#include "LuminophoreMonitorTransaction.hpp"

#include "../Compositor.hpp"
#include "../desktop/view/LayerSurface.hpp"
#include "../managers/EventManager.hpp"
#include "../managers/eventLoop/EventLoopManager.hpp"
#include "../protocols/LayerShell.hpp"
#include "../protocols/core/Compositor.hpp"
#include "../render/Renderer.hpp"
#include "../render/luminophore/LuminophoreShellBloom.hpp"
#include "../state/MonitorState.hpp"

#include <algorithm>
#include <cmath>
#include <format>

using namespace Luminophore;

static constexpr auto  SHELL_BLOOM_FRAME_INTERVAL = std::chrono::microseconds(13889);
static constexpr float SHELL_BLOOM_DAMAGE_PADDING = 128.F;
static constexpr float SHELL_DROP_HIT_PADDING     = 24.F;

CLuminophoreShellProjection::CLuminophoreShellProjection() : m_bloom(makeUnique<Render::CLuminophoreShellBloom>()) {}

CLuminophoreShellProjection::~CLuminophoreShellProjection() {
    if (!g_pEventLoopManager)
        return;

    for (auto& [id, animation] : m_bloomAnimations) {
        if (!animation.timer)
            continue;
        animation.timer->cancel();
        g_pEventLoopManager->removeTimer(animation.timer);
    }
}

bool CLuminophoreShellProjection::transact(const std::string& surfaceNamespace, const std::string& generation, uint64_t revision, uint64_t contentRevision, eShellProjectionPhase phase,
                                    eShellProjectionPlane requestedPlane, eShellWidgetRole role, const SShellProjectionStyle& style) {
    if (!std::isfinite(style.blurPanel.x) || !std::isfinite(style.blurPanel.y) || !std::isfinite(style.blurPanel.width) || !std::isfinite(style.blurPanel.height) ||
        style.blurPanel.x < 0.0 || style.blurPanel.y < 0.0 || style.blurPanel.width < 0.0 || style.blurPanel.height < 0.0 || style.blurPanel.x > 16384.0 ||
        style.blurPanel.y > 16384.0 || style.blurPanel.width > 16384.0 || style.blurPanel.height > 16384.0)
        return false;
    if (!surfaceNamespace.starts_with("luminophore-shell-") || generation.empty() || revision == 0)
        return false;
    if (!std::isfinite(style.radius) || !std::isfinite(style.outline) || !std::isfinite(style.extent) || !std::isfinite(style.intensity) || !std::isfinite(style.phase) ||
        !std::isfinite(style.revealFrom) || !std::isfinite(style.revealTo) || !std::isfinite(style.revealStarted) || !std::isfinite(style.revealDuration) ||
        !std::isfinite(style.revealOffset.x) || !std::isfinite(style.revealOffset.y) || !std::isfinite(style.panel.x) || !std::isfinite(style.panel.y) ||
        !std::isfinite(style.panel.width) || !std::isfinite(style.panel.height) || style.radius < 0.F || style.radius > 256.F || style.outline < 0.F || style.outline > 32.F ||
        style.extent < 1.F || style.extent > 512.F || style.intensity < 0.F || style.intensity > 8.F || style.revealFrom < 0.F || style.revealFrom > 1.F || style.revealTo < 0.F ||
        style.revealTo > 1.F || style.revealDuration < 0.F || style.revealDuration > 10.F || std::abs(style.revealOffset.x) > 512.F || std::abs(style.revealOffset.y) > 512.F ||
        style.panel.x < -1.F || style.panel.y < -1.F || style.panel.width < 0.F || style.panel.height < 0.F || style.panel.width > 16384.F || style.panel.height > 16384.F)
        return false;

    discardExpired();
    const auto latest = m_latestRevision.find(surfaceNamespace);

    if (phase == SHELL_PROJECTION_ABORT) {
        if (latest != m_latestRevision.end() && revision < latest->second)
            return true;
        if (const auto IT = m_prepared.find(surfaceNamespace); IT != m_prepared.end() && IT->second->generation == generation && IT->second->revision == revision)
            m_prepared.erase(IT);
        if (const auto IT = m_pending.find(surfaceNamespace); IT != m_pending.end() && IT->second->generation == generation && IT->second->revision == revision)
            m_pending.erase(IT);
        return true;
    }

    if (phase == SHELL_PROJECTION_PREPARE) {
        if (latest != m_latestRevision.end() && revision < latest->second)
            return false;
        const auto sameRequest = [&](const auto& snapshots) {
            const auto it = snapshots.find(surfaceNamespace);
            return it != snapshots.end() && it->second->revision == revision && it->second->generation == generation;
        };
        if (sameRequest(m_prepared) || sameRequest(m_pending) || sameRequest(m_committed))
            return true;
        if (latest != m_latestRevision.end() && revision == latest->second)
            return false;

        auto snapshot = makeSnapshot(surfaceNamespace, generation, revision, contentRevision, requestedPlane, role, style);
        if (!snapshot)
            return false;
        m_latestRevision[surfaceNamespace] = revision;
        m_prepared[surfaceNamespace]       = std::move(snapshot);
        if (const auto pending = m_pending.find(surfaceNamespace); pending != m_pending.end() && pending->second->revision < revision)
            m_pending.erase(pending);
        return true;
    }

    const auto IT = m_prepared.find(surfaceNamespace);
    if (IT == m_prepared.end()) {
        const auto PENDING = m_pending.find(surfaceNamespace);
        if (PENDING != m_pending.end() && PENDING->second->generation == generation && PENDING->second->revision == revision)
            return true;
        const auto COMMITTED = m_committed.find(surfaceNamespace);
        return COMMITTED != m_committed.end() && COMMITTED->second->generation == generation && COMMITTED->second->revision == revision;
    }
    if (IT->second->generation != generation || IT->second->revision != revision || IT->second->requestedPlane != requestedPlane)
        return false;
    if (!stillBound(IT->second))
        return false;

    m_pending[surfaceNamespace] = IT->second;
    m_prepared.erase(IT);
    const auto MONITOR = m_pending[surfaceNamespace]->monitor.lock();
    monitorTransaction()->requestFrame(MONITOR);
    return true;
}

std::vector<SShellProjectionReceipt> CLuminophoreShellProjection::receiptsFor(const std::string& surfaceNamespace) const {
    std::vector<SShellProjectionReceipt> result;
    const auto                           append = [&](const auto& snapshots, eShellSnapshotState state) {
        const auto it = snapshots.find(surfaceNamespace);
        if (it != snapshots.end() && stillBound(it->second))
            result.push_back({it->second->generation, it->second->revision, it->second->contentRevision, state});
    };
    append(m_prepared, SHELL_SNAPSHOT_PREPARED);
    append(m_pending, SHELL_SNAPSHOT_COMMITTED);
    append(m_committed, SHELL_SNAPSHOT_COMMITTED);
    const auto frame     = m_frameSnapshots.find(surfaceNamespace);
    const auto committed = m_committed.find(surfaceNamespace);
    if (frame != m_frameSnapshots.end() && committed != m_committed.end() && stillBound(committed->second) && frame->second->generation == committed->second->generation &&
        frame->second->revision == committed->second->revision && frame->second->contentRevision == committed->second->contentRevision &&
        frame->second->state == SHELL_SNAPSHOT_PRESENTED)
        result.push_back({frame->second->generation, frame->second->revision, frame->second->contentRevision, SHELL_SNAPSHOT_PRESENTED});
    return result;
}

SShellProjectionFrame CLuminophoreShellProjection::frameFor(PHLLS surface, PHLMONITOR monitor) const {
    if (!isProjected(surface, monitor))
        return {};

    const auto& STYLE    = m_committed.at(surface->m_namespace)->style;
    const auto  NOW      = std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    const float FRACTION = STYLE.revealDuration <= 0.F ? 1.F : std::clamp((float)((NOW - STYLE.revealStarted) / STYLE.revealDuration), 0.F, 1.F);
    const float EASED    = STYLE.revealTo >= STYLE.revealFrom ? 1.F - std::pow(1.F - FRACTION, 3.F) : FRACTION * FRACTION * FRACTION;
    const float OPACITY  = std::clamp(STYLE.revealFrom + (STYLE.revealTo - STYLE.revealFrom) * EASED, 0.F, 1.F);
    return {
        .offset  = STYLE.revealOffset * (1.F - OPACITY),
        .opacity = OPACITY,
        .active  = FRACTION < 1.F,
        .panel   = panelFor(m_committed.at(surface->m_namespace)),
    };
}

PShellSurfaceFrameSnapshot CLuminophoreShellProjection::snapshotFor(PHLLS surface, PHLMONITOR monitor) const {
    if (!surface || !monitor)
        return {};
    const auto IT = m_frameSnapshots.find(surface->m_namespace);
    if (IT == m_frameSnapshots.end() || IT->second->surface.lock() != surface || IT->second->monitor.lock() != monitor)
        return {};
    return IT->second;
}

std::vector<PShellSurfaceFrameSnapshot> CLuminophoreShellProjection::renderList(PHLMONITOR monitor, eShellProjectionPlane plane) const {
    std::vector<PShellSurfaceFrameSnapshot> result;
    if (!monitor)
        return result;

    for (const auto& [name, snapshot] : m_frameSnapshots) {
        if (snapshot->monitor.lock() == monitor && snapshot->plane == plane && snapshot->mapped && snapshot->visible)
            result.emplace_back(snapshot);
    }
    std::ranges::sort(result, {}, [](const auto& snapshot) { return snapshot->surfaceNamespace; });
    return result;
}

void CLuminophoreShellProjection::visualSettingsChanged() {
    if (!g_pHyprRenderer)
        return;
    // A full output damage includes both the previous effect footprint and the
    // new one, including retained bloom-cache allocations. Content/input stay intact.
    for (const auto& [name, snapshot] : m_committed) {
        if (const auto monitor = snapshot->monitor.lock()) {
            g_pHyprRenderer->damageMonitor(monitor);
            scheduleBloomFrame(monitor);
        }
    }
}

void CLuminophoreShellProjection::enqueueBloom(const PShellSurfaceFrameSnapshot& snapshot) {
    if (!snapshot || !m_bloom || !snapshot->visible || !snapshot->style.bloom)
        return;
    const auto MONITOR = snapshot->monitor.lock();
    if (!MONITOR || snapshotFor(snapshot->surface.lock(), MONITOR) != snapshot)
        return;
    const auto& STYLE = snapshot->style;
    m_bloom->enqueue(snapshot->surfaceNamespace, MONITOR, STYLE.color, STYLE.radius, STYLE.outline, snapshot->visualBundle, STYLE.phase, snapshot->renderBox, snapshot->opacity);
}

bool CLuminophoreShellProjection::isProjected(PHLLS surface, PHLMONITOR monitor) const {
    if (!surface || !monitor)
        return false;
    const auto IT = m_committed.find(surface->m_namespace);
    return IT != m_committed.end() && IT->second->surface.lock() == surface && IT->second->monitor.lock() == monitor && stillBound(IT->second);
}

bool CLuminophoreShellProjection::isProjectedBottom(PHLLS surface, PHLMONITOR monitor) const {
    if (!isProjected(surface, monitor))
        return false;
    return effectivePlaneFor(m_committed.at(surface->m_namespace)) == SHELL_PROJECTION_BOTTOM;
}

bool CLuminophoreShellProjection::isProjectedOverlay(PHLLS surface, PHLMONITOR monitor) const {
    if (!isProjected(surface, monitor))
        return false;
    return effectivePlaneFor(m_committed.at(surface->m_namespace)) == SHELL_PROJECTION_OVERLAY;
}

std::vector<PHLLSREF> CLuminophoreShellProjection::projectedOverlaySurfaces(PHLMONITOR monitor) const {
    std::vector<PHLLSREF> result;
    for (const auto& SNAPSHOT : renderList(monitor, SHELL_PROJECTION_OVERLAY))
        result.emplace_back(SNAPSHOT->surface);
    return result;
}

std::vector<PHLLSREF> CLuminophoreShellProjection::physicalBottomSurfaces(PHLMONITOR monitor) const {
    std::vector<PHLLSREF> result;
    if (!monitor)
        return result;

    for (const auto& WEAK : monitor->m_layerSurfaceLayers[ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM]) {
        if (!isProjectedOverlay(WEAK.lock(), monitor))
            result.emplace_back(WEAK);
    }
    return result;
}

auto CLuminophoreShellProjection::makeSnapshot(const std::string& surfaceNamespace, const std::string& generation, uint64_t revision, uint64_t contentRevision,
                                        eShellProjectionPlane requestedPlane, eShellWidgetRole role, const SShellProjectionStyle& style) const -> PSnapshot {
    for (const auto& MONITOR : State::monitorState()->monitors()) {
        for (uint32_t layer = ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND; layer <= ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY; ++layer) {
            for (const auto& WEAK : MONITOR->m_layerSurfaceLayers[layer]) {
                const auto SURFACE = WEAK.lock();
                if (SURFACE && SURFACE->m_namespace == surfaceNamespace && SURFACE->m_mapped && SURFACE->m_layer == ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM) {
                    return std::make_shared<const SSnapshot>(SSnapshot{
                        .surfaceNamespace = surfaceNamespace,
                        .generation       = generation,
                        .revision         = revision,
                        .contentRevision  = contentRevision,
                        .requestedPlane   = requestedPlane,
                        .role             = role,
                        .style            = style,
                        .surface          = SURFACE,
                        .monitor          = MONITOR,
                        .queuedAt         = std::chrono::steady_clock::now(),
                    });
                }
            }
        }
    }
    return {};
}

bool CLuminophoreShellProjection::stillBound(const PSnapshot& snapshot) const {
    const auto surface = snapshot ? snapshot->surface.lock() : nullptr;
    const auto monitor = snapshot ? snapshot->monitor.lock() : nullptr;
    return surface && monitor && surface->m_mapped && surface->m_namespace == snapshot->surfaceNamespace && surface->m_monitor.lock() == monitor;
}

eShellProjectionPlane CLuminophoreShellProjection::effectivePlaneFor(const PSnapshot& snapshot) const {
    if (!snapshot || snapshot->requestedPlane != SHELL_PROJECTION_OVERLAY)
        return SHELL_PROJECTION_BOTTOM;
    const auto monitor = snapshot->monitor.lock();
    if (!monitor)
        return SHELL_PROJECTION_BOTTOM;
    const auto it = m_framePlanes.find(monitor->m_id);
    return it == m_framePlanes.end() ? SHELL_PROJECTION_BOTTOM : it->second;
}

CBox CLuminophoreShellProjection::panelFor(const PSnapshot& snapshot) const {
    if (!stillBound(snapshot))
        return {};

    // Explicit panel geometry is authoritative for compact surfaces such as
    // the OSD.  A zero-sized panel is the protocol sentinel used by full-output
    // corner surfaces to select their live input region instead.
    if (snapshot->style.panel.width > 0.F && snapshot->style.panel.height > 0.F)
        return snapshot->style.panel;

    const auto SURFACE  = snapshot->surface.lock();
    const auto RESOURCE = SURFACE->wlSurface()->resource();
    if (RESOURCE) {
        const auto INPUT = RESOURCE->m_current.effectiveInputRegion().getExtents();
        if (INPUT.width > 0.F && INPUT.height > 0.F)
            return INPUT;
    }

    return {0, 0, SURFACE->m_geometry.width, SURFACE->m_geometry.height};
}

PShellSurfaceFrameSnapshot CLuminophoreShellProjection::resolveSnapshot(const PSnapshot& snapshot, PHLMONITOR monitor, eShellSnapshotState state) const {
    if (!snapshot || !monitor || snapshot->monitor.lock() != monitor || !stillBound(snapshot))
        return {};

    const auto SURFACE = snapshot->surface.lock();
    const auto FRAME   = frameFor(SURFACE, monitor);
    if (FRAME.panel.empty())
        return {};

    const auto ORIGIN      = SURFACE->position(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
    const CBox CONTENT_BOX = {ORIGIN.x + FRAME.panel.x, ORIGIN.y + FRAME.panel.y, FRAME.panel.width, FRAME.panel.height};
    const CBox RENDER_BOX  = CONTENT_BOX.copy().translate(FRAME.offset);
    const auto VISUAL      = visualSettings()->current().bundle;
    const CBox BLOOM_BOX   = snapshot->style.bloom && VISUAL.glowIntensity > 0.0 ?
        RENDER_BOX.copy().expand(std::max<double>(SHELL_BLOOM_DAMAGE_PADDING / monitor->m_scale, VISUAL.glowExtent * 2.0)) :
        RENDER_BOX;
    const CBox HIT_BOX     = RENDER_BOX.copy().expand(SHELL_DROP_HIT_PADDING);

    return std::make_shared<const SShellSurfaceFrameSnapshot>(SShellSurfaceFrameSnapshot{
        .surfaceNamespace = snapshot->surfaceNamespace,
        .generation       = snapshot->generation,
        .revision         = snapshot->revision,
        .contentRevision  = snapshot->contentRevision,
        .surface          = snapshot->surface,
        .monitor          = snapshot->monitor,
        .role             = snapshot->role,
        .plane            = effectivePlaneFor(snapshot),
        .contentBox       = CONTENT_BOX,
        .renderBox        = RENDER_BOX,
        // Blur the app backdrop before compositing the editor texture.
        .blurBox =
            snapshot->surfaceNamespace == "luminophore-shell-spatial-editor" ? snapshot->style.blurPanel.copy().translate(ORIGIN + FRAME.offset).intersection(RENDER_BOX) : RENDER_BOX,
        .bloomBox       = BLOOM_BOX,
        .hitBox         = HIT_BOX,
        .offset         = FRAME.offset,
        .opacity        = FRAME.opacity,
        .style          = snapshot->style,
        .visualBundle   = VISUAL,
        .visualRevision = visualSettings()->revision(),
        .state          = state,
        .mapped         = SURFACE->m_mapped,
        .visible        = SURFACE->visible() && FRAME.opacity > 0.F,
    });
}

void CLuminophoreShellProjection::damageSnapshot(const PSnapshot& snapshot) const {
    damagePanel(snapshot, panelFor(snapshot));
}

void CLuminophoreShellProjection::damagePanel(const PSnapshot& snapshot, const CBox& panel) const {
    if (!stillBound(snapshot) || panel.width <= 0.F || panel.height <= 0.F)
        return;

    const auto SURFACE  = snapshot->surface.lock();
    const auto MONITOR  = snapshot->monitor.lock();
    const auto POSITION = SURFACE->position(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
    const auto PADDING  = snapshot->style.bloom ?
        std::max<double>(SHELL_BLOOM_DAMAGE_PADDING / MONITOR->m_scale, std::max<double>(snapshot->style.extent, visualSettings()->current().bundle.glowExtent) * 2.0) :
        0.F;
    CBox       DAMAGE   = {POSITION.x + panel.x, POSITION.y + panel.y, panel.width, panel.height};
    g_pHyprRenderer->damageBox(DAMAGE.expand(PADDING).round());
}

void CLuminophoreShellProjection::damageFrameSnapshot(const PShellSurfaceFrameSnapshot& snapshot) const {
    if (!snapshot || !g_pHyprRenderer)
        return;
    g_pHyprRenderer->damageBox(snapshot->bloomBox.copy().round());
}

bool CLuminophoreShellProjection::hasAnimatedBloom(PHLMONITOR monitor) const {
    if (!monitor)
        return false;

    return std::ranges::any_of(m_committed, [&](const auto& item) {
        const auto& SNAPSHOT = item.second;
        if (SNAPSHOT->monitor.lock() != monitor || !stillBound(SNAPSHOT))
            return false;
        const auto SURFACE = SNAPSHOT->surface.lock();
        return frameFor(SURFACE, monitor).active || (SNAPSHOT->style.bloom && visualSettings()->current().bundle.animated);
    });
}

void CLuminophoreShellProjection::scheduleBloomFrame(PHLMONITOR monitor) {
    if (!monitor || !g_pEventLoopManager || !hasAnimatedBloom(monitor))
        return;

    auto& animation   = m_bloomAnimations[monitor->m_id];
    animation.monitor = monitor;
    if (!animation.timer) {
        const auto ID   = monitor->m_id;
        animation.timer = makeShared<CEventLoopTimer>(SHELL_BLOOM_FRAME_INTERVAL, [this, ID](SP<CEventLoopTimer>, void*) { requestBloomFrame(ID); }, nullptr);
        g_pEventLoopManager->addTimer(animation.timer);
    } else if (!animation.timer->armed())
        animation.timer->updateTimeout(SHELL_BLOOM_FRAME_INTERVAL);
}

void CLuminophoreShellProjection::requestBloomFrame(MONITORID monitorID) {
    const auto IT = m_bloomAnimations.find(monitorID);
    if (IT == m_bloomAnimations.end())
        return;

    const auto MONITOR = IT->second.monitor.lock();
    if (!hasAnimatedBloom(MONITOR)) {
        if (IT->second.timer)
            IT->second.timer->updateTimeout(std::nullopt);
        return;
    }

    for (const auto& [name, snapshot] : m_committed) {
        if (snapshot->monitor.lock() == MONITOR && stillBound(snapshot) &&
            (frameFor(snapshot->surface.lock(), MONITOR).active || (snapshot->style.bloom && visualSettings()->current().bundle.animated)))
            damageSnapshot(snapshot);
    }

    // CEventLoopTimer is one-shot: its deadline is cleared before the callback.
    // Own both the output request and the next deadline here so breathing does
    // not depend on an unrelated wl_surface commit to restart the cycle.
    monitorTransaction()->requestFrame(MONITOR);
    if (IT->second.timer)
        IT->second.timer->updateTimeout(SHELL_BLOOM_FRAME_INTERVAL);
}

void CLuminophoreShellProjection::discardExpired() {
    const auto NOW = std::chrono::steady_clock::now();
    std::erase_if(m_prepared, [&](const auto& item) { return NOW - item.second->queuedAt > std::chrono::milliseconds(500); });
    std::erase_if(m_pending, [&](const auto& item) { return NOW - item.second->queuedAt > std::chrono::seconds(1); });
    std::erase_if(m_committed, [&](const auto& item) {
        if (stillBound(item.second))
            return false;
        if (m_bloom)
            m_bloom->discard(item.first);
        m_framePanels.erase(item.first);
        m_frameSnapshots.erase(item.first);
        return true;
    });
    std::erase_if(m_awaitingPresentation, [&](const auto& item) { return !stillBound(item.second); });
}

void CLuminophoreShellProjection::applyPendingForMonitor(PHLMONITOR monitor) {
    if (!monitor)
        return;
    discardExpired();

    for (auto IT = m_pending.begin(); IT != m_pending.end();) {
        if (IT->second->monitor.lock() != monitor) {
            ++IT;
            continue;
        }
        if (!stillBound(IT->second)) {
            IT = m_pending.erase(IT);
            continue;
        }

        const auto NAMESPACE = IT->first;
        const auto SNAPSHOT  = IT->second;
        IT                   = m_pending.erase(IT);
        if (!stillBound(SNAPSHOT))
            continue;

        const auto OLD_FRAME = m_frameSnapshots.find(NAMESPACE);
        if (OLD_FRAME != m_frameSnapshots.end())
            damageFrameSnapshot(OLD_FRAME->second);
        // Plane, reveal and content revisions do not invalidate the bloom
        // allocation. CLuminophoreShellBloom::render() compares the actual geometry
        // and shader parameters through luminophore_bloom_prepare_capacity(). Keeping
        // this cache also preserves createdAt, avoiding a fresh 200 ms age ramp
        // every time the overview is promoted above client windows.
        m_committed[NAMESPACE]            = SNAPSHOT;
        m_awaitingPresentation[NAMESPACE] = SNAPSHOT;
    }
}

void CLuminophoreShellProjection::resolveFrameForMonitor(PHLMONITOR monitor) {
    if (!monitor)
        return;
    const auto FRAME_PLANE       = compositionPolicy()->effectiveShellPlane(monitor, SHELL_PROJECTION_OVERLAY);
    const auto OLD_PLANE         = m_framePlanes.find(monitor->m_id);
    const bool PLANE_CHANGED     = OLD_PLANE == m_framePlanes.end() || OLD_PLANE->second != FRAME_PLANE;
    m_framePlanes[monitor->m_id] = FRAME_PLANE;

    for (const auto& [name, snapshot] : m_committed) {
        if (snapshot->monitor.lock() != monitor || !stillBound(snapshot))
            continue;
        const auto panel     = panelFor(snapshot);
        const auto OLD_FRAME = m_frameSnapshots.find(name);
        const auto STATE     = OLD_FRAME != m_frameSnapshots.end() && OLD_FRAME->second->revision == snapshot->revision && OLD_FRAME->second->state == SHELL_SNAPSHOT_PRESENTED ?
            SHELL_SNAPSHOT_PRESENTED :
            SHELL_SNAPSHOT_COMMITTED;
        if (const auto frameSnapshot = resolveSnapshot(snapshot, monitor, STATE))
            m_frameSnapshots[name] = frameSnapshot;
        else
            m_frameSnapshots.erase(name);
        const auto old = m_framePanels.find(name);
        if (!PLANE_CHANGED && old != m_framePanels.end() && old->second == panel)
            continue;
        if (old != m_framePanels.end())
            damagePanel(snapshot, old->second);
        damagePanel(snapshot, panel);
        m_framePanels[name] = panel;
    }
}

void CLuminophoreShellProjection::presentedForMonitor(PHLMONITOR monitor) {
    if (!monitor)
        return;

    for (auto IT = m_awaitingPresentation.begin(); IT != m_awaitingPresentation.end();) {
        const auto SNAPSHOT = IT->second;
        if (SNAPSHOT->monitor.lock() != monitor) {
            ++IT;
            continue;
        }

        IT = m_awaitingPresentation.erase(IT);
        if (!stillBound(SNAPSHOT))
            continue;

        if (const auto frameSnapshot = resolveSnapshot(SNAPSHOT, monitor, SHELL_SNAPSHOT_PRESENTED))
            m_frameSnapshots[SNAPSHOT->surfaceNamespace] = frameSnapshot;

        if (!g_pEventManager)
            continue;

        g_pEventManager->postEvent(SHyprIPCEvent{
            .event = "luminophoreshellpresented",
            .data  = std::format("{},{},{},{}", SNAPSHOT->surfaceNamespace, SNAPSHOT->generation, SNAPSHOT->revision, SNAPSHOT->contentRevision),
        });
    }

    scheduleBloomFrame(monitor);
}

bool CLuminophoreShellProjection::blocksDirectScanout(PHLMONITOR monitor) const {
    if (!monitor)
        return false;

    for (const auto& [NAMESPACE, SNAPSHOT] : m_committed) {
        if (SNAPSHOT->monitor.lock() == monitor && effectivePlaneFor(SNAPSHOT) == SHELL_PROJECTION_OVERLAY && stillBound(SNAPSHOT))
            return true;
    }
    return false;
}

Render::SLuminophoreShellBloomDiagnostics CLuminophoreShellProjection::bloomDiagnostics() const {
    return m_bloom ? m_bloom->diagnostics() : Render::SLuminophoreShellBloomDiagnostics{};
}

UP<CLuminophoreShellProjection>& Luminophore::shellProjection() {
    static UP<CLuminophoreShellProjection> projection = makeUnique<CLuminophoreShellProjection>();
    return projection;
}

std::optional<CHyprColor> CLuminophoreShellProjection::accentForMonitor(PHLMONITOR monitor) const {
    PSnapshot selected;
    for (const auto& [name, snapshot] : m_committed) {
        if (snapshot->monitor.lock() != monitor || snapshot->role != SHELL_WIDGET_PASSIVE || !stillBound(snapshot))
            continue;
        if (!selected || name < selected->surfaceNamespace)
            selected = snapshot;
    }
    return selected ? std::optional<CHyprColor>{selected->style.color} : std::nullopt;
}
