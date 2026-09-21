from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RuntimeNamespaceTests(unittest.TestCase):
    def source(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_shader_overrides_are_luminophore_owned(self) -> None:
        source = self.source("src/render/ShaderLoader.cpp")
        self.assertIn('"/luminophore/shaders/"', source)
        self.assertNotIn('"/hypr/shaders/"', source)

    def test_bundled_render_assets_are_luminophore_owned(self) -> None:
        source = self.source("src/render/Renderer.cpp")
        self.assertIn('"/luminophore/" + filename', source)
        self.assertNotIn('"/hypr/" + filename', source)

    def test_wallpaper_control_uses_only_luminophore_runtime_identity(self) -> None:
        source = self.source("hyprctl/src/hyprpaper/Hyprpaper.cpp")
        self.assertIn('getenv("LUMINOPHORE_INSTANCE_SIGNATURE")', source)
        self.assertIn('"/luminophore/"s', source)
        self.assertNotIn('getenv("HYPRLAND_INSTANCE_SIGNATURE")', source)
        self.assertNotIn('"/hypr/"s', source)

    def test_hyprpaper_wire_names_are_protocol_compatibility_only(self) -> None:
        source = self.source("hyprctl/src/hyprpaper/Hyprpaper.cpp")
        self.assertIn("hyprpaper_core-client.hpp", source)
        self.assertIn('rq.starts_with("/hyprpaper ")', source)
        self.assertIn('SOCKET_NAME = ".hyprpaper.sock"', source)


if __name__ == "__main__":
    unittest.main()
