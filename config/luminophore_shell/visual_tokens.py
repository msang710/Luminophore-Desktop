from __future__ import annotations

from dataclasses import dataclass
import colorsys

from .theme import Palette


_BASE_SURFACE = "#080B10"


@dataclass(frozen=True)
class LuminophoreVisualTokens:
    raw_primary: str
    raw_secondary: str
    face_primary: str
    face_secondary: str
    text_shadow_near_alpha: float = 0.62
    text_shadow_far_alpha: float = 0.26
    icon_shadow_near_alpha: float = 0.68
    icon_shadow_far_alpha: float = 0.30


def _normalize_hex(color: str) -> str:
    clean = color.removeprefix("#")
    if len(clean) != 6:
        raise ValueError("color must use #RRGGBB")
    try:
        int(clean, 16)
    except ValueError as exc:
        raise ValueError("color must use #RRGGBB") from exc
    return f"#{clean.upper()}"


def _rgb(color: str) -> tuple[float, float, float]:
    clean = _normalize_hex(color)[1:]
    return tuple(int(clean[index : index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]


def _hex(channels: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, channel)) * 255):02X}" for channel in channels)


def _linear_channel(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    red, green, blue = _rgb(color)
    return 0.2126 * _linear_channel(red) + 0.7152 * _linear_channel(green) + 0.0722 * _linear_channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    light, dark = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _readable_face(color: str, background: str = _BASE_SURFACE) -> str:
    normalized = _normalize_hex(color)
    if contrast_ratio(normalized, background) >= 4.5:
        return normalized
    red, green, blue = _rgb(normalized)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    for step in range(1, 101):
        candidate = _hex(colorsys.hls_to_rgb(hue, lightness + (1.0 - lightness) * step / 100, saturation))
        if contrast_ratio(candidate, background) >= 4.5:
            return candidate
    return "#FFFFFF"


def derive_visual_tokens(palette: Palette, backdrop_opacity: float) -> LuminophoreVisualTokens:
    # Opacity is part of the public derivation contract. GTK ultimately
    # composites the acrylic over unknown wallpaper pixels, so the stable
    # contrast anchor is the panel's own dark base colour.
    _ = max(0.0, min(1.0, float(backdrop_opacity)))
    raw_primary = _normalize_hex(palette.primary)
    raw_secondary = _normalize_hex(palette.secondary)
    return LuminophoreVisualTokens(
        raw_primary=raw_primary,
        raw_secondary=raw_secondary,
        face_primary=_readable_face(raw_primary),
        face_secondary=_readable_face(raw_secondary),
    )
