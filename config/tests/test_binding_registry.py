from pathlib import Path
import os
import re
import shutil
import subprocess
import unittest

from luminophore_shell.binding_registry import (
    BindingAction,
    BindingFlags,
    BindingRegistry,
    default_registry,
    normalize_chord,
)


class BindingRegistryTests(unittest.TestCase):
    def test_chord_normalization_is_stable(self) -> None:
        self.assertEqual(normalize_chord("shift + super + q"), "SUPER + SHIFT + Q")
        self.assertEqual(normalize_chord("control + alt + Delete"), "CTRL + ALT + DELETE")

    def test_duplicate_press_chord_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            BindingRegistry.build((
                BindingAction("a", "A", "test", "SUPER + A", recovery=True),
                BindingAction("b", "B", "test", "SUPER + A"),
            ))

    def test_press_and_release_pair_can_share_chord(self) -> None:
        registry = BindingRegistry.build((
            BindingAction("a", "A", "test", "SUPER + A", group_id="pair", recovery=True),
            BindingAction("b", "B", "test", "SUPER + A", BindingFlags(release=True), "pair"),
        ))
        self.assertEqual(len(registry.actions), 2)

    def test_atomic_group_cannot_be_partially_unbound(self) -> None:
        registry = default_registry()
        with self.assertRaisesRegex(ValueError, "partially unbound"):
            registry.update({"hardware.brightness_up.preview": (None, None)})

    def test_atomic_group_requires_same_chord_and_compatible_flags(self) -> None:
        registry = default_registry()
        with self.assertRaisesRegex(ValueError, "one chord"):
            registry.update({"hardware.brightness_up.commit": ("SUPER + B", None)})
        with self.assertRaisesRegex(ValueError, "consistent locked"):
            registry.update({
                "hardware.brightness_up.commit": (
                    "XF86MonBrightnessUp",
                    BindingFlags(release=True),
                ),
            })

    def test_recovery_actions_cannot_all_be_unbound(self) -> None:
        registry = default_registry()
        with self.assertRaisesRegex(ValueError, "recovery"):
            registry.update({"shell.settings": (None, None), "app.terminal": (None, None)})

    def test_default_registry_has_no_workspace_actions(self) -> None:
        registry = default_registry()
        ids = {action.action_id for action in registry.actions}
        self.assertEqual(
            {action_id for action_id in ids if action_id.startswith("workspace.")},
            set(),
        )
        self.assertEqual(registry.payload(), default_registry().payload())

    def test_default_registry_covers_current_binding_categories(self) -> None:
        ids = {action.action_id for action in default_registry().actions}
        expected = {
            "window.drag.begin", "shell.overview", "shell.appearance",
            "hardware.microphone_mute", "hardware.brightness_up.commit",
            "utility.color_picker", "app.mission_center",
            "view.wide.toggle",
        }
        self.assertTrue(expected <= ids)

    def test_custom_action_is_not_accepted_by_update(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            default_registry().update({"custom.shell": ("SUPER + R", None)})

    def test_default_registry_exactly_covers_current_binds_lua_inventory(self) -> None:
        source = Path("binds.lua").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'\bbind\("', source)), 63)
        self.assertEqual(len(re.findall(r"\bhl\.bind\(", source)), 2)  # packaged actions + app bundle shortcuts
        registry = default_registry()
        self.assertEqual(len(registry.actions), 63)

        evidence = {
            "window.kill_active": 'mainMod .. " + Escape"',
            "window.close": 'mainMod .. " + Q"',
            "window.float": 'mainMod .. " + D"',
            "window.fullscreen": 'mainMod .. " + F"',
            "view.wide.toggle": 'mainMod .. " + G"',
            "spatial.undo": 'history.undo',
            "spatial.redo": 'history.redo',
            "window.cycle": '"ALT + Tab"',
            "shell.spatial_editor": 'toggle spatial-editor',
            "shell.launcher": 'mainMod .. " + Tab"',
            "window.drag.begin": 'hl.dsp.window.drag()',
            "shell.outside_click": 'non_consuming = true',
            "cursor.zoom_out": 'zoomfunction(-0.3)',
            "cursor.zoom_in": 'zoomfunction(0.3)',
            "app.terminal": 'launchApp(TERMINAL)',
            "app.files": 'launchApp(FILE_MANAGER)',
            "app.editor": 'launchApp(EDITOR)',
            "app.calculator": 'launchApp(CALCULATOR)',
            "app.calculator_hardware": '"XF86Calculator"',
            "app.browser": 'launchApp(BROWSER)',
            "app.mission_center": 'launchApp("missioncenter")',
            "shell.settings": 'open system --provider settings',
            "view.desktop.toggle": 'hl.dsp.luminophore.desktop_toggle()',
            "shell.overview": 'toggle overview',
            "shell.emoji": 'open launcher --provider emoji',
            "hardware.volume_up": 'hardware volume-up',
            "hardware.volume_down": 'hardware volume-down',
            "hardware.volume_mute": 'hardware volume-mute',
            "hardware.microphone_mute": 'hardware mic-mute',
            "hardware.media_toggle": '"XF86AudioPlay"',
            "hardware.media_pause": '"XF86AudioPause"',
            "hardware.media_next": 'hardware media-next',
            "hardware.media_previous": 'hardware media-previous',
            "hardware.brightness_up.preview": 'hardware brightness-preview-up',
            "hardware.brightness_up.commit": 'hardware brightness-commit',
            "hardware.brightness_down.preview": 'hardware brightness-preview-down',
            "hardware.brightness_down.commit": 'hardware brightness-commit',
            "utility.color_picker": 'hyprpicker -a -n',
            "capture.region": 'hyprshot -m region --freeze -s --clipboard-only',
            "capture.region_save": 'xdg-user-dir PICTURES',
            "shell.appearance": 'open system --provider palette',
            "shell.wallpaper": 'wallpaper open',
            "shell.clipboard": 'open launcher --provider clip',
            "shell.notifications": 'open notifications',
        }
        ids = {action.action_id for action in registry.actions}
        dynamic_families = {
            action_id for action_id in ids
            if action_id.startswith(("view.move.", "view.adjust.", "board.move.", "window.focus.", "cursor.zoom_"))
        }
        covered = set(evidence) | dynamic_families | {"window.resize"}
        self.assertEqual(covered, ids)
        for action_id, snippet in evidence.items():
            with self.subTest(action_id=action_id):
                self.assertIn(snippet, source)

    def test_current_flags_and_atomic_groups_are_exactly_inventoried(self) -> None:
        actions = {action.action_id: action for action in default_registry().actions}
        repeating = {
            "cursor.zoom_out", "cursor.zoom_in", "cursor.zoom_out_keypad", "cursor.zoom_in_keypad",
            "hardware.volume_up", "hardware.volume_down",
            "hardware.brightness_up.preview", "hardware.brightness_down.preview",
        }
        locked = {
            "hardware.volume_up", "hardware.volume_down", "hardware.volume_mute",
            "hardware.microphone_mute", "hardware.media_toggle", "hardware.media_pause",
            "hardware.media_next", "hardware.media_previous",
            "hardware.brightness_up.preview", "hardware.brightness_up.commit",
            "hardware.brightness_down.preview", "hardware.brightness_down.commit",
        }
        release = {
            "shell.outside_click", "shell.overview",
            "hardware.brightness_up.commit", "hardware.brightness_down.commit",
        }
        non_consuming = {"shell.outside_click"}
        groups = {
            "hardware.brightness_up.preview": "brightness_up",
            "hardware.brightness_up.commit": "brightness_up",
            "hardware.brightness_down.preview": "brightness_down",
            "hardware.brightness_down.commit": "brightness_down",
        }
        self.assertEqual({key for key, value in actions.items() if value.flags.repeating}, repeating)
        self.assertEqual({key for key, value in actions.items() if value.flags.locked}, locked)
        self.assertEqual({key for key, value in actions.items() if value.flags.release}, release)
        self.assertEqual({key for key, value in actions.items() if value.flags.non_consuming}, non_consuming)
        self.assertEqual({key: value.group_id for key, value in actions.items() if value.group_id}, groups)

    @unittest.skipUnless(shutil.which("lua"), "lua interpreter unavailable")
    def test_packaged_lua_payload_exactly_matches_registry(self) -> None:
        script = r'''
local payload = dofile("luminophore_bindings.lua")
for _, row in ipairs(payload.bindings) do
  local flags = row.flags or {}
  print(table.concat({row.action_id, row.chord or "",
    flags.locked and "1" or "0", flags.repeating and "1" or "0",
    flags.release and "1" or "0", flags.non_consuming and "1" or "0",
    row.group_id or "", row.recovery and "1" or "0"}, "\t"))
end
'''
        output = subprocess.run(
            ["lua", "-e", script], check=True, text=True, capture_output=True,
        ).stdout.splitlines()
        actual = {}
        for line in output:
            fields = line.split("\t")
            self.assertEqual(len(fields), 8)
            action_id, chord, locked, repeating, release, non_consuming, group_id, recovery = fields
            actual[action_id] = (chord, locked, repeating, release, non_consuming, group_id, recovery)
        expected = {
            action.action_id: (
                action.chord or "",
                "1" if action.flags.locked else "0",
                "1" if action.flags.repeating else "0",
                "1" if action.flags.release else "0",
                "1" if action.flags.non_consuming else "0",
                action.group_id,
                "1" if action.recovery else "0",
            )
            for action in default_registry().actions
        }
        self.assertEqual(actual, expected)

    def test_generated_lua_payload_is_data_only_and_atomic(self) -> None:
        source = Path("luminophore_bindings.lua").read_text(encoding="utf-8")
        for token in ("exec_cmd", "hyprctl", "os.execute", "io.popen", "command ="):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        binds = Path("binds.lua").read_text(encoding="utf-8")
        self.assertIn('pcall(require, "config.luminophore_bindings")', binds)
        self.assertIn("if not seen[action_id] and not optional_action_ids[action_id] then rows = nil", binds)
        self.assertIn("local selected = rows and rows[item.action_id] or item", binds)

    @unittest.skipUnless(shutil.which("lua"), "lua interpreter unavailable")
    def test_optional_spatial_dispatcher_failure_cannot_abort_existing_bindings(self) -> None:
        harness = r'''
TERMINAL, FILE_MANAGER, EDITOR, CALCULATOR, BROWSER = "term", "files", "edit", "calc", "browser"
local proxy = {}
setmetatable(proxy, { __index = function() return proxy end, __call = function() return proxy end })
local captured = {}
hl = proxy
hl.dsp = proxy
hl.dsp.luminophore = { view_move = proxy, view_adjust = proxy, board_move = proxy }
hl.bind = function(chord) captured[#captured + 1] = chord end
dofile("binds.lua")
print(#captured)
'''
        output = subprocess.run(
            ["lua", "-e", harness], check=True, text=True, capture_output=True,
        ).stdout.strip()
        optional_count = sum(
            action.action_id.startswith(("view.move.", "view.adjust.", "board.move.", "window.focus."))
            or action.action_id in {"view.wide.toggle", "view.desktop.toggle", "spatial.undo", "spatial.redo"}
            for action in default_registry().actions
        )
        self.assertEqual(output, str(sum(a.chord is not None for a in default_registry().actions) - optional_count))
        source = Path("binds.lua").read_text(encoding="utf-8")
        spatial_block = source.index("local spatialDispatchersOk")
        wide_binding = source.index('bind("view.wide.toggle"')
        self.assertNotIn('bind("workspace.', source)
        self.assertNotIn('bind("', source[wide_binding + 1 : source.index("commit_bindings()", wide_binding)])
        self.assertIn("pcall(function()", source)

    def test_capture_backend_switch_is_scoped_to_luminophore_compositor(self) -> None:
        binds = Path("binds.lua").read_text(encoding="utf-8")
        self.assertIn('local luminophoreCompositor = os.getenv("LUMINOPHORE_COMPOSITOR") == "1"', binds)
        self.assertIn('luminophoreShell .. " capture region --freeze --clipboard-only"', binds)
        self.assertIn('luminophoreShell .. " capture region --freeze --save"', binds)
        self.assertIn('or "hyprshot -m region --freeze -s --clipboard-only"', binds)
        self.assertIn("or 'hyprshot -m region --freeze -s -o", binds)
        self.assertIn('hl.dsp.exec_cmd(captureRegion)', binds)
        self.assertIn('hl.dsp.exec_cmd(captureRegionSave)', binds)

    @unittest.skipUnless(shutil.which("lua"), "lua interpreter unavailable")
    def test_capture_backend_switch_evaluates_per_session_identity(self) -> None:
        harness = r'''
TERMINAL, FILE_MANAGER, EDITOR, CALCULATOR, BROWSER = "term", "files", "edit", "calc", "browser"
local proxy = {}
local proxy_meta = { __index = function() return proxy end, __call = function() return proxy end }
setmetatable(proxy, proxy_meta)
local captured = {}
local dsp = setmetatable({
  exec_cmd = function(command) return { kind = "exec", command = command } end,
}, proxy_meta)
hl = setmetatable({ dsp = dsp }, proxy_meta)
hl.bind = function(chord, implementation, flags)
  if chord == "Print" or chord == "SUPER + Print" then captured[chord] = implementation end
end
dofile("binds.lua")
print(captured["Print"].command)
print(captured["SUPER + Print"].command)
'''
        base = dict(os.environ)
        base["HOME"] = "/tmp/luminophore-test-home"
        base.pop("LUMINOPHORE_COMPOSITOR", None)
        legacy = subprocess.run(
            ["lua", "-e", harness], check=True, text=True, capture_output=True, env=base,
        ).stdout.splitlines()
        luminophore_env = {**base, "LUMINOPHORE_COMPOSITOR": "1"}
        luminophore = subprocess.run(
            ["lua", "-e", harness], check=True, text=True, capture_output=True, env=luminophore_env,
        ).stdout.splitlines()
        self.assertEqual(legacy, [
            "hyprshot -m region --freeze -s --clipboard-only",
            'hyprshot -m region --freeze -s -o "$(xdg-user-dir PICTURES)"',
        ])
        self.assertEqual(luminophore, [
            "/tmp/luminophore-test-home/.config/hypr/config/luminophore-shell capture region --freeze --clipboard-only",
            "/tmp/luminophore-test-home/.config/hypr/config/luminophore-shell capture region --freeze --save",
        ])

    def test_history_defaults_replace_unused_settings_shortcut(self) -> None:
        actions = {a.action_id: a for a in default_registry().actions}
        self.assertIsNone(actions["shell.settings"].chord)
        self.assertEqual(actions["spatial.undo"].chord, "SUPER + Z")
        self.assertEqual(actions["spatial.redo"].chord, "SUPER + SHIFT + Z")
        self.assertFalse(actions["spatial.undo"].flags.repeating)
        self.assertFalse(actions["spatial.redo"].flags.repeating)

    @unittest.skipUnless(shutil.which("lua"), "lua interpreter unavailable")
    def test_lua_payload_adoption_and_whole_fallback(self) -> None:
        harness = r'''
TERMINAL, FILE_MANAGER, EDITOR, CALCULATOR, BROWSER = "term", "files", "edit", "calc", "browser"
local proxy = {}
setmetatable(proxy, { __index = function() return proxy end, __call = function() return proxy end })
local captured = {}
hl = proxy
hl.bind = function(chord, implementation, flags)
  captured[#captured + 1] = { chord = chord, flags = flags or {} }
end
local payload = dofile("luminophore_bindings.lua")
for _, row in ipairs(payload.bindings) do
  if row.action_id == "window.kill_active" then
    row.chord = "SUPER + K"
  end
end
local mode = arg[1]
if mode == "missing" then table.remove(payload.bindings) end
if mode == "foreign" then
  payload.bindings[#payload.bindings + 1] = {action_id="custom.command", chord="SUPER + R", flags={}}
end
if mode == "duplicate" then payload.bindings[1].chord = payload.bindings[2].chord end
package.preload["config.luminophore_bindings"] = function() return payload end
dofile("binds.lua")
print(#captured .. "\t" .. captured[1].chord)
'''
        expected = {
            "valid": "62\tSUPER + K",
            "missing": "62\tSUPER + Escape",
            "foreign": "62\tSUPER + Escape",
            "duplicate": "62\tSUPER + Escape",
        }
        for mode, result in expected.items():
            with self.subTest(mode=mode):
                output = subprocess.run(
                    ["lua", "-e", harness.replace("local mode = arg[1]", f'local mode = "{mode}"')],
                    check=True, text=True, capture_output=True,
                ).stdout.strip()
                self.assertEqual(output, result)


if __name__ == "__main__":
    unittest.main()
