import json
from pathlib import Path
import subprocess
import sys

from test_artifact import ArtifactFixture
from luminophore_runtime import inventory
from luminophore_runtime.common import ContractError


class ProviderTests(ArtifactFixture):
    def test_cli_classifies_explicit_policy_without_host_discovery(self):
        policy = self.root / 'policy.json'
        policy.write_text(json.dumps({'candidates': {n: p['path'] for n, p in self.providers.items()},
            'system': ['libc.so.6'], 'required_private': ['libanswer.so.1']}))
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'luminophore-runtime'),
            'classify-providers', '--sysroot', str(self.sysroot), '--policy', str(policy)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['libleaf.so.1']['owner'], 'private')

    def classify(self, system, private=()):
        return inventory.classify(self.sysroot,
            {name: p['path'] for name, p in self.providers.items()}, system, private)

    def test_system_dependencies_stay_system_and_private_is_preserved(self):
        result = self.classify(['libc.so.6', 'libanswer.so.1'])
        self.assertEqual(result['libleaf.so.1']['owner'], 'system')
        result = self.classify(['libc.so.6'], ['libanswer.so.1'])
        self.assertEqual(result['libleaf.so.1']['owner'], 'private')
        inventory.scan(self.sysroot, ['usr/bin/demo'], result)

    def test_required_private_cannot_be_promoted_by_system_dependency(self):
        with self.assertRaisesRegex(ContractError, 'private.*system'):
            self.classify(['libanswer.so.1'], ['libleaf.so.1'])

    def test_unresolved_system_dependency_fails_closed(self):
        del self.providers['libleaf.so.1']
        with self.assertRaisesRegex(ContractError, 'missing provider'):
            self.classify(['libanswer.so.1'])

    def test_wrong_soname_and_unknown_policy_seed_rejected(self):
        with self.assertRaisesRegex(ContractError, 'missing provider'):
            self.classify(['libunknown.so'])
        self.providers['libanswer.so.1']['path'] = 'usr/lib/libleaf.so.1'
        with self.assertRaisesRegex(ContractError, 'SONAME'):
            self.classify(['libanswer.so.1'])

    def test_policy_lists_reject_objects_strings_and_non_string_members(self):
        for bad in [{'libanswer.so.1': False}, 'libanswer.so.1', [None], [['libanswer.so.1']]]:
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                self.classify(bad)
            with self.subTest(private=bad), self.assertRaises(ContractError):
                self.classify(['libc.so.6'], bad)
