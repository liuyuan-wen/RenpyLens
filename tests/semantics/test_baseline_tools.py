import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_tools


class BaselineToolTests(unittest.TestCase):
    def test_timing_fields_do_not_change_digest(self):
        first = {"summary": {"nodes": 2}, "elapsed_seconds": 1.0}
        second = {"summary": {"nodes": 2}, "elapsed_seconds": 9.0}
        self.assertEqual(baseline_tools.digest(first), baseline_tools.digest(second))

    def test_graph_components_are_order_independent(self):
        graph = {
            "icfg": {"nodes": [{"node_id": "N2"}, {"node_id": "N1"}], "edges": []},
            "narrative_graph": {"blocks": [], "edges": [], "return_sites": []},
        }
        reversed_graph = copy.deepcopy(graph)
        reversed_graph["icfg"]["nodes"].reverse()
        self.assertEqual(
            baseline_tools.story_graph_digests(graph),
            baseline_tools.story_graph_digests(reversed_graph),
        )

    def test_semantic_change_is_reported(self):
        first = {"dialogue_items": [{"what": "one"}]}
        second = {"dialogue_items": [{"what": "two"}]}
        self.assertTrue(baseline_tools.compare(
            baseline_tools.shadow_route_digest(first),
            baseline_tools.shadow_route_digest(second),
        ))

    def test_semantic_route_ignores_protocol_metadata(self):
        first = {"graph_id": "G_x", "choices": [{"node_id": "N_x", "event_seq": 1}], "state_version": 3}
        second = {"graph_id": "G_x", "choices": [{"node_id": "N_x", "event_seq": 9}], "state_version": 8}
        self.assertEqual(
            baseline_tools.semantic_route_digest(first),
            baseline_tools.semantic_route_digest(second),
        )

    def test_semantic_route_detects_graph_identity_change(self):
        first = {"graph_id": "G_x", "choices": []}
        second = {"graph_id": "G_y", "choices": []}
        self.assertNotEqual(
            baseline_tools.semantic_route_digest(first),
            baseline_tools.semantic_route_digest(second),
        )


if __name__ == "__main__":
    unittest.main()
