from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from luminophore_shell.power_profiles import (
    PROFILE_MAP,
    PowerProfileError,
    TunedPowerController,
    parse_active_profile,
    parse_available_profiles,
    validate_profile_source,
)


class TunedPowerProfileTests(unittest.TestCase):
    def test_parser_maps_only_luminophore_owned_profiles(self) -> None:
        active = parse_active_profile("Current active profile: luminophore-balanced-capped\n")
        available = parse_available_profiles(
            "Available profiles:\n- balanced - upstream\n- luminophore-eco-capped - eco\n"
            "- luminophore-balanced-capped - normal\n- luminophore-gaming-capped - game\n"
        )
        self.assertEqual(active, "luminophore-balanced-capped")
        self.assertEqual(set(PROFILE_MAP.values()) & available, set(PROFILE_MAP.values()))

    def test_snapshot_exposes_stable_public_profile_ids(self) -> None:
        def runner(argv):
            output = (
                "Current active profile: luminophore-balanced-capped\n" if argv[-1] == "active" else
                "- luminophore-eco-capped\n- luminophore-balanced-capped\n- luminophore-gaming-capped\n"
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

        state = TunedPowerController(runner).snapshot()
        self.assertTrue(state.available)
        self.assertEqual(state.active, "balanced")
        self.assertEqual(state.profiles, ("eco", "balanced", "gaming"))

    def test_profile_change_uses_fixed_argv_and_verifies_result(self) -> None:
        calls: list[tuple[str, ...]] = []
        active = ["luminophore-balanced-capped"]

        def runner(argv):
            calls.append(tuple(argv))
            if argv[1] == "active":
                return subprocess.CompletedProcess(argv, 0, f"Current active profile: {active[0]}\n", "")
            if argv[1] == "profile":
                active[0] = argv[2]
            return subprocess.CompletedProcess(argv, 0, "", "")

        TunedPowerController(runner).set_profile("gaming", ("eco", "balanced", "gaming"))
        self.assertIn(("tuned-adm", "profile", "luminophore-gaming-capped"), calls)
        self.assertEqual(calls[-1], ("tuned-adm", "verify"))

    def test_failed_verification_restores_previous_profile(self) -> None:
        calls: list[tuple[str, ...]] = []
        active = ["luminophore-balanced-capped"]

        def runner(argv):
            calls.append(tuple(argv))
            if argv[1] == "active":
                return subprocess.CompletedProcess(argv, 0, f"Current active profile: {active[0]}\n", "")
            if argv[1] == "profile":
                active[0] = argv[2]
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 1, "", "verify failed")

        with self.assertRaises(PowerProfileError):
            TunedPowerController(runner).set_profile("gaming", tuple(PROFILE_MAP))
        self.assertEqual(active[0], "luminophore-balanced-capped")
        self.assertEqual(calls[-1], ("tuned-adm", "profile", "luminophore-balanced-capped"))

    def test_duplicate_transition_is_rejected(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def runner(argv):
            if argv[1] == "active" and not entered.is_set():
                entered.set()
                release.wait(2)
            return subprocess.CompletedProcess(argv, 0, "Current active profile: luminophore-balanced-capped\n", "")

        controller = TunedPowerController(runner)
        thread = threading.Thread(target=lambda: controller.set_profile("balanced", tuple(PROFILE_MAP)))
        thread.start()
        entered.wait(1)
        with self.assertRaisesRegex(PowerProfileError, "transition_busy"):
            controller.set_profile("balanced", tuple(PROFILE_MAP))
        release.set()
        thread.join(2)

    def test_profile_sources_never_control_firmware_boost_or_force_minimum(self) -> None:
        names = validate_profile_source(Path("power-profiles"))
        self.assertEqual(set(names), set(PROFILE_MAP.values()))

    def test_profile_source_validator_rejects_boost(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in PROFILE_MAP.values():
                path = root / name / "tuned.conf"
                path.parent.mkdir(parents=True)
                path.write_text("[main]\n[cpu]\n", encoding="utf-8")
            (root / "luminophore-gaming-capped/tuned.conf").write_text(
                "[main]\n[cpu]\nboost=1\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(PowerProfileError, "forbidden_profile_key"):
                validate_profile_source(root)

    def test_profile_source_validator_rejects_inheritance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in PROFILE_MAP.values():
                path = root / name / "tuned.conf"
                path.parent.mkdir(parents=True)
                path.write_text("[main]\n[cpu]\n", encoding="utf-8")
            (root / "luminophore-eco-capped/tuned.conf").write_text(
                "[main]\ninclude=cachyos-desktop\n[cpu]\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(PowerProfileError, "unsafe_profile_inheritance"):
                validate_profile_source(root)


if __name__ == "__main__":
    unittest.main()
