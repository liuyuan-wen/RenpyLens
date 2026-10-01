import hashlib
from collections import OrderedDict
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from renpylens_semantics.expressions import parse_expression_ir
from renpylens_semantics.python_semantics import analyze_ir
from renpylens_semantics.call_semantics import CallPlanError, evaluate_call, plan_call
from renpylens_semantics.model import (
    BlockIR, CallArgumentIR, CallSiteIR, EdgeIR, EdgeRef, EffectIR, ExprIR,
    FormalBindingIR, GraphIR, NodeIR, NodeRef, PrecisionSet, ScreenActionIR,
    ScreenAnalysis, ScreenIR, SourceLocation, SymbolRef,
)
from renpylens_semantics.protocol import EventOrderTracker, EventSequencer, envelope
from renpylens_semantics.renpy_adapter import IdentityIndex, canonical_path, script_manifest
from renpylens_semantics.serialization import from_dict, project_v2, semantic_view
from renpylens_semantics.graph_v3 import _block_precision
from renpylens_semantics.screen_semantics import analyze_action_source, analyze_screen_sources


class IRModelTests(unittest.TestCase):
    def test_value_objects_round_trip(self):
        expression = ExprIR(
            "persistent.flag and score",
            (SymbolRef.persistent("flag"), SymbolRef.store("score")),
            "exact",
        )
        effect = EffectIR("assign", SymbolRef.store("score"), "add", expression)
        self.assertEqual(ExprIR.from_dict(expression.to_dict()).source, expression.source)
        self.assertEqual(EffectIR.from_dict(effect.to_dict()).target.display(), "score")
        self.assertEqual(SourceLocation.from_dict({"filename": "game\\a.rpy", "line": 4}).path,
                         "a.rpy")
        self.assertEqual(NodeRef("G_x", "N_x", "S_x", "C_x").to_dict()["graph_id"], "G_x")
        self.assertEqual(EdgeRef("G_x", "E_x", "N_a", "N_b").target_node_id, "N_b")
        self.assertEqual(PrecisionSet.from_dict({"topology": "partial"}).topology, "partial")
        node = NodeIR(NodeRef("G_x", "N_x", "S_x", "C_x"), "Say",
                      location=SourceLocation("game/a.rpy", 3),
                      reads=(SymbolRef.store("score"),),
                      extra={"source_tag": "fixture"})
        self.assertEqual(NodeIR.from_dict(node.to_dict()).extra["source_tag"], "fixture")
        edge = EdgeIR(EdgeRef("G_x", "E_x", "N_x", "N_y"), "NEXT",
                      metadata={"source_tag": "fixture"})
        self.assertEqual(EdgeIR.from_dict(edge.to_dict()).metadata["source_tag"], "fixture")
        call = CallSiteIR(
            NodeRef("G_x", "N_y", "S_y", "C_y"), False,
            (CallArgumentIR(0, None, ExprIR("score")),),
            (FormalBindingIR(SymbolRef.local("value", "S_y"), "positional", 0,
                             ExprIR("score"), "exact"),),
            NodeRef("G_x", "N_z", "S_z", "C_z"), precision=PrecisionSet(),
        )
        edge = EdgeIR(EdgeRef("G_x", "E_call", "N_x", "N_y"), "CALL_EDGE",
                      call_site=call)
        self.assertEqual(EdgeIR.from_dict(edge.to_dict()).call_site.target.node_id, "N_y")
        block = BlockIR("B_x", "basic", "linear", "N_x", "N_x",
                        location=SourceLocation("game/a.rpy", 3),
                        source_nodes=("N_x",), effects=(effect,),
                        story_relevance=("state",), metadata={"source_tag": "fixture"})
        loaded_block = BlockIR.from_dict(block.to_dict())
        self.assertEqual(loaded_block.metadata["source_tag"], "fixture")
        self.assertEqual(loaded_block.effects[0].target.display(), "score")
        self.assertEqual(loaded_block.story_relevance, ("state",))

    def test_full_v3_round_trip_preserves_local_owner_and_screen_fields(self):
        graph_id = "G_" + "a" * 64
        owner = "S_" + "1" * 32
        node = NodeIR(NodeRef(graph_id, "N_" + "1" * 32, owner, "C_" + "2" * 64),
                      "Python", reads=(SymbolRef.local("value", owner),))
        action = ScreenActionIR("A_x", "set_variable", target=SymbolRef.store("flag"))
        screen = ScreenIR("SC_x", "choice", parameters=(SymbolRef.local("p", "SC_x"),),
                          actions=(action,))
        graph = GraphIR(graph_id=graph_id, nodes=(node,), screens=(screen,))
        loaded = from_dict(graph.to_dict())
        self.assertEqual(loaded.nodes[0].reads[0].owner, owner)
        self.assertEqual(loaded.screens[0].actions[0].kind, "set_variable")

    def test_symbol_classification_and_parse_failure(self):
        expression = parse_expression_ir("persistent.flag and score and args[0]")
        kinds = {item.kind for item in expression.reads}
        self.assertEqual(kinds, {"persistent", "store", "subscript"})
        failed = parse_expression_ir("score +")
        self.assertEqual(failed.precision, "unknown")
        self.assertTrue(failed.parse_error)
        self.assertEqual(failed.reads[0].kind, "unknown")

    def test_python_statement_ir_keeps_store_namespaces_typed(self):
        result = analyze_ir(
            "persistent.flag = score\n"
            "if persistent.flag:\n"
            "    local_value += 1\n",
            local_names=("local_value",), local_owner="S_owner",
        )
        self.assertIn("persistent.flag", [item.display() for item in result["reads"]])
        self.assertEqual(result["control_reads"][0].kind, "persistent")
        self.assertEqual(result["effects"][0].target.kind, "persistent")
        self.assertEqual(result["effects"][1].target.kind, "local")

    def test_presentation_does_not_lower_block_topology_or_content(self):
        presentation = NodeIR(
            NodeRef("G_x", "N_p", "S_p", "C_p"), "UserStatement",
            precision=PrecisionSet("exact", "conservative", "conservative"),
            story_relevance="presentation",
        )
        narrative = NodeIR(
            NodeRef("G_x", "N_n", "S_n", "C_n"), "Say",
            precision=PrecisionSet("exact", "exact", "exact"),
            story_relevance="narrative",
        )
        precision = _block_precision((presentation, narrative))
        self.assertEqual(precision.topology, "exact")
        self.assertEqual(precision.state, "conservative")
        self.assertEqual(precision.content, "exact")

    def test_v2_loader_and_projection_preserve_original_document(self):
        original = {
            "schema_version": 2, "engine": "test",
            "provenance": {"sha256": "a" * 64},
            "icfg": {"nodes": [], "edges": [], "return_sites": [], "indexes": {}},
            "narrative_graph": {"blocks": [], "edges": [], "return_sites": []},
        }
        self.assertEqual(project_v2(from_dict(original)), original)
        exported = from_dict(original).to_dict()
        self.assertEqual(project_v2(from_dict(exported)), original)

    def test_semantic_view_keeps_v3_canonical_ids(self):
        node = NodeIR(NodeRef("G_" + "a" * 64, "N_" + "1" * 32,
                              "S_" + "1" * 32, "C_" + "1" * 64), "Python",
                      reads=(SymbolRef.store("score"),))
        graph = from_dict({
            "schema_version": 3, "graph_id": node.ref.graph_id,
            "provenance": {"sha256": "a" * 64},
            "icfg": {"nodes": [node.to_dict()], "edges": [], "return_sites": [], "indexes": {}},
            "narrative_graph": {"blocks": [], "edges": [], "return_sites": []},
        })
        view = semantic_view(graph)
        self.assertEqual(view["icfg"]["nodes"][0]["node_id"], node.ref.node_id)
        self.assertEqual(view["icfg"]["nodes"][0]["reads"], ["score"])

    def test_v3_loader_preserves_call_site_on_edge(self):
        graph_id = "G_" + "a" * 64
        call = CallSiteIR(
            NodeRef(graph_id, "N_" + "2" * 32, "S_" + "2" * 32, "C_" + "2" * 64),
            False,
            (CallArgumentIR(0, None, ExprIR("score")),),
            (FormalBindingIR(SymbolRef.local("message", "S_" + "2" * 32),
                             "positional", 0, ExprIR("score"), "exact"),),
            NodeRef(graph_id, "N_" + "3" * 32, "S_" + "3" * 32, "C_" + "3" * 64),
        )
        edge = EdgeIR(
            EdgeRef(graph_id, "E_" + "4" * 32, "N_" + "1" * 32,
                    "N_" + "2" * 32),
            "CALL_EDGE", call_site=call,
        )
        graph = from_dict({
            "schema_version": 3, "graph_id": graph_id,
            "icfg": {"nodes": [], "edges": [edge.to_dict()],
                     "return_sites": [], "indexes": {}},
            "narrative_graph": {"blocks": [], "edges": [], "return_sites": []},
        })
        loaded = graph.edges[0].call_site
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.formal_bindings[0].formal.kind, "local")
        self.assertEqual(semantic_view(graph)["icfg"]["edges"][0]["call"]["arguments"][0]["expression"]["source"], "score")


class IdentityProtocolTests(unittest.TestCase):
    def _nodes(self, order=(0, 1)):
        Label = type("Label", (), {})
        Say = type("Say", (), {})
        label = Label()
        label.name = "start"
        label.filename = "game/script.rpy"
        label.linenumber = 1
        label.next = None
        label.block = []
        say_a = Say()
        say_a.filename = "game/script.rpy"
        say_a.linenumber = 2
        say_a.name = "start"
        say_a.who = "a"
        say_a.what = "first"
        say_a.next = None
        say_b = Say()
        say_b.filename = "game/script.rpy"
        say_b.linenumber = 3
        say_b.name = "start"
        say_b.who = "a"
        say_b.what = "second"
        say_b.next = None
        label.block = [say_a, say_b]
        return [label, [say_a, say_b][order[0]], [say_a, say_b][order[1]]]

    def test_ids_are_structural_and_content_is_separate(self):
        nodes = self._nodes()
        index = IdentityIndex("G_" + "a" * 64, nodes, "")
        first = index.ref_for(nodes[1])
        changed = type("Say", (), {})()
        changed.filename = nodes[1].filename
        changed.linenumber = nodes[1].linenumber
        changed.name = nodes[1].name
        changed.who = nodes[1].who
        changed.what = "changed"
        changed.next = None
        nodes[0].block[0] = changed
        nodes[1] = changed
        changed_index = IdentityIndex("G_" + "a" * 64, nodes, "")
        changed_ref = changed_index.ref_for(changed)
        self.assertEqual(first.node_id, changed_ref.node_id)
        self.assertNotEqual(first.content_hash, changed_ref.content_hash)

    def test_label_formals_produce_local_symbol_refs(self):
        nodes = self._nodes()
        nodes[0].parameters = SimpleNamespace(positional=[("message", None)], extrapos=None, extrakw=None)
        index = IdentityIndex("G_" + "a" * 64, nodes, "")
        names, owner = index.scope_for(nodes[1])
        self.assertEqual(names, ("message",))
        self.assertTrue(owner.startswith("S_"))
        self.assertEqual(parse_expression_ir("message", names, owner).reads[0].kind, "local")

    def test_input_order_and_paths_are_normalized(self):
        first_nodes = self._nodes((0, 1))
        second_nodes = self._nodes((1, 0))
        first = IdentityIndex("G_" + "a" * 64, first_nodes, "")
        second = IdentityIndex("G_" + "a" * 64, second_nodes, "")
        first_say = next(node for node in first_nodes if getattr(node, "what", "") == "first")
        second_say = next(node for node in second_nodes if getattr(node, "what", "") == "first")
        self.assertEqual(first.ref_for(first_say).node_id, second.ref_for(second_say).node_id)
        self.assertEqual(canonical_path("game\\sub\\a.rpyc"), "sub/a.rpy")

        first_node = self._nodes()[1]
        second_node = self._nodes()[2]
        first_node.linenumber = second_node.linenumber = 20
        first_node.what, second_node.what = "same-a", "same-b"
        first_index = IdentityIndex("G_" + "a" * 64, [first_node, second_node], "")
        second_index = IdentityIndex("G_" + "a" * 64, [second_node, first_node], "")
        self.assertEqual(len(first_index.ambiguous_nodes), 2)
        self.assertEqual(len(second_index.ambiguous_nodes), 2)
        self.assertNotEqual(first_index.ref_for(first_node).node_id,
                            first_index.ref_for(second_node).node_id)

    def test_menu_edge_slot_is_shared_with_runtime_identity(self):
        Menu = type("Menu", (), {})
        Say = type("Say", (), {})
        menu = Menu()
        menu.filename = "game/script.rpy"
        menu.linenumber = 10
        menu.name = "menu"
        menu.next = None
        choice = Say()
        choice.filename = "game/script.rpy"
        choice.linenumber = 11
        choice.name = "menu"
        choice.what = "choice"
        choice.who = ""
        choice.next = None
        menu.items = [("Choice", None, [choice])]
        index = IdentityIndex("G_" + "a" * 64, [menu, choice], "")
        self.assertEqual(
            index.edge_id(menu, choice, "MENU_CHOICE", 0),
            index.edge_id(menu, choice, "MENU_CHOICE", "0"),
        )

    def test_translation_owner_is_a_structural_anchor(self):
        Translate = type("Translate", (), {})
        Say = type("TranslateSay", (), {})
        translation = Translate()
        translation.identifier = "fixture_line"
        translation.language = "test"
        translation.filename = "game/script.rpy"
        translation.linenumber = 20
        translation.block = []
        translation.next = None
        line = Say()
        line.filename = "game/script.rpy"
        line.linenumber = 21
        line.what = "first translation"
        line.who = ""
        line.next = None
        translation.block = [line]
        first = IdentityIndex("G_" + "a" * 64, [translation, line], "")
        first_ref = first.ref_for(line)
        line.what = "edited translation"
        second = IdentityIndex("G_" + "b" * 64, [line, translation], "")
        second_ref = second.ref_for(line)
        self.assertEqual(first_ref.node_id, second_ref.node_id)
        self.assertNotEqual(first_ref.content_hash, second_ref.content_hash)

    def test_protocol_sequence_and_envelope(self):
        sequence = EventSequencer()
        self.assertEqual(sequence.next(), 1)
        self.assertEqual(sequence.next(), 2)
        message = envelope("current", "G_x", "session", 4, "N_x", event_seq=2)
        self.assertEqual(message["protocol_version"], 3)
        self.assertEqual(message["event_seq"], 2)
        self.assertEqual(message["state_version"], 4)
        self.assertEqual(envelope("runtime_ready", "G_x", "session")["node_id"], "")

    def test_protocol_order_rejects_duplicate_and_allows_continuation(self):
        tracker = EventOrderTracker()
        tracker.establish("G_x", "session")
        self.assertTrue(tracker.accept(envelope("current", "G_x", "session", 2, "N_x", event_seq=1)))
        self.assertTrue(tracker.accept(envelope("branch_prefetch", "G_x", "session", 2, "N_x", event_seq=2)))
        self.assertFalse(tracker.accept(envelope("branch_prefetch", "G_x", "session", 2, "N_x", event_seq=2)))
        self.assertFalse(tracker.accept(envelope("current", "G_x", "session", 1, "N_x", event_seq=3)))
        self.assertFalse(tracker.accept(envelope("current", "G_x", "other", 3, "N_x", event_seq=4)))
        tracker.establish("G_next", "other")
        self.assertTrue(tracker.accept(envelope("current", "G_next", "other", 0, "N_x", event_seq=1)))
        self.assertTrue(tracker.accept({"type": "current", "session_id": "legacy"}))

    def test_current_and_prefetch_can_arrive_in_reverse_order_for_one_state(self):
        for earlier_type, later_type in (("current", "branch_prefetch"),
                                         ("branch_prefetch", "current")):
            with self.subTest(earlier_type=earlier_type):
                tracker = EventOrderTracker()
                tracker.establish("G_x", "session")
                earlier = envelope(earlier_type, "G_x", "session", 2, "N_x", event_seq=1)
                later = envelope(later_type, "G_x", "session", 2, "N_x", event_seq=2)
                self.assertTrue(tracker.accept(later))
                self.assertTrue(tracker.accept(earlier))
                self.assertEqual(tracker.event_seq, 2)
                self.assertFalse(tracker.accept(earlier))
                self.assertFalse(tracker.accept(later))
                self.assertTrue(tracker.accept(envelope(
                    "branch_prefetch", "G_x", "session", 2, "N_x", event_seq=3)))

    def test_reordered_text_cannot_cross_a_route_change_or_selection(self):
        for control_type in ("route_invalidated", "menu_selected"):
            with self.subTest(control_type=control_type):
                tracker = EventOrderTracker()
                tracker.establish("G_x", "session")
                self.assertTrue(tracker.accept(envelope(
                    control_type, "G_x", "session", 2, "N_x", event_seq=2)))
                self.assertTrue(tracker.accept(envelope(
                    "branch_prefetch", "G_x", "session", 2, "N_x", event_seq=3)))
                self.assertFalse(tracker.accept(envelope(
                    "current", "G_x", "session", 2, "N_x", event_seq=1)))

    def test_reordered_current_still_rejects_older_state_or_current_event(self):
        tracker = EventOrderTracker()
        tracker.establish("G_x", "session")
        self.assertTrue(tracker.accept(envelope(
            "current", "G_x", "session", 2, "N_x", event_seq=2)))
        self.assertTrue(tracker.accept(envelope(
            "branch_prefetch", "G_x", "session", 2, "N_x", event_seq=4)))
        self.assertFalse(tracker.accept(envelope(
            "current", "G_x", "session", 2, "N_x", event_seq=1)))
        self.assertFalse(tracker.accept(envelope(
            "current", "G_x", "session", 1, "N_x", event_seq=3)))
        self.assertTrue(tracker.accept(envelope(
            "current", "G_x", "session", 2, "N_x", event_seq=3)))

    def test_call_planner_binds_source_order_and_preserves_errors(self):
        arguments = SimpleNamespace(arguments=[(None, "score + 1"), ("suffix", "'ok'")],
                                    starred_indexes=set(), doublestarred_indexes=set())
        parameters = SimpleNamespace(parameters=[("message", None), ("suffix", "'default'")],
                                     extrapos=None, extrakw=None)
        site = plan_call(arguments, parameters, callee_owner="S_callee")
        self.assertEqual([item.formal.display() for item in site.formal_bindings],
                         ["local.message", "local.suffix"])
        self.assertEqual([item.source_kind for item in site.formal_bindings],
                         ["positional", "keyword"])
        self.assertEqual(evaluate_call(site, lambda source: {"score + 1": 4, "'ok'": "ok"}[source]),
                         {"message": 4, "suffix": "ok"})
        invalid = plan_call(SimpleNamespace(arguments=[(None, "1")], starred_indexes={0},
                                            doublestarred_indexes=set()), parameters)
        with self.assertRaises(CallPlanError):
            evaluate_call(invalid, lambda source: source)

    def test_call_planner_represents_default_missing_extra_and_dynamic_calls(self):
        parameters = SimpleNamespace(
            parameters=[("required", None), ("optional", "score + 2")],
            extrapos=None, extrakw=None,
        )
        defaulted = plan_call(
            SimpleNamespace(arguments=[(None, "1")], starred_indexes=set(), doublestarred_indexes=set()),
            parameters, callee_owner="S_target",
        )
        self.assertEqual([item.source_kind for item in defaulted.formal_bindings],
                         ["positional", "default"])
        self.assertEqual(evaluate_call(defaulted, lambda source: {"1": 1, "score + 2": 5}[source]),
                         {"required": 1, "optional": 5})
        missing = plan_call(
            SimpleNamespace(arguments=[], starred_indexes=set(), doublestarred_indexes=set()),
            parameters,
        )
        self.assertEqual(missing.reason, "missing-argument")
        extra = plan_call(
            SimpleNamespace(arguments=[(None, "1"), (None, "2"), (None, "3")],
                            starred_indexes=set(), doublestarred_indexes=set()),
            parameters,
        )
        self.assertEqual(extra.reason, "unexpected-arguments")
        dynamic = plan_call(
            SimpleNamespace(arguments=[(None, "score")], starred_indexes=set(), doublestarred_indexes=set()),
            None, dynamic_target=True,
        )
        self.assertTrue(dynamic.dynamic_target)
        self.assertEqual(dynamic.precision.topology, "unknown")
        self.assertEqual(dynamic.arguments[0].expression.reads[0].display(), "score")

        legacy_parameters = SimpleNamespace(
            positional=[("legacy", "'fallback'")], extrapos=None, extrakw=None,
        )
        legacy = plan_call(None, legacy_parameters)
        self.assertEqual(legacy.formal_bindings[0].source_kind, "default")

    def test_call_planner_detects_duplicate_keywords_and_preserves_mapping_order(self):
        arguments = SimpleNamespace(arguments=[("value", "1"), ("value", "2")],
                                    starred_indexes=set(), doublestarred_indexes=set())
        parameters = SimpleNamespace(parameters=[("value", None)], extrapos=None, extrakw=None)
        duplicate = plan_call(arguments, parameters)
        self.assertEqual(duplicate.reason, "duplicate-argument")
        empty = object()
        ordered = SimpleNamespace(parameters=OrderedDict((
            ("zeta", SimpleNamespace(name="zeta", default=empty, empty=empty, kind=0)),
            ("alpha", SimpleNamespace(name="alpha", default=empty, empty=empty, kind=0)),
        )),
                                  extrapos=None, extrakw=None)
        planned = plan_call(SimpleNamespace(arguments=[(None, "1"), (None, "2")],
                                            starred_indexes=set(), doublestarred_indexes=set()),
                            ordered)
        self.assertEqual([item.formal.name for item in planned.formal_bindings],
                         ["zeta", "alpha"])

    def test_malformed_declared_v3_protocol_is_rejected(self):
        tracker = EventOrderTracker()
        tracker.establish("G_x", "session")
        self.assertFalse(tracker.accept({"type": "current", "protocol_version": 3,
                                         "session_id": "session", "event_seq": 1,
                                         "state_version": 0}))
        self.assertFalse(tracker.accept({"type": "current", "protocol_version": 3,
                                         "graph_id": "G_x", "session_id": "session",
                                         "state_version": 0}))


class ScreenSemanticsTests(unittest.TestCase):
    def test_native_actions_and_ordered_lists(self):
        action = analyze_action_source(
            "[SetVariable('score', score + 1), If(flag, Jump('win'), Return('lost'))]",
            "SC_x",
        )
        self.assertEqual(action.kind, "list")
        self.assertEqual(action.children[0].kind, "set_variable")
        self.assertEqual(action.children[0].effects[0].target.display(), "score")
        self.assertEqual(action.children[1].kind, "if")
        self.assertEqual(action.children[1].children[0].target, "win")
        self.assertEqual(action.children[1].children[1].kind, "return")

    def test_dynamic_and_custom_actions_are_unknown_boundaries(self):
        dynamic = analyze_action_source("Jump(target_label)", "SC_x")
        custom = analyze_action_source("CustomAction('fake_label')", "SC_x")
        self.assertEqual(dynamic.precision.topology, "unknown")
        self.assertEqual(custom.kind, "unknown")
        self.assertNotEqual(custom.target, "fake_label")

    def test_screen_analysis_round_trip(self):
        analysis = analyze_screen_sources([{
            "name": "fixture", "variant": "", "parameters": ["choice"],
            "location": {"path": "game/screens.rpy", "line": 3},
            "actions": [{"source": "ToggleVariable('persistent.flag')"}],
        }])
        self.assertEqual(analysis.screens[0].parameters[0].kind, "local")
        self.assertEqual(analysis.screens[0].actions[0].effects[0].target.kind, "persistent")
        loaded = ScreenAnalysis.from_dict(analysis.to_dict())
        self.assertEqual(loaded.screens[0].name, "fixture")

    def test_manifest_prefers_source_and_excludes_instrumentation(self):
        with tempfile.TemporaryDirectory() as root:
            game = Path(root) / "game"
            game.mkdir()
            (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
            (game / "script.rpyc").write_bytes(b"compiled")
            (game / "_translator_hook.rpy").write_text("ignored", encoding="utf-8")
            (game / "_renpylens_semantics.zip").write_bytes(b"ignored")
            (game / "cache").mkdir()
            (game / "cache" / "compiled.rpy").write_text("ignored", encoding="utf-8")
            (game / "saves").mkdir()
            (game / "saves" / "save.rpy").write_text("ignored", encoding="utf-8")
            manifest, graph_id = script_manifest(str(game))
            self.assertTrue(graph_id.startswith("G_") and len(graph_id) == 66)
            self.assertEqual([item["path"] for item in manifest["script_files"]], ["script.rpy"])


if __name__ == "__main__":
    unittest.main()
