"""Ren'Py-side semantic ICFG and narrative basic-block graph extractor.

This is the stage-1 home of the existing StoryGraph v2 implementation.  The
runtime adapter remains a thin command registration wrapper in ``graph/``.
"""

import hashlib
import io
import json
import os
import re
import time
import traceback

import renpy

from .audit import validate_components as _kernel_validate_components
from .call_semantics import label_parameter_names as _kernel_parameter_names
from .expressions import content_reads as _kernel_content_reads
from .expressions import expression_reads as _kernel_expression_reads
from .model import merge_precision as _kernel_merge_precision
from .python_semantics import analyze as _kernel_python_semantics
from .renpy_adapter import all_nodes as _kernel_all_nodes
from .renpy_adapter import lookup as _kernel_lookup
from .screen_semantics import call_screen as _kernel_call_screen
from .screen_semantics import screen_jump_actions as _kernel_screen_jump_actions


_FLOW_NODE_KINDS = set((
    "Label", "Say", "TranslateSay", "Menu", "If", "While", "Jump",
    "Call", "Return", "Python", "UserStatement", "Scene", "Show", "Hide",
    "With", "Pass",
))


def _single_line(value):
    return str(value or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _node_ids(nodes):
    def identity(node):
        kind = type(node).__name__
        parts = [
            str(getattr(node, "filename", "")).replace("\\", "/"),
            str(int(getattr(node, "linenumber", 0) or 0)),
            kind,
            str(getattr(node, "name", "")),
        ]
        if kind == "Python":
            parts.append(str(getattr(getattr(node, "code", None), "source", "")))
        elif kind in ("Say", "TranslateSay"):
            parts.extend((str(getattr(node, "who", "") or ""),
                          str(getattr(node, "what", "") or "")))
        elif kind == "UserStatement":
            parts.append(str(getattr(node, "line", "") or ""))
        elif kind in ("Jump", "Call"):
            parts.extend((str(getattr(node, "target" if kind == "Jump" else "label", "")),
                          str(bool(getattr(node, "expression", False)))))
        elif kind == "Return":
            parts.append(str(getattr(node, "expression", "") or ""))
        return "\x1f".join(parts)

    groups = {}
    for node in nodes:
        groups.setdefault(identity(node), []).append(node)
    result = {}
    for payload in sorted(groups):
        digest = hashlib.sha1(payload.encode("utf-8", "surrogatepass")).hexdigest()[:20]
        group = groups[payload]
        for index, node in enumerate(group):
            suffix = "_%d" % (index + 1) if len(group) > 1 else ""
            result[id(node)] = "N_" + digest + suffix
    return result


def _script_manifest():
    files = []
    digest = hashlib.sha256()
    root = renpy.config.gamedir
    for directory, _dirs, filenames in os.walk(root):
        for filename in filenames:
            lowered = filename.lower()
            if not lowered.endswith((".rpy", ".rpyc")):
                continue
            if lowered.startswith("zz_renpylens_"):
                continue
            path = os.path.join(directory, filename)
            relative = os.path.relpath(path, root).replace("\\", "/")
            file_digest = hashlib.sha256()
            try:
                with open(path, "rb") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        file_digest.update(chunk)
            except OSError:
                continue
            value = file_digest.hexdigest()
            size = os.path.getsize(path)
            files.append({"path": relative, "size": size, "sha256": value})
    files.sort(key=lambda item: item["path"].lower())
    for item in files:
        digest.update(item["path"].encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "script_files": files}


def _static_target(node, attribute):
    if getattr(node, "expression", False):
        return None
    target = str(getattr(node, attribute, ""))
    if target.startswith("."):
        target = str(getattr(node, "global_label", "")) + target
    return target


def _script_scope(filename):
    normalized = str(filename or "").replace("\\", "/")
    lowered = normalized.lower()
    if "zz_renpylens_" in lowered:
        return "instrumentation"
    if lowered.startswith("renpy/common/"):
        return "engine"
    if "/tl/" in lowered or lowered.startswith("tl/"):
        return "translation"
    return "project"


def _node_semantics(node, node_id):
    kind = type(node).__name__
    result = {
        "node_id": node_id,
        "kind": kind,
        "engine_name": str(getattr(node, "name", "")),
        "location": {
            "filename": str(getattr(node, "filename", "")),
            "line": getattr(node, "linenumber", None),
        },
        "reads": [],
        "control_reads": [],
        "writes": [],
        "precision": "exact",
        "contents": [],
    }
    if kind == "Python":
        source = str(getattr(getattr(node, "code", None), "source", ""))
        result.update(_python_semantics(source))
        result["source"] = source
    elif kind in ("Say", "TranslateSay"):
        who = str(getattr(node, "who", "") or "")
        what = str(getattr(node, "what", "") or "")
        result["contents"].append({
            "kind": "dialogue",
            "who": _single_line(who), "what": _single_line(what),
            "raw_who": who, "raw_what": what,
        })
        result["reads"] = sorted(set(_content_reads(who) + _content_reads(what)))
    elif kind == "Return":
        expression = str(getattr(node, "expression", "") or "")
        result["reads"] = _expression_reads(expression)
        result["writes"] = [{
                "var": "_return", "op": "set", "expr": expression,
                "value_known": not bool(expression), "value": None,
            }]
    elif kind == "If":
        result["control_reads"] = sorted(set(
            name
            for condition, _block in (getattr(node, "entries", None) or [])
            for name in _expression_reads(str(condition))
        ))
    elif kind == "Menu":
        conditions = [
            str(item[1]) for item in (getattr(node, "items", None) or [])
            if item and len(item) >= 2 and item[1] not in (None, True)
        ]
        result["control_reads"] = sorted(set(
            name for condition in conditions for name in _expression_reads(condition)
        ))
        set_name = getattr(node, "set", None)
        if isinstance(set_name, str) and re.match(r"^[A-Za-z_]\w*$", set_name):
            result["reads"] = [set_name]
    elif kind == "While":
        result["control_reads"] = _expression_reads(
            str(getattr(node, "condition", "True"))
        )
    elif kind in ("Jump", "Call") and getattr(node, "expression", False):
        expression = str(getattr(node, "target" if kind == "Jump" else "label", ""))
        result["control_reads"] = _expression_reads(expression)
        result["precision"] = "unknown"
        result["unknown_effect"] = {
            "may_read": result["control_reads"] or "*",
            "may_write": [],
            "source": expression,
            "reason": "dynamic-" + kind.lower() + "-target",
        }
    elif kind == "UserStatement":
        source = str(getattr(node, "line", "") or "")
        result["source"] = source
        if re.match(r"^\s*(?:show\s+screen|hide\s+screen|play\s+|stop\s+|pause(?:\s|$)|window\s+(?:show|hide))", source):
            result["precision"] = "conservative"
            result["unknown_effect"] = {
                "may_read": "*", "may_write": [], "source": source,
                "reason": "presentation-statement",
            }
        else:
            # Custom statements and call-screen callbacks can execute game
            # code, so unknown store writes remain explicit.
            result["precision"] = "unknown"
            result["unknown_effect"] = {
                "may_read": "*", "may_write": "*", "source": source,
                "reason": "user-statement",
            }
    elif kind == "Call":
        if getattr(node, "arguments", None) is not None:
            result["precision"] = "conservative"
            result["unknown_effect"] = {"may_read": "*", "may_write": [],
                                        "reason": "call-argument-binding"}
    elif kind == "Label":
        parameters = getattr(node, "parameters", None)
        if parameters is not None:
            names = _parameter_names(parameters)
            result["precision"] = "unknown"
            result["unknown_effect"] = {"may_read": [], "may_write": names or "*",
                                        "reason": "label-parameter-binding"}
    elif kind not in ("Show", "Hide", "With", "Scene", "Pass", "Init", "Jump"):
        result["precision"] = "unknown"
        result["unknown_effect"] = {
            "may_read": "*", "may_write": "*",
            "reason": "unsupported-node-kind", "kind": kind,
        }
    result["effect_precision"] = result["precision"]
    return result


def _build_icfg(nodes, ids, screen_actions):
    node_records = [_node_semantics(node, ids[id(node)]) for node in nodes]
    records = dict((node["node_id"], node) for node in node_records)
    edges = []
    return_sites = []
    edge_keys = set()

    def mark_terminal(node, reason):
        records[ids[id(node)]]["terminal"] = reason

    def add(source, target, kind, **metadata):
        source_id = ids.get(id(source))
        target_id = ids.get(id(target)) if target is not None else None
        key = (source_id, target_id, kind, json.dumps(metadata, sort_keys=True))
        if key in edge_keys:
            return
        edge_keys.add(key)
        identity = json.dumps(
            [source_id, target_id, kind, metadata],
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        record = {
            "edge_id": "E_" + hashlib.sha1(
                identity.encode("utf-8", "surrogatepass")
            ).hexdigest()[:20],
            "from": source_id,
            "to": target_id,
            "kind": kind,
            "precision": "exact" if target_id is not None else "unknown",
            "derivation": "renpy-ast",
            "target_resolution": "resolved" if target_id is not None else "unresolved",
        }
        record.update(metadata)
        record["topology_precision"] = record["precision"]
        edges.append(record)

    for node in nodes:
        kind = type(node).__name__
        if kind == "Call":
            continuation = getattr(node, "next", None)
            return_sites.append({
                "kind": "RETURN_SITE",
                "call_node": ids[id(node)],
                "continuation": ids.get(id(continuation)),
            })
            label = _static_target(node, "label")
            target = _lookup(label) if label is not None else None
            add(node, target, "CALL_EDGE", label=label or "", pushes_return_site=True)
            continue
        if kind == "Return":
            continue
        if kind == "Jump":
            label = _static_target(node, "target")
            target = _lookup(label) if label is not None else None
            add(node, target, "JUMP_EDGE", label=label or "")
            continue
        if kind == "Menu":
            conditions = []
            set_name = getattr(node, "set", None)
            for index, item in enumerate(getattr(node, "items", None) or []):
                if not item or len(item) < 3:
                    continue
                if not item[2]:
                    if item[0]:
                        records[ids[id(node)]]["contents"].append({
                            "kind": "menu_caption",
                            "text": _single_line(item[0]),
                            "raw_text": str(item[0]),
                        })
                    continue
                condition = str(item[1]) if item[1] not in (None, True) else "True"
                conditions.append(condition)
                metadata = {
                    "choice_index": index,
                    "choice": _single_line(item[0]),
                    "raw_choice": str(item[0] or ""),
                    "condition": condition,
                    "control_reads": _expression_reads(condition),
                }
                if isinstance(set_name, str) and re.match(r"^[A-Za-z_]\w*$", set_name):
                    metadata["menu_set"] = set_name
                    metadata["writes"] = [{
                        "var": set_name,
                        "op": "menu_set_add",
                        "expr": repr(str(item[0] or "")),
                        "value_known": True,
                        "value": str(item[0] or ""),
                    }]
                add(node, item[2][0], "MENU_CHOICE", **metadata)
            continuation = getattr(node, "next", None)
            if continuation is not None:
                add(
                    node, continuation, "MENU_FALLTHROUGH",
                    condition="no-visible-choice", control_reads=sorted(set(
                        name for condition in conditions for name in _expression_reads(condition)
                    )), precision="conservative",
                )
            else:
                mark_terminal(node, "menu-without-fallthrough")
            continue
        if kind == "If":
            conditions = []
            for index, (condition, block) in enumerate(getattr(node, "entries", None) or []):
                source = str(condition)
                guard = " and ".join(["not (%s)" % earlier for earlier in conditions] + ["(%s)" % source])
                conditions.append(source)
                add(
                    node, block[0] if block else getattr(node, "next", None), "IF_BRANCH",
                    branch_index=index, condition=guard,
                    control_reads=_expression_reads(guard),
                )
            if not any(item.strip() in ("True", "else") for item in conditions):
                continuation = getattr(node, "next", None)
                if continuation is not None:
                    add(
                        node, continuation, "IF_FALLTHROUGH",
                        condition=" and ".join("not (%s)" % item for item in conditions) or "True",
                        control_reads=sorted(set(
                            name for condition in conditions for name in _expression_reads(condition)
                        )),
                    )
                else:
                    mark_terminal(node, "if-without-fallthrough")
            continue
        if kind == "While":
            condition = str(getattr(node, "condition", "True"))
            block = getattr(node, "block", None) or []
            add(node, block[0] if block else node, "WHILE_BODY", condition=condition,
                control_reads=_expression_reads(condition))
            continuation = getattr(node, "next", None)
            if continuation is not None:
                add(node, continuation, "WHILE_EXIT",
                    condition="not (%s)" % condition, control_reads=_expression_reads(condition))
            else:
                mark_terminal(node, "while-without-exit")
            continue
        if kind == "UserStatement":
            screen_name, argument_labels = _call_screen(node)
            if screen_name is not None:
                for label in argument_labels:
                    add(
                        node, _lookup(label), "SCREEN_ARGUMENT_JUMP",
                        screen=screen_name, label=label,
                        condition="selected screen argument",
                        control_reads=[], precision="conservative",
                        derivation="literal-screen-argument",
                    )
                for action in screen_actions.get(screen_name, []):
                    add(
                        node, _lookup(action["label"]),
                        "SCREEN_" + action["action"] + "_EDGE",
                        screen=screen_name,
                        label=action["label"],
                        condition=action["condition"],
                        control_reads=_expression_reads(action["condition"]),
                        precision="conservative",
                        derivation="screen-source-scan",
                    )
                continuation = getattr(node, "next", None)
                if continuation is not None:
                    add(
                        node, continuation, "SCREEN_RETURN_EDGE",
                        screen=screen_name, condition="screen returns",
                        control_reads=[], precision="conservative",
                        derivation="call-screen-return-model",
                    )
                else:
                    mark_terminal(node, "call-screen-without-continuation")
                continue
        continuation = getattr(node, "next", None)
        if continuation is not None:
            add(node, continuation, "NEXT")
        else:
            mark_terminal(node, "end-of-chain")

    return node_records, edges, return_sites


def _game_reachable(node_records, edges, return_sites, explicit_roots=()):
    records = dict((node["node_id"], node) for node in node_records)
    seeds = []
    game_label_seeds = []
    project_flow_seeds = []
    for node in node_records:
        filename = node["location"]["filename"].replace("\\", "/")
        if _script_scope(filename) != "project":
            continue
        if node["kind"] == "Label":
            game_label_seeds.append(node["node_id"])
        if node["kind"] in _FLOW_NODE_KINDS:
            seeds.append(node["node_id"])
            project_flow_seeds.append(node["node_id"])
    for node_id in explicit_roots:
        if node_id is not None and node_id not in seeds:
            seeds.append(node_id)

    adjacency = {}
    for edge in edges:
        if edge["to"] is not None:
            adjacency.setdefault(edge["from"], []).append(edge["to"])
    for site in return_sites:
        if site["continuation"] is not None:
            adjacency.setdefault(site["call_node"], []).append(site["continuation"])

    def walk(roots):
        visited = set(item for item in roots if item is not None)
        pending = list(visited)
        while pending:
            source = pending.pop()
            for target in adjacency.get(source, []):
                if target not in visited:
                    visited.add(target)
                    pending.append(target)
        return visited

    reachable = walk(seeds)
    entry_reachable = walk(explicit_roots)

    filtered_nodes = [node for node in node_records if node["node_id"] in reachable]
    filtered_edges = []
    for edge in edges:
        if edge["from"] not in reachable:
            continue
        if edge["to"] is not None and edge["to"] not in reachable:
            external = records.get(edge["to"])
            edge = dict(edge)
            edge["external_target"] = {
                "node_id": edge["to"],
                "kind": external["kind"] if external else "",
                "location": external["location"] if external else {},
            }
            edge["to"] = None
            edge["precision"] = "external"
            edge["topology_precision"] = "external"
            edge["target_resolution"] = "external"
        filtered_edges.append(edge)
    filtered_return_sites = [
        site for site in return_sites if site["call_node"] in reachable
    ]
    excluded = [node for node in node_records if node["node_id"] not in reachable]
    excluded_by_kind = {}
    excluded_by_file = {}
    included_by_scope = {}
    excluded_by_scope = {}
    excluded_project_flow_nodes = 0
    excluded_project_flow = []
    for node in filtered_nodes:
        scope = _script_scope(node["location"]["filename"])
        included_by_scope[scope] = included_by_scope.get(scope, 0) + 1
    for node in excluded:
        kind = node["kind"]
        filename = node["location"]["filename"].replace("\\", "/") or "<unknown>"
        excluded_by_kind[kind] = excluded_by_kind.get(kind, 0) + 1
        excluded_by_file[filename] = excluded_by_file.get(filename, 0) + 1
        scope = _script_scope(filename)
        excluded_by_scope[scope] = excluded_by_scope.get(scope, 0) + 1
        if scope == "project" and kind in _FLOW_NODE_KINDS:
            excluded_project_flow_nodes += 1
            excluded_project_flow.append({
                "node_id": node["node_id"], "kind": kind,
                "engine_name": node.get("engine_name", ""),
                "location": node["location"],
            })
    coverage = {
        "policy": "all project flow nodes plus their reachable nodes",
        "discovered_nodes": len(node_records),
        "included_nodes": len(filtered_nodes),
        "excluded_nodes": len(excluded),
        "game_label_roots": len(game_label_seeds),
        "project_flow_roots": len(project_flow_seeds),
        "explicit_roots": len(set(item for item in explicit_roots if item is not None)),
        "entry_reachable_nodes": len(entry_reachable & reachable),
        "included_not_entry_reachable": len(reachable - entry_reachable),
        "included_by_scope": dict(sorted(included_by_scope.items())),
        "excluded_by_scope": dict(sorted(excluded_by_scope.items())),
        "excluded_project_flow_nodes": excluded_project_flow_nodes,
        "excluded_project_flow": excluded_project_flow,
        "excluded_by_kind": dict(sorted(excluded_by_kind.items())),
        "excluded_by_file": dict(sorted(excluded_by_file.items())),
    }
    return filtered_nodes, filtered_edges, filtered_return_sites, coverage


def _build_narrative_blocks(node_records, edges, return_sites=()):
    records = dict((node["node_id"], node) for node in node_records)
    outgoing = {}
    incoming = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge)
        if edge["to"] is None:
            continue
        incoming.setdefault(edge["to"], []).append(edge)
    leaders = set(
        site["continuation"] for site in return_sites
        if site.get("continuation") is not None
    )
    boundaries = set((
        "Menu", "If", "While", "Jump", "Call", "Return", "Label",
    ))
    for node_id, record in records.items():
        if record["kind"] in boundaries or len(incoming.get(node_id, [])) != 1:
            leaders.add(node_id)
    for edge in edges:
        if edge["to"] is not None and edge["kind"] != "NEXT":
            leaders.add(edge["to"])
    assigned = {}
    blocks = []

    ordered_starts = sorted(leaders) + sorted(set(records) - leaders)
    for node_id in ordered_starts:
        if node_id in assigned:
            continue
        source_nodes = []
        current = node_id
        block_id = "B" + node_id[1:]
        while current is not None and current not in assigned:
            if source_nodes and current in leaders:
                break
            record = records[current]
            source_nodes.append(current)
            assigned[current] = block_id
            links = outgoing.get(current, [])
            if record["kind"] in boundaries or len(links) != 1 or links[0]["kind"] != "NEXT":
                break
            successor = links[0]["to"]
            if (successor is None or len(incoming.get(successor, [])) != 1
                    or successor in leaders):
                break
            current = successor

        contents = []
        reads = set()
        control_reads = set()
        writes = []
        unknown_effects = []
        precisions = []
        for raw_id in source_nodes:
            record = records[raw_id]
            contents.extend(record.get("contents", []))
            reads.update(record.get("reads", []))
            control_reads.update(record.get("control_reads", []))
            writes.extend(record.get("writes", []))
            if "unknown_effect" in record:
                unknown_effects.append({
                    "source_node": raw_id,
                    "effect": record["unknown_effect"],
                })
            precisions.append(record.get("precision", "exact"))
        precision = _merge_precision(precisions)
        terminator = records[source_nodes[-1]]
        terminator_edges = outgoing.get(source_nodes[-1], [])
        has_control_terminator = (
            terminator["kind"] in boundaries
            or len(terminator_edges) != 1
            or (terminator_edges and terminator_edges[0]["kind"] != "NEXT")
        )
        blocks.append({
            "block_id": block_id,
            "kind": "story_control_block" if has_control_terminator and contents else "control" if has_control_terminator else "story_block",
            "control_kind": terminator["kind"] if has_control_terminator else "",
            "terminator_node": source_nodes[-1],
            "entry_node": source_nodes[0],
            "location": records[source_nodes[0]]["location"],
            "source_nodes": source_nodes,
            "contents": contents,
            "reads": sorted(reads),
            "control_reads": sorted(control_reads),
            "writes": writes,
            "unknown_effects": unknown_effects,
            "precision": precision,
            "effect_precision": precision,
        })

    block_edges = []
    block_records = dict((block["block_id"], block) for block in blocks)
    seen = set()
    for edge in edges:
        source = assigned.get(edge["from"])
        target = assigned.get(edge["to"])
        if source is None:
            continue
        if (source == target and edge["kind"] == "NEXT" and
                edge["from"] != block_records[source]["terminator_node"]):
            continue
        metadata = dict((key, value) for key, value in edge.items()
                        if key not in ("edge_id", "from", "to"))
        key = (source, target, edge["kind"], json.dumps(metadata, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        identity = json.dumps(
            [source, target, edge["kind"], edge["edge_id"], metadata],
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        block_edges.append(dict({
            "edge_id": "BE_" + hashlib.sha1(
                identity.encode("utf-8", "surrogatepass")
            ).hexdigest()[:20],
            "from": source,
            "to": target,
            "source_edge": edge["edge_id"],
        }, **metadata))
    return blocks, block_edges, assigned


def _variable_indexes(node_records, edges):
    indexes = {
        "reads": {}, "writes": {}, "controls": {},
        "edge_writes": {}, "edge_controls": {},
        "labels": {},
    }
    unknown = []
    unknown_writes = []
    for node in node_records:
        node_id = node["node_id"]
        for name in node.get("reads", []):
            indexes["reads"].setdefault(name, []).append(node_id)
        for write in node.get("writes", []):
            indexes["writes"].setdefault(write["var"], []).append(node_id)
        for name in node.get("control_reads", []):
            indexes["controls"].setdefault(name, []).append(node_id)
        if node.get("kind") == "Label" and node.get("engine_name"):
            indexes["labels"].setdefault(node["engine_name"], []).append(node_id)
        if "unknown_effect" in node:
            unknown.append(node_id)
            if node["unknown_effect"].get("may_write") == "*":
                unknown_writes.append(node_id)
    for edge in edges:
        for write in edge.get("writes", []):
            indexes["edge_writes"].setdefault(write["var"], []).append(edge["edge_id"])
        for name in edge.get("control_reads", []):
            indexes["edge_controls"].setdefault(name, []).append(edge["edge_id"])
    indexes["unknown_effect_nodes"] = unknown
    indexes["unknown_write_nodes"] = unknown_writes
    return indexes



def run():
    renpy.arguments.takes_no_arguments("Builds a semantic Ren'Py ICFG.")
    with io.open(os.environ["RENPYLENS_STORY_GRAPH_SETTINGS"], "r", encoding="utf-8") as stream:
        settings = json.load(stream)
    clock = getattr(time, "perf_counter", time.time)
    started = clock()
    if int(settings.get("schema_version", 3) or 3) == 3:
        try:
            from .graph_v3 import build as build_v3
            result = build_v3(__import__(__name__, fromlist=["*"]), settings)
            if hasattr(result, "to_dict"):
                result = result.to_dict()
        except Exception:
            result = {
                "status": "crashed",
                "engine": renpy.version,
                "schema_version": 3,
                "error": traceback.format_exc(),
            }
        result["elapsed_seconds"] = round(clock() - started, 3)
        with io.open(settings["output"], "w", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2))
        return False
    result = {"status": "running", "engine": renpy.version, "error": None}
    try:
        provenance = _script_manifest()
        nodes = _all_nodes()
        ids = _node_ids(nodes)
        entry_nodes = dict(
            (name, _lookup(name)) for name in settings.get("entry_points", ["start"])
            if _lookup(name) is not None
        )
        raw_nodes, raw_edges, return_sites = _build_icfg(
            nodes, ids, _screen_jump_actions()
        )
        raw_nodes, raw_edges, return_sites, coverage = _game_reachable(
            raw_nodes, raw_edges, return_sites,
            [ids.get(id(node)) for node in entry_nodes.values()],
        )
        blocks, block_edges, node_blocks = _build_narrative_blocks(raw_nodes, raw_edges, return_sites)
        block_return_sites = [
            {
                "kind": "RETURN_SITE",
                "call_block": node_blocks.get(site["call_node"]),
                "continuation_block": node_blocks.get(site["continuation"]),
                "source_call_node": site["call_node"],
            }
            for site in return_sites
        ]
        indexes = _variable_indexes(raw_nodes, raw_edges)
        audit = _audit_graph(
            raw_nodes, raw_edges, return_sites, blocks, block_edges,
            node_blocks, coverage,
        )
        result.update({
            "status": "completed",
            "schema_version": 2,
            "provenance": provenance,
            "coverage": coverage,
            "audit": audit,
            "model": {
                "raw": "semantic-icfg",
                "call_semantics": "pushdown",
                "return_rule": "pop continuation from call stack",
                "compressed": "narrative-basic-block-graph",
                "node_id": "sha1(source identity), 80-bit prefix",
            },
            "entry_points": dict(
                (name, ids.get(id(node))) for name, node in entry_nodes.items()
            ),
            "icfg": {
                "nodes": raw_nodes,
                "edges": raw_edges,
                "return_sites": return_sites,
                "indexes": indexes,
            },
            "narrative_graph": {
                "blocks": blocks,
                "edges": block_edges,
                "return_sites": block_return_sites,
            },
            "summary": {
                "raw_nodes": len(raw_nodes),
                "raw_edges": len(raw_edges),
                "call_sites": len(return_sites),
                "unknown_nodes": sum(1 for node in raw_nodes if node["precision"] == "unknown"),
                "partial_nodes": sum(1 for node in raw_nodes if node["precision"] == "partial"),
                "story_blocks": len(blocks),
                "block_edges": len(block_edges),
                "game_label_roots": coverage["game_label_roots"],
                "read_variables": len(indexes["reads"]),
                "written_variables": len(indexes["writes"]),
                "control_variables": len(set(indexes["controls"]) | set(indexes["edge_controls"])),
            },
        })
        if not audit["valid"]:
            result["status"] = "invalid"
            result["error"] = "Structural graph audit failed: " + ", ".join(audit["errors"])
    except Exception:
        result["status"] = "crashed"
        result["error"] = traceback.format_exc()
    result["elapsed_seconds"] = round(clock() - started, 3)
    with io.open(settings["output"], "w", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2))
    return False


# The aliases below make the extracted modules the production implementations.
# Legacy runner imports continue to resolve through these compatibility names.
_merge_precision = _kernel_merge_precision
_python_semantics = _kernel_python_semantics
_expression_reads = _kernel_expression_reads
_content_reads = _kernel_content_reads
_parameter_names = _kernel_parameter_names
_all_nodes = lambda: _kernel_all_nodes(renpy)
_screen_jump_actions = lambda: _kernel_screen_jump_actions(renpy)
_lookup = lambda label: _kernel_lookup(renpy, label)


def _call_screen(node):
    """Compatibility adapter for the shared Screen fallback service."""
    return _kernel_call_screen(node, _lookup)

# Public names used by the thin v2 compatibility wrappers and migration tests.
python_semantics = _python_semantics
screen_jump_actions = _screen_jump_actions
_audit_graph = _kernel_validate_components
audit_graph = _audit_graph
build = run
