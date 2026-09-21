from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence


Status = Literal["PASS", "FAIL", "NOT_RUN", "EVIDENCE_PENDING"]
Surface = Literal["source", "package", "gui", "live"]
STATUSES = frozenset(("PASS", "FAIL", "NOT_RUN", "EVIDENCE_PENDING"))
SURFACES = ("source", "package", "gui", "live")


@dataclass(frozen=True)
class AcceptanceCheck:
    id: str
    surface: Surface
    status: Status
    detail: str
    evidence: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "surface": self.surface,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def _source_check(root: Path) -> AcceptanceCheck:
    required = (
        "luminophore-shell",
        "luminophore_shell/settings_app.py",
        "luminophore_shell/settings_service.py",
        "luminophore_shell/appearance_service.py",
        "luminophore_shell/system_theme.py",
        "luminophore_shell/settings_generation.py",
        "desktop/io.github.msang710.LuminophoreSettings.desktop",
        "scripts/deploy-luminophore-shell",
        "scripts/rollback-luminophore-shell",
    )
    missing = [relative for relative in required if not (root / relative).is_file()]
    desktop = root / "desktop/io.github.msang710.LuminophoreSettings.desktop"
    desktop_text = desktop.read_text(encoding="utf-8") if desktop.is_file() else ""
    failures = [f"missing={relative}" for relative in missing]
    if "/home/" in desktop_text:
        failures.append("desktop-entry-contains-user-home")
    if desktop_text and "@LUMINOPHORE_SETTINGS_EXEC@" not in desktop_text:
        failures.append("desktop-entry-missing-install-placeholder")
    return AcceptanceCheck(
        "source-settings-contract",
        "source",
        "FAIL" if failures else "PASS",
        "; ".join(failures) if failures else "required settings sources and relocatable desktop entry are present",
        evidence="read-only repository inspection",
    )


def _unobserved_checks() -> list[AcceptanceCheck]:
    return [
        AcceptanceCheck(
            "package-install",
            "package",
            "NOT_RUN",
            "reason=INSTALL_NOT_OBSERVED; requires an approved candidate install",
        ),
        AcceptanceCheck(
            "package-rollback",
            "package",
            "NOT_RUN",
            "reason=ROLLBACK_NOT_OBSERVED; requires an approved package-pair rehearsal",
        ),
        AcceptanceCheck(
            "gui-launch-and-deep-links",
            "gui",
            "NOT_RUN",
            "reason=WAYLAND_GUI_NOT_OBSERVED; launch, single-instance and page routing require interaction",
        ),
        AcceptanceCheck(
            "gui-layout-and-errors",
            "gui",
            "NOT_RUN",
            "reason=GUI_INTERACTION_NOT_OBSERVED; scrolling, capability gates and error states require interaction",
        ),
        AcceptanceCheck(
            "gui-visual-acceptance",
            "gui",
            "EVIDENCE_PENDING",
            "reason=USER_VISUAL_ACCEPTANCE_REQUIRED",
        ),
        AcceptanceCheck(
            "live-hyprland-settings",
            "live",
            "NOT_RUN",
            "reason=LIVE_MUTATION_NOT_AUTHORIZED; preview, readback and rollback require a Hyprland session",
        ),
        AcceptanceCheck(
            "live-wallpaper-dual-monitor",
            "live",
            "NOT_RUN",
            "reason=PHYSICAL_TOPOLOGY_NOT_OBSERVED; provider transaction requires both monitors",
        ),
        AcceptanceCheck(
            "live-system-theme-targets",
            "live",
            "NOT_RUN",
            "reason=INSTALLED_TARGETS_NOT_OBSERVED; application activation and visual readback required",
        ),
        AcceptanceCheck(
            "live-restart-and-rollback",
            "live",
            "NOT_RUN",
            "reason=SERVICE_RESTART_NOT_AUTHORIZED; generation recovery requires a controlled rehearsal",
        ),
    ]


def load_evidence(path: Path | None) -> dict[str, tuple[Status, str]]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("evidence must be a JSON object keyed by check id")
    parsed: dict[str, tuple[Status, str]] = {}
    for check_id, value in raw.items():
        if not isinstance(check_id, str) or not isinstance(value, dict):
            raise ValueError("each evidence entry must be an object keyed by a string check id")
        status = value.get("status")
        evidence = value.get("evidence")
        if status not in STATUSES:
            raise ValueError(f"invalid status for {check_id}: {status!r}")
        if status in {"PASS", "FAIL"} and (not isinstance(evidence, str) or not evidence.strip()):
            raise ValueError(f"{check_id} requires non-empty evidence for {status}")
        parsed[check_id] = (status, evidence if isinstance(evidence, str) else "")
    return parsed


def collect_acceptance(
    root: Path, evidence: Mapping[str, tuple[Status, str]] | None = None
) -> list[AcceptanceCheck]:
    checks = [_source_check(root), *_unobserved_checks()]
    overrides = dict(evidence or {})
    known = {check.id for check in checks if check.surface != "source"}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(f"unknown or non-overridable check ids: {', '.join(unknown)}")
    result: list[AcceptanceCheck] = []
    for check in checks:
        override = overrides.get(check.id)
        if override is None:
            result.append(check)
            continue
        status, proof = override
        result.append(
            AcceptanceCheck(
                check.id,
                check.surface,
                status,
                "externally supplied observation; see evidence",
                proof,
            )
        )
    return result


def surface_summary(checks: Iterable[AcceptanceCheck]) -> dict[str, Status]:
    grouped: dict[str, list[Status]] = {surface: [] for surface in SURFACES}
    for check in checks:
        grouped[check.surface].append(check.status)
    priority = ("FAIL", "EVIDENCE_PENDING", "NOT_RUN", "PASS")
    return {
        surface: next(status for status in priority if status in grouped[surface])
        for surface in SURFACES
    }


def report(checks: Sequence[AcceptanceCheck]) -> dict[str, object]:
    return {
        "goal": "settings-application-separation/T-013",
        "non_destructive": True,
        "surfaces": surface_summary(checks),
        "checks": [check.as_dict() for check in checks],
        "activation_gate": "BLOCKED" if any(check.status != "PASS" for check in checks) else "READY_FOR_SEPARATE_APPROVAL",
    }


def render_text(payload: Mapping[str, object]) -> str:
    surfaces = payload["surfaces"]
    checks = payload["checks"]
    assert isinstance(surfaces, dict) and isinstance(checks, list)
    lines = [f"{str(surfaces[surface]):<16} {surface}" for surface in SURFACES]
    lines.append(f"activation_gate  {payload['activation_gate']}")
    lines.extend(
        f"{item['status']:<16} {item['surface']}/{item['id']}: {item['detail']}"
        for item in checks
        if isinstance(item, dict)
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-settings-acceptance")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--evidence", type=Path, help="read-only JSON observations; no actions are executed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = report(collect_acceptance(args.root, load_evidence(args.evidence)))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_text(payload))
    return 1 if "FAIL" in payload["surfaces"].values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
