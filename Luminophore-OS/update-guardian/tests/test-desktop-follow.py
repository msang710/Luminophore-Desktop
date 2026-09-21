from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "desktop-follow.py"
SPEC = spec_from_file_location("desktop_follow", SOURCE)
desktop_follow = module_from_spec(SPEC)
SPEC.loader.exec_module(desktop_follow)


class DesktopFollowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "db"
        self.queue = self.root / "queue"
        self.db.mkdir()
        self.queue.mkdir()
        self.manifest = self.root / "capabilities.json"
        self.manifest.write_text(json.dumps({
            "schema": "luminophore-desktop-capabilities/v1",
            "packages": {"required": ["glibc", "systemd"], "private": [], "optional": {}},
            "capabilities": [],
        }))
        self.release_state = self.root / "state.json"
        self.release_state.write_text(json.dumps({"selected": "a" * 64}))
        self.package("glibc", "1-1")
        self.package("quickhack", "1.0-1")

    def package(self, name, version):
        directory = self.db / f"{name}-{version}"
        directory.mkdir()
        (directory / "desc").write_text(
            f"%NAME%\n{name}\n\n%VERSION%\n{version}\n", encoding="utf-8"
        )

    def observe(self, targets):
        return desktop_follow.observe(
            targets, dbpath=self.db, queue=self.queue, manifest_path=self.manifest,
            release_state=self.release_state, observed_at="2026-09-21T00:00:00Z",
        )

    def test_unrelated_app_is_ignored_but_shared_package_is_queued(self):
        self.assertIsNone(self.observe(["quickhack"]))
        record_path = self.observe(["glibc", "quickhack"])
        record = json.loads(record_path.read_text())
        self.assertEqual(record["state"], "observed")
        self.assertEqual(record["changes"], [{"name": "glibc", "version": "1-1"}])
        self.assertEqual(record["release_digest"], "a" * 64)
        self.assertEqual(len(record["package_set_digest"]), 64)

    def test_same_final_package_set_is_deduplicated(self):
        first = self.observe(["glibc"])
        second = self.observe(["glibc"])
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.queue.glob("*.json"))), 1)

    def test_different_final_package_set_is_a_distinct_transaction(self):
        first = self.observe(["glibc"])
        (self.db / "glibc-1-1").rename(self.db / "glibc-1-2")
        (self.db / "glibc-1-2/desc").write_text("%NAME%\nglibc\n\n%VERSION%\n1-2\n")
        second = self.observe(["glibc"])
        self.assertNotEqual(first, second)

    def test_state_machine_persists_and_rejects_skipped_transition(self):
        path = self.observe(["glibc"])
        desktop_follow.transition(path, "evaluating", {"attempt": 1})
        with self.assertRaisesRegex(desktop_follow.FollowError, "transition"):
            desktop_follow.transition(path, "installed", {})
        desktop_follow.transition(path, "compatible", {"contract": "b" * 64})
        self.assertEqual(json.loads(path.read_text())["state"], "compatible")

    def test_resume_returns_interrupted_work_to_observed(self):
        path = self.observe(["glibc"])
        desktop_follow.transition(path, "evaluating", {"attempt": 1})
        self.assertEqual(desktop_follow.resume(self.queue), 1)
        record = json.loads(path.read_text())
        self.assertEqual(record["state"], "observed")
        self.assertEqual(record["last_error"], "interrupted:evaluating")

    def test_precheck_is_read_only_and_never_claims_pacman_authority(self):
        result = desktop_follow.precheck(["glibc", "quickhack"], self.manifest)

        self.assertEqual(result["relevant"], ["glibc"])
        self.assertEqual(result["action"], "evaluate-after-transaction")
        self.assertEqual(list(self.queue.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
