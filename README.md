# Luminophore Desktop

Luminophore is one Linux desktop product: its compositor, control client, widget
shell, greeter, session launcher, portals, settings and private runtime are built
and distributed together. It has its own Wayland desktop session alongside
Hyprland. Custom kernels, EFI renderers and bootloader experiments are outside
this product; the kernel and graphics drivers are supplied by the host OS.

## Source layout

- `compositor/`: compositor, control tools, native TOML settings and packaging assets.
- `config/`: Shell, greeter, widget assets, desktop integration and user services.
- `Luminophore-OS/runtime/`: whole-desktop build, package, generation and session tools.
- `Luminophore-OS/update-guardian/`: desktop-only host update observation and follow-up.

The `Luminophore-OS` directory name is retained for existing build/test paths.
It contains no custom kernel or EFI implementation in this repository.
Upstream compositor dependencies are pinned Git submodules:

```sh
git clone --recurse-submodules https://github.com/msang710/Luminophore-Desktop.git
```

## Current baseline

The initial source import records the independent-session stabilization of
2026-09-22. The deployed package is `luminophore-compositor 0.1.0rc6-8`;
the package name is historical and includes the complete desktop runtime.
See [release metadata](releases/0.1.0rc6-8.json) for the exact binary digest.
Build/runtime tooling is documented in [runtime/README.md](Luminophore-OS/runtime/README.md).
Packaging inputs live in `compositor/luminophore/packaging/arch/`.

Boot and the disappearance of overlapping widget glow were confirmed on the
maintainer's CachyOS machine. The overlap was caused by the old NEON Shell and
Luminophore Shell running simultaneously, not by different visual setting values.
Existing NEON installations must retire their old Shell service and its child
bloom process before using this desktop. This existing package does not perform
that legacy cleanup automatically. Do not run both Shells in one Wayland session.

The boot greeter uses the Luminophore UI; session unlock currently uses gtklock.
Their appearance is not yet unified. General hardware compatibility and unattended
upgrades are not implied by this stabilization baseline. Host updates are observed,
not blocked. Installing the package does not itself switch the display manager.

## Validation

```sh
cd Luminophore-OS/runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

The release package was built before this monorepo import and is published unchanged;
this import does not claim a new reproducible build. Local backups, session logs,
package payload trees and custom kernel sources are excluded.

## Licenses

The repository's root LICENSE is GPL-3.0. Existing component and third-party
licenses and notices remain in their original directories, including the Shell's
MIT license and the compositor's upstream license. See those files for their scope.
