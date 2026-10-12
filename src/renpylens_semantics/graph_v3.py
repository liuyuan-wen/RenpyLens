"""Typed StoryGraph v3 assembly from Ren'Py control-flow records."""

from .audit import validate
from .expressions import parse_expression_ir
from .call_semantics import formal_entries, label_parameter_names, plan_call
from .model import (BlockIR, EdgeIR, EdgeRef, EffectIR, GraphIR, NodeRef, PrecisionSet, SourceLocation,
                    SymbolRef,
                    merge_precision, stable_digest)
from .renpy_adapter import (IdentityIndex, initial_scalar_state, node_source_path, screen_source_records,
                            script_manifest)
from .screen_semantics import analyze_screen_sources, call_screen, instantiate_screen
from .assembly_records import block_ir, edge_ir, node_ir


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
    """Attach typed expression dependencies and precision."""
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
        state = "unknown" if any(item.parse_error for item in parsed_items) else "partial"
        converted.precision = PrecisionSet(converted.precision.topology, merge_precision((converted.precision.state, state)),
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
    """Index typed symbol access records by role."""
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


def build(builder, settings):
    """Build typed nodes, calls, Screen actions, and narrative blocks."""
    renpy = builder.renpy
    nodes = builder._all_nodes()
    manifest, graph_id = script_manifest(getattr(renpy.config, "gamedir", ""))
    screen_records = screen_source_records(renpy)
    screen_analysis = analyze_screen_sources(screen_records)
    identity = IdentityIndex(graph_id, nodes, getattr(renpy.config, "gamedir", ""))
    source_ids = dict((id(node), identity.ref_for(node).node_id) for node in nodes)
    ast_by_id = dict((source_ids[id(node)], node) for node in nodes)
    entry_nodes = dict(
        (name, builder._lookup(name))
        for name in settings.get("entry_points", ["start"])
        if builder._lookup(name) is not None
    )
    raw_nodes, raw_edges, return_sites = builder._build_icfg(
        nodes, source_ids, builder._screen_jump_actions()
    )
    raw_nodes, raw_edges, return_sites, coverage = builder._game_reachable(
        raw_nodes, raw_edges, return_sites,
        [source_ids.get(id(node)) for node in entry_nodes.values()],
    )
    raw_blocks, raw_block_edges, node_blocks = builder._build_narrative_blocks(
        raw_nodes, raw_edges, return_sites
    )
    raw_block_return_sites = [
        {
            "kind": "RETURN_SITE",
            "call_block": node_blocks.get(site["call_node"]),
            "continuation_block": node_blocks.get(site["continuation"]),
            "source_call_node": site["call_node"],
        }
        for site in return_sites
    ]
    indexes = builder._variable_indexes(raw_nodes, raw_edges)
    node_map = {}
    v3_nodes = []
    ir_by_source = {}
    fallback_ids = set(identity.fallback_nodes)
    ambiguous_ids = set(identity.ambiguous_nodes)
    for record in raw_nodes:
        source_id = record["node_id"]
        node = ast_by_id.get(source_id)
        local_names, local_owner = identity.scope_for(node)
        converted = node_ir(record, graph_id, local_names, local_owner)
        converted.location = SourceLocation(
            node_source_path(node, getattr(renpy.config, "gamedir", "")),
            converted.location.line, converted.location.column,
        )
        _apply_expression_semantics(converted, node, local_names, local_owner)
        ref = identity.ref_for(node)
        if ref is None:
            fallback = stable_digest({
                "version": 1, "kind": record.get("kind", ""),
                "location": record.get("location", {}), "source_id": source_id,
            }, "S_")
            ref = NodeRef(
                graph_id, "N_" + fallback[2:], fallback,
                stable_digest({"kind": record.get("kind", ""),
                               "contents": record.get("contents", [])}, "C_", 64),
            )
        converted.ref = ref
        ir_by_source[source_id] = converted
        if id(node) in fallback_ids:
            converted.extra["identity_fallback"] = True
        if id(node) in ambiguous_ids:
            converted.extra["identity_ambiguous"] = True
        node_map[source_id] = converted.ref.node_id
        v3_nodes.append(converted)

    edge_map = {}
    v3_edges = []
    candidate_edges = []
    edge_ordinals = {}
    for record in raw_edges:
        source_id = record.get("from")
        target_id = record.get("to")
        source_node = ast_by_id.get(source_id)
        target_node = ast_by_id.get(target_id)
        slot = record.get("choice_index", record.get("branch_index", ""))
        key = (source_id, target_id, record.get("kind", ""))
        ordinal = edge_ordinals.get(key, 0)
        edge_ordinals[key] = ordinal + 1
        # Menu choices already have a stable branch slot (the item index),
        # which is also what GraphMachine and the Hook pass to IdentityIndex.
        # Only edges without an explicit slot need a duplicate occurrence.
        edge_slot = slot
        if edge_slot in (None, ""):
            edge_slot = "occurrence:%d" % ordinal
        edge_id = identity.edge_id(source_node or source_id, target_node or target_id,
                                   record.get("kind", ""), str(edge_slot))
        converted = edge_ir(record, graph_id)
        converted.ref = EdgeRef(graph_id, edge_id, node_map.get(source_id, source_id),
                                node_map.get(target_id, target_id) if target_id is not None else None)
        is_candidate = record.get("derivation") in (
            "screen-source-scan", "literal-screen-argument", "call-screen-return-model"
        )
        if is_candidate:
            converted.metadata["candidate_edge"] = True
        edge_map[record.get("edge_id", "")] = edge_id
        (candidate_edges if is_candidate else v3_edges).append(converted)

    # Parsed Screen Language actions are authoritative v3 evidence.  Source
    # scan guesses stay in candidate_edges and never enter determined CFG.
    variant_order = [str(item or '') for item in getattr(renpy.config, "variants", (None,))]
    records_by_name = {}
    for record in screen_records:
        if record["variant"] not in variant_order:
            continue
        previous = records_by_name.get(record["name"])
        if previous is None or variant_order.index(record["variant"]) < variant_order.index(previous["variant"]):
            records_by_name[record["name"]] = record
    screens_by_variant = dict(((screen.name, screen.variant), screen) for screen in screen_analysis.screens)
    screens_by_name = dict((name, screens_by_variant[(name, record["variant"])]) for name, record in records_by_name.items())
    labels_by_name = dict((item.engine_name, item) for item in v3_nodes
                          if item.kind == "Label" and item.engine_name)

    for source_id, source_ir in ir_by_source.items():
        source_node = ast_by_id.get(source_id)
        if source_node is None or type(source_node).__name__ != "UserStatement":
            continue
        screen_name, _guessed_labels = call_screen(source_node)
        screen = screens_by_name.get(screen_name)
        if screen is None:
            continue
        outcomes, unknown = instantiate_screen(source_node, records_by_name[screen_name], screen.screen_id, source_id)
        source_ir.story_relevance = "control"
        source_ir.extra["screen_name"] = screen_name
        source_ir.extra["screen_choice"] = len(outcomes) >= 2 and all(
            item["choice"] and item["action"].kind in ("jump", "call", "return", "end", "rollback") for item in outcomes)
        source_ir.extra["screen_unknown_reasons"] = unknown
        if not unknown:
            source_ir.effects = tuple(effect for effect in source_ir.effects if effect.reason != "user-statement")
            source_ir.reads = tuple(symbol for symbol in source_ir.reads if symbol.kind != "unknown")
            source_ir.precision = PrecisionSet("exact", merge_precision(
                [item["action"].precision.state for item in outcomes] +
                [effect.precision for item in outcomes for effect in item["effects"]]), "exact")
        if outcomes and all(item["action"].kind == "end" for item in outcomes):
            source_ir.extra["terminal"] = "screen-exit"
        if outcomes and all(item["action"].kind == "rollback" for item in outcomes):
            source_ir.extra["terminal"] = "screen-rollback"
        for item in outcomes:
            action = item["action"]
            if action.kind in ("end", "rollback"):
                edge_id = identity.edge_id(source_node, None, "SCREEN_%s" % action.kind.upper(), action.action_id)
                v3_edges.append(EdgeIR(
                    EdgeRef(graph_id, edge_id, source_ir.ref.node_id, None),
                    "SCREEN_%s" % action.kind.upper(), action.precision, "screen-sl-ast",
                    {"screen_id": screen.screen_id, "action": action.to_dict(),
                     "target_resolution": "terminal", "terminal": action.kind,
                     "choice": item["choice"], "condition": item["condition"], "choice_index": item["slot"],
                     "effects": [effect.to_dict() for effect in item["effects"]]},
                ))
                continue
            target_ir = labels_by_name.get(action.target) if action.kind in ("jump", "call") else None
            if action.kind == "return":
                target_ir = ir_by_source.get(source_ids.get(id(getattr(source_node, "next", None))))
            if target_ir is None:
                if action.kind in ("jump", "call"):
                    source_ir.extra["screen_unknown_reasons"].append(
                        "dynamic-screen-target" if action.target is None else "missing-screen-label:%s" % action.target)
                    source_ir.precision = PrecisionSet("unknown", source_ir.precision.state, "exact")
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
            edge_id = identity.edge_id(source_node, ast_by_id.get(target_ir.ref.node_id), edge_kind, action.action_id)
            v3_edges.append(EdgeIR(
                EdgeRef(graph_id, edge_id, source_ir.ref.node_id, target_ir.ref.node_id),
                edge_kind, action.precision, "screen-sl-ast",
                {"screen_id": screen.screen_id, "action": action.to_dict(),
                 "target_resolution": "resolved", "choice": item["choice"],
                 "condition": item["condition"], "choice_index": item["slot"],
                 "effects": [effect.to_dict() for effect in item["effects"]]},
            ))

    # Calls evaluate arguments in caller scope and bind callee-local parameters.
    call_edges = dict((edge.ref.source_node_id, edge)
                      for edge in v3_edges if edge.kind == "CALL_EDGE")
    return_by_call = dict((site.get("call_node"), site.get("continuation"))
                          for site in return_sites)
    for source_id, converted in ir_by_source.items():
        node = ast_by_id.get(source_id)
        if node is None:
            continue
        kind = type(node).__name__
        if kind == "Label" and getattr(node, "parameters", None) is not None:
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
        target_id = next((record.get("to") for record in raw_edges
                           if record.get("kind") == "CALL_EDGE" and record.get("from") == source_id), None)
        target_node = ast_by_id.get(target_id)
        target_converted = ir_by_source.get(target_id)
        caller_names, caller_owner = identity.scope_for(node)
        callee_names, callee_owner = identity.scope_for(target_node)
        continuation = ir_by_source.get(return_by_call.get(source_id))
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
    for record in raw_blocks:
        source_nodes = [node_map.get(item, item) for item in record.get("source_nodes", [])]
        entry = node_map.get(record.get("entry_node"), record.get("entry_node"))
        block_id = stable_digest({"version": 1, "entry": entry,
                                  "source_nodes": source_nodes,
                                  "control_kind": record.get("control_kind", "")}, "B_")
        converted = block_ir(record)
        converted.block_id = block_id
        converted.entry_node = entry
        converted.terminator_node = node_map.get(record.get("terminator_node"), record.get("terminator_node"))
        converted.source_nodes = tuple(source_nodes)
        members = [ir_by_source.get(item) for item in record.get("source_nodes", [])]
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
            screen_member = next((node for node in members if node.extra.get("screen_name")), None)
            if screen_member is not None:
                converted.metadata["screen_name"] = screen_member.extra["screen_name"]
                converted.metadata["screen_unknown_reasons"] = screen_member.extra["screen_unknown_reasons"]
                if screen_member.extra.get("screen_choice"):
                    converted.control_kind = "ScreenChoice"
                elif screen_member.extra.get("terminal") == "screen-exit":
                    converted.control_kind = "End"
                elif screen_member.extra.get("terminal") == "screen-rollback":
                    converted.control_kind = "Rollback"
        block_map[record.get("block_id", "")] = block_id
        v3_blocks.append(converted)

    v3_block_edges = []
    edge_by_id = dict((edge.ref.edge_id, edge) for edge in v3_edges + candidate_edges)
    for record in raw_block_edges:
        converted = edge_ir(record, graph_id)
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
        if not converted.metadata.get("candidate_edge"):
            v3_block_edges.append(converted)

    canonical_to_source = dict((value, key) for key, value in node_map.items())
    for source_edge in [edge for edge in v3_edges if edge.derivation == "screen-sl-ast"]:
        source_record = canonical_to_source.get(source_edge.ref.source_node_id)
        target_record = canonical_to_source.get(source_edge.ref.target_node_id)
        source_block = block_map.get(node_blocks.get(source_record))
        target_block = block_map.get(node_blocks.get(target_record))
        if source_block is None or target_block is None and not source_edge.metadata.get("terminal"):
            continue
        edge_id = stable_digest({"version": 1, "from": source_block, "to": target_block,
                                 "kind": source_edge.kind,
                                 "source_edge": source_edge.ref.edge_id}, "BE_")
        source_ir = ir_by_source[source_record]
        kind = "MENU_CHOICE" if source_ir.extra.get("screen_choice") else source_edge.kind
        v3_block_edges.append(EdgeIR(
            EdgeRef(graph_id, edge_id, source_block, target_block), kind,
            source_edge.precision, source_edge.derivation,
            {"source_edge": source_edge.ref.edge_id, "target_resolution": source_edge.metadata["target_resolution"],
             "terminal": source_edge.metadata.get("terminal", ""),
             "screen_id": source_edge.metadata.get("screen_id", ""),
             "choice": source_edge.metadata.get("choice", ""),
             "condition": source_edge.metadata.get("condition", "True"),
             "choice_index": source_edge.metadata.get("choice_index", 0),
             "effects": source_edge.metadata.get("effects", [])},
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
        for site in raw_block_return_sites
    ]
    v3_indexes = _typed_indexes(indexes, v3_nodes, v3_edges, node_map, edge_map)
    graph = GraphIR(
        graph_id=graph_id,
        engine=getattr(renpy, "version", ""),
        provenance=manifest,
        coverage=coverage,
        audit={},
        entry_points=dict((name, node_map.get(source_ids.get(id(node)))) for name, node in entry_nodes.items()),
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
        },
        model={"raw": "semantic-icfg", "schema": "storygraph-ir-v3",
               "identity": "structural-path-v1", "call_semantics": "pushdown",
               "initial_state": initial_scalar_state(nodes, getattr(renpy, "store", None))},
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
