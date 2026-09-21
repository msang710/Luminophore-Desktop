from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DirectionalMoveResultContractTests(unittest.TestCase):
    def test_result_contract_is_typed_and_complete(self) -> None:
        header = (ROOT / "src/layout/LayoutManager.hpp").read_text()

        self.assertIn("enum eDirectionalMoveResult : uint8_t", header)
        for result in (
            "DIRECTIONAL_MOVE_MOVED",
            "DIRECTIONAL_MOVE_BLOCKED_EDGE",
            "DIRECTIONAL_MOVE_BLOCKED_POLICY",
            "DIRECTIONAL_MOVE_INVALID_TARGET",
        ):
            self.assertIn(result, header)

    def test_result_survives_every_layout_boundary(self) -> None:
        paths = (
            "src/layout/LayoutManager.hpp",
            "src/layout/space/Space.hpp",
            "src/layout/algorithm/Algorithm.hpp",
            "src/layout/algorithm/ModeAlgorithm.hpp",
        )

        for path in paths:
            source = (ROOT / path).read_text()
            self.assertIn("eDirectionalMoveResult", source, path)

        manager = (ROOT / "src/layout/LayoutManager.cpp").read_text()
        space = (ROOT / "src/layout/space/Space.cpp").read_text()
        algorithm = (ROOT / "src/layout/algorithm/Algorithm.cpp").read_text()
        self.assertIn("return target->space()->moveTargetInDirection", manager)
        self.assertIn("return m_algorithm->moveTargetInDirection", space)
        self.assertIn("return m_tiled->moveTargetInDirection", algorithm)
        self.assertIn("return m_floating->moveTargetInDirection", algorithm)

    def test_canonical_algorithms_implement_the_contract(self) -> None:
        paths = (
            "src/layout/algorithm/floating/default/DefaultFloatingAlgorithm.cpp",
            "src/luminophore/LuminophoreSpatialTiledAlgorithm.cpp",
        )

        for path in paths:
            source = (ROOT / path).read_text()
            self.assertIn("eDirectionalMoveResult", source, path)
            self.assertIn("DIRECTIONAL_MOVE_MOVED", source, path)
            self.assertIn("DIRECTIONAL_MOVE_BLOCKED_EDGE", source, path)

    def test_policy_block_is_distinct_from_physical_edge(self) -> None:
        space = (ROOT / "src/layout/space/Space.cpp").read_text()
        adapter = (ROOT / "src/luminophore/LuminophoreSpatialTiledAlgorithm.cpp").read_text()
        self.assertIn("DIRECTIONAL_MOVE_BLOCKED_POLICY", space)
        self.assertIn("DIRECTIONAL_MOVE_BLOCKED_EDGE", adapter)

    def test_keyboard_and_trackpad_edges_do_not_trigger_retired_half_clear(self) -> None:
        actions = (ROOT / "src/config/shared/actions/ConfigActions.cpp").read_text()
        gesture = (ROOT / "src/managers/input/trackpad/gestures/MoveGesture.cpp").read_text()

        self.assertNotIn("halfClearAfterDirectionalBlock", actions)
        self.assertNotIn("DIRECTIONAL_MOVE_BLOCKED_EDGE", gesture)

    def test_monitor_crossing_precedes_vacant_half_interpretation(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialTiledAlgorithm.cpp").read_text()
        start = source.index("Layout::eDirectionalMoveResult CLuminophoreSpatialTiledAlgorithm::moveTargetInDirection")
        move = source[start : source.index("CLuminophoreSpatialTiledAlgorithm::getNextCandidate", start)]

        self.assertIn("spatialRuntime()->dispatch(eSpatialAction::MOVE_WINDOW", move)
        self.assertIn("DIRECTIONAL_MOVE_MOVED", move)
        self.assertIn("DIRECTIONAL_MOVE_BLOCKED_EDGE", move)
        self.assertNotIn("Vacant", move)


if __name__ == "__main__":
    unittest.main()
