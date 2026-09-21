"""Production Shell host for the native joint-settings service.

All disk/IPC waits run on this worker. GTK callbacks are invoked through the
application's scheduler; this class never decides to commit or roll back.
"""
import json
import os
from copy import deepcopy
import threading
import time
import tomllib
from dataclasses import asdict
from uuid import uuid4
from .settings_generation import fixture_store, generation_config, edit_candidate
from .settings_checkpoint import CheckpointWorker
from .settings_lease import SettingsLease
from .settings_participant import ShellSettingsParticipant
from .settings_schema import settings_values


class SettingsServiceHost:
    def __init__(self, client, schedule, apply, verify):
        self.client, self.schedule = client, schedule
        self._apply, self._verify = apply, verify
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._wake = threading.Event()
        self._queued = None
        self._confirmation = None
        self._confirmation_choice = None
        self._last_request = None
        self._result = None
        self._snapshot = None
        self._documents = None
        self._restart_required = False
        self._error = 'settings service starting'
        self._thread = threading.Thread(target=self._run, name='luminophore-settings-host', daemon=True)
        self._thread.start()

    def _main(self, fn, value):
        done = threading.Event(); outcome = []; cancelled = threading.Event()
        started = threading.Event(); admission = threading.Lock()
        def run():
            with admission:
                if cancelled.is_set(): done.set(); return False
                started.set()
            try: fn(value)
            except Exception as error: outcome.append(error)
            finally: done.set()
            return False
        self.schedule(run)
        while not done.wait(.1):
            if self._closed.is_set():
                with admission:
                    if not started.is_set():
                        cancelled.set()
                        raise RuntimeError('Shell closed before settings operation')
                # An executing GTK callback owns the lease until it terminates.
                # Cancellation cannot prove its writes have stopped.
        if outcome: raise outcome[0]

    def _remote(self, wire):
        state = json.loads(self.client._run('luminophoresettingsservice', wire))
        if not isinstance(state, dict) or 'error' in state or state.get('version') != 1:
            raise RuntimeError('native settings service rejected request')
        return state

    def _wire_result(self, request, category='completion_unknown', message=''):
        restart = getattr(self, '_restart_required', False)
        if category == 'ok' and restart:
            category = 'saved_pending_next_start'
            message = '저장했습니다. 시작 명령·종료 명령·환경 변수 변경은 다음 세션에서 적용됩니다.'
        return dict(ok=True, found=True, request_id=request['request_id'], category=category,
                    phase='complete' if category != 'completion_unknown' else 'completion_unknown',
                    digest=request['expected_digest'], changed_paths=sorted(request['changes']), message=message, requires_session_restart=restart)

    def snapshot(self):
        with self._lock:
            if self._snapshot is None: return dict(ok=False, error=self._error)
            config, generation = self._snapshot
            return dict(ok=True, online=True, digest=generation, recovery_mode=os.environ.get('LUMINOPHORE_SETTINGS_RECOVERY') == '1', requires_session_restart=getattr(self, '_restart_required', False), values=settings_values(config), devices={name: {key: value for key, value in rule.items() if key != 'kb_snapshot'} for name, rule in config.devices.items()})

    def controller_snapshot(self):
        with self._lock:
            if self._snapshot is None: raise RuntimeError(self._error)
            return self._snapshot

    def binding_snapshot(self):
        from .settings_bundle import _bindings
        with self._lock:
            if self._snapshot is None:
                return dict(ok=False, error=self._error)
            raw = tomllib.loads(self._documents['bindings.toml'])
            raw.pop('schema_version')
            registry = _bindings(raw)
            digest = self._snapshot[1]
        return dict(ok=True, digest=digest, generation_id=digest, reload_status='native', customized=bool(raw.get('actions')),
                    bindings=[asdict(action) for action in registry.actions])

    def apply_bindings(self, request):
        with self._lock:
            if self._snapshot is None:
                return dict(ok=False, error=self._error)
            if request.get('expected_digest') != self._snapshot[1]:
                return dict(ok=False, category='conflict', error='settings generation changed')
            actions = tomllib.loads(self._documents['bindings.toml']).get('actions', {})
        changes = request.get('changes')
        if not isinstance(changes, dict) or not changes:
            return dict(ok=False, category='validation', error='binding changes required')
        for key, row in changes.items():
            if not isinstance(row, dict) or set(row) != {'chord', 'flags'}:
                return dict(ok=False, category='validation', error='typed binding update required')
            actions[key] = {'flags': row['flags'], **({'disabled': True} if row['chord'] is None else {'chord': row['chord']})}
        return self.apply(dict(request_id=request.get('request_id') or 'bindings-'+uuid4().hex,
                               expected_digest=request['expected_digest'], changes={'bindings.actions': actions}))

    def apply(self, request):
        with self._lock:
            if self._result and request.get('request_id') == self._result['request_id']:
                if request.get('changes') != self._last_request['changes'] or request.get('expected_digest') != self._last_request['expected_digest']:
                    return self._wire_result(request, 'conflict', 'request ID reused with different content')
                return dict(self._result)
            if self._snapshot is None or (self._result and self._result['category'] == 'completion_unknown'):
                return dict(ok=False, category='busy', error='settings service not ready')
            request = dict(request)
            request['changes'] = deepcopy(request['changes'])
            self._result = self._wire_result(request)
            self._last_request = request
            self._queued = request
            self._wake.set()
            return dict(self._result)

    def status(self, request_id='', recover=False):
        with self._lock:
            if self._result and (not request_id or request_id == self._result['request_id']):
                return {**self._result, 'awaiting_confirmation': self._confirmation is not None, 'recovery_error': self._error if self._snapshot is None else ''}
            return dict(ok=True, found=False, recovery_error=self._error if self._snapshot is None else '')

    def decide(self, request_id, keep):
        with self._lock:
            if type(keep) is not bool or not self._confirmation or self._confirmation[0] != request_id:
                return dict(ok=False, error='stale confirmation')
            choice = (self._confirmation, keep)
            if self._confirmation_choice and self._confirmation_choice != choice:
                return dict(ok=False, error='confirmation already submitted')
            self._confirmation_choice = choice
            self._wake.set()
            return dict(ok=True, pending=True)

    def _run(self):
        while not self._closed.is_set():
            try: self._session()
            except Exception as error:
                with self._lock:
                    self._snapshot = None
                    self._error = str(error)
                self._closed.wait(.2)

    def _session(self):
        checkpoint = None
        lease = None
        try:
            store = fixture_store()
            state = self._remote('attach')
            while state.get('recovering') and not self._closed.wait(.02):
                state = self._remote('status')
            if self._closed.is_set(): return
            if state.get('recoveryFailed'): raise RuntimeError('native recovery failed')
            lease = SettingsLease(store.root/'shell.lock')
            check = self._remote('status')
            if check['epoch'] != state['epoch'] or check.get('recovering'):
                raise RuntimeError('coordinator changed during attachment')
            store.bind_epoch(state['epoch'])
            recovered = store.recovery_request()
            with self._lock:
                if recovered and self._last_request is None:
                    self._last_request = recovered
                    self._result = self._wire_result(recovered, message='reinitializing completed settings')
            boot = store.current()
            if boot is None or boot.id != state['confirmed'] or state['state'] != 'idle':
                raise RuntimeError('settings startup requires matching completed generation and idle coordinator')
            epoch = state['epoch']; sequence = int(state['sequence'])
            participant = ShellSettingsParticipant(store, boot,
                lambda config: self._main(self._apply, config), lambda config: self._main(self._verify, config))
            config = generation_config(store, boot)
            self._main(self._apply, config); self._main(self._verify, config)
            with self._lock:
                self._restart_required = state.get('requiresSessionRestart', False)
                self._snapshot = config, boot.id
                self._documents = dict(boot.documents)
                self._error = ''
                if recovered and (self._last_request is None or self._last_request['request_id'] == recovered['request_id']):
                    self._last_request = recovered
                    category = 'ok' if recovered['candidate'] == boot.id else 'runtime_apply_failed_rolled_back'
                    if boot.id not in (recovered['candidate'], recovered['expected_digest']): category = 'conflict'
                    self._result = self._wire_result(recovered, category, 'recovered from completed checkpoint')
                    self._result['digest'] = boot.id
                elif self._queued is None and self._last_request and self._result and self._result['category'] == 'completion_unknown':
                    # Journal persistence failed before start was sent. A verified
                    # unchanged completed generation proves this request did not
                    # commit; do not substitute an older journal's request ID.
                    if boot.id == self._last_request['expected_digest']:
                        self._result = self._wire_result(self._last_request,
                            'runtime_apply_failed_rolled_back', 'request was not durably started')
            while not self._closed.is_set():
                self._wake.wait(.1); self._wake.clear()
                with self._lock: request = self._queued; self._queued = None
                if request is None:
                    state = self._remote('status')
                    if state['epoch'] != epoch or state.get('recovering'):
                        raise RuntimeError('coordinator restarted')
                    continue
                current = store.current()
                if current.id != request['expected_digest']:
                    with self._lock: self._result = self._wire_result(request, 'conflict')
                    continue
                try: candidate = edit_candidate(store, current, request['changes'])
                except (ValueError, TypeError) as error:
                    with self._lock: self._result = self._wire_result(request, 'validation', str(error))
                    continue
                if checkpoint: checkpoint.wait_closed()
                if candidate.id == current.id:
                    with self._lock: self._result = self._wire_result(request, 'ok', 'unchanged')
                    continue
                store.record_request(request, candidate.id)
                checkpoint = CheckpointWorker(epoch, store, lambda: candidate)
                sequence += 1
                start = f'start {epoch} {sequence} {current.id} {candidate.id}'
                # start is idempotent; on transport loss inspect status, never
                # create another transaction or replay the user save operation.
                try: state = self._remote(start)
                except Exception: state = self._remote('status')
                while not self._closed.is_set():
                    if not checkpoint.alive:
                        raise RuntimeError('checkpoint worker exited; completed checkpoint recovery required')
                    if state['epoch'] != epoch or state['sequence'] != str(sequence):
                        raise RuntimeError('settings coordinator changed')
                    identity = (request['request_id'], epoch, str(sequence), current.id, candidate.id)
                    with self._lock:
                        self._confirmation = identity if state.get('awaitingConfirmation') else None
                        choice = self._confirmation_choice
                        self._confirmation_choice = None
                    if choice and choice[0] == identity and state.get('awaitingConfirmation'):
                        action = 'keep' if choice[1] else 'cancel'
                        state = self._remote(action + ' ' + ' '.join(identity[1:]))
                        continue
                    if state['state'] == 'unknown' and not state['command']:
                        state = self._remote(f'recover {epoch} {sequence} {current.id} {candidate.id}')
                    if state['state'] in ('complete', 'aborted', 'stale'):
                        committed = store.current()
                        if state['state'] == 'complete':
                            if committed.id != candidate.id or state['confirmed'] != candidate.id or participant.confirmed.id != candidate.id:
                                raise RuntimeError('joint completion mismatch')
                            config = generation_config(store, committed)
                            with self._lock:
                                self._snapshot = config, committed.id
                                self._documents = dict(committed.documents)
                        with self._lock:
                            self._restart_required = state.get('requiresSessionRestart', False)
                            self._result = self._wire_result(request, 'ok' if state['state']=='complete' else 'runtime_apply_failed_rolled_back')
                            self._result['digest'] = committed.id
                        break
                    command = state['command']
                    receipt = None
                    if command:
                        fields = command.split(' ')
                        if len(fields) != 8 or fields[1:3] != [epoch, str(sequence)] or fields[4:6] != [current.id, candidate.id]:
                            raise RuntimeError('settings command identity mismatch')
                        if fields[6] == 'store':
                            job = checkpoint.submit(command)
                            if job.status in ('ok', 'failed'): receipt = job.wire()
                        elif fields[6] == 'shell':
                            receipt = command + ' ' + participant.execute(fields) + ' -'
                        else: raise RuntimeError('invalid settings participant')
                    try: state = self._remote('receipt '+receipt if receipt else 'status')
                    except Exception:
                        self._closed.wait(.1)
                        try: state = self._remote('status')
                        except Exception: continue
                    self._closed.wait(.02)
        finally:
            with self._lock:
                self._confirmation = self._confirmation_choice = None
            # Keep ownership until queued writes and GTK callbacks are quiescent.
            # Joining here never blocks the GTK thread.
            if checkpoint:
                checkpoint.wait_closed()
            if lease: lease.close()

    def close(self):
        self._closed.set(); self._wake.set()
