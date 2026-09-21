from __future__ import annotations

from pathlib import Path
import unittest

from luminophore_shell.settings_app import APPLICATION_ID, SettingsAppModel, _route_from_arguments
from luminophore_shell.settings_contract import (
    SettingsApplyPhase,
    SettingsApplyResult,
    SettingsResultCategory,
    SettingsRoute,
    SettingsSnapshot,
)


class Backend:
    def __init__(self, online: bool = True) -> None:
        self.online = online

    def snapshot(self) -> SettingsSnapshot:
        return SettingsSnapshot.build("digest", {"theme.ui_scale": 1.0}, self.online)

    def capabilities(self) -> dict[str, bool]:
        return {
            "appearance": True, "hyprland": True, "bindings": True,
            "system_theme": True, "settings_apply": True,
        }

    def apply(self, request):
        self.request = request
        return SettingsApplyResult(
            request.request_id, SettingsResultCategory.PENDING_HYPRLAND_RELOAD,
            SettingsApplyPhase.PENDING_HYPRLAND_RELOAD, "next", tuple(key for key, _ in request.changes),
        )

    def binding_snapshot(self):
        return {
            "digest": "b1", "customized": True, "generation_id": "g1",
            "reload_status": "unknown",
            "bindings": [{"action_id": "shell.settings", "chord": "SUPER + Z"}],
        }

    def apply_bindings(self, expected_digest, changes):
        self.binding_request = (expected_digest, changes)
        return {"digest": "b2", "generation_id": "g2", "reload_required": True}

    def system_theme_status(self): return {"phase": "idle"}
    def preview_system_theme(self, mode): return {"phase": "preview", "preview": {"mode": mode, "preview_id": "p1"}}
    def apply_system_theme(self, preview_id): return {"phase": "complete", "preview_id": preview_id}
    def rollback_system_theme(self): return {"phase": "complete", "rolled_back": True}
    def appearance_context(self): return {"provider": "hyprpaper", "monitors": ["DP-1"]}
    def preview_appearance(self, request_id, sources, mode):
        self.appearance_preview_request = (request_id, sources, mode)
        return {"request_id": request_id, "phase": "previewing"}
    def appearance_status(self, request_id):
        return {"request_id": request_id, "phase": "ready", "preview_id": "ap1"}
    def apply_appearance(self, request_id, preview_id):
        return {"request_id": request_id, "phase": "applying", "preview_id": preview_id}


class SettingsAppModelTests(unittest.TestCase):
    def test_unknown_blocks_new_apply_and_status_refresh_never_replays(self):
        backend = Backend()
        calls = []
        def unknown(request):
            calls.append(request)
            return SettingsApplyResult(request.request_id, SettingsResultCategory.COMPLETION_UNKNOWN,
                SettingsApplyPhase.COMPLETION_UNKNOWN, "next", tuple(key for key, _ in request.changes))
        backend.apply = unknown
        model = SettingsAppModel(backend)
        model.activate()
        model.apply_settings({"theme.ui_scale": 1.2}, "r-1")
        model.activate("appearance")
        with self.assertRaises(RuntimeError):
            model.apply_settings({"theme.ui_scale": 1.3}, "r-2")
        backend.settings_status = lambda request_id: None
        self.assertTrue(model.refresh_settings_status().completion_unknown)
        backend.settings_status = lambda request_id: SettingsApplyResult(request_id,
            SettingsResultCategory.OK, SettingsApplyPhase.COMPLETE, "next", ("theme.ui_scale",))
        backend.snapshot = lambda: SettingsSnapshot.build("next", {"theme.ui_scale": 1.2}, True)
        self.assertFalse(model.refresh_settings_status().completion_unknown)
        self.assertEqual(model.values["theme.ui_scale"], 1.2)
        self.assertEqual(len(calls), 1)

    def test_application_identity_and_routes_are_stable(self) -> None:
        self.assertEqual(APPLICATION_ID, "io.github.msang710.LuminophoreSettings")
        model = SettingsAppModel(Backend())
        for route in SettingsRoute:
            self.assertEqual(model.activate(route.value).route, route)

    def test_invalid_route_falls_back_home_and_latest_deep_link_wins(self) -> None:
        model = SettingsAppModel(Backend())
        self.assertEqual(model.activate("unknown").route, SettingsRoute.HOME)
        model.navigate("wallpaper")
        model.navigate("bindings")
        self.assertEqual(model.state.route, SettingsRoute.BINDINGS)

    def test_forwarded_command_line_carries_latest_deep_link(self) -> None:
        self.assertEqual(
            _route_from_arguments(["luminophore-settings", "--page", "wallpaper"], SettingsRoute.HOME),
            SettingsRoute.WALLPAPER,
        )
        self.assertEqual(
            _route_from_arguments(["luminophore-settings", "--page=bindings"], SettingsRoute.HOME),
            SettingsRoute.BINDINGS,
        )
        self.assertEqual(
            _route_from_arguments(["luminophore-settings", "--page=/private/wallpaper.png"], SettingsRoute.MOTION),
            SettingsRoute.HOME,
        )

    def test_offline_disables_live_capability_and_shows_recovery_banner(self) -> None:
        model = SettingsAppModel(Backend(online=False))
        state = model.activate("appearance")
        self.assertFalse(model.capability_enabled("appearance"))
        self.assertIn("일반 설정만", state.banner)

    def test_capabilities_are_snapshotted_once_and_failure_is_fail_closed(self) -> None:
        class FailingCapabilitiesBackend(Backend):
            def capabilities(self):
                self.capability_calls = getattr(self, "capability_calls", 0) + 1
                raise RuntimeError("/home/louise/private-capability-error")

        backend = FailingCapabilitiesBackend()
        model = SettingsAppModel(backend)
        model.activate()
        self.assertFalse(model.capability_enabled("appearance"))
        self.assertFalse(model.capability_available("settings_apply"))
        self.assertEqual(backend.capability_calls, 1)

    def test_incompatible_backend_opens_recovery_state_without_mutation_capabilities(self) -> None:
        class IncompatibleBackend(Backend):
            def snapshot(self):
                raise RuntimeError("unknown command: /home/louise/private")

        model = SettingsAppModel(IncompatibleBackend())
        state = model.activate("bindings")
        self.assertEqual(state.route, SettingsRoute.BINDINGS)
        self.assertTrue(state.backend_incompatible)
        self.assertFalse(state.online)
        self.assertFalse(model.capability_enabled("bindings"))
        self.assertNotIn("/home", state.banner)
        self.assertIn("호환되지", state.banner)

    def test_result_states_remain_distinguishable(self) -> None:
        cases = (
            (SettingsResultCategory.BUSY, SettingsApplyPhase.APPLYING, "busy"),
            (SettingsResultCategory.CONFLICT, SettingsApplyPhase.COMPLETE, "conflict"),
            (SettingsResultCategory.COMPLETION_UNKNOWN, SettingsApplyPhase.COMPLETION_UNKNOWN, "completion_unknown"),
            (SettingsResultCategory.SAVED_PENDING_NEXT_START, SettingsApplyPhase.PENDING_NEXT_START, "pending_next_start"),
            (SettingsResultCategory.PENDING_HYPRLAND_RELOAD, SettingsApplyPhase.PENDING_HYPRLAND_RELOAD, "pending_hyprland_reload"),
            (SettingsResultCategory.ROLLBACK_FAILED, SettingsApplyPhase.COMPLETE, "rollback_failed"),
        )
        for category, phase, field in cases:
            model = SettingsAppModel(Backend())
            model.activate()
            state = model.consume_result(SettingsApplyResult("r1", category, phase, "digest"))
            self.assertTrue(getattr(state, field), (category, phase))

    def test_backend_message_is_not_rendered_in_banner(self) -> None:
        model = SettingsAppModel(Backend())
        model.activate()
        state = model.consume_result(SettingsApplyResult(
            "r1", SettingsResultCategory.OK, SettingsApplyPhase.COMPLETE,
            "digest", (), "/home/louise/Pictures/private.png",
        ))
        self.assertNotIn("/home", state.banner)
        self.assertNotIn("private.png", state.banner)

    def test_compositor_and_motion_values_apply_with_reload_banner(self) -> None:
        backend = Backend()
        model = SettingsAppModel(backend)
        model.activate("motion")
        state = model.apply_settings({"motion.speed": 1.25}, "typed-apply")
        self.assertTrue(state.pending_hyprland_reload)
        self.assertIn("Hyprland", state.banner)
        self.assertEqual(model.values["motion.speed"], 1.25)
        self.assertEqual(dict(backend.request.changes), {"motion.speed": 1.25})

    def test_binding_system_theme_and_appearance_models_are_backend_driven(self) -> None:
        backend = Backend()
        model = SettingsAppModel(backend)
        model.activate()
        self.assertEqual(model.load_bindings()["digest"], "b1")
        self.assertFalse(model.state.pending_hyprland_reload)
        result = model.apply_binding_changes({"shell.settings": {"chord": "SUPER + Y", "flags": {}}})
        self.assertEqual(result["digest"], "b2")
        self.assertTrue(model.state.pending_hyprland_reload)
        self.assertEqual(model.load_system_theme()["phase"], "idle")
        self.assertEqual(model.preview_system_theme("dark")["preview"]["preview_id"], "p1")
        self.assertEqual(model.apply_system_theme()["phase"], "complete")
        self.assertTrue(model.rollback_system_theme()["rolled_back"])
        self.assertEqual(model.load_appearance_context()["monitors"], ["DP-1"])
        self.assertEqual(model.preview_appearance({"DP-1": "/tmp/a.png"}, "dark")["phase"], "previewing")
        self.assertEqual(model.refresh_appearance()["preview_id"], "ap1")
        self.assertEqual(model.apply_appearance()["phase"], "applying")

    def test_module_has_no_layer_shell_or_command_execution_dependency(self) -> None:
        root = Path(__file__).parents[1] / "luminophore_shell"
        files = (
            root / "settings_app.py",
            root / "ui/settings_wallpaper.py",
            root / "ui/settings_hyprland.py",
            root / "ui/settings_bindings.py",
        )
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        for forbidden in ("Gtk4LayerShell", "layer_shell", "subprocess", "hyprctl"):
            self.assertNotIn(forbidden, text)
        self.assertIn("Gtk.ScrolledWindow", text)
        self.assertIn("_poll_system_theme", text)
        self.assertIn("_appearance_status: dict", text)
        self.assertIn("Gio.ApplicationFlags.HANDLES_COMMAND_LINE", text)
        self.assertNotIn("message=str(error)", text)


if __name__ == "__main__":
    unittest.main()


class SettingsPendingDiscoveryTests(unittest.TestCase):
    def test_new_app_discovers_other_clients_pending_and_recovers(self):
        backend = Backend()
        pending = SettingsApplyResult("other-client", SettingsResultCategory.COMPLETION_UNKNOWN,
            SettingsApplyPhase.COMPLETION_UNKNOWN, "digest", ("visual.enabled",), "")
        backend.settings_status = lambda request_id: pending
        recovered = []
        def recover(request_id):
            recovered.append(request_id)
            return SettingsApplyResult(request_id, SettingsResultCategory.CONFLICT, SettingsApplyPhase.COMPLETE,
                "digest", ("visual.enabled",), "retired")
        backend.recover_settings = recover
        model = SettingsAppModel(backend)
        model.activate()
        self.assertEqual(model.settings_request_id, "other-client")
        self.assertTrue(model.state.completion_unknown)
        with self.assertRaises(RuntimeError):
            model.apply_settings({"visual.enabled": False})
        model.refresh_settings_status(recover=True)
        self.assertEqual(recovered, ["other-client"])
        self.assertFalse(model.state.completion_unknown)
        self.assertTrue(model.state.conflict)

    def test_busy_response_finds_actual_pending_id_instead_of_polling_unrecorded_request(self):
        backend = Backend()
        backend.settings_status = lambda request_id: None
        model = SettingsAppModel(backend)
        model.activate()
        backend.apply = lambda request: SettingsApplyResult(request.request_id, SettingsResultCategory.BUSY,
            SettingsApplyPhase.COMPLETION_UNKNOWN, "digest", (), "")
        backend.settings_status = lambda request_id: SettingsApplyResult("real-pending", SettingsResultCategory.COMPLETION_UNKNOWN,
            SettingsApplyPhase.COMPLETION_UNKNOWN, "digest", (), "")
        model.apply_settings({"theme.ui_scale": 1.25}, "new-id")
        self.assertEqual(model.settings_request_id, "real-pending")
        self.assertTrue(model.state.completion_unknown)
