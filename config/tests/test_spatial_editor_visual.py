"""Offscreen checks: never read the user's desktop or compositor buffers."""
from types import SimpleNamespace
import unittest

import cairo
from luminophore_shell.ui.spatial_editor_visual import EditorVisual, wave_sample


class EditorVisualTests(unittest.TestCase):
    def visual(self, progress=1, exiting=False):
        visual = SimpleNamespace(progress=progress, exiting=exiting,
                                 primary=(.5, .75, 1.),
                                 owner=SimpleNamespace(panel=SimpleNamespace(cell_size=20), state=SimpleNamespace(view=None)),
                                 geometry=lambda: ((60, 60, 80, 80), ()))
        visual.opacity_at = lambda radius: EditorVisual.opacity_at(visual, radius)
        return visual

    def test_preview_view_uses_retained_lattice_before_and_after_allocation(self):
        from unittest.mock import Mock
        base = {point: Mock() for point in ((0, 0), (1, 0))}
        preview = {point: Mock() for point in base}
        base[(0, 0)].has_css_class.return_value = True
        base[(1, 0)].has_css_class.return_value = False
        preview[(0, 0)].has_css_class.return_value = False
        preview[(1, 0)].has_css_class.return_value = True
        boxes = {base[(0, 0)]: (100, 100, 68, 68), base[(1, 0)]: (168, 100, 68, 68)}
        owner = SimpleNamespace(panel=SimpleNamespace(holders=base),
                                preview_panel=SimpleNamespace(holders=preview, get_visible=lambda: True),
                                _bounds=lambda widget, _target: boxes.get(widget))
        visual = SimpleNamespace(owner=owner)
        expected = ((100, 100, 136, 68), ((168, 100, 68, 68),))
        self.assertEqual(EditorVisual.geometry(visual), expected)
        # A preview allocation may lag or differ; it must not move the backdrop.
        boxes.update({preview[(0, 0)]: (0, 0, 0, 0), preview[(1, 0)]: (230, 180, 90, 90)})
        self.assertEqual(EditorVisual.geometry(visual), expected)
        owner.preview_panel.get_visible = lambda: False
        self.assertEqual(EditorVisual.geometry(visual)[1], ((100, 100, 68, 68),))

    def test_lattice_fades_to_transparent_without_black_pixels(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
        EditorVisual.draw_lines(self.visual(), None, cairo.Context(surface), 200, 200)
        surface.flush()
        pixels = memoryview(surface.get_data()).cast('B')
        visible = 0
        for offset in range(0, len(pixels), 4):
            blue, green, red, alpha = pixels[offset:offset+4]
            if alpha > 2:
                visible += 1
                self.assertGreater(blue, 0)
                self.assertAlmostEqual(blue, alpha, delta=1)
        self.assertGreater(visible, 0)
        self.assertEqual(pixels[3], 0)

    def test_exit_erases_center_before_outer_cells(self):
        visual = self.visual(.55, True)
        self.assertEqual(visual.opacity_at(0), 0)
        self.assertGreater(visual.opacity_at(.6), 0)
        self.assertEqual(self.visual(1, True).opacity_at(.6), 0)
        self.assertEqual(self.visual(0).opacity_at(0), 0)

class EditorAnimationLifecycleTests(unittest.TestCase):
    def visual(self):
        from unittest.mock import Mock
        visual = SimpleNamespace(progress=1.0, tick=0, finished=None, exiting=False,
                                 _running=False, _animation_generation=0, _resume_progress=0.0,
                                 started=None, mapped=True, callbacks={}, serial=0,
                                 refresh=Mock())
        def add(callback):
            visual.serial += 1
            visual.callbacks[visual.serial] = callback
            return visual.serial
        visual.add_tick_callback = add
        visual.remove_tick_callback = lambda ident: visual.callbacks.pop(ident, None)
        visual.get_mapped = lambda: visual.mapped
        for name in ('cancel', 'suspend', 'resume', 'animate', 'advance'):
            setattr(visual, name, lambda *args, name=name: getattr(EditorVisual, name)(visual, *args))
        return visual

    def frame(self, visual, seconds):
        callback = visual.callbacks[visual.tick]
        return callback(visual, SimpleNamespace(get_frame_time=lambda: int(seconds*1_000_000)))

    def test_remap_at_first_frame_and_mid_animation_preserves_completion(self):
        from unittest.mock import Mock
        for elapsed in (0, .2):
            with self.subTest(elapsed=elapsed):
                v = self.visual()
                done = Mock()
                v.animate(True, done)
                self.frame(v, 1)
                self.frame(v, 1+elapsed)
                progress = v.progress
                v.mapped = False
                v.suspend()
                v.suspend()  # Both unmap and unrealize can arrive.
                self.assertEqual(v.tick, 0)
                self.assertIs(v.finished, done)
                v.mapped = True
                v.resume()
                v.resume()
                self.assertEqual(len(v.callbacks), 1)
                self.frame(v, 100)  # Replacement frame clock has a new origin.
                self.assertAlmostEqual(v.progress, progress)
                self.frame(v, 101)
                self.assertEqual(v.progress, 1)
                self.assertFalse(v._running)
                done.assert_called_once()
                v.resume()
                self.assertEqual(v.tick, 0)

    def test_old_exit_callback_cannot_hide_reopened_editor(self):
        from unittest.mock import Mock
        v = self.visual()
        hide, ready = Mock(), Mock()
        v.animate(False, hide)
        old = v.callbacks[v.tick]
        self.frame(v, 1)
        v.animate(True, ready)
        self.assertFalse(old(v, SimpleNamespace(get_frame_time=lambda: 10_000_000)))
        hide.assert_not_called()
        self.frame(v, 10)  # Exit had not advanced: reopening is already complete.
        self.assertEqual(v.tick, 0)
        ready.assert_called_once()

    def test_retarget_preserves_wave_and_reverses_without_restarting(self):
        v = self.visual()
        v.animate(True)
        self.frame(v, 1)
        self.frame(v, 1.1)
        before = v.progress
        v.animate(False)
        self.assertEqual(v.progress, before)
        self.frame(v, 2)
        self.frame(v, 2.02)
        self.assertLess(v.progress, before)
        before = v.progress
        v.animate(True)
        self.assertEqual(v.progress, before)
        self.frame(v, 3)
        self.frame(v, 3.02)
        self.assertGreater(v.progress, before)

    def test_exit_remap_and_explicit_cancel(self):
        from unittest.mock import Mock
        v = self.visual()
        done = Mock()
        v.animate(False, done)
        self.frame(v, 1)
        self.frame(v, 1.2)
        v.suspend()
        v.resume()
        self.assertTrue(v.exiting)
        self.frame(v, 3)
        self.frame(v, 4)
        done.assert_called_once()
        v.animate(True, done)
        v.cancel()
        v.resume()
        self.assertFalse(v._running)
        self.assertEqual(v.tick, 0)
        self.assertIsNone(v.finished)


class RippleGeometryTests(unittest.TestCase):
    def test_displacement_settles_and_never_changes_source_geometry(self):
        grid = (60, 60, 80, 80)
        for exiting in (False, True):
            for progress in (0, 1):
                x, y, height = wave_sample(100, 100, grid, 20, progress, exiting)
                self.assertAlmostEqual(x, 100)
                self.assertAlmostEqual(y, 100)
                self.assertAlmostEqual(height, 0)
        samples = [wave_sample(120, 100, grid, 20, n/100)[2] for n in range(100)]
        self.assertGreater(max(samples), .1)
        self.assertLess(min(samples), -.01)
        self.assertEqual(grid, (60, 60, 80, 80))

    def test_view_face_replaces_grid_alpha(self):
        visual = EditorVisualTests().visual()
        visual.accent = (1., .4, .2)
        def pixel(views):
            visual.geometry = lambda: ((60,60,80,80), views)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
            EditorVisual.draw_lines(visual, None, cairo.Context(surface), 200, 200)
            surface.flush()
            start = 110*surface.get_stride()+110*4
            return bytes(surface.get_data()[start:start+4])
        grid = pixel(())
        view = pixel(((100,100,20,20),))
        self.assertEqual(grid[3], view[3])
        self.assertGreater(grid[3], 0)
        self.assertNotEqual(grid[:3], view[:3])
