#include "LuminophoreSpatialGrabController.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "LuminophoreSpatialBadge.hpp"
#include "../render/Renderer.hpp"
#include "../render/pass/SurfacePassElement.hpp"
#include "../protocols/core/Compositor.hpp"
#include "../protocols/LayerShell.hpp"
#include "../desktop/view/WLSurface.hpp"
#include "LuminophoreSpatialPreviewMetrics.hpp"
#include "LuminophoreSpatialEditorProtocol.hpp"
#include "LuminophoreShellProjection.hpp"
#include "LuminophoreSpatialNativeAdapter.hpp"
#include "../animation/AnimationManager.hpp"

#include "../layout/target/Target.hpp"
#include "../Compositor.hpp"
#include "../helpers/MiscFunctions.hpp"
#include "../desktop/view/Window.hpp"
#include "../desktop/view/LayerSurface.hpp"
#include "../managers/EventManager.hpp"
#include "../state/MonitorState.hpp"
#include "../output/Monitor.hpp"

#include <format>
#include "../pointer/cursor/CursorShapeOverrideController.hpp"
#include "../layout/LayoutManager.hpp"
#include "../desktop/state/WindowState.hpp"

using namespace Luminophore;

static SLuminophoreSpatialSnapshot grabFacts(const SSpatialSnapshot& source) {
    SLuminophoreSpatialSnapshot result{.extent                 = source.extent,
                                .presentationMode       = source.presentationMode,
                                .outputViews            = source.outputViews,
                                .outputTopologyRevision = source.topologyRevision,
                                .revision               = source.revision};
    result.independent = source.independent;
    for (const auto& window : source.windows) {
        if (window.floating)
            result.floating.emplace_back(SLuminophoreFloatingPlacement{.key = window.key, .host = window.coordinate});
        else
            result.tiled.emplace_back(SLuminophoreTiledPlacement{.key = window.key, .point = window.coordinate});
    }
    return result;
}

UP<CLuminophoreSpatialGrabController>& Luminophore::spatialGrabController() {
    static auto controller = makeUnique<CLuminophoreSpatialGrabController>();
    return controller;
}

void CLuminophoreSpatialGrabController::publish(const char* phase, const SLuminophoreSpatialGrabState& state, const std::optional<SLuminophoreSpatialTransactionResult>& result) const {
    const PreviewMetrics::CTimer timer(PreviewMetrics::eStage::EVENT);
    if (!g_pEventManager)
        return;
    std::string finalFields;
    if (std::string_view{phase} == "end" || std::string_view{phase} == "cancel") {
        const auto settled = spatialRuntime()->snapshot();
        finalFields = std::format(",\"settledRevision\":{},\"settledTopologyRevision\":{},\"settledCommitted\":{}", settled.revision, settled.topologyRevision, settled.committed);
    }
    const auto frame   = editorFrame();
    const auto pointer = m_direct ? std::format("[{},{}]", m_pointerX, m_pointerY) : frame ? std::format("[{},{}]", m_pointerX - frame->x, m_pointerY - frame->y) : "null";
    g_pEventManager->postEvent(SHyprIPCEvent{
        .event = m_direct ? "luminophoredirectgrab" : "luminophorespatialgrab",
        .data  = std::format(
            R"({{"schema":2,"source":"{}","phase":"{}","generation":{},"revision":{},"topologyRevision":{},"output":{},"window":"0x{:x}","floating":{},"editorOrigin":{},"result":{},"pointer":{},"targetOutput":{},"targetEpoch":{}{}}})",
            escapeJSONStrings(g_pCompositor ? g_pCompositor->m_instanceSignature : ""), phase, state.generation, state.revision, state.topologyRevision, state.outputID,
            state.window, state.floating, m_options.origin == eLuminophoreDragOrigin::EDITOR, result ? CLuminophoreSpatialEditorProtocol::serialize(*result) : "null", pointer,
            state.targetOutputID, state.targetEpoch, finalFields),
    });
}

bool CLuminophoreSpatialGrabController::begin(const SP<Layout::ITarget>& target, double x, double y, SLuminophoreDragOptions options) {
    if (m_terminal)
        return false;
    if (active())
        end(true, 0, 0);
    if (!target || !target->window() || (!spatialRuntime()->managesTiledTarget(target) && !spatialRuntime()->managesFloatingTarget(target)))
        return false;
    const auto source = spatialRuntime()->snapshot();
    if (!source.active || !source.committed)
        return false;
    const auto key   = static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(target->window().get()));
    const auto state = m_grab.begin(grabFacts(source), key, source.selectedOutputID);
    if (!state)
        return false;
    m_options     = options;
    m_direct      = options.presentation == eLuminophoreDragPresentation::WINDOW;
    m_directStart = {x, y};
    m_directCells.clear();
    if (m_direct && !state->floating) {
        // Visible ownership takes precedence over the underlying empty grid cell.
        for (const auto& window : source.windows) {
            if (window.floating)
                continue;
            const auto entry = spatialRuntime()->presentationFor(window.key);
            if (!entry || !entry->visible)
                continue;
            for (const auto& fragment : entry->fragments)
                m_directCells.push_back({.point = window.coordinate, .outputID = fragment.outputID, .box = fragment.box});
        }
        const auto cells = spatialRuntime()->projectedCells();
        m_directCells.insert(m_directCells.end(), cells.begin(), cells.end());
    }
    m_updateQueue.cancel();
    m_previewCache.clear();
    m_target    = target;
    m_sourceBox = CBox{target->window()->positionAnimation()->value(), target->window()->sizeAnimation()->value()};
    m_badgeRenderer.reset();
    m_colorProgress.reset();
    m_shellClient.reset();
    m_editorSurface.reset();
    m_layoutReady = false;
    m_progress.reset();
    m_pointerX = x;
    m_pointerY = y;
    prepareVisual();
    publish("begin", *state);
    return true;
}

std::optional<SLuminophoreEditorFrame> CLuminophoreSpatialGrabController::editorFrame() const {
    if (!m_grab.state())
        return std::nullopt;
    for (const auto& monitor : State::monitorState()->monitors()) {
        if (!monitor || static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get())) != m_grab.state()->targetOutputID)
            continue;
        for (const auto& frame : shellProjection()->renderList(monitor, SHELL_PROJECTION_OVERLAY)) {
            const auto surface = frame->surface.lock();
            if (!surface || !surface->m_mapped || surface->m_monitor.lock() != monitor || !m_shellClient.accepts(surface->wlSurface()->resource()->client()))
                continue;
            if (frame->surfaceNamespace != "luminophore-shell-spatial-editor" || frame->state != SHELL_SNAPSHOT_PRESENTED || !frame->mapped || !frame->visible || frame->opacity < 1.F)
                continue;
            return SLuminophoreEditorFrame{.generation  = frame->generation,
                                    .revision    = frame->revision,
                                    .outputID    = m_grab.state()->targetOutputID,
                                    .x           = frame->renderBox.x,
                                    .y           = frame->renderBox.y,
                                    .width       = frame->renderBox.w,
                                    .height      = frame->renderBox.h,
                                    .targetEpoch = m_grab.state()->targetEpoch};
        }
    }
    return std::nullopt;
}

bool CLuminophoreSpatialGrabController::bindLayout(uint64_t generation, uint64_t revision, uint64_t topologyRevision, const std::string& frameGeneration, uint64_t frameRevision,
                                            const std::vector<SLuminophoreEditorCell>& cells, uint64_t targetEpoch) {
    const auto source = spatialRuntime()->snapshot();
    const auto frame  = editorFrame();
    if (!m_grab.state() || generation != m_grab.state()->generation || targetEpoch != m_grab.state()->targetEpoch)
        return false;
    ++m_layoutGeneration;
    m_previewCache.clear();
    if (!source.active || !source.committed || source.revision != revision || source.topologyRevision != topologyRevision || !frame || frame->generation != frameGeneration ||
        frame->revision != frameRevision) {
        m_layoutReady = false;
        m_grab.bindLayout(generation, grabFacts(source), SLuminophoreEditorFrame{.targetEpoch = targetEpoch}, {});
        update(m_pointerX, m_pointerY);
        return false;
    }
    const bool accepted = m_grab.bindLayout(generation, grabFacts(source), *frame, cells);
    if (accepted) {
        m_layoutReady = !cells.empty();
        if (m_layoutReady) {
            for (const auto& monitor : State::monitorState()->monitors()) {
                for (const auto& snapshot : shellProjection()->renderList(monitor, SHELL_PROJECTION_OVERLAY)) {
                    if (snapshot->surfaceNamespace == "luminophore-shell-spatial-editor" && snapshot->generation == frameGeneration && snapshot->revision == frameRevision)
                        m_editorSurface = snapshot->surface;
                    if (const auto editor = m_editorSurface.lock()) {
                        m_shellClient.bind(editor->wlSurface()->resource()->client(), [this] { update(m_pointerX, m_pointerY); });
                    }
                }
            }
            prepareVisual();
        }
        update(m_pointerX, m_pointerY);
    }
    return accepted;
}

void CLuminophoreSpatialGrabController::update(double x, double y) {
    if (!std::isfinite(x) || !std::isfinite(y))
        return;
    damageVisual();
    m_pointerX = x;
    m_pointerY = y;
    damageVisual();
    if (!m_grab.state())
        return;
    const auto token = m_updateQueue.push(x, y);
    if (!token) {
        PreviewMetrics::count(PreviewMetrics::eStage::COALESCED);
        return;
    }
    m_pendingUpdate = g_pEventLoopManager->doLaterLock([this, token = *token, lifetime = WP<bool>{m_lifetime}] {
        if (!lifetime.lock())
            return;
        const auto point = m_updateQueue.take(token);
        if (!point)
            return;
        m_pendingUpdate.reset();
        processUpdate(point->first, point->second);
    });
}

void CLuminophoreSpatialGrabController::processUpdate(double x, double y) {
    if (!m_grab.state())
        return;
    uint64_t output = 0;
    for (const auto& monitor : State::monitorState()->monitors()) {
        const auto box = monitor->logicalBox();
        if (x >= box.x && x < box.x + box.w && y >= box.y && y < box.y + box.h) {
            output = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get()));
            break;
        }
    }
    if (m_grab.selectTarget(output)) {
        ++m_layoutGeneration;
        m_layoutReady = false;
        m_editorSurface.reset();
        m_previewCache.clear();
        publish("update", *m_grab.state());
    }
    if (!m_grab.state())
        return;
    const auto state  = *m_grab.state();
    const auto source = spatialRuntime()->snapshot();
    const auto target = m_target.lock();
    if (m_shellClient.lost() || !target || !target->window() || static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(target->window().get())) != state.window || !source.active ||
        !source.committed || source.revision != state.revision || source.topologyRevision != state.topologyRevision || !spatialRuntime()->isBoardRoot(target->window()) ||
        target->floating() != state.floating || CLuminophoreSpatialWindowRegistry::participationFor(target) != eSpatialParticipation::BOARD_ROOT) {
        Log::logger->log(Log::DEBUG, "[luminophore-grab] cancel generation={} target={} epoch={} reason=source-or-shell-invalid", state.generation, state.targetOutputID,
                         state.targetEpoch);
        if (g_pEventManager)
            g_pEventManager->postEvent(SHyprIPCEvent{
                .event = "luminophorespatialgrabdiagnostic",
                .data  = std::format(
                    R"({{"generation":{},"targetEpoch":{},"expectedRevision":{},"actualRevision":{},"expectedTopology":{},"actualTopology":{},"shellLost":{},"targetLost":{},"active":{},"committed":{}}})",
                    state.generation, state.targetEpoch, state.revision, source.revision, state.topologyRevision, source.topologyRevision, m_shellClient.lost(),
                    !target || !target->window(), source.active, source.committed),
            });
        if (m_preparingEnd)
            m_cancelEnding = true;
        else
            end(true, x, y);
        return;
    }
    if (m_direct) {
        // Direct motion changes presentation only; the model is committed on release.
        const bool valid = state.floating ? floatingCommand(source, x, y).has_value() : directCommand(source, x, y).has_value();
        Pointer::Cursor::overrideController->setOverride(valid ? "grabbing" : "no-drop", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        publish("update", state);
        return;
    }
    const auto                   frame    = editorFrame();
    const auto                   command  = state.floating ? floatingCommand(source, x, y) : frame ? m_grab.commandAt(grabFacts(source), *frame, x, y) : std::nullopt;
    const auto                   payload  = command ? std::get_if<SMoveWindowToCommand>(&command->payload) : nullptr;
    const auto                   floating = command ? std::get_if<SUpdateFloatingCommand>(&command->payload) : nullptr;
    const SLuminophoreSpatialPreviewKey key{
        .generation            = state.generation,
        .revision              = source.revision,
        .topologyRevision      = source.topologyRevision,
        .observationGeneration = spatialRuntime()->observationGeneration(),
        .layoutGeneration      = m_layoutGeneration,
        .window                = state.window,
        .outputID              = payload ? payload->outputID :
            floating                     ? floating->outputID :
                                           state.targetOutputID,
        .point                 = payload ? std::optional{payload->point} :
            floating                     ? std::optional{floating->host} :
                                           std::nullopt,
        .frame                 = frame,
    };
    if (m_previewCache.matches(key)) {
        PreviewMetrics::count(PreviewMetrics::eStage::CACHE_HIT);
        if (!command)
            publish("update", state);
        return;
    }
    const auto result = command ? std::optional{spatialRuntime()->edit(*command, true)} : std::nullopt;
    // A transient rejection may recover without a model revision; don't cache it.
    if (!result || result->status == eLuminophoreSpatialTransactionStatus::APPLIED || result->status == eLuminophoreSpatialTransactionStatus::NO_CHANGE ||
        result->status == eLuminophoreSpatialTransactionStatus::NO_CAPACITY)
        m_previewCache.remember(key);
    else
        m_previewCache.clear();
    publish("update", state, result);
}

std::optional<SLuminophoreSpatialCommand> CLuminophoreSpatialGrabController::directCommand(const SSpatialSnapshot& source, double x, double y) const {
    if (!std::isfinite(x) || !std::isfinite(y))
        return std::nullopt;
    for (const auto& cell : m_directCells) {
        if (x >= cell.box.x && x < double(cell.box.x) + cell.box.width && y >= cell.box.y && y < double(cell.box.y) + cell.box.height)
            return m_grab.commandAtPoint(grabFacts(source), cell.outputID, cell.point);
    }
    return std::nullopt;
}

void CLuminophoreSpatialGrabController::end(bool cancelled, double x, double y, bool deferred) {
    if (!deferred && g_layoutManager->dragController()->target()) {
        g_layoutManager->cancelDragTarget();
        return;
    }
    m_preparingEnd = true;
    m_cancelEnding = false;
    if (!cancelled)
        processUpdate(x, y);
    cancelled      = cancelled || m_cancelEnding;
    m_preparingEnd = false;
    m_updateQueue.cancel();
    m_pendingUpdate.reset();
    m_previewCache.clear();
    if (!m_grab.state())
        return;
    if (!cancelled && std::isfinite(x) && std::isfinite(y)) {
        damageVisual();
        m_pointerX = x;
        m_pointerY = y;
    }
    const auto                         state  = *m_grab.state();
    const auto                         source = spatialRuntime()->snapshot();
    const auto                         target = m_target.lock();
    const auto                         frame  = editorFrame();
    std::optional<SLuminophoreSpatialCommand> command;
    if (!cancelled && !m_shellClient.lost() && target && target->window() && static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(target->window().get())) == state.window &&
        spatialRuntime()->isBoardRoot(target->window()) && target->floating() == state.floating &&
        CLuminophoreSpatialWindowRegistry::participationFor(target) == eSpatialParticipation::BOARD_ROOT && source.active && source.committed && (m_direct || frame)) {
        if (state.floating) {
            command = floatingCommand(source, x, y);
            m_grab.cancel();
        } else if (m_direct) {
            command = directCommand(source, x, y);
            m_grab.cancel();
        } else
            command = m_grab.release(grabFacts(source), *frame, x, y);
    } else
        m_grab.cancel();
    if (target && target->window()) {
        const auto window = target->window();
        if (const auto visual = visualBox(window)) {
            const auto positionGoal = window->positionAnimation()->goal(), sizeGoal = window->sizeAnimation()->goal();
            window->positionAnimation()->setValueAndWarp(visual->pos());
            window->sizeAnimation()->setValueAndWarp(visual->size());
            *window->positionAnimation() = positionGoal;
            *window->sizeAnimation()     = sizeGoal;
        }
    }
    m_progress.reset();
    m_badgeRenderer.reset();
    m_colorProgress.reset();
    m_shellClient.reset();
    m_editorSurface.reset();
    m_layoutReady = false;
    m_target.reset();
    SpatialNative::damageOutputs();
    // The grab has already been consumed before any commit can trigger callbacks.
    m_terminal = STerminal{.state = state, .cancelled = cancelled};
    if (command)
        m_terminal->result = spatialRuntime()->edit(*command, false);
    if (!deferred)
        finishEnd();
    PreviewMetrics::dump();
}

void CLuminophoreSpatialGrabController::finishEnd() {
    if (!m_terminal)
        return;
    g_pInputManager->settleSpatialDragFocus();
    const auto terminal = std::exchange(m_terminal, std::nullopt);
    publish(terminal->cancelled ? "cancel" : "end", terminal->state, terminal->result);
}

bool CLuminophoreSpatialGrabController::active() const {
    return m_grab.state().has_value();
}

CLuminophoreSpatialGrabController::~CLuminophoreSpatialGrabController() {
    m_lifetime.reset();
    m_pendingUpdate.reset();
}

std::optional<CBox> CLuminophoreSpatialGrabController::visualBox(const PHLWINDOW& window) const {
    const auto target = m_target.lock();
    if ((!m_progress && !m_direct) || !target || target->window() != window)
        return std::nullopt;
    if (m_direct)
        return CBox{m_sourceBox.pos() + Vector2D{m_pointerX, m_pointerY} - m_directStart, m_sourceBox.size()};
    return SpatialBadge::transition(m_sourceBox, badgeBox(), m_progress->value());
}
float CLuminophoreSpatialGrabController::visualAlpha(const PHLWINDOW& window) const {
    return !m_direct && visualBox(window) ? 1.F - std::clamp(m_progress->value(), 0.F, 1.F) : 1.F;
}

CBox CLuminophoreSpatialGrabController::badgeBox() const {
    return SpatialBadge::target({m_pointerX, m_pointerY}, {44, 44});
}

void CLuminophoreSpatialGrabController::damageVisual() const {
    if ((!m_progress && !m_direct) || !g_pHyprRenderer)
        return;
    g_pHyprRenderer->damageBox(badgeBox().copy().expand(32));
    const auto target = m_target.lock();
    if (target && target->window()) {
        if (const auto box = visualBox(target->window()))
            g_pHyprRenderer->damageBox(box->copy().expand(m_direct ? 64 : 2));
    }
}

void CLuminophoreSpatialGrabController::prepareVisual() {
    if (m_direct || m_progress || !m_grab.state())
        return;
    const auto target = m_target.lock();
    if (!target || !target->window())
        return;
    Animation::mgr()->createAnimation(0.F, m_progress, target->window()->positionAnimation()->getConfig().lock(), target->window(), AVARDAMAGE_ENTIRE);
    m_progress->setUpdateCallback([](auto) { SpatialNative::damageOutputs(); });
    *m_progress = 1.F;
}

bool CLuminophoreSpatialGrabController::badgeAsset(uint64_t generation, uint64_t epoch, const std::string& mask, const CHyprColor& color) {
    if (!m_grab.state() || generation != m_grab.state()->generation || epoch != m_grab.state()->targetEpoch || (!mask.empty() && mask.size() != 64 * 64 * 2))
        return false;
    std::vector<uint8_t> decoded;
    const auto           digit = [](char c) -> int { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
    for (size_t i = 0; i < mask.size(); i += 2) {
        const int a = digit(mask[i]), b = digit(mask[i + 1]);
        if (a < 0 || b < 0)
            return false;
        decoded.push_back(a * 16 + b);
    }
    damageVisual();
    if (!decoded.empty())
        m_badgeRenderer.setMask(std::move(decoded));
    const auto target = m_target.lock();
    if (m_colorProgress) {
        const float t = m_colorProgress->value();
        m_previousBadgeColor =
            CHyprColor{m_previousBadgeColor.r + (m_badgeColor.r - m_previousBadgeColor.r) * t, m_previousBadgeColor.g + (m_badgeColor.g - m_previousBadgeColor.g) * t,
                       m_previousBadgeColor.b + (m_badgeColor.b - m_previousBadgeColor.b) * t, 1.F};
    } else
        m_previousBadgeColor = m_badgeColor;
    m_badgeColor = color;
    if (target && target->window()) {
        Animation::mgr()->createAnimation(0.F, m_colorProgress, target->window()->positionAnimation()->getConfig().lock(), target->window(), AVARDAMAGE_ENTIRE);
        m_colorProgress->setUpdateCallback([](auto) { SpatialNative::damageOutputs(); });
        *m_colorProgress = 1.F;
    }
    damageVisual();
    return true;
}

void CLuminophoreSpatialGrabController::renderBadge(PHLMONITOR monitor, const Time::steady_tp& time) {
    prepareVisual();
    if (!m_progress)
        return;
    if (m_shellClient.lost() || !m_target.lock() || !m_target.lock()->window()) {
        update(m_pointerX, m_pointerY);
        return;
    }
    const float      t = m_colorProgress ? m_colorProgress->value() : 1.F;
    const CHyprColor color{m_previousBadgeColor.r + (m_badgeColor.r - m_previousBadgeColor.r) * t, m_previousBadgeColor.g + (m_badgeColor.g - m_previousBadgeColor.g) * t,
                           m_previousBadgeColor.b + (m_badgeColor.b - m_previousBadgeColor.b) * t, 1.F};
    m_badgeRenderer.enqueue(monitor, badgeBox(), color, m_progress->value());
}

bool CLuminophoreSpatialGrabController::beginFromEditor(LuminophoreWindowKey key, uint64_t revision, uint64_t topology, uint64_t output, uint32_t pressTime) {
    const auto report = [&](const char* reason, bool accepted = false) {
        Log::logger->log(Log::INFO, "[luminophore-editor-drag] press={} reason={} accepted={}", pressTime, reason, accepted);
        if (g_pEventManager)
            g_pEventManager->postEvent(SHyprIPCEvent{
                .event = "luminophorespatialgrabdiagnostic",
                .data  = std::format(R"({{"phase":"begin-check","press":{},"window":"0x{:x}","output":{},"revision":{},"topologyRevision":{},"reason":"{}","accepted":{}}})",
                                     pressTime, key, output, revision, topology, reason, accepted),
            });
        return accepted;
    };
    if (!pressTime || g_pInputManager->spatialDragPressTime() != pressTime)
        return report("press-not-held-or-mismatch");
    if (active())
        return report("spatial-grab-already-active");
    if (g_layoutManager->dragController()->target())
        return report("drag-target-already-active");
    const auto source = spatialRuntime()->snapshot();
    if (!source.active || !source.committed)
        return report("model-not-ready");
    if (source.revision != revision)
        return report("stale-model-revision");
    if (source.topologyRevision != topology)
        return report("stale-topology-revision");
    const auto press    = g_pInputManager->spatialDragPressPosition();
    bool       onEditor = false;
    for (const auto& monitor : State::monitorState()->monitors()) {
        if (static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get())) != output)
            continue;
        for (const auto& frame : shellProjection()->renderList(monitor, SHELL_PROJECTION_OVERLAY))
            if (frame->surfaceNamespace == "luminophore-shell-spatial-editor" && frame->state == SHELL_SNAPSHOT_PRESENTED && frame->visible && frame->mapped &&
                press.x >= frame->renderBox.x && press.y >= frame->renderBox.y && press.x < frame->renderBox.x + frame->renderBox.w &&
                press.y < frame->renderBox.y + frame->renderBox.h)
                onEditor = true;
    }
    if (!onEditor)
        return report("press-outside-presented-editor");
    for (const auto& window : Desktop::windowState()->windows()) {
        if (static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(window.get())) != key)
            continue;
        if (!spatialRuntime()->isBoardRoot(window))
            return report("window-not-board-root");
        g_layoutManager->dragController()->dragBegin(window->layoutTarget(), MBIND_MOVE, std::nullopt, true, true);
        if (active()) {
            m_editorPressTime = pressTime;
            return report("accepted", true);
        }
        return report("native-drag-did-not-start");
    }
    return report("window-not-found");
}

bool CLuminophoreSpatialGrabController::cancelFromEditor(uint32_t pressTime) {
    if (!active() || m_options.origin != eLuminophoreDragOrigin::EDITOR || m_editorPressTime != pressTime)
        return false;
    g_layoutManager->cancelDragTarget();
    return true;
}
std::optional<SLuminophoreSpatialCommand> CLuminophoreSpatialGrabController::floatingCommand(const SSpatialSnapshot& source, double x, double y) const {
    if (!m_grab.state() || !m_grab.state()->floating || !std::isfinite(x) || !std::isfinite(y))
        return std::nullopt;
    const auto state = *m_grab.state();
    if (source.revision != state.revision || source.topologyRevision != state.topologyRevision)
        return std::nullopt;
    std::optional<SLuminophoreBoardPoint> point;
    if (!m_direct) {
        const auto frame = editorFrame();
        // Reuse exactly the tiled grid hit test, allowing a floating host move.
        if (frame)
            if (auto command = m_grab.commandAt(grabFacts(source), *frame, x, y))
                point = std::get<SMoveWindowToCommand>(command->payload).point;
    }
    auto cells = spatialRuntime()->projectedCells();
    if (point) {
        cells.clear();
        if (const auto host = spatialRuntime()->projectedHostBox(*point, state.targetOutputID))
            cells.push_back({.point = *point, .outputID = state.targetOutputID, .box = *host});
    }
    for (const auto& cell : cells) {
        if (cell.outputID != state.targetOutputID ||
            (point ? cell.point != *point : !m_direct || x < cell.box.x || y < cell.box.y || x >= double(cell.box.x) + cell.box.width || y >= double(cell.box.y) + cell.box.height))
            continue;
        // A quick regrab may begin halfway through the badge expansion. Its
        // visual size is not a new client size; retain the committed geometry.
        const auto presentation = spatialRuntime()->presentationFor(state.window);
        const auto geometry     = spatialRuntime()->floatingGeometry(state.window);
        // Hidden commits intentionally have no clientBox. The persistent client
        // dimensions survive leaving the view and must drive editor re-entry.
        const Vector2D size = geometry && geometry->logicalWidth > 0 && geometry->logicalHeight > 0 ? Vector2D{geometry->logicalWidth, geometry->logicalHeight} :
            presentation && presentation->visible                                                   ? Vector2D{presentation->clientBox.width, presentation->clientBox.height} :
                                                                                                      Vector2D{};
        if (size.x <= 0 || size.y <= 0)
            return std::nullopt;
        const CBox box   = m_direct ? CBox{m_sourceBox.pos() + Vector2D{x, y} - m_directStart, size} :
                                      CBox{Vector2D{cell.box.x + cell.box.width / 2.0, cell.box.y + cell.box.height / 2.0} - size / 2.0, size};
        const auto local = CLuminophoreSpatialProjection::normalize({int(box.x), int(box.y), int(box.w), int(box.h)}, cell.box);
        if (!local)
            return std::nullopt;
        return SLuminophoreSpatialCommand{.expectedRevision = state.revision,
                                   .payload          = SUpdateFloatingCommand{.key = state.window, .host = cell.point, .localBox = *local, .outputID = cell.outputID}};
    }
    // A board host outside the visible view has no physical rectangle yet.
    // Retain its normalized floating geometry, as ordinary host relocation does.
    if (point)
        return SLuminophoreSpatialCommand{
            .expectedRevision = state.revision,
            .payload          = SMoveWindowToCommand{.key = state.window, .point = *point, .outputID = state.targetOutputID, .expectedTopologyRevision = state.topologyRevision}};
    return std::nullopt;
}
