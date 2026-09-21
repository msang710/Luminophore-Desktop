"""Behavioral validation for the final general-settings batch."""
from dataclasses import asdict
import unittest
from luminophore_shell.config import CompositorConfig
from luminophore_shell.owned_settings import validate

class RemainingSettingsTests(unittest.TestCase):
    def test_native_preferences_are_owned(self):
        values=asdict(CompositorConfig())
        for key in ('cursor_start_output','zoom_factor','locale','font_family','wake_on_key','wake_on_pointer','display_idle_minutes','primary_selection','auto_hdr','sdr_transfer','icc_vcgt','xwayland_native_pixels','background_color','shadow_enabled','shadow_range','shadow_power','shadow_sharp','shadow_color','shadow_inactive_color','shadow_scale','shadow_offset_x','shadow_offset_y','float_gap_top','float_gap_right','float_gap_bottom','float_gap_left'):
            self.assertIn(key,values)
    def test_reject_invalid_complex_values_before_apply(self):
        for key,value in [('background_color','ff000000 trailing'),('shadow_color','red'),('shadow_color','ff000000 360deg'),('shadow_inactive_color',''),('font_family',''),('locale','en\nUS'),('cursor_start_output','DP-1\x00'),('float_gap_left',-1),('zoom_factor',0.5),('sdr_transfer','guess')]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                validate({'compositor.'+key:value})
    def test_gradient_and_independent_edges_round_trip(self):
        values={'compositor.shadow_color':'ff000000 80112233 270deg','compositor.shadow_inactive_color':'inherit','compositor.float_gap_top':2,'compositor.float_gap_left':17}
        actual=validate(values)
        for key,value in values.items():self.assertEqual(actual[key],value)

    def test_shadow_style_preserves_custom_and_only_replaces_shape(self):
        from luminophore_shell.settings_schema import SHADOW_STYLES, shadow_style
        fields = {"compositor."+key:value for key,value in SHADOW_STYLES["기본"].items()}
        fields["compositor.shadow_color"] = "ff000000 0deg"
        self.assertEqual(shadow_style(fields), "기본")
        fields["compositor.shadow_offset_x"] = 1.5
        self.assertEqual(shadow_style(fields), "사용자 지정")
        self.assertTrue(all("shadow_color" not in preset for preset in SHADOW_STYLES.values()))

    def test_lock_blur_requires_explicit_backdrop(self):
        with self.assertRaises(ValueError):
            validate({"compositor.lock_blur":True})
        self.assertTrue(validate({"compositor.lock_blur":True,"compositor.lock_background":True})["compositor.lock_blur"])
