"""Initial placement rules saved through the joint native settings service."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import re


DIRECTIONS = {"default": "기본 규칙", "right": "뷰 오른쪽", "left": "뷰 왼쪽", "up": "뷰 위쪽", "down": "뷰 아래쪽"}
PREFIX = "-- luminophore-placement-v1 "


def validate(rules):
    if not isinstance(rules, dict) or len(rules) > 256:
        raise ValueError("앱 배치 규칙은 최대 256개까지 등록할 수 있습니다")
    for app, direction in rules.items():
        if not isinstance(app, str) or not app.strip() or app != app.strip() or len(app) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in app):
            raise ValueError("올바른 앱 ID를 입력하세요")
        if not isinstance(direction, str) or direction not in DIRECTIONS or direction == "default":
            raise ValueError("올바른 배치 방향을 선택하세요")
    return dict(sorted(rules.items()))


def encode(rules):
    rules = validate(rules)
    # Lua quoted strings accept JSON's quotes/backslashes; control characters are forbidden.
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = [PREFIX + json.dumps(rules, ensure_ascii=False, sort_keys=True), "return {"]
    for app, direction in rules.items():
        pattern = "^" + re.sub(r'([\\.^$|?*+()\[\]{}])', r'\\\1', app) + "$"
        lines.append(f"    {{ app = {quote(app)}, pattern = {quote(pattern)}, direction = {quote(direction)} }},")
    return ("\n".join(lines) + "\n}\n").encode()


class PlacementRules:
    def __init__(self, path=None, *, service=None):
        from .domain_client import DomainClient
        self.service = service or DomainClient()

    def snapshot(self):
        docs, digest = self.service.snapshot()
        return validate(docs['placement.toml'].get('rules', {})), digest

    def set(self, app, direction, expected_digest):
        validate({app: 'right' if direction == 'default' else direction})
        rules, digest = self.snapshot()
        if digest != expected_digest:
            raise ValueError('다른 설정 창에서 변경되었습니다. 목록을 새로고침하세요')
        if direction == 'default': rules.pop(app, None)
        else: rules[app] = direction
        self.service.commit({'placement.rules': rules}, digest)
        return self.snapshot()
