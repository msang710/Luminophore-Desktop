#!/usr/bin/env python3
"""Build product components inside the locked /src → /build sandbox.

Prepared dependency package roots come from /src/packages. This does not
download/build Python or system libraries, install services, or run the DE.
"""
import os
from pathlib import Path
import shutil
import subprocess


def configure_command(source, binary, locked_source):
    command = ['cmake', '-S', str(source), '-B', str(binary), '-G', 'Ninja',
               '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_INSTALL_PREFIX=/usr/lib/luminophore',
               '-DFETCHCONTENT_FULLY_DISCONNECTED=ON', '-DLUMINOPHORE_EFFECTS=ON']
    glaze = locked_source / 'glaze'
    if (glaze / 'CMakeLists.txt').is_file():
        if not glaze.resolve().is_relative_to(locked_source.resolve()):
            raise RuntimeError('Glaze source must belong to the locked source tree')
        command.append('-DFETCHCONTENT_SOURCE_DIR_GLAZE=' + str(glaze))
    return command


def prepare_compositor(source, target):
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
    return target


def prepare_shell(source, target):
    target.mkdir(parents=True, exist_ok=False)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'profile-runtime', '.git')
    for name in ['luminophore_shell', 'native', 'scripts']:
        shutil.copytree(source / name, target / name, symlinks=True, ignore=ignore)
    for name in ['luminophore-shell', 'LICENSE']:
        shutil.copy2(source / name, target / name)
    helper = source / 'luminophore-dragctl.c'
    if helper.is_file():
        shutil.copy2(helper, target / helper.name)


def build_portal(source, work, packages):
    target = packages / 'luminophore-portal'
    if target.exists():
        raise RuntimeError('prepared packages collide with portal component root')
    subprocess.run(['/usr/bin/python3', '-B', str(source / 'runtime/scripts/build-portal.py'),
                    '--source', str(source / 'portal-upstream'),
                    '--output', str(work / 'portal')], check=True)
    shutil.copytree(work / 'portal/package', target, symlinks=True)
    # Fonts and Qt plugins must be supplied from the locked package inputs.
    resources = source / 'release-resources'
    for name in ['etc/fonts/fonts.conf', 'share/fonts/luminophore/fallback.ttf',
                 'lib/qt6/plugins/platforms/libqwayland.so']:
        if not (resources / name).is_file():
            raise RuntimeError('missing locked desktop resource: ' + name)
    shutil.copytree(resources, target / 'usr/lib/luminophore', dirs_exist_ok=True, symlinks=True)


def main():
    source, output = Path('/src'), Path('/build')
    if Path.cwd() != output or 'SOURCE_DATE_EPOCH' not in os.environ:
        raise SystemExit('use this script through the locked build at /build')
    for name in ['compositor', 'config', 'packages', 'portal-upstream', 'release-resources']:
        if not (source / name).is_dir():
            raise SystemExit('missing locked source directory: ' + name)
    packages = output / 'packages'
    shutil.copytree(source / 'packages', packages, symlinks=True)
    # Package roots below must not collide with prepared dependency packages.
    compositor = packages / 'luminophore-compositor'
    shell = packages / 'luminophore-shell'
    if compositor.exists() or shell.exists():
        raise SystemExit('prepared packages collide with product component roots')
    work = output / 'work'
    work.mkdir()
    build_portal(source, work, packages)
    cmake = work / 'compositor'
    compositor_source = prepare_compositor(source / 'compositor', work / 'compositor-source')
    subprocess.run(configure_command(compositor_source, cmake, source), check=True)
    subprocess.run(['cmake', '--build', str(cmake), '--target', 'Hyprland', 'hyprctl',
                    'start-hyprland', '--parallel', '2'], check=True)
    prefix = compositor / 'usr/lib/luminophore'
    (prefix / 'bin').mkdir(parents=True)
    for name, built in [('Hyprland', 'Hyprland'), ('hyprctl', 'hyprctl/hyprctl'),
                        ('start-hyprland', 'start/start-hyprland')]:
        shutil.copy2(cmake / built, prefix / 'bin' / name)
    license_dir = prefix / 'share/licenses/luminophore-compositor'
    license_dir.mkdir(parents=True)
    shutil.copy2(source / 'compositor/LICENSE', license_dir / 'LICENSE')
    shell_work = work / 'config'
    prepare_shell(source / 'config', shell_work)
    # These build-only scripts write into the private work copy. Never invoke
    # deploy-luminophore-shell or install-window-glow-plugin from a build.
    subprocess.run(['/bin/sh', str(shell_work / 'scripts/build-glow-layer')], check=True)
    subprocess.run(['/bin/sh', str(shell_work / 'scripts/build-background-layer')], check=True)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-o',
                    str(shell_work / 'luminophore-dragctl'),
                    str(shell_work / 'luminophore-dragctl.c')], check=True)
    prefix = shell / 'usr/lib/luminophore'
    (prefix / 'shell').mkdir(parents=True)
    shutil.copytree(shell_work / 'luminophore_shell', prefix / 'shell/luminophore_shell', symlinks=True,
                    ignore=shutil.ignore_patterns('lua_settings.py', 'production_settings.py', 'settings_coordinator.py', 'settings_backend.py', 'binding_service.py', '__pycache__', '*.pyc'))
    (prefix / 'shell/native').mkdir()
    shutil.copy2(shell_work / 'native/luminophore_glow_shader.h', prefix / 'shell/native/luminophore_glow_shader.h')
    for name in ['luminophore-shell', 'luminophore-dragctl']:
        shutil.copy2(shell_work / name, prefix / 'shell' / name)
    subprocess.run(['/usr/bin/python3', '-B', '-m', 'luminophore_shell.desktop_defaults',
                    '--output', str(prefix/'config')], cwd=shell_work, check=True)
    shutil.copy2(source / 'runtime/defaults/greeter-theme.json', prefix / 'config/greeter-theme.json')
    license_dir = prefix / 'share/licenses/luminophore-shell'
    license_dir.mkdir(parents=True)
    shutil.copy2(source / 'config/LICENSE', license_dir / 'LICENSE')


if __name__ == '__main__':
    main()
