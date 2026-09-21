import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from luminophore_shell.placement_rules import PlacementRules, encode
from luminophore_shell.hyprland import HyprlandClient, HyprlandError

class PlacementTests(unittest.TestCase):
    def test_roundtrip_remove_and_stale_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            from tests.domain_fixture import Domains
            domains = Domains(self)
            service = PlacementRules(service=domains)
            _, digest = service.snapshot()
            rules, fresh = service.set('org.example.App', 'left', digest)
            self.assertEqual(rules, {'org.example.App': 'left'})
            with self.assertRaises(ValueError):
                service.set('other', 'right', digest)
            self.assertEqual(service.set('org.example.App', 'default', fresh)[0], {})
    def test_invalid_input_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.lua'
            from tests.domain_fixture import Domains
            service = PlacementRules(service=Domains(self))
            for app, direction in [('', 'right'), ('app\nfoo','right'), ('app', 'north'), ('app', 1)]:
                with self.assertRaises(ValueError):
                    service.set(app, direction, service.snapshot()[1])
                self.assertFalse(path.exists())
    def test_quotes_and_regex_are_literals(self):
        payload = encode({'a.b[1]"\\': 'up'}).decode()
        self.assertIn('a\\\\.b\\\\[1\\\\]', payload)
        self.assertEqual(json.loads(payload.splitlines()[0].split(' ',2)[2]), {'a.b[1]"\\': 'up'})
    def test_modified_generation_not_silently_overwritten(self):
        from tests.domain_fixture import Domains
        domains = Domains(self)
        current = domains.store.current()
        (domains.store.generations/current.id/'placement.toml').write_text('broken')
        with self.assertRaises(ValueError): PlacementRules(service=domains).snapshot()
    def test_failed_atomic_write_keeps_previous_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            from tests.domain_fixture import Domains
            domains = Domains(self)
            service = PlacementRules(service=domains)
            rules, digest = service.set('app','right',service.snapshot()[1])
            domains.fail = OSError('disk')
            with self.assertRaises(OSError):
                service.set('app','left',digest)
            self.assertEqual(service.snapshot(), (rules,digest))
    def test_reload_reports_config_errors_and_does_not_retry(self):
        client = HyprlandClient()
        client._run = Mock(side_effect=['ok', 'bad config'])
        with self.assertRaises(HyprlandError):
            client.reload_config()
        self.assertEqual(client._run.call_count, 2)
        client._run = Mock(side_effect=HyprlandError('timeout'))
        with self.assertRaises(HyprlandError):
            client.reload_config()
        client._run.assert_called_once()

    def test_generated_file_executes_as_lua_data_without_code_injection(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.lua'
            path.write_bytes(encode({'app"; error("injected") --': 'down', '한글앱': 'up'}))
            result = subprocess.run(['lua', '-e', 'local rows=dofile(arg[0]); assert(#rows==2); assert(rows[1].direction=="down"); print(rows[2].app)', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('한글앱', result.stdout)
