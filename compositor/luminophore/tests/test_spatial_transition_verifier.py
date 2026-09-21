from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "luminophore" / "scripts" / "verify-spatial-transition"


def state(*, revision: int = 4, topology: int = 2, committed: bool = True, visible: bool = True) -> dict:
    return {
        "active": True,
        "committed": committed,
        "revision": revision,
        "topologyRevision": topology,
        "committedRevision": {"model": revision, "topology": topology},
        "presentationMode": "normal",
        "wideKey": None,
        "focusedKey": "0x1" if visible else None,
        "extent": {"columns": 4, "rows": 2},
        "view": {"x": 0, "y": 0, "columns": 2, "rows": 2},
        "outputViews": [{"output": 100, "x": 0, "y": 0, "columns": 2, "rows": 2, "anchorKey": "0x1" if visible else None}],
        "outputs": [{
            "id": 100,
            "name": "DP-2",
            "box": {"x": 0, "y": 0, "width": 1920, "height": 1080},
            "scaleMilli": 1000,
            "transform": 0,
        }],
        "windows": [{
            "address": "0x1",
            "mode": "tiled",
            "x": 0,
            "y": 0,
            "visible": visible,
            "primaryOutput": 100 if visible else 0,
            "box": {"x": 0, "y": 0, "width": 960 if visible else 0, "height": 1080 if visible else 0},
            "fragments": ([{
                "output": 100,
                "x": 0,
                "y": 0,
                "box": {"x": 0, "y": 0, "width": 960, "height": 1080},
            }] if visible else []),
        }],
    }


class SpatialTransitionVerifierTests(unittest.TestCase):
    def test_desktop_retains_wide_target_but_rejects_visible_clients(self) -> None:
        hidden = state(visible=False)
        hidden["presentationMode"] = "desktop"
        hidden["wideKey"] = "0x1"
        self.assertEqual(self.run_verifier(hidden).returncode, 0)
        visible = state()
        visible["presentationMode"] = "desktop"
        self.assertNotEqual(self.run_verifier(visible).returncode, 0)

    def run_verifier(
        self,
        before: dict,
        after: dict | None = None,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            states = root / "states.json"
            states.write_text(json.dumps([before, after or before]))
            fake = root / "hyprctl"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "root = pathlib.Path(os.environ['FAKE_ROOT'])\n"
                "if sys.argv[1:3] == ['-j', 'luminophorespatialstate2']:\n"
                "    index = root / 'index'\n"
                "    value = int(index.read_text()) if index.exists() else 0\n"
                "    print(json.dumps(json.loads((root / 'states.json').read_text())[min(value, 1)]))\n"
                "    index.write_text(str(value + 1))\n"
                "elif sys.argv[1:2] == ['dispatch']:\n"
                "    sys.exit(0)\n"
                "else:\n"
                "    sys.exit(2)\n"
            )
            fake.chmod(0o755)
            env = {**os.environ, "LUMINOPHORE_HYPRCTL": str(fake), "FAKE_ROOT": str(root)}
            env.update(extra_env or {})
            return subprocess.run([VERIFIER, *args], env=env, text=True, capture_output=True)

    def test_v2_accepts_negative_view_and_disjoint_same_output_regions(self) -> None:
        value = state()
        value["protocolVersion"] = 2
        value["view"]["x"] = -1
        value["outputViews"][0].update(board=1, x=-1)
        window = value["windows"][0]
        window["board"] = 1
        window["fragments"] = [
            {"output": 100, "box": {"x": 0, "y": 0, "width": 480, "height": 1080}},
            {"output": 100, "box": {"x": 480, "y": 0, "width": 480, "height": 540}},
        ]
        result = self.run_verifier(value)
        self.assertEqual(result.returncode, 0, result.stderr)
        window["fragments"][1]["box"]["x"] = 479
        self.assertNotEqual(self.run_verifier(value).returncode, 0)

    def test_v2_rejects_unknown_version_and_duplicate_board_binding(self) -> None:
        value = state()
        value["protocolVersion"] = 99
        self.assertNotEqual(self.run_verifier(value).returncode, 0)
        value["protocolVersion"] = 2
        value["windows"][0]["board"] = 1
        value["outputViews"][0]["board"] = 1
        value["outputs"].append({**value["outputs"][0], "id": 200, "name": "DP-1"})
        value["outputViews"].append({**value["outputViews"][0], "output": 200})
        self.assertNotEqual(self.run_verifier(value).returncode, 0)

    def test_accepts_read_only_committed_snapshot(self) -> None:
        result = self.run_verifier(state())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_off_view_keyboard_focus(self) -> None:
        value = state(visible=False)
        value["focusedKey"] = "0x1"
        result = self.run_verifier(value)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_floating_client_box_can_extend_beyond_visible_fragment(self) -> None:
        value = state()
        value["focusedKey"] = None
        value["outputViews"][0]["anchorKey"] = None
        window = value["windows"][0]
        window["mode"] = "floating"
        window["box"] = {"x": -20, "y": -10, "width": 980, "height": 1090}
        result = self.run_verifier(value)
        self.assertEqual(result.returncode, 0, result.stderr)
        window["box"]["width"] = 100
        result = self.run_verifier(value)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_uncommitted_snapshot(self) -> None:
        result = self.run_verifier(state(committed=False))
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_visible_window_without_geometry(self) -> None:
        invalid = state()
        invalid["windows"][0]["box"]["width"] = 0
        result = self.run_verifier(invalid)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_fragment_whose_union_does_not_match_client_box(self) -> None:
        invalid = state()
        invalid["windows"][0]["fragments"][0]["box"]["width"] = 480
        result = self.run_verifier(invalid)
        self.assertNotEqual(result.returncode, 0)

    def test_accepts_wide_window_with_one_fragment_per_output(self) -> None:
        wide = state()
        wide["view"] = {"x": 0, "y": 0, "columns": 1, "rows": 1}
        wide["presentationMode"] = "wide"
        wide["wideKey"] = "0x1"
        wide["outputs"].append({
            "id": 200,
            "name": "DP-1",
            "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
            "scaleMilli": 1000,
            "transform": 0,
        })
        wide["outputViews"] = [
            {"output": 100, "x": 0, "y": 0, "columns": 1, "rows": 1, "anchorKey": "0x1"},
            {"output": 200, "x": 1, "y": 0, "columns": 1, "rows": 1, "anchorKey": None},
        ]
        wide["windows"][0]["box"] = {"x": 0, "y": 0, "width": 3840, "height": 1080}
        wide["windows"][0]["fragments"] = [
            {"output": 100, "x": 0, "y": 0, "box": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
            {"output": 200, "x": 0, "y": 0, "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080}},
        ]
        result = self.run_verifier(wide)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_two_by_one_with_one_window_per_output(self) -> None:
        split = state()
        split["view"] = {"x": 0, "y": 0, "columns": 2, "rows": 1}
        split["outputs"].append({
            "id": 200,
            "name": "DP-1",
            "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
            "scaleMilli": 1000,
            "transform": 0,
        })
        split["outputViews"] = [
            {"output": 100, "x": 0, "y": 0, "columns": 1, "rows": 1, "anchorKey": "0x1"},
            {"output": 200, "x": 1, "y": 0, "columns": 1, "rows": 1, "anchorKey": "0x2"},
        ]
        split["windows"] = [
            {
                "address": "0x1", "mode": "tiled", "x": 0, "y": 0, "visible": True,
                "primaryOutput": 100,
                "box": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "fragments": [{"output": 100, "x": 0, "y": 0, "box": {"x": 0, "y": 0, "width": 1920, "height": 1080}}],
            },
            {
                "address": "0x2", "mode": "tiled", "x": 1, "y": 0, "visible": True,
                "primaryOutput": 200,
                "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
                "fragments": [{"output": 200, "x": 1, "y": 0, "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080}}],
            },
        ]
        result = self.run_verifier(split, extra_env={"LUMINOPHORE_EXPECT_OUTPUTS": "2", "LUMINOPHORE_EXPECT_VIEW": "2x1"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_one_by_two_with_output_local_rows(self) -> None:
        rows = state()
        rows["view"] = {"x": 0, "y": 0, "columns": 1, "rows": 2}
        rows["outputs"].append({
            "id": 200,
            "name": "DP-1",
            "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
            "scaleMilli": 1000,
            "transform": 0,
        })
        rows["outputViews"] = [
            {"output": 100, "x": 0, "y": 0, "columns": 1, "rows": 2, "anchorKey": "0x1"},
            {"output": 200, "x": 1, "y": 0, "columns": 1, "rows": 1, "anchorKey": None},
        ]
        rows["windows"] = []
        for index, y in enumerate((0, 1), start=1):
            top = 0 if y == 0 else 540
            fragments = [{"output": 100, "x": 0, "y": y, "box": {"x": 0, "y": top, "width": 1920, "height": 540}}]
            rows["windows"].append({
                "address": f"0x{index}", "mode": "tiled", "x": 0, "y": y, "visible": True,
                "primaryOutput": 100,
                "box": {"x": 0, "y": top, "width": 1920, "height": 540},
                "fragments": fragments,
            })
        result = self.run_verifier(rows, extra_env={"LUMINOPHORE_EXPECT_OUTPUTS": "2", "LUMINOPHORE_EXPECT_VIEW": "1x2"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_tiled_fragment_outside_its_output(self) -> None:
        invalid = state()
        invalid["windows"][0]["box"]["x"] = 1800
        invalid["windows"][0]["fragments"][0]["box"]["x"] = 1800
        result = self.run_verifier(invalid)
        self.assertNotEqual(result.returncode, 0)

    def test_enforces_requested_topology_and_view_shape(self) -> None:
        accepted = self.run_verifier(state(), extra_env={"LUMINOPHORE_EXPECT_OUTPUTS": "1", "LUMINOPHORE_EXPECT_VIEW": "2x2"})
        rejected = self.run_verifier(state(), extra_env={"LUMINOPHORE_EXPECT_OUTPUTS": "2", "LUMINOPHORE_EXPECT_VIEW": "1x1"})
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0)

    def test_enforces_connector_keyed_output_views_and_presentation(self) -> None:
        split = state()
        split["extent"]["rows"] = 3
        split["outputs"].append({
            "id": 200,
            "name": "DP-1",
            "box": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
            "scaleMilli": 1000,
            "transform": 0,
        })
        split["outputViews"] = [
            {"output": 100, "x": 0, "y": 0, "columns": 1, "rows": 1, "anchorKey": "0x1"},
            {"output": 200, "x": 1, "y": 0, "columns": 1, "rows": 3, "anchorKey": None},
        ]
        expected = json.dumps({
            "DP-2": {"x": 0, "y": 0, "columns": 1, "rows": 1},
            "DP-1": {"x": 1, "y": 0, "columns": 1, "rows": 3},
        })
        accepted = self.run_verifier(
            split,
            extra_env={"LUMINOPHORE_EXPECT_PRESENTATION": "normal", "LUMINOPHORE_EXPECT_OUTPUT_VIEWS": expected},
        )
        wrong = json.dumps({
            "DP-2": {"x": 0, "y": 0, "columns": 1, "rows": 3},
            "DP-1": {"x": 1, "y": 0, "columns": 1, "rows": 1},
        })
        rejected = self.run_verifier(split, extra_env={"LUMINOPHORE_EXPECT_OUTPUT_VIEWS": wrong})
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0)

    def test_rejects_duplicate_or_missing_connector_identity(self) -> None:
        duplicate = state()
        duplicate["outputs"].append({**duplicate["outputs"][0], "id": 200})
        duplicate["outputViews"].append({
            "output": 200, "x": 2, "y": 0, "columns": 1, "rows": 1, "anchorKey": None,
        })
        missing = state()
        del missing["outputs"][0]["name"]
        self.assertNotEqual(self.run_verifier(duplicate).returncode, 0)
        self.assertNotEqual(self.run_verifier(missing).returncode, 0)

    def test_accepts_atomic_revision_advance_after_action(self) -> None:
        result = self.run_verifier(state(), state(revision=5), "view-move", "right")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_stale_commit_after_action(self) -> None:
        after = state(revision=5)
        after["committed"] = False
        after["committedRevision"]["model"] = 4
        result = self.run_verifier(state(), after, "view-move", "right")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
