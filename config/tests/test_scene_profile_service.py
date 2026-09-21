import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from luminophore_shell.background_scene import SceneAsset
from luminophore_shell.scene_profile import ProfileLayer, SceneProfileDraft
from luminophore_shell.scene_profile_service import SceneProfileService
from luminophore_shell.scene_profile_store import ProfileStore


class ProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / "image.png"
        Image.new("RGBA", (80, 60), "red").save(source)
        self.draft = SceneProfileDraft(
            "draft", SceneAsset.capture(source), [ProfileLayer("base", 0)], motion=0
        )
        self.service = SceneProfileService(
            ProfileStore(self.root / "profiles"),
            os.environ.get("LUMINOPHORE_BACKGROUND_TEST_BINARY"),
        )
        self.addCleanup(self.service.cancel)

    def test_apply_requires_saved_preview(self):
        with self.assertRaisesRegex(ValueError, "preview_required"):
            self.service.save(self.draft)

    def test_cancelled_worker_cannot_kill_new_worker(self):
        class Process:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
                self.killed = 0
                self.returncode = None

            def communicate(self, timeout):
                self.started.set()
                self.release.wait(3)
                raise ValueError("old worker failed")

            def poll(self):
                return self.returncode

            def terminate(self):
                pass

            def kill(self):
                self.killed += 1
                self.returncode = -9

            def wait(self, timeout):
                return self.returncode

        old, new = Process(), Process()
        done_old = threading.Event()
        done_new = threading.Event()
        with patch(
            "luminophore_shell.scene_profile_service.subprocess.Popen", side_effect=[old, new]
        ):
            self.service.compile(self.draft, done_old.set)
            self.assertTrue(old.started.wait(2))
            self.service.compile(self.draft, done_new.set)
            self.assertTrue(new.started.wait(2))
            old.release.set()
            self.assertTrue(done_old.wait(2))
            self.assertEqual(new.killed, 0)
            new.release.set()
            self.assertTrue(done_new.wait(2))

    @unittest.skipUnless(
        os.environ.get("LUMINOPHORE_BACKGROUND_TEST_BINARY")
        and importlib.util.find_spec("cv2"),
        "native/OpenCV integration dependencies required",
    )
    def test_real_worker_preview_save_and_dirty_invalidation(self):
        done = threading.Event()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            self.service.compile(self.draft, done.set)
            self.assertTrue(done.wait(30))
        finally:
            os.chdir(previous)
        self.assertEqual(self.service.phase, "preview_ready", self.service.error)
        self.assertTrue(Path(self.service.result["preview"]).is_file())
        saved = self.service.save(self.draft)
        saved.validate()
        self.service.cancel()
        self.draft.revision += 1
        with self.assertRaisesRegex(ValueError, "preview_required"):
            self.service.save(self.draft)
        saved.validate()
