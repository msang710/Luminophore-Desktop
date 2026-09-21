from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from luminophore_shell.power_backend import LEGACY_CLIENT, LEGACY_SERVICE, PowerBackendInstaller


class FakeSystem:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self.active = {LEGACY_SERVICE: True, "tuned.service": False}
        self.enabled = {LEGACY_SERVICE: True, "tuned.service": False}
        self.masked = {LEGACY_SERVICE: False, "tuned.service": False}
        self.legacy_profile = "balanced"
        self.tuned_profile = ""
        self.tuned_start_preset: tuple[str, str] | None = None
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[0] == "systemctl" and argv[1] in {"is-active", "is-enabled"}:
            if argv[1] == "is-enabled" and "--quiet" not in argv:
                value = "masked\n" if self.masked.get(argv[-1], False) else (
                    "enabled\n" if self.enabled.get(argv[-1], False) else "disabled\n"
                )
                return subprocess.CompletedProcess(argv, 0 if self.enabled.get(argv[-1], False) else 1, value, "")
            values = self.active if argv[1] == "is-active" else self.enabled
            return subprocess.CompletedProcess(argv, 0 if values.get(argv[-1], False) else 1, "", "")
        if argv[:3] == ("systemctl", "disable", "--now"):
            self.active[argv[-1]] = False
            self.enabled[argv[-1]] = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ("systemctl", "mask", "--now"):
            self.active[argv[-1]] = False
            self.enabled[argv[-1]] = False
            self.masked[argv[-1]] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ("systemctl", "unmask"):
            self.masked[argv[-1]] = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ("systemctl", "enable", "--now"):
            if argv[-1] == "tuned.service" and self.root is not None:
                tuned_dir = self.root / "etc/tuned"
                self.tuned_start_preset = (
                    (tuned_dir / "active_profile").read_text(encoding="utf-8"),
                    (tuned_dir / "profile_mode").read_text(encoding="utf-8"),
                )
            self.active[argv[-1]] = True
            self.enabled[argv[-1]] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ("systemctl", "start"):
            self.active[argv[-1]] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv == (LEGACY_CLIENT, "get"):
            return subprocess.CompletedProcess(argv, 0, self.legacy_profile + "\n", "")
        if argv[:2] == (LEGACY_CLIENT, "set"):
            self.legacy_profile = argv[2]
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv == ("tuned-adm", "active"):
            return subprocess.CompletedProcess(argv, 0, f"Current active profile: {self.tuned_profile}\n", "")
        if argv[:2] == ("tuned-adm", "profile"):
            self.tuned_profile = argv[2]
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv == ("tuned-adm", "verify"):
            return subprocess.CompletedProcess(argv, 0, "Verification succeeded\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


class PowerBackendTransactionTests(unittest.TestCase):
    def test_install_and_rollback_preserve_service_and_boost_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            boost = root / "sys/devices/system/cpu/cpufreq/policy0/boost"
            boost.parent.mkdir(parents=True)
            boost.write_text("0\n", encoding="utf-8")
            fake = FakeSystem(root)
            installer = PowerBackendInstaller(root, fake)

            tuned_dir = root / "etc/tuned"
            tuned_dir.mkdir(parents=True)
            active_profile = tuned_dir / "active_profile"
            profile_mode = tuned_dir / "profile_mode"
            active_profile.write_text("old-profile\n", encoding="utf-8")
            profile_mode.write_text("auto\n", encoding="utf-8")

            backup = installer.install(Path("power-profiles"))
            self.assertTrue(fake.active["tuned.service"])
            self.assertFalse(fake.active[LEGACY_SERVICE])
            self.assertTrue(fake.masked[LEGACY_SERVICE])
            self.assertEqual(fake.tuned_profile, "luminophore-balanced-capped")
            self.assertEqual(boost.read_text(encoding="utf-8"), "0\n")
            self.assertEqual(active_profile.read_text(encoding="utf-8"), "luminophore-balanced-capped\n")
            self.assertEqual(profile_mode.read_text(encoding="utf-8"), "manual\n")
            self.assertEqual(fake.tuned_start_preset, ("luminophore-balanced-capped\n", "manual\n"))
            for name in ("luminophore-eco-capped", "luminophore-balanced-capped", "luminophore-gaming-capped"):
                self.assertTrue((root / "etc/tuned/profiles" / name / "tuned.conf").is_file())

            installer.rollback(backup.path)
            self.assertTrue(fake.active[LEGACY_SERVICE])
            self.assertTrue(fake.enabled[LEGACY_SERVICE])
            self.assertFalse(fake.masked[LEGACY_SERVICE])
            self.assertFalse(fake.active["tuned.service"])
            self.assertEqual(fake.legacy_profile, "balanced")
            self.assertEqual(boost.read_text(encoding="utf-8"), "0\n")
            self.assertEqual(active_profile.read_text(encoding="utf-8"), "old-profile\n")
            self.assertEqual(profile_mode.read_text(encoding="utf-8"), "auto\n")
            self.assertFalse((root / "etc/tuned/profiles/luminophore-balanced-capped").exists())

    def test_preexisting_legacy_mask_is_preserved_by_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSystem(root)
            fake.active[LEGACY_SERVICE] = False
            fake.enabled[LEGACY_SERVICE] = False
            fake.masked[LEGACY_SERVICE] = True

            backup = PowerBackendInstaller(root, fake).install(Path("power-profiles"))
            PowerBackendInstaller(root, fake).rollback(backup.path)

            self.assertTrue(fake.masked[LEGACY_SERVICE])
            self.assertFalse(fake.active[LEGACY_SERVICE])

    def test_existing_profile_directory_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            existing = root / "etc/tuned/profiles/luminophore-balanced-capped"
            existing.mkdir(parents=True)
            (existing / "tuned.conf").write_text("old\n", encoding="utf-8")
            fake = FakeSystem(root)
            backup = PowerBackendInstaller(root, fake).install(Path("power-profiles"))
            PowerBackendInstaller(root, fake).rollback(backup.path)
            self.assertEqual((existing / "tuned.conf").read_text(encoding="utf-8"), "old\n")

    def test_transaction_never_emits_direct_cpu_or_boost_write_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSystem(root)
            PowerBackendInstaller(root, fake).install(Path("power-profiles"))
            flattened = "\n".join(" ".join(call) for call in fake.calls)
            self.assertNotIn("/sys/", flattened)
            self.assertNotIn("boost", flattened)

    def test_rollback_removes_preset_files_that_did_not_preexist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSystem(root)
            installer = PowerBackendInstaller(root, fake)
            backup = installer.install(Path("power-profiles"))
            installer.rollback(backup.path)
            self.assertFalse((root / "etc/tuned/active_profile").exists())
            self.assertFalse((root / "etc/tuned/profile_mode").exists())

    def test_rollback_clears_preset_for_profile_it_removes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tuned_dir = root / "etc/tuned"
            tuned_dir.mkdir(parents=True)
            (tuned_dir / "active_profile").write_text("luminophore-balanced-capped\n", encoding="utf-8")
            (tuned_dir / "profile_mode").write_text("manual\n", encoding="utf-8")
            fake = FakeSystem(root)
            installer = PowerBackendInstaller(root, fake)
            backup = installer.install(Path("power-profiles"))
            installer.rollback(backup.path)
            self.assertEqual((tuned_dir / "active_profile").read_text(encoding="utf-8"), "")
            self.assertEqual((tuned_dir / "profile_mode").read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
