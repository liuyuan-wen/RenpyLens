"""StoryGraph v3 codec and v2 compatibility projection."""

import copy
import io
import json
import re

from .expressions import parse_expression_ir
from .model import (BlockIR, CallSiteIR, EdgeIR, EdgeRef, EffectIR, ExprIR, GraphIR, NodeIR,
                    NodeRef, PrecisionSet, ScreenActionIR, ScreenIR, SourceLocation, SymbolRef,
                    merge_precision, stable_digest)
from .python_semantics import analyze_ir


_V3_NODE_ID = re.compile(r"^N_[0-9a-f]{32}$")
_V3_EDGE_ID = re.compile(r"^E_[0-9a-f]{32}$")
_V3_BLOCK_ID = re.compile(r"^B_[0-9a-f]{32}$")
_V3_BLOCK_EDGE_ID = re.compile(r"^BE_[0-9a-f]{32}$")


def _graph_id(value, legacy=None):
    value = str(value or "")
    if value.startswith("G_") and len(value) == 66:
        return value
    provenance = (legacy or {}).get("provenance", {}) if isinstance(legacy, dict) else {}
    digest = str(provenance.get("sha256", ""))
    if re.match(r"^[0-9a-fA-F]{64}$", digest):
        return "G_" + digest.lower()
    return stable_digest({"legacy_graph": legacy or {}, "graph_id": value}, "G_", 64)


def _location(value):
    location = SourceLocation.from_dict(value)
    if location.path.startswith("game/"):
        location.path = location.path[5:]
    if location.path.lower().endswith(".rpyc"):
        location.path = location.path[:-5] + ".rpy"
    return location


def _symbol(value, owner="", local_names=()):
    if isinstance(value, SymbolRef):
        return value
    if isinstance(value, dict):
        return SymbolRef.from_dict(value)
    text = str(value or "")
    if text == "*":
        return SymbolRef.unknown()
    if text.startswith("persistent."):
        return SymbolRef.persistent(text[11:])
    if text.startswith("local."):
        return SymbolRef.local(text[6:], owner)
    if text in set(local_names or ()):
        return SymbolRef.local(text, owner)
    if "." in text:
        base, member = text.rsplit(".", 1)
        return SymbolRef("attribute", base=_symbol(base, owner), member=member,
                         precision="unknown")
    return SymbolRef.store(text)


def _symbols(values, owner="", local_names=()):
    if isinstance(values, (str, bytes)):
        values = [values]
    return tuple(_symbol(value, owner, local_names) for value in (values or ()))


def _effect_from_write(write, owner="", local_names=()):
    write = write or {}
    expression = parse_expression_ir(write.get("expr", ""), local_names, owner)
    target = _symbol(write.get("var", ""), owner, local_names)
    precision = expression.precision if expression.precision != "exact" else target.precision
    return EffectIR("assign", target, write.get("op", "set"), expression,
                    expression.reads, (), precision, "", write.get("value_known", False),
                    write.get("value"))


def _effect_from_unknown(unknown, owner="", local_names=()):
    unknown = unknown or {}
    def may(value):
        if value == "*":
            return (SymbolRef.unknown(),)
        return _symbols(value, owner, local_names)
    expression = parse_expression_ir(unknown.get("source", ""), local_names, owner)
    return EffectIR("unknown", None, "", expression, may(unknown.get("may_read")),
                    may(unknown.get("may_write")), "unknown", unknown.get("reason", ""))


def _node_from_v2(record, graph_id, local_names=(), local_owner="", canonicalize=True):
    record = record or {}
    legacy_id = str(record.get("node_id", ""))
    structural_id = str(record.get("structural_id") or "")
    if not re.match(r"^S_[0-9a-f]{32}$", structural_id):
        structural_id = stable_digest({
            "version": 1, "legacy_node_id": legacy_id,
            "kind": record.get("kind", ""),
            "location": record.get("location", {}),
        }, "S_", 32)
    content_hash = str(record.get("content_hash") or stable_digest({
        "kind": record.get("kind", ""), "contents": record.get("contents", []),
        "source": record.get("source", ""), "location": record.get("location", {}),
    }, "C_", 64))
    node_id = legacy_id
    if canonicalize and not _V3_NODE_ID.match(node_id):
        node_id = "N_" + structural_id[2:]
    ref = NodeRef(graph_id, node_id, structural_id, content_hash)
    typed_statement = None
    if record.get("kind") == "Python" and record.get("source"):
        typed_statement = analyze_ir(record.get("source", ""), local_names, local_owner)
    if typed_statement is not None:
        effects = list(typed_statement.get("effects", ()))
    elif record.get("effects"):
        effects = [EffectIR.from_dict(item) for item in record.get("effects", [])]
    else:
        effects = [_effect_from_write(item, local_owner, local_names) for item in record.get("writes", [])]
    if record.get("unknown_effect") and not record.get("effects"):
        effects.append(_effect_from_unknown(record["unknown_effect"], local_owner, local_names))
    # A failed expression is an unknown read boundary in v3.  Keep the
    # historical empty v2 read list in ``extra['_v2']`` while exposing the
    # wildcard on the typed node and effect model.
    unknown_read = any(
        getattr(effect.expression, "precision", "") == "unknown"
        or any(getattr(item, "kind", "") == "unknown" for item in effect.may_read)
        for effect in effects
    )
    raw_precision = record.get("precision", "exact")
    if isinstance(raw_precision, dict):
        dimensions = PrecisionSet.from_dict(raw_precision)
        precision = dimensions.state
    else:
        precision = raw_precision
    if typed_statement is not None:
        reads = list(typed_statement.get("reads", ()))
        controls = list(typed_statement.get("control_reads", ()))
    else:
        reads = list(_symbols(record.get("reads"), local_owner, local_names))
        controls = list(_symbols(record.get("control_reads"), local_owner, local_names))
    if (unknown_read or precision == "unknown") and not any(
            item.kind == "unknown" for item in reads):
        reads.append(SymbolRef.unknown())
    relevance = _story_relevance(record)
    if not isinstance(raw_precision, dict):
        if relevance == "presentation":
            dimensions = PrecisionSet("exact", precision, "exact")
        else:
            dimensions = PrecisionSet(precision, precision, precision)
    known = set(("graph_id", "node_id", "kind", "engine_name", "location", "reads", "control_reads",
                 "writes", "unknown_effect", "precision", "effect_precision", "contents",
                 "structural_id", "content_hash"))
    extra = dict((key, copy.deepcopy(value)) for key, value in record.items() if key not in known)
    extra["_v2"] = copy.deepcopy(record)
    return NodeIR(ref, record.get("kind", ""), record.get("engine_name", ""),
                  _location(record.get("location")),
                  reads,
                  controls,
                  effects, dimensions, relevance, record.get("contents", []), extra)


def _story_relevance(record):
    kind = str(record.get("kind", ""))
    if kind in ("Say", "TranslateSay", "Menu"):
        return "narrative"
    if kind in ("If", "While", "Jump", "Call", "Return", "Label"):
        return "control"
    if kind == "Python":
        return "state"
    if kind in ("Scene", "Show", "Hide", "With", "Pass"):
        return "presentation"
    if kind == "UserStatement":
        source = str(record.get("source", "") or "").strip().lower()
        if source.startswith((
                "show screen", "hide screen", "play ", "stop ", "pause",
                "window show", "window hide", "window auto",
        )):
            return "presentation"
        return "unknown"
    if kind in ("Init", "Define", "Default", "Screen", "Style"):
        return "definition"
    return "unknown"


def _edge_from_v2(record, graph_id, source_node_id=None, target_node_id=None,
                  canonicalize=True, occurrence=0):
    record = record or {}
    source = record.get("from", "") if source_node_id is None else source_node_id
    target = record.get("to") if target_node_id is None else target_node_id
    legacy_edge_id = str(record.get("edge_id", ""))
    edge_id = legacy_edge_id
    if canonicalize and not _V3_EDGE_ID.match(edge_id):
        slot = record.get("choice_index", record.get("branch_index", ""))
        edge_id = stable_digest({
            "version": 1, "from": source, "to": target,
            "kind": record.get("kind", ""),
            "slot": "" if slot is None else str(slot),
            "occurrence": int(occurrence or 0),
        }, "E_", 32)
    ref = EdgeRef(graph_id, edge_id, source, target)
    precision = record.get("precision", "exact")
    dimensions = (PrecisionSet.from_dict(precision)
                  if isinstance(precision, dict)
                  else PrecisionSet(precision, precision, precision))
    # ``load_graph`` uses this compatibility converter for both v2 and v3.
    # Keep the typed CallSiteIR attached to a CALL_EDGE when a v3 document is
    # read back; otherwise query/slice consumers silently lose the stage-4
    # parameter effects after a dump/load cycle.
    raw_call = record.get("call")
    call_site = (CallSiteIR.from_dict(raw_call)
                 if isinstance(raw_call, dict) else raw_call)
    metadata = dict((key, copy.deepcopy(value)) for key, value in record.items()
                    if key not in ("graph_id", "edge_id", "from", "to", "kind", "precision", "topology_precision", "derivation", "call"))
    condition = record.get("condition")
    if condition is not None:
        condition_ir = parse_expression_ir(condition)
        metadata["condition_ir"] = condition_ir
        if condition_ir.precision == "unknown":
            dimensions = PrecisionSet("unknown", "unknown", "unknown")
            if not metadata.get("control_reads"):
                metadata["control_reads"] = [{
                    "kind": "unknown", "name": "*", "owner": "",
                    "base": None, "member": "", "index": None,
                    "precision": "unknown",
                }]
    metadata["_v2"] = copy.deepcopy(record)
    return EdgeIR(ref, record.get("kind", ""), dimensions,
                  record.get("derivation", ""), metadata, call_site)


def _block_from_v2(record, block_id=None, source_nodes=None, entry_node=None,
                   terminator_node=None, canonicalize=True):
    record = record or {}
    raw_precision = record.get("precision", "exact")
    dimensions = (PrecisionSet.from_dict(raw_precision)
                  if isinstance(raw_precision, dict)
                  else PrecisionSet(raw_precision, raw_precision, raw_precision))
    relevance_values = []
    if record.get("contents"):
        relevance_values.append("narrative")
    else:
        relevance_values.append("control")
    if record.get("writes") or record.get("effects"):
        relevance_values.append("state")
    relevance = tuple(sorted(set(relevance_values)))
    known = set(("block_id", "kind", "control_kind", "terminator_node", "entry_node", "location",
                 "source_nodes", "contents", "reads", "control_reads", "writes",
                 "unknown_effects", "precision", "effect_precision"))
    metadata = dict((key, copy.deepcopy(value)) for key, value in record.items() if key not in known)
    metadata["_v2"] = {
        "precision": raw_precision,
        "effect_precision": record.get("effect_precision", raw_precision),
        "writes": copy.deepcopy(record.get("writes", [])),
        "unknown_effects": copy.deepcopy(record.get("unknown_effects", [])),
    }
    legacy_block_id = str(record.get("block_id", ""))
    if block_id is None:
        block_id = legacy_block_id
    if canonicalize and not _V3_BLOCK_ID.match(str(block_id)):
        block_id = stable_digest({
            "version": 1, "legacy_block_id": legacy_block_id,
            "source_nodes": list(source_nodes if source_nodes is not None else record.get("source_nodes", [])),
            "control_kind": record.get("control_kind", ""),
        }, "B_", 32)
    return BlockIR(block_id, record.get("kind", ""), record.get("control_kind", ""),
                   record.get("entry_node", "") if entry_node is None else entry_node,
                   record.get("terminator_node", "") if terminator_node is None else terminator_node,
                   _location(record.get("location")),
                   source_nodes if source_nodes is not None else record.get("source_nodes", []),
                   record.get("contents", []), _symbols(record.get("reads")),
                   _symbols(record.get("control_reads")), [],
                   dimensions, relevance, metadata)


def _aggregate_block_precision(block, nodes, block_edges=()):
    members = [nodes[node_id] for node_id in block.source_nodes
               if node_id in nodes]
    topology = [node.precision.topology for node in members
                if node.story_relevance in ("control", "unknown")]
    state = [node.precision.state for node in members]
    content = [node.precision.content for node in members
               if node.story_relevance == "narrative"]
    for edge in block_edges:
        if edge.ref.source_node_id == block.block_id or edge.ref.target_node_id == block.block_id:
            if edge.metadata.get("candidate_edge"):
                continue
            topology.append(edge.precision.topology)
    block.precision = PrecisionSet(
        merge_precision(topology), merge_precision(state), merge_precision(content)
    )
    block.story_relevance = tuple(sorted(set(
        item for node in members for item in (node.story_relevance,)
    )))


def from_dict(graph):
    """Load either a v2 or v3 dictionary into a GraphIR container."""
    graph = graph or {}
    if isinstance(graph, GraphIR):
        return graph
    graph_id = graph.get("graph_id", "")
    if graph.get("schema_version") == 3:
        # A v3 document is already typed.  Re-running the v2 migration here
        # loses lexical owners and block effects because there is no live AST
        # available during loading.
        known = set(("status", "schema_version", "engine", "graph_id", "provenance",
                     "coverage", "audit", "entry_points", "model", "icfg",
                     "narrative_graph", "summary", "screens", "candidate_edges",
                     "compatibility"))
        return GraphIR(
            graph_id=graph_id, engine=graph.get("engine", ""),
            provenance=graph.get("provenance"), coverage=graph.get("coverage"),
            audit=graph.get("audit"), entry_points=graph.get("entry_points"),
            nodes=[NodeIR.from_dict(item) for item in graph.get("icfg", {}).get("nodes", [])],
            edges=[EdgeIR.from_dict(item) for item in graph.get("icfg", {}).get("edges", [])],
            return_sites=graph.get("icfg", {}).get("return_sites", []),
            blocks=[BlockIR.from_dict(item) for item in graph.get("narrative_graph", {}).get("blocks", [])],
            block_edges=[EdgeIR.from_dict(item) for item in graph.get("narrative_graph", {}).get("edges", [])],
            block_return_sites=graph.get("narrative_graph", {}).get("return_sites", []),
            indexes=graph.get("icfg", {}).get("indexes", {}), summary=graph.get("summary"),
            model=graph.get("model"),
            extra=dict((key, graph[key]) for key in graph if key not in known),
            legacy=graph.get("compatibility", {}).get("v2"),
            screens=[ScreenIR.from_dict(item) for item in graph.get("screens", [])],
            candidate_edges=[EdgeIR.from_dict(item) for item in graph.get("candidate_edges", [])],
        )
    # v2 has shorter, location-based identifiers.  Give the in-memory IR a
    # valid v3 identity while retaining the untouched document for
    # ``project_v2`` and old consumers.
    graph_id = _graph_id(graph_id, graph)
    raw_nodes = graph.get("icfg", {}).get("nodes", [])
    nodes = [_node_from_v2(item, graph_id) for item in raw_nodes]
    node_map = {}
    for index, item in enumerate(raw_nodes):
        if index < len(nodes):
            node_map[item.get("node_id", "")] = nodes[index].ref.node_id

    raw_edges = graph.get("icfg", {}).get("edges", [])
    edges = []
    edge_map = {}
    edge_occurrences = {}
    for item in raw_edges:
        source = node_map.get(item.get("from", ""), item.get("from", ""))
        target = (node_map.get(item.get("to"), item.get("to"))
                  if item.get("to") is not None else None)
        occurrence_key = (source, target, item.get("kind", ""),
                          item.get("choice_index", item.get("branch_index", "")))
        occurrence = edge_occurrences.get(occurrence_key, 0)
        edge_occurrences[occurrence_key] = occurrence + 1
        edge = _edge_from_v2(item, graph_id, source, target, occurrence=occurrence)
        if item.get("derivation") in ("screen-source-scan", "literal-screen-argument",
                                       "call-screen-return-model"):
            edge.metadata["candidate_edge"] = True
        edges.append(edge)
        edge_map[item.get("edge_id", "")] = edge.ref.edge_id

    raw_blocks = graph.get("narrative_graph", {}).get("blocks", [])
    blocks = []
    block_map = {}
    for item in raw_blocks:
        source_nodes = [node_map.get(value, value) for value in item.get("source_nodes", [])]
        entry = node_map.get(item.get("entry_node"), item.get("entry_node", ""))
        terminator = node_map.get(item.get("terminator_node"), item.get("terminator_node", ""))
        block = _block_from_v2(item, source_nodes=source_nodes,
                               entry_node=entry, terminator_node=terminator)
        blocks.append(block)
        block_map[item.get("block_id", "")] = block.block_id

    raw_block_edges = graph.get("narrative_graph", {}).get("edges", [])
    block_edges = []
    edges_by_id = dict((candidate.ref.edge_id, candidate) for candidate in edges)
    for item in raw_block_edges:
        source = block_map.get(item.get("from", ""), item.get("from", ""))
        target = (block_map.get(item.get("to"), item.get("to"))
                  if item.get("to") is not None else None)
        edge = _edge_from_v2(item, graph_id, source, target)
        # Block edges use their own namespace and therefore need a BE_ ID.
        if not _V3_BLOCK_EDGE_ID.match(edge.ref.edge_id):
            edge.ref = EdgeRef(graph_id, stable_digest({
                "version": 1, "from": source, "to": target,
                "kind": item.get("kind", ""),
                "source_edge": edge_map.get(item.get("source_edge"), item.get("source_edge")),
            }, "BE_", 32), source, target)
        edge.metadata["source_edge"] = edge_map.get(item.get("source_edge"), item.get("source_edge"))
        source_edge = edges_by_id.get(edge.metadata["source_edge"])
        if source_edge is not None and source_edge.metadata.get("candidate_edge"):
            edge.metadata["candidate_edge"] = True
        block_edges.append(edge)

    node_by_id = dict((node.ref.node_id, node) for node in nodes)
    for block in blocks:
        _aggregate_block_precision(block, node_by_id, block_edges)

    return_sites = [
        dict(site,
             call_node=node_map.get(site.get("call_node"), site.get("call_node")),
             continuation=(node_map.get(site.get("continuation"), site.get("continuation"))
                           if site.get("continuation") is not None else None))
        for site in graph.get("icfg", {}).get("return_sites", [])
    ]
    block_return_sites = [
        dict(site,
             call_block=block_map.get(site.get("call_block"), site.get("call_block")),
             continuation_block=block_map.get(site.get("continuation_block"), site.get("continuation_block")),
             source_call_node=node_map.get(site.get("source_call_node"), site.get("source_call_node")))
        for site in graph.get("narrative_graph", {}).get("return_sites", [])
    ]
    entry_points = dict((name, node_map.get(value, value))
                        for name, value in (graph.get("entry_points", {}) or {}).items())
    indexes = graph.get("icfg", {}).get("indexes", {})
    def remap_index(value):
        if isinstance(value, dict):
            return dict((key, remap_index(item)) for key, item in value.items())
        if isinstance(value, list):
            return [remap_index(item) for item in value]
        if value in node_map:
            return node_map[value]
        if value in edge_map:
            return edge_map[value]
        return value
    return GraphIR(graph_id, graph.get("engine", ""), graph.get("provenance"),
                   graph.get("coverage"), graph.get("audit"), entry_points,
                   nodes, edges, return_sites, blocks, block_edges, block_return_sites,
                   remap_index(indexes), graph.get("summary"), graph.get("model"),
                   graph, graph)


def _v2_node(node):
    result = dict(node.extra.get("_v2", {}))
    result.update({
        "node_id": result.get("node_id", node.ref.node_id),
        "kind": node.kind,
        "engine_name": node.engine_name,
        "location": {"filename": node.location.path, "line": node.location.line},
        "contents": list(node.contents),
    })
    for key, value in node.extra.items():
        if key != "_v2":
            result[key] = copy.deepcopy(value)
    return result


def _v2_edge(edge):
    result = dict(edge.metadata.get("_v2", {}))
    result.update({"edge_id": edge.ref.edge_id, "from": edge.ref.source_node_id,
                   "to": edge.ref.target_node_id, "kind": edge.kind,
                   "derivation": edge.derivation})
    precision = result.get("precision", edge.precision.topology)
    result["precision"] = precision
    result["topology_precision"] = result.get("topology_precision", precision)
    for key, value in edge.metadata.items():
        if key != "_v2":
            result[key] = copy.deepcopy(value)
    return result


def project_v2(graph):
    """Return the legacy v2 dictionary view used by existing tools."""
    graph = from_dict(graph)
    if isinstance(graph.legacy, dict) and graph.legacy.get("schema_version") == 2:
        return copy.deepcopy(graph.legacy)
    result = {
        "status": "completed", "engine": graph.engine, "error": None,
        "schema_version": 2, "provenance": copy.deepcopy(graph.provenance),
        "coverage": copy.deepcopy(graph.coverage), "audit": copy.deepcopy(graph.audit),
        "entry_points": copy.deepcopy(graph.entry_points), "model": copy.deepcopy(graph.model),
        "icfg": {
            "nodes": [_v2_node(node) for node in graph.nodes],
            "edges": [_v2_edge(edge) for edge in graph.edges],
            "return_sites": copy.deepcopy(list(graph.return_sites)),
            "indexes": copy.deepcopy(graph.indexes),
        },
        "narrative_graph": {
            "blocks": [dict(block.metadata.get("_v2", {}), block_id=block.block_id,
                              kind=block.kind, control_kind=block.control_kind,
                              terminator_node=block.terminator_node, entry_node=block.entry_node,
                              location={"filename": block.location.path, "line": block.location.line},
                              source_nodes=list(block.source_nodes), contents=list(block.contents),
                              reads=[item.display() for item in block.reads],
                              control_reads=[item.display() for item in block.control_reads])
                       for block in graph.blocks],
            "edges": [_v2_edge(edge) for edge in graph.block_edges],
            "return_sites": copy.deepcopy(list(graph.block_return_sites)),
        },
        "summary": copy.deepcopy(graph.summary),
    }
    return result


def semantic_view(graph):
    """Return the legacy algorithm shape without discarding v3 identities.

    Query, slice, and solver predate typed IR and currently consume strings.
    This adapter preserves their input contract while retaining canonical v3
    node, edge, and block identifiers instead of returning the archived v2
    compatibility document.
    """
    graph = from_dict(graph)
    if not graph.graph_id:
        return project_v2(graph)
    value = graph.to_dict()
    result = copy.deepcopy(value)
    for node in result.get("icfg", {}).get("nodes", []):
        location = node.get("location", {}) or {}
        if "filename" not in location:
            location["filename"] = location.get("path", "")
        node["location"] = location
        node["reads"] = [SymbolRef.from_dict(item).display() if isinstance(item, dict) else str(item)
                         for item in node.get("reads", [])]
        node["control_reads"] = [SymbolRef.from_dict(item).display() if isinstance(item, dict) else str(item)
                                 for item in node.get("control_reads", [])]
        precision = node.get("precision", {}) or {}
        if isinstance(precision, (str, bytes)):
            precision = PrecisionSet.from_dict(precision).to_dict()
        node["precision"] = precision.get("topology", "exact")
        node["effect_precision"] = precision.get("state", "exact")
        writes = []
        unknown = []
        for effect in node.get("effects", []):
            target = effect.get("target") if isinstance(effect, dict) else None
            if isinstance(target, dict) and effect.get("kind") in ("assign", "bind_parameter"):
                expression = effect.get("expression") or {}
                writes.append({"var": SymbolRef.from_dict(target).display(),
                               "op": effect.get("operator", "set"),
                               "expr": expression.get("source", "") if isinstance(expression, dict) else "",
                               "value_known": bool(effect.get("value_known", False))})
            if isinstance(effect, dict) and effect.get("precision") == "unknown":
                unknown.append(effect)
        node["writes"] = writes
        if unknown:
            node["unknown_effect"] = {"may_read": "*", "may_write": "*",
                                      "reason": unknown[0].get("reason", "semantic-unknown")}
    for edge in result.get("icfg", {}).get("edges", []):
        precision = edge.get("precision", {}) or {}
        edge["precision"] = (precision if isinstance(precision, (str, bytes))
                             else precision.get("topology", "exact"))
        edge["topology_precision"] = edge["precision"]
    for block in result.get("narrative_graph", {}).get("blocks", []):
        location = block.get("location", {}) or {}
        if "filename" not in location:
            location["filename"] = location.get("path", "")
        block["location"] = location
        block["reads"] = [SymbolRef.from_dict(item).display() if isinstance(item, dict) else str(item)
                          for item in block.get("reads", [])]
        block["control_reads"] = [SymbolRef.from_dict(item).display() if isinstance(item, dict) else str(item)
                                  for item in block.get("control_reads", [])]
        precision = block.get("precision", {}) or {}
        if isinstance(precision, (str, bytes)):
            precision = PrecisionSet.from_dict(precision).to_dict()
        block["precision"] = precision.get("topology", "exact")
        block["effect_precision"] = precision.get("state", "exact")
    for edge in result.get("narrative_graph", {}).get("edges", []):
        precision = edge.get("precision", {}) or {}
        edge["precision"] = (precision if isinstance(precision, (str, bytes))
                              else precision.get("topology", "exact"))
        edge["topology_precision"] = edge["precision"]
    return result


def dump_graph(graph, path=None, schema_version=3):
    graph = from_dict(graph)
    value = graph.to_dict() if schema_version == 3 else project_v2(graph)
    if path is not None:
        with io.open(path, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    return value


def load_graph(source):
    """Load a v2/v3 graph from a path, file-like object, or dictionary."""
    if isinstance(source, GraphIR):
        return source
    if isinstance(source, dict):
        return from_dict(source)
    if hasattr(source, "read"):
        return from_dict(json.load(source))
    with io.open(source, "r", encoding="utf-8") as stream:
        return from_dict(json.load(stream))
