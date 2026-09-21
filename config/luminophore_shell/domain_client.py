"""Settings UI worker client. The compositor alone commits complete generations."""
import time
import tomllib
from uuid import uuid4

from .ipc import IpcClient, IpcError
from .settings_store import StoreError


class DomainClient:
    def __init__(self, client=None, store=None):
        self.client = client or IpcClient()
        self.store = store
        self.last_request_id = None

    def snapshot(self):
        from .settings_generation import fixture_store
        store = self.store or fixture_store()
        current = store.current()
        if current is None:
            raise StoreError('설정 세대가 아직 초기화되지 않았습니다')
        return {name: tomllib.loads(text) for name, text in current.documents.items()}, current.id

    def request(self, command, **fields):
        reply = self.client.request({'command': command, **fields})
        if not reply.get('ok'):
            raise StoreError(reply.get('error', 'settings service unavailable'))
        return reply

    def start(self, changes, digest):
        identifier = 'domain-' + uuid4().hex
        self.last_request_id = identifier
        try:
            reply = self.request('settings-apply', changes=changes, expected_digest=digest, request_id=identifier)
        except IpcError:
            # The save may have reached the host. Only query its original ID.
            try:
                reply = self.request('settings-status', request_id=identifier)
            except (IpcError, StoreError) as error:
                raise StoreError('설정 완료 여부를 확인할 수 없습니다: ' + identifier) from error
            if not reply.get('found'):
                raise StoreError('설정 요청 상태를 확인할 수 없습니다: ' + identifier)
        if reply.get('request_id') != identifier:
            raise StoreError('settings receipt identity mismatch: ' + identifier)
        return reply

    def wait(self, reply, *, preview=False, timeout=30):
        end = time.monotonic() + timeout
        identifier = reply.get('request_id')
        if not identifier:
            raise StoreError('settings receipt identity missing')
        while reply.get('category') == 'completion_unknown':
            if preview and reply.get('awaiting_confirmation'):
                return reply
            if time.monotonic() >= end:
                raise StoreError('설정 완료 확인이 지연되고 있습니다. 재적용하지 말고 상태를 확인하세요: ' + reply['request_id'])
            time.sleep(.025)
            reply = self.request('settings-status', request_id=reply['request_id'])
            if not reply.get('found'):
                raise StoreError('설정 요청 상태를 확인할 수 없습니다')
            if reply.get('request_id') != identifier:
                raise StoreError('settings receipt identity mismatch: ' + identifier)
        if reply.get('category') not in ('ok', 'saved_pending_next_start'):
            raise StoreError(reply.get('message') or reply.get('category') or 'settings transaction failed')
        return reply

    def commit(self, changes, digest):
        return self.wait(self.start(changes, digest))
