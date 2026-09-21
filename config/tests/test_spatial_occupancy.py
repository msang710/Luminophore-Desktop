from dataclasses import replace
import unittest
from luminophore_shell.hyprland import _spatial_diagnostics
from spatial_test_fixtures import snapshot

class SpatialOccupancyTests(unittest.TestCase):
    def state(self):
        s = snapshot()
        return replace(s, protocol_version=2,
                       output_views=tuple(replace(v, board_id=i+1) for i, v in enumerate(s.output_views)),
                       windows=(replace(s.windows[0], visible=False, board_id=1), replace(s.windows[1], visible=False, board_id=1)))

    def test_floating_cannot_share_a_coordinate_with_any_mode(self):
        for mode in ('tiled', 'floating'):
            s = self.state()
            s = replace(s, windows=(replace(s.windows[0], mode=mode), s.windows[1]))
            self.assertIn('duplicate-board-cell', _spatial_diagnostics(s))

    def test_same_coordinate_on_different_boards_is_distinct(self):
        s = self.state()
        s = replace(s, windows=(s.windows[0], replace(s.windows[1], board_id=2)))
        self.assertNotIn('duplicate-board-cell', _spatial_diagnostics(s))

    def test_preview_rejects_duplicate_occupancy_before_display(self):
        from luminophore_shell.spatial_edit import parse_edit_preview
        data = {'schema': 1, 'status': 'applied', 'revision': 2, 'topologyRevision': 1,
                'outputViews': [], 'windows': [
                    {'address': '0x1', 'x': 0, 'y': 0, 'mode': 'tiled', 'board': 1},
                    {'address': '0x2', 'x': 0, 'y': 0, 'mode': 'floating', 'board': 1}]}
        with self.assertRaisesRegex(ValueError, 'duplicate spatial preview coordinate'):
            parse_edit_preview(data)
        data['windows'][1]['board'] = 2
        self.assertEqual(len(parse_edit_preview(data).windows), 2)
