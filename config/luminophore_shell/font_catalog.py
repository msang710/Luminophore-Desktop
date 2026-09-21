from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import gi

gi.require_version("PangoCairo", "1.0")
gi.require_version("PangoFc", "1.0")
from gi.repository import PangoCairo, PangoFc  # noqa: E402


@dataclass(frozen=True)
class FontDropdownItems:
    values: tuple[str, ...]
    labels: tuple[str, ...]
    selected: int


def normalize_font_families(names: Iterable[str]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for raw in (name.strip() for name in names if name.strip()):
        unique.setdefault(raw.casefold(), raw)
    return tuple(sorted(unique.values(), key=lambda value: (value.casefold(), value)))


def installed_font_families() -> tuple[str, ...]:
    try:
        font_map = PangoCairo.FontMap.get_default()
        if font_map is None:
            return ()
        if isinstance(font_map, PangoFc.FontMap):
            PangoFc.FontMap.config_changed(font_map)
        return normalize_font_families(family.get_name() for family in font_map.list_families())
    except (AttributeError, RuntimeError):
        return ()


def font_dropdown_items(current: str, installed: Iterable[str]) -> FontDropdownItems:
    families = normalize_font_families(installed)
    current_name = current.strip()
    matching = next(
        (index for index, family in enumerate(families) if family.casefold() == current_name.casefold()),
        None,
    )
    if matching is not None:
        return FontDropdownItems(families, families, matching)
    if current_name:
        return FontDropdownItems(
            (current_name, *families),
            (f"{current_name} · 설치되지 않음", *families),
            0,
        )
    if families:
        return FontDropdownItems(families, families, 0)
    return FontDropdownItems((current,), ("설치된 글꼴 없음",), 0)
