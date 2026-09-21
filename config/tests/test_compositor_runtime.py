from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.compositor_runtime import (
    CompositorRuntimeError,
    compositor_instance,
    resolve_hyprctl,
)
from luminophore_shell.hyprland import event_socket_path, HyprlandError


class CompositorRuntimeTests(unittest.TestCase):
    def test_luminophore_identity_ignores_hyprland_signature(self) -> None:
        namespace, signature = compositor_instance({
            "XDG_CURRENT_DESKTOP": "Luminophore",
            "LUMINOPHORE_INSTANCE_SIGNATURE": "luminophore-instance",
            "HYPRLAND_INSTANCE_SIGNATURE": "hyprland-instance",
        })
        self.assertEqual((namespace, signature), ("luminophore", "luminophore-instance"))

    def test_hyprland_identity_does_not_consume_luminophore_signature(self) -> None:
        namespace, signature = compositor_instance({
            "LUMINOPHORE_INSTANCE_SIGNATURE": "luminophore-instance",
            "HYPRLAND_INSTANCE_SIGNATURE": "hyprland-instance",
        })
        self.assertEqual((namespace, signature), ("hypr", "hyprland-instance"))

    def test_luminophore_event_socket_ignores_stale_hyprland_identity(self):
        with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': 'Luminophore',
                'XDG_RUNTIME_DIR': '/run/user/1000',
                'LUMINOPHORE_INSTANCE_SIGNATURE': 'new_123_456',
                'HYPRLAND_INSTANCE_SIGNATURE': 'old_123_456'}, clear=True):
            self.assertEqual(event_socket_path(),
                Path('/run/user/1000/luminophore/new_123_456/.socket2.sock'))

    def test_event_socket_rejects_cross_directory_instance(self):
        with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': 'Luminophore',
                'XDG_RUNTIME_DIR': '/run/user/1000',
                'LUMINOPHORE_INSTANCE_SIGNATURE': '../hypr/old_123_456',
                'HYPRLAND_INSTANCE_SIGNATURE': '../hypr/old_123_456'}, clear=True):
            with self.assertRaises(HyprlandError):
                event_socket_path()

    @staticmethod
    def runtime(root: Path) -> tuple[Path, dict[str, str]]:
        binary = root / "bin" / "hyprctl"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        generation = hashlib.sha256(b"generation").hexdigest()
        generation_root = root.parent / generation
        root.rename(generation_root)
        (generation_root / "build-manifest.json").write_text(json.dumps({
            "schema": "luminophore-runtime-artifact/v1",
            "generation": generation,
            "binaries": {"hyprctl": {"sha256": digest}},
        }))
        return generation_root / "bin" / "hyprctl", {
            "LUMINOPHORE_COMPOSITOR": "1",
            "LUMINOPHORE_COMPOSITOR_ROOT": str(generation_root),
        }

    def test_system_session_uses_explicit_compatibility_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = Path(directory) / "hyprctl"
            system.write_text("system")
            system.chmod(0o755)
            self.assertEqual(resolve_hyprctl({}, system_path=system), str(system))

    def test_luminophore_session_uses_verified_generation_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary, environ = self.runtime(Path(directory) / "source")
            self.assertEqual(resolve_hyprctl(environ), str(binary))

    def test_luminophore_identity_never_falls_back_to_system_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = Path(directory) / "system-hyprctl"
            system.write_text("system")
            system.chmod(0o755)
            with self.assertRaisesRegex(CompositorRuntimeError, "root is unavailable"):
                resolve_hyprctl({"LUMINOPHORE_COMPOSITOR": "1"}, system_path=system)

    def test_luminophore_session_rejects_tampered_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary, environ = self.runtime(Path(directory) / "source")
            binary.write_text("tampered")
            with self.assertRaisesRegex(CompositorRuntimeError, "digest mismatch"):
                resolve_hyprctl(environ)

    def test_desktop_identity_without_release_never_uses_system_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = Path(directory) / "hyprctl"
            system.write_text("#!/bin/sh\nexit 0\n")
            system.chmod(0o755)
            for environment in (
                {"XDG_CURRENT_DESKTOP": "Luminophore"},
                {"XDG_CURRENT_DESKTOP": "Luminophore:Other"},
                {"XDG_SESSION_DESKTOP": "luminophore"},
                {"LUMINOPHORE_RELEASE_ID": "a" * 64},
            ):
                with self.subTest(environment=environment):
                    with self.assertRaises(CompositorRuntimeError):
                        resolve_hyprctl(environment, system_path=system)


if __name__ == "__main__":
    unittest.main()
