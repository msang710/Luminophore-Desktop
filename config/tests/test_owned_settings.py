import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from luminophore_shell.generated_settings import SCHEMA
from luminophore_shell.owned_settings import DEFAULTS, SPECS, validate
from luminophore_shell.config import CompositorConfig, MotionConfig
from luminophore_shell.hyprland_settings import HYPRLAND_OPTION_SPECS


ROOT = Path(__file__).resolve().parents[2]


class OwnedSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        source = directory / 'probe.cpp'
        source.write_text(r'''
#include "GeneratedSettings.hpp"
#include <iostream>
#include <iomanip>
#include <string>
int main(int argc, char**) {
    using namespace Luminophore::Settings;
    if (validate({{"motion.enabled", false}, {"motion.preset", std::string{"custom"}}}).empty()) return 2;
    if (validate({{"compositor.active_opacity", 0.5}, {"compositor.inactive_opacity", 0.9}}).empty()) return 3;
    if (argc > 1) {
        for (const auto& [key, value] : defaults()) {
            std::cout << key << " ";
            std::visit([](const auto& v) { std::cout << std::boolalpha << std::setprecision(17) << v; }, value);
            std::cout << '\n';
        }
        return 0;
    }
    std::string key, tag, text;
    while (std::cin >> key >> tag) {
        std::cin.get();
        std::getline(std::cin, text);
        Value value;
        if (tag == "int") value = int64_t{std::stoll(text)};
        else if (tag == "float") value = std::stod(text);
        else if (tag == "bool") value = text == "true";
        else value = text;
        std::cout << validate({{key, value}}).empty() << '\n';
    }
}
''')
        cls.binary = directory / 'probe'
        native = ROOT / 'compositor/src/config/luminophore'
        subprocess.run(['g++', '-std=c++23', '-Wall', '-Wextra', '-Werror', '-I', str(native),
                        str(source), str(native / 'GeneratedSettings.cpp'), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def test_generated_files_are_current(self):
        subprocess.run([sys.executable, str(ROOT / 'config/schema/generate_settings.py'), '--check'], check=True)

    def test_defaults_owned_by_schema(self):
        self.assertEqual(validate({}), DEFAULTS)
        self.assertEqual(CompositorConfig().default_view_columns, 1)
        self.assertEqual(CompositorConfig().default_view_rows, 1)
        for section, value in [('compositor', CompositorConfig()), ('motion', MotionConfig())]:
            for key in value.__dataclass_fields__:
                self.assertEqual(getattr(value, key), DEFAULTS[section + '.' + key])
        for key, spec in HYPRLAND_OPTION_SPECS.items():
            self.assertIs(spec, SPECS[key])
        result = subprocess.run([str(self.binary), 'defaults'], capture_output=True, text=True, check=True)
        native = {}
        for line in result.stdout.splitlines():
            key, value = line.split(' ', 1)
            kind = SPECS[key].value_type
            native[key] = value == 'true' if kind is bool else kind(value)
        self.assertEqual(native, DEFAULTS)

    def test_python_and_native_accept_same_values(self):
        cases = []
        for field in SCHEMA['fields']:
            key, kind, value = field['key'], field['type'], field['default']
            cases.append((key, kind, value))
            cases.append((key, 'str' if kind != 'str' else 'int', 'wrong' if kind != 'str' else 1))
            if field['minimum'] is not None:
                cases.append((key, kind, field['minimum'] - 1))
            if field['maximum'] is not None:
                cases.append((key, kind, field['maximum'] + 1))
        cases += [('unknown.option', 'int', 1), ('motion.speed', 'float', float('nan')),
                  ('visual.intensity', 'float', float('inf'))]
        lines, expected = [], []
        for key, kind, value in cases:
            value = {'int': int, 'float': float, 'bool': bool, 'str': str}[kind](value)
            wire = str(value).lower() if kind == 'bool' else str(value)
            lines.append(f'{key} {kind} {wire}')
            try:
                validate({key: value})
                expected.append('1')
            except ValueError:
                expected.append('0')
        result = subprocess.run([str(self.binary)], input='\n'.join(lines) + '\n', capture_output=True,
                                text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), expected)

    def test_cross_field_constraints(self):
        with self.assertRaisesRegex(ValueError, 'ordering'):
            validate({'compositor.active_opacity': .5, 'compositor.inactive_opacity': .9})
        with self.assertRaisesRegex(ValueError, 'requires'):
            validate({'motion.enabled': False, 'motion.preset': 'custom'})
        self.assertEqual(validate({'motion.enabled': False})['motion.enabled'], False)


if __name__ == '__main__':
    unittest.main()
