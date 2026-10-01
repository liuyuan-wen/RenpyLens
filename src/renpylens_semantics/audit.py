"""Validate a persisted Ren'Py semantic ICFG and its basic-block projection."""

import argparse
import json
import io
import os
import re
from collections import Counter, defaultdict


PRECISION_ORDER = {"exact": 0, "conservative": 1, "partial": 2,
                   "unknown": 3, "external": 4}

try:
    _TEXT_TYPES = (basestring,)
except NameError:
    _TEXT_TYPES = (str,)


def _fullmatch(pattern, value):
    """Python 2 compatible equivalent of re.fullmatch."""
    return re.match(r"(?:%s)\Z" % pattern, value)


def _merged_precision(values):
    return max(values or ["exact"],
               key=lambda value: PRECISION_ORDER.get(value, 3))


def validate(graph):
    errors = []
    warnings = []

    def error(code, **details):
        if len(errors) < 100:
            errors.append(dict({"code": code}, **details))

    schema_version = graph.get("schema_version")
    if schema_version not in (2, 3):
        error("unsupported-schema-version", actual=graph.get("schema_version"))
    fingerprint = graph.get("graph_id", "") if schema_version == 3 else graph.get("provenance", {}).get("sha256", "")
    if schema_version == 3 and fingerprint.startswith("G_"):
        fingerprint = fingerprint[2:]
    if not _fullmatch(r"[0-9a-f]{64}", fingerprint):
        error("invalid-game-fingerprint")

    icfg = graph.get("icfg", {})
    narrative = graph.get("narrative_graph", {})
    nodes = icfg.get("nodes", [])
    edges = icfg.get("edges", [])
    sites = icfg.get("return_sites", [])
    blocks = narrative.get("blocks", [])
    block_edges = narrative.get("edges", [])

    node_ids = [node.get("node_id") for node in nodes]
    edge_ids = [edge.get("edge_id") for edge in edges]
    block_ids = [block.get("block_id") for block in blocks]
    if len(set(node_ids)) != len(node_ids):
        error("duplicate-node-id")
    if len(set(edge_ids)) != len(edge_ids):
        error("duplicate-edge-id")
    if len(set(block_ids)) != len(block_ids):
        error("duplicate-block-id")
    for node_id in node_ids:
        pattern = r"N_[0-9a-f]{32}" if schema_version == 3 else r"N_[0-9a-f]{20}(?:_\d+)?"
        if not isinstance(node_id, _TEXT_TYPES) or not _fullmatch(pattern, node_id):
            error("invalid-node-id", node_id=node_id)
    if schema_version == 3:
        for node in nodes:
            for field, pattern in (("structural_id", r"S_[0-9a-f]{32}"),
                                   ("content_hash", r"C_[0-9a-f]{64}")):
                if not _fullmatch(pattern, str(node.get(field, ""))):
                    error("invalid-node-reference", field=field, node=node.get("node_id"))
            if node.get("graph_id") not in (None, graph.get("graph_id", "")):
                error("node-graph-id-mismatch", node=node.get("node_id"))
    for edge_id in edge_ids:
        pattern = r"E_[0-9a-f]{32}" if schema_version == 3 else r"E_[0-9a-f]{20}"
        if not isinstance(edge_id, _TEXT_TYPES) or not _fullmatch(pattern, edge_id):
            error("invalid-edge-id", edge_id=edge_id)
    if schema_version == 3:
        for edge in edges:
            if edge.get("graph_id") not in (None, graph.get("graph_id", "")):
                error("edge-graph-id-mismatch", edge=edge.get("edge_id"))
        for block_id in block_ids:
            if not isinstance(block_id, _TEXT_TYPES) or not _fullmatch(r"B_[0-9a-f]{32}", block_id):
                error("invalid-block-id", block_id=block_id)
        for edge in block_edges:
            edge_id = edge.get("edge_id")
            if not isinstance(edge_id, _TEXT_TYPES) or not _fullmatch(r"BE_[0-9a-f]{32}", edge_id):
                error("invalid-block-edge-id", edge_id=edge_id)

    node_map = {node["node_id"]: node for node in nodes}
    block_map = {block["block_id"]: block for block in blocks}
    edge_map = {edge["edge_id"]: edge for edge in edges}
    outgoing = defaultdict(list)
    edge_pairs = defaultdict(list)
    for edge in edges:
        source, target = edge.get("from"), edge.get("to")
        if source not in node_map:
            error("missing-edge-source", edge=edge.get("edge_id"), node=source)
        if target is not None and target not in node_map:
            error("missing-edge-target", edge=edge.get("edge_id"), node=target)
        resolution = edge.get("target_resolution")
        if target is None and resolution not in ("unresolved", "external"):
            error("null-target-without-boundary", edge=edge.get("edge_id"))
        if target is not None and resolution != "resolved":
            error("resolved-target-marked-otherwise", edge=edge.get("edge_id"))
        outgoing[source].append(edge)
        edge_pairs[(source, target)].append(edge)

    block_topology_evidence = defaultdict(list)
    if schema_version == 3:
        for edge in block_edges:
            if edge.get("candidate_edge"):
                continue
            edge_precision = (edge.get("precision", {}) or {}).get("topology", "exact")
            block_topology_evidence[edge.get("from")].append(edge_precision)
            if edge.get("to") is not None:
                block_topology_evidence[edge.get("to")].append(edge_precision)

    owners = {}
    for block in blocks:
        source_nodes = block.get("source_nodes", [])
        if not source_nodes:
            error("empty-basic-block", block=block.get("block_id"))
            continue
        if block.get("entry_node") != source_nodes[0]:
            error("wrong-block-entry", block=block.get("block_id"))
        if block.get("terminator_node") != source_nodes[-1]:
            error("wrong-block-terminator", block=block.get("block_id"))
        for node_id in source_nodes:
            if node_id not in node_map:
                error("block-contains-missing-node", block=block.get("block_id"), node=node_id)
            if node_id in owners:
                error("node-has-multiple-blocks", node=node_id)
            owners[node_id] = block.get("block_id")
        for source, target in zip(source_nodes, source_nodes[1:]):
            links = edge_pairs.get((source, target), [])
            if len(links) != 1 or links[0].get("kind") != "NEXT":
                error("nonlinear-block-interior", block=block.get("block_id"), node=source)
        if schema_version == 3:
            expected = {}
            members = [node_map[node_id] for node_id in source_nodes
                       if node_id in node_map]
            expected["topology"] = _merged_precision([
                (node.get("precision", {}) or {}).get("topology", "exact")
                for node in members
                if node.get("story_relevance", "unknown") in ("control", "unknown")
            ] + block_topology_evidence.get(block.get("block_id"), []))
            expected["state"] = _merged_precision([
                (node.get("precision", {}) or {}).get("state", "exact")
                for node in members
            ])
            expected["content"] = _merged_precision([
                (node.get("precision", {}) or {}).get("content", "exact")
                for node in members
                if node.get("story_relevance", "unknown") == "narrative"
            ])
            actual = block.get("precision", {}) or {}
            for dimension, value in expected.items():
                if actual.get(dimension, "exact") != value:
                    error("wrong-block-precision", block=block.get("block_id"), dimension=dimension)
        else:
            expected_precision = _merged_precision([
                node_map[node_id].get("effect_precision", node_map[node_id].get("precision", "exact"))
                for node_id in source_nodes if node_id in node_map
            ])
            if block.get("effect_precision", block.get("precision")) != expected_precision:
                error("wrong-block-precision", block=block.get("block_id"))
    for node_id in node_ids:
        if node_id not in owners:
            error("node-without-block", node=node_id)

    expected_projected_edges = set()
    for edge in edges:
        source_block = owners.get(edge.get("from"))
        target_block = owners.get(edge.get("to"))
        if source_block is None:
            continue
        terminator = block_map[source_block].get("terminator_node")
        if source_block != target_block or edge.get("to") is None or edge.get("from") == terminator:
            expected_projected_edges.add(edge.get("edge_id"))
    projected_sources = set()
    for edge in block_edges:
        if edge.get("from") not in block_map:
            error("missing-block-edge-source", edge=edge.get("edge_id"))
        if edge.get("to") is not None and edge.get("to") not in block_map:
            error("missing-block-edge-target", edge=edge.get("edge_id"))
        source_edge = edge.get("source_edge")
        if source_edge not in edge_map:
            error("missing-raw-source-edge", edge=edge.get("edge_id"))
        projected_sources.add(source_edge)
    if projected_sources != expected_projected_edges:
        error("raw-block-edge-set-mismatch",
              missing=len(expected_projected_edges - projected_sources),
              extra=len(projected_sources - expected_projected_edges))

    call_nodes = {node_id for node_id, node in node_map.items() if node.get("kind") == "Call"}
    site_calls = [site.get("call_node") for site in sites]
    if len(site_calls) != len(set(site_calls)):
        error("duplicate-return-site")
    for site in sites:
        if site.get("call_node") not in call_nodes:
            error("return-site-not-owned-by-call", node=site.get("call_node"))
        continuation = site.get("continuation")
        if continuation is not None and continuation not in node_map:
            error("missing-return-continuation", node=continuation)
    for node_id, node in node_map.items():
        if node.get("kind") == "Return" and outgoing.get(node_id):
            error("return-has-static-outgoing-edge", node=node_id)
        if node.get("terminal") == "end-of-chain" and outgoing.get(node_id):
            error("end-of-chain-has-outgoing-edge", node=node_id)

    label_index = icfg.get("indexes", {}).get("labels", {})
    for label, values in label_index.items():
        for node_id in values:
            node = node_map.get(node_id)
            if node is None or node.get("kind") != "Label" or node.get("engine_name") != label:
                error("invalid-label-index", label=label, node=node_id)

    unresolved = [edge for edge in edges if edge.get("target_resolution") == "unresolved"]
    if unresolved:
        warnings.append({"code": "unresolved-edge-targets", "count": len(unresolved)})
    fallback_nodes = [node for node in nodes if node.get("identity_fallback")]
    if fallback_nodes:
        warnings.append({
            "code": "identity-fallback-nodes",
            "count": len(fallback_nodes),
            "nodes": [node.get("node_id") for node in fallback_nodes[:100]],
        })
    ambiguous_nodes = [node for node in nodes if node.get("identity_ambiguous")]
    if ambiguous_nodes:
        warnings.append({
            "code": "ambiguous-node-identities",
            "count": len(ambiguous_nodes),
            "nodes": [node.get("node_id") for node in ambiguous_nodes[:100]],
        })
    unknown_nodes = [node for node in nodes if (
        node.get("effect_precision") == "unknown"
        or (schema_version == 3 and any(
            (effect.get("precision") == "unknown")
            for effect in node.get("effects", []) if isinstance(effect, dict)
        ))
    )]
    if unknown_nodes:
        warnings.append({"code": "unknown-node-effects", "count": len(unknown_nodes)})

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "nodes": len(nodes), "edges": len(edges), "blocks": len(blocks),
            "block_edges": len(block_edges), "return_sites": len(sites),
            "labels": len(label_index), "unresolved_edges": len(unresolved),
            "unknown_nodes": len(unknown_nodes),
            "node_kinds": dict(sorted(Counter(
                node.get("kind", "") for node in nodes
            ).items())),
        },
    }



def _legacy_count_by(records, key):
    result = {}
    for record in records:
        value = record.get(key, "")
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))

def validate_components(nodes, edges, return_sites, blocks, block_edges, node_blocks,
                 coverage):
    errors = []
    warnings = []
    node_ids = [node["node_id"] for node in nodes]
    node_set = set(node_ids)
    block_ids = [block["block_id"] for block in blocks]
    block_set = set(block_ids)
    edge_ids = [edge["edge_id"] for edge in edges]

    if len(node_ids) != len(node_set):
        errors.append("duplicate-node-id")
    if len(block_ids) != len(block_set):
        errors.append("duplicate-block-id")
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("duplicate-edge-id")

    dangling_edges = [
        edge["edge_id"] for edge in edges
        if edge["from"] not in node_set or
        (edge["to"] is not None and edge["to"] not in node_set)
    ]
    if dangling_edges:
        errors.append("dangling-edge-endpoint")

    owned = [node_id for block in blocks for node_id in block["source_nodes"]]
    ownership_counts = {}
    for node_id in owned:
        ownership_counts[node_id] = ownership_counts.get(node_id, 0) + 1
    missing_owners = sorted(node_set - set(owned))
    duplicate_owners = sorted(
        node_id for node_id, count in ownership_counts.items() if count != 1
    )
    if missing_owners:
        errors.append("node-without-basic-block")
    if duplicate_owners:
        errors.append("node-in-multiple-basic-blocks")

    raw_edge_set = set(edge_ids)
    invalid_block_edges = [
        edge["edge_id"] for edge in block_edges
        if edge["from"] not in block_set or
        (edge["to"] is not None and edge["to"] not in block_set) or
        edge.get("source_edge") not in raw_edge_set
    ]
    if invalid_block_edges:
        errors.append("invalid-basic-block-edge")

    block_records = dict((block["block_id"], block) for block in blocks)
    expected_block_sources = set()
    internal_sequence_errors = []
    edge_by_pair = {}
    for edge in edges:
        edge_by_pair.setdefault((edge["from"], edge["to"]), []).append(edge)
        source_block = node_blocks.get(edge["from"])
        target_block = node_blocks.get(edge["to"])
        if (source_block != target_block or edge["to"] is None or
                edge["from"] == block_records[source_block]["terminator_node"]):
            expected_block_sources.add(edge["edge_id"])
    actual_block_sources = set(edge.get("source_edge") for edge in block_edges)
    if expected_block_sources != actual_block_sources:
        errors.append("raw-and-basic-block-edge-mismatch")
    for block in blocks:
        source_nodes = block["source_nodes"]
        for source, target in zip(source_nodes, source_nodes[1:]):
            links = edge_by_pair.get((source, target), [])
            if len(links) != 1 or links[0]["kind"] != "NEXT":
                internal_sequence_errors.append(block["block_id"])
                break
    if internal_sequence_errors:
        errors.append("invalid-basic-block-internal-sequence")

    invalid_return_sites = [
        site for site in return_sites
        if site["call_node"] not in node_set or
        (site["continuation"] is not None and site["continuation"] not in node_set)
    ]
    if invalid_return_sites:
        errors.append("invalid-return-site")

    block_precision_mismatches = []
    records = dict((node["node_id"], node) for node in nodes)
    for block in blocks:
        expected = _merged_precision([
            records[node_id].get("precision", "exact")
            for node_id in block["source_nodes"]
        ])
        if block.get("precision") != expected:
            block_precision_mismatches.append(block["block_id"])
    if block_precision_mismatches:
        errors.append("basic-block-precision-mismatch")

    unresolved_edges = [
        {
            "edge_id": edge["edge_id"], "from": edge["from"],
            "kind": edge["kind"], "derivation": edge.get("derivation"),
            "external_target": edge.get("external_target"),
        }
        for edge in edges if edge.get("target_resolution") == "unresolved"
    ]
    external_edges = [
        edge["edge_id"] for edge in edges
        if edge.get("target_resolution") == "external"
    ]
    if unresolved_edges:
        warnings.append("unresolved-edge-targets")
    if external_edges:
        warnings.append("edges-leave-the-included-project-graph")
    if coverage.get("excluded_project_flow_nodes"):
        warnings.append("project-flow-nodes-excluded-by-inclusion-policy")

    unknown_reasons = {}
    for node in nodes:
        effect = node.get("unknown_effect")
        if not effect:
            continue
        reason = effect.get("reason", "unspecified")
        unknown_reasons[reason] = unknown_reasons.get(reason, 0) + 1

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "unique_node_ids": len(node_ids) == len(node_set),
            "unique_edge_ids": len(edge_ids) == len(set(edge_ids)),
            "unique_block_ids": len(block_ids) == len(block_set),
            "all_edge_endpoints_resolve": not dangling_edges,
            "every_node_has_one_block": not missing_owners and not duplicate_owners,
            "all_block_edges_trace_to_raw_edges": not invalid_block_edges,
            "raw_and_block_edges_are_equivalent": (
                expected_block_sources == actual_block_sources
            ),
            "block_internal_sequences_are_linear_next_edges": not internal_sequence_errors,
            "all_return_sites_resolve": not invalid_return_sites,
            "block_precision_matches_members": not block_precision_mismatches,
        },
        "counts": {
            "node_precision": _legacy_count_by(nodes, "precision"),
            "edge_precision": _legacy_count_by(edges, "precision"),
            "edge_derivation": _legacy_count_by(edges, "derivation"),
            "block_precision": _legacy_count_by(blocks, "precision"),
            "unknown_effect_reasons": dict(sorted(unknown_reasons.items())),
            "unresolved_edges": len(unresolved_edges),
            "external_edges": len(external_edges),
            "terminal_nodes": sum(1 for node in nodes if node.get("terminal")),
        },
        "unresolved_edges": unresolved_edges,
        "assumptions": [
            "screen-source-scan edges come from static .rpy text scanning",
            "literal-screen-argument edges infer labels from call-screen string arguments",
            "call-screen-return-model assumes the screen may return to the following node",
            "dynamic targets and unsupported node effects remain explicit unknown boundaries",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph")
    parser.add_argument("--output")
    args = parser.parse_args()
    with io.open(args.graph, "r", encoding="utf-8") as stream:
        graph = json.load(stream)
    result = validate(graph)
    if args.output:
        output = os.path.abspath(os.path.expanduser(args.output))
        parent = os.path.dirname(output)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(output, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
