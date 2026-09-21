from __future__ import annotations

import unittest
from unittest.mock import Mock

from luminophore_shell.ui.osd import (
    OSD_BAR_HEIGHT,
    OSD_ICON_SIZE,
    OSD_KINDS,
    OSD_BOTTOM_MARGIN,
    OSD_BLOOM_PADDING,
    OSD_VALUE_WIDTH_CHARS,
    OSD_VALUE_THEME_CLASS,
    OsdGlowViewport,
    OsdLayer,
    osd_glow_layout,
    validate_osd,
)


class OsdPolicyTests(unittest.TestCase):
    def test_only_approved_hardware_osd_kinds_exist(self) -> None:
        self.assertEqual(OSD_KINDS, {"volume", "microphone", "brightness", "privacy"})
        self.assertEqual(validate_osd("brightness", 120), ("brightness", 100))

    def test_media_layout_and_wifi_osd_are_rejected(self) -> None:
        for kind in ("media", "keyboard-layout", "wifi"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "unsupported OSD"):
                validate_osd(kind, 50)

    def test_osd_row_uses_one_compact_center_axis(self) -> None:
        self.assertEqual(OSD_ICON_SIZE, 18)
        self.assertEqual(OSD_BAR_HEIGHT, 8)
        self.assertEqual(OSD_VALUE_WIDTH_CHARS, 4)
        self.assertEqual(OSD_VALUE_THEME_CLASS, "luminophore-key-secondary")

    def test_glow_reserves_space_without_moving_panel_bottom_edge(self) -> None:
        extent, bottom_padding, layer_margin = osd_glow_layout(64)
        self.assertEqual(extent, OSD_BLOOM_PADDING)
        self.assertEqual(bottom_padding, OSD_BOTTOM_MARGIN)
        self.assertEqual(layer_margin + bottom_padding, OSD_BOTTOM_MARGIN)

    def test_osd_native_geometry_comes_from_viewport_allocation(self) -> None:
        source = __import__("inspect").getsource(OsdGlowViewport)

        self.assertIn("self._panel_bounds = next_bounds", source)
        self.assertIn("GlowGeometryInput", source)
        self.assertIn("self.geometry_changed()", source)

    def test_osd_prefers_compositor_projection_with_external_fallback(self) -> None:
        source = __import__("inspect").getsource(OsdLayer)

        self.assertIn("self.external_layer_glow = True", source)
        self.assertIn("def global_bounds", source)
        self.assertNotIn("subsurface_glow", source)
        self.assertIn('"panel_x": geometry.panel_x', source)
        self.assertIn("not self._compositor_projected", source)
        self.assertIn("Gtk4LayerShell.Layer.BOTTOM", source)
        self.assertIn("GLib.idle_add(self._sync_external_glow_once)", source)
        self.assertNotIn("GLib.idle_add(self.external_glow_changed)", source)

    def test_osd_keeps_projection_surface_mapped_between_reveals(self) -> None:
        source = __import__("inspect").getsource(OsdLayer)

        constructor = source.split("def _glow_geometry_changed", 1)[0]
        finish_hide = source.split("def _finish_hide", 1)[1].split("def stop", 1)[0]
        self.assertIn("self.window.set_can_target(False)", constructor)
        self.assertIn("self.window.set_opacity(0.0)", constructor)
        self.assertIn("self.window.present()", constructor)
        self.assertIn("self._projection_handshake.start()", constructor)
        self.assertIn("self.window.set_opacity(0.0)", finish_hide)
        self.assertNotIn("self.window.set_visible(False)", finish_hide)
        self.assertNotIn("self._projection_handshake.cancel()", finish_hide)

    def test_deferred_external_glow_sync_is_one_shot(self) -> None:
        callback = Mock(return_value=True)
        layer = object.__new__(OsdLayer)
        layer.external_glow_changed = callback

        self.assertFalse(layer._sync_external_glow_once())
        callback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
