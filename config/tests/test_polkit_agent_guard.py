from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path("scripts/luminophore-polkit-agent-available")


def load_guard():
    loader = SourceFileLoader("luminophore_polkit_guard", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PolkitAgentGuardTests(unittest.TestCase):
    def test_detects_known_agent_and_ignores_unrelated_processes(self) -> None:
        guard = load_guard()
        with tempfile.TemporaryDirectory() as raw:
            proc = Path(raw)
            (proc / "10").mkdir()
            (proc / "10/cmdline").write_bytes(b"/usr/bin/sleep\x0010")
            self.assertEqual(guard.find_other_agent(proc, 99), "")
            (proc / "11").mkdir()
            (proc / "11/cmdline").write_bytes(b"/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1\x00")
            self.assertIn("polkit-gnome", guard.find_other_agent(proc, 99))

    def test_current_process_is_ignored(self) -> None:
        guard = load_guard()
        with tempfile.TemporaryDirectory() as raw:
            proc = Path(raw)
            (proc / "42").mkdir()
            (proc / "42/cmdline").write_bytes(b"hyprpolkitagent\x00")
            self.assertEqual(guard.find_other_agent(proc, 42), "")


if __name__ == "__main__":
    unittest.main()
