#!/usr/bin/python3
"""Probe an already trusted release inside its matching isolated host root.

Does not start a desktop session. Successful results are not hardware acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luminophore_runtime import artifact


def probe(root):
    root = Path(root).resolve(strict=True)
    manifest = artifact.verify(root, Path('/'))
    result = artifact.preflight(root)
    with tempfile.TemporaryDirectory(prefix='luminophore-probe-') as temporary:
        env = {'PATH': '/usr/bin:/bin', 'HOME': temporary, 'XDG_RUNTIME_DIR': temporary,
               'LUMINOPHORE_RELEASE_ROOT': str(root), 'LUMINOPHORE_RELEASE_ID': manifest['generation'],
               'LUMINOPHORE_COMPOSITOR': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'LD_BIND_NOW': '1'}
        env.update({key: str(root / value) for key, value in manifest['runtime_env'].items()})
        code = ('import sys;sys.path.insert(0,' + repr(str(root / 'shell')) + ');'
                'from luminophore_shell.bootstrap import configure_release;configure_release();'
                'import gi;gi.require_version("Gtk","4.0");from gi.repository import Gio,Gtk;'
                'assert Gio.TlsBackend.get_default().supports_tls();assert Gtk.get_major_version()==4;'
                'import cairo,dbus,requests,numpy,cv2;from PIL import Image;'
                'from OpenGL import GL;assert callable(GL.glCreateShader);'
                'assert Image.new("RGB",(2,2)).size==(2,2);'
                'assert numpy.dot(numpy.ones((2,2)),numpy.ones((2,2)))[0,0]==2;'
                'assert cv2.cvtColor(numpy.zeros((2,2,3),dtype=numpy.uint8),cv2.COLOR_RGB2GRAY).shape==(2,2);'
                'print("PRIVATE_GTK_GIO_TLS_PASS")')
        subprocess.run([str(root / 'python/bin/python3'), '-X', 'faulthandler', '-B', '-s', '-c', code],
                       env=env, check=True, timeout=30)
        subprocess.run([str(root / 'bin/xdg-desktop-portal-luminophore'), '--version'],
                       env=env, check=True, timeout=10)
        for profile in ('desktop', 'greeter'):
            env['LUMINOPHORE_CONFIG_ROOT'] = str(root / 'config' / profile)
            env['LUMINOPHORE_SESSION_ROLE'] = profile
            env['XDG_STATE_HOME'] = str(Path(temporary) / ('state-' + profile))
            config_command = [str(root / 'bin/Hyprland'), '--verify-config', '--config',
                              str(root / 'config' / profile / 'settings.toml')]
            # Parsing never starts a compositor session, including as UID 0.
            if os.geteuid() == 0:
                config_command.append('--i-am-really-stupid')
            subprocess.run(config_command, env=env, check=True, timeout=20)
        # A timeout only establishes that initialization did not terminate; this
        # does not certify capture, monitor selection or a live Wayland backend.
        try:
            subprocess.run([str(root / 'bin/luminophore-share-picker')],
                           env={**env, 'QT_QPA_PLATFORM': 'offscreen'}, check=True, timeout=3)
            result['picker_initialization'] = 'EXITED_OK'
        except subprocess.TimeoutExpired:
            result['picker_initialization'] = 'STAYED_RUNNING'
        result.update({'python_gtk_gio_tls': 'PASS', 'python_native_modules': 'PASS',
                       'config': 'PASS', 'greeter_config': 'PASS', 'portal_version': 'PASS',
                       'physical': 'NOT_RUN', 'boundary': 'isolated-processes'})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.release), sort_keys=True))
