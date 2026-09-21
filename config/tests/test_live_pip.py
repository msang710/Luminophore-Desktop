from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch
import json
from contextlib import nullcontext
from luminophore_shell.live_pip import LivePipClient, LivePipError, initial_placement, command_wire
from luminophore_shell.hyprland import HyprlandError
from luminophore_shell.capture_selection import CaptureSelection
from luminophore_shell.live_capture import LiveCaptureBackend

class PlacementTests(TestCase):
    def test_rightmost_monitor_and_shared_widget_margin(self):
        monitors=[NS(name='DP-9',x=-1920,y=-100,width=1920,height=1080),NS(name='DP-1',x=0,y=0,width=2560,height=1440)]
        p=initial_placement(monitors,(10,20,720,400),32)
        self.assertEqual((p.output,p.x,p.y,p.width,p.height),('DP-1',2168,1208,360,200))
    def test_portrait_small_output_preserves_aspect(self):
        p=initial_placement([NS(name='DP-1',x=-300,y=-200,width=300,height=400)],(0,0,500,1000),24)
        self.assertLessEqual(p.height,352);self.assertAlmostEqual(p.width/p.height,.5,places=2)
        self.assertEqual(p.y+p.height,176)
    def test_invalid_crop(self):
        with self.assertRaises(LivePipError): initial_placement([], (0,0,0,0),24)

class SelectionTests(TestCase):
    def test_button_and_negative_fractional_geometry(self):
        for button,mode in [(1,'screenshot'),(3,'pip')]:
            s=CaptureSelection();self.assertTrue(s.begin(button,-10.2,12.4))
            self.assertEqual(s.finish(-20.5,40.1),{'mode':mode,'rect':(-21,12,11,29)})
            self.assertIsNone(s.finish(0,0))
    def test_cancel_and_extra_press(self):
        s=CaptureSelection();s.begin(3,0,0);s.update(100,100)
        self.assertFalse(s.begin(1,0,0));self.assertIsNone(s.finish(100,100))
        s.begin(3,0,0);s.cancel();self.assertIsNone(s.finish(100,100))

class ClientTests(TestCase):
    def client(self):
        c=LivePipClient(Mock());c.instance='instance';return c
    def test_wire_rejects_invalid_tokens(self):
        for value in ['x"}); os.exit()',float('nan'),True]:
            with self.assertRaises(LivePipError): command_wire('resolve','request','instance',x=value,y=0,width=30,height=40)
    def test_lost_create_response_queries_without_replay(self):
        c=self.client();p=initial_placement([NS(name='DP-1',x=0,y=0,width=1920,height=1080)],(0,0,600,400),24)
        reply=json.dumps(dict(request=c.request,instance=c.instance,status='applied',id='1'))
        c.client._run.side_effect=[HyprlandError('lost'),reply,reply]
        self.assertEqual(c.create(p)['id'],'1');c.create(p)
        calls=[a.args[1] for a in c.client._run.call_args_list]
        self.assertEqual(sum(a.startswith('create ') for a in calls),1)
        self.assertEqual(sum(a.startswith('result ') for a in calls),2)
        self.assertTrue(all(a.args[0] == 'luminophorepipcommand' for a in c.client._run.call_args_list))

    def test_native_wire_rejects_extra_fields_and_invalid_coordinates(self):
        from luminophore_shell.live_pip import command_wire
        self.assertEqual(command_wire('resolve','req','instance',x=-1.5,y=0,width=30,height=40), 'resolve req instance -1.5 0 30 40')
        for fields in ({'x': 1}, {'x': 1, 'y': 0, 'width': 0, 'height': 40}, {'x': True, 'y': 0, 'width': 30, 'height': 40}):
            with self.assertRaises(LivePipError): command_wire('resolve','req','instance',**fields)
        with self.assertRaises(LivePipError): command_wire('begin','req','instance',x=1)
    def test_old_instance_response_rejected(self):
        c=self.client();c.client._run.return_value=json.dumps(dict(request=c.request,instance='old',status='applied'))
        with self.assertRaises(LivePipError): c.call('result')

class CaptureTests(TestCase):
    def test_selector_resolves_package_outside_shell_directory(self):
        import os
        import subprocess
        import tempfile
        def run_selector(argv, **kwargs):
            env = dict(os.environ)
            env.pop('PYTHONPATH', None)
            probe = subprocess.run(
                [argv[0], '-c', 'import importlib.util; assert importlib.util.find_spec("luminophore_shell.capture_selector")'],
                env=env, **kwargs)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            return NS(returncode=0, stdout='null')
        with tempfile.TemporaryDirectory() as directory:
            original = os.getcwd()
            try:
                os.chdir(directory)
                backend = LiveCaptureBackend(pip=Mock(), runner=run_selector)
                self.assertIsNone(backend.select())
            finally:
                os.chdir(original)

    def backend(self, mode='pip'):
        pip=Mock();pip.resolve.return_value=(0,0,600,400)
        pip.monitors.return_value=[NS(name='DP-1',x=0,y=0,width=1920,height=1080)]
        b=LiveCaptureBackend(pip=pip)
        b._capture_lock=Mock(return_value=nullcontext())
        b._preflight=Mock(return_value={'hyprpicker':'freeze','grim':'grim','wl-copy':'copy','xdg-user-dir':'pictures'})
        b.select=Mock(return_value={'mode':mode,'rect':[10,20,60,40]})
        b._start_freezer=Mock(return_value='freezer');b._stop_freezer=Mock(return_value=True)
        b._render_png=Mock(return_value=b'png');b._copy_png=Mock()
        return b
    @patch('luminophore_shell.live_capture.load_config',return_value=NS(layout=NS(edge_margin=24)))
    def test_freezer_stopped_before_create_and_no_clipboard(self,_):
        b=self.backend();order=[]
        b._stop_freezer.side_effect=lambda p:order.append(('stop',p)) or True
        b.pip.create.side_effect=lambda p:order.append(('create',p))
        self.assertEqual(b.capture_region(save=False,freeze=True).category,'pip_created')
        self.assertEqual(order[0],('stop','freezer'));self.assertEqual(order[1][0],'create')
        b._copy_png.assert_not_called();b.pip.cancel.assert_called_once()
    def test_left_button_still_copies_capture(self):
        b=self.backend('screenshot');self.assertEqual(b.capture_region(save=False,freeze=True).category,'copied')
        b._render_png.assert_called_once_with('grim','10,20 60x40');b._copy_png.assert_called_once_with('copy',b'png')
        b.pip.create.assert_not_called()
    def test_cancel_always_releases_freeze(self):
        b=self.backend();b.select.return_value=None
        self.assertEqual(b.capture_region(save=False,freeze=True).category,'cancelled')
        b._stop_freezer.assert_called_once_with('freezer');b.pip.cancel.assert_called_once();b.pip.create.assert_not_called()
    def test_failed_resolve_cleans_without_create(self):
        from luminophore_shell.capture import CaptureError
        b=self.backend();b.pip.resolve.side_effect=LivePipError('stale')
        with self.assertRaises(CaptureError):b.capture_region(save=False,freeze=True)
        b.pip.create.assert_not_called();b._stop_freezer.assert_called_once_with('freezer');b.pip.cancel.assert_called_once()

class OutputCapabilityTests(TestCase):
    def test_native_logical_bounds_used_verbatim(self):
        c=LivePipClient(Mock());c.instance='current'
        c.client.query.return_value={'instance':'current','outputs':[dict(name='DP-1',x=-960,y=0,width=960,height=540)]}
        p=initial_placement(c.monitors(),(0,0,720,400),24)
        self.assertEqual((p.x,p.y),(-384,316))
    def test_changed_instance_cannot_retarget_output(self):
        c=LivePipClient(Mock());c.instance='current';c.client.query.return_value={'instance':'old','outputs':[]}
        with self.assertRaises(LivePipError):c.monitors()
    def test_tiny_crop_has_usable_frame(self):
        p=initial_placement([NS(name='DP-1',x=0,y=0,width=1920,height=1080)],(0,0,2,2),24)
        self.assertEqual((p.width,p.height),(96,96))
