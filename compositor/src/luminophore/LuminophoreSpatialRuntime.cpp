#include "LuminophoreSpatialRuntime.hpp"
#include "LuminophoreLaunchOrigin.hpp"
#include "../protocols/core/Compositor.hpp"
#include "../protocols/XDGActivation.hpp"
#include "LuminophoreSpatialEdit.hpp"
#include "LuminophoreSpatialNativeAdapter.hpp"
#include "LuminophoreSpatialTopology.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include "LuminophoreSpatialMotion.hpp"
#include "LuminophoreSpatialGrabController.hpp"
#include "LuminophoreSpatialPreviewMetrics.hpp"

#include "../desktop/state/FocusState.hpp"
#include "../desktop/state/WindowState.hpp"
#include "../desktop/view/Window.hpp"
#include "../desktop/rule/windowRule/WindowRuleApplicator.hpp"
#include "../layout/target/Target.hpp"
#include "../layout/LayoutManager.hpp"
#include "../layout/supplementary/DragController.hpp"
#include "../output/Monitor.hpp"
#include "../pointer/PointerManager.hpp"
#include <cmath>
#include <climits>
#include "../config/ConfigValue.hpp"

#include <algorithm>
#include <tuple>

using namespace Luminophore;

static LuminophoreWindowKey keyFor(const PHLWINDOW& window) {
    // IPC consumers join this key against hyprctl's public window address.
    // Keep that contract until the snapshot exposes stableId separately.
    return window ? static_cast<LuminophoreWindowKey>(reinterpret_cast<uintptr_t>(window.get())) : 0;
}

UP<CLuminophoreSpatialRuntime>& Luminophore::spatialRuntime() {
    static auto runtime = makeUnique<CLuminophoreSpatialRuntime>();
    return runtime;
}

void CLuminophoreSpatialRuntime::bootstrap() {
    if (m_bootstrapped)
        return;

    topologyChanged();
    if (!m_model)
        return;

    m_bootstrapped = true;
    for (const auto& window : Desktop::windowState()->windows()) {
        if (!window || !window->layoutTarget())
            continue;
        observeTarget(window->layoutTarget());
    }
    m_history = CLuminophoreSpatialHistory{};
}

void CLuminophoreSpatialRuntime::observeTarget(const SP<Layout::ITarget>& target, std::optional<CBox> desiredFloatingBox) {
    if (!target)
        return;
    if (!m_model)
        topologyChanged();
    if (!m_model)
        return;

    const auto window = target->window();
    const auto key    = keyFor(window);
    if (key == 0)
        return;
    const auto lifetime = m_lifetimes.find(key);
    if (lifetime == m_lifetimes.end() || lifetime->second.first.lock() != window) {
        m_lifetimes[key] = {window, ++m_nextLifetime};
        m_directLaunches.erase(key);
        m_launchGroups.erase(key);
    }
    if (window->m_isMapped && !m_directLaunches.contains(key)) {
        const auto env        = window->getEnv();
        const auto token      = env.find(LaunchOrigin::ENV);
        const auto origin     = token != env.end() ? LaunchOrigin::take(token->second) : std::nullopt;
        m_directLaunches[key] = origin.has_value();
        if (origin && !origin->empty())
            m_launchGroups[key] = *origin;
    }

    const auto causalSource =
        window->m_isMapped && PROTO::activation && window->wlSurface() ? PROTO::activation->takePlacementSource(window->wlSurface()->resource(), window->m_class) : nullptr;
    if (participationFor(target) != eSpatialParticipation::BOARD_ROOT) {
        m_windows.clearMotion(key);
        SpatialNative::releaseSpatialInput(window);
        submit(SObserveWindowCommand{.key = key, .mode = eLuminophoreWindowPlacementMode::ABSENT, .closed = !window->m_isMapped});
        m_exits.erase(key);
        m_windows.forget(key);
        if (!window->m_isMapped) {
            m_lifetimes.erase(key);
            m_directLaunches.erase(key);
            m_launchGroups.erase(key);
        }
        return;
    }

    if (m_restoringHistory) {
        m_windows.remember(key, target);
        return;
    }
    focusChanged();
    m_windows.remember(key, target);
    if (!target->floating()) {
        m_windows.clearMotion(key);
    } else if (m_windows.hasMotion(key))
        return;
    const bool                               overlay   = target->floating();
    const auto                               preferred = overlay ? preferredPoint(window, true) : std::nullopt;
    std::optional<SLuminophoreNormalizedBox> localBox;
    if (overlay && preferred) {
        const auto host =
            projectedHostBox(*preferred, m_model->outputFor(key).value_or(static_cast<uint64_t>(reinterpret_cast<uintptr_t>(Desktop::focusState()->monitor().get()))));
        const auto box = desiredFloatingBox.value_or(target->position());
        if (host)
            localBox = CLuminophoreSpatialProjection::normalize(
                {.x = static_cast<int>(box.x), .y = static_cast<int>(box.y), .width = static_cast<int>(box.w), .height = static_cast<int>(box.h)}, *host);
    }
    const bool directLaunch = m_directLaunches.contains(key) && m_directLaunches.at(key);
    const auto direction    = overlay || !directLaunch || causalSource ? std::string{} : window->m_ruleApplicator->static_.initialPlacement;
    submit(SObserveWindowCommand{
        .key              = key,
        .mode             = overlay ? eLuminophoreWindowPlacementMode::FLOATING : eLuminophoreWindowPlacementMode::TILED,
        .preferred        = preferred,
        .localBox         = localBox,
        .outputID         = direction.empty() || direction == "default" ? static_cast<uint64_t>(reinterpret_cast<uintptr_t>(Desktop::focusState()->monitor().get())) :
                                                                          selectedOutputID().value_or(0),
        .causalSource     = !overlay && causalSource && causalSource != window ? keyFor(causalSource) : 0,
        .initialPlacement = direction,
        .directLaunch     = directLaunch,
    });
}

void CLuminophoreSpatialRuntime::beginFloatingMotion(const SP<Layout::ITarget>& target) {
    if (!target || !target->floating() || !managesFloatingTarget(target))
        return;
    const auto box = target->position();
    m_windows.beginMotion(keyFor(target->window()),
                          {.x = static_cast<int>(box.x), .y = static_cast<int>(box.y), .width = static_cast<int>(box.w), .height = static_cast<int>(box.h)});
}

bool CLuminophoreSpatialRuntime::updateFloatingMotion(const SP<Layout::ITarget>& target, const CBox& box) {
    if (!target || !target->floating())
        return false;
    return m_windows.updateMotion(keyFor(target->window()),
                                  {.x = static_cast<int>(box.x), .y = static_cast<int>(box.y), .width = static_cast<int>(box.w), .height = static_cast<int>(box.h)});
}

void CLuminophoreSpatialRuntime::finishFloatingMotion(const SP<Layout::ITarget>& target) {
    const auto key = keyFor(target ? target->window() : nullptr);
    m_windows.endMotion(key);
    const auto saved = m_windows.takeMotion(key);
    if (!saved)
        return;
    const auto box = *saved;
    if (!target || !target->floating() || participationFor(target) != eSpatialParticipation::BOARD_ROOT)
        return;
    if (setFloatingGeometry(target, CBox{box.x, box.y, box.width, box.height}))
        return;
    // Preserve the actual drag result even when its host cannot be committed.
    // Later topology/geometry commits must not silently restore the old box.
    m_windows.restoreMotion(key, box);
    SpatialEvents::floatingBlocked(key);
}

bool CLuminophoreSpatialRuntime::setFloatingGeometry(const SP<Layout::ITarget>& target, const CBox& box) {
    if (!target || !target->floating() || participationFor(target) != eSpatialParticipation::BOARD_ROOT || !m_model)
        return false;
    const auto key         = keyFor(target->window());
    const auto currentHost = m_model->floatingHostOf(key);
    if (!currentHost)
        return false;
    auto       host              = *currentHost;
    const auto center            = box.middle();
    bool       matched           = false;
    uint64_t   destinationOutput = 0;
    for (const auto& cell : projectedCells()) {
        if (center.x < cell.box.x || center.y < cell.box.y || center.x >= cell.box.x + cell.box.width || center.y >= cell.box.y + cell.box.height)
            continue;
        host              = cell.point;
        destinationOutput = cell.outputID;
        matched           = true;
        break;
    }
    if (!matched)
        return false;
    const auto hostBox = projectedHostBox(host, destinationOutput);
    if (!hostBox)
        return false;
    const auto local = CLuminophoreSpatialProjection::normalize(
        {.x = static_cast<int>(box.x), .y = static_cast<int>(box.y), .width = static_cast<int>(box.w), .height = static_cast<int>(box.h)}, *hostBox);
    if (!local)
        return false;
    const auto result = submit(SUpdateFloatingCommand{.key = key, .host = host, .localBox = *local, .outputID = destinationOutput});
    return result.status == eLuminophoreSpatialTransactionStatus::APPLIED || result.status == eLuminophoreSpatialTransactionStatus::NO_CHANGE;
}

bool CLuminophoreSpatialRuntime::managesFloatingTarget(const SP<Layout::ITarget>& target) const {
    if (!target || !m_model)
        return false;
    return m_model->floatingHostOf(keyFor(target->window())).has_value();
}

bool CLuminophoreSpatialRuntime::managesTiledTarget(const SP<Layout::ITarget>& target) const {
    if (!target || !m_model)
        return false;
    return m_model->coordinateOf(keyFor(target->window())).has_value();
}

void CLuminophoreSpatialRuntime::topologyChanged() {
    const auto outputs = SpatialTopology::physicalOutputs();

    if (outputs != m_outputs) {
        m_outputs = outputs;
        ++m_topologyRevision;
    }

    if (!m_model) {
        m_model = makeUnique<CLuminophoreSpatialModel>();
    }
    m_active            = true;
    static auto columns = CConfigValue<Config::INTEGER>("misc:luminophore_default_view_columns");
    static auto rows    = CConfigValue<Config::INTEGER>("misc:luminophore_default_view_rows");
    const auto  current = m_model->snapshot();
    if (m_model->outputTopologyRevision() == m_topologyRevision && m_model->outputViews().size() == m_outputs.size() && current.independent &&
        current.independent->defaultColumns == *columns && current.independent->defaultRows == *rows) {
        commitCurrent();
        return;
    }
    std::vector<SLuminophoreOutputGeometry> geometry;
    for (const auto& o : m_outputs)
        geometry.push_back({o.id, o.name, o.box.x, o.box.y, o.box.width, o.box.height});
    submit(SSpatialTopologyCommand{.outputs = std::move(geometry), .revision = m_topologyRevision, .columns = static_cast<int>(*columns), .rows = static_cast<int>(*rows)});
}

bool CLuminophoreSpatialRuntime::commitCurrent() {
    if (m_restoringHistory)
        return true;
    if (!m_model)
        return false;

    return commitSnapshot(m_model->snapshot());
}

bool CLuminophoreSpatialRuntime::commitSnapshot(const SLuminophoreSpatialSnapshot& snapshot) {
    if (snapshot.outputTopologyRevision != m_topologyRevision)
        return false;

    const auto plan = CLuminophoreSpatialProjection::plan(snapshot, m_topologyRevision, m_outputs);
    if (!plan)
        return false;
    auto commit = CLuminophoreSpatialCommitter::prepare(*plan);
    if (!commit)
        return false;

    SpatialNative::applyOutputOverrides(*commit, snapshot.presentationMode, snapshot.wideKey, m_windows, m_restoringHistory);

    if (snapshot.presentationMode == eLuminophorePresentationMode::NORMAL) {
        for (auto& entry : commit->entries) {
            if (const auto motion = floatingMotionPresentation(entry.key); motion)
                entry = *motion;
        }
    }

    auto                                                    visual = *commit;
    std::map<LuminophoreWindowKey, SLuminophorePhysicalBox> starts;
    auto                                                    exits = m_exits;
    std::erase_if(exits, [&](const auto& p) { return !SpatialTopology::monitorForOutput(p.second.primaryOutputID) || !targetFor(p.first); });
    const auto previous = m_committer.presented();
    for (auto& entry : visual.entries) {
        if (entry.visible) {
            if (previous && previous->presentationMode == eLuminophorePresentationMode::NORMAL && snapshot.presentationMode == eLuminophorePresentationMode::NORMAL &&
                !exits.contains(entry.key)) {
                const auto old    = std::ranges::find(previous->entries, entry.key, &SLuminophoreSpatialCommitEntry::key);
                const auto point  = m_model->coordinateOf(entry.key);
                const auto view   = std::ranges::find(m_model->outputViews(), entry.primaryOutputID, &SLuminophoreOutputView::outputID);
                const auto output = std::ranges::find(m_outputs, entry.primaryOutputID, &SLuminophorePhysicalOutput::id);
                if (old != previous->entries.end() && !old->visible && point && view != m_model->outputViews().end() && output != m_outputs.end())
                    if (const auto start = CLuminophoreSpatialMotion::outside(entry, *point, view->rect, output->box))
                        starts[entry.key] = start->clientBox;
            }
            exits.erase(entry.key);
            continue;
        }
        if (snapshot.presentationMode != eLuminophorePresentationMode::NORMAL) {
            exits.erase(entry.key);
            continue;
        }
        const auto old =
            previous ? std::ranges::find(previous->entries, entry.key, &SLuminophoreSpatialCommitEntry::key) : std::vector<SLuminophoreSpatialCommitEntry>::const_iterator{};
        if (previous && old != previous->entries.end() && old->visible) {
            const auto point  = std::ranges::find(snapshot.tiled, entry.key, &SLuminophoreTiledPlacement::key);
            const auto view   = std::ranges::find(snapshot.outputViews, old->primaryOutputID, &SLuminophoreOutputView::outputID);
            const auto output = std::ranges::find(m_outputs, old->primaryOutputID, &SLuminophorePhysicalOutput::id);
            if (point != snapshot.tiled.end() && view != snapshot.outputViews.end() && output != m_outputs.end())
                if (const auto exit = CLuminophoreSpatialMotion::outside(*old, point->point, view->rect, output->box))
                    exits[entry.key] = *exit;
        }
        if (exits.contains(entry.key))
            entry = exits.at(entry.key);
    }
    auto batch = SpatialNative::CResolvedBatch::prepare(visual, m_windows);
    if (!batch)
        return false;
    const auto savedExits = m_exits;
    m_exits               = exits;
    const bool applied    = m_committer.applyBatch(
        *commit, [this](LuminophoreWindowKey key) { return !!targetFor(key); }, [](uint64_t outputID) { return !!SpatialTopology::monitorForOutput(outputID); },
        [&batch, &visual, &starts, revision = snapshot.revision](const auto&) { return batch->apply(visual.entries, revision, starts); });
    if (!applied)
        m_exits = savedExits;
    return applied;
}

bool CLuminophoreSpatialRuntime::dispatch(eSpatialAction action, eLuminophoreSpatialDirection direction, std::optional<PHLWINDOW> window) {
    bootstrap();
    if (!m_model)
        return false;

    const auto before      = m_model->snapshot();
    auto       eventOutput = selectedOutputID().value_or(0);
    const auto eventKey    = focusedKey(window);
    const auto emit        = [&](const SLuminophoreSpatialTransactionResult& result, const std::string& reason) {
        const bool applied = result.status == eLuminophoreSpatialTransactionStatus::APPLIED;
        const auto key     = result.nextFocusedKey ? result.nextFocusedKey : eventKey;
        if (applied && action == eSpatialAction::FOCUS_DIRECTION && key) {
            if (const auto presentation = presentationFor(*key))
                eventOutput = presentation->primaryOutputID;
        }
        const auto presentation = key ? presentationFor(*key) : std::nullopt;
        const bool visible      = presentation && presentation->visible;
        const auto monitor      = SpatialTopology::monitorForOutput(eventOutput);
        SpatialEvents::action(action, direction, before, result, eventKey, key, eventOutput, monitor ? monitor->m_name : "", visible, reason);
        return applied;
    };
    const auto blocked = [&]() { return emit({.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = m_model->snapshot()}, "invalid-target"); };
    SLuminophoreSpatialTransactionResult result;
    switch (action) {
        case eSpatialAction::MOVE_VIEW: {
            const auto outputID = selectedOutputID(window);
            if (!outputID)
                return blocked();
            const std::optional<LuminophoreWindowKey> key = std::nullopt;
            result                                        = submit(SMoveViewCommand{
                .direction                = direction,
                .focusedKey               = key,
                .outputID                 = *outputID,
                .expectedTopologyRevision = m_topologyRevision,
            });
            break;
        }
        case eSpatialAction::ADJUST_VIEW: {
            const auto outputID = selectedOutputID(window);
            if (!outputID)
                return blocked();
            const std::optional<LuminophoreWindowKey> key    = std::nullopt;
            const auto                                anchor = anchorForOutput(*outputID, key);
            if (!anchor || !outputID)
                return blocked();
            result = submit(SAdjustViewCommand{
                .direction                = direction,
                .anchor                   = *anchor,
                .anchorKey                = key,
                .outputID                 = *outputID,
                .expectedTopologyRevision = m_topologyRevision,
            });
            break;
        }
        case eSpatialAction::MOVE_WINDOW: {
            const auto selected = window.value_or(Desktop::focusState()->window());
            const auto key      = selected && m_model && m_model->boardFor(keyFor(selected)) ? std::optional{keyFor(selected)} : std::nullopt;
            if (!key)
                return blocked();
            result = submit(SMoveTiledCommand{.key = *key, .direction = direction});
            break;
        }
        case eSpatialAction::FOCUS_DIRECTION: {
            const auto key = focusedKey(window);
            if (!key)
                return blocked();
            const auto outputID = m_model->outputFor(*key);
            if (!outputID)
                return blocked();
            eventOutput = *outputID;
            result      = submit(SFocusDirectionCommand{
                .key                      = *key,
                .direction                = direction,
                .outputID                 = *outputID,
                .expectedTopologyRevision = m_topologyRevision,
            });
            break;
        }
        case eSpatialAction::TOGGLE_DESKTOP: {
            result = submit(SToggleDesktopCommand{});
            break;
        }
        case eSpatialAction::TOGGLE_WIDE: {
            const auto key = focusedKey(window);
            if (!key)
                return blocked();
            result = submit(SToggleWideCommand{.key = *key});
            break;
        }
    }
    const auto reason = result.status == eLuminophoreSpatialTransactionStatus::APPLIED ? "" :
        result.status == eLuminophoreSpatialTransactionStatus::STALE_REVISION          ? "stale-revision" :
        result.status == eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY          ? "stale-topology" :
        result.status == eLuminophoreSpatialTransactionStatus::COMMIT_FAILED           ? "commit-failed" :
                                                                                         "no-change";
    return emit(result, reason);
}

SLuminophoreSpatialTransactionResult CLuminophoreSpatialRuntime::edit(const SLuminophoreSpatialCommand& command, bool previewOnly) {
    const auto current = m_model ? m_model->snapshot() : SLuminophoreSpatialSnapshot{};
    if (!m_model || m_restoringHistory || m_commandQueue.draining() || m_commandQueue.pending() != 0 || CLuminophoreSpatialCommitter::isApplying())
        return {.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = current};

    const auto windowMove   = std::get_if<SMoveWindowToCommand>(&command.payload);
    const auto floatingMove = std::get_if<SUpdateFloatingCommand>(&command.payload);
    const auto movedKey     = windowMove ? std::optional{windowMove->key} : floatingMove ? std::optional{floatingMove->key} : std::nullopt;
    if (movedKey && m_windows.activeMotion(*movedKey))
        return {.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = current};

    auto candidate = [&] {
        const PreviewMetrics::CTimer timer(PreviewMetrics::eStage::COPY);
        return makeUnique<CLuminophoreSpatialModel>(*m_model);
    }();
    auto result = [&] {
        const PreviewMetrics::CTimer timer(PreviewMetrics::eStage::TRANSACT);
        return Luminophore::applyEditorCandidate(*candidate, command);
    }();
    if (result.status != eLuminophoreSpatialTransactionStatus::APPLIED)
        return result;
    if (previewOnly) {
        const auto plan = [&] {
            const PreviewMetrics::CTimer timer(PreviewMetrics::eStage::PROJECTION);
            return CLuminophoreSpatialProjection::plan(result.snapshot, m_topologyRevision, m_outputs);
        }();
        if (!plan)
            return {.status = eLuminophoreSpatialTransactionStatus::COMMIT_FAILED, .snapshot = current};
        const auto prepared = [&] {
            const PreviewMetrics::CTimer timer(PreviewMetrics::eStage::PREPARE);
            return CLuminophoreSpatialCommitter::prepare(*plan);
        }();
        if (!prepared)
            return {.status = eLuminophoreSpatialTransactionStatus::COMMIT_FAILED, .snapshot = current};
        return result;
    }

    // A deliberate editor move supersedes an idle failed-drag presentation,
    // but a rejected commit must retain that physical recovery box.
    auto historyBefore = historyFrame();
    auto savedMotion   = movedKey ? m_windows.takeMotion(*movedKey) : std::nullopt;
    if (!commitSnapshot(result.snapshot)) {
        if (savedMotion)
            m_windows.restoreMotion(*movedKey, *savedMotion);
        return {.status = eLuminophoreSpatialTransactionStatus::COMMIT_FAILED, .snapshot = current};
    }
    m_model = std::move(candidate);
    recordHistory(std::move(historyBefore));
    notifyStateChanged();
    return result;
}

bool CLuminophoreSpatialRuntime::desktopExposed() const {
    const auto effective = m_committer.effective();
    return effective && effective->presentationMode == eLuminophorePresentationMode::DESKTOP;
}

bool CLuminophoreSpatialRuntime::active() const {
    return m_active;
}

uint64_t CLuminophoreSpatialRuntime::revision() const {
    return m_model ? m_model->revision() : 0;
}

std::optional<SLuminophoreNormalizedBox> CLuminophoreSpatialRuntime::floatingGeometry(LuminophoreWindowKey key) const {
    if (!m_model)
        return std::nullopt;
    const auto snapshot  = m_model->snapshot();
    const auto placement = std::ranges::find(snapshot.floating, key, &SLuminophoreFloatingPlacement::key);
    return placement == snapshot.floating.end() ? std::nullopt : std::optional{placement->localBox};
}

SSpatialSnapshot CLuminophoreSpatialRuntime::snapshot() const {
    const PreviewMetrics::CTimer timer(PreviewMetrics::eStage::SNAPSHOT);
    if (!m_model)
        return {};

    const auto       modelSnapshot = m_model->snapshot();
    const auto&      presented     = m_committer.presented();
    SSpatialSnapshot result{
        .active                    = m_active,
        .committed                 = presented && presented->modelRevision == modelSnapshot.revision && presented->topologyRevision == m_topologyRevision,
        .revision                  = modelSnapshot.revision,
        .topologyRevision          = m_topologyRevision,
        .committedModelRevision    = presented ? presented->modelRevision : 0,
        .committedTopologyRevision = presented ? presented->topologyRevision : 0,
        .extent                    = modelSnapshot.extent,
        .view                      = modelSnapshot.view,
        .presentationMode          = modelSnapshot.presentationMode,
        .outputViews               = modelSnapshot.outputViews,
        .wideKey                   = modelSnapshot.wideKey,
        .focusedKey                = [&]() -> std::optional<LuminophoreWindowKey> {
            const auto window = Desktop::focusState()->window();
            const auto key    = keyFor(window);
            return m_model->coordinateOf(key) ? std::optional<LuminophoreWindowKey>{key} : std::nullopt;
        }(),
        .selectedOutputID = selectedOutputID().value_or(0),
        .outputs          = m_outputs,
        .independent      = modelSnapshot.independent,
    };
    std::ranges::sort(result.outputs, [](const auto& lhs, const auto& rhs) { return std::tie(lhs.box.x, lhs.box.y, lhs.id) < std::tie(rhs.box.x, rhs.box.y, rhs.id); });

    const auto committedEntry = [&presented](LuminophoreWindowKey key) -> std::optional<SLuminophoreSpatialCommitEntry> {
        if (!presented)
            return std::nullopt;
        const auto it = std::ranges::find(presented->entries, key, &SLuminophoreSpatialCommitEntry::key);
        return it == presented->entries.end() ? std::nullopt : std::optional{*it};
    };

    const auto sourceFor = [this](LuminophoreWindowKey key) {
        const auto target  = m_windows.targetFor(key);
        const auto window  = target ? target->window() : nullptr;
        const auto surface = window && window->wlSurface() ? window->wlSurface()->resource() : nullptr;
        return surface && surface->m_luminophoreSource ? surface->m_luminophoreSource->snapshot() : SSurfaceSourceSnapshot{};
    };
    result.windows.reserve(modelSnapshot.tiled.size() + modelSnapshot.floating.size());
    for (const auto& placement : modelSnapshot.tiled) {
        const auto entry = committedEntry(placement.key);
        result.windows.emplace_back(SSpatialWindowSnapshot{
            .key                   = placement.key,
            .coordinate            = placement.point,
            .committedBox          = entry ? entry->clientBox : SLuminophorePhysicalBox{},
            .primaryOutputID       = entry ? entry->primaryOutputID : 0,
            .fragments             = entry ? entry->fragments : std::vector<SLuminophoreProjectedFragment>{},
            .visible               = entry && entry->visible,
            .floating              = false,
            .boardID               = m_model->boardFor(placement.key).value_or(0),
            .source                = sourceFor(placement.key),
            .presentationAvailable = entry && entry->presentationBox().has_value(),
        });
    }
    for (const auto& placement : modelSnapshot.floating) {
        const auto entry = committedEntry(placement.key);
        result.windows.emplace_back(SSpatialWindowSnapshot{
            .key                   = placement.key,
            .coordinate            = placement.host,
            .committedBox          = entry ? entry->clientBox : SLuminophorePhysicalBox{},
            .primaryOutputID       = entry ? entry->primaryOutputID : 0,
            .fragments             = entry ? entry->fragments : std::vector<SLuminophoreProjectedFragment>{},
            .visible               = entry && entry->visible,
            .floating              = true,
            .boardID               = m_model->boardFor(placement.key).value_or(0),
            .source                = sourceFor(placement.key),
            .presentationAvailable = entry && entry->presentationBox().has_value(),
        });
    }
    std::ranges::sort(result.windows, {}, &SSpatialWindowSnapshot::key);
    return result;
}

std::optional<SLuminophoreSpatialCommitEntry> CLuminophoreSpatialRuntime::presentationFor(LuminophoreWindowKey key) const {
    const auto effective = m_committer.effective();
    if (!effective)
        return std::nullopt;
    const auto it = std::ranges::find(effective->entries, key, &SLuminophoreSpatialCommitEntry::key);
    if (it == effective->entries.end())
        return std::nullopt;
    if (effective->presentationMode == eLuminophorePresentationMode::NORMAL) {
        if (const auto motion = floatingMotionPresentation(key); motion)
            return motion;
    }
    if (!it->visible && m_exits.contains(key))
        return m_exits.at(key);
    return *it;
}

std::optional<SLuminophoreSpatialCommitEntry> CLuminophoreSpatialRuntime::presentationFor(const PHLWINDOW& window) const {
    return presentationFor(keyFor(window));
}

bool CLuminophoreSpatialRuntime::isBoardRoot(const PHLWINDOW& window) const {
    return presentationFor(window).has_value();
}

SLuminophoreSpatialTransactionResult CLuminophoreSpatialRuntime::submit(LuminophoreSpatialPayload payload) {
    if (m_restoringHistory)
        return {.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = m_model ? m_model->snapshot() : SLuminophoreSpatialSnapshot{}};
    std::string launchGroup;
    if (const auto observation = std::get_if<SObserveWindowCommand>(&payload);
        observation && observation->mode != eLuminophoreWindowPlacementMode::ABSENT && m_model && !m_model->boardFor(observation->key)) {
        if (const auto found = m_launchGroups.find(observation->key); found != m_launchGroups.end()) {
            launchGroup = found->second;
            m_launchGroups.erase(found);
        }
    }
    const auto focusCommand = std::get_if<SSpatialFocusCommand>(&payload);
    const bool focusOnly    = focusCommand && !focusCommand->reveal;
    const bool resizing     = std::holds_alternative<SSpatialResizeCommand>(payload) || (m_resizeStart && std::holds_alternative<SUpdateFloatingCommand>(payload));
    if (m_resizeStart && !resizing && !focusOnly)
        resizeGesture(false, true);
    auto historyBefore = !focusOnly && !resizing ? historyFrame() : SLuminophoreHistoryFrame{};
    m_commandQueue.enqueue(std::move(payload));
    if (m_commandQueue.draining() || !m_model)
        return {.status = eLuminophoreSpatialTransactionStatus::NO_CHANGE, .snapshot = m_model ? m_model->snapshot() : SLuminophoreSpatialSnapshot{}};

    const bool wasDesktop    = desktopExposed();
    const auto previousFocus = Desktop::focusState()->window();
    auto       candidate     = makeUnique<CLuminophoreSpatialModel>(*m_model);
    const auto results       = m_commandQueue.drain(*candidate);
    const bool changed       = std::ranges::any_of(results, [](const auto& result) { return result.status == eLuminophoreSpatialTransactionStatus::APPLIED; });
    if (changed && !commitSnapshot(candidate->snapshot()))
        return {.status = eLuminophoreSpatialTransactionStatus::COMMIT_FAILED, .snapshot = m_model->snapshot()};

    if (changed) {
        m_model = std::move(candidate);
        if (!focusOnly && !resizing)
            recordHistory(std::move(historyBefore), results.size() == 1 ? launchGroup : "");
        const auto focusResult = std::ranges::find_if(results.rbegin(), results.rend(), &SLuminophoreSpatialTransactionResult::updatesFocus);
        if (!wasDesktop && desktopExposed()) {
            m_desktopFocus = previousFocus;
            SpatialNative::clearClientPointerFocus();
            m_committedFocusKey.reset();
            SpatialNative::focus(nullptr);
        } else if (focusResult != results.rend())
            applyFocusUpdate(*focusResult);
        else if (wasDesktop && !desktopExposed()) {
            const auto restore = m_desktopFocus.lock();
            m_desktopFocus.reset();
            if (restore && restore->m_isMapped && !restore->isHidden() && !restore->isMinimized())
                SpatialNative::focus(restore);
        } else if (const auto focused = Desktop::focusState()->window(); focused) {
            const auto key          = keyFor(focused);
            const auto presentation = presentationFor(key);
            if (presentation && presentation->visible)
                m_committedFocusKey = key;
        }
        if (wasDesktop != desktopExposed())
            SpatialNative::damageOutputs();
        notifyStateChanged();
    }
    return results.empty() ? SLuminophoreSpatialTransactionResult{.status = eLuminophoreSpatialTransactionStatus::NO_CHANGE, .snapshot = m_model->snapshot()} : results.back();
}

void CLuminophoreSpatialRuntime::applyFocusUpdate(const SLuminophoreSpatialTransactionResult& result) {
    if (!result.updatesFocus)
        return;

    if (!result.nextFocusedKey) {
        m_anchorKey.reset();
        m_committedFocusKey.reset();
        SpatialNative::focus(nullptr);
        return;
    }

    const auto target = targetFor(*result.nextFocusedKey);
    const auto window = target ? target->window() : nullptr;
    if (!window)
        return;

    m_anchorKey         = result.nextFocusedKey;
    m_committedFocusKey = result.nextFocusedKey;
    SpatialNative::focus(window);
}

void CLuminophoreSpatialRuntime::notifyStateChanged() {
    if (!m_model || m_notifiedRevision == m_model->revision())
        return;
    if (SpatialEvents::stateChanged(m_model->revision()))
        m_notifiedRevision = m_model->revision();
}

std::optional<LuminophoreWindowKey> CLuminophoreSpatialRuntime::focusedKey(std::optional<PHLWINDOW> window) {
    const auto selected = window.value_or(Desktop::focusState()->window());
    if (!selected || selected->m_isFloating || !m_model)
        return std::nullopt;
    const auto key = keyFor(selected);
    return m_model->coordinateOf(key) ? std::optional<LuminophoreWindowKey>{key} : std::nullopt;
}

std::optional<LuminophoreWindowKey> CLuminophoreSpatialRuntime::anchorKeyForOutput(uint64_t outputID, std::optional<PHLWINDOW> window) {
    if (!m_model)
        return std::nullopt;

    const auto selected = window.value_or(Desktop::focusState()->window());
    if (selected && !selected->m_isFloating) {
        const auto key        = keyFor(selected);
        const auto coordinate = m_model->coordinateOf(key);
        const auto outputView = std::ranges::find(m_model->outputViews(), outputID, &SLuminophoreOutputView::outputID);
        if (coordinate && m_model->outputFor(key) == outputID && outputView != m_model->outputViews().end() && outputView->rect.contains(*coordinate)) {
            m_anchorKey = key;
            return key;
        }
    }

    const auto outputView = std::ranges::find(m_model->outputViews(), outputID, &SLuminophoreOutputView::outputID);
    return outputView == m_model->outputViews().end() ? std::nullopt : outputView->anchorKey;
}

std::optional<SLuminophoreBoardPoint> CLuminophoreSpatialRuntime::anchorForOutput(uint64_t outputID, std::optional<LuminophoreWindowKey> key) const {
    if (!m_model)
        return std::nullopt;
    if (key) {
        const auto coordinate = m_model->coordinateOf(*key);
        if (coordinate)
            return coordinate;
    }
    const auto outputView = std::ranges::find(m_model->outputViews(), outputID, &SLuminophoreOutputView::outputID);
    return outputView == m_model->outputViews().end() ? std::nullopt : std::optional<SLuminophoreBoardPoint>{outputView->rect.origin};
}

std::vector<SLuminophoreProjectedCell> CLuminophoreSpatialRuntime::projectedCells() const {
    if (!m_model)
        return {};
    const auto plan = CLuminophoreSpatialProjection::plan(m_model->snapshot(), m_topologyRevision, m_outputs);
    return plan ? plan->cells : std::vector<SLuminophoreProjectedCell>{};
}

std::optional<SLuminophoreBoardPoint> CLuminophoreSpatialRuntime::preferredPoint(const PHLWINDOW& window, bool allowOccupied) const {
    if (!m_model || !window)
        return std::nullopt;

    const auto key = keyFor(window);
    if (const auto tiled = m_model->coordinateOf(key); tiled)
        return tiled;
    if (const auto floating = m_model->floatingHostOf(key); floating)
        return floating;

    const auto cells        = projectedCells();
    const auto focusMonitor = Desktop::focusState()->monitor();
    const auto hostMonitor  = focusMonitor ? focusMonitor : window->m_monitor.lock();
    if (hostMonitor) {
        const auto outputID = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(hostMonitor.get()));
        for (const auto& cell : cells) {
            if (cell.outputID == outputID && (allowOccupied || !m_model->tiledAt(cell.point)))
                return cell.point;
        }
    }
    for (const auto& cell : cells) {
        if (allowOccupied || !m_model->tiledAt(cell.point))
            return cell.point;
    }
    return std::nullopt;
}

std::optional<SLuminophorePhysicalBox> CLuminophoreSpatialRuntime::projectedHostBox(const SLuminophoreBoardPoint& point, uint64_t outputID) const {
    if (!m_model)
        return std::nullopt;
    if (m_model->snapshot().independent) {
        for (const auto& output : m_outputs)
            if (!outputID || output.id == outputID)
                if (const auto box = CLuminophoreSpatialProjection::hostBox(m_model->snapshot(), point, output))
                    return box;
        return std::nullopt;
    }
    const auto                             cells = projectedCells();
    std::optional<SLuminophorePhysicalBox> result;
    for (const auto& cell : cells) {
        if (cell.point != point || (outputID && cell.outputID != outputID))
            continue;
        if (!result) {
            result = cell.box;
            continue;
        }
        const int right  = std::max(result->x + result->width, cell.box.x + cell.box.width);
        const int bottom = std::max(result->y + result->height, cell.box.y + cell.box.height);
        result->x        = std::min(result->x, cell.box.x);
        result->y        = std::min(result->y, cell.box.y);
        result->width    = right - result->x;
        result->height   = bottom - result->y;
    }
    return result;
}

std::optional<uint64_t> CLuminophoreSpatialRuntime::selectedOutputID(std::optional<PHLWINDOW>) const {
    return m_model ? SpatialTopology::selectedOutputID(m_model->outputViews()) : std::nullopt;
}

eSpatialParticipation CLuminophoreSpatialRuntime::classifyParticipation(const SSpatialParticipationFacts& facts) {
    return CLuminophoreSpatialWindowRegistry::classifyParticipation(facts);
}
eSpatialParticipation CLuminophoreSpatialRuntime::participationFor(const SP<Layout::ITarget>& target) const {
    return CLuminophoreSpatialWindowRegistry::participationFor(target);
}
SP<Layout::ITarget> CLuminophoreSpatialRuntime::targetFor(LuminophoreWindowKey key) const {
    return m_windows.targetFor(key);
}
std::optional<SLuminophoreSpatialCommitEntry> CLuminophoreSpatialRuntime::floatingMotionPresentation(LuminophoreWindowKey key) const {
    return m_windows.presentation(key, m_model ? m_model->floatingHostOf(key).value_or(SLuminophoreBoardPoint{}) : SLuminophoreBoardPoint{}, m_outputs);
}
uint64_t CLuminophoreSpatialRuntime::observationGeneration() const {
    return m_windows.generation();
}

void CLuminophoreSpatialRuntime::focusChanged() {
    if (!m_model || !m_active || m_resizeGesture.active() || CLuminophoreSpatialCommitter::isApplying())
        return;
    const auto           monitor = Desktop::focusState()->monitor();
    SSpatialFocusCommand command{focusedKey(std::nullopt), static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get()))};
    static auto          forceZero = CConfigValue<Config::INTEGER>("xwayland:force_zero_scaling");
    for (const auto& item : snapshot().windows) {
        const auto target = targetFor(item.key);
        const auto window = target ? target->window() : nullptr;
        if (!window || item.floating)
            continue;
        auto minimum = target->minSize().value_or(Vector2D{1, 1});
        if (window->m_isX11 && *forceZero && window->m_monitor)
            minimum /= window->m_monitor->m_scale;
        if (!std::isfinite(minimum.x) || !std::isfinite(minimum.y) || minimum.x > INT_MAX || minimum.y > INT_MAX)
            return;
        command.clientMinimums[item.key] = {std::max<int64_t>(1, std::ceil(minimum.x)), std::max<int64_t>(1, std::ceil(minimum.y))};
    }
    submit(std::move(command));
}
std::vector<CBox> CLuminophoreSpatialRuntime::regionsFor(const PHLWINDOW& window, const PHLMONITOR& monitor) const {
    std::vector<CBox> result;
    const auto        entry = presentationFor(window);
    if (!entry || !monitor || !entry->visible)
        return result;
    const auto  id     = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get()));
    const auto  visual = spatialGrabController()->visualBox(window).value_or(CBox{window->positionAnimation()->value(), window->sizeAnimation()->value()});
    const auto  pos = visual.pos(), size = visual.size();
    const auto& goal = entry->clientBox;
    for (const auto& f : entry->fragments) {
        if ((m_exits.contains(keyFor(window)) || isWideWindow(window)) && f.outputID != id)
            continue;
        const double sx = goal.width > 0 ? size.x / goal.width : 1, sy = goal.height > 0 ? size.y / goal.height : 1;
        CBox         box{pos.x + (f.box.x - goal.x) * sx, pos.y + (f.box.y - goal.y) * sy, f.box.width * sx, f.box.height * sy};
        box = box.intersection(monitor->logicalBox());
        if (!box.empty())
            result.push_back(box);
    }
    return result;
}

bool CLuminophoreSpatialRuntime::resizeTiled(const SP<Layout::ITarget>& target, const Vector2D& delta, bool left, bool top) {
    if (!target || !target->window() || !m_model || !m_model->independentBoards() || !std::isfinite(delta.x) || !std::isfinite(delta.y))
        return false;
    const auto key   = keyFor(target->window());
    const auto board = m_model->boardFor(key);
    if (!board)
        return false;
    const auto  snapshot = m_model->snapshot();
    const auto& s        = *snapshot.independent;
    if (snapshot.presentationMode != eLuminophorePresentationMode::NORMAL || !s.meshes.contains(*board))
        return false;
    const auto& mesh = s.meshes.at(*board);
    const auto  fill = Spatial::computeOwnership(s.state.boards.at(*board), mesh.fillFocus);
    const auto  out  = m_model->outputFor(key);
    if (!out || !s.outputs.contains(*out))
        return false;
    const auto& output  = s.outputs.at(*out);
    const auto  pointer = Pointer::mgr()->position() - Vector2D(output.x, output.y);
    if (m_resizeGesture.terminated())
        return false;
    std::map<LuminophoreWindowKey, Spatial::SPoint> minimums;
    static auto                                     forceZero = CConfigValue<Config::INTEGER>("xwayland:force_zero_scaling");
    for (const auto& [point, k] : s.state.boards.at(*board).tiled) {
        const auto t = targetFor(k);
        const auto w = t ? t->window() : nullptr;
        if (!w)
            continue;
        auto minimum = t->minSize().value_or(Vector2D(1, 1));
        if (w->m_isX11 && *forceZero && w->m_monitor)
            minimum /= w->m_monitor->m_scale;
        if (!std::isfinite(minimum.x) || !std::isfinite(minimum.y) || minimum.x > INT_MAX || minimum.y > INT_MAX)
            return false;
        minimums[k] = {std::max<int64_t>(1, std::ceil(minimum.x)), std::max<int64_t>(1, std::ceil(minimum.y))};
    }
    const auto select = [&](Spatial::eSide side, double d) { return m_resizeGesture.select(mesh, fill, key, pointer.x, pointer.y, side, d); };
    auto       x = select(left ? Spatial::eSide::LEFT : Spatial::eSide::RIGHT, delta.x), y = select(top ? Spatial::eSide::TOP : Spatial::eSide::BOTTOM, delta.y);
    if (m_resizeGesture.terminated() || (!x && !y))
        return false;
    const auto first  = x ? *x : *y;
    const auto result = submit(SSpatialResizeCommand{key, static_cast<int>(first.side), first.delta, first.face, first.minimum, x ? y : std::nullopt, minimums});
    return result.status == eLuminophoreSpatialTransactionStatus::APPLIED || result.status == eLuminophoreSpatialTransactionStatus::NO_CHANGE;
}

bool CLuminophoreSpatialRuntime::isWideWindow(const PHLWINDOW& window) const {
    if (!m_model)
        return false;
    return m_model->isWideKey(keyFor(window));
}

void CLuminophoreSpatialRuntime::advanceMotion() {
    for (auto it = m_exits.begin(); it != m_exits.end();) {
        const auto target = targetFor(it->first);
        const auto window = target ? target->window() : nullptr;
        if (window && window->m_isMapped && window->positionAnimation()->isBeingAnimated()) {
            ++it;
            continue;
        }
        if (window) {
            window->setSpatiallySuppressed(true);
            window->setInputBlocked(Desktop::View::INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW, true);
        }
        it = m_exits.erase(it);
    }
}

void CLuminophoreSpatialRuntime::resizeGesture(bool active, bool cancelled) {
    const bool wasActive = m_resizeGesture.active();
    if (active && !wasActive && m_model)
        m_resizeStart = historyFrame();
    m_resizeGesture.begin(active);
    if (wasActive && !active && m_resizeStart) {
        auto start = std::move(*m_resizeStart);
        m_resizeStart.reset();
        if (cancelled && m_model) {
            auto                           candidate = *m_model;
            std::set<LuminophoreWindowKey> retained;
            for (const auto& [key, id] : historyFrame().lifetimes)
                if (start.lifetimes.contains(key) && start.lifetimes.at(key) == id)
                    retained.insert(key);
            if (candidate.restoreSpatial(start.snapshot, retained)) {
                m_restoringHistory   = true;
                const bool committed = commitHistory(candidate.snapshot(), start);
                m_restoringHistory   = false;
                if (committed) {
                    *m_model = std::move(candidate);
                    notifyStateChanged();
                } else {
                    recordHistory(std::move(start));
                    SpatialEvents::historyCancelFailed(m_historyRecoveryFailed);
                }
            } else {
                recordHistory(std::move(start));
                SpatialEvents::historyCancelFailed(false);
            }
        } else
            recordHistory(std::move(start));
        focusChanged();
    }
}

bool CLuminophoreSpatialRuntime::requiresComposition(const PHLMONITOR& monitor) const {
    if (!monitor)
        return false;
    if (!m_exits.empty() || spatialGrabController()->active())
        return true;
    const auto effective = m_committer.effective();
    if (!effective)
        return false;
    const auto id = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get()));
    for (const auto& entry : effective->entries)
        if (entry.visible && entry.fragments.size() > 1 && std::ranges::any_of(entry.fragments, [&](const auto& f) { return f.outputID == id; }))
            return true;
    return false;
}

bool CLuminophoreSpatialRuntime::revealWindow(PHLWINDOW window) {
    if (!window || !window->m_isMapped || !m_model || m_restoringHistory || m_commandQueue.draining() || CLuminophoreSpatialCommitter::isApplying() ||
        g_layoutManager->dragController()->target())
        return false;
    const auto key = keyFor(window);
    if (!m_model->boardFor(key)) {
        if (window->isDesktopSuppressed() || window->isHidden() || window->isMinimized())
            return false;
        SpatialNative::focus(window);
        return true;
    }
    const auto result = submit(SSpatialFocusCommand{.key = key, .reveal = true});
    if (result.status != eLuminophoreSpatialTransactionStatus::APPLIED && result.status != eLuminophoreSpatialTransactionStatus::NO_CHANGE)
        return false;
    SpatialNative::focus(window);
    return true;
}
