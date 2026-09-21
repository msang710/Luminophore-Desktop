from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image

from luminophore_shell.appearance_types import AppearanceErrorCategory, AppearanceSource, AppearanceSourceKind, MonitorAssignment
from luminophore_shell.wallpaper_providers import AwwwProvider, HyprpaperProvider, WallpaperProviderError


class StatefulRunner:
    def __init__(self, provider: str, state: dict[str, str]) -> None:
        self.provider = provider
        self.state = dict(state)
        self.calls: list[tuple[str, ...]] = []
        self.fail_path = ""

    def __call__(self, argv, _timeout: float):
        args = tuple(argv)
        self.calls.append(args)
        if args[-1] == "listactive":
            return subprocess.CompletedProcess(args, 0, "".join(f"{key}: {value}\n" for key, value in self.state.items()), "")
        if "query" in args:
            payload = {"awww-daemon": [{"name": key, "displaying": {"image": value}} for key, value in self.state.items()]}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if self.provider == "hyprpaper":
            connector, path, _fit = args[-1].split(",", 2)
        else:
            connector, path = args[args.index("--outputs") + 1], args[-1]
        if path == self.fail_path:
            return subprocess.CompletedProcess(args, 1, "", "failed")
        self.state[connector] = path
        return subprocess.CompletedProcess(args, 0, "", "")


class WallpaperProviderTests(unittest.TestCase):
    def _images(self, root: str) -> dict[str, Path]:
        paths = {name: Path(root) / f"{name}.png" for name in ("old1", "old2", "new1", "new2")}
        for index, path in enumerate(paths.values()):
            Image.new("RGB", (8, 8), (index * 30, 20, 40)).save(path)
        return paths

    def test_hyprpaper_query_apply_and_verify_exact_state(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            images = self._images(root)
            runner = StatefulRunner("hyprpaper", {"DP-1": str(images["old1"]), "DP-2": str(images["old2"])})
            provider = HyprpaperProvider(runner)
            before = provider.query(("DP-1", "DP-2"))
            expected = provider.apply_batch((
                MonitorAssignment("DP-1", AppearanceSource(AppearanceSourceKind.IMAGE, str(images["new1"]))),
                MonitorAssignment("DP-2", AppearanceSource(AppearanceSourceKind.IMAGE, str(images["new2"]))),
            ), before)
            provider.verify(expected)
            self.assertEqual(runner.state, {"DP-1": str(images["new1"]), "DP-2": str(images["new2"])})

    def test_partial_apply_is_compensated_in_reverse(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            images = self._images(root)
            runner = StatefulRunner("awww", {"DP-1": str(images["old1"]), "DP-2": str(images["old2"])})
            runner.fail_path = str(images["new2"])
            provider = AwwwProvider(runner)
            before = provider.query(("DP-1", "DP-2"))
            rows = (
                MonitorAssignment("DP-1", AppearanceSource(AppearanceSourceKind.IMAGE, str(images["new1"]))),
                MonitorAssignment("DP-2", AppearanceSource(AppearanceSourceKind.IMAGE, str(images["new2"]))),
            )
            with self.assertRaises(WallpaperProviderError) as raised:
                provider.apply_batch(rows, before)
            self.assertEqual(raised.exception.category, AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK)
            self.assertEqual(runner.state, {"DP-1": str(images["old1"]), "DP-2": str(images["old2"])})
            apply_paths = [call[-1] for call in runner.calls if "img" in call]
            self.assertEqual(apply_paths, [str(images["new1"]), str(images["new2"]), str(images["old1"])])

    def test_incomplete_pre_state_blocks_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            images = self._images(root)
            runner = StatefulRunner("hyprpaper", {"DP-1": str(images["old1"])})
            provider = HyprpaperProvider(runner)
            with self.assertRaises(WallpaperProviderError) as raised:
                provider.query(("DP-1", "DP-2"))
            self.assertEqual(raised.exception.category, AppearanceErrorCategory.PROVIDER_INCOMPLETE)
            self.assertFalse(any("wallpaper" in call for call in runner.calls))


if __name__ == "__main__":
    unittest.main()
