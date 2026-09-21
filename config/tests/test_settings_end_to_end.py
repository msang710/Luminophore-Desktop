"""Real service + disk + settings frontend integration; display consumers are
substituted here. The isolated compositor/GTK acceptance run is separate.
"""
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from luminophore_shell.settings_service import SettingsServiceHost
from luminophore_shell.settings_generation import fixture_store, edit_candidate
from luminophore_shell.__main__ import _SettingsIpcFacade
from luminophore_shell.settings_app import SettingsAppModel
from luminophore_shell.app import LuminophoreShellApplication

ROOT = Path(__file__).resolve().parents[2]
CPP = '#include "SettingsService.hpp"\n#include <iostream>\nusing namespace Luminophore::Settings;\nint main(int argc,char**argv) {\n try {\n  if(argc!=2)return 2;\n  CSettingsService service(argv[1],[](const std::string& epoch,const SGeneration& boot) {\n    auto actual=std::make_shared<Snapshot>(boot.values);\n    return std::make_shared<CSettingsParticipant>(epoch,boot.id,boot.values,[actual](const Snapshot& s){*actual=s;},[actual]{return *actual;});\n  });\n  std::string line;while(std::getline(std::cin,line))std::cout<<service.request(line)<<std::endl;\n } catch(const std::exception& e) {std::cerr<<e.what();return 1;}\n}\n'

class SettingsEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = TemporaryDirectory(); cls.addClassCleanup(cls.build.cleanup)
        root = Path(cls.build.name); source = root/'probe.cpp'; source.write_text(CPP)
        native = ROOT/'compositor/src/config/luminophore'
        cls.binary = root/'probe'
        files = ('GeneratedSettings','SettingsParticipant','JointSettingsMember','AsyncJointSettings',
                 'SettingsCommandProtocol','SettingsService','SettingsGeneration','MonitorSettings','DesktopSettings')
        result = subprocess.run(['g++','-std=c++23','-I',str(native),str(source),
            *[str(native/(name+'.cpp')) for name in files],'-ltomlplusplus','-lcrypto','-luuid','-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode: raise RuntimeError(result.stderr)

    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = {name:str(Path(self.temp.name)/folder) for name,folder in
               [('XDG_CONFIG_HOME','config'),('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache')]}
        env['LUMINOPHORE_SETTINGS_FIXTURE']='1'
        self.environment = patch.dict(os.environ,env); self.environment.start(); self.addCleanup(self.environment.stop)
        self.store = fixture_store(); self.queue = Queue(); self.actual = None; self.reject = False
        self.start_components(); self.addCleanup(self.stop_components)

    def start_components(self):
        self.process = subprocess.Popen([str(self.binary),str(self.store.root)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.host = SettingsServiceHost(SimpleNamespace(_run=self.remote),self.queue.put,self.apply,self.verify)
        self.pump(lambda:self.host.snapshot()['ok'])
        app = SimpleNamespace(_settings_fixture_host=self.host, appearance_service=None,
            system_theme_controller=SimpleNamespace(targets=SimpleNamespace(selected_targets=lambda: [])))
        app._settings_status = lambda request_id='',recover=False:self.host.status(request_id,recover)
        client = SimpleNamespace(request=lambda request:LuminophoreShellApplication._dispatch(app,request))
        self.model = SettingsAppModel(_SettingsIpcFacade(client)); self.model.activate()

    def stop_components(self):
        self.host.close(); self.host._thread.join(3)
        self.process.stdin.close()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process.stdout.close();self.process.stderr.close()

    def remote(self, endpoint, wire):
        self.assertEqual(endpoint,'luminophoresettingsservice')
        self.process.stdin.write(wire+'\n');self.process.stdin.flush()
        value=self.process.stdout.readline()
        if not value:raise RuntimeError('native probe stopped')
        return value

    def apply(self, config):
        self.actual = config
        if self.reject and config.layout.panel_height == 64: raise RuntimeError('partial Shell apply')

    def verify(self, config):
        self.assertEqual(self.actual,config)

    def pump(self, condition):
        for _ in range(500):
            try:self.queue.get(timeout=.02)()
            except Empty:pass
            if condition():return
        self.fail(str((self.host._error,self.host.status())))

    def save(self):
        self.model.apply_settings({'compositor.border_size':9,'layout.panel_height':64})
        self.pump(lambda:self.host.status().get('category')!='completion_unknown')
        self.model.refresh_settings_status()

    def test_frontend_save_through_product_dispatch_and_both_receipts(self):
        self.save()
        self.assertEqual(self.host.status()['category'],'ok')
        self.assertEqual(self.actual.layout.panel_height,64)
        self.assertEqual(self.actual.compositor.border_size,9)
        self.assertEqual(self.model.snapshot.digest,self.store.current().id)

    def test_partial_shell_apply_restores_before_abandon(self):
        before=self.store.current(); self.reject=True; self.save()
        self.assertEqual(self.host.status()['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(self.actual.layout.panel_height,44)
        self.assertEqual(self.store.current().id,before.id)
        self.assertFalse((self.store.root/'pending.json').exists())

    def test_restart_uses_completed_and_ignores_pending(self):
        self.save(); completed=self.store.current()
        pending=edit_candidate(self.store,completed,{'layout.panel_height':72})
        self.store.prepare(pending.documents,completed.id)
        self.stop_components(); self.start_components()
        self.assertEqual(self.actual.layout.panel_height,64)
        self.assertEqual(self.host.snapshot()['digest'],completed.id)

    def test_invalid_candidate_never_changes_completed_files(self):
        before=self.store.current()
        self.model.apply_settings({'layout.panel_height':True})
        self.pump(lambda:self.host.status().get('category')!='completion_unknown')
        self.assertEqual(self.host.status()['category'],'validation')
        self.assertEqual(self.store.current().documents,before.documents)
        self.assertFalse((self.store.root/'pending.json').exists())

    def test_candidate_editor_preserves_comments_and_other_files(self):
        before=self.store.current()
        from luminophore_shell.settings_store import Generation
        texts=dict(before.documents);texts['settings.toml']+='\n[layout]\npanel_height = 44 # retain me\n'
        commented=self.store._candidate(texts)
        candidate=edit_candidate(self.store,commented,{'layout.panel_height':64})
        self.assertIn('panel_height = 64 # retain me',candidate.documents['settings.toml'])
        for name in texts:
            if name!='settings.toml':self.assertEqual(texts[name],candidate.documents[name])

    def test_duplicate_save_id_cannot_change_payload(self):
        self.save()
        original = dict(self.host._last_request)
        response = self.host.apply({**original, 'changes': {'layout.panel_height': 72}})
        self.assertEqual(response['category'], 'conflict')
        self.assertEqual(self.store.current().id, self.model.snapshot.digest)

    def test_restore_retry_requires_new_coordinator_ticket(self):
        from luminophore_shell.settings_participant import ShellSettingsParticipant
        base = self.store.current()
        calls = []
        def apply(config):
            calls.append(config)
            if len(calls) == 1: raise RuntimeError('temporary destination failure')
        participant = ShellSettingsParticipant(self.store, base, apply, lambda config: None)
        fields = ['1', 'epoch', '1', '1', base.id, base.id, 'shell', 'prepare']
        self.assertEqual(participant.execute(fields), 'ok')
        fields[3], fields[7] = '2', 'restore'
        self.assertEqual(participant.execute(fields), 'failed')
        self.assertEqual(participant.execute(fields), 'failed')
        self.assertEqual(len(calls), 1)
        fields[3] = '3'
        self.assertEqual(participant.execute(fields), 'ok')
        self.assertEqual(len(calls), 2)

    def test_native_cold_start_rejects_integer_for_float(self):
        from luminophore_shell.settings_store import _digest
        self.stop_components()
        base=self.store.current();documents=dict(base.documents)
        documents['settings.toml']+='\n[motion]\nspeed = 1\n'
        bad=_digest(documents);directory=self.store.generations/bad;directory.mkdir()
        for name,text in documents.items():(directory/name).write_text(text)
        pointer=self.store.root/'completed.json'
        pointer.write_text(json.dumps({'generation':bad}))
        try:
            result=subprocess.run([str(self.binary),str(self.store.root)],input='',capture_output=True,text=True,timeout=3)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('settings type mismatch',result.stderr)
        finally:pointer.write_text(json.dumps({'generation':base.id}))


    def test_live_monitor_change_is_rejected_before_publication(self):
        base=self.store.current()
        original=edit_candidate
        def candidate(store,generation,changes):
            edited=original(store,generation,changes);documents=dict(edited.documents)
            documents['monitors.toml']+='[outputs.DP-1]\nscale=1.25\n'
            return store._candidate(documents)
        with patch('luminophore_shell.settings_service.edit_candidate',side_effect=candidate):
            self.model.apply_settings({'compositor.border_size':9})
            self.pump(lambda:self.host.status().get('category')!='completion_unknown')
        self.assertEqual(self.host.status()['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(self.store.current().id,base.id)
        self.assertEqual(self.actual.layout.panel_height,44)
        self.assertFalse((self.store.root/'pending.json').exists())


def run_isolated_acceptance(recovery=False, expanded=False, native_manager=False, monitors=False, confirmation=False, devices=False, workspace_recovery=False):
    """Requires a private dbus-run-session; never uses the installed session."""
    import os,sys,json,socket,subprocess,time,tempfile,signal,hashlib
    from pathlib import Path
    root=ROOT.parent
    build=root/'.luminophore-build/rename'
    test=Path(tempfile.mkdtemp(prefix='lb1-'))
    print('fixture',test,flush=True)
    env=dict(os.environ)
    for name in ('WAYLAND_DISPLAY','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE'):
        env.pop(name,None)
    for key,folder in [('XDG_CONFIG_HOME','config'),('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','r')]:
        p=test/folder;p.mkdir(mode=0o700);env[key]=str(p)
    env.update(LUMINOPHORE_SETTINGS_FIXTURE='1',LUMINOPHORE_COMPOSITOR='1',LUMINOPHORE_NESTED_ONLY='1',GCOV_PREFIX=str(test/'gcov'),AQ_NO_MODIFIERS='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root/'.luminophore-migration/config'),PATH=str(build/'hyprctl')+':'+env['PATH'])
    config=test/'hyprland.lua';config.write_text('hl.config({debug={disable_logs=false, enable_stdout_logs=true}})\n'+'hl.monitor({output="",mode="1280x720@60",position="0x0",scale=1})\n')
    if native_manager:
        config.unlink()
        config=test/'unused.toml'
        env['LUMINOPHORE_NATIVE_CONFIG']='1'
    if monitors:
        assert native_manager
        from luminophore_shell.settings_bundle import settings_store
        from luminophore_shell.settings_store import SettingsPaths, FILES
        store=settings_store(SettingsPaths.current(env))
        documents={name:'schema_version = 1\n' for name in FILES}
        documents['monitors.toml']+='[outputs.WAYLAND-1]\nmode="1280x720@60"\nscale=1.25\ntransform=1\nposition=[-500,100]\n[outputs.DISCONNECTED]\nmode="1920x1080@60"\n'
        generation=store.prepare(documents,'');store.publish(generation.id,'')
    runtime_id=hashlib.sha256((build/'hyprctl/hyprctl').read_bytes()).hexdigest()
    runtime=test/runtime_id;(runtime/'bin').mkdir(parents=True)
    (runtime/'bin/hyprctl').symlink_to(build/'hyprctl/hyprctl')
    (runtime/'build-manifest.json').write_text(json.dumps(dict(schema='luminophore-runtime-artifact/v1',generation=runtime_id,binaries={'hyprctl':{'sha256':runtime_id}})))
    env['LUMINOPHORE_COMPOSITOR_ROOT']=str(runtime)
    env['GSETTINGS_BACKEND']='memory';env['GTK_USE_PORTAL']='0';env['GTK_A11Y']='none';env['PYTHONFAULTHANDLER']='1'
    subprocess.run(['dbus-update-activation-environment','XDG_RUNTIME_DIR','XDG_CONFIG_HOME','XDG_STATE_HOME','XDG_CACHE_HOME','GSETTINGS_BACKEND'],env=env,check=True)
    processes=[]
    # Test-only process gates: real Store and GTK consumers still execute.
    # Marker + SIGSTOP makes the actual crash point deterministic.
    hooks=test/'hooks';hooks.mkdir()
    (hooks/'sitecustomize.py').write_text("""
import os, signal
from pathlib import Path
from luminophore_shell.settings_store import SettingsStore
root=Path(os.environ['LUMINOPHORE_RECOVERY_TEST_ROOT'])
def wrap(name):
    original=getattr(SettingsStore,name)
    def call(self,*args,**kwargs):
        def gate(phase):
            control=root/'gate'
            if control.exists() and control.read_text()==name+'.'+phase:
                (root/'reached').write_text(name+'.'+phase)
                os.kill(os.getpid(),signal.SIGSTOP)
        gate('before')
        result=original(self,*args,**kwargs)
        gate('after')
        return result
    setattr(SettingsStore,name,call)
for name in ('prepare','publish'):wrap(name)
from luminophore_shell.settings_participant import ShellSettingsParticipant
original_execute=ShellSettingsParticipant.execute
def execute(self,fields):
    control=root/'gate'
    phase=control.read_text() if control.exists() else ''
    if phase=='restore.before' and fields[7]=='restore':
        (root/'reached').write_text(phase);os.kill(os.getpid(),signal.SIGSTOP)
    result=original_execute(self,fields)
    if phase.startswith('restore.') and fields[7]=='apply' and result=='ok':return 'failed'
    if phase=='restore.after' and fields[7]=='restore':
        (root/'reached').write_text(phase);os.kill(os.getpid(),signal.SIGSTOP)
    return result
ShellSettingsParticipant.execute=execute
""")
    if recovery:
        env['PYTHONPATH']=str(hooks)+':'+env['PYTHONPATH']
        env['LUMINOPHORE_RECOVERY_TEST_ROOT']=str(test)
    def ctl(*args):
        return subprocess.check_output([str(build/'hyprctl/hyprctl'),*args],env=env,text=True,stderr=subprocess.PIPE,timeout=3)
    def ipc(**request):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(3);s.connect(str(test/'r/luminophore-shell/control.sock'));s.sendall(json.dumps(request).encode()+b'\n')
            data=b''
            while not data.endswith(b'\n'):data+=s.recv(65536)
            return json.loads(data)
    def wait(fn,seconds=25):
        end=time.monotonic()+seconds;error=None
        while time.monotonic()<end:
            try:
                value=fn()
                if value:return value
            except Exception as e:error=e
            if any(p.poll() is not None for p in processes):raise RuntimeError('fixture component exited '+str([(p.pid,p.poll()) for p in processes]))
            time.sleep(.15)
        raise RuntimeError('timeout: '+str(error))
    def start(round):
        parent = subprocess.Popen(['kwin_wayland','--virtual','--socket','b1-parent','--width','1280','--height','720','--no-lockscreen'],env=env,start_new_session=True,stdout=(test/f'parent-{round}.log').open('w'),stderr=subprocess.STDOUT)
        processes.append(parent)
        def parent_socket():
            return 'b1-parent' if (test/'r/b1-parent').is_socket() else None
        env['WAYLAND_DISPLAY'] = wait(parent_socket)
        compositor=subprocess.Popen([str(build/'Hyprland')]+([] if native_manager else ['--config',str(config)]),env=env,start_new_session=True,stdout=(test/f'compositor-{round}.log').open('w'),stderr=subprocess.STDOUT);processes.append(compositor)
        instances=wait(lambda:json.loads(ctl('-j','instances')))
        instance=next(i for i in instances if i['pid']==compositor.pid)
        env['HYPRLAND_INSTANCE_SIGNATURE']=instance['instance'];env['WAYLAND_DISPLAY']=instance['wl_socket']
        wait(lambda:json.loads(ctl('luminophoresettingsservice','status')).get('state')=='idle')
        wait(lambda:json.loads(ctl('-j','monitors')))
        if monitors:
            actual=next(m for m in json.loads(ctl('-j','monitors')) if m['name']=='WAYLAND-1')
            assert actual['scale']==1.25 and actual['transform']==1, actual
            assert (actual['x'],actual['y'])==(-500,100), actual
            assert (actual['width'],actual['height'])==(1280,720), actual
            print('monitor readback',actual['name'],actual['width'],actual['height'],actual['scale'],actual['transform'],actual['x'],actual['y'],flush=True)

        if native_manager:
            assert not config.exists(), 'Native boot must not generate or parse a legacy config'
            spaces=json.loads(ctl('-j','workspaces'))
            assert spaces and all(w['name'].startswith('luminophore-base-') or w['name']=='special:luminophore-spotify' for w in spaces), spaces
            for monitor in json.loads(ctl('-j','monitors')):
                assert monitor['activeWorkspace']['name'].startswith('luminophore-base-'), monitor
            for command in ('workspace 2', 'movetoworkspace 2', 'renameworkspace 1 code'):
                result=subprocess.run([str(build/'hyprctl/hyprctl'),'dispatch',command],env=env,text=True,capture_output=True,timeout=3)
                assert result.returncode != 0 or result.stdout.strip() != 'ok', (command,result.stdout)
            assert json.loads(ctl('-j','workspaces')) == spaces

            assert ctl('eval', 'return 1').strip() == 'eval is only supported with the lua config manager'

        if workspace_recovery:
            from tests.workspace_recovery_fixture import verify_workspace_recovery
            verify_workspace_recovery(ctl, wait, env, test, processes)

        shell=subprocess.Popen([sys.executable,str(root/'.luminophore-migration/config/luminophore-shell'),'daemon'],env=env,start_new_session=True,stdout=(test/f'shell-{round}.log').open('w'),stderr=subprocess.STDOUT);processes.append(shell)
        def ready():
            result=ipc(command='settings-snapshot')
            if not result.get('ok'):(test/'last-status.json').write_text(json.dumps(result))
            return result if result.get('ok') else None
        return wait(ready,40)
    def stop():
        for p in reversed(processes):
            if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
        processes.clear()
        env.pop('HYPRLAND_INSTANCE_SIGNATURE',None);env.pop('WAYLAND_DISPLAY',None)
    try:
        before=start(1);print('initial',before['digest'],flush=True)
        os.environ.update(env);sys.path.insert(0,str(root/'.luminophore-migration/config'))
        from luminophore_shell.settings_app import SettingsAppModel
        from luminophore_shell.__main__ import _SettingsIpcFacade
        from luminophore_shell.ipc import IpcClient
        model=SettingsAppModel(_SettingsIpcFacade(IpcClient()));model.activate()
        if not native_manager:
            from tests.test_keymap_snapshot import SOURCE
            unsupported_keymap=test/'legacy-import.xkb'
            unsupported_keymap.write_text(SOURCE)
            old_border=json.loads(ctl('-j','getoption','general:border_size'))['int']
            old_file=json.loads(ctl('-j','getoption','input:kb_file'))['str']
            model.apply_settings({'input.kb_file':str(unsupported_keymap),'compositor.border_size':19})
            rejected_request=ipc(command='settings-status')['request_id']
            def rejected():
                result=ipc(command='settings-status',request_id=rejected_request)
                return result if result.get('found') and result.get('category')!='completion_unknown' else None
            result=wait(rejected,40)
            assert result['category']!='ok',result
            assert ipc(command='settings-snapshot')['digest']==before['digest'],result
            assert json.loads(ctl('-j','getoption','general:border_size'))['int']==old_border
            assert json.loads(ctl('-j','getoption','input:kb_file'))['str']==old_file
            model.activate()
            print('unsupported legacy import rejected, generation and consumers preserved',flush=True)
        changes={'compositor.border_size':9,'layout.panel_height':64}
        if expanded:
            changes.update({'compositor.rounding':12,'compositor.active_opacity':0.85,'compositor.inactive_opacity':0.8,
                            'motion.enabled':False,'motion.speed':1.25,'visual.intensity':1.3})
        input_values={'accel_profile':'custom 0.5 0 1 3','scroll_points':'0.2 0 1','focus_on_close':2,'float_switch_override_focus':2,'follow_mouse':2,'follow_mouse_threshold':0.3,'mouse_refocus':False,'follow_mouse_shrink':12,'off_window_axis_events':0,'emulate_discrete_scroll':2,'numlock_by_default':True,'resolve_binds_by_sym':True,'scroll_method':'on_button_down','scroll_button':274,'scroll_button_lock':True,'rotation':90,'repeat_rate':40,'repeat_delay':350,'sensitivity':0.42,'scroll_factor':1.3,
                      'natural_scroll':True,'left_handed':True,'force_no_accel':True,
                      'kb_layout':'de','kb_model':'pc105','kb_variant':'','kb_options':'caps:escape','kb_rules':'evdev'} if native_manager else {}
        using_import = native_manager and (expanded or recovery)
        if using_import:
            from tests.test_keymap_snapshot import SOURCE
            from luminophore_shell.keymap_snapshot import compile_keymap
            keymap_source=test/'imported-keyboard.xkb'
            keymap_source.write_text(SOURCE)
            expected_import=str(keymap_source)+'\n'+compile_keymap(SOURCE)
            input_values['kb_file']=str(keymap_source)
        changes.update({'input.'+key:value for key,value in input_values.items()})
        touchpad_values={'disable_while_typing':False,'natural_scroll':True,'scroll_factor':1.3,
                         'middle_button_emulation':True,'tap_button_map':'lmr','clickfinger_behavior':True,
                         'tap_to_click':False,'drag_lock':2,'tap_and_drag':False,'flip_x':True,'flip_y':True,'drag_3fg':2} if native_manager else {}
        changes.update({'touchpad.'+key:value for key,value in touchpad_values.items()})
        absolute_values={'touchdevice.transform':2,'touchdevice.output':'[[Auto]]','touchdevice.enabled':True,
                         'virtualkeyboard.share_states':1,'virtualkeyboard.release_pressed_on_close':True,
                         'tablet.transform':3,'tablet.output':'DP-9','tablet.relative_input':True,
                         'tablet.left_handed':True,'tablet.absolute_region_position':True,
                         'tablet.region_position_x':0.3,'tablet.region_position_y':0.7,
                         'tablet.region_size_x':120.5,'tablet.region_size_y':80.25,
                         'tablet.active_area_size_x':100.5,'tablet.active_area_size_y':70.25,
                         'tablet.active_area_position_x':1.25,'tablet.active_area_position_y':2.5,
                         'tablettool.eraser_button_mode':1,'tablettool.eraser_button_override':2,
                         'tablettool.pressure_range_min':0.1,'tablettool.pressure_range_max':0.9} if native_manager else {}
        changes.update(absolute_values)
        interaction_values={'drag_threshold': 23, 'scroll_event_delay': 23, 'cursor_inactive_timeout': 1.3, 'cursor_no_warps': True, 'cursor_persistent_warps': True, 'cursor_hide_on_key_press': True, 'cursor_hide_on_touch': False, 'cursor_hide_on_tablet': True, 'cursor_warp_back_after_non_mouse_input': True, 'resize_on_border': True, 'extend_border_grab_area': 23, 'resize_on_border_inner_area': 23, 'hover_icon_on_border': False, 'resize_corner': 2, 'close_gesture_timeout': 550} if native_manager else {}
        interaction_native={'drag_threshold': 'binds:drag_threshold', 'scroll_event_delay': 'binds:scroll_event_delay', 'cursor_inactive_timeout': 'cursor:inactive_timeout', 'cursor_no_warps': 'cursor:no_warps', 'cursor_persistent_warps': 'cursor:persistent_warps', 'cursor_hide_on_key_press': 'cursor:hide_on_key_press', 'cursor_hide_on_touch': 'cursor:hide_on_touch', 'cursor_hide_on_tablet': 'cursor:hide_on_tablet', 'cursor_warp_back_after_non_mouse_input': 'cursor:warp_back_after_non_mouse_input', 'resize_on_border': 'general:resize_on_border', 'extend_border_grab_area': 'general:extend_border_grab_area', 'resize_on_border_inner_area': 'general:resize_on_border_inner_area', 'hover_icon_on_border': 'general:hover_icon_on_border', 'resize_corner': 'general:resize_corner', 'close_gesture_timeout': 'gestures:close_max_timeout'}
        changes.update({'input.'+key:value for key,value in interaction_values.items()})
        render_values={'fullscreen_opacity':0.73,'dim_inactive':True,'dim_modal':False,'dim_strength':0.31,
                       'dim_around':0.27,'blur_popups':True,'blur_input_methods':True,
                       'render_unfocused_fps':37,'zoom_rigid':True,'zoom_detached_camera':False,'zoom_disable_aa':True,'pointer_focus_output':False,'fullscreen_focus_policy':1,
                       'fullscreen_after_close':True,'xwayland_nearest_neighbor':False,'allow_tearing':True} if native_manager else {}
        render_native={'fullscreen_opacity':'decoration:fullscreen_opacity','dim_inactive':'decoration:dim_inactive',
                       'dim_modal':'decoration:dim_modal','dim_strength':'decoration:dim_strength','dim_around':'decoration:dim_around',
                       'blur_popups':'decoration:blur:popups','blur_input_methods':'decoration:blur:input_methods',
                       'render_unfocused_fps':'misc:render_unfocused_fps','zoom_rigid':'cursor:zoom_rigid',
                       'zoom_detached_camera':'cursor:zoom_detached_camera','zoom_disable_aa':'cursor:zoom_disable_aa',
                       'pointer_focus_output':'misc:mouse_move_focuses_monitor',
                       'fullscreen_focus_policy':'misc:on_focus_under_fullscreen',
                       'fullscreen_after_close':'misc:exit_window_retains_fullscreen',
                       'xwayland_nearest_neighbor':'xwayland:use_nearest_neighbor','allow_tearing':'general:allow_tearing'}
        remaining_values={'cursor_start_output': 'TEST-1', 'zoom_factor': 1.3, 'locale': 'en_US', 'font_family': 'Monospace', 'wake_on_key': True, 'wake_on_pointer': True, 'display_idle_minutes': 3, 'primary_selection': False, 'auto_hdr': 2, 'sdr_transfer': 'gamma22', 'icc_vcgt': False, 'xwayland_native_pixels': True, 'background_color': '80224466', 'shadow_enabled': False, 'shadow_range': 12, 'shadow_power': 2, 'shadow_sharp': True, 'shadow_color': 'ffffffff 0deg', 'shadow_inactive_color': 'ffffffff 0deg', 'shadow_scale': 0.83, 'shadow_offset_x': 2.5, 'shadow_offset_y': 4.5, 'float_gap_top': 2, 'float_gap_right': 3, 'float_gap_bottom': 4, 'float_gap_left': 5, 'color_management': False, 'lock_background': True, 'lock_blur': True} if native_manager else {}
        remaining_native={'cursor_start_output': 'cursor:default_monitor', 'zoom_factor': 'cursor:zoom_factor', 'locale': 'general:locale', 'font_family': 'misc:font_family', 'wake_on_key': 'misc:key_press_enables_dpms', 'wake_on_pointer': 'misc:mouse_move_enables_dpms', 'display_idle_minutes': 'misc:luminophore_monitor_idle_minutes', 'primary_selection': 'misc:middle_click_paste', 'auto_hdr': 'render:cm_auto_hdr', 'sdr_transfer': 'render:cm_sdr_eotf', 'icc_vcgt': 'render:icc_vcgt_enabled', 'xwayland_native_pixels': 'xwayland:force_zero_scaling', 'background_color': 'misc:background_color', 'shadow_enabled': 'decoration:shadow:enabled', 'shadow_range': 'decoration:shadow:range', 'shadow_power': 'decoration:shadow:render_power', 'shadow_sharp': 'decoration:shadow:sharp', 'shadow_color': 'decoration:shadow:color', 'shadow_inactive_color': 'decoration:shadow:color_inactive', 'shadow_scale': 'decoration:shadow:scale', 'shadow_offset_x': 'decoration:shadow:offset', 'shadow_offset_y': 'decoration:shadow:offset', 'float_gap_top': 'general:float_gaps', 'float_gap_right': 'general:float_gaps', 'float_gap_bottom': 'general:float_gaps', 'float_gap_left': 'general:float_gaps', 'color_management': 'luminophore:next_session_cm_enabled', 'lock_background': 'misc:session_lock_xray', 'lock_blur': 'misc:session_lock_blur'}
        changes.update({'compositor.'+key:value for key,value in remaining_values.items()})
        def verify_remaining(snapshot, expected):
            for key,value in expected.items():
                observed=json.loads(ctl('-j','getoption',remaining_native[key]))
                if key.startswith('float_gap_'):
                    index=('top','right','bottom','left').index(key.removeprefix('float_gap_'))
                    actual=int(observed['css'].split()[index])
                elif key.startswith('shadow_offset_'):
                    actual=observed['vec2'][int(key.endswith('_y'))]
                elif key in ('shadow_color','shadow_inactive_color'):
                    actual=observed['gradient']
                    if value=='inherit':
                        # Native inherited sentinel has a distinct negative color representation.
                        assert observed['set'] is False, observed
                        assert actual == 'ffffffff 0deg', observed
                        actual='inherit'
                    elif key=='shadow_inactive_color':
                        assert observed['set'] is True, observed
                elif key=='background_color':
                    actual=format(observed['int'],'08x')
                else:
                    actual=observed[{float:'float',int:'int',bool:'bool',str:'str'}[type(value)]]
                    if key=='cursor_start_output' and actual=='[[EMPTY]]':actual=''
                assert (abs(actual-value)<1e-6 if type(value) is float else actual==value),(key,value,observed)
                assert snapshot['values']['compositor.'+key]==value,(key,snapshot)
            if expected:print('remaining general native readback PASS',flush=True)
        changes.update({'compositor.'+key:value for key,value in render_values.items()})
        def verify_input(snapshot):
            verify_remaining(snapshot, remaining_values)
            for key,value in render_values.items():
                observed=json.loads(ctl('-j','getoption',render_native[key]))
                actual=observed[{float:'float',int:'int',bool:'bool'}[type(value)]]
                assert abs(actual-value)<1e-6,(key,observed)
                assert snapshot['values']['compositor.'+key]==value,(key,snapshot)
            if render_values: print('general render native readback PASS',flush=True)
            for key,value in interaction_values.items():
                observed=json.loads(ctl('-j','getoption',interaction_native[key]))
                actual=observed[{float:'float',int:'int',bool:'bool'}[type(value)]]
                assert abs(actual-value)<1e-6,(key,observed)
                assert snapshot['values']['input.'+key]==value,(key,snapshot)
            if interaction_values: print('general interaction native readback PASS',flush=True)
            for key,value in input_values.items():
                observed=json.loads(ctl('-j','getoption','input:'+key))
                effective=observed[{float:'float',int:'int',bool:'bool',str:'str'}[type(value)]]
                assert (effective==value if isinstance(value,str) else abs(effective-value)<1e-6),(key,observed)
                assert snapshot['values']['input.'+key]==value,(key,snapshot)
            for key,value in touchpad_values.items():
                native_key={'tap_to_click':'tap-to-click','tap_and_drag':'tap-and-drag'}.get(key,key)
                observed=json.loads(ctl('-j','getoption','input:touchpad:'+native_key))
                effective=observed[{float:'float',int:'int',bool:'bool',str:'str'}[type(value)]]
                assert (effective==value if isinstance(value,str) else abs(effective-value)<1e-6),(key,observed)
                assert snapshot['values']['touchpad.'+key]==value,(key,snapshot)
            for key,value in absolute_values.items():
                group,field=key.split('.')
                vector=group=='tablet' and field.endswith(('_x','_y'))
                native_field=field[:-2] if vector else field
                observed=json.loads(ctl('-j','getoption','input:'+group+':'+native_field))
                effective=observed['vec2'][0 if field.endswith('_x') else 1] if vector else observed[{float:'float',int:'int',bool:'bool',str:'str'}[type(value)]]
                assert (effective==value if isinstance(value,str) else abs(effective-value)<1e-6),(key,observed)
                assert snapshot['values'][key]==value,(key,snapshot)
            if absolute_values: print('absolute input native readback PASS',flush=True)
            if touchpad_values: print('touchpad native readback PASS',flush=True)
            if using_import:
                assert json.loads(ctl('-j','getoption','input:kb_snapshot'))['str']==expected_import
                print('immutable keymap readback PASS',flush=True)
            if input_values: print('input readback PASS',flush=True)
        model.apply_settings(changes)
        request=ipc(command='settings-status')
        print('requested',request,flush=True)
        def completed():
            s=ipc(command='settings-status',request_id=request['request_id'])
            return s if s.get('found') and s.get('category')!='completion_unknown' else None
        result=wait(completed,40);assert result['category']=='ok',result
        model.refresh_settings_status()
        assert model.snapshot.digest==result['digest']
        after=ipc(command='settings-snapshot');native=json.loads(ctl('-j','getoption','general:border_size'))
        assert native['int']==9,native
        assert after['values']['layout.panel_height']==64,after
        verify_input(after)
        if native_manager:
            assert json.loads(ctl('-j','getoption','render:cm_enabled'))['bool'] is True
            print('CM active state preserved until next session PASS',flush=True)
        if using_import: keymap_source.unlink()
        if expanded:
            assert json.loads(ctl('-j','getoption','decoration:rounding'))['int']==12
            assert abs(json.loads(ctl('-j','getoption','decoration:active_opacity'))['float']-.85)<1e-5
            for key,value in changes.items():assert after['values'][key]==value,(key,after)
        print('applied',result,native,flush=True)
        stop();restored=start(2)
        assert restored['digest']==after['digest'],restored
        assert json.loads(ctl('-j','getoption','general:border_size'))['int']==9
        assert restored['values']['layout.panel_height']==64
        verify_input(restored)
        if native_manager:
            assert json.loads(ctl('-j','getoption','render:cm_enabled'))['bool'] is False
            print('CM next-session activation PASS',flush=True)
        if expanded:
            assert json.loads(ctl('-j','getoption','decoration:rounding'))['int']==12
            for key,value in changes.items():assert restored['values'][key]==value,(key,restored)
        if devices:
            keyboards=json.loads(ctl('-j','devices'))['keyboards']
            assert keyboards, 'isolated backend supplied no keyboard'
            name=keyboards[0]['name']
            rules={name:{'numlock_by_default':False,'resolve_binds_by_sym':False,'kb_layout':'us','kb_variant':'dvorak','repeat_rate':47},
                   'disconnected-fixture-mouse':{'scroll_method':'no_scroll','scroll_button':275,'scroll_button_lock':False,'rotation':180,'region_position_x':0.31,'active_area_size_y':90.3,'pressure_range_min':0.2,'sensitivity':0.42,'left_handed':True,'tap_to_click':True,'tap_and_drag':True,'drag_lock':1,'tap_button_map':'lrm','disable_while_typing':True,'scroll_factor':0.42}}
            def change_device(fields, category='ok'):
                import uuid
                before=ipc(command='settings-snapshot')
                rid='devices-'+uuid.uuid4().hex
                accepted=ipc(command='settings-apply',request_id=rid,expected_digest=before['digest'],changes=fields)
                assert accepted.get('ok'),accepted
                def finished():
                    status=ipc(command='settings-status',request_id=rid)
                    return status if status.get('found') and status.get('category')!='completion_unknown' else None
                result=wait(finished,40)
                assert result['category']==category,result
                return ipc(command='settings-snapshot')
            def verify_device(snapshot, expected):
                keyboard=next(k for k in json.loads(ctl('-j','devices'))['keyboards'] if k['name']==name)
                assert keyboard['layout']==expected,keyboard
                assert snapshot['devices']==rules,snapshot
                assert keyboard['options']=='caps:escape',keyboard
            current=change_device({'devices':rules})
            verify_device(current,'us')
            stop();current=start(3)
            verify_device(current,'us')
            # Invalid XKB compilation must leave both the device and scalar domain intact.
            baseline=current
            invalid={**rules,name:{'kb_layout':'luminophore-no-such-layout'}}
            rejected=change_device({'devices':invalid,'input.repeat_delay':999},'runtime_apply_failed_rolled_back')
            assert rejected['digest']==baseline['digest'],rejected
            verify_device(rejected,'us')
            assert json.loads(ctl('-j','getoption','input:repeat_delay'))['int']==350
            rules={'disconnected-fixture-mouse':rules['disconnected-fixture-mouse']}
            current=change_device({'devices':rules})
            verify_device(current,'de')
            print('device overrides, restart, keymap rejection, fallback PASS',flush=True)
        if confirmation:
            assert monitors
            import uuid
            def display():
                m=next(m for m in json.loads(ctl('-j','monitors')) if m['name']=='WAYLAND-1')
                return m['scale'],m['transform'],m['x'],m['y']
            for action in ('invalid-scale','cancel','keep','timeout','shell-loss'):
                old_display=display();baseline=ipc(command='settings-snapshot')
                rid='monitor-'+uuid.uuid4().hex
                scale=1.5 if action=='invalid-scale' else (2.0 if old_display[0]!=2.0 else 1.25)
                rules={'WAYLAND-1':{'mode':'1280x720@60','scale':scale,'transform':0,'position':[-250,50]},
                       'DISCONNECTED':{'mode':'1920x1080@60'}}
                keyboard_name=json.loads(ctl('-j','devices'))['keyboards'][0]['name']
                old_keymap=json.loads(ctl('-j','devices'))['keyboards'][0]['layout']
                new_keymap='us' if old_keymap!='us' else 'de'
                device_rules={keyboard_name:{'kb_layout':new_keymap,'repeat_rate':49}}
                accepted=ipc(command='settings-apply',request_id=rid,expected_digest=baseline['digest'],changes={'monitors.outputs':rules,'devices':device_rules})
                assert accepted.get('ok'),accepted
                if action=='invalid-scale':
                    def rejected():
                        status=ipc(command='settings-status',request_id=rid)
                        assert not status.get('awaiting_confirmation'),status
                        return status if status.get('category') not in ('completion_unknown',None) else None
                    result=wait(rejected,20)
                    assert result['category']=='runtime_apply_failed_rolled_back',result
                    assert display()==old_display
                    assert fixture_store().current().id==baseline['digest']
                    print('monitor confirmation',action,'PASS',flush=True)
                    continue
                def waiting():
                    status=ipc(command='settings-status',request_id=rid)
                    if status.get('category') not in ('completion_unknown',None):raise AssertionError(status)
                    return status if status.get('awaiting_confirmation') else None
                wait(waiting,20)
                assert display()==(scale,0,-250,50),display()
                assert json.loads(ctl('-j','devices'))['keyboards'][0]['layout']==new_keymap
                store_now=fixture_store().current();assert store_now.id==baseline['digest']
                assert not ipc(command='settings-confirm',request_id='stale',keep=True)['ok']
                if action in ('cancel','keep'):
                    assert ipc(command='settings-confirm',request_id=rid,keep=action=='keep')['ok']
                elif action=='shell-loss':
                    shell=processes.pop();os.killpg(shell.pid,signal.SIGKILL);shell.wait(timeout=5)
                    wait(lambda:display()==old_display,22)
                    assert fixture_store().current().id==baseline['digest']
                    shell=subprocess.Popen([sys.executable,str(root/'.luminophore-migration/config/luminophore-shell'),'daemon'],env=env,start_new_session=True,
                        stdout=(test/'shell-monitor-recovery.log').open('w'),stderr=subprocess.STDOUT);processes.append(shell)
                    wait(lambda:ipc(command='settings-snapshot').get('ok'),30)
                def resolved():
                    value=ipc(command='settings-status',request_id=rid)
                    return value if value.get('category') not in ('completion_unknown',None) else None
                result=wait(resolved,24)
                assert result['category']==('ok' if action=='keep' else 'runtime_apply_failed_rolled_back'),(action,result)
                assert display()==((scale,0,-250,50) if action=='keep' else old_display),(action,display(),old_display)
                assert (fixture_store().current().id!=baseline['digest'])==(action=='keep')
                assert not ipc(command='settings-confirm',request_id=rid,keep=True)['ok']
                assert json.loads(ctl('-j','devices'))['keyboards'][0]['layout']==(new_keymap if action=='keep' else old_keymap)
                assert ipc(command='settings-snapshot')['devices']==(device_rules if action=='keep' else baseline['devices'])
                print('monitor confirmation',action,'PASS',flush=True)
        recovered_phases=[]
        if recovery:
            for index,phase in enumerate(('prepare.before','prepare.after','publish.before','publish.after','restore.before','restore.after')):
                baseline=ipc(command='settings-snapshot')
                model.activate()
                keymap_change={'input.drag_threshold':41,'input.cursor_inactive_timeout':3.5,
                               'compositor.fullscreen_opacity':0.43,'compositor.dim_modal':True,
                               'compositor.dim_strength':0.67,'compositor.blur_popups':False,
                               'compositor.render_unfocused_fps':29,'compositor.zoom_rigid':False,
                               'compositor.zoom_detached_camera':True,'compositor.zoom_disable_aa':False} if native_manager else {}
                if native_manager:
                    for key in ('zoom_rigid','zoom_detached_camera','zoom_disable_aa','pointer_focus_output','fullscreen_after_close','xwayland_nearest_neighbor','allow_tearing'):
                        keymap_change['compositor.'+key]=not baseline['values']['compositor.'+key]
                    keymap_change['compositor.fullscreen_focus_policy']=(baseline['values']['compositor.fullscreen_focus_policy']+1)%3
                remaining_candidate={}
                if native_manager:
                    for key in remaining_values:
                        value=baseline['values']['compositor.'+key]
                        if type(value) is bool: new=not value
                        elif key=='auto_hdr':new=(value+1)%3
                        elif key=='shadow_power':new=1 if value!=1 else 4
                        elif type(value) is int:new=value+1
                        elif type(value) is float:new=value+0.01 if key!='shadow_scale' else (0.72 if value!=0.72 else 0.83)
                        elif key=='sdr_transfer':new='srgb' if value!='srgb' else 'gamma22'
                        elif key=='background_color':new='ff123456' if value!='ff123456' else '80224466'
                        elif key in ('shadow_color','shadow_inactive_color'):new='ff123456 45deg' if value!='ff123456 45deg' else '77112233 0deg'
                        else:new=remaining_values[key]+'x' if value==remaining_values[key] else remaining_values[key]
                        remaining_candidate[key]=new
                    keymap_change.update({'compositor.'+key:value for key,value in remaining_candidate.items()})
                if using_import:
                    candidate_source=SOURCE.replace('pc+us+','pc+'+('de' if index%2 else 'fr')+'+')
                    keymap_source.write_text(candidate_source)
                    candidate_import=str(keymap_source)+'\n'+compile_keymap(candidate_source)
                    baseline_import=json.loads(ctl('-j','getoption','input:kb_snapshot'))['str']
                    keymap_change.update({'input.kb_file':str(keymap_source)})
                (test/'gate').write_text(phase)
                model.apply_settings({**keymap_change,'compositor.border_size':10+index,'layout.panel_height':70+index, **({'input.numlock_by_default':bool(index%2),'input.resolve_binds_by_sym':bool(index%2),'input.follow_mouse_threshold':0.31+index*0.1,'input.focus_on_close':index%3,'input.float_switch_override_focus':index%3,'input.accel_profile':'custom 1 0 '+str(index+2),'input.scroll_points':'1 0 '+str(index+1),'input.rotation':10+index,'input.repeat_delay':400+index,'touchpad.scroll_factor':0.2+index*0.1,'tablet.region_position_x':0.31+index*0.1} if native_manager else {})})
                request_id=model.settings_request_id
                wait(lambda:(test/'reached').exists(),40)
                if using_import: keymap_source.unlink()
                shell=processes.pop()
                os.killpg(shell.pid,signal.SIGKILL);shell.wait(timeout=5)
                (test/'gate').unlink();(test/'reached').unlink()
                shell=subprocess.Popen([sys.executable,str(root/'.luminophore-migration/config/luminophore-shell'),'daemon'],env=env,start_new_session=True,
                    stdout=(test/f'shell-recovery-{index}.log').open('w'),stderr=subprocess.STDOUT)
                processes.append(shell)
                def recovered_ready():
                    snapshot=ipc(command='settings-snapshot')
                    return snapshot if snapshot.get('ok') else None
                snapshot=wait(recovered_ready,40)
                receipt=ipc(command='settings-status',request_id=request_id)
                expected=70+index if phase=='publish.after' else baseline['values']['layout.panel_height']
                expected_border=10+index if phase=='publish.after' else baseline['values']['compositor.border_size']
                if using_import:
                    expected_import=candidate_import if phase=='publish.after' else baseline_import
                    assert json.loads(ctl('-j','getoption','input:kb_snapshot'))['str']==expected_import
                assert snapshot['values']['layout.panel_height']==expected,(phase,snapshot)
                if native_manager:
                    delay=400+index if phase=='publish.after' else baseline['values']['input.repeat_delay']
                    assert snapshot['values']['input.repeat_delay']==delay,(phase,snapshot)
                    assert json.loads(ctl('-j','getoption','input:repeat_delay'))['int']==delay
                    rotation=10+index if phase=='publish.after' else baseline['values']['input.rotation']
                    assert snapshot['values']['input.rotation']==rotation
                    assert json.loads(ctl('-j','getoption','input:rotation'))['int']==rotation
                    for key,point in (('accel_profile',index+2),('scroll_points',index+1)):
                        expected_curve=(('custom ' if key=='accel_profile' else '')+'1 0 '+str(point)) if phase=='publish.after' else baseline['values']['input.'+key]
                        assert snapshot['values']['input.'+key]==expected_curve
                        assert json.loads(ctl('-j','getoption','input:'+key))['str']==expected_curve
                    for key in ('focus_on_close','float_switch_override_focus'):
                        expected_focus=index%3 if phase=='publish.after' else baseline['values']['input.'+key]
                        assert snapshot['values']['input.'+key]==expected_focus
                        assert json.loads(ctl('-j','getoption','input:'+key))['int']==expected_focus
                    threshold=0.31+index*0.1 if phase=='publish.after' else baseline['values']['input.follow_mouse_threshold']
                    assert snapshot['values']['input.follow_mouse_threshold']==threshold
                    assert abs(json.loads(ctl('-j','getoption','input:follow_mouse_threshold'))['float']-threshold)<1e-6
                    for key in ('numlock_by_default','resolve_binds_by_sym'):
                        expected_policy=bool(index%2) if phase=='publish.after' else baseline['values']['input.'+key]
                        assert snapshot['values']['input.'+key]==expected_policy
                        assert json.loads(ctl('-j','getoption','input:'+key))['bool']==expected_policy
                    factor=0.2+index*0.1 if phase=='publish.after' else baseline['values']['touchpad.scroll_factor']
                    assert snapshot['values']['touchpad.scroll_factor']==factor
                    assert abs(json.loads(ctl('-j','getoption','input:touchpad:scroll_factor'))['float']-factor)<1e-6
                    position=0.31+index*0.1 if phase=='publish.after' else baseline['values']['tablet.region_position_x']
                    assert snapshot['values']['tablet.region_position_x']==position
                    assert abs(json.loads(ctl('-j','getoption','input:tablet:region_position'))['vec2'][0]-position)<1e-6

                if native_manager:
                    expected_remaining=remaining_candidate if phase=='publish.after' else {key:baseline['values']['compositor.'+key] for key in remaining_values}
                    verify_remaining(snapshot,expected_remaining)
                assert json.loads(ctl('-j','getoption','general:border_size'))['int']==expected_border
                assert receipt['request_id']==request_id and receipt['found'],receipt
                assert receipt['category']==('ok' if phase=='publish.after' else 'runtime_apply_failed_rolled_back'),receipt
                if native_manager:
                    for key,new_value in (('drag_threshold',41),('cursor_inactive_timeout',3.5)):
                        expected_value=new_value if phase=='publish.after' else baseline['values']['input.'+key]
                        assert snapshot['values']['input.'+key]==expected_value
                        native_value=json.loads(ctl('-j','getoption',interaction_native[key]))
                        assert abs(native_value['float' if type(expected_value) is float else 'int']-expected_value)<1e-6
                if native_manager:
                    for key,new_value in (('fullscreen_opacity',0.43),('dim_modal',True),('dim_strength',0.67),
                                          ('blur_popups',False),('render_unfocused_fps',29),('zoom_rigid',False),
                                          ('zoom_detached_camera',True),('zoom_disable_aa',False),
                                          ('pointer_focus_output',False),('fullscreen_focus_policy',0),
                                          ('fullscreen_after_close',False),('xwayland_nearest_neighbor',True),('allow_tearing',False)):
                        if key.startswith('zoom_') or key in ('pointer_focus_output','fullscreen_focus_policy','fullscreen_after_close','xwayland_nearest_neighbor','allow_tearing'):
                            new_value=keymap_change['compositor.'+key]
                        expected_value=new_value if phase=='publish.after' else baseline['values']['compositor.'+key]
                        assert snapshot['values']['compositor.'+key]==expected_value,(phase,key,snapshot)
                        observed=json.loads(ctl('-j','getoption',render_native[key]))
                        actual=observed[{float:'float',bool:'bool',int:'int'}[type(expected_value)]]
                        assert abs(actual-expected_value)<1e-6,(phase,key,observed)
                recovered_phases.append(dict(phase=phase,receipt=receipt,digest=snapshot['digest']))
                print('recovered',phase,receipt,flush=True)
            # A compositor crash also kills its Wayland connection. Restart the
            # isolated session from the durable checkpoint, never live desktop.
            last=ipc(command='settings-snapshot')
            compositor=processes[-2]
            os.killpg(compositor.pid,signal.SIGKILL);compositor.wait(timeout=5)
            stop();again=start(3)
            assert again['digest']==last['digest'],again
            assert again['values']['layout.panel_height']==last['values']['layout.panel_height']
            recovered_phases.append(dict(phase='native-process-kill',digest=again['digest']))
        (test/'result.json').write_text(json.dumps(dict(result='PASS',initial=before,applied=after,restarted=restored,native=native,recovery=recovered_phases),indent=2))
        print('PASS',test,flush=True)
    finally:stop()


@unittest.skipUnless(os.environ.get('LUMINOPHORE_RUN_SETTINGS_ACCEPTANCE') == '1',
                     'explicit isolated GPU/Wayland acceptance')
class SettingsDisplayAcceptanceTests(unittest.TestCase):
    def test_real_frontend_consumers_and_restart(self):
        import sys
        command = ["dbus-run-session", "--", sys.executable, "-c",
                   "from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance()"]
        result = subprocess.run(command, cwd=ROOT/'config', capture_output=True, text=True, timeout=150)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS /tmp/lb1-', result.stdout)
        print(result.stdout)


@unittest.skipUnless(os.environ.get('LUMINOPHORE_RUN_SETTINGS_ACCEPTANCE') == '1',
                     'explicit isolated scalar consumers acceptance')
class SettingsScalarDisplayTests(unittest.TestCase):
    def test_shared_scalar_types_reach_consumers_and_restart(self):
        import sys
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(expanded=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS /tmp/lb1-',result.stdout)
        print(result.stdout)


@unittest.skipUnless(os.environ.get('LUMINOPHORE_RUN_SETTINGS_ACCEPTANCE')=='1',
                     'explicit isolated native manager acceptance')
class SettingsNativeManagerDisplayTests(unittest.TestCase):
    def test_native_manager_boot_apply_and_restart_without_lua(self):
        import sys
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(expanded=True,native_manager=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS /tmp/lb1-',result.stdout)
        print(result.stdout)

    def test_device_overrides_and_invalid_keymap_rollback(self):
        import sys
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(native_manager=True,devices=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=240)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('device overrides, restart, keymap rejection, fallback PASS',result.stdout)
        print(result.stdout)

    def test_native_manager_preserves_process_loss_recovery(self):
        import sys
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(recovery=True,native_manager=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=300)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS /tmp/lb1-',result.stdout)
        print(result.stdout)

    def test_toml_monitor_rules_boot_and_restart(self):
        import sys
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(native_manager=True,monitors=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stdout.count('monitor readback'),2,result.stdout)
        print(result.stdout)

    def test_live_monitor_keep_cancel_timeout_and_shell_loss(self):
        import sys
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(native_manager=True,monitors=True,confirmation=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=240)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stdout.count('monitor confirmation'),5,result.stdout)
        print(result.stdout)

@unittest.skipUnless(os.environ.get("LUMINOPHORE_RUN_SETTINGS_ACCEPTANCE") == "1", "isolated display acceptance")
class WorkspaceRecoveryDisplayTests(unittest.TestCase):
    def test_output_removal_rehomes_clients_to_remaining_base(self):
        subprocess.run(["dbus-run-session", "--", "python3", "-c",
            "from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(native_manager=True,workspace_recovery=True)"],
            check=True, timeout=150)
