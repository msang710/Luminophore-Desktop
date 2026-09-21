"""Exercise the production bloom shaders with inherited output scissoring."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class BloomScissorEglTests(unittest.TestCase):
    def test_output_scissor_does_not_poison_bloom_cache(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("C compiler and EGL/GLES development packages required")
        flags = subprocess.run(
            ["pkg-config", "--cflags", "--libs", "egl", "glesv2"],
            capture_output=True, text=True,
        )
        if flags.returncode:
            self.skipTest("EGL/GLES development packages unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="luminophore-bloom-test-") as directory:
            binary = str(Path(directory) / "bloom-scissor")
            subprocess.run([
                "cc", str(Path(__file__).with_name("bloom_scissor_egl.c")),
                "-I", str(root / "src/render/luminophore"), "-o", binary,
                *flags.stdout.split(), "-lm",
            ], check=True, capture_output=True, timeout=30)
            result = subprocess.run(
                [binary], env={**os.environ, "LIBGL_ALWAYS_SOFTWARE": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode in (2, 3):
                self.skipTest("Surfaceless EGL context unavailable")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
