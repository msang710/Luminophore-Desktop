"""Native joint participant ordering, restore and duplicate command recovery."""
from pathlib import Path
import unittest
from tests.domain_fixture import Domains
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_participant import ShellSettingsParticipant


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.domains=Domains(self)
        self.store=self.domains.store
        self.before=self.store.current()
        self.after=edit_candidate(self.store,self.before,{'visual.enabled':False,'compositor.border_size':7})
        self.store.prepare(self.after.documents,self.before.id)
        self.live=generation_config(self.store,self.before)
        self.writes=[]
        self.fail_read=False
        def apply(config): self.writes.append(config); self.live=config
        def verify(config):
            if self.fail_read or self.live!=config: raise RuntimeError('readback failed')
        self.participant=ShellSettingsParticipant(self.store,self.before,apply,verify)

    def execute(self, op, ticket='1', sequence='1'):
        return self.participant.execute(['1','a'*32,sequence,ticket,self.before.id,self.after.id,'shell',op])

    def test_prepare_does_not_write_and_duplicate_apply_never_replays(self):
        self.assertEqual(self.execute('prepare'),'ok')
        self.assertEqual(self.writes,[])
        self.assertEqual(self.execute('apply'),'ok')
        self.assertEqual(self.execute('apply'),'ok')
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.store.current().id,self.before.id)
        self.assertEqual(self.execute('verify'),'ok')
        self.assertEqual(self.execute('confirm'),'ok')
        self.assertEqual(self.participant.confirmed.id,self.after.id)
        # Only the store participant can publish a completed generation.
        self.assertEqual(self.store.current().id,self.before.id)

    def test_readback_failure_restores_whole_shell_without_publishing(self):
        self.execute('prepare'); self.execute('apply'); self.fail_read=True
        self.assertEqual(self.execute('verify'),'failed')
        self.assertEqual(self.execute('confirm'),'failed')
        self.assertEqual(self.participant.confirmed.id,self.before.id)
        self.fail_read=False
        self.assertEqual(self.execute('restore'),'ok')
        self.assertTrue(self.live.visual.enabled)
        self.assertEqual(self.live.compositor.border_size,generation_config(self.store,self.before).compositor.border_size)
        self.assertEqual(self.store.current().id,self.before.id)

    def test_lost_apply_receipt_recovers_via_verify_without_replay(self):
        self.execute('prepare'); self.execute('apply')
        self.assertEqual(self.execute('verify'),'ok')
        self.assertEqual(self.execute('apply'),'ok')
        self.assertEqual(len(self.writes),1)

    def test_new_restore_ticket_can_retry_failed_readback(self):
        self.execute('prepare'); self.execute('apply'); self.fail_read=True
        self.assertEqual(self.execute('restore'),'failed')
        self.fail_read=False
        self.assertEqual(self.execute('restore',ticket='2'),'ok')
        self.assertTrue(self.live.visual.enabled)

    def test_foreign_epoch_and_stale_sequence_cannot_apply(self):
        self.execute('prepare')
        with self.assertRaisesRegex(ValueError,'stale'):
            self.execute('apply',sequence='2')
        fields=['1','b'*32,'1','1',self.before.id,self.after.id,'shell','apply']
        with self.assertRaisesRegex(ValueError,'stale'): self.participant.execute(fields)
        self.assertEqual(self.writes,[])

    def test_external_runtime_change_blocks_confirmation(self):
        self.execute('prepare'); self.execute('apply')
        self.live=generation_config(self.store,self.before)
        self.assertEqual(self.execute('confirm'),'failed')
        self.assertEqual(self.participant.confirmed.id,self.before.id)
