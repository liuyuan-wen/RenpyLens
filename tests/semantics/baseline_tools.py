"""Normalize and compare current StoryGraph and Shadow Route results."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TIMING_KEYS = frozenset((
    "elapsed_seconds",
    "runner_elapsed_seconds",
    "max_ms",
))

ROUTE_VOLATILE_KEYS = frozenset((
    "event_seq", "protocol_version", "state_version", "session_id",
    "runtime_path", "temporary_path", "elapsed_seconds",
    "runner_elapsed_seconds", "max_ms",
))


def _normalized(value):
    if isinstance(value, dict):
        return {
            key: _normalized(item)
            for key, item in sorted(value.items())
            if key not in TIMING_KEYS
        }
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def _semantic_normalized(value):
    """Keep route meaning while dropping transport and run-specific metadata."""
    if isinstance(value, dict):
        return {
            key: _semantic_normalized(item)
            for key, item in sorted(value.items())
            if key not in ROUTE_VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_semantic_normalized(item) for item in value]
    return value


def digest(value):
    payload = json.dumps(
        _normalized(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def story_graph_digests(graph):
    icfg = graph.get("icfg", {})
    narrative = graph.get("narrative_graph", {})
    components = {
        "coverage": graph.get("coverage"),
        "audit": graph.get("audit"),
        "summary": graph.get("summary"),
        "nodes": sorted(icfg.get("nodes", []), key=lambda item: item.get("node_id", "")),
        "edges": sorted(icfg.get("edges", []), key=lambda item: item.get("edge_id", "")),
        "return_sites": sorted(
            icfg.get("return_sites", []),
            key=lambda item: (item.get("call_node", ""), item.get("continuation", "")),
        ),
        "indexes": icfg.get("indexes", {}),
        "blocks": sorted(
            narrative.get("blocks", []), key=lambda item: item.get("block_id", "")
        ),
        "block_edges": sorted(
            narrative.get("edges", []), key=lambda item: item.get("edge_id", "")
        ),
        "block_return_sites": sorted(
            narrative.get("return_sites", []),
            key=lambda item: (item.get("call_block", ""), item.get("continuation_block", "")),
        ),
    }
    return {name: digest(value) for name, value in components.items()}


def shadow_route_digest(route):
    fields = (
        "engine", "seed", "dialogue_items", "choices", "inputs", "screens",
        "random", "stop_reason", "stop_node", "visited_nodes", "steps",
        "target_reached", "target_state", "constrained_menus", "used_target_labels",
    )
    return {name: digest(route.get(name)) for name in fields if name in route}


def semantic_route_digest(route):
    """Digest a deterministic route, excluding protocol and host metadata."""
    fields = (
        "engine", "graph_id", "language", "seed", "dialogue_items", "choices", "inputs",
        "screens", "random", "stop_reason", "stop_node", "visited_nodes",
        "steps", "target_reached", "target_state", "constrained_menus",
        "used_target_labels", "return_stack", "important_state",
    )
    defaults = {"graph_id": None, "language": None, "return_stack": [], "important_state": {}}
    return {
        name: digest(_semantic_normalized(route.get(name, defaults.get(name))))
        for name in fields
        if name in route or name in defaults
    }


def compare(expected, actual):
    differences = []
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            differences.append({
                "component": key,
                "expected": expected.get(key),
                "actual": actual.get(key),
            })
    return differences


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind", choices=("story-graph", "shadow-route", "semantic-route")
    )
    parser.add_argument("expected")
    parser.add_argument("actual")
    args = parser.parse_args()
    normalize = (
        story_graph_digests if args.kind == "story-graph"
        else semantic_route_digest if args.kind == "semantic-route"
        else shadow_route_digest
    )
    expected = normalize(_load(args.expected))
    actual = normalize(_load(args.actual))
    result = {"valid": not compare(expected, actual), "expected": expected, "actual": actual}
    result["differences"] = compare(expected, actual)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
