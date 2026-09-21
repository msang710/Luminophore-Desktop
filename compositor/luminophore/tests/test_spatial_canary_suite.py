from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "luminophore" / "scripts" / "run-spatial-canary-suite"


class SpatialCanarySuiteTests(unittest.TestCase):
    def fixture(self, directory: str) -> tuple[Path, dict[str, str], Path]:
        root = Path(directory)
        binary = root / "bin"
        binary.mkdir()
        manifest = root / "build-manifest.json"
        manifest.write_text(json.dumps({"generation": "test-generation"}))
        calls = root / "calls"
        hyprctl = binary / "hyprctl"
        hyprctl.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >>\"$CALLS\"\n"
            "if [ \"$*\" = '-j activewindow' ]; then\n"
            "  printf '{\"address\":\"0x1234\"}\\n'\n"
            "fi\n"
        )
        verifier = binary / "verify-spatial-transition"
        verifier.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >>\"$CALLS\"\n"
            "if [ $# -eq 0 ]; then\n"
            "  printf '{\"presentationMode\":\"%s\",\"wideKey\":null}\\n' \"${INITIAL_MODE:-normal}\"\n"
            "elif [ \"$*\" = 'wide-toggle' ]; then\n"
            "  count=$(cat \"$WIDE_COUNT\" 2>/dev/null || printf 0)\n"
            "  count=$((count + 1))\n"
            "  printf '%s' \"$count\" >\"$WIDE_COUNT\"\n"
            "  if [ \"$count\" -eq 1 ]; then mode=${FIRST_WIDE_MODE:-wide}; else mode=${SECOND_WIDE_MODE:-normal}; fi\n"
            "  printf '{\"changed\":true,\"committed\":true,\"after\":{\"presentationMode\":\"%s\"}}\\n' \"$mode\"\n"
            "else\n"
            "  printf '{\"changed\":true,\"committed\":true,\"arguments\":\"%s\"}\\n' \"$*\"\n"
            "fi\n"
        )
        installed = binary / "run-spatial-canary-suite"
        installed.write_bytes(SUITE.read_bytes())
        for path in (hyprctl, verifier, installed):
            path.chmod(0o755)
        return installed, {**os.environ, "CALLS": str(calls), "WIDE_COUNT": str(root / "wide-count")}, calls

    def test_default_audit_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, env, calls = self.fixture(directory)
            result = subprocess.run([suite], env=env, check=True, text=True, capture_output=True)
            evidence = json.loads(result.stdout)
            self.assertEqual(evidence["schema"], "luminophore-spatial-evidence/v2")
            self.assertEqual(evidence["mode"], "audit")
            self.assertEqual(evidence["step"], "snapshot")
            self.assertEqual(calls.read_text().strip(), "")

    def test_exercise_requires_double_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, env, _ = self.fixture(directory)
            result = subprocess.run([suite, "--exercise"], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("LUMINOPHORE_CANARY_EXERCISE=1", result.stderr)

    def test_exercise_runs_fixed_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, env, calls = self.fixture(directory)
            env["LUMINOPHORE_CANARY_EXERCISE"] = "1"
            result = subprocess.run([suite, "--exercise"], env=env, check=True, text=True, capture_output=True)
            evidence = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(
                [item["step"] for item in evidence],
                [
                    "view-move-right", "view-move-left", "view-adjust-right", "view-adjust-left",
                    "window-move-right", "window-move-left", "wide-toggle-enter", "wide-toggle-leave",
                ],
            )
            self.assertEqual(len(calls.read_text().splitlines()), 11)
            self.assertIn('dispatch hl.dsp.focus({ window = "address:0x1234" })', calls.read_text())

    def test_exercise_rejects_non_normal_initial_presentation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, env, _ = self.fixture(directory)
            env.update({"LUMINOPHORE_CANARY_EXERCISE": "1", "INITIAL_MODE": "wide"})
            result = subprocess.run([suite, "--exercise"], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("initial NORMAL presentation", result.stderr)

    def test_exercise_rejects_wrong_wide_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, env, _ = self.fixture(directory)
            env.update({"LUMINOPHORE_CANARY_EXERCISE": "1", "FIRST_WIDE_MODE": "normal"})
            result = subprocess.run([suite, "--exercise"], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected wide presentation", result.stderr)


if __name__ == "__main__":
    unittest.main()
