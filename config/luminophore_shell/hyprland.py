from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Iterable

from .compositor_runtime import CompositorRuntimeError, compositor_instance, resolve_hyprctl
from uuid import uuid4

from .visual_settings import VisualSettings, VisualSettingsState, parse_visual_settings
from .settings_contract import SettingsCompletionUnknown
from .spatial_edit import SpatialEditPreview, SpatialEditRequest, SpatialGrabLayout, parse_edit_preview


class HyprlandError(RuntimeError):
    pass


def normalize_address(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text if text.startswith("0x") else f"0x{text}"


@dataclass(frozen=True)
class WorkspaceRef:
    id: int
    name: str
    role: str = ""

    def __post_init__(self) -> None:
        if self.role:
            return
        if self.name.startswith("luminophore-base-"):
            role = "base"
        elif self.name == "special:luminophore-spotify":
            role = "service"
        else:
            role = "unknown"
        object.__setattr__(self, "role", role)



@dataclass(frozen=True)
class MonitorRecord:
    id: int
    name: str
    x: int
    y: int
    width: int
    height: int
    active_workspace: WorkspaceRef
    special_workspace: WorkspaceRef = WorkspaceRef(0, "")
    focused: bool = False


@dataclass(frozen=True)
class WindowRecord:
    address: str
    app_class: str
    initial_class: str
    title: str
    initial_title: str
    pid: int
    monitor_id: int
    monitor_name: str
    workspace: WorkspaceRef
    floating: bool
    at: tuple[int, int]
    size: tuple[int, int]
    focus_history_id: int
    urgent: bool
    mapped: bool
    hidden: bool
    fullscreen: int = 0
    placement: str = "tiled"

    @property
    def app_key(self) -> str:
        return (self.initial_class or self.app_class or "unknown").casefold()

    @property
    def minimized(self) -> bool:
        return self.placement == "minimized"

    @property
    def immersive_fullscreen(self) -> bool:
        return self.fullscreen >= 2


@dataclass(frozen=True)
class SpatialView:
    x: int
    y: int
    columns: int
    rows: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.columns and self.y <= y < self.y + self.rows


@dataclass(frozen=True)
class SurfaceSource:
    instance: str
    token: int
    revision: int
    extent_revision: int
    alive: bool
    mapped: bool
    has_buffer: bool
    extent: tuple[float, float] | None


def parse_surface_source(raw: object, instance: str) -> SurfaceSource:
    if not isinstance(raw, dict):
        raise HyprlandError("invalid surface source")
    numbers = []
    for key in ("token", "revision", "extentRevision"):
        value = raw.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", value) or int(value) > 2**64-1:
            raise HyprlandError("invalid surface source identity")
        numbers.append(int(value))
    if any(type(raw.get(key)) is not bool for key in ("alive", "mapped", "hasBuffer")):
        raise HyprlandError("invalid surface source availability")
    extent = raw.get("extent")
    if extent is not None:
        if not isinstance(extent, dict) or any(type(extent.get(key)) not in (int, float) or not 0 < extent[key] <= 2**31-1 or not math.isfinite(extent[key]) for key in ("width", "height")):
            raise HyprlandError("invalid surface source extent")
        if not all((numbers[0], numbers[1], numbers[2], raw["alive"], raw["mapped"], raw["hasBuffer"])):
            raise HyprlandError("unavailable surface source has extent")
        extent = (float(extent["width"]), float(extent["height"]))
    if not raw["alive"] and (raw["mapped"] or raw["hasBuffer"]):
        raise HyprlandError("dead surface source is available")
    return SurfaceSource(instance, *numbers, raw["alive"], raw["mapped"], raw["hasBuffer"], extent)


@dataclass(frozen=True)
class SpatialWindow:
    address: str
    x: int
    y: int
    visible: bool
    mode: str = "tiled"
    primary_output: int = 0
    fragment_outputs: tuple[int, ...] = ()
    board_id: int = 0
    # Source commits are not spatial changes. Do not rebuild editor widgets or
    # cancel a drag just because video/content has committed another frame.
    source: SurfaceSource | None = field(default=None, compare=False)
    presentation_available: bool | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SpatialOutputView:
    output_id: int
    rect: SpatialView
    anchor_key: str = ""
    board_id: int = 0


@dataclass(frozen=True)
class SpatialState:
    active: bool
    revision: int
    columns: int
    rows: int
    view: SpatialView
    windows: tuple[SpatialWindow, ...]
    committed: bool = False
    topology_revision: int = 0
    committed_model_revision: int = 0
    committed_topology_revision: int = 0
    presentation_mode: str = "normal"
    output_views: tuple[SpatialOutputView, ...] = ()
    wide_key: str = ""
    focused_key: str = ""
    selected_output_id: int = 0
    diagnostics: tuple[str, ...] = ()
    display_origin: tuple[int, int] = (0, 0)
    output_names: tuple[tuple[int, str], ...] = ()
    protocol_version: int = 1
    editor_origin: tuple[int, int] | None = None

    def display_point(self, x: int, y: int) -> tuple[int, int]:
        return x - self.display_origin[0], y - self.display_origin[1]

    @property
    def target_output_id(self) -> int:
        if self.selected_output_id and any(view.output_id == self.selected_output_id for view in self.output_views):
            return self.selected_output_id
        if self.presentation_mode == "wide":
            return 0
        if self.focused_key:
            focused = next((window for window in self.windows if window.address == self.focused_key and window.visible), None)
            if focused and focused.primary_output:
                return focused.primary_output
        anchored = next((view for view in self.output_views if view.anchor_key and view.anchor_key == self.focused_key), None)
        return anchored.output_id if anchored else 0


def _pair(value: object) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) < 2:
        return (0, 0)
    return (int(value[0]), int(value[1]))


def _workspace(value: object) -> WorkspaceRef:
    raw = value if isinstance(value, dict) else {}
    return WorkspaceRef(
        int(raw.get("id", 0)),
        str(raw.get("name", "")),
        str(raw.get("role", "")),
    )


def parse_monitors(payload: object) -> list[MonitorRecord]:
    if not isinstance(payload, list):
        raise HyprlandError("monitor response is not a list")
    records: list[MonitorRecord] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        records.append(
            MonitorRecord(
                id=int(raw.get("id", -1)),
                name=str(raw.get("name", "")),
                x=int(raw.get("x", 0)),
                y=int(raw.get("y", 0)),
                width=int(raw.get("width", 0)),
                height=int(raw.get("height", 0)),
                active_workspace=_workspace(raw.get("activeWorkspace")),
                special_workspace=_workspace(raw.get("specialWorkspace")),
                focused=bool(raw.get("focused", False)),
            )
        )
    return records


def parse_windows(payload: object, monitors: Iterable[MonitorRecord]) -> list[WindowRecord]:
    if not isinstance(payload, list):
        raise HyprlandError("client response is not a list")
    monitor_names = {monitor.id: monitor.name for monitor in monitors}
    records: list[WindowRecord] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        address = normalize_address(raw.get("address"))
        if not address:
            continue
        monitor_id = int(raw.get("monitor", -1))
        records.append(
            WindowRecord(
                address=address,
                app_class=str(raw.get("class", "")),
                initial_class=str(raw.get("initialClass", "")),
                title=str(raw.get("title", "")),
                initial_title=str(raw.get("initialTitle", "")),
                pid=int(raw.get("pid", 0)),
                monitor_id=monitor_id,
                monitor_name=monitor_names.get(monitor_id, ""),
                workspace=_workspace(raw.get("workspace")),
                floating=bool(raw.get("floating", False)),
                at=_pair(raw.get("at")),
                size=_pair(raw.get("size")),
                focus_history_id=int(raw.get("focusHistoryID", -1)),
                urgent=bool(raw.get("urgent", False)),
                mapped=bool(raw.get("mapped", True)),
                hidden=bool(raw.get("hidden", False)),
                placement=str(raw.get("placement", "floating" if raw.get("floating", False) else "tiled")),
                fullscreen=int(raw.get("fullscreen", 0)),
            )
        )
    return records


def parse_spatial_state(payload: object) -> SpatialState:
    if not isinstance(payload, dict):
        raise HyprlandError("spatial state response is not an object")
    version = payload.get("protocolVersion", 1)
    if type(version) is not int or version not in {1, 2}:
        raise HyprlandError("unsupported spatial protocol version")
    extent = payload.get("extent")
    view = payload.get("view")
    raw_windows = payload.get("windows")
    if not isinstance(extent, dict) or not isinstance(view, dict) or not isinstance(raw_windows, list):
        raise HyprlandError("spatial state response is incomplete")
    if version == 2:
        if not isinstance(payload.get("outputViews"), list):
            raise HyprlandError("invalid spatial protocol 2 output views")
        def integer_fields(value, fields, minimum=-(2**63), maximum=2**63-1):
            if not isinstance(value, dict) or any(type(value.get(key)) is not int or not minimum <= value[key] <= maximum for key in fields):
                raise HyprlandError("invalid spatial protocol 2 integer fields")
        integer_fields(payload, ("revision", "topologyRevision"), 0, 2**64-1)
        integer_fields(payload.get("committedRevision"), ("model", "topology"), 0, 2**64-1)
        integer_fields(extent, ("columns", "rows"), 1, 2**31-1)
        for rect in (view, *payload.get("outputViews", [])):
            integer_fields(rect, ("x", "y"))
            integer_fields(rect, ("columns", "rows"), 1, 2**31-1)
        for item in payload.get("outputViews", []):
            integer_fields(item, ("output", "board"), 1, 2**64-1)
        for item in raw_windows:
            integer_fields(item, ("x", "y"))
            integer_fields(item, ("board",), 1, 2**64-1)
            if type(item.get("visible")) is not bool or not normalize_address(item.get("address")):
                raise HyprlandError("invalid spatial protocol 2 window")
    capabilities = payload.get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
        raise HyprlandError("invalid spatial capabilities")
    source_supported = "surface-source-v1" in capabilities
    source_instance = payload.get("sourceInstance")
    if source_supported and (not isinstance(source_instance, str) or not source_instance):
        raise HyprlandError("invalid surface source instance")
    windows: list[SpatialWindow] = []
    for raw in raw_windows:
        if not isinstance(raw, dict):
            continue
        address = normalize_address(raw.get("address"))
        if not address:
            continue
        source = parse_surface_source(raw.get("source"), source_instance) if source_supported else None
        presentation = raw.get("presentationAvailable") if source_supported else None
        if source_supported and (type(presentation) is not bool or presentation != raw.get("visible")):
            raise HyprlandError("inconsistent spatial presentation availability")
        windows.append(
            SpatialWindow(
                address=address,
                source=source,
                presentation_available=presentation,
                x=int(raw.get("x", 0)),
                y=int(raw.get("y", 0)),
                visible=bool(raw.get("visible", False)),
                mode=str(raw.get("mode", "tiled")),
                primary_output=int(raw.get("primaryOutput", 0)),
                board_id=int(raw.get("board", 0)),
                fragment_outputs=tuple(
                    int(fragment.get("output", 0)) for fragment in raw.get("fragments", []) if isinstance(fragment, dict) and int(fragment.get("output", 0)) > 0
                ),
            )
        )
    raw_output_views = payload.get("outputViews", [])
    output_views = tuple(
        SpatialOutputView(
            output_id=int(raw.get("output", 0)),
            rect=SpatialView(
                x=int(raw.get("x", 0)),
                y=int(raw.get("y", 0)),
                columns=max(1, int(raw.get("columns", 1))),
                rows=max(1, int(raw.get("rows", 1))),
            ),
            anchor_key=normalize_address(raw.get("anchorKey")),
            board_id=int(raw.get("board", 0)),
        )
        for raw in raw_output_views
        if isinstance(raw, dict)
    ) if isinstance(raw_output_views, list) else ()
    display_origin = payload.get("displayOrigin")
    display_origin = display_origin if isinstance(display_origin, dict) else {}
    committed_revision = payload.get("committedRevision")
    committed_revision = committed_revision if isinstance(committed_revision, dict) else {}
    state = SpatialState(
        protocol_version=version,
        active=bool(payload.get("active", False)),
        revision=int(payload.get("revision", 0)),
        columns=max(1, int(extent.get("columns", 1))),
        rows=max(1, int(extent.get("rows", 1))),
        view=SpatialView(
            x=int(view.get("x", 0)),
            y=int(view.get("y", 0)),
            columns=max(1, int(view.get("columns", 1))),
            rows=max(1, int(view.get("rows", 1))),
        ),
        windows=tuple(windows),
        committed=bool(payload.get("committed", False)),
        topology_revision=int(payload.get("topologyRevision", 0)),
        committed_model_revision=int(committed_revision.get("model", 0)),
        committed_topology_revision=int(committed_revision.get("topology", 0)),
        presentation_mode=str(payload.get("presentationMode", "normal")),
        output_views=output_views,
        wide_key=normalize_address(payload.get("wideKey")),
        focused_key=normalize_address(payload.get("focusedKey")),
        selected_output_id=int(payload.get("selectedOutput", 0)),
        display_origin=(int(display_origin.get("x", 0)), int(display_origin.get("y", 0))),
        output_names=tuple((int(output.get("id", 0)), str(output.get("name", ""))) for output in payload.get("outputs", []) if isinstance(output, dict)),
    )
    return replace(state, diagnostics=_spatial_diagnostics(state))


def _spatial_diagnostics(state: SpatialState) -> tuple[str, ...]:
    diagnostics: list[str] = []
    if not state.committed or state.revision != state.committed_model_revision or state.topology_revision != state.committed_topology_revision:
        diagnostics.append("stale-revision")
    if state.presentation_mode not in {"normal", "wide", "desktop"}:
        diagnostics.append("invalid-mode")

    output_ids: set[int] = set()
    if state.committed and not state.output_views:
        diagnostics.append("missing-output-views")
    for index, view in enumerate(state.output_views):
        if view.output_id <= 0 or view.output_id in output_ids:
            diagnostics.append("invalid-output")
        output_ids.add(view.output_id)
        if state.protocol_version == 1 and (view.rect.x < 0 or view.rect.y < 0 or view.rect.x + view.rect.columns > state.columns or view.rect.y + view.rect.rows > state.rows):
            diagnostics.append("view-out-of-bounds")
        if state.protocol_version == 1 and any(_views_overlap(view.rect, other.rect) for other in state.output_views[index + 1 :]):
            diagnostics.append("overlapping-views")
        if view.anchor_key and not any(window.address == view.anchor_key and window.mode == "tiled" and view.rect.contains(window.x, window.y) for window in state.windows):
            diagnostics.append("invalid-anchor")

    focused = next((window for window in state.windows if window.address == state.focused_key), None) if state.focused_key else None
    if state.focused_key and (focused is None or focused.mode != "tiled"):
        diagnostics.append("invalid-focus")
    if state.presentation_mode == "wide":
        wide = next((window for window in state.windows if window.address == state.wide_key), None) if state.wide_key else None
        if wide is None or not wide.visible or (state.protocol_version == 1 and len(wide.fragment_outputs) != len(state.output_views)):
            diagnostics.append("invalid-wide")
        if any(window.visible and window.address != state.wide_key and (state.protocol_version == 1 or (wide and set(window.fragment_outputs) & set(wide.fragment_outputs))) for window in state.windows):
            diagnostics.append("wide-leak")
    elif state.presentation_mode == "desktop":
        if any(window.visible or window.fragment_outputs for window in state.windows):
            diagnostics.append("desktop-leak")
        if state.wide_key and not any(window.address == state.wide_key and window.mode == "tiled" for window in state.windows):
            diagnostics.append("invalid-wide")
    elif state.wide_key:
        diagnostics.append("unexpected-wide-key")
    elif any(
        window.visible
        and window.mode == "tiled"
        and (
            len(set(window.fragment_outputs)) != 1
            or not any(view.output_id == window.fragment_outputs[0] and (state.protocol_version == 1 or view.board_id == window.board_id) and view.rect.contains(window.x, window.y) for view in state.output_views)
        )
        for window in state.windows
    ):
        diagnostics.append("invalid-fragments")
    if state.protocol_version == 2:
        if any(v.board_id <= 0 for v in state.output_views) or len({v.board_id for v in state.output_views}) != len(state.output_views):
            diagnostics.append("invalid-board-binding")
        seen = set()
        for w in state.windows:
            if w.board_id <= 0 or not -(2**63) <= w.x < 2**63 or not -(2**63) <= w.y < 2**63:
                diagnostics.append("invalid-board-coordinate")
            cell = (w.board_id, w.x, w.y)
            if cell in seen:
                diagnostics.append("duplicate-board-cell")
            seen.add(cell)
    return tuple(dict.fromkeys(diagnostics))


def _views_overlap(left: SpatialView, right: SpatialView) -> bool:
    return left.x < right.x + right.columns and right.x < left.x + left.columns and left.y < right.y + right.rows and right.y < left.y + left.rows


class HyprlandClient:
    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        *,
        hyprctl: str | None = None,
    ) -> None:
        self._runner = runner
        self._hyprctl = hyprctl or resolve_hyprctl()
        self._projection_epoch = 0
        self._projection_revisions: dict[str, int] = {}
        self._pending_projections: dict[tuple[str, str, int], tuple[float, int]] = {}
        self._projection_lock = threading.Lock()
        self._projection_local = threading.local()
        self._projection_transport = None

    def _run(self, *arguments: str, timeout: float = 0.8) -> str:
        from .bootstrap import preload_entries, without_preload, LAYER_SHELL
        env = os.environ.copy()
        preload = without_preload(preload_entries(env.get("LD_PRELOAD")), LAYER_SHELL)
        if preload:
            env["LD_PRELOAD"] = preload
        else:
            env.pop("LD_PRELOAD", None)
        try:
            result = self._runner(
                [self._hyprctl, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HyprlandError(f"hyprctl failed: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise HyprlandError(detail)
        return result.stdout

    def query(self, name: str) -> Any:
        try:
            return json.loads(self._run("-j", name))
        except json.JSONDecodeError as exc:
            raise HyprlandError(f"invalid JSON from hyprctl {name}") from exc

    def option_int(self, name: str) -> int:
        try:
            raw = json.loads(self._run("-j", "getoption", name))
        except json.JSONDecodeError as exc:
            raise HyprlandError(f"invalid JSON from hyprctl getoption {name}") from exc
        if not isinstance(raw, dict) or type(raw.get("int")) is not int:
            raise HyprlandError(f"hyprctl option is not an integer: {name}")
        return raw["int"]

    def blur_settings(self) -> tuple[int, int]:
        return (
            self.option_int("decoration:blur:size"),
            self.option_int("decoration:blur:passes"),
        )

    def visual_state(self) -> VisualSettingsState:
        try:
            return parse_visual_settings(json.loads(self._run("luminophorevisualstate")))
        except (ValueError, TypeError, OverflowError) as error:
            raise HyprlandError("invalid visual settings readback") from error

    def visual_settings(self, settings: VisualSettings, receipt: tuple[str, str, int] | None = None) -> VisualSettingsState:
        try:
            result = parse_visual_settings(json.loads(self._run("luminophorevisualsettings", settings.wire(receipt))))
            if not result.configured:
                raise ValueError("visual settings were not configured")
            return result
        except (ValueError, TypeError, OverflowError) as error:
            raise HyprlandError("invalid visual settings apply response") from error

    def apply_visual_settings(self, settings: VisualSettings) -> VisualSettingsState:
        # Read-only generation check precedes the single mutation. Older typed
        # IPC builds can store settings without having connected renderers.
        try:
            settings.wire()
        except (ValueError, TypeError, OverflowError) as error:
            raise HyprlandError("invalid visual settings request") from error
        if settings.schema_version != 1 or settings.preset != "balanced" or not 0 <= settings.intensity <= 3:
            raise HyprlandError("visual settings must be normalized before apply")
        if getattr(self, "_visual_uncertain", False):
            raise SettingsCompletionUnknown("previous visual receipt requires reconciliation")
        before = self.visual_state()
        if before.renderer_schema != 1 or not before.source:
            raise HyprlandError("compositor visual renderer/receipt protocol is unavailable")
        receipt = (before.source, uuid4().hex, before.serial)
        self._visual_request = (settings, receipt)
        self._visual_uncertain = True
        try:
            applied = self.visual_settings(settings, receipt)
            if (applied.renderer_schema != 1 or applied.settings != settings or applied.recovery != "none"
                    or (applied.source, applied.token, applied.serial) != (receipt[0], receipt[1], receipt[2] + 1)):
                raise SettingsCompletionUnknown("visual apply acknowledgment does not match request receipt")
            observed = self.visual_state()
            if observed != applied:
                raise SettingsCompletionUnknown("visual readback changed after apply")
            self._visual_uncertain = False
            return observed
        except HyprlandError as error:
            raise SettingsCompletionUnknown("visual mutation was dispatched but completion could not be verified") from error

    def reconcile_visual_settings(self, *, fence: bool = False) -> str:
        """Prove completion or retirement; never replay an uncertain request.

        An explicit fence writes the currently observed bundle using CAS. Either
        it wins, preventing the old request from running, or the old request wins
        and readback supplies its receipt. Losing the fence ack is also safe.
        """
        request = getattr(self, "_visual_request", None)
        if request is None:
            return "pending"
        settings, (source, token, serial) = request
        state = self.visual_state()
        if not state.source:
            return "pending"
        if fence and state.source == source and state.serial == serial:
            try:
                self.visual_settings(state.settings, (source, uuid4().hex, serial))
            except HyprlandError:
                pass
            state = self.visual_state()
        if (state.source == source and state.serial == serial + 1 and state.token == token
                and state.renderer_schema == 1 and state.settings == settings and state.recovery == "none"):
            self._visual_uncertain = False
            return "complete"
        if state.source != source or state.serial > serial:
            self._visual_uncertain = False
            return "superseded"
        return "pending"

    def set_blur(self, size: int, passes: int) -> None:
        if type(size) is not int or type(passes) is not int or min(size, passes) < 1:
            raise HyprlandError("blur size and passes must be integers of at least 1")
        from .domain_client import DomainClient
        service = DomainClient()
        _, digest = service.snapshot()
        try:
            service.commit({'compositor.blur_size': size, 'compositor.blur_passes': passes}, digest)
        except (ValueError, RuntimeError) as error:
            raise HyprlandError('could not apply blur settings: ' + str(error)) from error

    @staticmethod
    def _quoted_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _window_ref(address: str) -> str:
        normalized = normalize_address(address)
        if not re.fullmatch(r"0x[0-9a-f]+", normalized):
            raise HyprlandError("invalid window address")
        # Hyprland 0.56 dispatcher window parameters accept exact selector
        # strings. Passing an hl.get_window() result here can terminate the
        # compositor instead of focusing the target.
        return json.dumps(f"address:{normalized}")

    def reload_config(self) -> None:
        reply = self._run("reload", timeout=3.0).strip()
        if reply != "ok":
            raise HyprlandError(reply or "설정 다시 읽기 응답 없음")
        errors = self._run("configerrors", timeout=3.0).strip()
        if errors:
            raise HyprlandError(errors)

    def dispatch(self, expression: str) -> None:
        self.native_command('action', name=expression)

    def native_command(self, action: str, **fields) -> str:
        from .toml_edit import literal
        wire = '\n'.join(key + ' = ' + literal(value) for key, value in
                         {'version': 1, 'action': action, **fields}.items())
        if getattr(self._projection_local, 'direct', False):
            reply = self._projection_socket_request('/luminophorecommand ' + wire)
        else:
            reply = self._run('luminophorecommand', wire)
        if reply.strip().startswith('error:'):
            raise HyprlandError(reply.strip())
        return reply.strip()

    def monitors(self) -> list[MonitorRecord]:
        return parse_monitors(self.query("monitors"))

    def windows(self, monitors: list[MonitorRecord] | None = None) -> list[WindowRecord]:
        known_monitors = monitors if monitors is not None else self.monitors()
        return parse_windows(self.query("clients"), known_monitors)

    def active_window(self, monitors: list[MonitorRecord] | None = None) -> WindowRecord | None:
        known_monitors = monitors if monitors is not None else self.monitors()
        raw = self.query("activewindow")
        parsed = parse_windows([raw], known_monitors)
        return parsed[0] if parsed else None

    def spatial_state(self) -> SpatialState:
        command = getattr(self, "_spatial_state_command", "luminophorespatialstate2")
        raw = self._run("-j", command)
        # Capability probing is read-only and cached for this compositor session.
        # Invalid protocol-2 data is never silently treated as protocol 1.
        if command == "luminophorespatialstate2" and raw.strip() == "unknown request":
            self._spatial_state_command = "luminophorespatialstate"
            return parse_spatial_state(self.query("luminophorespatialstate"))
        try:
            return parse_spatial_state(json.loads(raw))
        except (ValueError, TypeError) as error:
            raise HyprlandError("invalid spatial state response") from error

    def spatial_preview(self, request: SpatialEditRequest) -> SpatialEditPreview:
        try:
            raw = self._run("-j", "luminophorespatialpreview", *request.arguments())
            return parse_edit_preview(json.loads(raw))
        except (ValueError, TypeError) as error:
            raise HyprlandError("invalid spatial preview response or request") from error

    def spatial_commit(self, request: SpatialEditRequest) -> SpatialEditPreview:
        try:
            raw = self._run("-j", "luminophorespatialcommit", *request.arguments())
            return parse_edit_preview(json.loads(raw))
        except (ValueError, TypeError) as error:
            raise HyprlandError("invalid spatial commit response or request") from error

    def spatial_grab_begin(self, address, revision, topology, output, press_time):
        values = (int(json.loads(self._window_ref(address)).removeprefix("address:"), 16), revision, topology, output, press_time)
        if any(type(v) is not int or not 0 <= v < 2**64 for v in values) or not 0 < press_time < 2**32:
            raise HyprlandError("invalid editor grab identity")
        fields = ('window_id', 'revision', 'topology', 'output', 'press_time')
        return self.native_command('grab-begin', **dict(zip(fields, map(str, values)))) == 'true'

    def spatial_grab_cancel(self, press_time):
        if type(press_time) is not int or not 0 < press_time < 2**32:
            raise HyprlandError("invalid editor grab cancellation")
        return self.native_command('grab-cancel', press_time=str(press_time)) == 'true'

    def spatial_grab_layout(self, layout: SpatialGrabLayout) -> bool:
        try:
            raw = self.native_command('grab-layout', **layout.payload())
        except (ValueError, TypeError, OverflowError) as error:
            raise HyprlandError("invalid spatial grab layout request") from error
        if raw not in {"true", "false"}:
            raise HyprlandError("invalid spatial grab layout acknowledgement")
        return raw == "true"

    def cursor_position(self) -> tuple[int, int]:
        raw = self.query("cursorpos")
        if not isinstance(raw, dict):
            raise HyprlandError("cursor response is not an object")
        return int(raw.get("x", 0)), int(raw.get("y", 0))

    def minimize(self, address: str) -> None:
        window = self._window_ref(address)
        self.native_command('minimize', window=json.loads(window))

    def _projection_socket_request(self, command: str) -> str:
        path = str(event_socket_path().with_name(".socket.sock"))
        payload = command.encode()
        if len(payload) > 65536:
            raise HyprlandError("projection request too large")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.8)
            connection.connect(path)
            connection.sendall(payload)
            chunks = bytearray()
            deadline = time.monotonic() + 0.8
            while True:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                block = connection.recv(8192)
                if not block:
                    break
                chunks.extend(block)
                if len(chunks) > 65536 or time.monotonic() >= deadline:
                    raise TimeoutError("projection reply exceeds bounds; completion unknown")
        reply = chunks.decode()
        if reply.strip() != "ok":
            raise HyprlandError("projection request rejected: " + reply[:160])
        return reply

    def shell_projection_async(self, name, overlay, generation, content_revision, style, completed):
        from gi.repository import GLib
        from .projection_transport import ProjectionRequest, ProjectionTransport
        if os.getenv("LUMINOPHORE_COMPOSITOR") != "1":
            return False
        if self._projection_transport is None:
            self._projection_transport = ProjectionTransport(self, GLib.idle_add)
        return self._projection_transport.submit(ProjectionRequest(name, overlay, generation, content_revision, tuple(style.items())), completed)

    def reconcile_projection(self, name, generation):
        if self._projection_transport:
            return self._projection_transport.reconcile(name, generation)
        return False

    def consume_projection_receipt(self, name, generation, content_revision):
        with self._projection_lock:
            key = next((k for k, v in self._pending_projections.items()
                        if k[:2] == (f"luminophore-shell-{name}", generation) and v[1] == content_revision), None)
            if key is None:
                return None
            self._pending_projections.pop(key)
        return f"{key[0]},{key[1]},{key[2]},{content_revision}"

    def projection_state(self, name, generation):
        namespace = f"luminophore-shell-{name}"
        with self._projection_lock:
            receipt = next(((key, value) for key, value in self._pending_projections.items()
                            if key[:2] == (namespace, generation)), None)
        if receipt is None:
            return "unknown"
        raw = self._run("-j", "luminophoreshellprojectionstate", namespace)
        if raw.strip() == "unknown request":
            return "unsupported"
        data = json.loads(raw)
        if data.get("version") != 1 or not isinstance(data.get("receipts"), list):
            return "unknown"
        key, (_, content) = receipt
        if any(not isinstance(r, dict) or r.get("state") not in {"prepared", "committed", "presented"}
               or not isinstance(r.get("generation"), str) or type(r.get("revision")) is not int
               or type(r.get("contentRevision")) is not int for r in data["receipts"]):
            return "unknown"
        states = {r.get("state") for r in data["receipts"] if
                  (r.get("generation"), r.get("revision"), r.get("contentRevision")) == (generation, key[2], content)}
        if "presented" in states:
            return "presented"
        if "committed" in states:
            return "unknown"
        # The read executes after the transaction on the compositor's main loop.
        # A prepared-only or absent request has not acquired presentation.
        return "failed"

    def forget_projection(self, name):
        with self._projection_lock:
            self._pending_projections = {k: v for k, v in self._pending_projections.items() if k[0] != f"luminophore-shell-{name}"}
        if self._projection_transport:
            self._projection_transport.forget(name)

    def reset_projections(self):
        if self._projection_transport:
            self._projection_transport.reset()
        with self._projection_lock:
            self._projection_epoch = getattr(self, "_projection_epoch", 0) + 1
            self._pending_projections.clear()

    def close_projections(self):
        if self._projection_transport:
            self._projection_transport.close()
        with self._projection_lock:
            self._pending_projections.clear()

    def shell_projection(
        self,
        name: str,
        overlay: bool,
        generation: str,
        content_revision: int,
        style: dict[str, float],
    ) -> bool:
        if os.getenv("LUMINOPHORE_COMPOSITOR") != "1":
            return False
        surface = f"luminophore-shell-{name}"
        token = generation
        requested_plane = "overlay" if overlay else "bottom"
        role = "osd" if name.startswith("osd-") else "passive"
        bloom = bool(style.get("bloom", os.getenv("LUMINOPHORE_SHELL_BLOOM") == "1"))
        with self._projection_lock:
            revision = max(
                time.monotonic_ns() // 1_000,
                self._projection_revisions.get(name, 0) + 1,
            )
            self._projection_revisions[name] = revision
            epoch = getattr(self, "_projection_epoch", 0)
        numeric = {
            key: float(style.get(key, default))
            for key, default in {
                "red": 0.612,
                "green": 0.796,
                "blue": 0.984,
                "radius": 14.0,
                "outline": 2.0,
                "extent": 64.0,
                "intensity": 1.0,
                "glow_phase": 0.0,
                "reveal_from": 1.0,
                "reveal_to": 1.0,
                "reveal_started": 0.0,
                "reveal_duration": 0.0,
                "reveal_offset_x": 0.0,
                "reveal_offset_y": 0.0,
                "panel_x": -1.0,
                "panel_y": -1.0,
                "panel_width": 0.0,
                "panel_height": 0.0,
                "blur_x": 0.0,
                "blur_y": 0.0,
                "blur_width": 0.0,
                "blur_height": 0.0,
            }.items()
        }

        def transact(phase: str) -> None:
            reply = self.native_command('projection', surface=surface, generation=token,
                revision=str(revision), content_revision=str(max(0, int(content_revision))),
                phase=phase, requested_plane=requested_plane, role=role, bloom=bloom, **numeric)
            if reply != 'ok':
                raise HyprlandError('projection rejected: ' + reply[:160])

        valid = getattr(self._projection_local, "valid", lambda: True)
        try:
            if not valid():
                return False
            with self._projection_lock:
                if epoch != getattr(self, "_projection_epoch", 0) or not valid():
                    return False
                now = time.monotonic()
                self._pending_projections = {key: stamp for key, stamp in self._pending_projections.items() if key[0] != f"luminophore-shell-{name}"}
                self._pending_projections[(f"luminophore-shell-{name}", generation, revision)] = (now, content_revision)
            transact("prepare")
            if not valid():
                transact("abort")
                return False
            transact("commit")
            return True
        except HyprlandError as exc:
            logging.getLogger("luminophore-shell").debug("projection rejected surface=%s reason=%s", name, exc)
            with self._projection_lock:
                self._pending_projections.pop((f"luminophore-shell-{name}", generation, revision), None)
            try:
                transact("abort")
            except HyprlandError:
                pass
            return False

    def shell_projection_presented(self, payload: str) -> bool:
        try:
            surface, generation, raw_revision, _content_revision = payload.split(",", 3)
            revision = int(raw_revision)
            content_revision = int(_content_revision)
        except (TypeError, ValueError):
            return False
        with self._projection_lock:
            receipt = self._pending_projections.get((surface, generation, revision))
            matched = receipt is not None and receipt[1] == content_revision
            if matched:
                self._pending_projections.pop((surface, generation, revision), None)
        if matched and self._projection_transport:
            self._projection_transport.presented(surface, generation, int(_content_revision))
        return matched

    def focus(self, address: str) -> None:
        self.native_command('focus', window=json.loads(self._window_ref(address)))

    def restore(self, window: "RestorableWindow") -> None:
        target = self._window_ref(window.address)
        self.native_command('restore', window=json.loads(target))


class RestorableWindow:
    address: str
    monitor_name: str
    workspace: WorkspaceRef
    floating: bool
    at: tuple[int, int]
    size: tuple[int, int]


def event_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    try:
        namespace, signature = compositor_instance()
    except CompositorRuntimeError as error:
        raise HyprlandError(str(error)) from error
    if not runtime or not Path(runtime).is_absolute():
        raise HyprlandError("compositor runtime environment is unavailable")
    return Path(runtime) / namespace / signature / ".socket2.sock"


class HyprlandEventListener:
    """Small reconnecting event listener; consumers resnapshot on each event."""

    def __init__(self, callback: Callable[[str, str], None]) -> None:
        self.callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="luminophore-hypr-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(1.0)
                    connection.connect(str(event_socket_path()))
                    self.callback("luminophoreconnection", "connected")
                    pending = bytearray()
                    while not self._stop.is_set():
                        try:
                            block = connection.recv(4096)
                        except socket.timeout:
                            continue
                        if not block:
                            break
                        pending.extend(block)
                        while b"\n" in pending:
                            raw, _, remainder = pending.partition(b"\n")
                            pending = bytearray(remainder)
                            line = raw.decode("utf-8", errors="replace")
                            name, separator, data = line.partition(">>")
                            if separator:
                                self.callback(name, data)
            except (OSError, HyprlandError):
                self._stop.wait(0.5)
