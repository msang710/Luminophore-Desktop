from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LUMINOPHORE = ROOT / "luminophore"
SESSION = LUMINOPHORE / "scripts" / "luminophore-compositor-session"
VERIFIER = LUMINOPHORE / "scripts" / "verify-spatial-transition"
SUITE = LUMINOPHORE / "scripts" / "run-spatial-canary-suite"
STAGER = LUMINOPHORE / "scripts" / "stage-runtime-bundle"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_artifact(parent: Path, label: str) -> Path:
    temporary = parent / f"source-{label}"
    binary_dir = temporary / "bin"
    binary_dir.mkdir(parents=True)
    (binary_dir / "Hyprland").write_text("#!/bin/sh\necho compositor\n")
    (binary_dir / "hyprctl").write_text("#!/bin/sh\necho control\n")
    (binary_dir / "start-hyprland").write_text(
        "#!/bin/sh\nprintf 'watchdog=%s\\nroot=%s\\nbloom=%s\\npath=%s\\nargs=%s\\n' \"$0\" \"$LUMINOPHORE_COMPOSITOR_ROOT\" \"$LUMINOPHORE_SHELL_BLOOM\" \"$PATH\" \"$*\"\n"
    )
    for path in binary_dir.iterdir():
        path.chmod(0o755)
    binaries = {name: {"sha256": digest(binary_dir / name)} for name in ("Hyprland", "hyprctl", "start-hyprland")}
    seed = label + "".join(value["sha256"] for value in binaries.values())
    generation = hashlib.sha256(seed.encode()).hexdigest()
    artifact = parent / generation
    temporary.rename(artifact)
    (artifact / "build-manifest.json").write_text(json.dumps({
        "schema": "luminophore-runtime-artifact/v1",
        "generation": generation,
        "source_commit": label * 8,
        "luminophore_effects": True,
        "acceptance": {
            "spatial_contract": "luminophore-spatial-evidence/v2",
            "audit_read_only": True,
            "exercise_opt_in": True,
        },
        "binaries": binaries,
        "runtime_files": {
            "luminophore-compositor-session": {"sha256": digest(SESSION)},
        },
    }))
    session = artifact / "bin" / "luminophore-compositor-session"
    session.write_bytes(SESSION.read_bytes())
    session.chmod(0o755)
    canary = artifact / "bin" / "luminophore-hyprland-canary"
    canary.write_text("#!/bin/sh\nexit 0\n")
    canary.chmod(0o755)
    verifier = artifact / "bin" / "verify-spatial-transition"
    verifier.write_bytes(VERIFIER.read_bytes())
    verifier.chmod(0o755)
    suite = artifact / "bin" / "run-spatial-canary-suite"
    suite.write_bytes(SUITE.read_bytes())
    suite.chmod(0o755)
    manifest = json.loads((artifact / "build-manifest.json").read_text())
    manifest["runtime_files"]["luminophore-hyprland-canary"] = {"sha256": digest(canary)}
    manifest["runtime_files"]["verify-spatial-transition"] = {"sha256": digest(verifier)}
    manifest["runtime_files"]["run-spatial-canary-suite"] = {"sha256": digest(suite)}
    (artifact / "build-manifest.json").write_text(json.dumps(manifest))
    return artifact


class RuntimeBundleTests(unittest.TestCase):
    def test_stager_preflight_verifies_candidate_and_recovery_generations_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            first = make_artifact(artifacts, "m")
            second = make_artifact(artifacts, "n")
            candidate = make_artifact(artifacts, "o")
            destination = root / "runtime"
            subprocess.run([STAGER, "install", first, destination], check=True)
            subprocess.run([STAGER, "install", second, destination], check=True)
            before = (os.readlink(destination / "current"), os.readlink(destination / "previous"))
            result = subprocess.run([STAGER, "preflight", candidate, destination], check=True, text=True, capture_output=True)
            evidence = json.loads(result.stdout)
            self.assertEqual(evidence["schema"], "luminophore-spatial-activation-preflight/v1")
            self.assertEqual(evidence["candidate"], candidate.name)
            self.assertTrue(evidence["ready"])
            self.assertEqual(before, (os.readlink(destination / "current"), os.readlink(destination / "previous")))

    def test_stager_rejects_candidate_without_v2_spatial_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = make_artifact(root, "p")
            manifest_path = artifact / "build-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            del manifest["acceptance"]
            manifest_path.write_text(json.dumps(manifest))
            result = subprocess.run([STAGER, "install", artifact, root / "runtime"], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("luminophore-spatial-evidence/v2", result.stderr)

    def test_session_verifies_and_uses_colocated_watchdog_with_poisoned_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = make_artifact(root, "a")
            poison = root / "poison"
            poison.mkdir()
            for name in ("Hyprland", "hyprctl", "start-hyprland"):
                path = poison / name
                path.write_text("#!/bin/sh\nexit 99\n")
                path.chmod(0o755)
            verified = subprocess.run([artifact / "bin/luminophore-compositor-session", "--verify-only"], check=True, text=True, capture_output=True)
            self.assertIn("LUMINOPHORE_RUNTIME_OK", verified.stdout)
            env = {**os.environ, "PATH": f"{poison}:{os.environ['PATH']}"}
            launched = subprocess.run([artifact / "bin/luminophore-compositor-session", "--demo"], check=True, text=True, capture_output=True, env=env)
            self.assertIn(f"watchdog={artifact}/bin/start-hyprland", launched.stdout)
            self.assertIn(f"root={artifact}", launched.stdout)
            self.assertIn("bloom=1", launched.stdout)
            self.assertIn(f"path={artifact}/bin:", launched.stdout)
            self.assertIn(f"--path {artifact}/bin/Hyprland -- --config", launched.stdout)
            self.assertIn("/hypr/hyprland.lua", launched.stdout)
            self.assertTrue(launched.stdout.rstrip().endswith("--demo"))

    def test_session_disables_shell_bloom_for_effects_off_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = make_artifact(Path(directory), "i")
            manifest_path = artifact / "build-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["luminophore_effects"] = False
            manifest_path.write_text(json.dumps(manifest))
            launched = subprocess.run([artifact / "bin/luminophore-compositor-session", "--demo"], check=True, text=True, capture_output=True)
            self.assertIn("bloom=0", launched.stdout)

    def test_session_rejects_missing_effects_ownership_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = make_artifact(Path(directory), "j")
            manifest_path = artifact / "build-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            del manifest["luminophore_effects"]
            manifest_path.write_text(json.dumps(manifest))
            result = subprocess.run([artifact / "bin/luminophore-compositor-session", "--verify-only"], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid luminophore_effects flag", result.stderr)

    def test_session_rejects_tampered_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = make_artifact(Path(directory), "b")
            (artifact / "bin/hyprctl").write_text("tampered")
            result = subprocess.run([artifact / "bin/luminophore-compositor-session", "--verify-only"], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest mismatch for hyprctl", result.stderr)

    def test_session_rejects_tampered_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = make_artifact(Path(directory), "g")
            session = artifact / "bin/luminophore-compositor-session"
            session.write_text(session.read_text() + "\n# tampered\n")
            result = subprocess.run([session, "--verify-only"], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest mismatch for luminophore-compositor-session", result.stderr)

    def test_session_rejects_tampered_spatial_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = make_artifact(Path(directory), "k")
            verifier = artifact / "bin/verify-spatial-transition"
            verifier.write_text(verifier.read_text() + "\n# tampered\n")
            result = subprocess.run([artifact / "bin/luminophore-compositor-session", "--verify-only"], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest mismatch for verify-spatial-transition", result.stderr)

    def test_session_rejects_tampered_spatial_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = make_artifact(Path(directory), "l")
            suite = artifact / "bin/run-spatial-canary-suite"
            suite.write_text(suite.read_text() + "\n# tampered\n")
            result = subprocess.run([artifact / "bin/luminophore-compositor-session", "--verify-only"], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest mismatch for run-spatial-canary-suite", result.stderr)

    def test_stager_rejects_corrupt_existing_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            artifact = make_artifact(artifacts, "h")
            destination = root / "runtime"
            subprocess.run([STAGER, "install", artifact, destination], check=True)
            installed = destination / "generations" / artifact.name / "bin" / "hyprctl"
            installed.chmod(0o755)
            installed.write_text("corrupt")
            result = subprocess.run([STAGER, "install", artifact, destination], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest mismatch for hyprctl", result.stderr)

    def test_stager_selects_and_rolls_back_complete_generations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            first = make_artifact(artifacts, "c")
            second = make_artifact(artifacts, "d")
            destination = root / "runtime"
            subprocess.run([STAGER, "install", first, destination], check=True)
            self.assertEqual(os.readlink(destination / "current"), f"generations/{first.name}")
            subprocess.run([STAGER, "install", second, destination], check=True)
            self.assertEqual(os.readlink(destination / "current"), f"generations/{second.name}")
            self.assertEqual(os.readlink(destination / "previous"), f"generations/{first.name}")
            subprocess.run([STAGER, "rollback", destination], check=True)
            self.assertEqual(os.readlink(destination / "current"), f"generations/{first.name}")
            self.assertEqual(os.readlink(destination / "previous"), f"generations/{second.name}")

    def test_stager_does_not_replace_current_with_invalid_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            valid = make_artifact(artifacts, "e")
            invalid = make_artifact(artifacts, "f")
            destination = root / "runtime"
            subprocess.run([STAGER, "install", valid, destination], check=True)
            (invalid / "bin/Hyprland").write_text("broken")
            result = subprocess.run([STAGER, "install", invalid, destination], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(os.readlink(destination / "current"), f"generations/{valid.name}")


if __name__ == "__main__":
    unittest.main()
