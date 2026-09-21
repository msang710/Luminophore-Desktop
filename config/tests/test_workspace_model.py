from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest

from luminophore_shell.binding_registry import default_registry


class WorkspaceModelTests(unittest.TestCase):
    def test_active_sources_have_no_numbered_workspace_model(self) -> None:
        sources = "\n".join(
            Path(path).read_text(encoding="utf-8")
            for path in (
                "variables.lua",
                "workspaces.lua",
                "binds.lua",
                "luminophore_bindings.lua",
                "luminophore_shell/binding_registry.py",
                "luminophore_shell/binding_service.py",
            )
        )
        self.assertNotIn("WORKSPACE_COUNT", sources)
        self.assertNotIn("workspace.focus.", sources)
        self.assertNotIn("workspace.move.", sources)

    def test_binding_inventory_has_no_workspace_actions(self) -> None:
        actions = {
            action.action_id
            for action in default_registry().actions
            if action.action_id.startswith("workspace.")
        }
        self.assertEqual(actions, set())

    def test_removed_workspace_actions_cannot_be_reintroduced_by_override(self) -> None:
        for action in ("workspace.special.move", "workspace.special.toggle"):
            with self.assertRaisesRegex(ValueError, "unknown"):
                default_registry().update({action: ("SUPER + S", None)})

    def test_game_rules_target_primary_monitor_base(self) -> None:
        source = Path("windowrules.lua").read_text(encoding="utf-8")
        self.assertIn('local gamingWorkspace = "name:luminophore-base-" .. PRIMARY_MONITOR', source)
        self.assertNotIn('local gamingWorkspace = "3"', source)

    def test_only_special_workspace_motion_remains(self) -> None:
        source = Path("animations.lua").read_text(encoding="utf-8")
        self.assertNotIn('leaf = "workspaces"', source)
        self.assertIn('leaf = "specialWorkspaceIn"', source)
        self.assertIn('leaf = "specialWorkspaceOut"', source)

    @unittest.skipUnless(shutil.which("lua"), "lua interpreter unavailable")
    def test_workspace_rules_define_one_base_per_monitor(self) -> None:
        harness = r'''
hl = { workspace_rule = function(rule) rules[#rules + 1] = rule end }
rules = {}
dofile("variables.lua")
dofile("workspaces.lua")
assert(#rules == 2)
assert(rules[1].workspace == "name:luminophore-base-DP-2")
assert(rules[1].monitor == "DP-2" and rules[1].persistent)
assert(rules[2].workspace == "name:luminophore-base-DP-1")
assert(rules[2].monitor == "DP-1" and rules[2].persistent)
'''
        subprocess.run(["lua", "-e", harness], check=True, text=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
