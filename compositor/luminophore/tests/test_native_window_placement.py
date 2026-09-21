from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class NativeWindowPlacementTests(unittest.TestCase):
    def test_minimized_is_independent_from_hidden(self) -> None:
        header = (ROOT / "src/desktop/view/Window.hpp").read_text()
        self.assertIn("m_minimized", header)
        self.assertIn("m_restorePlacement", header)
        self.assertIn("m_hidden", header)

    def test_shell_transaction_protocol_is_retired(self) -> None:
        source = (ROOT / "src/config/lua/bindings/LuaBindingsDispatchers.cpp").read_text()
        self.assertIn('Internal::setFn(L, "placement", hlLuminophorePlacement)', source)
        self.assertNotIn("hlLuminophoreHalfClear", source)

    def test_spatial_model_owns_relocation_without_a_layout_vacancy_tree(self) -> None:
        header = (ROOT / "src/luminophore/LuminophoreSpatialModel.hpp").read_text()
        source = (ROOT / "src/luminophore/LuminophoreSpatialModel.cpp").read_text()
        self.assertIn("relocationFor", header)
        self.assertIn("pushChain", header)
        self.assertIn("commitTiled", source)
        self.assertNotIn("DWINDLE_NODE_VACANT", source)
        self.assertFalse((ROOT / "src/layout/algorithm/tiled/dwindle/DwindleAlgorithm.cpp").exists())

    def test_service_and_legacy_spaces_do_not_enter_user_history(self) -> None:
        source = (
            ROOT / "src/desktop/history/WorkspaceHistoryTracker.cpp"
        ).read_text()
        self.assertIn("ws->role() == WORKSPACE_ROLE_SERVICE", source)
        self.assertIn("ws->role() == WORKSPACE_ROLE_LEGACY", source)
        self.assertIn("roleModelActive()", source)

    def test_workspace_migration_preflights_every_window(self) -> None:
        source = (ROOT / "src/state/WorkspacePlacementController.cpp").read_text()
        validation_end = source.index("report.workspaceCount = workspaces.size()")
        mutation_start = source.index("monitor->changeWorkspace(base")
        self.assertLess(validation_end, mutation_start)
        preflight = source[:validation_end]
        self.assertIn("State::migrationBlocker", preflight)
        self.assertIn("is not migration-safe", preflight)


if __name__ == "__main__":
    unittest.main()
