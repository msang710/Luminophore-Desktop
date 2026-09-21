"""Declarative desktop domains are data, never executable Lua."""
import unittest

from luminophore_shell.native_domains import decode_native


class NativeDomainTests(unittest.TestCase):
    def test_desktop_domains_round_trip(self):
        data = {
            'environment': {'QT_IM_MODULE': 'fcitx'},
            'startup': ['steam -silent'],
            'shutdown': [],
            'applications': {'terminal': 'ghostty'},
            'gestures': [{'fingers': 3, 'direction': 'down', 'action': 'close'}],
            'window_rules': [{'match': {'class': '^steam$'}, 'effects': {'float': True}}],
            'layer_rules': [{'match': {'namespace': '^luminophore-shell-.*$'}, 'effects': {'blur': True}}],
            'workspace_rules': [{'workspace': 'name:luminophore-base-DP-2', 'monitor': 'DP-2', 'persistent': True}],
        }
        decoded = decode_native(data)
        for key, value in data.items():
            self.assertEqual(decoded[key], value)

    def test_unknown_or_wrongly_typed_domain_rejected(self):
        for value in ({'lua': 'return 1'}, {'startup': 'steam'}, {'environment': {'A': 1}},
                      {'gestures': [{'fingers': True, 'direction': 'up', 'action': 'close'}]},
                      {'window_rules': [{'match': {}, 'effects': {'float': True}}]},
                      {'applications': {'terminal': 'a\x00b'}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_native(value)

    def test_rules_require_supported_effects_and_match_properties(self):
        for value in ({'match': {'bogus': 'x'}, 'effects': {'float': True}},
                      {'match': {'class': 'x'}, 'effects': {'bogus': True}}):
            with self.assertRaises(ValueError):
                decode_native({'window_rules': [value]})
