"""Convert live Ren'Py control-flow records into typed StoryGraph IR."""

import copy
from .expressions import parse_expression_ir
from .model import (BlockIR, CallSiteIR, EdgeIR, EdgeRef, EffectIR, NodeIR,
                    NodeRef, PrecisionSet, SourceLocation, SymbolRef)
from .python_semantics import analyze_ir


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
    source = write.get("expr", "")
    if not source and write.get("value_known"):
        source = repr(write.get("value"))
    expression = parse_expression_ir(source, local_names, owner)
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


def node_ir(record, graph_id, local_names=(), local_owner=""):
    record = record or {}
    ref = NodeRef(graph_id, record["node_id"])
    typed_statement = None
    if record.get("kind") == "Python" and record.get("source"):
        typed_statement = analyze_ir(record.get("source", ""), local_names, local_owner)
    if typed_statement is not None:
        effects = list(typed_statement.get("effects", ()))
    elif record.get("effects"):
        effects = [EffectIR.from_dict(item) for item in record.get("effects", [])]
    else:
        effects = [_effect_from_write(item, local_owner, local_names) for item in record.get("writes", [])]
    if typed_statement is None and record.get("unknown_effect") and not record.get("effects"):
        effects.append(_effect_from_unknown(record["unknown_effect"], local_owner, local_names))
    # Failed expressions retain an explicit unknown read boundary.
    unknown_read = any(
        getattr(effect.expression, "precision", "") == "unknown"
        or any(getattr(item, "kind", "") == "unknown" for item in effect.may_read)
        for effect in effects
    )
    raw_precision = typed_statement.get("precision", "exact") if typed_statement is not None else record.get("precision", "exact")
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
        if typed_statement is not None:
            dimensions = PrecisionSet("unknown" if any(effect.kind == "unknown" for effect in effects) else "exact",
                                      precision, "exact")
        elif relevance == "presentation":
            dimensions = PrecisionSet("exact", precision, "exact")
        else:
            dimensions = PrecisionSet(precision, precision, precision)
    known = set(("graph_id", "node_id", "kind", "engine_name", "location", "reads", "control_reads",
                 "writes", "unknown_effect", "precision", "effect_precision", "contents",
                 "structural_id", "content_hash"))
    extra = dict((key, copy.deepcopy(value)) for key, value in record.items() if key not in known)
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
    if kind in ("Init", "Define", "Default", "Screen", "Style", "Image", "Transform"):
        return "definition"
    return "unknown"


def edge_ir(record, graph_id):
    record = record or {}
    ref = EdgeRef(graph_id, record["edge_id"], record.get("from", ""), record.get("to"))
    precision = record.get("precision", "exact")
    dimensions = (PrecisionSet.from_dict(precision)
                  if isinstance(precision, dict)
                  else PrecisionSet(precision, precision, precision))
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
            # An object member or runtime method leaves a condition value
            # uncertain, while both syntactic branch destinations are known.
            failed = bool(condition_ir.parse_error)
            dimensions = PrecisionSet("unknown" if failed else dimensions.topology,
                                      "unknown" if failed else "partial", dimensions.content)
            if not metadata.get("control_reads"):
                metadata["control_reads"] = [{
                    "kind": "unknown", "name": "*", "owner": "",
                    "base": None, "member": "", "index": None,
                    "precision": "unknown",
                }]
    return EdgeIR(ref, record.get("kind", ""), dimensions,
                  record.get("derivation", ""), metadata, call_site)


def block_ir(record):
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
    return BlockIR(record["block_id"], record.get("kind", ""), record.get("control_kind", ""),
                   record.get("entry_node", ""),
                   record.get("terminator_node", ""),
                   _location(record.get("location")),
                   record.get("source_nodes", []),
                   record.get("contents", []), _symbols(record.get("reads")),
                   _symbols(record.get("control_reads")), [],
                   dimensions, relevance, metadata)
