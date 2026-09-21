from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import time
import unittest
from luminophore_shell.background_controller import BackgroundSceneController
from luminophore_shell.background_scene import SceneAsset, WallpaperScene
from luminophore_shell.background_store import BackgroundStore


class FakeLayer:
    def __init__(self):
        self.process = None
        self.commands = []
        self.closed = 0
        self.ready = threading.Event()
        self.ready.set()
        self.presented = threading.Event()
        self.presented.set()
        self.failure = None

    def start(self):
        self.process = SimpleNamespace(poll=lambda: None)

    def send(self, command, generation, **fields):
        self.commands.append((command, generation, fields))

    def wait(self, kind, generation, timeout=10, cancelled=lambda: False):
        gate = self.ready if kind == "READY" else self.presented
        while not gate.wait(0.005):
            if cancelled():
                raise RuntimeError("superseded")
        if self.failure and kind == "READY":
            raise ValueError(self.failure)
        return {"event": kind, "generation": generation}

    def close(self):
        self.closed += 1
        self.process = None


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        source = root / "image.png"
        source.write_bytes(b"fixture")
        asset = SceneAsset.capture(source)
        self.store = BackgroundStore(root / "config", root / "state")
        for name in ("a", "b", "c"):
            self.store.save(WallpaperScene(name, name, asset, asset))
        self.layer = FakeLayer()
        self.actions = []
        monitors = lambda: [
            SimpleNamespace(name=f"DP-{i}", x=i * 1920, y=0, width=1920, height=1080)
            for i in range(2)
        ]
        self.controller = BackgroundSceneController(
            monitors,
            self.store,
            self.layer,
            palette=lambda *_: self.actions.append("palette"),
            retire=lambda: self.actions.append("retire"),
        )
        self.addCleanup(self.controller.close)

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail(self.controller.status())

    def test_palette_and_state_wait_for_all_presentations(self):
        self.layer.presented.clear()
        self.controller.request("a", "first")
        self.until(lambda: self.controller.phase == "transitioning")
        self.assertIsNone(self.store.committed())
        self.assertEqual(self.actions, [])
        self.layer.presented.set()
        self.until(lambda: self.controller.phase == "committed")
        self.assertEqual(self.store.committed()["scene"], "a")
        self.assertEqual(self.actions, ["retire", "palette"])

    def test_prepare_failure_preserves_current_process_and_last_good(self):
        self.controller.request("a", "first")
        self.until(lambda: self.controller.phase == "committed")
        self.layer.failure = "asset_or_output_invalid"
        self.controller.request("b", "second")
        self.until(lambda: self.controller.phase == "error")
        self.assertEqual(self.store.committed()["scene"], "a")
        self.assertEqual(self.layer.closed, 0)
        self.assertIn("ABORT", [c[0] for c in self.layer.commands])

    def test_latest_request_supersedes_preparing_without_committing_it(self):
        self.layer.ready.clear()
        self.controller.request("a", "first")
        self.until(lambda: self.controller.phase == "preparing")
        self.controller.request("b", "second")
        self.until(
            lambda: len([c for c in self.layer.commands if c[0] == "PREPARE"]) == 2
        )
        self.layer.ready.set()
        self.until(lambda: self.controller.phase == "committed")
        self.assertEqual(self.store.committed()["scene"], "b")
        self.assertEqual(len([c for c in self.layer.commands if c[0] == "COMMIT"]), 1)

    def test_duplicate_request_is_idempotent_and_conflict_rejected(self):
        self.controller.request("a", "same")
        self.until(lambda: self.controller.phase == "committed")
        self.controller.request("a", "same")
        self.assertEqual(len([c for c in self.layer.commands if c[0] == "COMMIT"]), 1)
        with self.assertRaisesRegex(ValueError, "request_conflict"):
            self.controller.request("b", "same")

    def test_user_request_replaces_pending_recovery(self):
        with self.controller.lock:
            self.controller.pending = (None, "a")
            self.controller.request("b", "replacement")
        self.until(lambda: self.controller.phase == "committed")
        self.assertEqual(self.controller.active, "b")

    def test_palette_failure_does_not_undo_presented_scene(self):
        self.controller.palette = lambda *_: (_ for _ in ()).throw(
            RuntimeError("palette failure")
        )
        self.controller.request("a")
        self.until(lambda: self.controller.error == "palette_failed")
        self.assertEqual(self.store.committed()["scene"], "a")
        self.assertEqual(self.layer.closed, 0)

    def test_restore_uses_last_good_when_library_index_is_corrupt(self):
        scene = self.store.scenes()[0]
        self.store.commit(scene, 1, {"left": "DP-0", "right": "DP-1"})
        (self.store.root / "wallpaper-scenes.json").write_text("broken")
        self.controller.restore()
        self.until(lambda: self.controller.phase == "committed")
        self.assertEqual(self.controller.active, scene.id)

    def test_corrupt_state_uses_native_recovery_without_committing_it(self):
        self.store.state.mkdir(parents=True)
        (self.store.state / "background-state.json").write_text("broken")
        self.controller.restore()
        self.until(lambda: self.controller.phase == "degraded")
        self.assertIn("FALLBACK", [c[0] for c in self.layer.commands])
        self.assertEqual(
            (self.store.state / "background-state.json").read_text(), "broken"
        )
