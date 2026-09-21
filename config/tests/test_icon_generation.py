from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from luminophore_shell.applications import ApplicationRecord
from luminophore_shell.icon_generation import (
    GeneratedIconStore,
    IconDraftGenerator,
    IconGenerationError,
    inspect_icon_source,
    validate_generated_svg,
)


class FakeInfo:
    pass


def app() -> ApplicationRecord:
    return ApplicationRecord(
        "dev.example.App.desktop",
        "Example",
        "",
        "",
        None,
        FakeInfo(),  # type: ignore[arg-type]
        icon_names=("example-app",),
    )


class IconGenerationTests(unittest.TestCase):
    def test_png_generates_inactive_arcticons_svg_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((12, 12, 84, 84), fill="white")
            draw.rectangle((42, 26, 54, 70), fill="black")
            image.save(source)
            generator = IconDraftGenerator(lambda _app: source, root / "cache")

            drafts = generator.generate(app())

            self.assertGreaterEqual(len(drafts), 1)
            for draft in drafts:
                validate_generated_svg(draft.svg_path.read_bytes())
                self.assertIn('viewBox="0 0 48 48"', draft.svg_path.read_text(encoding="utf-8"))
            self.assertFalse((root / "data/icons/luminophore-shell-arcticons").exists())

    def test_approve_stale_and_remove_are_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGBA", (64, 64), "white").save(source)
            generator = IconDraftGenerator(lambda _app: source, root / "cache")
            draft = generator.generate(app())[0]
            changed: list[bool] = []
            store = GeneratedIconStore(root / "data", lambda: changed.append(True))

            entry = store.approve(draft, "example-app")

            active = root / "data/icons/luminophore-shell-arcticons/scalable/apps/example-app.svg"
            self.assertTrue(active.is_file())
            self.assertEqual(store.status(app(), source), "적용됨")
            Image.new("RGBA", (64, 64), "black").save(source)
            self.assertEqual(store.status(app(), source), "업데이트 필요")
            self.assertEqual(store.entry_for(app().desktop_id), entry)
            self.assertTrue(store.remove(app().desktop_id))
            self.assertFalse(active.exists())
            self.assertEqual(len(changed), 2)

    def test_unsupported_or_unsafe_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = root / "icon.txt"
            text.write_text("not an image", encoding="utf-8")
            with self.assertRaises(IconGenerationError):
                inspect_icon_source(app(), text)
            unsafe = root / "unsafe.svg"
            unsafe.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', encoding="utf-8")
            generator = IconDraftGenerator(lambda _app: unsafe, root / "cache")
            with self.assertRaisesRegex(IconGenerationError, "script"):
                generator.generate(app())

    def test_standard_svg_namespace_is_allowed_but_external_href_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal = root / "normal.svg"
            normal.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
                '<path fill="white" d="M4 4h40v40H4z"/></svg>',
                encoding="utf-8",
            )
            drafts = IconDraftGenerator(lambda _app: normal, root / "normal-cache").generate(app())
            self.assertGreaterEqual(len(drafts), 1)

            external = root / "external.svg"
            external.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg">'
                '<image href="https://example.com/icon.png"/></svg>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IconGenerationError, "외부 참조"):
                IconDraftGenerator(lambda _app: external, root / "external-cache").generate(app())


if __name__ == "__main__":
    unittest.main()
