"""Import the prepared Shell source layout without retired Lua writers."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class PackagedSettingsCutoverTests(unittest.TestCase):
    def test_runtime_imports_without_legacy_coordinators_or_lua_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            retired={'lua_settings.py','production_settings.py','settings_coordinator.py','settings_backend.py','binding_service.py'}
            shutil.copytree(Path('luminophore_shell'),root/'luminophore_shell',
                            ignore=shutil.ignore_patterns(*retired,'__pycache__','*.pyc','assets'))
            code='import sys;sys.path.insert(0,sys.argv[1]);import luminophore_shell.settings_migration,luminophore_shell.desktop_defaults,luminophore_shell.settings_service,luminophore_shell.__main__;assert not any(n in sys.modules for n in ["luminophore_shell.lua_settings","luminophore_shell.production_settings","luminophore_shell.settings_coordinator","luminophore_shell.binding_service"])'
            result=subprocess.run([sys.executable,'-I','-B','-c',code,str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            build=(Path(__file__).resolve().parents[2]/'Luminophore-OS/runtime/scripts/build-components.py').read_text()
            for filename in retired:self.assertIn(repr(filename),build)

    def test_recovery_store_is_private_and_does_not_replace_normal_xdg_state(self):
        from luminophore_shell.settings_store import SettingsPaths,StoreError
        env={'XDG_STATE_HOME':'/home/example/.local/state','LUMINOPHORE_SETTINGS_STATE_ROOT':'/run/user/1000/recovery/state'}
        paths=SettingsPaths.current(env,Path('/home/example'))
        self.assertEqual(paths.state,Path('/run/user/1000/recovery/state'))
        self.assertEqual(env['XDG_STATE_HOME'],'/home/example/.local/state')
        with self.assertRaises(StoreError):SettingsPaths.current({**env,'LUMINOPHORE_SETTINGS_STATE_ROOT':'relative'},Path('/home/example'))
