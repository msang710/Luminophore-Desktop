#include "../Compositor.hpp"
#include "LuminophoreLivePipController.hpp"
#include "LuminophoreLivePipMapping.hpp"
#include "../protocols/core/Compositor.hpp"
#include "../protocols/LayerShell.hpp"
#include "../render/Renderer.hpp"
#include "../render/luminophore/LuminophoreLivePipRenderer.hpp"
#include "../state/MonitorState.hpp"
#include "../output/Monitor.hpp"
#include "../managers/SessionLockManager.hpp"
#include "../helpers/MiscFunctions.hpp"
#include <format>
#include "../desktop/state/ViewState.hpp"
#include "../desktop/state/ViewHitTester.hpp"
#include "../desktop/view/LayerSurface.hpp"
#include "LuminophoreShellProjection.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "../desktop/view/Window.hpp"
#include "../desktop/view/WLSurface.hpp"
#include <algorithm>

using namespace Luminophore;

static PHLMONITOR pipMonitor(const std::string& output) {
    for (const auto& monitor : State::monitorState()->monitors())
        if (monitor->m_name == output)
            return monitor;
    return nullptr;
}

static bool pipIntersects(PHLMONITOR monitor, const SLivePipRect& destination) {
    if (!monitor || !CLuminophoreLivePipModel::validRect(destination))
        return false;
    const auto bounds = monitor->logicalBox();
    return destination.x < bounds.x + bounds.width && destination.y < bounds.y + bounds.height && destination.x + destination.width > bounds.x &&
        destination.y + destination.height > bounds.y;
}

UP<CLuminophoreLivePipController>& Luminophore::livePipController() {
    static auto controller = makeUnique<CLuminophoreLivePipController>();
    return controller;
}

void CLuminophoreLivePipController::damage(const SLivePipEntry& entry) const {
    if (g_pSessionLockManager->isSessionLocked())
        return;
    const auto monitor = pipMonitor(entry.output);
    if (!monitor)
        return;
    const auto& d = entry.destination;
    g_pHyprRenderer->damageBox(Render::CLuminophoreLivePipRenderer::decorationBounds(d));
}

std::optional<uint64_t> CLuminophoreLivePipController::create(const SP<CLuminophoreSurfaceSource>& source, uint64_t expectedRevision, uint64_t expectedExtentRevision, SLivePipRect crop,
                                                       const std::string& output, SLivePipRect destination) {
    if (m_model.entries().size() >= 4 || !source || expectedRevision != m_model.revision() || !pipIntersects(pipMonitor(output), destination) ||
        g_pSessionLockManager->isSessionLocked())
        return std::nullopt;
    const auto snapshot = source->snapshot();
    const auto surface  = source->surface();
    if (!surface)
        return std::nullopt;
    const auto id = m_model.create(snapshot, expectedExtentRevision, crop, output, destination);
    if (!id)
        return std::nullopt;
    if (!m_sources.contains(snapshot.token)) {
        auto subscription      = makeUnique<SSourceSubscription>();
        subscription->source   = source;
        subscription->previous = snapshot;
        subscription->commit   = surface->m_events.commit.listen([this, token = snapshot.token] { sourceChanged(token); });
        subscription->destroy  = surface->m_events.destroy.listen([this, token = snapshot.token] { sourceChanged(token); });
        m_sources.emplace(snapshot.token, std::move(subscription));
    }
    damage(m_model.entries().at(*id));
    return id;
}

bool CLuminophoreLivePipController::update(uint64_t id, uint64_t expectedRevision, uint64_t expectedExtentRevision, SLivePipRect crop, const std::string& output,
                                    SLivePipRect destination) {
    const auto it = m_model.entries().find(id);
    if (it == m_model.entries().end() || !pipIntersects(pipMonitor(output), destination) || g_pSessionLockManager->isSessionLocked())
        return false;
    const auto old = it->second;
    if (!m_model.update(id, expectedRevision, m_sources.at(old.sourceToken)->source->snapshot(), expectedExtentRevision, crop, output, destination))
        return false;
    damage(old);
    damage(m_model.entries().at(id));
    return true;
}

bool CLuminophoreLivePipController::remove(uint64_t id, uint64_t expectedRevision) {
    const auto it = m_model.entries().find(id);
    if (expectedRevision != m_model.revision() || it == m_model.entries().end())
        return false;
    const auto old = it->second;
    if (!m_model.remove(id))
        return false;
    damage(old);
    Render::CLuminophoreLivePipRenderer::discard(id);
    if (m_hovered == id)
        m_hovered.reset();
    bool used = false;
    for (const auto& [otherID, entry] : m_model.entries())
        used |= entry.sourceToken == old.sourceToken;
    if (!used)
        m_sources.erase(old.sourceToken);
    return true;
}

void CLuminophoreLivePipController::sourceChanged(uint64_t token) {
    auto&      subscription = *m_sources.at(token);
    const auto current      = subscription.source->snapshot();
    if (!current.alive) {
        std::vector<uint64_t> removed;
        for (const auto& [id, entry] : m_model.entries())
            if (entry.sourceToken == token) {
                damage(entry);
                removed.push_back(id);
            }
        for (auto id : removed) {
            if (m_grab && m_grab->id == id)
                m_grab.reset();
            m_model.remove(id);
            Render::CLuminophoreLivePipRenderer::discard(id);
            if (m_hovered == id)
                m_hovered.reset();
        }
        // Subscription pruning happens outside the signal emission in render().
        return;
    }
    const auto previous   = subscription.previous;
    subscription.previous = current;
    const auto surface    = subscription.source->surface();
    const bool full       = !surface || previous.extent != current.extent || previous.mapped != current.mapped || previous.hasBuffer != current.hasBuffer ||
        (surface && (surface->m_current.updated.bits.viewport || surface->m_current.updated.bits.scale || surface->m_current.updated.bits.transform));
    CRegion changed;
    if (!full && current.extent) {
        auto& state = surface->m_current;
        changed     = state.accumulateBufferDamage();
        changed.transform(Math::wlTransformToHyprutils(state.transform), state.bufferSize.x, state.bufferSize.y).scale(1.0 / state.scale);
        if (state.viewport.hasSource)
            changed.intersect(state.viewport.source).translate(-state.viewport.source.pos());
        const auto sourceSize = state.sourceSize();
        if (sourceSize.x > 0 && sourceSize.y > 0)
            changed.scale(Vector2D{current.extent->width, current.extent->height} / sourceSize);
    }
    for (const auto& [id, entry] : m_model.entries()) {
        if (entry.sourceToken != token)
            continue;
        if (full || (surface && !surface->m_current.callbacks.empty())) {
            // Frame requests need a presentation even if the damaged pixels lie
            // outside the crop. The shared callback queue is consumed only once.
            damage(entry);
            continue;
        }
        for (const auto& rect : changed.getRects()) {
            auto mapped = CLuminophoreLivePipMapping::damage(entry, {double(rect.x1), double(rect.y1), double(rect.x2 - rect.x1), double(rect.y2 - rect.y1)});
            if (!mapped)
                continue;
            g_pHyprRenderer->damageBox(CBox{mapped->x, mapped->y, mapped->width, mapped->height});
        }
    }
}

bool CLuminophoreLivePipController::occupies(PHLMONITOR monitor) const {
    if (!monitor || g_pSessionLockManager->isSessionLocked())
        return false;
    for (const auto& [id, entry] : m_model.entries())
        if (entry.output == monitor->m_name && pipIntersects(monitor, entry.destination) && m_sources.at(entry.sourceToken)->source->snapshot().alive)
            return true;
    return false;
}

void CLuminophoreLivePipController::render(PHLMONITOR monitor, const Time::steady_tp& time) {
    reconcileOutputs();
    std::erase_if(m_sources, [&](const auto& source) {
        return std::none_of(m_model.entries().begin(), m_model.entries().end(), [&](const auto& e) { return e.second.sourceToken == source.first; });
    });
    if (!monitor || g_pSessionLockManager->isSessionLocked())
        return;
    for (const auto& [id, entry] : m_model.entries()) {
        if (entry.output != monitor->m_name || !pipIntersects(monitor, entry.destination))
            continue;
        Render::CLuminophoreLivePipRenderer::enqueue(entry, m_sources.at(entry.sourceToken)->source, monitor, time, m_pointer, m_hovered == id || (m_grab && m_grab->id == id));
    }
}

std::string CLuminophoreLivePipController::query() const {
    std::string entries;
    for (const auto& [id, entry] : m_model.entries()) {
        const auto source = m_sources.at(entry.sourceToken)->source->snapshot();
        if (!entries.empty())
            entries += ",";
        entries += std::format(R"({{"id":"{}","sourceToken":"{}","sourceRevision":"{}","available":{},"output":"{}","crop":[{},{},{},{}],"destination":[{},{},{},{}]}})", id,
                               source.token, source.revision, CLuminophoreLivePipModel::sampleable(entry, source), escapeJSONStrings(entry.output), entry.crop.x, entry.crop.y,
                               entry.crop.width, entry.crop.height, entry.destination.x, entry.destination.y, entry.destination.width, entry.destination.height);
    }
    std::string outputs;
    for (const auto& monitor : State::monitorState()->monitors()) {
        if (!outputs.empty())
            outputs += ",";
        const auto box = monitor->logicalBox();
        outputs += std::format(R"({{"name":"{}","x":{},"y":{},"width":{},"height":{}}})", escapeJSONStrings(monitor->m_name), box.x, box.y, box.width, box.height);
    }
    return std::format(R"({{"revision":"{}","entries":[{}],"instance":"{}","selection":true,"outputs":[{}]}})", m_model.revision(), entries,
                       escapeJSONStrings(g_pCompositor->m_instanceSignature), outputs);
}

uint64_t CLuminophoreLivePipController::revision() const {
    return m_model.revision();
}
size_t CLuminophoreLivePipController::count() const {
    return m_model.entries().size();
}
void CLuminophoreLivePipController::setEdgeMargin(double margin) {
    if (std::isfinite(margin))
        m_edgeMargin = std::clamp(margin, 0.0, 256.0);
}

bool CLuminophoreLivePipController::place(uint64_t id, uint64_t revision, const std::string& output, SLivePipRect destination) {
    const auto it = m_model.entries().find(id);
    if (it == m_model.entries().end() || !pipIntersects(pipMonitor(output), destination) || g_pSessionLockManager->isSessionLocked())
        return false;
    const auto before = it->second;
    if (!m_model.place(id, revision, output, destination))
        return false;
    damage(before);
    damage(m_model.entries().at(id));
    return true;
}

void CLuminophoreLivePipController::reconcileOutputs() {
    if (g_pSessionLockManager->isSessionLocked()) {
        cancelPointer();
        return;
    }
    PHLMONITOR right;
    for (const auto& monitor : State::monitorState()->monitors())
        if (!right || monitor->m_position.x > right->m_position.x)
            right = monitor;
    if (!right) {
        cancelPointer();
        return;
    }
    for (const auto& [id, entry] : m_model.entries()) {
        if (pipMonitor(entry.output))
            continue;
        m_grab.reset();
        auto         d      = entry.destination;
        const auto   bounds = right->logicalBox();
        const double ratio  = std::min({1.0, std::max(1.0, bounds.width - 2 * m_edgeMargin) / d.width, std::max(1.0, bounds.height - 2 * m_edgeMargin) / d.height});
        d.width *= ratio;
        d.height *= ratio;
        d.x = bounds.x + bounds.width - m_edgeMargin - d.width;
        d.y = bounds.y + bounds.height - m_edgeMargin - d.height;
        m_model.place(id, m_model.revision(), right->m_name, d);
        damage(m_model.entries().at(id));
    }
}

std::optional<uint64_t> CLuminophoreLivePipController::hit(Vector2D position) const {
    if (g_pSessionLockManager->isSessionLocked())
        return std::nullopt;
    const auto monitor = State::monitorState()->query().vec(position).run();
    if (!monitor)
        return std::nullopt;
    Vector2D   local;
    PHLLS      layer;
    const auto projected = Luminophore::shellProjection()->projectedOverlaySurfaces(monitor);
    if (Desktop::viewState()->hitTest().layerSurfaceAt(position, &projected, &local, &layer))
        return std::nullopt;
    for (auto plane : {ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, ZWLR_LAYER_SHELL_V1_LAYER_TOP})
        if (Desktop::viewState()->hitTest().layerSurfaceAt(position, &monitor->m_layerSurfaceLayers[plane], &local, &layer))
            return std::nullopt;
    for (auto it = m_model.entries().rbegin(); it != m_model.entries().rend(); ++it) {
        const auto& d = it->second.destination;
        if (it->second.output == monitor->m_name && CBox{d.x - 4, d.y - 4, d.width + 8, d.height + 8}.containsPoint(position))
            return it->first;
    }
    return std::nullopt;
}

bool CLuminophoreLivePipController::pointerAt(Vector2D position) const {
    return m_grab.has_value() || hit(position).has_value();
}

bool CLuminophoreLivePipController::cancelPointer() {
    if (!m_grab)
        return false;
    const auto before = m_grab->before;
    m_grab.reset();
    const auto it = m_model.entries().find(before.id);
    if (it != m_model.entries().end()) {
        damage(it->second);
        m_model.place(before.id, m_model.revision(), before.output, before.destination);
        damage(before);
    }
    return true;
}

bool CLuminophoreLivePipController::pointerButton(uint32_t button, bool pressed, Vector2D position) {
    if (!pressed && m_consumedButtons.erase(button)) {
        if (button == 272)
            m_grab.reset();
        return true;
    }
    if (g_pSessionLockManager->isSessionLocked()) {
        cancelPointer();
        return false;
    }
    const auto id = hit(position);
    if (!m_grab && !id)
        return false;
    if (!pressed)
        return false;
    m_consumedButtons.insert(button);
    if (m_grab) {
        cancelPointer();
        return true;
    }
    if (button != 272 || !id)
        return true;
    const auto  entry = m_model.entries().at(*id);
    const auto& d     = entry.destination;
    if (Render::CLuminophoreLivePipRenderer::controlBox(d, true).containsPoint(position)) {
        remove(*id, m_model.revision());
        return true;
    }
    if (Render::CLuminophoreLivePipRenderer::controlBox(d, false).containsPoint(position)) {
        const auto subscription = m_sources.find(entry.sourceToken);
        if (subscription == m_sources.end())
            return true;
        const auto source = subscription->second->source->surface();
        if (!source)
            return true;
        for (const auto& window : Desktop::viewState()->windows()) {
            if (!window->m_isMapped || !window->wlSurface()->resource())
                continue;
            bool matches = false;
            window->wlSurface()->resource()->breadthfirst([&](SP<CWLSurfaceResource> surface, const Vector2D&, void*) { matches = matches || surface == source; }, nullptr);
            if (!matches)
                continue;
            spatialRuntime()->revealWindow(window);
            break;
        }
        return true;
    }
    m_grab = SGrab{*id, position.x >= d.x + d.width - 12 && position.y >= d.y + d.height - 12, position, entry};
    return true;
}

bool CLuminophoreLivePipController::pointerMotion(Vector2D position) {
    const auto hovered = hit(position);
    const auto control = [&](std::optional<uint64_t> id, Vector2D point) {
        if (!id || !m_model.entries().contains(*id))
            return 0;
        const auto& d = m_model.entries().at(*id).destination;
        if (Render::CLuminophoreLivePipRenderer::controlBox(d, true).containsPoint(point))
            return 1;
        if (Render::CLuminophoreLivePipRenderer::controlBox(d, false).containsPoint(point))
            return 3;
        return point.y >= d.y + d.height - 12 && point.x >= d.x + d.width - 12 ? 2 : 0;
    };
    if (hovered != m_hovered || control(hovered, position) != control(m_hovered, m_pointer)) {
        if (m_hovered && m_model.entries().contains(*m_hovered))
            damage(m_model.entries().at(*m_hovered));
        if (hovered)
            damage(m_model.entries().at(*hovered));
    }
    m_hovered = hovered;
    m_pointer = position;
    if (g_pSessionLockManager->isSessionLocked()) {
        cancelPointer();
        return false;
    }
    if (!m_grab)
        return pointerAt(position);
    const auto monitor = State::monitorState()->query().vec(position).run();
    if (!monitor) {
        cancelPointer();
        return true;
    }
    auto       d     = m_grab->before.destination;
    const auto delta = position - m_grab->origin;
    if (m_grab->resize) {
        const double minimum = std::max(48.0 / d.width, 32.0 / d.height);
        const double ratio   = std::clamp((d.width + delta.x) / d.width, minimum, std::max(minimum, 8.0));
        d.width *= ratio;
        d.height *= ratio;
    } else {
        d.x += delta.x;
        d.y += delta.y;
    }
    const auto   bounds = monitor->logicalBox();
    const double fit    = std::min({1.0, bounds.width / d.width, std::max(1.0, bounds.height) / d.height});
    d.width *= fit;
    d.height *= fit;
    d.x = std::clamp(d.x, bounds.x, bounds.x + bounds.width - d.width);
    d.y = std::clamp(d.y, bounds.y, bounds.y + bounds.height - d.height);
    if (!place(m_grab->id, m_model.revision(), monitor->m_name, d))
        cancelPointer();
    return true;
}

std::vector<CBox> CLuminophoreLivePipController::selectionBlockers() const {
    std::vector<CBox> result;
    for (const auto& [id, e] : m_model.entries())
        result.emplace_back(e.destination.x - 4, e.destination.y - 4, e.destination.width + 8, e.destination.height + 8);
    return result;
}

bool CLuminophoreLivePipController::pointerOwned() const {
    return m_grab.has_value() || !m_consumedButtons.empty();
}
bool CLuminophoreLivePipController::escape(bool pressed) {
    if (!pressed && m_escapeHeld) {
        m_escapeHeld = false;
        return true;
    }
    if (pressed && (m_escapeHeld || cancelPointer())) {
        m_escapeHeld = true;
        return true;
    }
    return false;
}
