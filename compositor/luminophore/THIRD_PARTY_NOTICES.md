# Third-party notices

LUMINOPHORE Compositor is based on Hyprland. The upstream source is pinned in
`upstream.lock`; its license is preserved in the repository root `LICENSE`.

The build also links against system libraries and the exact versions are
captured in each generated artifact `build-manifest.json`. Submodule source and
license files remain in their original submodule directories.

This PR does not install or activate the compositor and does not replace the
distribution-owned Hyprland package.

The runtime artifact owns `Hyprland`, `hyprctl`, and `start-hyprland`. System
shared libraries, Xwayland, and xdg-desktop-portal-hyprland remain declared
runtime dependencies and are not copied into the artifact.
