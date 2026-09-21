import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from luminophore_shell.background_scene import SceneAsset, WallpaperScene, bind_outputs
from luminophore_shell.background_store import BackgroundStore, atomic_json
from luminophore_shell.background_picker import ScenePickerModel


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "image.png"
        self.source.write_bytes(b"original")
        self.asset = SceneAsset.capture(self.source)
        self.scene = WallpaperScene("pair", "Pair", self.asset, self.asset)
        self.store = BackgroundStore(self.root / "config", self.root / "state")

    def test_scene_roundtrip_and_register_does_not_apply(self):
        self.store.save(self.scene)
        self.assertEqual(self.store.scenes(), (self.scene,))
        self.assertIsNone(self.store.committed())
        self.assertEqual(self.source.read_bytes(), b"original")

    def test_changed_source_is_rejected(self):
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source_changed"):
            self.asset.validate()

    def test_invalid_viewport(self):
        for value in (float("nan"), -1, 2, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SceneAsset(self.asset.path, self.asset.sha256, focal_x=value)

    def test_roles_follow_position_not_enumeration(self):
        monitors = [
            SimpleNamespace(name="DP-2", x=1920),
            SimpleNamespace(name="DP-1", x=0),
        ]
        self.assertEqual(bind_outputs(monitors), {"left": "DP-1", "right": "DP-2"})
        monitors[0].x = 0
        with self.assertRaises(ValueError):
            bind_outputs(monitors)

    def test_unsupported_output_counts(self):
        for count in (0, 1, 3):
            with self.assertRaises(ValueError):
                bind_outputs([SimpleNamespace(name=str(i), x=i) for i in range(count)])

    def test_failed_atomic_replace_keeps_last_good(self):
        path = self.root / "state.json"
        atomic_json(path, {"value": "old"})
        with patch(
            "luminophore_shell.background_store.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                atomic_json(path, {"value": "new"})
        self.assertEqual(json.loads(path.read_text()), {"value": "old"})
        self.assertEqual(list(self.root.glob(".scene-*")), [])

    def test_picker_wraps_without_mutating_scene(self):
        second = WallpaperScene("second", "Second", self.asset, self.asset)
        model = ScenePickerModel((self.scene, second), "pair")
        self.assertEqual(model.navigate(-1), second)
        self.assertEqual(model.navigate(1), self.scene)
        self.assertIsNone(ScenePickerModel().navigate(1))
