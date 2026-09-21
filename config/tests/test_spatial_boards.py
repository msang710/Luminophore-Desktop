from dataclasses import replace
import unittest
from luminophore_shell.hyprland import parse_spatial_state, HyprlandError
from luminophore_shell.ui.spatial_map import spatial_cells
from luminophore_shell.spatial_edit import SpatialEditRequest, SpatialGrabLayout


def payload():
    return dict(protocolVersion=2, active=True, committed=True, revision=3, topologyRevision=1,
                committedRevision=dict(model=3, topology=1), extent=dict(columns=2, rows=2),
                view=dict(x=-1,y=-1,columns=2,rows=2), selectedOutput=10, presentationMode='normal',
                outputViews=[dict(output=o,board=b,x=-1,y=-1,columns=2,rows=2) for o,b in ((10,1),(20,2))],
                windows=[dict(address=a,board=b,x=0,y=0,visible=True,mode='tiled',primaryOutput=o,
                              fragments=[dict(output=o),dict(output=o)]) for a,b,o in (('0xa',1,10),('0xb',2,20))])


class SpatialBoardsTests(unittest.TestCase):
    def test_same_coordinates_are_distinct_boards_and_regions_can_repeat_output(self):
        state=parse_spatial_state(payload())
        self.assertEqual(state.diagnostics,())
        cells=spatial_cells(state)
        self.assertEqual(next(c.addresses for c in cells if (c.x,c.y)==(0,0)),('0xa',))
        cells=spatial_cells(replace(state,selected_output_id=20))
        self.assertEqual(next(c.addresses for c in cells if (c.x,c.y)==(0,0)),('0xb',))

    def test_editor_viewport_is_bounded_and_can_pan_negative(self):
        state=replace(parse_spatial_state(payload()),editor_origin=(-10**12,-10**12))
        cells=spatial_cells(state)
        self.assertLessEqual(len(cells),192)
        self.assertEqual((cells[0].x,cells[0].y),state.editor_origin)

    def test_unknown_protocol_and_duplicate_owned_coordinate_rejected(self):
        p=payload();p['protocolVersion']=3
        with self.assertRaises(HyprlandError):parse_spatial_state(p)
        p=payload();p['windows'][1]['board']=1
        self.assertIn('duplicate-board-cell',parse_spatial_state(p).diagnostics)

    def test_wide_does_not_require_unrelated_outputs_to_disappear(self):
        p=payload();p.update(presentationMode='wide',wideKey='0xa')
        self.assertEqual(parse_spatial_state(p).diagnostics,())

    def test_signed64_requests_keep_exact_integer_text(self):
        request=SpatialEditRequest('move-view',3,1,10,-9007199254740993,9007199254740993)
        self.assertIn('-9007199254740993', request.arguments())
        with self.assertRaises(ValueError):replace(request,x=2**63).arguments()
        layout=SpatialGrabLayout(1,3,1,'frame',1,((-9007199254740993,0,0.,0.,10.,10.),))
        self.assertEqual(layout.payload()['cells'][0]['column'], -9007199254740993)

    def test_version_two_rejects_lossy_and_missing_coordinates(self):
        for replacement in (1.5, True, "3", 2**63):
            p = payload()
            p["windows"][0]["x"] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(HyprlandError):
                parse_spatial_state(p)
        p = payload()
        del p["outputViews"][0]["board"]
        with self.assertRaises(HyprlandError):
            parse_spatial_state(p)

    def test_grab_layout_carries_destination_board_output(self):
        layout = SpatialGrabLayout(1, 3, 1, "frame", 1, ((0, 0, 0., 0., 10., 10.),), 20)
        self.assertEqual(layout.payload()['cells'][0]['output'], '20')

    def test_editor_coordinates_stay_fixed_when_view_moves_or_resizes(self):
        state = replace(parse_spatial_state(payload()), editor_origin=(-5, -4))
        initial = spatial_cells(state, (10, 8))
        moved = replace(state, output_views=tuple(replace(v, rect=replace(v.rect, x=3, y=2, columns=9, rows=7)) for v in state.output_views))
        preview = spatial_cells(moved, (10, 8))
        self.assertEqual([(c.x, c.y) for c in initial], [(c.x, c.y) for c in preview])
        self.assertNotEqual([c.in_view for c in initial], [c.in_view for c in preview])
