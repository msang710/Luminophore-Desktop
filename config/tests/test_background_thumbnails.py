from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from PIL import Image
from luminophore_shell.background_thumbnails import BackgroundThumbnails


class ThumbnailTests(unittest.TestCase):
    def test_rapid_navigation_keeps_latest_request_and_bounds_queue(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.png"
            Image.new("RGB", (8, 8), "blue").save(path)
            entered, release, latest = Event(), Event(), Event()

            def blocked():
                entered.set()
                release.wait(3)

            def asset(key, validate=lambda: None):
                return SimpleNamespace(
                    path=str(path),
                    sha256=str(key),
                    fit="cover",
                    focal_x=0.5,
                    focal_y=0.5,
                    validate=validate,
                )

            loader = BackgroundThumbnails()
            self.addCleanup(loader.close)
            loader.request(asset("blocked", blocked), lambda _: None)
            self.assertTrue(entered.wait(2))
            for i in range(100):
                loader.request(
                    asset(i),
                    lambda pixels, k=i: latest.set() if k == 99 and pixels else None,
                )
            with loader.condition:
                self.assertLessEqual(len(loader.pending), 12)
            release.set()
            self.assertTrue(latest.wait(2))
            loader.close()
