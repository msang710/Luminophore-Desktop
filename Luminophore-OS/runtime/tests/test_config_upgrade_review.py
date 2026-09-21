from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from luminophore_runtime.common import ContractError
from luminophore_runtime.desktop_session import prepare_config, compile_user_config


class ConfigUpgradeReviewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.release=self.root/'release'
        self.module=self.release/'shell/luminophore_shell/settings_bootstrap.py'
        self.module.parent.mkdir(parents=True);self.module.touch()
        self.env={'HOME':str(self.root/'home')}
        self.target=prepare_config(self.release,self.env)
        self.env['LUMINOPHORE_CONFIG_ROOT']=str(self.target)
        self.session=SimpleNamespace(root=self.release,manifest={'runtime_env':{'PYTHONHOME':'python'},'entries':{'shell':{'path':'python/bin/python3'}}})

    def test_bootstrap_uses_release_python_and_five_file_defaults(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs));return subprocess.CompletedProcess(argv,0,'','')
        compile_user_config(self.session,self.env,runner=runner)
        argv,kwargs=calls[0]
        self.assertEqual(argv[0],str(self.release/'python/bin/python3'))
        self.assertEqual(argv[argv.index('-m')+1],'luminophore_shell.settings_bootstrap')
        self.assertEqual(argv[argv.index('--target')+1],str(self.target))
        self.assertEqual(argv[argv.index('--defaults')+1],str(self.release/'config/desktop'))
        self.assertEqual(kwargs['env']['PYTHONPATH'],str(self.release/'shell'))
        self.assertFalse(self.target.exists())

    def test_legacy_release_without_native_bootstrap_cannot_silently_fall_back(self):
        self.module.unlink()
        def forbidden(*args,**kwargs):self.fail('legacy compiler invoked')
        with self.assertRaisesRegex(ContractError,'native settings bootstrap'):
            compile_user_config(self.session,self.env,runner=forbidden)
        self.assertFalse(self.target.exists())

    def test_failed_import_preserves_user_source_and_surfaces_error(self):
        self.target.mkdir(parents=True)
        source=self.target/'custom.lua';source.write_text('custom()')
        def runner(argv,**kwargs):return subprocess.CompletedProcess(argv,1,'','custom Lua requires resolution')
        with self.assertRaisesRegex(ContractError,'custom Lua requires resolution'):
            compile_user_config(self.session,self.env,runner=runner)
        self.assertEqual(source.read_text(),'custom()')

    def test_user_root_symlink_is_rejected_before_bootstrap(self):
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(self.release,target_is_directory=True)
        with self.assertRaises(ContractError):prepare_config(self.release,self.env)

    def test_relative_xdg_root_is_rejected(self):
        with self.assertRaises(ContractError):prepare_config(self.release,{**self.env,'XDG_CONFIG_HOME':'relative'})
