from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Iterable, Literal, Sequence


Status = Literal["PASS", "MISSING", "WARN", "NOT_RUN"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    required: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "required": self.required,
        }


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


CAPABILITY_SCHEMA = "luminophore-desktop-capabilities/v1"
INSTALLED_CAPABILITY_MANIFEST = Path("/usr/share/luminophore/desktop-capabilities.json")
SOURCE_CAPABILITY_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "Luminophore-OS/runtime/profiles/desktop-capabilities.json"
)

FORBIDDEN_EXECUTABLE_ACTIONS = {
    "enable",
    "disable",
    "start",
    "stop",
    "restart",
    "reboot",
    "poweroff",
    "suspend",
    "hibernate",
}


def safe_run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    if not argv:
        raise ValueError("empty argv")
    if any(part in FORBIDDEN_EXECUTABLE_ACTIONS for part in argv[1:]):
        raise ValueError(f"preflight refused state-changing argv: {tuple(argv)!r}")
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)


def _capability_manifest_path() -> Path:
    override = os.environ.get("LUMINOPHORE_CAPABILITY_MANIFEST")
    if override:
        return Path(override)
    return INSTALLED_CAPABILITY_MANIFEST if INSTALLED_CAPABILITY_MANIFEST.is_file() else SOURCE_CAPABILITY_MANIFEST


def command_contract(path: Path | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    source = Path(path) if path is not None else _capability_manifest_path()
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid capability manifest: {source}") from exc
    if manifest.get("schema") != CAPABILITY_SCHEMA or not isinstance(manifest.get("capabilities"), list):
        raise ValueError(f"invalid capability manifest: {source}")
    required, optional = set(), set()
    for row in manifest["capabilities"]:
        if not isinstance(row, dict) or row.get("kind") != "command" or row.get("ownership") == "private-release":
            continue
        names = row.get("names")
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError(f"invalid command capability: {source}")
        (required if row.get("requirement") == "required" else optional).update(names)
    return tuple(sorted(required)), tuple(sorted(optional))


def _command_checks(which: Which, root: Path) -> list[CheckResult]:
    try:
        required_commands, optional_commands = command_contract()
    except ValueError as exc:
        return [CheckResult("capability-manifest", "MISSING", str(exc))]
    results = [
        CheckResult(f"command:{name}", "PASS" if which(name) else "MISSING", which(name) or "not found")
        for name in required_commands
    ]
    optional_found = {name: which(name) for name in optional_commands}
    results.extend(
        CheckResult(
            f"optional-command:{name}",
            "PASS" if path else "WARN",
            path or "not installed; required only when this provider is enabled",
            required=False,
        )
        for name, path in optional_found.items()
    )
    geoclue = root / "usr/share/dbus-1/system-services/org.freedesktop.GeoClue2.service"
    results.append(CheckResult("geoclue-service", "PASS" if geoclue.is_file() else "MISSING", str(geoclue)))
    gnome_polkit = root / "usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1"
    polkit = [path for name, path in optional_found.items() if "polkit" in name and path]
    if gnome_polkit.is_file():
        polkit.append(str(gnome_polkit))
    results.append(
        CheckResult(
            "polkit-agent",
            "PASS" if len(polkit) == 1 else "MISSING" if not polkit else "WARN",
            "exactly one standalone agent found" if len(polkit) == 1 else f"standalone agents found: {len(polkit)}",
        )
    )
    return results


def _greetd_check(root: Path) -> CheckResult:
    preferred = root / "etc/greetd/greetd.conf"
    path = preferred if preferred.is_file() else root / "etc/greetd/config.toml"
    if not path.is_file():
        return CheckResult("greetd-config", "MISSING", str(path))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return CheckResult(
            "greetd-config",
            "NOT_RUN",
            f"reason=PERMISSION_DENIED; run the read-only preflight as root to inspect {path}",
        )
    if "start-hyprland" in text and "/etc/luminophore-shell/greeter-hyprland.lua" in text:
        return CheckResult("greetd-config", "PASS", f"first-party Luminophore GTK4 Greeter configured in {path}")
    if "start-hyprland" in text and "/etc/luminophore-shell/greeter-hyprland.conf" in text:
        return CheckResult("greetd-config", "WARN", "legacy Hyprland Greeter parser; Lua migration required")
    if "start-hyprland" in text and "/etc/nwg-hello/luminophore-hyprland.conf" in text:
        return CheckResult("greetd-config", "WARN", "legacy nwg-hello Greeter generation; migration required")
    if 'command = "/usr/bin/nwg-hello' in text:
        return CheckResult("greetd-config", "WARN", "nwg-hello is configured without a Wayland compositor")
    return CheckResult("greetd-config", "WARN", "non-target greeter command; migration required")


def _recovery_checks(root: Path) -> list[CheckResult]:
    passwd = root / "etc/passwd"
    shells = root / "etc/shells"
    tty_login = passwd.is_file() and shells.is_file()
    return [
        CheckResult(
            "tty-recovery-contract",
            "PASS" if tty_login else "MISSING",
            "passwd and shells databases are readable" if tty_login else "cannot establish local TTY login prerequisites",
        ),
        CheckResult(
            "ssh-recovery",
            "PASS" if (root / "usr/bin/ssh").exists() or (root / "usr/bin/sshd").exists() else "WARN",
            "SSH binary present" if (root / "usr/bin/ssh").exists() or (root / "usr/bin/sshd").exists() else "SSH recovery not established",
            required=False,
        ),
    ]


def _firmware_check(runner: Runner, which: Which) -> CheckResult:
    systemctl = which("systemctl")
    if not systemctl:
        return CheckResult("firmware-setup-capability", "MISSING", "systemctl not found")
    result = runner((systemctl, "--help"))
    supported = "--firmware-setup" in (result.stdout + result.stderr)
    return CheckResult(
        "firmware-setup-capability",
        "PASS" if supported else "WARN",
        "systemctl exposes --firmware-setup" if supported else "systemctl help lacks --firmware-setup",
    )


def _gtk4_layer_shell_check(root: Path) -> CheckResult:
    candidates = tuple((root / "usr/lib/girepository-1.0").glob("Gtk4LayerShell-1.0.typelib"))
    return CheckResult(
        "gtk4-layer-shell-typelib",
        "PASS" if len(candidates) == 1 else "MISSING",
        str(candidates[0]) if len(candidates) == 1 else "Gtk4LayerShell-1.0.typelib not found",
    )


def _greeter_power_authority_check() -> CheckResult:
    return CheckResult(
        "greeter-login1-authority",
        "NOT_RUN",
        "reason=GREETER_SEAT_REQUIRED; verify CanReboot, CanPowerOff and CanRebootToFirmwareSetup in G5C1",
    )


def _ddc_check(root: Path) -> CheckResult:
    buses = sorted((root / "dev").glob("i2c-*")) if (root / "dev").exists() else []
    if not buses:
        return CheckResult(
            "ddc-live",
            "NOT_RUN",
            "reason=PHYSICAL_ENVIRONMENT_UNAVAILABLE; no /dev/i2c-* visible",
        )
    return CheckResult("ddc-live", "WARN", f"{len(buses)} I2C buses visible; live get/set/restore requires approval")


def collect_checks(
    *, root: Path = Path("/"), which: Which = shutil.which, runner: Runner = safe_run
) -> list[CheckResult]:
    results = _command_checks(which, root)
    results.append(_greetd_check(root))
    results.extend(_recovery_checks(root))
    results.append(_gtk4_layer_shell_check(root))
    results.append(_firmware_check(runner, which))
    results.append(_greeter_power_authority_check())
    results.append(_ddc_check(root))
    return results


def exit_code(results: Iterable[CheckResult]) -> int:
    return 1 if any(item.required and item.status == "MISSING" for item in results) else 0


def render_text(results: Iterable[CheckResult]) -> str:
    return "\n".join(f"{item.status:<7} {item.name}: {item.detail}" for item in results)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-session-stack")
    parser.add_argument("--root", type=Path, default=Path("/"), help="fixture or live filesystem root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = collect_checks(root=args.root)
    print(json.dumps([item.as_dict() for item in results], ensure_ascii=False, indent=2) if args.json else render_text(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
