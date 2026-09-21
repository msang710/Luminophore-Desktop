from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from luminophore_shell.settings_protocol import encode, decode, validate_remote, ENDPOINT, SettingsProtocolError

ROOT = Path(__file__).resolve().parents[2]
ID = 'a' * 32


class SettingsProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        source = root/'probe.cpp'
        source.write_text('#include "SettingsProtocol.hpp"\n#include <iostream>\nint main(){std::string s;std::getline(std::cin,s);std::cout<<Luminophore::Settings::validateRequest(s);}')
        native = ROOT/'compositor/src/config/luminophore'
        cls.binary = root/'probe'
        subprocess.run(['g++','-std=c++23','-Wall','-Wextra','-Werror','-I',str(native),str(source),
                        str(native/'GeneratedSettings.cpp'),str(native/'SettingsProtocol.cpp'),'-o',str(cls.binary)],check=True,capture_output=True)

    def native(self, wire):
        return subprocess.run([str(self.binary)],input=wire,text=True,capture_output=True,check=True).stdout

    def test_defaults_and_partial_values_roundtrip(self):
        for values in ({}, {'motion.preset':'smooth','motion.speed':1.25}, {'visual.enabled':False}, {'compositor.gaps_in':10}):
            wire, expected = encode(values, ID)
            self.assertEqual(decode(self.native(wire), ID, expected), expected)

    def test_text_strings_roundtrip_without_token_loss(self):
        values = {'compositor.font_family': '본고딕 Sans', 'compositor.cursor_start_output': '',
                  'compositor.shadow_color': 'ee112233 ff445566 90deg'}
        wire, expected = encode(values, ID)
        self.assertIn('compositor.cursor_start_output h -', wire)
        self.assertEqual(decode(self.native(wire), ID, expected), expected)

    def test_utf8_length_limit_matches_native(self):
        wire, expected = encode({'compositor.font_family': '글' * 85}, ID)
        self.assertEqual(decode(self.native(wire), ID, expected), expected)
        with self.assertRaises(ValueError): encode({'compositor.font_family': '글' * 86}, ID)
        self.assertEqual(self.native(f'1 {ID} 1 compositor.font_family h ' + ('글' * 86).encode().hex()), f'1 {ID} invalid')

    def test_encoded_strings_reject_invalid_hex_and_utf8(self):
        wire, expected = encode({}, ID)
        reply = self.native(wire)
        for token in ('0', 'gg', 'ff', 'c080', 'eda080', 'f4908080', 'e282', '6162zz'):
            with self.subTest(token=token):
                self.assertEqual(self.native(f'1 {ID} 1 compositor.font_family h {token}'), f'1 {ID} invalid')
                bad = reply.replace('compositor.cursor_start_output h -', f'compositor.cursor_start_output h {token}')
                with self.assertRaises(SettingsProtocolError): decode(bad, ID, expected)

    def test_malformed_and_invalid_native_requests(self):
        for suffix in ('1 motion.speed f nan','1 motion.speed f inf','1 motion.speed i 1',
                       '1 unknown i 1','1 compositor.gaps_in i 999999999999999999999999',
                       '1 visual.enabled b 1','1 motion.preset s evil','1 motion.speed f 1junk',
                       '2 motion.speed f 1 motion.speed f 2','0 trailing','22',
                       '2 compositor.active_opacity f 0.4 compositor.inactive_opacity f 0.9'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.native('1 '+ID+' '+suffix), '1 '+ID+' invalid')
        for wire in ('2 '+ID+' 0', '1 nope 0', 'a'*8193, '1 '+ID+' 0\x00'):
            self.assertEqual(self.native(wire), '1 - invalid')

    def test_response_correlation_completeness_and_parity(self):
        wire, expected = encode({}, ID)
        reply = self.native(wire)
        for bad in (reply.replace(ID,'b'*32),reply.replace('validated','applied'),reply+' extra',
                    reply.replace('motion.speed f 1','motion.speed f 2'), reply.replace('motion.speed','motion.enabled')):
            with self.assertRaises(SettingsProtocolError): decode(bad,ID,expected)

    def test_remote_uses_only_read_only_endpoint(self):
        client = Mock()
        client._run.side_effect = lambda name, wire: self.native(wire) + "\n"
        result = validate_remote(client, {'compositor.default_view_columns':1})
        self.assertEqual(result['compositor.default_view_columns'],1)
        self.assertEqual(client._run.call_args.args[0],ENDPOINT)
        self.assertEqual(client._run.call_count,1)
        client._run.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError): validate_remote(client,{})
        self.assertEqual(client._run.call_count,2)
