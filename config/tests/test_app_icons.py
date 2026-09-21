from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from luminophore_shell.app_icons import ApplicationIconProvider, approved_overlay_files, icon_candidates, safe_icon_name, theme_root
from luminophore_shell.applications import ApplicationRecord


class FakeInfo:
    pass


def record(
    desktop_id: str,
    *,
    icon_names: tuple[str, ...] = (),
    window_class: str = "",
) -> ApplicationRecord:
    return ApplicationRecord(
        desktop_id,
        desktop_id,
        "",
        "",
        None,
        FakeInfo(),  # type: ignore[arg-type]
        window_class=window_class,
        icon_names=icon_names,
    )


class AppIconCandidateTests(unittest.TestCase):
    def test_default_code_oss_alias_precedes_source_names(self) -> None:
        app = record("code-oss.desktop", icon_names=("com.visualstudio.code.oss",))

        candidates = icon_candidates(app)

        self.assertEqual(candidates.theme_names[0], "visual-studio-code")
        self.assertIn("com.visualstudio.code.oss", candidates.theme_names)

    def test_explicit_alias_precedes_direct_candidate(self) -> None:
        app = record("dev.example.App.desktop", icon_names=("example",), window_class="Example")

        candidates = icon_candidates(app, {"dev.example.App.desktop": "custom-example"})

        self.assertEqual(candidates.theme_names[0], "custom-example")
        self.assertEqual(len(candidates.theme_names), len(set(name.casefold() for name in candidates.theme_names)))

    def test_icon_name_rejects_paths(self) -> None:
        self.assertEqual(safe_icon_name("dev.example-App"), "dev.example-App")
        with self.assertRaises(ValueError):
            safe_icon_name("../../example")

    def test_theme_root_requires_index_theme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theme = root / "arcticons-dark"
            theme.mkdir()
            self.assertIsNone(theme_root("arcticons-dark", (root,)))
            (theme / "index.theme").write_text("[Icon Theme]\nName=Test\n", encoding="utf-8")
            self.assertEqual(theme_root("arcticons-dark", (root,)), theme.resolve())

    def test_provider_distinguishes_overlay_base_and_original_by_resolved_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = root / "luminophore-shell-arcticons"
            base = root / "arcticons-dark"
            (overlay / "scalable/apps").mkdir(parents=True)
            (base / "scalable/apps").mkdir(parents=True)
            overlay_file = overlay / "scalable/apps/custom.svg"
            base_file = base / "scalable/apps/firefox.svg"
            overlay_file.write_text("<svg/>", encoding="utf-8")
            base_file.write_text("<svg/>", encoding="utf-8")

            class FakeFile:
                def __init__(self, path: Path) -> None:
                    self.path = path

                def get_path(self) -> str:
                    return str(self.path)

            class FakePaintable:
                def __init__(self, path: Path) -> None:
                    self.path = path

                def get_file(self) -> FakeFile:
                    return FakeFile(self.path)

            class FakeTheme:
                def __init__(self, paths: dict[str, Path]) -> None:
                    self.paths = paths

                def has_icon(self, name: str) -> bool:
                    return name in self.paths

                def lookup_icon(self, name: str, *_args: object) -> FakePaintable:
                    return FakePaintable(self.paths[name])

            provider = ApplicationIconProvider.__new__(ApplicationIconProvider)
            provider.theme = FakeTheme({"custom": overlay_file, "firefox": base_file})
            provider.base_theme = FakeTheme({"firefox": base_file})
            provider.overlay_root = overlay
            provider.base_root = base
            provider.approved_overlay_files = frozenset({overlay_file.resolve()})
            provider.aliases = {}

            self.assertEqual(provider._resolve_themed(record("custom.desktop", icon_names=("custom",)), 22, 1).origin, "overlay")
            self.assertEqual(provider._resolve_themed(record("firefox.desktop", icon_names=("firefox",)), 22, 1).origin, "arcticons")
            self.assertEqual(provider._resolve_themed(record("missing.desktop", icon_names=("missing",)), 22, 1).origin, "original")

    def test_overlay_requires_manifest_and_matching_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = root / "icons/luminophore-shell-arcticons"
            active = overlay / "scalable/apps/custom.svg"
            active.parent.mkdir(parents=True)
            active.write_text("<svg/>", encoding="utf-8")
            self.assertEqual(approved_overlay_files(overlay), frozenset())

            manifest = root / "luminophore-shell/generated-icons.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                '[icons."custom.desktop"]\n'
                'target_name = "custom"\n'
                f'object_sha256 = "{"0" * 64}"\n',
                encoding="utf-8",
            )
            self.assertEqual(approved_overlay_files(overlay), frozenset())

            import hashlib
            digest = hashlib.sha256(active.read_bytes()).hexdigest()
            manifest.write_text(
                '[icons."custom.desktop"]\n'
                'target_name = "custom"\n'
                f'object_sha256 = "{digest}"\n',
                encoding="utf-8",
            )
            self.assertEqual(approved_overlay_files(overlay), frozenset({active.resolve()}))


if __name__ == "__main__":
    unittest.main()
