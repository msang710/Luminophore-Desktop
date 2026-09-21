from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luminophore_runtime import capabilities, inventory
from luminophore_runtime.common import ContractError


MANIFEST = Path(__file__).resolve().parents[1] / "profiles/desktop-capabilities.json"


class CapabilityInventoryTests(unittest.TestCase):
    def test_exposed_shell_commands_are_owned_by_the_manifest(self):
        manifest = capabilities.load_manifest(MANIFEST)
        used = {
            "hyprpicker", "xdg-user-dir", "fd", "ghostty", "fish", "flatpak",
            "awww", "canberra-gtk-play", "notify-send", "plasma-apply-colorscheme",
            "secret-tool",
        }

        classified = inventory.validate_capability_inventory(manifest, used)

        self.assertEqual(set(classified), used)
        self.assertEqual(classified["hyprpicker"], "required-package")
        self.assertEqual(classified["flatpak"], "optional-provider")
        self.assertEqual(classified["ghostty"], "user-application")

    def test_unclassified_executable_fails_closed(self):
        manifest = capabilities.load_manifest(MANIFEST)
        with self.assertRaisesRegex(ContractError, "unclassified.*new-helper"):
            inventory.validate_capability_inventory(manifest, {"new-helper"})


if __name__ == "__main__":
    unittest.main()
