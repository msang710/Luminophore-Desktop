import copy
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luminophore_runtime import capabilities, desktop
from luminophore_runtime.common import ContractError


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).resolve().parents[1] / "profiles/desktop-capabilities.json"
PKGBUILD = ROOT / "compositor/luminophore/packaging/arch/PKGBUILD.in"


class CapabilityContractTests(unittest.TestCase):
    def test_checked_in_manifest_drives_package_and_command_contracts(self):
        manifest = capabilities.load_manifest(MANIFEST)

        self.assertEqual(
            set(capabilities.required_packages(manifest))
            - set(capabilities.private_packages(manifest)),
            set(capabilities.pkgbuild_arrays(PKGBUILD.read_text())["depends"]),
        )
        optional = capabilities.optional_packages(manifest)
        self.assertEqual(set(optional), set(capabilities.pkgbuild_arrays(PKGBUILD.read_text())["optdepends"]))
        groups = capabilities.command_groups(manifest)
        self.assertIn("hyprpicker", groups.required)
        self.assertIn("xdg-user-dir", groups.required)
        self.assertIn("fd", groups.required)
        self.assertIn("flatpak", groups.optional)
        self.assertIn("awww", groups.optional)
        self.assertNotIn("luminophorectl", groups.required)
        self.assertNotIn("ghostty", groups.required)

    def test_manifest_rejects_missing_ownership_and_duplicate_command(self):
        manifest = capabilities.load_manifest(MANIFEST)
        invalid = copy.deepcopy(manifest)
        invalid["capabilities"][0].pop("ownership")
        with self.assertRaisesRegex(ContractError, "capability"):
            capabilities.validate(invalid)

        duplicate = copy.deepcopy(manifest)
        duplicate["capabilities"].append(copy.deepcopy(duplicate["capabilities"][0]))
        duplicate["capabilities"][-1]["id"] = "duplicate"
        with self.assertRaisesRegex(ContractError, "command.*duplicate"):
            capabilities.validate(duplicate)

    def test_secret_service_has_both_client_and_provider(self):
        manifest = capabilities.load_manifest(MANIFEST)
        indexed = {row["id"]: row for row in manifest["capabilities"]}
        self.assertEqual(indexed["secret-tool"]["ownership"], "required-package")
        self.assertEqual(indexed["secret-service"]["kind"], "dbus-service")
        self.assertEqual(indexed["secret-service"]["requirement"], "required")
        self.assertIn("gnome-keyring", indexed["secret-service"]["packages"])

    def test_desktop_release_metadata_carries_the_same_manifest(self):
        manifest = capabilities.load_manifest(MANIFEST)

        metadata = desktop.capability_metadata()

        self.assertEqual(metadata["desktop-capabilities.json"], manifest)
        self.assertEqual(
            metadata["desktop-capabilities.sha256"],
            capabilities.manifest_digest(manifest),
        )


if __name__ == "__main__":
    unittest.main()
