"""Nonblocking checkpoint adapter for commands owned by the compositor.

This worker does disk I/O only. It cannot initiate runtime changes or decide that
participants have applied. The host must restrict submissions to its connected
coordinator epoch. Receipts are process-local; after process loss use cold-start
recovery, not a guessed/replayed mutation receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
import re
import threading
from typing import Callable

from .settings_store import Generation, SettingsStore, StoreError

_TOKEN = re.compile(r'[0-9a-f]+')
_OPERATIONS = frozenset({'current', 'prepare', 'publish', 'abandon'})
_MAX_U64 = (1 << 64) - 1


def _hex(value: str, length: int) -> bool:
    return len(value) == length and _TOKEN.fullmatch(value) is not None


def _number(value: str) -> int:
    if not value.isascii() or not value.isdigit() or value.startswith('0') or len(value) > 20:
        raise ValueError('invalid request counter')
    number = int(value)
    if number > _MAX_U64:
        raise ValueError('request counter overflow')
    return number


@dataclass(frozen=True)
class CheckpointCommand:
    epoch: str
    sequence: int
    ticket: int
    base: str
    candidate: str
    operation: str

    @classmethod
    def parse(cls, wire: str):
        if type(wire) is not str or len(wire) > 512 or not wire.isascii():
            raise ValueError('invalid checkpoint command')
        parts = wire.split(' ')
        if len(parts) != 8:
            raise ValueError('invalid checkpoint command shape')
        version, epoch, sequence, ticket, base, candidate, target, operation = parts
        if (version != '1' or target != 'store' or not _hex(epoch, 32)
                or not _hex(base, 64) or not _hex(candidate, 64) or base == candidate
                or operation not in _OPERATIONS):
            raise ValueError('invalid checkpoint command fields')
        return cls(epoch, _number(sequence), _number(ticket), base, candidate, operation)

    def wire(self):
        return f'1 {self.epoch} {self.sequence} {self.ticket} {self.base} {self.candidate} store {self.operation}'


@dataclass(frozen=True)
class CheckpointReceipt:
    command: CheckpointCommand
    status: str  # queued/running/ok/failed; queued/running are not terminal.
    current: str = ''
    error: str = ''

    def wire(self):
        status = self.status if self.status in ('ok', 'failed') else 'unknown'
        return f'{self.command.wire()} {status} {self.current or "-"}'


class CheckpointWorker:
    """One operation in flight; duplicate submission only returns its receipt.

    Constructor/submit/receipt never read disk or wait for a worker. Completion
    callbacks are deliberately absent: hosts poll receipts from their event loop.
    A timeout must not call close/recreate and resubmit the write.
    """
    def __init__(self, epoch: str, store: SettingsStore,
                 candidate: Callable[[], Generation] | None = None):
        if type(epoch) is not str or not _hex(epoch, 32):
            raise ValueError('invalid coordinator epoch')
        self.epoch = epoch
        self.store = store
        self.candidate = candidate or store.read_candidate
        self._lock = threading.Lock()
        self._receipt: CheckpointReceipt | None = None
        self._closed = False
        self._queue = Queue()
        self._thread = threading.Thread(target=self._run, name='luminophore-settings-disk', daemon=True)
        self._thread.start()

    def submit(self, wire: str) -> CheckpointReceipt:
        command = CheckpointCommand.parse(wire)
        with self._lock:
            if self._closed:
                raise RuntimeError('checkpoint worker closed')
            if command.epoch != self.epoch:
                raise ValueError('stale coordinator epoch')
            previous = self._receipt
            if previous:
                if command.ticket == previous.command.ticket:
                    if command != previous.command:
                        raise ValueError('reused command identity')
                    return previous
                if command.ticket < previous.command.ticket or command.sequence < previous.command.sequence:
                    raise ValueError('stale checkpoint command')
                if previous.status in ('queued', 'running'):
                    raise RuntimeError('checkpoint operation still running')
                if command.sequence == previous.command.sequence and (
                        command.base != previous.command.base or command.candidate != previous.command.candidate):
                    raise ValueError('changed transaction identity')
            self._receipt = CheckpointReceipt(command, 'queued')
            self._queue.put_nowait(command)
            return self._receipt

    def receipt(self, wire: str) -> CheckpointReceipt | None:
        command = CheckpointCommand.parse(wire)
        with self._lock:
            if self._receipt and self._receipt.command == command:
                return self._receipt
            return None

    def close(self):
        """Prevent new commands; queued commands cancel, running I/O finishes.

        Does not wait. Existing receipt remains queryable until object disposal.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Worker checks closed before starting queued work; a running task
            # will consume this sentinel after publishing its terminal receipt.
        self._queue.put_nowait(None)

    def wait_closed(self):
        """Join only from a worker/shutdown context, never the GTK thread."""
        self.close()
        self._thread.join()

    @property
    def alive(self):
        return self._thread.is_alive()

    def _run(self):
        while True:
            command = self._queue.get()
            if command is None:
                return
            with self._lock:
                if self._closed:
                    self._receipt = CheckpointReceipt(command, 'failed', error='cancelled before execution')
                    continue
                self._receipt = CheckpointReceipt(command, 'running')
            try:
                current = self._execute(command)
                receipt = CheckpointReceipt(command, 'ok', current)
            except Exception as error:
                # SettingsStore is synchronous: exception means no write remains
                # in flight, but a rename may already have succeeded. Coordinator
                # queries current before deciding forward recovery/compensation.
                receipt = CheckpointReceipt(command, 'failed', error=str(error))
            with self._lock:
                self._receipt = receipt

    def _execute(self, command: CheckpointCommand) -> str:
        if command.operation == 'current':
            current = self.store.current()
            if current is None:
                raise StoreError('no completed generation; native bootstrap required')
            return current.id
        if command.operation == 'prepare':
            candidate = self.candidate()
            if candidate.id != command.candidate:
                raise StoreError('candidate changed before preparation')
            prepared = self.store.prepare(candidate.documents, command.base)
            if prepared.id != command.candidate:
                raise StoreError('prepared generation mismatch')
        elif command.operation == 'publish':
            self.store.publish(command.candidate, command.base)
        else:
            self.store.abandon(command.candidate, expected=command.base)
        return ''
