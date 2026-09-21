#!/usr/bin/bash
# Disposable QEMU root only; never invoke on the host.
set -euo pipefail
[[ $(< /proc/cmdline) == *luminophore_test=1* ]] || exit 90
finish() { echo "COEXIST_VM_RESULT=$?"; /usr/bin/sync; /usr/bin/busybox poweroff -f; }
trap finish EXIT
mkdir -p /var/lib/pacman/local /var/cache/pacman/pkg /var/log /var/lib
cat > /etc/pacman.conf <<'CONF'
[options]
Architecture = auto
SigLevel = Never
LocalFileSigLevel = Never
CONF
pm() { /usr/bin/pacman --noconfirm --nodeps --nodeps "$@"; }
pm -U --dbonly /fixtures/shared-system-fixture-1.pkg.tar
pm -U /fixtures/hyprland.pkg.tar.zst
sha256sum /usr/bin/Hyprland /usr/bin/hyprctl > /fixtures/hyprland.sha
pm -U /fixtures/luminophore.pkg.tar.zst
sha256sum -c /fixtures/hyprland.sha
[[ -f /usr/share/wayland-sessions/hyprland.desktop ]]
[[ -f /usr/share/wayland-sessions/luminophore.desktop ]]
[[ -f /var/lib/luminophore/state.json ]]
/usr/bin/python3 -I /fixtures/check-store.py
# Real libalpm pretransaction rejection must happen before libc is changed.
sha256sum /usr/lib/libc.so.6 > /fixtures/libc.sha
if pm -U /fixtures/shared-system-fixture-2.pkg.tar > /fixtures/blocked.log 2>&1; then
    cat /fixtures/blocked.log; exit 21
fi
cat /fixtures/blocked.log
grep -q 'update changes the supported Luminophore system boundary' /fixtures/blocked.log
sha256sum -c /fixtures/libc.sha
# A real package version upgrade preserves the selected generation.
pm -U /fixtures/luminophore-upgrade.pkg.tar.zst
pm -R luminophore-compositor
sha256sum -c /fixtures/hyprland.sha
[[ -d /var/lib/luminophore/generations ]]
pm -R hyprland
# Opposite install order and Hyprland reinstall/removal.
pm -U /fixtures/luminophore.pkg.tar.zst
pm -U /fixtures/hyprland.pkg.tar.zst
pm -U /fixtures/hyprland-upgrade.pkg.tar.zst
pm -R hyprland
[[ -x /usr/bin/luminophore-session ]]
[[ -f /usr/share/wayland-sessions/luminophore.desktop ]]
/usr/bin/python3 -I /fixtures/check-store.py
pm -R luminophore-compositor
echo COEXIST_PACKAGE_MATRIX_PASS
