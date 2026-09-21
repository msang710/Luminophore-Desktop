from pathlib import Path
import unittest


class LuaSettingsBridgeTests(unittest.TestCase):
    def test_current_defaults_are_preserved_by_typed_bridge(self) -> None:
        config = Path("luminophore_shell/config.toml").read_text(encoding="utf-8")
        self.assertIn("gaps_in = 0", config)
        self.assertIn("border_size = 0", config)
        self.assertIn("rounding = 14", config)
        self.assertIn("active_opacity = 1.0", config)
        self.assertIn('preset = "balanced"', config)
        self.assertIn("speed = 1.0", config)
        decorations = Path("decorations.lua").read_text(encoding="utf-8")
        self.assertIn("resize_on_border_inner_area = 5", decorations)
        animations = Path("animations.lua").read_text(encoding="utf-8")
        misc = Path("misc.lua").read_text(encoding="utf-8")
        for key in ("gaps_in", "gaps_out", "border_size", "rounding", "active_opacity", "inactive_opacity", "dim_special", "blur_enabled", "blur_size", "blur_passes"):
            self.assertIn(f"compositor.{key}", decorations)
        self.assertIn("selected.windows * scale", animations)
        self.assertNotIn("selected.workspaces * scale", animations)
        self.assertIn('leaf = "specialWorkspaceIn"', animations)
        self.assertIn('leaf = "specialWorkspaceOut"', animations)
        self.assertIn("vrr = compositor.vrr", misc)

    def test_monitor_only_idle_policy_is_luminophore_compositor_scoped(self) -> None:
        misc = Path("misc.lua").read_text(encoding="utf-8")
        guard = 'if os.getenv("LUMINOPHORE_COMPOSITOR") == "1" then'
        assignment = "misc.luminophore_monitor_idle_minutes = 30"
        self.assertIn(guard, misc)
        self.assertIn(assignment, misc)
        self.assertLess(misc.index(guard), misc.index(assignment))
        self.assertIn("misc = misc", misc)

    def test_bridge_does_not_expose_raw_lua_or_topology(self) -> None:
        bridge = Path("luminophore_settings.lua").read_text(encoding="utf-8")
        self.assertNotIn("windowrule", bridge.casefold())
        self.assertNotIn("monitor", bridge.casefold())
        self.assertNotIn("loadstring", bridge.casefold())


if __name__ == "__main__":
    unittest.main()
