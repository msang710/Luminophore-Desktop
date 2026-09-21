from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import colorsys
from typing import TYPE_CHECKING, Iterable

from PIL import Image

from .config import ThemeConfig

if TYPE_CHECKING:
    from .visual_tokens import LuminophoreVisualTokens


@dataclass(frozen=True)
class Palette:
    primary: str
    secondary: str
    surface: str = "#0A0D12"
    text: str = "#F3F7FA"
    muted: str = "#9AA6B2"


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: Iterable[int]) -> str:
    return "#" + "".join(f"{max(0, min(255, channel)):02X}" for channel in rgb)


def _css_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'"{escaped}"'


def _surface_wash(color: tuple[int, int, int], intensity: float) -> str:
    if intensity <= 0:
        return "none"
    upper_alpha = min(0.07, 0.018 * intensity)
    lower_alpha = min(0.03, 0.007 * intensity)
    return (
        "linear-gradient(to bottom, "
        f"rgba({color[0]}, {color[1]}, {color[2]}, {upper_alpha:.3f}), "
        f"rgba({color[0]}, {color[1]}, {color[2]}, {lower_alpha:.3f}))"
    )


def _luminophoreize(color: tuple[int, int, int]) -> tuple[int, int, int]:
    red, green, blue = (channel / 255 for channel in color)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    saturation = max(0.58, min(0.92, saturation * 1.18))
    value = max(0.78, min(1.0, value * 1.12))
    return tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value))  # type: ignore[return-value]


def extract_palette_image(source: Image.Image, fallback: Palette) -> Palette:
    try:
        image = source.convert("RGB")
        image.thumbnail((160, 160))
        quantized = image.quantize(colors=12, method=Image.Quantize.MEDIANCUT).convert("RGB")
        colors = sorted(quantized.getcolors(maxcolors=160 * 160) or [], reverse=True)
    except (OSError, ValueError):
        return fallback

    candidates: list[tuple[int, tuple[int, int, int], float, float]] = []
    for count, color in colors:
        red, green, blue = (channel / 255 for channel in color)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        if 0.10 < value < 0.97 and saturation > 0.18:
            candidates.append((count, color, hue, saturation))
    if not candidates:
        return fallback

    primary_row = max(candidates, key=lambda row: row[0] * (0.65 + row[3]))
    primary = _luminophoreize(primary_row[1])
    alternatives = [row for row in candidates if min(abs(row[2] - primary_row[2]), 1 - abs(row[2] - primary_row[2])) >= 0.12]
    secondary_row = max(alternatives or candidates, key=lambda row: row[0] * (0.5 + row[3]))
    secondary = _luminophoreize(secondary_row[1])
    if secondary == primary:
        red, green, blue = (channel / 255 for channel in primary)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        secondary = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb((hue + 0.33) % 1, saturation, value))
    return Palette(primary=_hex(primary), secondary=_hex(secondary))


def extract_palette(image_path: Path, fallback: Palette) -> Palette:
    try:
        with Image.open(image_path) as source:
            return extract_palette_image(source, fallback)
    except (OSError, ValueError):
        return fallback


def fallback_palette(config: ThemeConfig) -> Palette:
    return Palette(config.fallback_primary, config.fallback_secondary)


def resolve_palettes(
    config: ThemeConfig,
    connectors: list[str],
    wallpapers: dict[str, Path],
    engine_palettes: dict[str, Palette],
) -> dict[int, Palette]:
    fallback = fallback_palette(config)
    if config.palette_source == "fixed":
        fixed = Palette(config.fixed_primary.upper(), config.fixed_secondary.upper())
        overrides = {
            connector: Palette(primary.upper(), secondary.upper())
            for connector, primary, secondary in config.fixed_monitor_palettes
        }
        return {
            index: overrides.get(connector, fixed)
            for index, connector in enumerate(connectors)
        }
    if config.palette_source in {"hyprpaper", "awww", "native"}:
        return {
            index: engine_palettes.get(connector, fallback)
            for index, connector in enumerate(connectors)
        }
    result: dict[int, Palette] = {}
    for index, connector in enumerate(connectors):
        path = wallpapers.get(connector) or wallpapers.get("default")
        result[index] = extract_palette(path, fallback) if path else fallback
    return result


def build_css(
    config: ThemeConfig,
    palettes: dict[int, Palette],
    radius: int = 12,
    visual_tokens: dict[int, "LuminophoreVisualTokens"] | None = None,
) -> str:
    surface_opacity = max(config.backdrop_opacity, 0.9) if config.high_contrast else config.backdrop_opacity
    surface = f"rgba(8, 11, 16, {surface_opacity:.3f})"
    glow_intensity = float(config.outline_glow_intensity)
    body_font = _css_string(config.body_font)
    numeric_font = _css_string(config.numeric_font)
    fallback_rgb = _rgb(config.fallback_primary)
    foreground = "#FFFFFF" if config.high_contrast else "#F3F7FA"
    muted = "#D7DEE5" if config.high_contrast else "#9AA6B2"
    outline_width = max(2, config.outline_width) if config.high_contrast else config.outline_width
    chunks = [
        f"""
        * {{
          font-family: {body_font};
        }}
        .numeric {{ font-family: {numeric_font}; font-variant-numeric: tabular-nums; }}
        window.luminophore-surface {{ background: transparent; font-size: {config.ui_scale * 100:.1f}%; }}
        .luminophore-panel {{
          background-color: {surface};
          background-image: {_surface_wash(fallback_rgb, glow_intensity)};
          color: {foreground};
          border-radius: {radius}px;
          padding: 8px 12px;
          border: {outline_width}px solid {config.fallback_primary};
        }}
        .luminophore-panel.gsk-luminophore-frame {{ border-color: transparent; }}
        .luminophore-osd > .osd-icon {{ min-width: 18px; min-height: 18px; }}
        .luminophore-osd > progressbar.osd-progress {{ min-height: 8px; }}
        .luminophore-osd > progressbar.osd-progress trough,
        .luminophore-osd > progressbar.osd-progress progress {{
          min-height: 8px;
          border-radius: 999px;
        }}
        .luminophore-osd > .osd-value {{
          margin: 0;
          padding: 0;
          font-size: 13px;
          font-weight: 700;
        }}
        .luminophore-expanded {{ padding-top: 12px; }}
        .luminophore-expanded.replace-collapsed {{ padding-top: 0; }}
        .luminophore-expanded.weather-expanded {{ padding-top: 4px; }}
        .luminophore-button {{
          min-width: 28px; min-height: 28px;
          padding: 2px 6px; border-radius: 8px;
          background: transparent; border: 0;
        }}
        .luminophore-button:hover {{ background: alpha(#78DCE8, 0.16); }}
        popover.luminophore-popover > contents {{
          background: rgba(8, 11, 16, 0.96);
          color: #F3F7FA;
          border: 1px solid #78DCE8;
          border-radius: {radius}px;
        }}
        popover.luminophore-popover .luminophore-button:hover,
        popover.luminophore-popover .luminophore-button:focus {{ background: alpha(#AB9DF2, 0.20); }}
        .luminophore-panel, .luminophore-button, .weather-cell, .launcher-result {{
          transition: background-color {config.palette_transition_ms}ms ease,
                      border-color {config.palette_transition_ms}ms ease,
                      color {config.palette_transition_ms}ms ease;
        }}
        .secondary {{ color: #AB9DF2; }}
        .muted {{ color: {muted}; }}
        .weather-clock {{ font-size: 28px; font-weight: 700; }}
        .weather-date {{ font-size: 12px; }}
        .weather-cell {{ padding: 4px 5px; border-radius: 7px; }}
        .weather-day {{ font-size: 16px; font-weight: 700; }}
        .weather-icon {{ color: #F3F7FA; }}
        .weather-temperature {{ font-size: 12px; }}
        .spotify-expanded {{ min-width: 410px; padding-top: 4px; }}
        .spotify-compact {{ min-width: 360px; }}
        .spotify-expanded progressbar trough {{ min-height: 4px; border-radius: 999px; }}
        .spotify-expanded progressbar progress {{ min-height: 4px; border-radius: 999px; }}
        .spotify-album-art {{ border-radius: 10px; }}
        .metric-value {{ font-size: 13px; font-weight: 600; }}
        .section-title {{ font-weight: 700; font-size: 13px; }}
        .launcher-result {{ padding: 5px 7px; border: 0; border-radius: 8px; background: transparent; }}
        .launcher-result:hover, .launcher-result:focus {{ background: alpha(#AB9DF2, 0.18); }}
        .app-tile {{ min-width: 68px; min-height: 48px; padding: 5px; border-radius: 8px; background: transparent; border: 0; }}
        .app-tile:hover, .app-tile:focus {{ background: alpha(#AB9DF2, 0.18); }}
        .taskbar-badge {{ font-size: 10px; font-weight: 800; background: #0A0D12; border-radius: 7px; padding: 0 2px; }}
        .spatial-editor-panel {{ padding: 14px; border-radius: 14px; }}
        .spatial-editor-panel .spatial-map-cell {{ padding: 4px; min-width: 48px; min-height: 48px; }}
        .spatial-editor-panel .spatial-map-cell label {{ font-size: 10px; }}
        .spatial-editor-view-left {{ border-left: 2px solid #7DD3FC; }}
        .spatial-editor-view-right {{ border-right: 2px solid #7DD3FC; }}
        .spatial-editor-view-top {{ border-top: 2px solid #7DD3FC; }}
        .spatial-editor-view-bottom {{ border-bottom: 2px solid #7DD3FC; }}
        .spatial-editor-focused {{ outline: 2px solid #E0F2FE; outline-offset: -2px; }}
        .spatial-editor-feedback {{ background: alpha(#7DD3FC, 0.4); }}
        .spatial-map-panel {{ padding: 7px; border-radius: 9px; }}
        .spatial-map-cell {{
          min-width: 12px;
          min-height: 12px;
          border: 1px solid alpha(#9AA6B2, 0.18);
          border-radius: 2px;
          background: alpha(#080B10, 0.34);
        }}
        .spatial-map-cell-target {{ border-width: 2px; }}
        .spatial-map-cell-conflict {{
          border-color: #FF6188;
          background: alpha(#FF6188, 0.32);
        }}
        .spatial-map-diagnostic {{
          border: 1px solid alpha(#FF6188, 0.9);
          border-radius: 3px;
        }}
        .closed-pin {{ opacity: 0.72; border: 1px solid alpha(#78DCE8, 0.5); }}
        .urgent {{ animation: none; background: alpha(#AB9DF2, 0.22); }}
        .notification-group {{ padding: 8px; border-radius: 8px; background: alpha(#78DCE8, 0.07); }}
        .hardware-mixer {{ padding: 2px 0 4px; }}
        .mixer-channel {{
          padding: 7px 5px;
          border-radius: 10px;
          background: alpha(#78DCE8, 0.055);
        }}
        .mixer-channel.mixer-master {{
          background: alpha(#AB9DF2, 0.11);
          border: 1px solid alpha(#AB9DF2, 0.24);
        }}
        .mixer-channel-title {{ min-height: 30px; font-size: 11px; font-weight: 650; }}
        .mixer-subheading {{ font-size: 10px; font-weight: 650; }}
        .mixer-separator {{ margin: 2px 0; background: alpha(#78DCE8, 0.28); }}
        scale.mixer-scale {{ min-height: 190px; min-width: 48px; }}
        scale.mixer-scale trough {{ min-width: 7px; border-radius: 999px; }}
        scale.mixer-scale highlight {{ border-radius: 999px; }}
        scale.mixer-scale slider {{ min-width: 17px; min-height: 17px; border-radius: 999px; }}
        """
    ]
    for index, palette in palettes.items():
        if visual_tokens and index in visual_tokens:
            tokens = visual_tokens[index]
        else:
            from .visual_tokens import derive_visual_tokens

            tokens = derive_visual_tokens(palette, config.backdrop_opacity)
        primary_rgb = _rgb(palette.primary)
        secondary_rgb = _rgb(palette.secondary)
        chunks.append(
            f"""
            .palette-{index} .luminophore-panel {{
              background-image: {_surface_wash(primary_rgb, glow_intensity)};
              border-color: {palette.primary};
            }}
            .palette-{index} .luminophore-panel.gsk-luminophore-frame {{
              border-color: transparent;
            }}
            .palette-{index} .luminophore-key-primary,
            .palette-{index} .accent {{
              color: {tokens.face_primary};
              text-shadow: 0 0 3px alpha({tokens.raw_primary}, {tokens.text_shadow_near_alpha:.3f}),
                           0 0 8px alpha({tokens.raw_primary}, {tokens.text_shadow_far_alpha:.3f});
            }}
            .palette-{index} .luminophore-key-secondary,
            .palette-{index} .secondary {{
              color: {tokens.face_secondary};
              text-shadow: 0 0 3px alpha({tokens.raw_secondary}, {tokens.text_shadow_near_alpha:.3f}),
                           0 0 8px alpha({tokens.raw_secondary}, {tokens.text_shadow_far_alpha:.3f});
            }}
            .palette-{index} .luminophore-symbol-primary {{
              color: {tokens.face_primary};
              -gtk-icon-shadow: 0 0 3px alpha({tokens.raw_primary}, {tokens.icon_shadow_near_alpha:.3f}),
                                0 0 8px alpha({tokens.raw_primary}, {tokens.icon_shadow_far_alpha:.3f});
            }}
            .palette-{index} .luminophore-symbol-secondary {{
              color: {tokens.face_secondary};
              -gtk-icon-shadow: 0 0 3px alpha({tokens.raw_secondary}, {tokens.icon_shadow_near_alpha:.3f}),
                                0 0 8px alpha({tokens.raw_secondary}, {tokens.icon_shadow_far_alpha:.3f});
            }}
            .palette-{index}.spatial-map-cell-view {{
              border-color: alpha({tokens.raw_primary}, 0.88);
              background: alpha({tokens.raw_primary}, 0.22);
            }}
            .palette-{index}.spatial-map-cell-target {{
              border-color: {tokens.face_primary};
              background: alpha({tokens.raw_primary}, 0.34);
            }}
            .palette-{index}.spatial-map-cell-wide {{
              background: alpha({tokens.raw_primary}, 0.30);
              border-color: {tokens.face_primary};
            }}
            .palette-{index}.spatial-map-cell-wide-key {{
              background: alpha({tokens.raw_primary}, 0.46);
              border-width: 2px;
            }}
            .palette-{index} .spotify-progress trough {{
              background: rgba({primary_rgb[0]}, {primary_rgb[1]}, {primary_rgb[2]}, 0.18);
            }}
            .palette-{index} .spotify-progress progress {{
              background: {palette.primary};
            }}
            .palette-{index} popover.luminophore-popover > contents {{
              border-color: {palette.primary};
            }}
            .palette-{index} .luminophore-button:hover,
            .palette-{index} popover.luminophore-popover .luminophore-button:hover,
            .palette-{index} popover.luminophore-popover .luminophore-button:focus,
            .palette-{index} .selected {{ background: rgba({secondary_rgb[0]}, {secondary_rgb[1]}, {secondary_rgb[2]}, 0.18); }}
            """
        )
    return "\n".join(chunks)
