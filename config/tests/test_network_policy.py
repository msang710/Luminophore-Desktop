from __future__ import annotations

import unittest

from luminophore_shell.network_policy import OfflineError, OfflinePolicy


class OfflinePolicyTests(unittest.TestCase):
    def test_online_allows_registered_purposes(self) -> None:
        policy = OfflinePolicy(False)
        for purpose in ("weather", "city_search", "google_calendar"):
            self.assertTrue(policy.allows(purpose))

    def test_offline_rejects_every_registered_purpose(self) -> None:
        policy = OfflinePolicy(True)
        for purpose in ("weather", "city_search", "google_calendar"):
            self.assertFalse(policy.allows(purpose))
            with self.assertRaisesRegex(OfflineError, f"offline:{purpose}"):
                policy.require(purpose)


if __name__ == "__main__":
    unittest.main()
