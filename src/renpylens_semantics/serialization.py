"""StoryGraph schema v3 codec."""

import copy
import gzip
import json
from .model import (BlockIR, EdgeIR, GraphIR, NodeIR, PrecisionSet, ScreenIR, SymbolRef)


try:
    _PATH_TEXT = unicode
except NameError:
    _PATH_TEXT = str


def write_json(value, path):
    """Write compact UTF-8 JSON; .gz paths use maximum gzip compression."""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as stream:
        if _PATH_TEXT(path).lower().endswith(".gz"):
            with gzip.GzipFile(filename="", mode="wb", fileobj=stream,
                               compresslevel=9, mtime=0) as compressed:
                compressed.write(payload)
        else:
            stream.write(payload)


def read_json(path):
    opener = gzip.open if _PATH_TEXT(path).lower().endswith(".gz") else open
    with opener(path, "rb") as stream:
        return json.load(stream)


def from_dict(graph):
    """Load a schema v3 dictionary into a GraphIR container."""
    graph = graph or {}
    if isinstance(graph, GraphIR):
        return graph
    graph_id = graph.get("graph_id", "")
    if graph.get("schema_version") != 3:
        raise ValueError("Only StoryGraph schema v3 is supported.")
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
        screens=[ScreenIR.from_dict(item) for item in graph.get("screens", [])],
        candidate_edges=[EdgeIR.from_dict(item) for item in graph.get("candidate_edges", [])],
    )


def semantic_view(graph):
    """Adapt typed v3 symbols for query, slice, and solver algorithms."""
    graph = from_dict(graph)
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


def dump_graph(graph, path=None):
    graph = from_dict(graph)
    value = graph.to_dict()
    if path is not None:
        write_json(value, path)
    return value


def load_graph(source):
    """Load a schema v3 graph from a path, file-like object, or dictionary."""
    if isinstance(source, GraphIR):
        return source
    if isinstance(source, dict):
        return from_dict(source)
    if hasattr(source, "read"):
        return from_dict(json.load(source))
    return from_dict(read_json(source))
