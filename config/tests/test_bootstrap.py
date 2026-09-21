from __future__ import annotations

import unittest
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from luminophore_shell.bootstrap import LAYER_SHELL, preload_entries, with_preload, without_preload


class BootstrapEnvironmentTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('LUMINOPHORE_TEST_RELEASE'), 'private release required')
    def test_private_gio_tls_survives_bootstrap_without_duplicate_registration(self):
        root = Path(os.environ['LUMINOPHORE_TEST_RELEASE'])
        code = '''
import sys
sys.path.insert(0, sys.argv[1])
from luminophore_shell.bootstrap import configure_release
configure_release()
from gi.repository import Gio
assert Gio.TlsBackend.get_default().supports_tls()
'''
        env = {**os.environ, 'LUMINOPHORE_RELEASE_ROOT': str(root), 'PYTHONHOME': str(root / 'python'),
               'GI_TYPELIB_PATH': str(root / 'lib/girepository-1.0'), 'GIO_MODULE_DIR': str(root / 'lib/gio/modules')}
        result = subprocess.run([root / 'python/bin/python3', '-B', '-c', code,
                                 str(Path(__file__).resolve().parents[1])], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('non-registered extension', result.stderr)
        self.assertNotIn('Two different plugins', result.stderr)
        self.assertNotIn('GTypeModule', result.stderr)
    def test_private_fontconfig_is_initialized_before_environment_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lib').mkdir()
            shutil.copy2('/usr/lib/libfontconfig.so.1', root / 'lib/libfontconfig.so.1')
            (root / 'fonts.conf').write_text('<?xml version="1.0"?><fontconfig><dir>/usr/share/fonts</dir></fontconfig>')
            code = '''
import os
from luminophore_shell import bootstrap
bootstrap._release_root = os.environ['TEST_RELEASE']
bootstrap.configure_release()
assert 'FONTCONFIG_FILE' not in os.environ
'''
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                env={**os.environ, 'TEST_RELEASE': directory, 'FONTCONFIG_FILE': str(root / 'fonts.conf')})
            self.assertEqual(result.returncode, 0, result.stderr)
    def test_layer_shell_is_added_once_for_daemon_loader(self) -> None:
        entries = preload_entries("/tmp/other.so")

        self.assertEqual(with_preload(entries, LAYER_SHELL), f"{LAYER_SHELL}:/tmp/other.so")
        self.assertEqual(
            with_preload(preload_entries(f"{LAYER_SHELL}:/tmp/other.so"), LAYER_SHELL),
            f"{LAYER_SHELL}:/tmp/other.so",
        )

    def test_layer_shell_is_removed_without_losing_other_preloads(self) -> None:
        entries = preload_entries(f"/tmp/before.so:{LAYER_SHELL}:/tmp/after.so:{LAYER_SHELL}")

        self.assertEqual(without_preload(entries, LAYER_SHELL), "/tmp/before.so:/tmp/after.so")
        self.assertIsNone(without_preload(preload_entries(LAYER_SHELL), LAYER_SHELL))


if __name__ == "__main__":
    unittest.main()
