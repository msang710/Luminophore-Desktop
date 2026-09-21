from __future__ import annotations

import json
from pathlib import Path
import unittest


class SecretServiceTests(unittest.TestCase):
    def test_session_requires_one_gnome_keyring_secret_provider(self) -> None:
        unit = Path("systemd/luminophore-secret-service.service").read_text()
        self.assertIn("Requires=gnome-keyring-daemon.socket gnome-keyring-daemon.service", unit)
        self.assertIn("After=gnome-keyring-daemon.socket gnome-keyring-daemon.service", unit)
        self.assertIn("PropagatesStopTo=gnome-keyring-daemon.service gnome-keyring-daemon.socket", unit)
        self.assertIn("busctl --user --timeout=5 status org.freedesktop.secrets", unit)
        self.assertNotIn("--replace", unit)

    def test_capability_manifest_requires_provider_and_client(self) -> None:
        manifest = json.loads(Path(
            "../Luminophore-OS/runtime/profiles/desktop-capabilities.json"
        ).read_text())
        by_id = {row["id"]: row for row in manifest["capabilities"]}
        self.assertEqual(by_id["secret-service"]["packages"], ["gnome-keyring"])
        self.assertEqual(by_id["secret-tool"]["packages"], ["libsecret"])
        self.assertEqual(by_id["session-lock"]["packages"], ["gtklock", "swayidle"])


if __name__ == "__main__":
    unittest.main()
