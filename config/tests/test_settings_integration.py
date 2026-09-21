from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from luminophore_shell.__main__ import _SettingsIpcFacade, _parser
from luminophore_shell.app import LuminophoreShellApplication
from luminophore_shell.config import config_digest, load_config
from luminophore_shell.settings_contract import SettingsApplyPhase, SettingsApplyRequest, SettingsApplyResult, SettingsResultCategory
from luminophore_shell.appearance_types import AppearanceErrorCategory
from luminophore_shell.appearance_service import AppearanceServiceError
from luminophore_shell.settings_service import SettingsServiceHost
from luminophore_shell.ipc import IpcError
from luminophore_shell.settings_contract import SettingsTransportCategory


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def request(self, payload):
        self.requests.append(payload)
        return self.responses[payload["command"]]


class OfflineClient:
    def request(self, payload): raise IpcError("offline")


class Coordinator:
    def __init__(self) -> None:
        self.result = SettingsApplyResult(
            "r1", SettingsResultCategory.OK, SettingsApplyPhase.COMPLETE, "next", ("motion.speed",), "applied"
        )

    def apply(self, request):
        self.request = request
        return self.result

    def status(self, request_id):
        return self.result if request_id == "r1" else None

    def pending_request_id(self):
        return ""

    def reconcile(self, request_id, **kwargs):
        return self.status(request_id)


class DeferredCoordinator(Coordinator):
    def __init__(self) -> None:
        self.result = SettingsApplyResult(
            "r2", SettingsResultCategory.SAVED_PENDING_NEXT_START,
            SettingsApplyPhase.PENDING_NEXT_START, "next-motion", ("motion.speed",),
            "saved for the next shell start",
        )


class ThemeTargets:
    def selected_targets(self): return ()


class ThemeController:
    def __init__(self):
        self.targets = ThemeTargets()
        self.backend = type("Backend", (), {"executable": Path("/missing/matugen")})()
        self.state = type("State", (), {
            "phase": "idle", "message": "", "error_category": "", "preview": None,
            "backup_available": False, "drifted": False,
        })()
    def open(self): return self.state
    def preview(self, mode):
        self.state = type("State", (), {
            "phase": "generating" if mode in {"dark", "light"} else "error",
            "message": "", "error_category": "" if mode in {"dark", "light"} else "validation",
            "preview": None, "backup_available": False, "drifted": False,
        })()


class DispatchHarness:
    _dispatch = LuminophoreShellApplication._dispatch
    _settings_status = LuminophoreShellApplication._settings_status

    def _visual_renderer_available(self):
        return False
    _system_theme_wire = staticmethod(LuminophoreShellApplication._system_theme_wire)

    def __init__(self, config) -> None:
        self.config = config
        self.appearance_service = Appearance()
        self.settings_coordinator = Coordinator()
        self.deferred_settings_coordinator = DeferredCoordinator()
        self.system_theme_controller = ThemeController()
        self._binding_temp = tempfile.TemporaryDirectory()
        root = Path(self._binding_temp.name)
        host = object.__new__(SettingsServiceHost)
        host._lock = threading.Lock()
        host._wake = threading.Event()
        host._snapshot = (config, 'a'*64)
        host._documents = {'bindings.toml':'schema_version=1\n'}
        host._error = ''
        host._result = host._last_request = host._queued = host._confirmation = None
        self._settings_fixture_host = host
    def _palette_monitors(self): return [Monitor("DP-2"), Monitor("DP-1")]


class Monitor:
    def __init__(self, name): self.name = name


class Appearance:
    def __init__(self, error=None):
        self.error = error
        self.preview_sources = None
        self.applied = None
    def preview(self, provider, sources, mode):
        if self.error: raise self.error
        self.preview_sources = (provider, sources, mode)
        return type("Preview", (), {"token": "token"})()
    def apply(self, token): self.applied = token


class AsyncAppearance:
    def __init__(self) -> None: self.applied = None
    def preview(self, provider, sources, mode):
        token = type("Token", (), {"preview_id": "preview-1", "provider": type("Provider", (), {"value": provider})()})()
        compiled = type("Compiled", (), {
            "generation_id": "g1", "palette": (("primary", "#112233"),),
        })()
        return type("Preview", (), {"token": token, "compiled": tuple((name, compiled) for name in sorted(sources))})()
    def apply(self, token): self.applied = token
    def discard(self, preview_id): return True


class AppearanceHarness:
    _apply_wallpaper_compatibility = LuminophoreShellApplication._apply_wallpaper_compatibility
    def __init__(self, config, appearance):
        self.config = config
        self.appearance_service = appearance
    def _palette_monitors(self): return [Monitor("DP-2"), Monitor("DP-1")]


class AppearanceAsyncHarness:
    _valid_job_id = staticmethod(LuminophoreShellApplication._valid_job_id)
    _finish_appearance_job = LuminophoreShellApplication._finish_appearance_job
    _appearance_fingerprint = staticmethod(LuminophoreShellApplication._appearance_fingerprint)
    _cleanup_appearance_jobs = LuminophoreShellApplication._cleanup_appearance_jobs
    _start_appearance_preview = LuminophoreShellApplication._start_appearance_preview
    _start_appearance_apply = LuminophoreShellApplication._start_appearance_apply
    def __init__(self, config):
        self.config = config
        self.appearance_service = AsyncAppearance()
        self._appearance_job_lock = threading.Lock()
        self._appearance_jobs = {}
        self._appearance_job_fingerprints = {}
        self._appearance_job_deadlines = {}
        self._appearance_job_finished = {}
        self._appearance_tokens = {}
        self._appearance_token_created = {}
        self._appearance_token_reservations = {}


class SettingsIntegrationTests(unittest.TestCase):
    def test_explicit_settings_cli_route(self) -> None:
        args = _parser().parse_args(("settings", "--page", "wallpaper"))
        self.assertEqual((args.command, args.page), ("settings", "wallpaper"))

    def test_facade_maps_snapshot_and_capabilities(self) -> None:
        client = Client({
            "settings-snapshot": {"ok": True, "digest": "d1", "online": True, "values": {"motion.speed": 1.0}},
            "settings-capabilities": {"ok": True, "capabilities": {"appearance": False, "settings_apply": True}},
            "bindings-snapshot": {"ok": True, "digest": "b1", "bindings": []},
            "bindings-apply": {
                "ok": True, "digest": "b2", "changed_actions": ["shell.settings"],
                "generation_id": "g2", "reload_required": False, "category": "ok", "request_id": "binding-test",
            },
        })
        facade = _SettingsIpcFacade(client)
        self.assertTrue(facade.snapshot().online)
        self.assertEqual(facade.capabilities(), {"appearance": False, "settings_apply": True})
        self.assertEqual(facade.binding_snapshot()["digest"], "b1")
        applied = facade.apply_bindings("b1", {
            "shell.settings": {
                "chord": "SUPER + Y",
                "flags": {"locked": False, "non_consuming": False, "release": False, "repeating": False},
            },
        })
        self.assertFalse(applied["reload_required"])

    def test_facade_classifies_connected_old_daemon_as_remote_rejection(self) -> None:
        facade = _SettingsIpcFacade(Client({
            "settings-snapshot": {"ok": False, "error": "unknown command"},
            "settings-capabilities": {"ok": False, "error": "unknown command"},
        }))
        for operation in (facade.snapshot, facade.capabilities):
            with self.assertRaises(IpcError) as caught:
                operation()
            self.assertEqual(caught.exception.category, SettingsTransportCategory.REMOTE_REJECTION)
            self.assertNotIn("unknown command", str(caught.exception))

    def test_facade_offline_apply_preserves_all_settings_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.toml'
            path.write_text('[motion]\nspeed=1.0\n')
            before = path.read_bytes()
            with self.assertRaises(IpcError):
                _SettingsIpcFacade(OfflineClient()).apply(SettingsApplyRequest.build('offline', 'old', {'motion.speed':1.25}))
            self.assertEqual(path.read_bytes(), before)

    def test_daemon_settings_contract_is_additive_and_fail_closed(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        harness = DispatchHarness(config)
        self.addCleanup(harness._binding_temp.cleanup)
        snapshot = harness._dispatch({"command": "settings-snapshot"})
        capabilities = harness._dispatch({"command": "settings-capabilities"})
        applied = harness._dispatch({
            "command": "settings-apply", "request_id": "r1",
            "expected_digest": config_digest(config.path), "changes": {"theme.ui_scale": 1.0},
        })
        self.assertTrue(snapshot["ok"])
        self.assertTrue(capabilities["capabilities"]["appearance"])
        self.assertTrue(capabilities["capabilities"]["settings_apply"])
        self.assertEqual(applied["category"], "completion_unknown")
        deferred = harness._dispatch({
            "command": "settings-apply", "request_id": "r2",
            "expected_digest": config_digest(config.path), "changes": {"motion.speed": 1.2},
        })
        self.assertFalse(deferred["ok"])
        self.assertEqual(deferred["category"], "busy")
        self.assertEqual(harness._settings_fixture_host.status('r1')['category'], 'completion_unknown')

    def test_binding_ipc_exposes_registered_actions_and_typed_apply_only(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        harness = DispatchHarness(config)
        self.addCleanup(harness._binding_temp.cleanup)
        snapshot = harness._dispatch({"command": "bindings-snapshot"})
        self.assertTrue(snapshot["ok"])
        settings = next(row for row in snapshot["bindings"] if row["action_id"] == "shell.settings")
        applied = harness._dispatch({
            "command": "bindings-apply",
            "expected_digest": snapshot["digest"],
            "changes": {
                "shell.settings": {
                    "chord": "SUPER + Y",
                    "flags": settings["flags"],
                },
            },
        })
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["category"], "completion_unknown")
        self.assertEqual(harness._settings_fixture_host._queued["changes"]["bindings.actions"]["shell.settings"]["chord"], "SUPER + Y")

        rejected = harness._dispatch({
            "command": "bindings-apply",
            "expected_digest": applied["digest"],
            "changes": {
                "shell.settings": {
                    "chord": "SUPER + U",
                    "flags": settings["flags"],
                    "command": "arbitrary command",
                },
            },
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["category"], "validation")

    def test_system_theme_ipc_reports_async_state_without_paths(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        harness = DispatchHarness(config)
        self.addCleanup(harness._binding_temp.cleanup)
        status = harness._dispatch({"command": "system-theme-status"})
        preview = harness._dispatch({"command": "system-theme-preview", "mode": "dark"})
        self.assertEqual(status["phase"], "idle")
        self.assertEqual(preview["phase"], "generating")
        self.assertNotIn("path", repr(preview).lower())

    def test_appearance_async_jobs_do_not_return_private_source_paths(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        harness = AppearanceAsyncHarness(config)
        started = harness._start_appearance_preview({
            "request_id": "preview-request", "mode": "dark",
            "sources": {"DP-1": "/private/user/wallpaper.png"},
        })
        self.assertEqual(started["phase"], "previewing")
        for _ in range(100):
            with harness._appearance_job_lock:
                ready = dict(harness._appearance_jobs["preview-request"])
            if ready["phase"] == "ready": break
            time.sleep(0.001)
        self.assertEqual(ready["phase"], "ready")
        self.assertNotIn("/private", repr(ready))
        repeated = harness._start_appearance_preview({
            "request_id": "preview-request", "mode": "dark",
            "sources": {"DP-1": "/private/user/wallpaper.png"},
        })
        self.assertEqual(repeated["preview_id"], ready["preview_id"])
        conflict = harness._start_appearance_preview({
            "request_id": "preview-request", "mode": "light",
            "sources": {"DP-1": "/private/user/wallpaper.png"},
        })
        self.assertEqual(conflict["category"], "conflict")
        applying = harness._start_appearance_apply({
            "request_id": "apply-request", "preview_id": ready["preview_id"],
        })
        self.assertEqual(applying["phase"], "applying")

    def test_appearance_context_returns_only_provider_and_connectors(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        harness = DispatchHarness(config)
        self.addCleanup(harness._binding_temp.cleanup)
        context = harness._dispatch({"command": "appearance-context"})
        self.assertEqual(context["monitors"], ["DP-1", "DP-2"])
        self.assertEqual(context["provider"], config.appearance.wallpaper_provider)
        self.assertNotIn("path", repr(context).lower())

    def test_deploy_and_rollback_include_desktop_entry(self) -> None:
        deploy = Path("scripts/deploy-luminophore-shell").read_text(encoding="utf-8")
        rollback = Path("scripts/rollback-luminophore-shell").read_text(encoding="utf-8")
        name = "io.github.msang710.LuminophoreSettings.desktop"
        self.assertIn(name, deploy)
        self.assertIn(name, rollback)
        desktop = Path("desktop") / name
        desktop_text = desktop.read_text(encoding="utf-8")
        self.assertNotIn("/home/louise", desktop_text)
        self.assertIn("@LUMINOPHORE_SETTINGS_EXEC@", desktop_text)
        self.assertIn("@LUMINOPHORE_SETTINGS_EXEC@", deploy)

    def test_legacy_chooser_uses_one_preview_apply_for_all_monitors(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        appearance = Appearance()
        harness = AppearanceHarness(config, appearance)
        self.assertTrue(harness._apply_wallpaper_compatibility(Path("/tmp/wallpaper.png")))
        _provider, sources, _mode = appearance.preview_sources
        self.assertEqual(tuple(sorted(sources)), ("DP-1", "DP-2"))
        self.assertEqual({item.value for item in sources.values()}, {"/tmp/wallpaper.png"})
        self.assertEqual(appearance.applied, "token")

    def test_legacy_chooser_fails_closed_on_stale_preview(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        appearance = Appearance(AppearanceServiceError(AppearanceErrorCategory.STALE_PREVIEW, "stale"))
        self.assertFalse(AppearanceHarness(config, appearance)._apply_wallpaper_compatibility(Path("/tmp/a.png")))
        self.assertIsNone(appearance.applied)


if __name__ == "__main__":
    unittest.main()
