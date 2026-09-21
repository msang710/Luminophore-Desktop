# Luminophore Desktop — Compositor

[한국어](README.md) · **English**

This directory owns reproducibility metadata for the pinned LUMINOPHORE Hyprland
fork. It deliberately does not install a session or alter `/usr/bin/Hyprland`.

## Verify source

```sh
./luminophore/scripts/verify-source
```

## Build an isolated canary artifact

```sh
./luminophore/scripts/build-canary
```

The canary build enables the compositor-owned LUMINOPHORE effects by default. Set
`LUMINOPHORE_EFFECTS=0` to build the same source with the effect integration compiled
out for an A/B artifact; neither mode activates a live session.

The build is placed at `luminophore/artifacts/<binary-sha256>/`. The executable is
`bin/luminophore-hyprland-canary`; `--version` prints the LUMINOPHORE build identity followed
by the embedded upstream version. Run it only from an explicitly selected
isolated session in a later, separately approved deployment step.

The wrapper exports `LUMINOPHORE_COMPOSITOR=1` to the canary session. The spatial
bindings use `SUPER+D` for floating, `SUPER+F` for fullscreen, `SUPER+G` for
WIDE, `SUPER+X` for DESKTOP, and `SUPER+SPACE` for the persistent editor.
The editor is also shared by native SUPER dragging. The former top-drop,
edge half-clear, launcher-drop minimize and numbered workspace adapters are
retired; shake isolate and individual restore remain independent actions.

## Semantic visual settings

The Settings appearance page saves only `[visual]` schema version, `balanced`
preset, enabled, breathing and intensity (0–3). The compositor owns blur kernels,
bloom extent, alpha curves and damage margins. Missing settings use balanced
defaults; malformed or old bundles recover as a whole. Loading never rewrites
legacy TOML. Saving creates a complete semantic table and preserves unrelated
text. With that table present, legacy glow fields are derived compatibility
values and cannot override it. Existing compositor blur configuration remains
available to ordinary, non-LUMINOPHORE render passes.

`hl.dsp.luminophore.visual_state()` returns normalized settings, recovery reason,
revision and the renderer/receipt protocol capabilities. `rendererSchema=1`
means effects were compiled in; it does not attest GPU presentation. The
Settings UI requires both the renderer and receipt capabilities to apply live.

`hl.dsp.luminophore.visual_settings(settings, source, token, serial)` captures the five
semantic fields and separate receipt metadata. Source is a per-owner lifetime
identity; serial is an exact decimal uint64 string, and token is a bounded ASCII
identifier. The source and expected serial must match at execution. Every
successful apply advances serial, including a bundle-preserving operation;
revision advances only when the bundle changes. The one-argument form retains
whole-bundle recovery for configuration callers but still advances serial.

Shell verifies its exact receipt after a single mutation. A lost acknowledgment
leaves a discoverable pending request and blocks new writes through both Settings
frontends. Status checks never replay it. Explicit recovery can fence a queued
request by conditionally writing the currently observed bundle, preserving the
visible settings. Whichever request wins advances serial and rejects the loser.
Unstarted Shell request IDs can also be retired before delayed IPC work begins.
Reconnection cancels stale editor gestures; the same visual owner is read only,
and a new owner receives the persisted bundle. Actual session, input and GPU
acceptance remain separate from source tests and builds.

## Independent spatial boards

The monitor-board runtime and Shell use the [version 2 spatial contract](SPATIAL_PROTOCOL.en.md). The matching Shell is required; the legacy state endpoint no longer supplies editable snapshots.
