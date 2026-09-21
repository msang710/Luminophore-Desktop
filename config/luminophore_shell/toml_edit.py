"""Data-only TOML edits with semantic readback and unchanged text preservation."""
from copy import deepcopy
import json
import re
import tomllib


def literal(value):
    if type(value) is dict:
        return '{' + ', '.join(json.dumps(k, ensure_ascii=False) + ' = ' + literal(v) for k, v in value.items()) + '}'
    if type(value) in (list, tuple):
        return '[' + ', '.join(literal(v) for v in value) + ']'
    if type(value) not in (str, int, float, bool):
        raise ValueError('unsupported TOML value')
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def patch(text, path, value):
    """Replace one declared path; reject ambiguous syntax instead of losing data."""
    before = tomllib.loads(text)
    expected = deepcopy(before)
    parts = path.split('.')
    parent = expected
    for part in parts[:-1]:
        parent = parent.setdefault(part, {})
        if type(parent) is not dict:
            raise ValueError('setting parent must be a table')
    parent[parts[-1]] = value
    if len(parts) > 1 and re.search(r'(?m)^\s*(?:' + re.escape(parts[0]) + '|' + re.escape(json.dumps(parts[0])) + r')\s*=\s*\{', text):
        return patch(text, parts[0], expected[parts[0]])
    # Preserve every unrelated line, including comments. Structured values are
    # written as inline data so a replaced table cannot capture later fields.
    lines = text.splitlines(keepends=True)
    section = ''
    kept = []
    inserted = False
    for line in lines:
        match = re.fullmatch(r'\s*\[([^\[\]]+)\]\s*(?:#.*)?\n?', line)
        if match:
            probe = tomllib.loads('[' + match[1] + ']\n__section_probe__=true\n')
            components = []
            while '__section_probe__' not in probe:
                key, probe = next(iter(probe.items()))
                components.append(key)
            section = '.'.join(components)
        if section == path or section.startswith(path + '.'):
            continue
        parent_path = '.'.join(parts[:-1])
        assignment = re.match(r'\s*(?:' + re.escape(parts[-1]) + '|' + re.escape(json.dumps(parts[-1])) + r')\s*=', line)
        if section == parent_path and assignment:
            # A single-line value has an unambiguous boundary. Multiline values
            # are deliberately rejected by the final semantic comparison.
            comment = ''; quote = None; escaped = False
            for index, char in enumerate(line):
                if escaped: escaped = False; continue
                if quote:
                    if char == '\\' and quote == '"': escaped = True
                    elif char == quote: quote = None
                elif char in ('"', "'"): quote = char
                elif char == '#': comment = ' ' + line[index:].rstrip('\n'); break
            kept.append(parts[-1] + ' = ' + literal(value) + comment + '\n')
            inserted = True
        else:
            kept.append(line)
    if not inserted:
        # Insert in an existing parent table or before the first table for root.
        parent_path = '.'.join(parts[:-1])
        start = -1 if not parent_path else next((i for i, line in enumerate(kept)
            if re.fullmatch(r'\s*\[' + re.escape(parent_path) + r'\]\s*(?:#.*)?\n?', line)), None)
        if start is None:
            if kept and not kept[-1].endswith('\n'): kept[-1] += '\n'
            kept.extend(['\n[' + parent_path + ']\n', parts[-1] + ' = ' + literal(value) + '\n'])
        else:
            index = next((i for i in range(start + 1, len(kept)) if kept[i].lstrip().startswith('[')), len(kept))
            if index and not kept[index-1].endswith('\n'): kept[index-1] += '\n'
            kept.insert(index, parts[-1] + ' = ' + literal(value) + '\n')
    result = ''.join(kept)
    if tomllib.loads(result) != expected:
        raise ValueError('cannot safely edit dotted, inline or multiline setting: ' + path)
    return result
