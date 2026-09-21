from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.settings_store import (
    FILES, ConflictError, SettingsPaths, SettingsStore, StoreError,
)


def documents(text=''):
    return {name: 'schema_version = 1\n' + (text if name == 'settings.toml' else '') for name in FILES}


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = SettingsPaths(root / 'config', root / 'state', root / 'cache')
        self.store = SettingsStore(self.paths, lambda parsed: None)

    def test_paths_use_xdg_without_creating_directories(self):
        paths = SettingsPaths.current({}, Path(self.temp.name))
        self.assertEqual(paths.config, Path(self.temp.name) / '.config/luminophore')
        self.assertFalse(paths.config.exists())
        with self.assertRaises(StoreError):
            SettingsPaths.current({'XDG_CONFIG_HOME': 'relative'}, Path(self.temp.name))

    def test_prepare_is_not_commit_and_comments_survive(self):
        candidate = self.store.prepare(documents('# user comment\n'), '')
        self.assertIsNone(self.store.current())
        current = self.store.publish(candidate.id, '')
        self.assertEqual(current.documents, documents('# user comment\n'))
        with self.assertRaises(TypeError):
            current.documents['settings.toml'] = 'changed'

    def test_restart_discards_unfinished_generation(self):
        first = self.store.prepare(documents(), '')
        self.store.publish(first.id, '')
        self.store.prepare(documents('# next\n'), first.id)
        restarted = SettingsStore(self.paths, lambda parsed: None)
        self.assertEqual(restarted.recover().id, first.id)
        self.assertFalse((restarted.root / 'pending.json').exists())

    def test_concurrent_candidates_do_not_overwrite_each_other(self):
        def prepare(i):
            try:
                return self.store.prepare(documents(f'# {i}\n'), '').id
            except ConflictError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(prepare, [1, 2]))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_stale_base_rejected(self):
        candidate = self.store.prepare(documents(), '')
        self.store.publish(candidate.id, '')
        with self.assertRaises(ConflictError):
            self.store.prepare(documents('# next\n'), '')

    def test_publish_requires_preparation(self):
        first = self.store.prepare(documents(), '')
        self.store.abandon(first.id)
        with self.assertRaises(ConflictError):
            self.store.publish(first.id, '')

    def test_incomplete_or_invalid_toml_rejected_without_writes(self):
        for bundle in ({'settings.toml': 'schema_version = 1'}, documents('broken = ['),
                       {name: 'schema_version = true' for name in FILES}):
            with self.subTest(bundle=bundle), self.assertRaises(StoreError):
                self.store.prepare(bundle, '')
        self.assertFalse(self.paths.state.exists())

    def test_product_validation_failure_does_not_write(self):
        def reject(parsed):
            raise ValueError('invalid product settings')
        store = SettingsStore(self.paths, reject)
        with self.assertRaises(ValueError):
            store.prepare(documents(), '')
        self.assertFalse(self.paths.state.exists())

    def test_tampered_generation_rejected(self):
        candidate = self.store.prepare(documents(), '')
        (self.store.generations / candidate.id / 'settings.toml').write_text('schema_version = 1\n# tamper')
        with self.assertRaisesRegex(StoreError, 'digest'):
            self.store.publish(candidate.id, '')
        self.assertIsNone(self.store.current())

    def test_write_failure_preserves_completed_pointer(self):
        first = self.store.prepare(documents(), '')
        self.store.publish(first.id, '')
        with patch('luminophore_shell.settings_store._atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.prepare(documents('# next\n'), first.id)
        self.assertEqual(self.store.current().id, first.id)

    def test_failed_publish_can_be_queried_and_retried(self):
        candidate = self.store.prepare(documents(), '')
        with patch('luminophore_shell.settings_store._atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.publish(candidate.id, '')
        self.assertIsNone(self.store.current())
        self.assertEqual(self.store.publish(candidate.id, '').id, candidate.id)

    def test_idempotent_prepare_and_publish(self):
        first = self.store.prepare(documents(), '')
        self.assertEqual(self.store.prepare(documents(), '').id, first.id)
        self.store.publish(first.id, '')
        self.assertEqual(self.store.publish(first.id, '').id, first.id)

    def test_noop_apply_does_not_block_next_candidate(self):
        first = self.store.prepare(documents(), '')
        self.store.publish(first.id, '')
        self.store.prepare(documents(), first.id)
        self.store.publish(first.id, first.id)
        self.store.prepare(documents('# next\n'), first.id)

    def test_delayed_completion_does_not_clear_newer_candidate(self):
        first = self.store.prepare(documents(), '')
        self.store.publish(first.id, '')
        second = self.store.prepare(documents('# next\n'), first.id)
        self.store.publish(first.id, '')
        self.assertEqual(self.store.publish(second.id, first.id).id, second.id)

    def test_failure_after_pointer_rename_is_resolved_by_query(self):
        from luminophore_shell.settings_store import _atomic
        first = self.store.prepare(documents(), '')
        def write_then_fail(path, value):
            _atomic(path, value)
            raise OSError('lost response after durable rename')
        with patch('luminophore_shell.settings_store._atomic', side_effect=write_then_fail):
            with self.assertRaises(OSError):
                self.store.publish(first.id, '')
        self.assertEqual(self.store.current().id, first.id)
        self.assertEqual(self.store.publish(first.id, '').id, first.id)
        self.assertFalse((self.store.root / 'pending.json').exists())

    def test_external_edit_during_validation_is_rejected(self):
        self.paths.config.mkdir()
        for name, text in documents().items():
            (self.paths.config / name).write_text(text)
        def edit(parsed):
            (self.paths.config / 'settings.toml').write_text('schema_version = 1\n# concurrent edit')
        store = SettingsStore(self.paths, edit)
        with self.assertRaises(ConflictError):
            store.read_candidate()

    def test_config_file_edits_are_only_candidates(self):
        self.paths.config.mkdir()
        for name, text in documents().items():
            (self.paths.config / name).write_text(text)
        candidate = self.store.read_candidate()
        self.assertEqual(candidate.documents, documents())
        self.assertIsNone(self.store.current())


if __name__ == '__main__':
    unittest.main()
