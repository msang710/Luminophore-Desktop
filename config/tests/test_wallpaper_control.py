from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from luminophore_shell.appearance_types import WallpaperProviderName
from luminophore_shell.wallpaper_control import WallpaperController, WallpaperControlError, validate_wallpaper


class WallpaperControllerTests(unittest.TestCase):
    def _image(self, root: str) -> Path:
        path = Path(root) / "wallpaper.png"
        Image.new("RGB", (8, 8), "#123456").save(path)
        return path

    def test_explicit_provider_delegates_to_transactional_adapter(self) -> None:
        adapter = Mock(name=WallpaperProviderName.HYPRPAPER)
        adapter.name = WallpaperProviderName.HYPRPAPER
        adapter.query.return_value = Mock()
        with tempfile.TemporaryDirectory() as directory, patch(
            "luminophore_shell.wallpaper_control.provider_for", return_value=adapter,
        ):
            image = self._image(directory)
            provider = WallpaperController("hyprpaper").apply(image, "DP-2")
        self.assertEqual(provider, "hyprpaper")
        adapter.query.assert_called_once_with(("DP-2",))
        adapter.apply_batch.assert_called_once()

    def test_missing_explicit_provider_and_invalid_image_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "not-image.txt"
            invalid.write_text("not an image", encoding="utf-8")
            with self.assertRaisesRegex(WallpaperControlError, "invalid_image"):
                validate_wallpaper(invalid)
            image = self._image(directory)
            with self.assertRaisesRegex(WallpaperControlError, "provider_required"):
                WallpaperController().apply(image, "DP-1")


if __name__ == "__main__":
    unittest.main()
