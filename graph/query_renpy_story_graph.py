"""Query nodes, labels, variables, locations, and neighborhoods in a story graph."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from renpylens_semantics.serialization import load_graph, semantic_view


def query(graph: dict, *, node_id=None, label=None, variable=None,
          filename=None, line=None, text=None, limit=100) -> dict:
    icfg = graph["icfg"]
    narrative = graph["narrative_graph"]
    nodes = {node["node_id"]: node for node in icfg["nodes"]}
    owners = {
        raw_id: block["block_id"]
        for block in narrative["blocks"] for raw_id in block["source_nodes"]
    }
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in icfg["edges"]:
        outgoing[edge["from"]].append(edge)
        if edge["to"] is not None:
            incoming[edge["to"]].append(edge)
    candidate_outgoing = defaultdict(list)
    candidate_incoming = defaultdict(list)
    for edge in graph.get("candidate_edges", []):
        candidate_outgoing[edge.get("from")].append(edge)
        if edge.get("to") is not None:
            candidate_incoming[edge.get("to")].append(edge)

    matched = set(nodes)
    criteria = {}
    if node_id:
        matched &= {node_id}
        criteria["node"] = node_id
    if label:
        values = icfg.get("indexes", {}).get("labels", {}).get(label, [])
        matched &= set(values)
        criteria["label"] = label
    if variable:
        indexes = icfg.get("indexes", {})
        variable_nodes = set()
        roles = {}
        for role in ("reads", "writes", "controls"):
            for value in indexes.get(role, {}).get(variable, []):
                variable_nodes.add(value)
                roles.setdefault(value, []).append(role[:-1] if role.endswith("s") else role)
        matched &= variable_nodes
        criteria["variable"] = variable
    else:
        roles = {}
    if filename:
        normalized = filename.replace("\\", "/").lower()
        matched &= {
            value for value, node in nodes.items()
            if node.get("location", {}).get("filename", "").replace("\\", "/").lower() == normalized
        }
        criteria["filename"] = filename
    if line is not None:
        matched &= {
            value for value, node in nodes.items()
            if node.get("location", {}).get("line") == line
        }
        criteria["line"] = line
    if text:
        needle = text.casefold()
        matched &= {
            value for value, node in nodes.items()
            if any(needle in str(content.get("raw_what", content.get("raw_text", ""))).casefold()
                   for content in node.get("contents", []))
            or any(needle in str(edge.get("raw_choice", "")).casefold()
                   for edge in outgoing.get(value, []))
        }
        criteria["text"] = text

    ordered = sorted(matched, key=lambda value: (
        nodes[value].get("location", {}).get("filename", ""),
        nodes[value].get("location", {}).get("line") or 0,
        value,
    ))
    truncated = len(ordered) > limit
    records = []
    for value in ordered[:limit]:
        records.append({
            "node": nodes[value],
            "block": owners.get(value),
            "variable_roles": roles.get(value, []),
            "incoming": incoming.get(value, []),
            "outgoing": outgoing.get(value, []),
            "candidate_incoming": candidate_incoming.get(value, []),
            "candidate_outgoing": candidate_outgoing.get(value, []),
        })
    return {
        "status": "completed",
        "graph_fingerprint": graph.get("provenance", {}).get("sha256"),
        "criteria": criteria,
        "match_count": len(ordered),
        "returned_count": len(records),
        "truncated": truncated,
        "results": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph")
    parser.add_argument("--node")
    parser.add_argument("--label")
    parser.add_argument("--variable")
    parser.add_argument("--file")
    parser.add_argument("--line", type=int)
    parser.add_argument("--text")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not any((args.node, args.label, args.variable, args.file, args.line, args.text)):
        parser.error("provide at least one query criterion")
    graph = semantic_view(load_graph(args.graph))
    result = query(
        graph, node_id=args.node, label=args.label, variable=args.variable,
        filename=args.file, line=args.line, text=args.text, limit=args.limit,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
