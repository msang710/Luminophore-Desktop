import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from PIL import Image


@unittest.skipUnless(
    os.environ.get("LUMINOPHORE_BACKGROUND_TEST_BINARY"),
    "native renderer fixture requires explicit binary",
)
class NativePixels(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = os.environ["LUMINOPHORE_BACKGROUND_TEST_BINARY"]

    def layer(self, name, color, size=(64, 64)):
        path = self.root / f"{name}.png"
        Image.new("RGBA", size, color).save(path)
        return {"path": str(path), "depth": 0}

    def render(self, spec):
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        out = self.root / "result.png"
        result = subprocess.run(
            [self.binary, "--preview", str(path), str(out)],
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return Image.open(out)

    def test_crossfade_flattens_translucent_layers_before_fade(self):
        spec = {
            "layers": [self.layer("base", "blue"), self.layer("top", (0, 255, 0, 128))],
            "previous": {"layers": [self.layer("old", "red")]},
            "progress": 0.5,
        }
        pixel = self.render(spec).getpixel((320, 180))
        for actual, expected in zip(pixel, (127, 64, 64, 255)):
            self.assertLessEqual(abs(actual - expected), 2)

    def test_contain_has_black_bars_and_cover_fills(self):
        layer = self.layer("portrait", "red", (20, 80))
        image = self.render({"layers": [layer], "fit": "contain"})
        self.assertEqual(image.getpixel((0, 180))[:3], (0, 0, 0))
        self.assertEqual(image.getpixel((320, 180))[:3], (255, 0, 0))
        image = self.render({"layers": [layer], "fit": "cover"})
        self.assertEqual(image.getpixel((0, 180))[:3], (255, 0, 0))

    def test_vertical_orientation_survives_transition_target(self):
        layer = self.layer("halves", "blue")
        im = Image.open(layer["path"])
        im.paste((255, 0, 0, 255), (0, 0, 64, 32))
        im.save(layer["path"])
        image = self.render(
            {
                "layers": [layer],
                "previous": {"layers": [self.layer("old", "black")]},
                "progress": 1,
            }
        )
        self.assertEqual(image.getpixel((320, 60))[:3], (255, 0, 0))
        self.assertEqual(image.getpixel((320, 300))[:3], (0, 0, 255))

    def test_cover_focal_y_selects_top_and_bottom(self):
        layer = self.layer("focal", "blue", (64, 128))
        im = Image.open(layer["path"])
        im.paste((255, 0, 0, 255), (0, 0, 64, 64))
        im.save(layer["path"])
        top = self.render({"layers": [layer], "focal_y": 0})
        self.assertEqual(top.getpixel((320, 180))[:3], (255, 0, 0))
        bottom = self.render({"layers": [layer], "focal_y": 1})
        self.assertEqual(bottom.getpixel((320, 180))[:3], (0, 0, 255))

    def test_missing_asset_fails_without_output(self):
        spec = self.root / "spec.json"
        spec.write_text(json.dumps({"layers": [{"path": "/does-not-exist"}]}))
        out = self.root / "missing.png"
        result = subprocess.run(
            [self.binary, "--preview", str(spec), str(out)],
            capture_output=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())
