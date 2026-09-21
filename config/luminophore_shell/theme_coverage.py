from __future__ import annotations

import json
from pathlib import Path


REQUIRED_THEME_TARGET_IDS = frozenset({
    "gtk3", "gtk4", "qt5", "qt6", "kde", "kitty", "alacritty", "btop",
    "ghostty", "hyprland", "cursor", "gimp", "libreoffice", "obs", "discord", "vscode",
})
# Compatibility export for callers that only need the complete required set.
EXPECTED_LEGACY_TEMPLATE_IDS = REQUIRED_THEME_TARGET_IDS


def coverage_manifest(path: Path | None = None) -> dict[str, object]:
    target = path or Path(__file__).with_name("templates") / "matugen" / "coverage.json"
    value = json.loads(target.read_text(encoding="utf-8"))
    rows = value.get("targets") if isinstance(value, dict) else None
    if value.get("version") != 2 or not isinstance(rows, list):
        raise ValueError("invalid theme coverage manifest")
    ids = {row.get("id") for row in rows if isinstance(row, dict)}
    if ids != REQUIRED_THEME_TARGET_IDS or len(rows) != len(ids):
        raise ValueError("theme coverage manifest must contain every required target exactly once")
    for row in rows:
        required = {"id", "compiler_output", "activation_target", "coverage_kind", "verification"}
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("theme coverage row is incomplete")
        kind = row.get("coverage_kind")
        if kind not in {"direct", "toolkit-indirect", "intentional-drop"}:
            raise ValueError("invalid theme coverage kind")
        if kind == "intentional-drop":
            if row.get("compiler_output") is not None or row.get("activation_target") is not None or not row.get("reason"):
                raise ValueError("intentional drop must have no output or activation target and must explain why")
        elif not row.get("compiler_output") or not row.get("activation_target") or not row.get("verification"):
            raise ValueError("covered theme requires compiler, activation, and verification contracts")
        if row.get("id") == "hyprland" and row.get("activation_target") != "hyprland-settings-transaction":
            raise ValueError("hyprland must consume the semantic palette contract")
        if row.get("id") == "cursor" and row.get("activation_target") != "dual Hyprcursor/XCursor transaction":
            raise ValueError("cursor must build and activate both supported formats")
    return value
