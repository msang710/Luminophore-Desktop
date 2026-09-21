# Luminophore Desktop

[한국어](README.md) · **English**

This is a personal Hyprland rice with a GTK4-based Luminophore Shell. It analyzes
wallpapers with `matugen` to build widget palettes and uses separate apply
actions to synchronize GTK3/4, Qt5/6, KDE, Kitty, Alacritty, Btop, Ghostty,
Hyprland, and Bibata cursors.

## Features

- Hyprpaper, Awww, and Linux Wallpaper Engine wallpaper sources
- Matugen v4 semantic dark/light palettes
- Separate actions for applying the widget palette and the system theme
- Multi-monitor palettes and transition animations
- App/window/emoji launcher, encrypted clipboard, notifications, weather, and system monitoring
- Per-focused-monitor Hyprpaper/Awww wallpaper application with automatic Matugen palette refresh
- Preview, backup, drift detection, and rollback before applying system themes
- Atomic Hyprcursor/XCursor generation and rollback that recolors Bibata shapes with the Matugen semantic palette

## Requirements

Required dependencies below use Arch Linux package names.

```sh
sudo pacman -S python python-gobject python-opengl gtk4 gtk4-layer-shell python-pillow \
  python-requests python-dbus matugen hyprland greetd hyprpaper wl-clipboard uwsm
```

Install whichever desktop wallpaper provider you use. The login stack always
requires `hyprpaper` to show static monitor backgrounds independently of the
desktop provider.

- `hyprpaper`
- `awww`
- `linux-wallpaperengine`

System-theme synchronization targets are optional dependencies.

- GTK: `gsettings`, `adw-gtk3`
- Qt: `qt6ct`, `qt5ct`
- KDE: `plasma-apply-colorscheme`
- Apps: `kitty`, `alacritty`, `btop`
- Cursor: `hyprcursor`, `librsvg`, `xorg-xcursorgen`

Other helpers include `librsvg` for icon generation, `hyprpicker` for color
selection, `libcanberra` for notification sounds, and `fd` for file search.
NVIDIA GPU metrics are shown only when `nvidia-smi` is available.

Independent desktop controls use `playerctl`, `ddcutil`, `networkmanager`,
`bluez`, `tuned-cachy`, `geoclue`, `libsecret`, and `polkit-gnome`.
Ghostty selects `theme = matugen` in this installation's
`~/.config/ghostty/config.ghostty`.

Power profiles expose only the non-inheriting
`power-profiles/luminophore-*-capped` profiles and do not alter firmware-owned
CPU boost or minimum performance ratios. The installer masks
`power-profiles-daemon` strongly enough to prevent D-Bus reactivation and writes
the Luminophore balanced preset before TuneD starts so upstream defaults are not
applied. Rollback restores the pre-install mask/enable/active state. Run this
non-destructive check before switching the real backend:

```sh
./scripts/check-power-backend
```

Installing `tuned-cachy`, transferring ownership of the system service, and
writing `/etc/tuned/profiles` happen only when
`install-power-backend --apply` is explicitly run with administrative privileges.

The migration state for the existing 12 theme targets is pinned in
`luminophore_shell/templates/matugen/coverage.json`. Each item is either mapped
to a common/dedicated Matugen target or marked `intentional-drop` with a reason
when no supported safe injection API exists.

To connect Google Calendar, enable the Calendar API and OAuth consent screen in
Google Cloud, download a Desktop app OAuth client JSON, and install it with mode
`0600` at:

```sh
install -Dm600 ~/Downloads/client_secret_*.json ~/.config/luminophore-shell/google-oauth-client.json
```

No browser or Google API request is made before the connect button is pressed.
Refresh tokens and calendar caches are stored in Secret Service rather than
ordinary files.

## Installation

This rice assumes the `~/.config/hypr/config` layout.

```sh
git clone https://github.com/msang710/myrice.git ~/.config/hypr/config
cd ~/.config/hypr/config
./scripts/deploy-luminophore-shell
```

Make sure `hyprland.lua` loads the Lua configuration from the `config`
directory. Fonts, terminal, pinned apps, weather location, and per-monitor
palettes can be customized in `luminophore_shell/config.toml`.

Check service and configuration state with:

```sh
systemctl --user status luminophore-shell.service
./luminophore-shell validate-config
./luminophore-shell ctl status
```

When removing it, disable the user systemd unit with the following script.
Configuration and state data are not deleted automatically.

```sh
./scripts/rollback-luminophore-shell
```

## Using palettes

1. Select Hyprpaper, Awww, or Linux Wallpaper Engine as the palette source in Settings.
2. Use the widget-palette apply action to review Luminophore Shell colors first.
3. Use the system-theme apply action to apply the same Matugen generation to supported apps.
4. If something goes wrong, run the system-theme rollback.

Matugen state files support v2 only. If an older v1 state exists, extract a new palette.

## Login theme staging

Generate the first-party Luminophore GTK4 login theme in user state from the
current Matugen scheme and active Hyprpaper/Awww background. The screen shows
only the password field and reboot, shutdown, and firmware-setup icons; it has no
account or session selector.

```sh
./scripts/stage-session-stack
```

The emitted stage does not contain the original wallpaper path or login account.
It contains only per-monitor PNG copies, the portable Greeter Python package,
supervisor, dedicated Hyprland/Hyprpaper configuration, and a checksum manifest.
The following command validates only the stage and does not modify `/etc/greetd`.

```sh
./scripts/install-session-stack --staged <output-stage-path> --login-user <local-user> --root / --check
```

`--apply` installs `/etc/luminophore-shell`, `/usr/lib/luminophore-shell`,
and the Luminophore-owned `/etc/greetd/greetd.conf` under one rollback manifest.
Actual application, GUI preview, greetd transition, and reboot must be separate
rollouts after preparing a login recovery path.

Keeping Plymouth's last frame is also a transaction independent of Greeter
installation. To check only the current vendor unit contract:

```sh
./scripts/install-login-handoff --root / --check
```

The `--retain-splash` drop-in has been applied and read back as a transaction
with its own rollback. Real cold-boot visual and journal validation, including
Lua blur/portal corrections, remains a separate reboot gate.

### Boot health first-frame signal

The daemon arms the launcher layer surface after calling `present()`. It writes
`$XDG_RUNTIME_DIR/luminophore-shell/first-frame-ready.json` only after GDK reports
a completed frame timing with a non-zero presentation time, the public GTK/GDK
indication that the frame became visible. The private atomic signal is bound to
the current boot ID, kernel release, Shell process ID, and monotonic timestamp.
Update Guardian compares the same process identity across its stability window;
a Shell restart therefore cannot reuse an earlier signal as healthy evidence.

## Development and validation

```sh
cd ~/.config/hypr/config
python -m unittest discover -s tests
python -m compileall -q luminophore_shell tests
./luminophore-shell validate-config
```

## License

[MIT](LICENSE)

### App initial placement

In Luminophore Settings → compositor, select a running app or enter its exact
Wayland app ID / XWayland class, then choose a view-relative direction. Save and
apply affects only newly mapped regular Board windows. Choosing the default
removes the app override. Floating windows, dialogs, and PiP retain their
existing placement behavior.

Causal placement takes precedence and puts a window to the right of its source.
App rules apply only to confirmed direct launches through the Shell or packaged
app-launch shortcuts (`luminophore-shell launch -- <argv>`). Unknown origins use
the existing fallback even when an app rule exists. A one-shot compositor token
that expires after 30 seconds is passed through the UWSM unit Environment
property and consumed by the first mapped window. Missing or unreadable process
metadata falls back; it is not inferred from timing or PID. Custom shortcuts can
use the same launch command. Ordinary exec/autostart does not claim a direct user
launch.

The reference is the cursor monitor's current view when the new window appears.
The boundary's middle cell determines perpendicular alignment; an occupied cell
pushes its chain in the requested direction. WIDE keeps its existing view
preservation policy. No coordinates or permanent reservations are configured.

The generated `luminophore_placements.lua` is a single atomic settings file.
Configuration errors leave it saved with a pending-application message; the
settings application does not retry an ambiguous reload or restart the session.

For hand-written native window rules, the static effect is
`initial_placement = "right"` (`left`, `up`, `down`, and `default` are also
accepted). It never repositions an already registered window. This is a
Luminophore extension and needs separate upstream wiki documentation if proposed
upstream.

## Removal of the window-group compatibility path

Luminophore's spatial model manages every window as an individually manipulable
object. Hyprland window groups and tab bars are not supported.

- `group:*`, `general:col.nogroup_border*`, `binds:ignore_group_lock`, and `binds:movefocus_cycles_groupfirst` settings have been removed. Delete them when migrating old configuration.
- `hl.dsp.group`, group merge/split/lock dispatchers, and `group` window rules are rejected. Group commands are not automatically translated into normal window movement.
- The `grouped` property and Lua group objects have been removed from window listings.
- Bundled app launches, grouped Undo, and key press/release bundles are separate features and remain available. Browser/editor tabs inside applications are unaffected.
