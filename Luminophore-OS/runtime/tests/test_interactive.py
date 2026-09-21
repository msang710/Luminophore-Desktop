import os
from pathlib import Path
import unittest
from unittest.mock import patch

from luminophore_runtime.session import Session
from luminophore_runtime.common import ContractError


class InteractiveTests(unittest.TestCase):
    def session(self):
        return Session(Path('/release'), {'generation': 'a' * 64,
            'entries': {'shell': {'path': 'python/bin/python3', 'args': []},
                        'shell_cli': {'path': 'python/bin/python3', 'args': []}},
            'runtime_env': {}, 'compatibility': {'config': 1}}, 9, '/store', '/')

    def test_internal_bloom_survives_but_does_not_leak_to_apps(self):
        from luminophore_runtime.session import application_environment
        with patch('luminophore_runtime.artifact.verify'), patch('luminophore_runtime.artifact.preflight'):
            _, env = self.session().command('shell', environment={'LUMINOPHORE_SHELL_BLOOM': '1'})
        self.assertEqual(env.get('LUMINOPHORE_SHELL_BLOOM'), '1')
        self.assertNotIn('LUMINOPHORE_SHELL_BLOOM', application_environment(env))

    def test_interactive_context_does_not_reverify_the_whole_generation(self):
        session = self.session()
        session.interactive = True
        with patch('luminophore_runtime.artifact.verify') as verify, patch('luminophore_runtime.artifact.preflight') as preflight:
            session.command('shell_cli', ['ctl', 'status'], environment={})
        verify.assert_not_called()
        preflight.assert_not_called()

    def test_interactive_context_cannot_launch_a_session_service(self):
        session = self.session()
        session.interactive = True
        with patch('luminophore_runtime.artifact.verify'), patch('luminophore_runtime.artifact.preflight'), self.assertRaisesRegex(ContractError, 'interactive'):
            session.command('shell', environment={})
