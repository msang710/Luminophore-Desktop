"""Luminophore-owned scalar validation shared with the native compositor."""
from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from .generated_settings import SCHEMA


@dataclass(frozen=True, slots=True)
class OptionSpec:
    key: str
    value_type: type
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    pattern: str | None = None

    def validate(self, value: Any) -> Any:
        if type(value) is not self.value_type:
            raise ValueError(f'{self.key} must be {self.value_type.__name__}')
        if self.value_type is float and not math.isfinite(value):
            raise ValueError(f'{self.key} must be finite')
        if self.choices and value not in self.choices:
            raise ValueError(f'{self.key} must be one of {self.choices}')
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f'{self.key} must be at least {self.minimum}')
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f'{self.key} must be at most {self.maximum}')
        # The generated std::regex contract counts UTF-8 bytes, not codepoints.
        if self.pattern and not re.fullmatch(self.pattern.encode("ascii"), value.encode("utf-8")):
            raise ValueError(f"{self.key}: format")
        return value


_TYPES = {'int': int, 'float': float, 'bool': bool, 'str': str}
SPECS = {f['key']: OptionSpec(f['key'], _TYPES[f['type']], f['minimum'], f['maximum'], tuple(f['choices']), f.get('pattern'))
         for f in SCHEMA['fields']}
DEFAULTS = {f['key']: f['default'] for f in SCHEMA['fields']}


def validate(values: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(values) - SPECS.keys()
    if unknown:
        raise ValueError(f'unknown Luminophore settings: {sorted(unknown)}')
    resolved = {**DEFAULTS, **{k: SPECS[k].validate(v) for k, v in values.items()}}
    for constraint in SCHEMA['constraints']:
        if constraint['kind'] == 'less_equal':
            if resolved[constraint['left']] > resolved[constraint['right']]:
                raise ValueError(f"{constraint['left']}: ordering")
        elif constraint['kind'] == 'requires':
            if resolved[constraint['key']] == constraint['value'] and not resolved[constraint['requires']]:
                raise ValueError(f"{constraint['key']}: requires")
        else:
            raise ValueError('unknown schema constraint')
    return resolved
