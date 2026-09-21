from __future__ import annotations

import unittest
from pathlib import Path

from luminophore_shell.hyprland import SpatialOutputView, SpatialState, SpatialView, SpatialWindow
from luminophore_shell.ui.spatial_map import spatial_cells


class SpatialMapTests(unittest.TestCase):
    def test_signed_display_coordinates_do_not_change_internal_grid(self) -> None:
        state = SpatialState(True, 1, 15, 5, SpatialView(0, 0, 2, 1), (), display_origin=(7, 2))
        cells = spatial_cells(state)
        self.assertEqual(state.display_point(cells[0].x, cells[0].y), (-7, -2))
        self.assertEqual(state.display_point(cells[-1].x, cells[-1].y), (7, 2))
        self.assertEqual(state.display_point(7, 2), (0, 0))
        for cell in cells:
            x, y = state.display_point(cell.x, cell.y)
            self.assertEqual((x + 7, y + 2), (cell.x, cell.y))

    def test_grid_has_one_cell_for_every_board_coordinate(self) -> None:
        state = SpatialState(
            active=True,
            revision=4,
            columns=4,
            rows=2,
            view=SpatialView(1, 0, 2, 1),
            windows=(
                SpatialWindow("0xabc", 1, 0, True),
                SpatialWindow("0xdef", 3, 1, False),
            ),
        )

        cells = spatial_cells(state)

        self.assertEqual(len(cells), 8)
        self.assertEqual((cells[0].x, cells[0].y), (0, 0))
        self.assertEqual((cells[-1].x, cells[-1].y), (3, 1))
        self.assertEqual(cells[1].address, "0xabc")
        self.assertTrue(cells[1].in_view)
        self.assertEqual(cells[-1].address, "0xdef")
        self.assertFalse(cells[-1].in_view)

    def test_editor_visibility_is_independent_of_overview_lifecycle(self) -> None:
        source = Path("luminophore_shell/app.py").read_text(encoding="utf-8")
        toggle = source[source.index("def _toggle_overview") : source.index("def _overview_surface_revealed")]
        hide = source[source.index("def _hide_overview") : source.index("def _app_icons_changed")]
        self.assertNotIn("spatial_editor_surface", toggle)
        self.assertNotIn("spatial_editor_surface", hide)
        self.assertNotIn("SpatialMapSurface(", source)

    def test_surface_keeps_one_overlay_layer_and_refresh_does_not_present(self) -> None:
        source = Path("luminophore_shell/ui/spatial_map.py").read_text(encoding="utf-8")
        update = source[source.index("    def update(self, windows") : source.index("    def destroy", source.index("    def update(self, windows"))]

        self.assertEqual(source.count("Gtk4LayerShell.set_layer"), 1)
        self.assertIn("Gtk4LayerShell.Layer.OVERLAY", source)
        self.assertNotIn("present()", update)

    def test_spatial_revision_event_refreshes_the_map(self) -> None:
        source = Path("luminophore_shell/app.py").read_text(encoding="utf-8")
        handler = source[source.index("    def _hypr_event") : source.index("    def _dismiss_expanded_at", source.index("    def _hypr_event"))]

        self.assertIn('"luminophorespatial"', handler)
        self.assertIn("GLib.idle_add(self._queue_window_refresh)", handler)

    def test_asymmetric_output_views_are_drawn_from_committed_rectangles(self) -> None:
        state = SpatialState(
            active=True,
            revision=7,
            columns=5,
            rows=3,
            view=SpatialView(0, 0, 2, 1),
            windows=(
                SpatialWindow("0xa", 0, 0, True, primary_output=20),
                SpatialWindow("0xb", 2, 2, True, primary_output=10),
            ),
            committed=True,
            topology_revision=3,
            committed_model_revision=7,
            committed_topology_revision=3,
            output_views=(
                SpatialOutputView(20, SpatialView(0, 0, 1, 1), "0xa"),
                SpatialOutputView(10, SpatialView(2, 0, 1, 3), "0xb"),
            ),
            focused_key="0xb",
        )

        cells = {(cell.x, cell.y): cell for cell in spatial_cells(state)}

        self.assertEqual(cells[(0, 0)].view_output_ids, (20,))
        self.assertEqual(cells[(2, 2)].view_output_ids, (10,))
        self.assertTrue(cells[(2, 2)].target_output)
        self.assertFalse(cells[(0, 0)].target_output)
        self.assertFalse(cells[(1, 0)].in_view)

    def test_wide_state_marks_only_the_explicit_wide_key(self) -> None:
        state = SpatialState(
            active=True,
            revision=8,
            columns=4,
            rows=1,
            view=SpatialView(0, 0, 2, 1),
            windows=(SpatialWindow("0xa", 1, 0, True, primary_output=10, fragment_outputs=(10, 20)),),
            presentation_mode="wide",
            output_views=(SpatialOutputView(10, SpatialView(0, 0, 1, 1)), SpatialOutputView(20, SpatialView(2, 0, 1, 1))),
            wide_key="0xa",
            focused_key="0xa",
        )

        cells = {(cell.x, cell.y): cell for cell in spatial_cells(state)}

        self.assertTrue(cells[(0, 0)].wide)
        self.assertTrue(cells[(1, 0)].wide)
        self.assertTrue(cells[(2, 0)].wide)
        self.assertTrue(cells[(1, 0)].wide_key)
        self.assertFalse(cells[(0, 0)].wide_key)


if __name__ == "__main__":
    unittest.main()
