from pathlib import Path
import subprocess
import select
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_checkpoint import CheckpointCommand, CheckpointWorker
from luminophore_shell.settings_store import FILES, SettingsPaths, ConflictError
from tests.test_settings_async import compile_async_probe

EPOCH = 'a'*32


class CheckpointFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = settings_store(SettingsPaths(self.root/'config',self.root/'state',self.root/'cache'))
        self.docs = {name:'schema_version = 1\n' for name in FILES}
        self.base = self.store.prepare(self.docs,'')
        self.store.publish(self.base.id,'')
        self.docs = dict(self.docs, **{'settings.toml':'schema_version = 1\n[motion]\nspeed = 1.5\n'})
        self.candidate = self.store._candidate(self.docs)
        self.worker = CheckpointWorker(EPOCH,self.store,lambda:self.candidate)
        self.addCleanup(self.close_worker)

    def close_worker(self):
        self.worker.close()
        self.worker._thread.join(3)
        self.assertFalse(self.worker._thread.is_alive())

    def wire(self, operation, ticket=1, **changes):
        values=dict(epoch=EPOCH,sequence=1,ticket=ticket,base=self.base.id,candidate=self.candidate.id,operation=operation)
        values.update(changes)
        return CheckpointCommand(**values).wire()

    def wait(self, wire):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            receipt=self.worker.receipt(wire)
            if receipt and receipt.status in ('ok','failed'):
                return receipt
            time.sleep(.001)
        self.fail('checkpoint worker did not finish')

    def execute(self, operation, ticket):
        wire=self.wire(operation,ticket)
        self.worker.submit(wire)
        return self.wait(wire)


class CheckpointTests(CheckpointFixture, unittest.TestCase):
    def test_prepare_keeps_old_checkpoint_and_publish_updates_all_files(self):
        self.assertEqual(self.execute('prepare',1).status,'ok')
        self.assertEqual(self.store.current().id,self.base.id)
        self.assertEqual(self.execute('publish',2).status,'ok')
        self.assertEqual(dict(self.store.current().documents),self.docs)
        self.assertEqual(self.execute('current',3).current,self.candidate.id)

    def test_duplicate_running_and_lost_receipt_do_not_repeat_write(self):
        started,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        original=self.store.prepare
        calls=[]
        def blocked(*args):
            calls.append(1);started.set();release.wait(3)
            return original(*args)
        with patch.object(self.store,'prepare',blocked):
            wire=self.wire('prepare')
            self.worker.submit(wire)
            self.assertTrue(started.wait(1))
            self.assertEqual(self.worker.submit(wire).status,'running')
            self.assertIn(' unknown -',self.worker.receipt(wire).wire())
            with self.assertRaises(RuntimeError): self.worker.submit(self.wire('publish',2))
            release.set()
            done=self.wait(wire)
            self.assertEqual(self.worker.submit(wire),done)
            self.assertEqual(calls,[1])

    def test_exception_after_publication_is_resolved_by_current_read(self):
        self.execute('prepare',1)
        original=self.store.publish
        def lost(*args):
            original(*args)
            raise OSError('reply lost after publication')
        with patch.object(self.store,'publish',lost):
            self.assertEqual(self.execute('publish',2).status,'failed')
        self.assertEqual(self.execute('current',3).current,self.candidate.id)
        self.assertEqual(self.execute('abandon',4).status,'failed')
        self.assertEqual(self.store.current().id,self.candidate.id)

    def test_prepare_rejects_edited_candidate_before_writing(self):
        self.worker.candidate=lambda:self.base
        self.assertEqual(self.execute('prepare',1).status,'failed')
        self.assertFalse((self.store.root/'pending.json').exists())

    def test_identity_fences_and_strict_wire(self):
        wire=self.wire('current')
        self.worker.submit(wire);self.wait(wire)
        with self.assertRaises(ValueError): self.worker.submit(self.wire('prepare'))
        with self.assertRaises(ValueError): self.worker.submit(self.wire('current',2,epoch='f'*32))
        for invalid in (wire+'\n',wire.replace(' 1 1 ',' 01 1 '),wire.replace(' store ',' shell '),wire+' extra',wire.replace(' current',' eval')):
            with self.assertRaises(ValueError): CheckpointCommand.parse(invalid)
        self.worker.submit(self.wire('current',2));self.wait(self.wire('current',2))
        with self.assertRaises(ValueError): self.worker.submit(wire)

    def test_abandon_requires_matching_base_inside_store_lock(self):
        self.store.prepare(self.docs,self.base.id)
        with self.assertRaises(ConflictError): self.store.abandon(self.candidate.id,expected='d'*64)
        self.assertTrue((self.store.root/'pending.json').exists())
        self.assertEqual(self.execute('abandon',1).status,'ok')
        self.assertFalse((self.store.root/'pending.json').exists())

    def test_close_does_not_wait_for_running_io_and_receipt_survives(self):
        started,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        original=self.store.current
        def blocked():
            started.set();release.wait(3);return original()
        with patch.object(self.store,'current',blocked):
            wire=self.wire('current');self.worker.submit(wire)
            self.assertTrue(started.wait(1))
            self.worker.close()
            self.assertEqual(self.worker.receipt(wire).status,'running')
            with self.assertRaises(RuntimeError): self.worker.submit(self.wire('current',2))
            release.set()
            self.assertEqual(self.wait(wire).status,'ok')


class NativeCheckpointIntegrationTests(CheckpointFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe_temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.probe_temp.cleanup)
        cls.binary=compile_async_probe(Path(cls.probe_temp.name))

    def test_native_commands_drive_real_five_file_store_with_delayed_receipts(self):
        process=subprocess.Popen([str(self.binary),'20',self.base.id,self.candidate.id],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if process.poll() is None: process.kill()
            process.communicate()
        self.addCleanup(cleanup)
        operations=[]
        while True:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], 'native coordinator stalled')
            wire=process.stdout.readline().rstrip('\n')
            if wire=='complete':break
            self.assertTrue(wire)
            command=CheckpointCommand.parse(wire)
            operations.append(command.operation)
            self.worker.submit(wire)
            # Force an UNKNOWN transport outcome before the terminal receipt.
            process.stdin.write(wire+' unknown -\n');process.stdin.flush()
            result=self.wait(wire)
            self.assertEqual(result.status,'ok',result.error)
            process.stdin.write(result.wire()+'\n');process.stdin.flush()
        process.stdin.close();process.stdin=None
        _,error=process.communicate(timeout=3)
        self.assertEqual(process.returncode,0,error)
        self.assertEqual(operations,['current','prepare','publish','current'])
        self.assertEqual(dict(self.store.current().documents),self.docs)
