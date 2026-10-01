"""v3 StoryGraph assembly over the existing v2 control-flow builder."""

from .audit import validate
from .expressions import parse_expression_ir
from .call_semantics import formal_entries, label_parameter_names, plan_call
from .model import (BlockIR, EdgeIR, EdgeRef, EffectIR, GraphIR, NodeRef, PrecisionSet, SourceLocation,
                    SymbolRef,
                    merge_precision, stable_digest)
from .renpy_adapter import (IdentityIndex, node_source_path, screen_source_records,
                            script_manifest)
from .screen_semantics import analyze_screen_sources, call_screen
from .serialization import _block_from_v2, _edge_from_v2, _node_from_v2


def _node_expression_sources(node):
    """Yield expressions whose parse precision belongs to node state/control."""
    if node is None:
        return ()
    kind = type(node).__name__
    if kind == "If":
        return tuple(
            str(condition) for condition, _block in (getattr(node, "entries", None) or [])
            if condition not in (None, True, False)
        )
    if kind == "While":
        return (str(getattr(node, "condition", "True")),)
    if kind == "Return":
        expression = str(getattr(node, "expression", "") or "")
        return (expression,) if expression else ()
    if kind in ("Jump", "Call") and getattr(node, "expression", False):
        return (str(getattr(node, "target" if kind == "Jump" else "label", "") or ""),)
    if kind == "Menu":
        return tuple(
            str(item[1]) for item in (getattr(node, "items", None) or [])
            if item and len(item) > 1 and item[1] not in (None, True, False)
        ) + ((str(getattr(node, "set", "")),)
             if getattr(node, "set", None) else ())
    return ()


def _apply_expression_semantics(converted, node, local_names=(), local_owner=""):
    """Attach typed expression evidence while retaining the v2 projection."""
    parsed_items = []
    for source in _node_expression_sources(node):
        parsed_items.append(parse_expression_ir(source, local_names, local_owner))
    if not parsed_items:
        return
    target = ("control_reads" if converted.kind in
              ("If", "While", "Jump", "Call", "Menu") else "reads")
    current = list(getattr(converted, target))
    seen = set(repr(item.to_dict()) for item in current)
    for parsed in parsed_items:
        for item in parsed.reads:
            key = repr(item.to_dict())
            if key not in seen:
                seen.add(key)
                current.append(item)
    setattr(converted, target, tuple(current))
    converted.extra["expression_ir"] = [item.to_dict() for item in parsed_items]
    if any(item.precision == "unknown" for item in parsed_items):
        converted.precision = PrecisionSet(converted.precision.topology, "unknown",
                                           converted.precision.content)


def _canonical_indexes(indexes, node_map, edge_map):
    result = {}
    for key, values in (indexes or {}).items():
        if isinstance(values, dict):
            result[key] = {}
            for name, refs in values.items():
                result[key][name] = [node_map.get(value, edge_map.get(value, value)) for value in refs]
        elif isinstance(values, list):
            result[key] = [node_map.get(value, edge_map.get(value, value)) for value in values]
        else:
            result[key] = values
    return result


def _typed_indexes(indexes, nodes, edges, node_map, edge_map):
    """Add typed symbol access records without changing v2 index spelling."""
    result = _canonical_indexes(indexes, node_map, edge_map)
    symbols = {}

    def add(symbol, role, value):
        if symbol is None:
            return
        key = "%s:%s:%s" % (symbol.kind, symbol.owner, symbol.display())
        entry = symbols.setdefault(key, {"symbol": symbol.to_dict(), "reads": [],
                                         "controls": [], "writes": [],
                                         "edge_reads": [], "edge_writes": []})
        if value not in entry[role]:
            entry[role].append(value)

    for node in nodes:
        for symbol in node.reads:
            add(symbol, "reads", node.ref.node_id)
        for symbol in node.control_reads:
            add(symbol, "controls", node.ref.node_id)
        for effect in node.effects:
            for symbol in effect.may_read:
                add(symbol, "reads", node.ref.node_id)
            for symbol in effect.may_write:
                add(symbol, "writes", node.ref.node_id)
    for edge in edges:
        edge_effects = list(edge.call_site.effects if edge.call_site is not None else ())
        action_value = edge.metadata.get("action")
        if isinstance(action_value, dict):
            from .model import ScreenActionIR
            pending = [ScreenActionIR.from_dict(action_value)]
            while pending:
                action = pending.pop()
                edge_effects.extend(action.effects)
                pending.extend(action.children)
        for effect in edge_effects:
            for symbol in effect.may_read:
                add(symbol, "edge_reads", edge.ref.edge_id)
                bucket = result.setdefault("edge_reads", {}).setdefault(symbol.display(), [])
                if edge.ref.edge_id not in bucket:
                    bucket.append(edge.ref.edge_id)
            for symbol in effect.may_write:
                add(symbol, "edge_writes", edge.ref.edge_id)
                bucket = result.setdefault("edge_writes", {}).setdefault(symbol.display(), [])
                if edge.ref.edge_id not in bucket:
                    bucket.append(edge.ref.edge_id)
    result["symbols"] = dict(sorted(symbols.items()))
    result["unknown_read_nodes"] = sorted(set(
        node.ref.node_id for node in nodes
        if any(symbol.kind == "unknown" for symbol in node.reads + node.control_reads)
    ))
    return result


def _block_precision(members, fallback="exact"):
    """Aggregate precision by evidence dimension and story relevance.

    Presentation nodes can carry state evidence, but their conservative
    rendering evidence must not make a narrative block's topology or content
    appear conservative.  Control and unknown nodes provide topology
    evidence; state evidence includes presentation nodes; content evidence is
    limited to narrative nodes.
    """
    topology = [
        node.precision.topology for node in members
        if node.story_relevance in ("control", "unknown")
    ]
    state = [node.precision.state for node in members]
    content = [
        node.precision.content for node in members
        if node.story_relevance == "narrative"
    ]
    return PrecisionSet(
        merge_precision(topology or [fallback]),
        merge_precision(state or [fallback]),
        merge_precision(content or [fallback]),
    )


def build(legacy_builder, settings):
    """Build v3 while preserving the exact v2 builder as the semantic source."""
    renpy = legacy_builder.renpy
    nodes = legacy_builder._all_nodes()
    legacy_ids = legacy_builder._node_ids(nodes)
    old_by_id = dict((value, node) for node, value in ((node, legacy_ids[id(node)]) for node in nodes))
    manifest, graph_id = script_manifest(getattr(renpy.config, "gamedir", ""))
    screen_analysis = analyze_screen_sources(screen_source_records(renpy))
    identity = IdentityIndex(graph_id, nodes, getattr(renpy.config, "gamedir", ""))
    entry_nodes = dict(
        (name, legacy_builder._lookup(name))
        for name in settings.get("entry_points", ["start"])
        if legacy_builder._lookup(name) is not None
    )
    raw_nodes, raw_edges, return_sites = legacy_builder._build_icfg(
        nodes, legacy_ids, legacy_builder._screen_jump_actions()
    )
    raw_nodes, raw_edges, return_sites, coverage = legacy_builder._game_reachable(
        raw_nodes, raw_edges, return_sites,
        [legacy_ids.get(id(node)) for node in entry_nodes.values()],
    )
    old_blocks, old_block_edges, node_blocks = legacy_builder._build_narrative_blocks(
        raw_nodes, raw_edges, return_sites
    )
    old_block_return_sites = [
        {
            "kind": "RETURN_SITE",
            "call_block": node_blocks.get(site["call_node"]),
            "continuation_block": node_blocks.get(site["continuation"]),
            "source_call_node": site["call_node"],
        }
        for site in return_sites
    ]
    indexes = legacy_builder._variable_indexes(raw_nodes, raw_edges)
    old_audit = legacy_builder._audit_graph(
        raw_nodes, raw_edges, return_sites, old_blocks, old_block_edges,
        node_blocks, coverage,
    )
    legacy_value = {
        "status": "completed",
        "engine": getattr(renpy, "version", ""),
        "schema_version": 2,
        "error": None,
        "provenance": legacy_builder._script_manifest(),
        "coverage": coverage,
        "audit": old_audit,
        "entry_points": dict((name, legacy_ids.get(id(node)))
                              for name, node in entry_nodes.items()),
        "model": {
            "raw": "semantic-icfg",
            "call_semantics": "pushdown",
            "return_rule": "pop continuation from call stack",
            "compressed": "narrative-basic-block-graph",
            "node_id": "sha1(source identity), 80-bit prefix",
        },
        "icfg": {
            "nodes": raw_nodes,
            "edges": raw_edges,
            "return_sites": return_sites,
            "indexes": indexes,
        },
        "narrative_graph": {
            "blocks": old_blocks,
            "edges": old_block_edges,
            "return_sites": old_block_return_sites,
        },
        "summary": {
            "raw_nodes": len(raw_nodes), "raw_edges": len(raw_edges),
            "call_sites": len(return_sites), "story_blocks": len(old_blocks),
            "block_edges": len(old_block_edges),
            "unknown_nodes": sum(1 for node in raw_nodes if node.get("precision") == "unknown"),
            "partial_nodes": sum(1 for node in raw_nodes if node.get("precision") == "partial"),
            "game_label_roots": coverage.get("game_label_roots", 0),
            "read_variables": len(indexes.get("reads", {})),
            "written_variables": len(indexes.get("writes", {})),
            "control_variables": len(set(indexes.get("controls", {})) | set(indexes.get("edge_controls", {}))),
        },
    }
    node_map = {}
    v3_nodes = []
    fallback_ids = set(identity.fallback_nodes)
    ambiguous_ids = set(identity.ambiguous_nodes)
    for record in raw_nodes:
        old_id = record["node_id"]
        node = old_by_id.get(old_id)
        local_names, local_owner = identity.scope_for(node)
        converted = _node_from_v2(record, graph_id, local_names, local_owner)
        converted.location = SourceLocation(
            node_source_path(node, getattr(renpy.config, "gamedir", "")),
            converted.location.line, converted.location.column,
        )
        _apply_expression_semantics(converted, node, local_names, local_owner)
        ref = identity.ref_for(node)
        if ref is None:
            fallback = stable_digest({
                "version": 1, "kind": record.get("kind", ""),
                "location": record.get("location", {}), "legacy_id": old_id,
            }, "S_")
            ref = NodeRef(
                graph_id, "N_" + fallback[2:], fallback,
                stable_digest({"kind": record.get("kind", ""),
                               "contents": record.get("contents", [])}, "C_", 64),
            )
        converted.ref = ref
        converted.extra["legacy_node_id"] = old_id
        if id(node) in fallback_ids:
            converted.extra["identity_fallback"] = True
        if id(node) in ambiguous_ids:
            converted.extra["identity_ambiguous"] = True
        node_map[old_id] = converted.ref.node_id
        v3_nodes.append(converted)

    edge_map = {}
    v3_edges = []
    candidate_edges = []
    edge_ordinals = {}
    for record in raw_edges:
        old_source = record.get("from")
        old_target = record.get("to")
        source_node = old_by_id.get(old_source)
        target_node = old_by_id.get(old_target)
        slot = record.get("choice_index", record.get("branch_index", ""))
        key = (old_source, old_target, record.get("kind", ""))
        ordinal = edge_ordinals.get(key, 0)
        edge_ordinals[key] = ordinal + 1
        # Menu choices already have a stable branch slot (the item index),
        # which is also what GraphMachine and the Hook pass to IdentityIndex.
        # Only edges without an explicit slot need a duplicate occurrence.
        edge_slot = slot
        if edge_slot in (None, ""):
            edge_slot = "occurrence:%d" % ordinal
        edge_id = identity.edge_id(source_node or old_source, target_node or old_target,
                                   record.get("kind", ""), str(edge_slot))
        converted = _edge_from_v2(record, graph_id)
        converted.ref = EdgeRef(graph_id, edge_id, node_map.get(old_source, old_source),
                                node_map.get(old_target, old_target) if old_target is not None else None)
        is_candidate = record.get("derivation") in (
            "screen-source-scan", "literal-screen-argument", "call-screen-return-model"
        )
        if is_candidate:
            converted.metadata["candidate_edge"] = True
        converted.metadata["legacy_edge_id"] = record.get("edge_id", "")
        edge_map[record.get("edge_id", "")] = edge_id
        (candidate_edges if is_candidate else v3_edges).append(converted)

    # Parsed Screen Language actions are authoritative v3 evidence.  Source
    # scan guesses stay in candidate_edges and never enter determined CFG.
    screens_by_name = dict((screen.name, screen) for screen in screen_analysis.screens)
    labels_by_name = dict((item.engine_name, item) for item in v3_nodes
                          if item.kind == "Label" and item.engine_name)

    def action_leaves(actions):
        for action in actions:
            if action.kind == "list":
                for child in action_leaves(action.children):
                    yield child
                    if child.kind in ("jump", "call", "return"):
                        break
            elif action.kind == "if":
                for child in action_leaves(action.children):
                    yield child
            else:
                yield action

    for old_id, source_ir in dict((item.extra.get("legacy_node_id"), item)
                                  for item in v3_nodes).items():
        source_node = old_by_id.get(old_id)
        if source_node is None or type(source_node).__name__ != "UserStatement":
            continue
        screen_name, _guessed_labels = call_screen(source_node)
        screen = screens_by_name.get(screen_name)
        if screen is None:
            continue
        for action in action_leaves(screen.actions):
            target_ir = labels_by_name.get(action.target) if action.kind in ("jump", "call") else None
            if action.kind == "return":
                target_ir = next((item for item in v3_nodes
                                  if item.extra.get("legacy_node_id") == legacy_ids.get(
                                      id(getattr(source_node, "next", None)))), None)
            if target_ir is None:
                if action.kind in ("jump", "call"):
                    candidate_edges.append(EdgeIR(
                        EdgeRef(graph_id, identity.edge_id(source_node, action.target,
                                                           "SCREEN_%s" % action.kind.upper(),
                                                           action.action_id),
                                source_ir.ref.node_id, None),
                        "SCREEN_%s" % action.kind.upper(),
                        PrecisionSet("unknown", action.precision.state, "exact"),
                        "screen-sl-ast-unresolved",
                        {"screen_id": screen.screen_id, "action": action.to_dict(),
                         "candidate_edge": True},
                    ))
                continue
            edge_kind = "SCREEN_%s" % action.kind.upper()
            edge_id = identity.edge_id(source_node, old_by_id.get(
                target_ir.extra.get("legacy_node_id")), edge_kind, action.action_id)
            v3_edges.append(EdgeIR(
                EdgeRef(graph_id, edge_id, source_ir.ref.node_id, target_ir.ref.node_id),
                edge_kind, action.precision, "screen-sl-ast",
                {"screen_id": screen.screen_id, "action": action.to_dict(),
                 "target_resolution": "resolved"},
            ))

    # Stage 4: control-flow topology is still supplied by the stable legacy
    # builder, while Call/Return state semantics come from the shared Kernel
    # planner rather than its conservative v2 placeholders.
    v3_by_legacy = dict((item.extra.get("legacy_node_id"), item) for item in v3_nodes)
    call_edges = dict((edge.ref.source_node_id, edge)
                      for edge in v3_edges if edge.kind == "CALL_EDGE")
    return_by_call = dict((site.get("call_node"), site.get("continuation"))
                          for site in return_sites)
    for old_id, converted in v3_by_legacy.items():
        node = old_by_id.get(old_id)
        if node is None:
            continue
        kind = type(node).__name__
        if kind == "Label" and getattr(node, "parameters", None) is not None:
            converted.effects = tuple(
                effect for effect in converted.effects
                if effect.reason != "label-parameter-binding"
            )
            if not any(effect.precision == "unknown" for effect in converted.effects):
                converted.reads = tuple(symbol for symbol in converted.reads
                                        if symbol.kind != "unknown")
                converted.control_reads = tuple(symbol for symbol in converted.control_reads
                                                if symbol.kind != "unknown")
                converted.precision = PrecisionSet(converted.precision.topology, "exact",
                                                   converted.precision.content)
            try:
                declarations = formal_entries(getattr(node, "parameters", None)) or ()
            except ValueError:
                declarations = ()
            converted.extra["formal_parameters"] = [
                {
                    "formal": SymbolRef.local(name, converted.ref.structural_id).to_dict(),
                    "default": (parse_expression_ir(str(default)).to_dict()
                                if default is not None else None),
                }
                for name, default in declarations
            ] or list(label_parameter_names(getattr(node, "parameters", None)) or ())
        if kind == "Return" and getattr(node, "expression", None):
            expression = parse_expression_ir(getattr(node, "expression", ""), *identity.scope_for(node))
            converted.effects = tuple(list(converted.effects) + [
                EffectIR("return_value", SymbolRef.store("_return"), "set", expression,
                         expression.reads, (SymbolRef.store("_return"),), expression.precision,
                         "", expression.constant_known, expression.constant_value)
            ])
        if kind != "Call":
            continue
        edge = call_edges.get(converted.ref.node_id)
        old_target = next((record.get("to") for record in raw_edges
                           if record.get("kind") == "CALL_EDGE" and record.get("from") == old_id), None)
        target_node = old_by_id.get(old_target)
        target_converted = v3_by_legacy.get(old_target)
        caller_names, caller_owner = identity.scope_for(node)
        callee_names, callee_owner = identity.scope_for(target_node)
        continuation = v3_by_legacy.get(return_by_call.get(old_id))
        dynamic = bool(getattr(node, "expression", False) or target_node is None)
        call_site = plan_call(
            getattr(node, "arguments", None),
            getattr(target_node, "parameters", None) if target_node is not None else None,
            caller_names, caller_owner, callee_owner,
            target_converted.ref if target_converted is not None else None,
            continuation.ref if continuation is not None else None,
            dynamic,
        )
        # Arguments are evaluated in the caller before a frame is entered.
        # Preserve their dependencies even for a dynamic target or an
        # otherwise-invalid binding; dropping them would make slicing claim
        # that the call has no state input.
        call_reads = []
        known_reads = set(repr(symbol.to_dict()) for symbol in converted.reads)
        for argument in call_site.arguments:
            for symbol in argument.expression.reads:
                key = repr(symbol.to_dict())
                if key not in known_reads:
                    known_reads.add(key)
                    call_reads.append(symbol)
        if call_reads:
            converted.reads = tuple(list(converted.reads) + call_reads)
        converted.effects = tuple(effect for effect in converted.effects
                                  if effect.reason != "call-argument-binding")
        if not any(argument.precision == "unknown" for argument in call_site.arguments):
            converted.reads = tuple(symbol for symbol in converted.reads
                                    if symbol.kind != "unknown")
            converted.control_reads = tuple(symbol for symbol in converted.control_reads
                                            if symbol.kind != "unknown")
        converted.precision = PrecisionSet(call_site.precision.topology,
                                           call_site.precision.state,
                                           converted.precision.content)
        converted.extra["call"] = call_site.to_dict()
        if edge is not None:
            edge.call_site = call_site
            edge.precision = call_site.precision

    block_map = {}
    v3_blocks = []
    node_by_legacy = dict((node.extra.get("legacy_node_id"), node) for node in v3_nodes)
    for record in old_blocks:
        source_nodes = [node_map.get(item, item) for item in record.get("source_nodes", [])]
        entry = node_map.get(record.get("entry_node"), record.get("entry_node"))
        block_id = stable_digest({"version": 1, "entry": entry,
                                  "source_nodes": source_nodes,
                                  "control_kind": record.get("control_kind", "")}, "B_")
        converted = _block_from_v2(record)
        converted.block_id = block_id
        converted.entry_node = entry
        converted.terminator_node = node_map.get(record.get("terminator_node"), record.get("terminator_node"))
        converted.source_nodes = tuple(source_nodes)
        members = [node_by_legacy.get(item) for item in record.get("source_nodes", [])]
        members = [node for node in members if node is not None]
        if members:
            converted.effects = tuple(
                effect for node in members for effect in node.effects
            )
            converted.precision = _block_precision(
                members, "exact" if members else converted.precision.topology
            )
            converted.story_relevance = tuple(sorted(set(
                item for node in members for item in (node.story_relevance,)
            )))
        converted.metadata["legacy_block_id"] = record.get("block_id", "")
        block_map[record.get("block_id", "")] = block_id
        v3_blocks.append(converted)

    v3_block_edges = []
    edge_by_id = dict((edge.ref.edge_id, edge) for edge in v3_edges + candidate_edges)
    for record in old_block_edges:
        converted = _edge_from_v2(record, graph_id)
        source = block_map.get(record.get("from"), record.get("from"))
        target = block_map.get(record.get("to"), record.get("to")) if record.get("to") is not None else None
        edge_id = stable_digest({"version": 1, "from": source, "to": target,
                                 "kind": record.get("kind", ""),
                                 "source_edge": edge_map.get(record.get("source_edge"), record.get("source_edge"))}, "BE_")
        converted.ref = EdgeRef(graph_id, edge_id, source, target)
        if record.get("source_edge") is not None:
            converted.metadata["source_edge"] = edge_map.get(
                record.get("source_edge"), record.get("source_edge")
            )
            source_edge = edge_by_id.get(converted.metadata["source_edge"])
            if source_edge is not None and source_edge.metadata.get("candidate_edge"):
                converted.metadata["candidate_edge"] = True
        converted.metadata["legacy_edge_id"] = record.get("edge_id", "")
        if not converted.metadata.get("candidate_edge"):
            v3_block_edges.append(converted)

    canonical_to_legacy = dict((value, key) for key, value in node_map.items())
    for source_edge in [edge for edge in v3_edges if edge.derivation == "screen-sl-ast"]:
        source_old = canonical_to_legacy.get(source_edge.ref.source_node_id)
        target_old = canonical_to_legacy.get(source_edge.ref.target_node_id)
        source_block = block_map.get(node_blocks.get(source_old))
        target_block = block_map.get(node_blocks.get(target_old))
        if source_block is None or target_block is None:
            continue
        edge_id = stable_digest({"version": 1, "from": source_block, "to": target_block,
                                 "kind": source_edge.kind,
                                 "source_edge": source_edge.ref.edge_id}, "BE_")
        v3_block_edges.append(EdgeIR(
            EdgeRef(graph_id, edge_id, source_block, target_block), source_edge.kind,
            source_edge.precision, source_edge.derivation,
            {"source_edge": source_edge.ref.edge_id, "target_resolution": "resolved",
             "screen_id": source_edge.metadata.get("screen_id", "")},
        ))

    # Block topology also includes the precision of the connecting control
    # edges.  Keep state/content dimensions from the relevance-aware member
    # aggregation above.
    block_by_id = dict((block.block_id, block) for block in v3_blocks)
    for edge in v3_block_edges:
        if edge.metadata.get("candidate_edge"):
            continue
        source = block_by_id.get(edge.ref.source_node_id)
        target = block_by_id.get(edge.ref.target_node_id)
        for block in (source, target):
            if block is None:
                continue
            block.precision = PrecisionSet(
                merge_precision((block.precision.topology,
                                 edge.precision.topology)),
                block.precision.state,
                block.precision.content,
            )

    v3_return_sites = [
        dict(site,
             call_node=node_map.get(site.get("call_node"), site.get("call_node")),
             continuation=node_map.get(site.get("continuation"), site.get("continuation"))
             if site.get("continuation") is not None else None)
        for site in return_sites
    ]
    v3_block_return_sites = [
        dict(site,
             call_block=block_map.get(site.get("call_block"), site.get("call_block")),
             continuation_block=block_map.get(site.get("continuation_block"), site.get("continuation_block")),
             source_call_node=node_map.get(site.get("source_call_node"), site.get("source_call_node")))
        for site in old_block_return_sites
    ]
    v3_indexes = _typed_indexes(indexes, v3_nodes, v3_edges, node_map, edge_map)
    graph = GraphIR(
        graph_id=graph_id,
        engine=getattr(renpy, "version", ""),
        provenance=manifest,
        coverage=coverage,
        audit={},
        entry_points=dict((name, node_map.get(legacy_ids.get(id(node)))) for name, node in entry_nodes.items()),
        nodes=v3_nodes,
        edges=v3_edges,
        return_sites=v3_return_sites,
        blocks=v3_blocks,
        block_edges=v3_block_edges,
        block_return_sites=v3_block_return_sites,
        indexes=v3_indexes,
        summary={
            "raw_nodes": len(v3_nodes), "raw_edges": len(v3_edges),
            "call_sites": len(v3_return_sites), "story_blocks": len(v3_blocks),
            "block_edges": len(v3_block_edges),
            "legacy_audit": old_audit,
        },
        model={"raw": "semantic-icfg", "schema": "storygraph-ir-v3",
               "identity": "structural-path-v1", "call_semantics": "pushdown"},
        legacy=legacy_value,
        screens=screen_analysis.screens,
        candidate_edges=candidate_edges,
    )
    value = graph.to_dict()
    graph.audit = validate(value)
    relevance_counts = {}
    for node in value["icfg"]["nodes"]:
        relevance = node.get("story_relevance", "unknown") or "unknown"
        relevance_counts[relevance] = relevance_counts.get(relevance, 0) + 1
    graph.summary.update({
        "unknown_nodes": sum(1 for node in value["icfg"]["nodes"]
                              if node.get("precision", {}).get("state") == "unknown"),
        "partial_nodes": sum(1 for node in value["icfg"]["nodes"]
                              if node.get("precision", {}).get("state") == "partial"),
        "game_label_roots": coverage.get("game_label_roots", 0),
        "read_variables": len(v3_indexes.get("reads", {})),
        "written_variables": len(v3_indexes.get("writes", {})),
        "control_variables": len(set(v3_indexes.get("controls", {})) | set(v3_indexes.get("edge_controls", {}))),
        "story_relevance": dict(sorted(relevance_counts.items())),
        "screens": len(graph.screens),
        "candidate_edges": len(graph.candidate_edges),
        "screen_unknown_boundaries": len(screen_analysis.unknown_boundaries),
    })
    return graph
