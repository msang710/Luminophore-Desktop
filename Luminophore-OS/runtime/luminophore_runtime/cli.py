"""Explicit build/inspection/staging actions; nothing activates on import."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from contextlib import ExitStack

from . import artifact, assembly, build, desktop, inventory, managed
from .common import ContractError, load
from .state import Store
from .session import owner_alive


def parser():
    p = argparse.ArgumentParser(description='Luminophore whole-DE runtime contracts')
    commands = p.add_subparsers(dest='action', required=True)
    commands.add_parser('session-ready')
    commands.add_parser('session-check')
    for action in ('start', 'restore', 'confirm'):
        commands.add_parser('greeter-trial-' + action)
    command = commands.add_parser('installed-init')
    command.add_argument('--store', type=Path, required=True)
    command = commands.add_parser('classify-providers')
    command.add_argument('--sysroot', required=True, type=Path)
    command.add_argument('--policy', required=True, type=Path)
    for action in ['assemble-desktop', 'build-desktop']:
        command = commands.add_parser(action)
        for flag in ['source', 'lock', 'build-output', 'output']:
            command.add_argument('--' + flag, required=True, type=Path)
        command.add_argument('--layout', required=True)
        command.add_argument('--' + ('sysroot' if action == 'build-desktop' else 'receipt'),
                             required=True, type=Path)
    for action in ['verify-assembly', 'pack-assembly']:
        command = commands.add_parser(action)
        command.add_argument('--assembly', required=True, type=Path)
        if action == 'pack-assembly':
            command.add_argument('--output', required=True, type=Path)
            command.add_argument('--patchelf', type=Path)
    for action in ['desktop-recipe', 'pack-desktop']:
        command = commands.add_parser(action)
        command.add_argument('--sysroot', required=True, type=Path)
        command.add_argument('--input', required=True, type=Path)
        if action == 'pack-desktop':
            command.add_argument('--output', required=True, type=Path)
            command.add_argument('--patchelf', type=Path)
    for action in ['inventory', 'pack']:
        command = commands.add_parser(action)
        command.add_argument('--sysroot', required=True, type=Path)
        command.add_argument('--recipe', required=True, type=Path)
        if action == 'pack':
            command.add_argument('--output', required=True, type=Path)
            command.add_argument('--patchelf', type=Path)
    command = commands.add_parser('verify')
    command.add_argument('--release', type=Path, required=True)
    command.add_argument('--host-root', type=Path)
    command.add_argument('--loader', action='store_true')
    for action in ['stage', 'status', 'select', 'rollback', 'confirm', 'revoke', 'gc', 'run']:
        command = commands.add_parser(action)
        command.add_argument('--store', type=Path, required=True)
        command.add_argument('--system-store', action='store_true')
        if action == 'stage':
            command.add_argument('--release', type=Path, required=True)
            command.add_argument('--trusted-generation', required=True)
        if action in ['select', 'confirm', 'revoke', 'run']:
            command.add_argument('--generation', required=action != 'run')
        if action in ['select', 'rollback', 'confirm', 'revoke']:
            command.add_argument('--revision', type=int, required=True)
        if action in ['select', 'rollback', 'run']:
            command.add_argument('--config-version', type=int, required=True)
        if action in ['select', 'rollback', 'confirm', 'run']:
            command.add_argument('--host-root', type=Path, default=Path('/'))
        if action == 'confirm':
            command.add_argument('--evidence', type=Path, required=True)
        if action == 'run':
            command.add_argument('--component', required=True)
            command.add_argument('--desktop-session', action='store_true')
            command.add_argument('--restart-limit', type=int, default=0)
            command.add_argument('args', nargs=argparse.REMAINDER)
    for action in ['build-lock', 'build']:
        command = commands.add_parser(action)
        command.add_argument('--sysroot', type=Path, required=True)
        command.add_argument('--source', type=Path, required=True)
        if action == 'build':
            command.add_argument('--lock', type=Path, required=True)
            command.add_argument('--output', type=Path, required=True)
        else:
            command.add_argument('--epoch', type=int, required=True)
            command.add_argument('args', nargs=argparse.REMAINDER)
    for action in ['os-describe', 'os-verify', 'os-admit']:
        command = commands.add_parser(action)
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--boot', type=Path, required=True)
        if action == 'os-describe':
            for flag in ['generation', 'profile', 'kernel', 'initramfs', 'entry']:
                command.add_argument('--' + flag, required=True)
        else:
            command.add_argument('--descriptor', type=Path, required=True)
        if action == 'os-admit':
            command.add_argument('--evidence', type=Path, action='append', default=[])
            command.add_argument('--status', choices=['trial', 'confirmed'], required=True)
    command = commands.add_parser('config-copy')
    command.add_argument('--source', type=Path, required=True)
    command.add_argument('--state', type=Path, required=True)
    command.add_argument('--generation', required=True)
    command.add_argument('--schema', type=int, required=True)
    return p


def run_session(store, args):
    if not 0 <= args.restart_limit <= 5:
        raise ContractError('restart limit must be between 0 and 5')
    child, stopped = None, False
    owner = os.environ.get('LUMINOPHORE_SESSION_OWNER')
    def forward(signum, frame):
        nonlocal stopped
        stopped = True
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass
    old = {sig: signal.signal(sig, forward) for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP]}
    try:
        with ExitStack() as stack:
            session = stack.enter_context(store.session(args.host_root, config_version=args.config_version, generation=args.generation))
            environment = dict(os.environ)
            if getattr(args, 'desktop_session', False):
                from .desktop_session import Lifecycle, prepare_config, compile_user_config
                if args.component != 'compositor':
                    raise ContractError('desktop-session requires the compositor component')
                environment.update({'LUMINOPHORE_RELEASE_ID': session.manifest['generation'],
                                    'LUMINOPHORE_RELEASE_ROOT': str(session.root),
                                    'LUMINOPHORE_STORE': str(session.store)})
                lifecycle = stack.enter_context(Lifecycle(environment))
                environment.update(lifecycle.environment)
                environment['LUMINOPHORE_CONFIG_ROOT'] = str(prepare_config(session.root, environment, config_version=session.manifest['compatibility']['config']))
                compile_user_config(session, environment)
            if args.component == 'greeter_compositor':
                runtime = Path(environment.get('XDG_RUNTIME_DIR', ''))
                if not runtime.is_absolute():
                    raise ContractError('greeter requires absolute runtime directory')
                private = runtime/'luminophore-greeter-settings'/session.manifest['generation']
                environment['LUMINOPHORE_CONFIG_ROOT'] = str(session.root/'config/greeter')
                environment['XDG_STATE_HOME'] = str(private)
                environment.pop('LUMINOPHORE_SESSION_READY_COMMAND', None)
                environment.pop('LUMINOPHORE_SHELL_COMMAND', None)
            extra = args.args[1:] if args.args[:1] == ['--'] else args.args
            for attempt in range(args.restart_limit + 1):
                if stopped or not owner_alive(owner):
                    return 128 + signal.SIGTERM
                command, env = session.command(args.component, extra, environment=environment)
                child = subprocess.Popen(command, env=env, pass_fds=(session.lease_fd,), start_new_session=True)
                while True:
                    try:
                        result = child.wait(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        if not owner_alive(owner):
                            forward(signal.SIGTERM, None)
                        if stopped:
                            try:
                                result = child.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                os.killpg(child.pid, signal.SIGKILL)
                                result = child.wait()
                            break
                if stopped or result == 0 or attempt == args.restart_limit:
                    return result if result >= 0 else 128 - result
                time.sleep(1)
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def execute(args):
    action = args.action
    if action in {'greeter-trial-start', 'greeter-trial-restore', 'greeter-trial-confirm'}:
        from .greeter_trial import main
        return main(action.removeprefix('greeter-trial-'))
    if action == 'installed-init':
        from .installed import InstalledStore
        return InstalledStore.initialize(args.store).read()
    if action in {'session-ready', 'session-check'}:
        from .desktop_session import check, ready
        if action == 'session-ready':
            ready()
        else:
            check()
        return 0
    if action == 'classify-providers':
        policy = load(args.policy)
        if set(policy) != {'candidates', 'system', 'required_private'}:
            raise ContractError('invalid provider classification policy')
        return inventory.classify(args.sysroot, **policy)
    if action == 'assemble-desktop':
        return assembly.assemble(args.build_output, args.source, load(args.lock), load(args.receipt),
                                 args.layout, args.output)
    if action == 'build-desktop':
        return assembly.run(args.sysroot, args.source, load(args.lock), args.layout,
                            args.build_output, args.output)
    if action == 'verify-assembly':
        result = assembly.verify(args.assembly)
        return {'assembly': result['assembly'], 'integrity': 'PASS', 'session': 'NOT_RUN'}
    if action == 'pack-assembly':
        return {'release': str(assembly.pack(args.assembly, args.output, patchelf=args.patchelf))}
    if action in {'desktop-recipe', 'pack-desktop'}:
        spec = load(args.input)
        if action == 'desktop-recipe':
            return desktop.recipe(args.sysroot, spec)
        return {'release': str(desktop.pack(args.sysroot, spec, args.output, patchelf=args.patchelf))}
    if action in {'inventory', 'pack'}:
        recipe = load(args.recipe)
        artifact.validate_recipe(recipe)
        if action == 'inventory':
            members = artifact.expand(args.sysroot.resolve(), recipe['files'])
            return inventory.scan(args.sysroot, members.values(), recipe['providers'], recipe['dynamic'])
        return {'release': str(artifact.pack(args.sysroot, recipe, args.output, patchelf=args.patchelf))}
    if action == 'verify':
        manifest = artifact.verify(args.release, args.host_root)
        return artifact.preflight(args.release) if args.loader else {
            'generation': manifest['generation'], 'integrity': 'PASS',
            'host': 'PASS' if args.host_root else 'NOT_RUN', 'loader': 'NOT_RUN', 'session': 'NOT_RUN'}
    if action == 'build-lock':
        command = args.args[1:] if args.args[:1] == ['--'] else args.args
        return build.create_lock(args.sysroot, args.source, command, args.epoch)
    if action == 'build':
        return build.run(args.sysroot, args.source, args.output, load(args.lock))
    if action == 'os-describe':
        return managed.describe(args.root, args.boot, args.generation, args.profile,
                                {key: getattr(args, key) for key in ['kernel', 'initramfs', 'entry']})
    if action == 'os-verify':
        return {'deployment': managed.verify(args.root, args.boot, load(args.descriptor)), 'activation': 'NOT_RUN'}
    if action == 'os-admit':
        return managed.admit(args.root, args.boot, load(args.descriptor),
                             [load(path) for path in args.evidence], args.status)
    if action == 'config-copy':
        return {'config': str(managed.copy_config(args.source, args.state, args.generation, args.schema))}
    if args.system_store:
        from .installed import InstalledStore
        store = InstalledStore(args.store)
    else:
        store = Store(args.store)
    if action == 'run':
        return run_session(store, args)
    if action == 'status':
        return store.read()
    if action == 'stage':
        return {'candidate': store.stage(args.release, args.trusted_generation)}
    if action == 'select':
        return store.select(args.generation, args.host_root, args.revision, config_version=args.config_version)
    if action == 'rollback':
        return store.rollback(args.host_root, args.revision, config_version=args.config_version)
    if action == 'confirm':
        return store.confirm(args.generation, load(args.evidence), args.revision, args.host_root)
    if action == 'revoke':
        store.revoke(args.generation, args.revision)
        return store.read()
    if action == 'gc':
        return {'removed': store.gc()}
    raise ContractError('unknown action')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ContractError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    if isinstance(result, int):
        return result
    print(json.dumps(result, sort_keys=True))
    return 0
