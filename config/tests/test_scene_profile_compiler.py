import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from PIL import Image, ImageDraw
from luminophore_shell.background_scene import SceneAsset
from luminophore_shell.scene_profile import ProfileLayer, SceneProfileDraft
from luminophore_shell.scene_profile_compiler import compile_profile, validate_package
from luminophore_shell.background_protocol import asset_spec


@unittest.skipUnless(
    importlib.util.find_spec("cv2"), "OpenCV compiler dependency is required"
)
class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "original.png"
        image = Image.new("RGBA", (80, 60), (10, 20, 30, 255))
        ImageDraw.Draw(image).rectangle((25, 15, 55, 45), fill=(220, 80, 10, 255))
        image.save(self.source)
        self.draft = SceneProfileDraft(
            "fixture",
            SceneAsset.capture(self.source),
            [
                ProfileLayer("background", 0),
                ProfileLayer(
                    "subject",
                    1,
                    [{"x": 0.5, "y": 0.5, "radius": 0.08, "include": True}],
                ),
            ],
        )

    def test_repeat_compilation_is_identical_and_preserves_original(self):
        before = self.source.read_bytes()
        a = compile_profile(self.draft, self.root / "first")
        b = compile_profile(self.draft, self.root / "second")
        self.assertEqual(a.read_bytes(), b.read_bytes())
        data = validate_package(a)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(len(data["layers"]), 2)
        for row in data["layers"]:
            self.assertEqual(
                (a.parent / row["file"]).read_bytes(),
                (b.parent / row["file"]).read_bytes(),
            )
        alpha = Image.open(a.parent / "layer-1.png").getchannel("A")
        self.assertGreater(alpha.getpixel((40, 30)), 240)
        self.assertEqual(alpha.getpixel((0, 0)), 0)

    def test_empty_seed_layer_is_transparent(self):
        self.draft.layers[1].strokes = []
        p = compile_profile(self.draft, self.root / "out")
        self.assertEqual(
            Image.open(p.parent / "layer-1.png").getchannel("A").getextrema(), (0, 0)
        )

    def test_fill_uses_connected_region_and_preserves_holes(self):
        self.draft.layers[1].strokes[0]["kind"] = "fill"
        self.draft.layers[1].feather = 0
        p = compile_profile(self.draft, self.root / "out")
        alpha = Image.open(p.parent / "layer-1.png").getchannel("A")
        self.assertEqual(alpha.getpixel((26, 16)), 255)
        self.assertEqual(alpha.getpixel((1, 1)), 0)

    def test_tampered_layer_rejected(self):
        p = compile_profile(self.draft, self.root / "out")
        (p.parent / "layer-0.png").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "package_hash"):
            validate_package(p)

    def test_changed_source_blocks_new_apply_but_cached_recovery_works(self):
        p = compile_profile(self.draft, self.root / "out")
        asset = SceneAsset.capture(p)
        self.source.unlink()
        with self.assertRaises(OSError):
            asset_spec(asset)
        self.assertEqual(len(asset_spec(asset, recovery=True)["layers"]), 2)

    def test_negative_dimensions_rejected(self):
        p = compile_profile(self.draft, self.root / "out")
        data = json.loads(p.read_text())
        data.update(width=-80, height=-60)
        p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "package_schema"):
            validate_package(p)

    def test_foreground_cannot_be_background_layer(self):
        self.draft.layers[0].depth = 1
        with self.assertRaisesRegex(ValueError, "background_depth"):
            compile_profile(self.draft, self.root / "out")
