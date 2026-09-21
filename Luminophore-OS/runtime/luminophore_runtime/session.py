"""A session resolves one generation; children never consult a mutable selector."""
import os
import shlex
from pathlib import Path

from .common import ContractError


SEARCH_VARIABLES = {'PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'GI_TYPELIB_PATH',
                    'GIO_MODULE_DIR', 'GIO_EXTRA_MODULES', 'GSETTINGS_SCHEMA_DIR',
                    'GTK_PATH', 'GTK_EXE_PREFIX', 'GTK_DATA_PREFIX', 'GDK_PIXBUF_MODULE_FILE',
                    'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML_IMPORT_PATH', 'FONTCONFIG_FILE', 'FONTCONFIG_PATH'}
INTERACTIVE_SHELL_COMMANDS = {'ctl', 'launch', 'launch-bundle', 'capture', 'restart', 'validate-config'}


def process_token(pid):
    try:
        row = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        if row[0] == 'Z':
            return None
        return str(int(pid)) + ':' + row[19]
    except (OSError, ValueError, IndexError):
        return None


def owner_alive(token):
    if token is None:
        return True
    return process_token(token.split(':', 1)[0]) == token


def application_environment(environment):
    result = {key: value for key, value in environment.items()
              if key not in SEARCH_VARIABLES and not key.startswith(('LD_', 'LUMINOPHORE_'))}
    result['PATH'] = '/usr/bin:/bin'
    return result


class Session:
    def __init__(self, root, manifest, lease_fd, store, host_root, *, system_store=False):
        self.root = Path(root)
        self.manifest = manifest
        self.lease_fd = lease_fd
        self.store = Path(store)
        self.host_root = Path(host_root)
        self.system_store = system_store
        self.interactive = False

    def command(self, component, args=(), environment=None):
        from . import artifact
        if self.interactive:
            if component not in {'control', 'shell_cli'}:
                raise ContractError('interactive context only supports public control commands')
            if component == 'shell_cli' and (not args or args[0] not in INTERACTIVE_SHELL_COMMANDS):
                raise ContractError('interactive shell context requires a public command')
        else:
            artifact.verify(self.root, self.host_root)
            artifact.preflight(self.root)
        try:
            entry = self.manifest['entries'][component]
        except KeyError as error:
            raise ContractError('unknown release component: ' + component) from error
        source_env = os.environ if environment is None else environment
        env = application_environment(source_env)
        env.pop('HYPRLAND_INSTANCE_SIGNATURE', None)
        env['XDG_CURRENT_DESKTOP'] = 'Luminophore'
        env['XDG_SESSION_DESKTOP'] = 'luminophore'
        env['HYPRLAND_NO_SD_VARS'] = '1'
        for key in ('LUMINOPHORE_SESSION_TOKEN', 'LUMINOPHORE_CONFIG_ROOT', 'LUMINOPHORE_STORE',
                    'LUMINOPHORE_SHELL_BLOOM', 'LUMINOPHORE_SETTINGS_STATE_ROOT', 'LUMINOPHORE_SETTINGS_RECOVERY'):
            if key in source_env:
                env[key] = source_env[key]
        if component != 'compositor' and source_env.get('LUMINOPHORE_INSTANCE_SIGNATURE'):
            env['LUMINOPHORE_INSTANCE_SIGNATURE'] = source_env['LUMINOPHORE_INSTANCE_SIGNATURE']
        env['LUMINOPHORE_RELEASE_ROOT'] = str(self.root)
        env['LUMINOPHORE_RELEASE_ID'] = self.manifest['generation']
        env['LUMINOPHORE_COMPOSITOR'] = '1'
        env['LUMINOPHORE_SESSION_ROLE'] = 'greeter' if component == 'greeter_compositor' else 'desktop'
        env['LUMINOPHORE_SESSION_OWNER'] = process_token(os.getpid())
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONNOUSERSITE'] = '1'
        runtime_env = {key: str(self.root / value) for key, value in self.manifest['runtime_env'].items()}
        # Shell entries use the bundled Python interpreter. The dispatcher
        # starts in that same interpreter with its own private Python home.
        shell = self.manifest['entries']['shell']
        shell_command = ['/usr/bin/env', *[key + '=' + value for key, value in runtime_env.items()],
                         str(self.root / shell['path']), '-B', str(self.root / 'support/luminophore-runtime'),
                         'run', '--store', str(self.store), '--generation', self.manifest['generation'],
                         '--component', 'shell', '--restart-limit', '5', '--config-version',
                         str(self.manifest['compatibility']['config']), '--host-root', str(self.host_root)]
        if self.system_store:
            shell_command.append('--system-store')
        env['LUMINOPHORE_SHELL_COMMAND'] = shlex.join(shell_command)
        if env.get('LUMINOPHORE_SESSION_TOKEN'):
            env['LUMINOPHORE_SESSION_READY_COMMAND'] = shlex.join([
                '/usr/bin/env', *[key + '=' + value for key, value in runtime_env.items()],
                str(self.root / shell['path']), '-B', str(self.root / 'support/luminophore-runtime'),
                'session-ready'])
        # Only the Python shell process needs these. Its bootstrap transfers
        # GI paths into the in-process repository, then removes the environment.
        if component in {'shell', 'shell_cli', 'settings', 'greeter',
                         'greeter_shell', 'greeter_session'}:
            env.update(runtime_env)
        elif component == 'portal':
            env.update({key: value for key, value in runtime_env.items() if key in {'FONTCONFIG_FILE', 'QT_PLUGIN_PATH'}})
        command = [str(self.root / entry['path'])]
        command.extend(value.replace('{release}', str(self.root)) for value in entry['args'])
        command.extend(args)
        return command, env
