from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from luminophore_shell.settings_acceptance import (
    collect_acceptance,
    load_evidence,
    report,
    surface_summary,
)


class SettingsAcceptanceTests(unittest.TestCase):
    def _source_fixture(self, root: Path, desktop: str = "Exec=@LUMINOPHORE_SETTINGS_EXEC@ settings\n") -> None:
        for relative in (
            "luminophore-shell",
            "luminophore_shell/settings_app.py",
            "luminophore_shell/settings_service.py",
            "luminophore_shell/appearance_service.py",
            "luminophore_shell/system_theme.py",
            "luminophore_shell/settings_generation.py",
            "scripts/deploy-luminophore-shell",
            "scripts/rollback-luminophore-shell",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        path = root / "desktop/io.github.msang710.LuminophoreSettings.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop, encoding="utf-8")

    def test_default_report_keeps_evidence_surfaces_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._source_fixture(root)
            checks = collect_acceptance(root)
        self.assertEqual(
            surface_summary(checks),
            {"source": "PASS", "package": "NOT_RUN", "gui": "EVIDENCE_PENDING", "live": "NOT_RUN"},
        )
        self.assertEqual(report(checks)["activation_gate"], "BLOCKED")

    def test_source_failure_does_not_change_unobserved_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checks = collect_acceptance(Path(temporary))
        summary = surface_summary(checks)
        self.assertEqual(summary["source"], "FAIL")
        self.assertEqual(summary["package"], "NOT_RUN")
        self.assertEqual(summary["live"], "NOT_RUN")

    def test_hardcoded_home_fails_source_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._source_fixture(root, "Exec=/home/example/luminophore-shell settings\n")
            check = collect_acceptance(root)[0]
        self.assertEqual(check.status, "FAIL")
        self.assertIn("desktop-entry-contains-user-home", check.detail)

    def test_observation_requires_proof_for_pass_or_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text(json.dumps({"package-install": {"status": "PASS"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-empty evidence"):
                load_evidence(path)

    def test_external_observation_can_update_only_known_non_source_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._source_fixture(root)
            checks = collect_acceptance(
                root,
                {"gui-launch-and-deep-links": ("PASS", "operator log 2026-08-31")},
            )
            indexed = {check.id: check for check in checks}
            self.assertEqual(indexed["gui-launch-and-deep-links"].status, "PASS")
            self.assertEqual(indexed["gui-launch-and-deep-links"].evidence, "operator log 2026-08-31")
            with self.assertRaisesRegex(ValueError, "non-overridable"):
                collect_acceptance(root, {"source-settings-contract": ("PASS", "claim")})


if __name__ == "__main__":
    unittest.main()
