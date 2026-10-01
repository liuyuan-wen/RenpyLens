"""Build a conservative READ/WRITE/CONTROL slice from a Ren'Py semantic graph."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from renpylens_semantics.serialization import load_graph, semantic_view


IGNORED_PREFIXES = ("_", "history_", "unread_")
IGNORED_NAMES = {"current_label", "last_label", "visited_labels"}


def _route_variables(state: dict) -> set[str]:
    return {
        name for name in state
        if name not in IGNORED_NAMES and not name.startswith(IGNORED_PREFIXES)
    }


def build_slice(graph: dict, seed_variables: set[str]) -> dict:
    icfg = graph["icfg"]
    narrative = graph["narrative_graph"]
    nodes = {node["node_id"]: node for node in icfg["nodes"]}
    blocks = {block["block_id"]: block for block in narrative["blocks"]}
    node_to_block = {
        node_id: block["block_id"]
        for block in narrative["blocks"]
        for node_id in block["source_nodes"]
    }
    write_nodes = defaultdict(set)
    write_edges = defaultdict(set)
    edge_records = {edge["edge_id"]: edge for edge in icfg["edges"]}
    for name, values in icfg.get("indexes", {}).get("writes", {}).items():
        write_nodes[name].update(values)
    for name, values in icfg.get("indexes", {}).get("edge_writes", {}).items():
        write_edges[name].update(values)

    relevant_variables = set(seed_variables)
    relevant_nodes = set()
    relevant_raw_edges = set()
    pending = deque(sorted(seed_variables))
    processed_variables = set()
    relevant_blocks = set()
    data_blocks = set()
    control_blocks = set()
    connector_blocks = set()
    inspected_control_origins = set()
    unknown_read_frontier = set()
    reverse = defaultdict(list)
    for edge in narrative["edges"]:
        reverse[edge["to"]].append(edge)

    while True:
        while pending:
            variable = pending.popleft()
            if variable in processed_variables:
                continue
            processed_variables.add(variable)
            for node_id in write_nodes.get(variable, ()):
                if node_id in relevant_nodes:
                    continue
                relevant_nodes.add(node_id)
                node = nodes[node_id]
                for dependency in node.get("reads", []) + node.get("control_reads", []):
                    if dependency == "*":
                        unknown_read_frontier.add(node_id)
                    if dependency not in relevant_variables:
                        relevant_variables.add(dependency)
                        pending.append(dependency)
            for edge_id in write_edges.get(variable, ()):
                relevant_raw_edges.add(edge_id)
                edge = edge_records[edge_id]
                relevant_nodes.add(edge["from"])
                for dependency in edge.get("control_reads", []):
                    if dependency not in relevant_variables:
                        relevant_variables.add(dependency)
                        pending.append(dependency)

        new_origins = {
            node_to_block[node_id] for node_id in relevant_nodes
            if node_id in node_to_block
        } - inspected_control_origins
        if not new_origins:
            break
        relevant_blocks.update(new_origins)
        data_blocks.update(new_origins)
        inspected_control_origins.update(new_origins)

        # Add the nearest upstream control point on every incoming path. This
        # is conservative structural control dependence, not a claim of full
        # post-dominator precision across dynamic calls.
        frontier = deque(new_origins)
        inspected = set(new_origins)
        while frontier:
            block_id = frontier.popleft()
            for edge in reverse.get(block_id, ()):
                predecessor = edge["from"]
                if predecessor in inspected:
                    continue
                inspected.add(predecessor)
                block = blocks[predecessor]
                if block["kind"] in ("control", "story_control_block"):
                    relevant_blocks.add(predecessor)
                    control_blocks.add(predecessor)
                    relevant_raw_edges.add(edge.get("source_edge"))
                    for dependency in block.get("control_reads", []):
                        if dependency == "*":
                            unknown_read_frontier.update(block.get("source_nodes", []))
                        if dependency not in relevant_variables:
                            relevant_variables.add(dependency)
                            pending.append(dependency)
                    continue
                relevant_blocks.add(predecessor)
                connector_blocks.add(predecessor)
                frontier.append(predecessor)

    selected_edges = [
        edge for edge in narrative["edges"]
        if edge["from"] in relevant_blocks and edge["to"] in relevant_blocks
    ]
    selected_return_sites = [
        site for site in narrative.get("return_sites", [])
        if site["call_block"] in relevant_blocks
        or site["continuation_block"] in relevant_blocks
    ]
    selected_blocks = []
    for block_id in sorted(relevant_blocks):
        block = dict(blocks[block_id])
        roles = []
        if block_id in data_blocks:
            roles.append("data")
        if block_id in control_blocks:
            roles.append("control")
        if block_id in connector_blocks:
            roles.append("connector")
        block["slice_roles"] = roles
        selected_blocks.append(block)
    unknown_blocks = [
        block["block_id"] for block in selected_blocks if block.get("unknown_effects")
    ]
    return {
        "status": "completed",
        "model": "indexed-known-effects-with-unknown-frontier",
        "seed_variables": sorted(seed_variables),
        "relevant_variables": sorted(relevant_variables),
        "summary": {
            "seed_variables": len(seed_variables),
            "relevant_variables": len(relevant_variables),
            "blocks": len(selected_blocks),
            "edges": len(selected_edges),
            "return_sites": len(selected_return_sites),
            "unknown_effect_blocks": len(unknown_blocks),
            "global_unknown_effect_nodes": len(
                icfg.get("indexes", {}).get("unknown_effect_nodes", [])
            ),
            "global_unknown_write_nodes": len(
                icfg.get("indexes", {}).get("unknown_write_nodes", [])
            ),
            "unknown_read_frontier": len(unknown_read_frontier),
        },
        "uncertainty": {
            "unknown_effects_are_first_class": True,
            "unknown_effects_outside_the_slice_may_affect_seed_variables": True,
            "included_unknown_effect_blocks": unknown_blocks,
            "unknown_read_frontier": sorted(unknown_read_frontier),
        },
        "unknown_read_frontier": sorted(unknown_read_frontier),
        "unknown_effect_blocks": unknown_blocks,
        "narrative_slice": {
            "blocks": selected_blocks,
            "edges": selected_edges,
            "return_sites": selected_return_sites,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", help="renpy_story_graph.json")
    parser.add_argument("--save-analysis", help="save_route_analysis.json")
    parser.add_argument("--variable", action="append", dest="variables")
    parser.add_argument("--include-runtime-state", action="store_true")
    parser.add_argument("--output", default="tests/renpy_story_slice.json")
    args = parser.parse_args()

    graph = semantic_view(load_graph(args.graph))
    variables = set(args.variables or [])
    if args.save_analysis:
        analysis = json.loads(Path(args.save_analysis).read_text(encoding="utf-8"))
        state = analysis.get("saved_state") or analysis.get("route_variables", {})
        variables.update(state if args.include_runtime_state else _route_variables(state))
    if not variables:
        parser.error("provide --save-analysis or at least one --variable")

    result = build_slice(graph, variables)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "status": result["status"],
        "output": str(output),
        "summary": result["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
