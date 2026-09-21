# Luminophore Desktop

[한국어](README.md) · **English**

Luminophore is an **independent Wayland desktop environment descended from a
Hyprland-based compositor fork**. It is not a rice layered on top of another
desktop session: the compositor, Shell, Settings, greeter, session launcher,
portal, and private runtime are developed and distributed as one desktop product.

It installs as its own `Luminophore` session alongside the system Hyprland
session and uses separate configuration paths, IPC, systemd units, portal
identity, and runtime generations.

## Core concept

### Spatial Desktop

Instead of a traditional tiling workspace model, Luminophore uses **independent
per-monitor Boards and Views**. Windows occupy cells on a finite two-dimensional
board, while a View represents the region currently visible on screen.

- Independent Board per monitor
- One spatial position per managed window
- Spatial navigation and editing through Views
- Luminophore-specific WIDE/DESKTOP window modes
- Native drag, preview, and collision handling

See the [Spatial Protocol](compositor/luminophore/SPATIAL_PROTOCOL.en.md) for the
runtime contract.

### One managed desktop product

Luminophore manages these components inside one product boundary:

- Luminophore compositor
- Luminophore Shell and widgets
- Settings
- Greeter and session launcher
- xdg-desktop-portal backend
- Wallpaper and visual-effect runtime
- Package / generation / rollback runtime

They are distributed as compatible generations rather than as unrelated pieces
to be mixed independently.

## Technology stack

| Area | Main technologies |
|---|---|
| Compositor | C++, Wayland, Hyprland fork lineage |
| Shell / Settings / Greeter | Python, GTK4, PyGObject, gtk4-layer-shell |
| Native visual / wallpaper helpers | C/C++, EGL, OpenGL ES 2.0, Wayland |
| Configuration | Lua, TOML |
| Desktop integration | systemd user services, UWSM, greetd, XDG portal |
| Build / release runtime | Python, Bubblewrap, content-addressed immutable generations |
| Packaging | Arch Linux / CachyOS `.pkg.tar.zst` |

The kernel and GPU drivers are supplied by the host Linux distribution rather
than by Luminophore.

## Installation

The current public release is a **prerelease package for Arch Linux / CachyOS
x86_64**.

1. Download the latest `luminophore-compositor-*.pkg.tar.zst` and
   `SHA256SUMS` from [GitHub Releases](https://github.com/msang710/Luminophore-Desktop/releases).
2. Verify the checksum in the download directory.

```sh
sha256sum -c SHA256SUMS
```

3. Install the package.

```sh
sudo pacman -U ./luminophore-compositor-*.pkg.tar.zst
```

4. Log out and select **Luminophore** from your Wayland session selector.

The package does not `provide`, `replace`, or `conflict` with the system
Hyprland package, and installation does not switch your display manager.

> The current release is a prerelease. Do not treat it as a stable release with
> guaranteed compatibility across general hardware or unattended upgrades.

## Getting the source

Upstream compositor dependencies are pinned Git submodules.

```sh
git clone --recurse-submodules https://github.com/msang710/Luminophore-Desktop.git
cd Luminophore-Desktop
```

See the [runtime documentation](Luminophore-OS/runtime/README.en.md) for the
whole-DE reproducible build and packaging pipeline.

## Repository layout

- `compositor/`: compositor, control tools, native settings, Luminophore spatial model, and packaging assets
- `config/`: Shell, Settings, Greeter, widgets, wallpaper/visual effects, and desktop integration
- `Luminophore-OS/runtime/`: whole-DE build, packaging, immutable generations, session and rollback tooling
- `Luminophore-OS/update-guardian/`: host-update observation and follow-up validation tooling
- `releases/`: public release metadata

The `Luminophore-OS` directory name is retained for compatibility with existing
build/test paths. This repository does not contain a custom kernel or EFI implementation.

## Current status

The current baseline release is `v0.1.0-rc6.8`, validated to boot as an
independent Wayland session.

Known limitations and migration notes for a specific release belong in its
[release notes](https://github.com/msang710/Luminophore-Desktop/releases/tag/v0.1.0-rc6.8).
The README intentionally focuses on the current product model and how to use it,
not incidents from a maintainer's development machine.

## Validation

Runtime unit tests:

```sh
cd Luminophore-OS/runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

Passing source tests and builds does not establish real GPU, input,
suspend/resume, monitor-hotplug, or complete graphical-session acceptance.
Physical-session validation remains a separate acceptance stage.

## Licenses

The repository root LICENSE is GPL-3.0. Existing component and third-party
licenses and notices remain in their original directories, including the
Shell's MIT license and the compositor's upstream license.
