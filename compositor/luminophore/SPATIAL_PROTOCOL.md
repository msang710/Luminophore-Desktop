# Independent monitor boards

LUMINOPHORE uses one session-stable board per monitor connector. Coordinates are signed
64-bit integers; a default view is a viewport, not board capacity. A window is
identified by its existing public window address plus its board in snapshots.
Connector identity and return leases last for the compositor session.

## Query and editing contract

`hyprctl -j luminophorespatialstate2` returns `protocolVersion: 2`. The old
`luminophorespatialstate` endpoint fails closed with a migration error. Deploy the
matching Shell and compositor together. The transport is still JSON/string IPC;
C++ command variants provide internal type checking, not a cross-process typed
transport.

- `revision` is the atomic model commit generation; `topologyRevision` is the
  observed output topology. `committedRevision` must match both before editing.
- `outputViews[].board` binds a connector output to its independent board.
  Views on different boards can use identical coordinates.
- `windows[].board` disambiguates identical coordinates. A visible window can
  have multiple non-overlapping `fragments` on one output. Its `box` is a single
  client bounding rectangle sampled through those regions.
- `focusRevision` distinguishes focus ownership changes. `meshes[]` supplies
  each board's mesh revision and `lastResetRevision`; zero means no explicit
  reconcile reset. Mesh changes also advance the model revision.
- Edit identities are decimal strings in Lua requests to avoid numeric rounding.
  Coordinate arguments are Lua integers, preserving exact signed 64-bit values.
- A native grab layout is tied to a grab generation and an acknowledged editor
  frame. Each cell may name its destination `output`; the frame remains on the
  original editor surface. Rebinding invalidates the previous cell geometry.
  Stale generations cannot commit a drop. Occupied drops swap only.

The editor renders a bounded viewport rather than allocating the whole board.
Its display origin is separate from logical coordinates. Monitor tabs switch
boards, and navigation controls move the editor viewport. Per-monitor palettes
identify the selected board. No coordinate extent is inferred from UI dimensions.

## Default view settings

Lua options `misc.luminophore_default_view_columns` and `misc.luminophore_default_view_rows`
accept 1–64, defaulting to 2. Shell settings are
`compositor.default_view_columns` / `compositor.default_view_rows`.
Changing them updates future board defaults and automatic shrink lower bounds;
it does not reset an existing user-edited view. These are LUMINOPHORE-specific options,
not upstream Hyprland options.

## Presentation ownership

The active runtime uses independent-board transactions, then mesh ownership,
projection and one native commit. The default model constructor always creates independent boards. Legacy
finite-model construction requires the explicitly named `finiteFixture` factory
used by baseline regression tests; no runtime caller selects or dual-writes it. Rendering and hit testing consume `regionsFor`, including animated
geometry. Native popup surfaces retain their own geometry and input region;
subsurfaces of a client inherit its clip. Window capture uses capture-local
coordinates. Existing window effects use the same owned-region clip.

WIDE uses the connected set of edge-adjacent outputs with identical full logical
sizes. Workarea reservations and differing physical scales do not determine
eligibility. Other outputs keep their normal boards. Normal fullscreen suppression
is output-local. The unified effect predicate consumes native decoration/role
facts and suppresses window effects in WIDE/fullscreen; Shell/OSD effects remain
owned by their existing path.

Resize captures an exposed face for the native gesture. An ownership change that
removes that edge terminates the gesture. The pure solver propagates pressure
through local slabs and keeps a 100-logical-pixel core where the original geometry
permits it. Client bounding-size constraints are additional limits. Concave client
constraints are checked on their complete bounding union and may conservatively
clamp a move. They do not impose a rectangular visible shape.

`benchmark-spatial-boards` measures CPU candidate/transaction/projection/prepare
cost at 75, 300 and 1,000 windows. It does not measure client configure latency,
GPU work, missed frames or real pointer-to-presentation latency. Those require
an actual graphics session.

## Native drag target and badge asset

`luminophorespatialgrab` schema 2 keeps `output` as the fixed source identity and adds
`targetOutput` (0 in physical monitor gaps) and monotonic `targetEpoch`.
Crossing a physical monitor boundary retains the grab generation and animation.
`spatial_drag_layout` includes `target_epoch`; an old epoch cannot invalidate a
new layout. Only matching presented frame geometry permits a drop. A release
rechecks its final physical target and consumes the grab before committing once.

Shell resolves the icon once, decodes it without a GTK window or snapshot, and
submits a 64×64 row-major alpha mask as exactly 8192 lowercase hex characters
through `hl.dsp.luminophore.spatial_badge`. Identity fields `generation` and
`target_epoch` are exact decimal strings. `red/green/blue` are finite 0..1 theme
primary components. An empty mask updates tint only; malformed/stale requests
are rejected. No file path, decoder, or filesystem access enters the compositor.

The native shader tints the silhouette and emits its halo. A built-in generic
mask is available immediately. Icon readiness never gates release; editor frame
readiness does. Native motion controls position across intersecting outputs,
retains the shrink/crossfade curve, and transitions tint on target changes.
End/cancel clears the texture. Late registration cannot attach to another grab.
The matching compositor/Shell pair must be installed together.

## Native auxiliary windows

Board participation reuses the X11 role result populated by `checkBorders()`:
notification, tooltip, popup/menu, combo, splash and override-redirect surfaces
are external overlays unless already parent-attached. The initial native floating
path consumes the same result and preserves the existing `wantsFocus()` decision.
No Steam class/title blacklist or duplicate atom table is introduced. User
`decorate=false` and ordinary floating state do not mark a window auxiliary.
An auxiliary window does not allocate a board coordinate or trigger board growth;
existing observation removal and native rendering/input paths remain authoritative.

## Editor-origin native window drags

`hl.dsp.luminophore.spatial_drag_begin` captures exact decimal string fields
`window_id`, `expected_revision`, `topology_revision`, `output`, and `press_time`.
The last field is the original primary-button event timestamp (uint32), not an
IPC timestamp. Native input checks that this same press is still held and that
it began on a presented editor surface on the specified output. Delayed requests
cannot acquire a later click. The dispatcher acknowledges acceptance as a bool;
all subsequent motion and release belong to native input, not GTK or Shell IPC.
`spatial_drag_cancel({press_time="..."})` cancels only that editor-origin press.

Grab events carry optional `editorOrigin` (default false). An editor-origin grab
preserves the persistent editor viewport and does not replay its reveal animation.
Its end returns to the prior persistent mode. Shell resolves geometry and badge
assets through the existing frame/target-epoch contracts. Window movement no
longer uses the Shell preview/commit worker; view handles retain that worker.

Tiled and board-owned floating drags share the native lifetime and pointer-based
monitor selection. Floating motion is visual until release; screen drops preserve
the grab offset and logical pixel size, while grid drops relocate the floating
host without turning it into a tiled window. Auxiliary app-controlled surfaces
remain on their native compatibility path. No automatic restart or deployment is
part of this source change.

### Settled drag termination

Schema 2 `end`/`cancel` events additionally report `settledRevision`,
`settledTopologyRevision`, and `settledCommitted`. These describe the runtime
snapshot after the move result, native input ownership cleanup, and pointer
monitor focus synchronization. `revision` still identifies the original grab;
`result` still describes the move transaction. Clients must not overwrite either
with the settled revision. The three fields are optional as a group for older
producers and are invalid on nonterminal events.

A persistent editor refreshes directly on termination. A new held press during
that refresh may wait for a valid unchanged input target, but may not be replayed
after release, cancellation, remapping to another target, or connection reset.
Native press identity and exact source revision checks remain mandatory.
