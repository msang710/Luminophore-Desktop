# Luminophore runtime tools

[한국어](README.md) · **English**

This directory implements development tooling for content-addressed DE releases.
It does not install packages, update the running OS, change a bootloader, or
restart an existing desktop. The existing compositor `v1` artifact remains a
diagnostic compositor-only format. Whole-DE artifacts use `luminophore-release/v2`.

Run `python3 -B luminophore-runtime --help` from this directory.

## Build and package

`build-lock` records every source/sysroot file, mode and symlink, the argument
vector and `SOURCE_DATE_EPOCH`. Save its JSON outside both input trees. `build`
rechecks the lock before and after a Bubblewrap invocation, with no network,
a read-only sysroot at `/`, read-only sources at `/src`, and an empty writable
output at `/build`. Prepare the `/src`, `/build`, `/proc`, `/dev`, `/tmp` mount
points in the build root first. Keep inputs immutable for the whole operation;
the lock's before/after checks cannot detect a transient mutation followed by
restoration. A read-only filesystem snapshot or image is appropriate.

```sh
python3 -B luminophore-runtime build-lock --sysroot /build-input/root \
  --source /build-input/source --epoch 1700000000 -- /bin/sh /src/build.sh > build-lock.json
python3 -B luminophore-runtime build --sysroot /build-input/root \
  --source /build-input/source --lock build-lock.json --output /build-output
```

The supplied build command must build **all** components and their private
dependencies from those inputs and produce a staged sysroot. The tools do not
download a toolchain or infer package versions from the developer's `/usr`.
Repeat independently and compare output digests before claiming reproducibility.

`pack --sysroot ROOT --recipe RECIPE --output ARTIFACTS` consumes a staged root.
The recipe has these fields (all required):

| Field | Meaning |
|---|---|
| `schema` | Integer `1` |
| `files` | Map of artifact-relative destinations to sysroot-relative files or trees |
| `providers` | Map of SONAME to `{ "path": "usr/lib/…", "owner": "private" or "system" }` |
| `dynamic` | Explicit sysroot-relative ELF paths loaded through dlopen/ctypes/GI |
| `entries` | Named components, each `{ "path": "bin/…", "args": [] }` |
| `compatibility` | Positive integer `config` and `ipc` versions |
| `provenance` | Nonempty `source`, SHA-256 `build_lock`, and nonempty `license` |
| `system_files` | Additional OS-relative file → SHA-256 pins for commands, services, drivers and resources |
| `runtime_env` | Relative directories for `PYTHONHOME`, `GI_TYPELIB_PATH`, `GIO_MODULE_DIR`, `GSETTINGS_SCHEMA_DIR` as needed |

`compositor`, `control`, and `shell` entries are mandatory. Entries execute ELF
files, so scripts must name their bundled interpreter and put the script in
`args`. `control` must have no prefix arguments, because shell consumers expect
an executable control-client path. `{release}` in arguments expands to the
immutable release directory. The compositor entry must include arguments that
load the packaged configuration/autostart files; it is not inferred from the
working directory. A Python shell entry has this shape:

```json
{"path":"python/bin/python3","args":["-B","-s","{release}/shell/luminophore-shell","daemon"]}
```

The integrated release autostart dispatcher currently assumes `shell.path` is
the bundled Python interpreter. Its standard library, site-packages (including
profile-worker dependencies), GTK/GI/typelibs, layer-shell, native helpers,
schemas, assets, licenses and configuration must be mapped explicitly. Python
virtualenv symlinks back to the host are not a private runtime. The `support/`
directory is reserved: the packer puts its dispatcher there so restarts use
the same version of the launch code.

The packer recursively resolves ELF `NEEDED` entries against explicit providers,
checks SONAMEs, records symbol-version names and interpreters, and copies private
providers into `lib/`. Missing providers fail closed. System providers and their
transitive dependencies must remain system-owned; a system→private edge is
rejected. This prevents accidentally mixing two versions of a shared graphics
dependency in one process. Keep glibc/ELF loader and the supported GPU stack in
the OS profile. Preserve the complete matching GPU userspace/kernel combination.

Each copied ELF must already have a relocation-safe RUNPATH to the release's
`lib/`. For example, `bin/Hyprland` uses `$ORIGIN/../lib`; a library in `lib/`
uses `$ORIGIN`. `pack --patchelf /absolute/path/to/patchelf` can rewrite RUNPATH
in the staging copy. Pin that tool in the build environment. No global
`LD_LIBRARY_PATH`, `ldconfig` modification, or SONAME compatibility symlink is
created. Source symlinks must resolve within the supplied root; artifact files
are regular copies. Unexpected files, modes, hashes and path escapes fail
verification.

`provenance.build_lock` is a reference, **not an attestation that this particular
binary was built under that lock**. Hash integrity is also not a signature or
license audit. A release process must connect the build receipt, provenance,
per-package licenses/SBOM, signature and independent rebuild evidence before
publishing. The packer always records session acceptance as `NOT_RUN`.

The existing compositor script dispatches these explicit modes without running
its legacy host CMake build:

```text
build-canary --locked-build SYSROOT SOURCE LOCK OUTPUT
build-canary --pack-release STAGED_SYSROOT RECIPE ARTIFACTS
```

## Desktop packaging profile

### Locked component builds and assembly

`build-desktop` now connects the locked builder to the desktop packer. It runs
the lock's command, verifies the resulting complete output tree against the
build receipt, and assembles declared package roots into one staged sysroot.
It does not construct a toolchain from the live machine or download packages.

Prepare an immutable source tree with this layout for the supplied component
builder (`runtime/` is a copy of these tools):

```text
source/
  compositor/                    # pinned complete source + vendored dependencies
  config/                        # matching shell sources and native helpers
  release-config/                # reviewed release-aware Lua configuration
    hyprland.lua
    autostart.lua
  packages/                      # prepared, pinned dependency package roots
    <package-name>/usr/...       # private Python/GI/runtime and OS profile files
  runtime/scripts/build-components.py
  glaze/                         # optional pinned FetchContent source override
  layout.json
```

The builder copies compositor and shell sources into `/build/work` before
building. This matters because compositor CMake writes generated files into its
source directory. It compiles compositor/control/watchdog and shell glow,
background and drag helpers, then writes product roots to
`/build/packages/luminophore-compositor` and
`/build/packages/luminophore-shell`. It copies `/src/packages` alongside them.
The build-only helper scripts are used; deployment scripts and service-starting
installers are not called. Network access remains disabled by Bubblewrap.

The toolchain root must already contain compatible CMake/Ninja/compiler,
Python, protocol generators and development dependencies. Prepared dependency
package roots must provide the complete private interpreter/stdlib/modules,
GI/resources and system profile. They must be generated from pinned inputs and
listed individually in the layout. This script does **not** build Python,
resolve distribution repositories, or build/install the optional window-glow
plugin against a possibly different compositor SDK. If that plugin is enabled,
provide its matching built package explicitly. Neither the release config nor
the public greeter/session units are inferred from the current personal setup.

If `source/glaze/CMakeLists.txt` exists, the component builder passes that locked
tree as `FETCHCONTENT_SOURCE_DIR_GLAZE`. This permits a clean offline CMake
configuration without a pre-populated `_deps/glaze-src` build directory. The
override must remain inside the locked source tree. Otherwise the toolchain
must supply a compatible installed Glaze CMake package; disabling downloads does
not itself provide the dependency.

`profiles/cachyos-2026-09-20.lock.json` is a concrete **build-toolchain input**
selection. It lists exact cached package archive names, versions, archive and
signature SHA-256 hashes, verification-key hashes, source commits and extraction
normalization. Some archives target x86_64_v3; it is not a universal CPU profile.
The package list is not a private-runtime ownership policy or desktop release.
It includes compilers and development dependencies that do not belong in a
shipped DE artifact.

To reconstruct this input, match every archive/signature and verification key
to its pinned hash before extraction. Use temporary binary keyrings when the
distribution key file is ASCII armored, verify the detached signatures, and
extract into a new unprivileged root without invoking package install scripts.
Extract `filesystem` first, omit `.PKGINFO`, `.BUILDINFO`, `.MTREE` and `.INSTALL`,
preserve containment checks, and add owner-read permission to the explicitly
listed normalization paths. Prepare `/src`, `/build`, `/proc`, `/dev` and `/tmp`
mount points. `build-lock` then binds the resulting files and modes, not merely
the package version strings. Do not use a package-manager transaction against
the running host to prepare this build root.

`profiles/cachyos-python-2026-09-20.lock.json` extends that exact base with
Pycairo, Python D-Bus, NumPy and its BLAS/LAPACK dependency archives plus the
project-pinned OpenCV headless wheel. The wheel is content-pinned; it does not
have a distribution package signature. Validate the base profile hash before
combining inputs. The GUI imports Cairo and D-Bus directly, and the offline
profile worker imports NumPy/OpenCV, so Pillow/requests/PyGObject alone do not
form a complete shell runtime. A separate-root import probe does not establish
that the relocated private release works: RUNPATH, typelib/resource paths and
host/private ownership still need the artifact checks and runtime acceptance.

`layout.json` is part of the locked source. Its exact fields are:

| Field | Contract |
|---|---|
| `schema` | `"luminophore-build-layout/v1"` |
| `desktop` | Desktop input below, excluding `packages`; `provenance` contains only `source` and `license`, and `system_files` is a list of paths |
| `packages` | Records with `root`, `name`, `version`, `owner`, `license`, `license_files` |

Each package `root` is relative to build output, for example
`packages/luminophore-shell`. Its contents already have their final sysroot
paths such as `usr/lib/luminophore/shell/luminophore-shell`. Other package fields
have the same meaning as the desktop inventory below. `license_files` uses
paths relative to that package root. Product license paths are
`usr/lib/luminophore/share/licenses/luminophore-shell/LICENSE` and
`usr/lib/luminophore/share/licenses/luminophore-compositor/LICENSE`.
Use actual source/package versions; do not label unknown versions as verified.

Assembly calculates `files` hashes and system-file pins from the copied bytes
and injects the actual build-lock digest into provenance. Any file collision,
even identical bytes, overlapping package roots, escaping symlink, untracked
source/output mutation or missing desktop dependency fails before publication.
Contained file symlinks become regular copies. Directory symlinks and special
files are rejected. Inputs must remain immutable throughout the operation;
before/after hashes cannot detect changes that are restored between checks.

```sh
python3 -B luminophore-runtime build-lock --sysroot /inputs/toolchain \
  --source /inputs/source --epoch 1700000000 -- \
  /usr/bin/python3 -B /src/runtime/scripts/build-components.py > build-lock.json
python3 -B luminophore-runtime build-desktop --sysroot /inputs/toolchain \
  --source /inputs/source --lock build-lock.json --layout layout.json \
  --build-output /outputs/build --output /outputs/assembly
python3 -B luminophore-runtime verify-assembly --assembly /outputs/assembly
python3 -B luminophore-runtime pack-assembly --assembly /outputs/assembly \
  --output /outputs/releases --patchelf /build-tools/patchelf
```

Build logs go to stderr and CLI result JSON goes to stdout. Existing output
assemblies are never replaced. A failed build leaves its diagnostic build
directory, but does not publish an assembly. Assembly publication uses a
sibling staging directory, file/directory fsync and rename. The published
directory contains `root/`, `desktop-input.json`, `build-receipt.json`,
`build-layout.json` and a content-digested `assembly.json`.

For a build already performed with `build`, use `assemble-desktop --source …
--build-output … --lock … --receipt … --layout layout.json --output …`.
The receipt and source/output identities must match. A caller-supplied receipt
is not an attestation that the command was executed; `build-desktop` performs
the execution in the current process. Signing and independent rebuild evidence
are still separate release responsibilities.

`pack-assembly` retains the assembly manifest, receipt and layout inside
`metadata/build-assembly.json`. Reads and copies are bound to the assembly's
verified file hashes and executable modes, so changes after verification do
not get published under older build evidence. A self-consistent digest is not
authentication: obtain inputs and their expected identities through a trusted
release process. The fixture test exercises a real isolated ELF builder through
CLI build → assembly → packaging. It does not compile the real compositor or
validate the full production Python/GI/graphics stack.

### Desktop layout and inventory

`desktop-recipe` and `pack-desktop` provide a concrete desktop-session layout
above the generic packer. They consume **prepared build outputs**, not the live
`/usr`, and never install packages. Prepare this tree inside the staged sysroot:

```text
usr/lib/luminophore/
  bin/Hyprland
  bin/hyprctl
  python/bin/python3
  python/lib/python3.X/                 # complete matching stdlib + extensions
    encodings/__init__.py
    site-packages/                      # complete dependency sets, including:
      gi/__init__.py                    # and its matching _gi*.so
      PIL/__init__.py
      requests/__init__.py
      cairo/__init__.py                # plus _cairo*.so
      dbus/__init__.py                 # plus top-level _dbus_bindings*.so
      numpy/__init__.py                # plus _core/_multiarray_umath*.so
      cv2/__init__.py                  # plus cv2*.so and wheel native libraries
  shell/luminophore-shell
  shell/luminophore_shell/              # complete shell package
    __main__.py
    bootstrap.py
    assets/
    templates/
  config/hyprland.lua                   # release-aware entry configuration
  config/autostart.lua                  # generation-pinned autostart
  lib/libgtk4-layer-shell.so.0
  lib/girepository-1.0/                 # complete typelib closure, including:
    Gtk-4.0.typelib
    Gtk4LayerShell-1.0.typelib
  lib/gio/modules/                      # matching native GIO modules
  share/glib-2.0/schemas/gschemas.compiled
```

The entire staged subtree is copied, including additional commands, plugins,
configuration, assets and resources. External private ELF providers are added
by the existing recursive scanner. `metadata`, `support` and `release.json`
are reserved top-level names. Source symlinks must stay within the sysroot;
private Python must not refer back to the host. Configure the compositor's Lua
module paths and includes to load this release's config directory. The generated
entry passes `--config {release}/config/hyprland.lua`; it does not rewrite Lua.

Supply a JSON input with exactly these fields:

| Field | Value |
|---|---|
| `schema` | `"luminophore-desktop-input/v1"` |
| `python_version` | Explicit major.minor, e.g. `"3.14"`, matching the built interpreter |
| `compatibility`, `provenance`, `providers`, `dynamic`, `system_files` | Same contracts as the generic recipe above |
| `packages` | Nonempty array of package records below |

Each package has `name`, `version`, `owner` (`private` or `system`), `license`
(publisher's license expression), `license_files` (sysroot-relative paths), and
`files` (sysroot-relative path → SHA-256). Obtain the file lists and versions from
the pinned package/build inputs, then hash those paths in the staged sysroot.
Do not generate ownership by assuming every unclassified library is private.
Every included resource, copied provider, declared dynamic module and pinned OS
file (including loader aliases) must have exactly one owner with the matching
ownership class and digest. A package cannot cross the private/system boundary;
split the build inventory into explicit records if that is intentional.

Every private package must provide at least one license file belonging to its
own file list. Those texts are copied to `licenses/<package>/<index>-<basename>`.
The records are a publisher declaration, not a license audit, signed provenance
or an SPDX/CycloneDX SBOM. A string naming a license does not verify its terms.

```sh
python3 -B luminophore-runtime desktop-recipe --sysroot /build-output/root \
  --input desktop-input.json > desktop-recipe.json
python3 -B luminophore-runtime pack-desktop --sysroot /build-output/root \
  --input desktop-input.json --output /build-output/artifacts \
  --patchelf /build-tools/patchelf
```

`desktop-recipe` is a read-only inspection command. Use `pack-desktop` for the
release: it repeats the checks, binds system hashes and copied source bytes to
the inventory, and embeds `metadata/desktop-inventory.json` under the generation
digest. Passing the inspection recipe directly to generic `pack` does **not**
retain those extra inventory checks or metadata. Input hashes describe the
original build outputs; the artifact manifest describes the bytes after RUNPATH
patching. Keep the sysroot immutable throughout packaging. A failed check never
publishes a new generation; an earlier existing generation remains intact.

Generated entries include compositor, control, shell daemon and standalone
settings. Private Python/GI/resource paths use the prior session dispatcher.
The profile checks structural completeness and ELF closure, not Python import
success, typelib compatibility, feature completeness or a graphical session.
The tests use real ELF programs with **fixture-only** Python/resource contents.
A production toolchain/dependency sysroot, a full component build with the supplied
or project-specific builder, import/resource probes, greeter packaging, public
session/service installation and graphical acceptance remain release-integration
work. No full desktop release has been
built or accepted by these fixture tests.

## Verification and selection

`verify --release DIR` checks the complete artifact and content identity.
`--host-root /` also checks OS file pins. `--loader` uses the pinned ELF loader's
`--list` mode and compares **actual resolved library paths and hashes** with the
manifest, including private libraries and declared dynamic modules. It does not
call compositor entrypoints. Use loader preflight only for trusted artifacts.
Successful static/loader checks do not establish GPU, input, Python import,
portal, resource or graphical-session acceptance.

`stage --store DIR --release ARTIFACT --trusted-generation SHA256` adds an
immutable-file generation and records a candidate. Obtain the trusted digest
from a separately verified channel. Stage does not change the selected release.
`status --store DIR` returns the revision needed by state-changing commands.

`select --store DIR --generation SHA256 --revision N --config-version V` validates
the current host and loader and changes the **next session** selection. A
`--host-root` override is for fixture/offline checks, not a chroot or loader
override: live loader preflight still uses the actual host. An older revision,
revoked release, incompatible config version, missing or changed OS pin fails.

Selection, previous and confirmed references live in one fsynced JSON record,
updated under a process lock with atomic replace. Rollback swaps selected and
previous in that same record. A failed pre-replace write leaves the previous
state readable. Orphan `.staging-*` directories are not candidates or GC input.

`confirm` requires the selected generation, exact selection revision, matching
profile digest, current boot ID and a `physical` evidence boundary. The checks
`compositor`, `shell`, `ipc`, `output`, `input`, `lock`, `resume`, `portal` must all
be JSON `true`. It rechecks current host bytes and loader resolution. Evidence
is supplied by an external collector/operator; this CLI does not manufacture
physical test results or cryptographically attest them.

`revoke` prevents new selection/session admission, while leaving already running
processes and their files intact. `gc` retains selected/previous/confirmed/
candidate generations and any process-held shared lease. GC takes an exclusive
lease before deletion. Leases inherited by child processes keep the generation
alive after the original supervisor exits. Programs that deliberately close
all inherited descriptors and detach must be contained by a future installed
session/cgroup adapter; this is not a general daemon-discovery mechanism.

## Sessions and application boundaries

`run --store DIR --component compositor --config-version V` resolves the
selection once, holds its lease, and executes its entry. `--generation SHA256`
is the pinned path for restarting another component. A shell launched through
the release autostart receives a quoted command naming this generation and its
bundled dispatcher/Python. `--restart-limit` is bounded at five failures.
The shell supervisor watches the owning session's PID and start time and stops
when the owner disappears. Signal handling forwards to the child process group
and bounds termination before escalation to SIGKILL.

External application environments discard private loader/Python/GI/plugin search
paths and use a system PATH. Shell startup moves GI paths into the in-process
repository and removes Python/GI environment overrides. Internal capture/profile
workers reconstruct declared private paths through `internal_python_environment`.
The v2 shell resolves `hyprctl` from the verified release control entry. The
release autostart imports only named display variables into D-Bus/systemd and
does not start the legacy fixed-source shell target.

`config-copy` creates a separate, fsynced settings tree for a generation and
refuses to overwrite it. The shared source is untouched. It does **not** invent
schema migrations: callers must implement and test the transition, or keep the
same config version. Public session registration, polkit/portal/service units,
desktop-file activation and session/cgroup deployment need an installed adapter;
the development CLI is not that installation.

## Managed OS admission boundary

`os-describe`, `os-verify` and `os-admit` bind an inactive root tree (including
package DB, modules and OS identity), separate boot assets (kernel, initramfs,
entry), DE generation and profile digest. Any byte/mode/symlink change produces
a different deployment. The live `/` and `/boot` are refused as candidate roots.
VM evidence can admit a `trial`; `confirmed` additionally requires physical
evidence for the same deployment. Required checks include session functions,
rollback and interrupted update. Admissions label evidence `external_report`
and boot activation `NOT_RUN`.

These are content/admission contracts. They do **not** update an inactive OS,
prove that an entry boots the claimed root, install boot entries, implement trial
boot counters, choose a firmware fallback, or switch/roll back the running OS.
Those operations require a tested host adapter for the chosen boot/root layout.
Until that exists and passes VM plus physical tests, this code does not provide
the design's strong “system updates cannot alter the running desktop” guarantee.
The existing kernel-only PostTransaction observer is unchanged.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests compile and run actual transitive ELF fixtures, remove their build root,
inject provider loss/tampering/profile drift, exercise selection CAS and failed
atomic writes, hold leases through child lifetime, stop session-owned workers,
run the packaging CLI and Lua autostart, and verify OS admission/config isolation.
The real Bubblewrap test needs user/network namespaces; a restricted outer
sandbox may skip it. These fixtures are not a packaged Luminophore desktop or a
physical boot test.

Loader/tool reference: [Bubblewrap](https://github.com/containers/bubblewrap/blob/main/bwrap.xml),
[patchelf](https://github.com/NixOS/patchelf/blob/master/patchelf.1).

## Explicit provider classification

`classify-providers --sysroot ROOT --policy POLICY.json` returns the provider
map consumed by the desktop input. The policy contains `candidates` (SONAME to
root-relative file), `system` (OS SONAME seeds), and `required_private` (SONAMEs
that must stay private). Seeds and private requirements are arrays of strings.
The command expands every OS seed's ELF dependencies and interpreter into the
OS set. A dependency on a required-private SONAME fails instead of silently
changing ownership. Missing providers and SONAME mismatches also fail.

Select OS seeds from the supported graphics/loader/service package policy;
include dynamically loaded OS driver modules explicitly. This command does not
discover host libraries or establish GPU compatibility. Private ELF entry/module
closure and typelib `dlopen` providers must still be included in the desktop
input. Artifact packing rewrites per-file RUNPATH; no global library path is
needed. Run loader and actual Python/GI import probes after relocation, since
valid ELF metadata alone cannot establish Python extension compatibility or
resource discovery.

`profiles/cachyos-providers-2026-09-20.json` records the candidates and OS
seeds for the pinned CachyOS toolchain plus Python additions. Its candidate
paths refer to the merged input root, before private-prefix relocation. It is
an input policy, not a hardware support declaration or a minimal package list.

`profiles/patchelf-0.19.1.lock.json` pins the relocation tool archive and
executable. ELF `--list` success does not execute native initializers; validate
actual imports/dlopen in a sandbox too. Preserve OS SONAME aliases as explicit
system file pins when constructing a minimal OS test root. Do not invent aliases
for a different ABI. OpenCV may modify its process-local `LD_LIBRARY_PATH`;
check the launched child environment as well as the original process input.

## Independent CachyOS package

The Arch package template lives in
`../../compositor/luminophore/packaging/arch/PKGBUILD.in`. It declares no
Hyprland `provides`, `conflicts`, or `replaces`. Its public commands are
`luminophore-compositor`, `luminophore-session`, `luminophorectl`, and
`luminophore-shell`. The login entry is `luminophore.desktop`; portal identity is
`org.freedesktop.impl.portal.desktop.luminophore`.

Prepare a concrete package from an already verified whole-desktop generation:

```sh
PYTHONPATH=. python3 -B -m luminophore_runtime.packaging \
  --release /build/releases/GENERATION --version 0.1.0rc1 --output /build/package
cd /build/package
makepkg
```

The version is explicit and must increase according to pacman version ordering;
a content hash is a generation identity, not a package version. Do not strip,
purge, recompress documentation, or otherwise rewrite release members during
packaging. Their hashes must survive package extraction. Relocation requires
`patchelf >= 0.19.1`; its version and digest are embedded in the artifact.
The current distro ELF inputs exposed a dynamic-loader crash with 0.17.2 even
though loader listing passed. The process probes are therefore mandatory.

Package activation copies the immutable release into root-owned
`/var/lib/luminophore/generations`, validates the host and loader, and atomically
selects it. It does not switch the display manager, restart the desktop, or
remove user configuration. An incompatible first activation leaves an empty
manageable store; an incompatible upgrade preserves the prior selection.
Session children hold read-only flock leases. Package removal is refused while
a lease is active, and new sessions are excluded during an admitted removal.
Retained generations and user settings survive package removal.

The small management launcher uses the host Python standard library; the shell,
its Python modules, GTK/GIO resources, Qt picker, and compositor dependencies use
the release payload. PolicyKit's authentication agent, `grim`, the generic
portal, PipeWire, logind, kernel and GPU stack remain declared system boundaries.
The private portal build requires a hash-locked `portal-upstream` directory and
`release-resources` containing the fallback font and Qt Wayland plugins. Product
Lua defaults are taken from `defaults/hyprland.lua` and the locked config source;
personal output names and a developer checkout path are not startup defaults.

## Update contract and acceptance boundaries

Luminophore does not install a global ALPM update-blocking hook. CachyOS package
transactions, including unrelated systemd services, proceed under pacman's
normal ownership and conflict rules. The compositor and shell remain separate
from the system Hyprland session through their package names, executable paths,
configuration root, systemd units, session entry, runtime directory and private
release dependencies.

Kernel, GPU, login, portal and other shared-system changes can still affect a
desktop session. When requested before an update, compatibility is reviewed
against the proposed CachyOS transaction and then validated after the update.
This review is advisory and explicit; it never blocks an unrelated package
transaction automatically. DE rollback does not roll back the kernel or GPU.

User settings live under the independent Luminophore config directory. Existing
files are preserved. Schema transitions require explicit migration and are
refused instead of using the incoming manifest's version as proof of the user's
config version. Generic portal processes are refreshed after importing the
Luminophore display environment, since they cache desktop/backend selection.
Same-user simultaneous graphical sessions are not supported by the lifecycle.

Useful verification entrypoints:

- `python3 -B -m unittest discover -s tests` from this runtime directory.
- `scripts/probe-release.py RELEASE` **inside a matching isolated host root**:
  ELF loading, private GTK/GIO TLS, portal version, Lua config, and offscreen Qt
  initialization. It reports session and physical acceptance as `NOT_RUN`.
- `tests/vm-pacman-coexist.sh` inside a disposable QEMU root booted with
  `luminophore_test=1`: both installation orders, package version upgrades,
  independent removal, immutable Hyprland files, and pre-unpack rejection of a
  changed shared-library fixture. The script intentionally uses an empty test
  pacman database and `--nodeps` twice; it tests transaction/file ownership and
  hooks, not clean-distribution dependency completeness or a graphical login.
- `tests/vm-desktop-package.sh` repeats the two installation orders and shared
  library refusal with the actual whole-DE package, and executes the installed
  private runtime probes. Copy `tests/vm-check-desktop.py` to
  `/fixtures/check-desktop.py`, `scripts/probe-release.py` to
  `/fixtures/probe-release.py`, and prepare matching
  signed-package system inputs in the disposable image. Before booting, run
  `ldconfig` within that root and recheck its profile: overlaying an older
  package without removing obsolete versioned library files can cause
  `ldconfig` to select the wrong version. This script also uses `--nodeps` twice
  and does not establish a graphical session or repository dependency closure.

A local build receipt is not an independent reproducibility attestation. DM
login, screen sharing, input, monitor hotplug, lock/resume, and physical GPU
acceptance must be collected separately before deploying a new desktop release.
