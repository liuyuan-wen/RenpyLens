"""Small, Python 2 compatible value objects used by the semantic kernel.

The injected Ren'Py runtime can be Python 2, so this module deliberately uses
ordinary classes instead of dataclasses, typing annotations, or Enum.
"""

import hashlib
import json


PRECISION_ORDER = {
    "exact": 0,
    "conservative": 1,
    "partial": 2,
    "unknown": 3,
    "external": 4,
}
STORY_RELEVANCE = (
    "narrative", "control", "state", "presentation", "definition", "unknown",
)


def merge_precision(values):
    return max(values or ["exact"], key=lambda value: PRECISION_ORDER.get(value, 3))


def _json_value(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return dict((key, _json_value(item)) for key, item in value.items())
    return value


class SourceLocation(object):
    __slots__ = ("path", "line", "column")

    def __init__(self, path="", line=None, column=None):
        normalized = str(path or "").replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.lower().startswith("game/"):
            normalized = normalized[5:]
        if normalized.lower().endswith(".rpyc"):
            normalized = normalized[:-5] + ".rpy"
        self.path = normalized
        self.line = line
        self.column = column

    def to_dict(self):
        return {"path": self.path, "line": self.line, "column": self.column}

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        return cls(value.get("path", value.get("filename", "")),
                   value.get("line"), value.get("column"))


class SymbolRef(object):
    __slots__ = ("kind", "name", "owner", "base", "member", "index", "precision")

    def __init__(self, kind="unknown", name="", owner="", base=None,
                 member="", index=None, precision="exact"):
        self.kind = str(kind or "unknown")
        self.name = str(name or "")
        self.owner = str(owner or "")
        self.base = base
        self.member = str(member or "")
        self.index = index
        self.precision = str(precision or "exact")

    @classmethod
    def store(cls, name, precision="exact"):
        return cls("store", name=name, precision=precision)

    @classmethod
    def persistent(cls, name, precision="exact"):
        return cls("persistent", name=name, precision=precision)

    @classmethod
    def local(cls, name, owner="", precision="exact"):
        return cls("local", name=name, owner=owner, precision=precision)

    @classmethod
    def unknown(cls, name="*", precision="unknown"):
        return cls("unknown", name=name, precision=precision)

    def display(self):
        if self.kind in ("store", "persistent", "local"):
            prefix = "" if self.kind == "store" else self.kind + "."
            return prefix + self.name
        if self.kind == "attribute":
            base = self.base.display() if hasattr(self.base, "display") else str(self.base or "")
            return base + "." + self.member
        if self.kind == "subscript":
            base = self.base.display() if hasattr(self.base, "display") else str(self.base or "")
            index = self.index.source if hasattr(self.index, "source") else str(self.index or "?")
            return "%s[%s]" % (base, index)
        return self.name or "*"

    def to_dict(self):
        return {
            "kind": self.kind,
            "name": self.name,
            "owner": self.owner,
            "base": _json_value(self.base),
            "member": self.member,
            "index": _json_value(self.index),
            "precision": self.precision,
        }

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        base = value.get("base")
        if isinstance(base, dict) and "kind" in base:
            base = cls.from_dict(base)
        index = value.get("index")
        if isinstance(index, dict) and "source" in index:
            index = ExprIR.from_dict(index)
        return cls(value.get("kind", "unknown"), value.get("name", ""),
                   value.get("owner", ""), base, value.get("member", ""),
                   index, value.get("precision", "exact"))


class ExprIR(object):
    __slots__ = ("source", "reads", "precision", "constant_known", "constant_value",
                 "parse_error")

    def __init__(self, source="", reads=(), precision="exact", constant_known=False,
                 constant_value=None, parse_error=""):
        self.source = str(source or "")
        self.reads = tuple(reads or ())
        self.precision = str(precision or "exact")
        self.constant_known = bool(constant_known)
        self.constant_value = constant_value
        self.parse_error = str(parse_error or "")

    def to_dict(self):
        result = {
            "source": self.source,
            "reads": [_json_value(item) for item in self.reads],
            "precision": self.precision,
            "constant_known": self.constant_known,
        }
        if self.constant_known:
            result["constant_value"] = _json_value(self.constant_value)
        if self.parse_error:
            result["parse_error"] = self.parse_error
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        raw_reads = value.get("reads", [])
        if raw_reads == "*":
            raw_reads = ["*"]
        elif isinstance(raw_reads, (str, bytes)):
            raw_reads = [raw_reads]
        return cls(value.get("source", ""),
                   [SymbolRef.from_dict(item) if isinstance(item, dict) else SymbolRef.unknown(str(item))
                    for item in raw_reads],
                   value.get("precision", "exact"), value.get("constant_known", False),
                   value.get("constant_value"), value.get("parse_error", ""))


class EffectIR(object):
    __slots__ = ("kind", "target", "operator", "expression", "may_read", "may_write",
                 "precision", "reason", "value_known", "value", "guard")

    def __init__(self, kind="unknown", target=None, operator="", expression=None,
                 may_read=(), may_write=(), precision="exact", reason="",
                 value_known=False, value=None, guard=None):
        self.kind = str(kind or "unknown")
        self.target = target
        self.operator = str(operator or "")
        self.expression = expression
        self.may_read = tuple(may_read or ())
        self.may_write = tuple(may_write or ())
        self.precision = str(precision or "exact")
        self.reason = str(reason or "")
        self.value_known = bool(value_known)
        self.value = value
        self.guard = guard

    def to_dict(self):
        result = {
            "kind": self.kind,
            "target": _json_value(self.target),
            "operator": self.operator,
            "expression": _json_value(self.expression),
            "may_read": [_json_value(item) for item in self.may_read],
            "may_write": [_json_value(item) for item in self.may_write],
            "precision": self.precision,
            "reason": self.reason,
            "value_known": self.value_known,
        }
        if self.value_known:
            result["value"] = _json_value(self.value)
        if self.guard is not None:
            result["guard"] = _json_value(self.guard)
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        def symbols(raw):
            if raw == "*":
                return [SymbolRef.unknown()]
            if isinstance(raw, (str, bytes)):
                return [SymbolRef.store(raw)]
            return [SymbolRef.from_dict(item) if isinstance(item, dict)
                    else SymbolRef.store(item) for item in (raw or [])]
        target = value.get("target")
        if isinstance(target, dict):
            target = SymbolRef.from_dict(target)
        elif isinstance(target, (str, bytes)) and target:
            target = SymbolRef.store(target)
        expression = value.get("expression")
        if isinstance(expression, dict):
            expression = ExprIR.from_dict(expression)
        return cls(value.get("kind", "unknown"), target, value.get("operator", ""),
                   expression,
                   symbols(value.get("may_read", [])),
                   symbols(value.get("may_write", [])),
                   value.get("precision", "exact"), value.get("reason", ""),
                   value.get("value_known", False), value.get("value"),
                   ExprIR.from_dict(value["guard"]) if isinstance(value.get("guard"), dict) else value.get("guard"))


class NodeRef(object):
    __slots__ = ("graph_id", "node_id", "structural_id", "content_hash")

    def __init__(self, graph_id="", node_id="", structural_id="", content_hash=""):
        self.graph_id = str(graph_id or "")
        self.node_id = str(node_id or "")
        self.structural_id = str(structural_id or "")
        self.content_hash = str(content_hash or "")

    def to_dict(self):
        return {"graph_id": self.graph_id, "node_id": self.node_id,
                "structural_id": self.structural_id, "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        return cls(value.get("graph_id", ""), value.get("node_id", ""),
                   value.get("structural_id", ""), value.get("content_hash", ""))


class EdgeRef(object):
    __slots__ = ("graph_id", "edge_id", "source_node_id", "target_node_id")

    def __init__(self, graph_id="", edge_id="", source_node_id="", target_node_id=None):
        self.graph_id = str(graph_id or "")
        self.edge_id = str(edge_id or "")
        self.source_node_id = str(source_node_id or "")
        self.target_node_id = target_node_id

    def to_dict(self):
        return {"graph_id": self.graph_id, "edge_id": self.edge_id,
                "source_node_id": self.source_node_id, "target_node_id": self.target_node_id}

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        return cls(value.get("graph_id", ""), value.get("edge_id", ""),
                   value.get("source_node_id", value.get("from", "")),
                   value.get("target_node_id", value.get("to")))


class CallArgumentIR(object):
    """One source-order argument at a Ren'Py call site."""

    __slots__ = ("index", "keyword", "expression", "unpacking", "precision")

    def __init__(self, index=0, keyword=None, expression=None, unpacking="",
                 precision="exact"):
        self.index = int(index or 0)
        self.keyword = keyword if keyword is None else str(keyword)
        self.expression = expression or ExprIR()
        self.unpacking = str(unpacking or "")
        self.precision = str(precision or "exact")

    def to_dict(self):
        return {"index": self.index, "keyword": self.keyword,
                "expression": _json_value(self.expression),
                "unpacking": self.unpacking, "precision": self.precision}

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        expression = value.get("expression")
        if isinstance(expression, dict):
            expression = ExprIR.from_dict(expression)
        return cls(value.get("index", 0), value.get("keyword"), expression,
                   value.get("unpacking", ""), value.get("precision", "exact"))


class FormalBindingIR(object):
    """Map one callee local formal to an argument or default expression."""

    __slots__ = ("formal", "source_kind", "argument_index", "expression",
                 "precision", "reason")

    def __init__(self, formal=None, source_kind="unknown", argument_index=None,
                 expression=None, precision="unknown", reason=""):
        self.formal = formal or SymbolRef.unknown()
        self.source_kind = str(source_kind or "unknown")
        self.argument_index = argument_index
        self.expression = expression
        self.precision = str(precision or "unknown")
        self.reason = str(reason or "")

    def to_dict(self):
        result = {"formal": _json_value(self.formal), "source_kind": self.source_kind,
                  "argument_index": self.argument_index,
                  "expression": _json_value(self.expression),
                  "precision": self.precision, "reason": self.reason}
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        formal = value.get("formal")
        expression = value.get("expression")
        return cls(SymbolRef.from_dict(formal) if isinstance(formal, dict) else SymbolRef.unknown(),
                   value.get("source_kind", "unknown"), value.get("argument_index"),
                   ExprIR.from_dict(expression) if isinstance(expression, dict) else expression,
                   value.get("precision", "unknown"), value.get("reason", ""))


class CallSiteIR(object):
    """Static call/return contract shared by StoryGraph and GraphMachine."""

    __slots__ = ("target", "dynamic_target", "arguments", "formal_bindings",
                 "return_continuation", "effects", "precision", "reason")

    def __init__(self, target=None, dynamic_target=False, arguments=(),
                 formal_bindings=(), return_continuation=None, effects=(),
                 precision=None, reason=""):
        self.target = target
        self.dynamic_target = bool(dynamic_target)
        self.arguments = tuple(arguments or ())
        self.formal_bindings = tuple(formal_bindings or ())
        self.return_continuation = return_continuation
        self.effects = tuple(effects or ())
        self.precision = precision or PrecisionSet()
        self.reason = str(reason or "")

    def to_dict(self):
        return {
            "target": _json_value(self.target), "dynamic_target": self.dynamic_target,
            "arguments": [_json_value(item) for item in self.arguments],
            "formal_bindings": [_json_value(item) for item in self.formal_bindings],
            "return_continuation": _json_value(self.return_continuation),
            "effects": [_json_value(item) for item in self.effects],
            "precision": _json_value(self.precision), "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        target = value.get("target")
        continuation = value.get("return_continuation")
        precision = value.get("precision")
        return cls(
            NodeRef.from_dict(target) if isinstance(target, dict) else target,
            value.get("dynamic_target", False),
            [CallArgumentIR.from_dict(item) for item in value.get("arguments", [])],
            [FormalBindingIR.from_dict(item) for item in value.get("formal_bindings", [])],
            NodeRef.from_dict(continuation) if isinstance(continuation, dict) else continuation,
            [EffectIR.from_dict(item) for item in value.get("effects", [])],
            PrecisionSet.from_dict(precision), value.get("reason", ""),
        )


class PrecisionSet(object):
    __slots__ = ("topology", "state", "content")

    def __init__(self, topology="exact", state="exact", content="exact"):
        self.topology = topology or "exact"
        self.state = state or "exact"
        self.content = content or "exact"

    def to_dict(self):
        return {"topology": self.topology, "state": self.state, "content": self.content}

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, (str, bytes)):
            return cls(value, value, value)
        value = value or {}
        return cls(value.get("topology", "exact"), value.get("state", "exact"),
                   value.get("content", "exact"))


class NodeIR(object):
    __slots__ = ("ref", "kind", "engine_name", "location", "reads", "control_reads",
                 "effects", "precision", "story_relevance", "contents", "extra")

    def __init__(self, ref=None, kind="", engine_name="", location=None, reads=(),
                 control_reads=(), effects=(), precision=None, story_relevance="unknown",
                 contents=(), extra=None):
        self.ref = ref or NodeRef()
        self.kind = str(kind or "")
        self.engine_name = str(engine_name or "")
        self.location = location or SourceLocation()
        self.reads = tuple(reads or ())
        self.control_reads = tuple(control_reads or ())
        self.effects = tuple(effects or ())
        self.precision = precision or PrecisionSet()
        self.story_relevance = story_relevance or "unknown"
        self.contents = tuple(contents or ())
        self.extra = dict(extra or {})

    def to_dict(self):
        result = {
            "graph_id": self.ref.graph_id,
            "node_id": self.ref.node_id,
            "structural_id": self.ref.structural_id,
            "content_hash": self.ref.content_hash,
            "kind": self.kind,
            "engine_name": self.engine_name,
            "location": self.location.to_dict(),
            "reads": [_json_value(item) for item in self.reads],
            "control_reads": [_json_value(item) for item in self.control_reads],
            "effects": [_json_value(item) for item in self.effects],
            "precision": self.precision.to_dict(),
            "story_relevance": self.story_relevance,
            "contents": [_json_value(item) for item in self.contents],
        }
        result.update(dict(
            (key, _json_value(value)) for key, value in self.extra.items()
        ))
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        reads = value.get("reads", [])
        controls = value.get("control_reads", [])
        if isinstance(reads, (str, bytes)):
            reads = [reads]
        if isinstance(controls, (str, bytes)):
            controls = [controls]
        known = set(("graph_id", "node_id", "structural_id", "content_hash", "kind",
                     "engine_name", "location", "reads", "control_reads", "effects",
                     "precision", "story_relevance", "contents"))
        return cls(
            NodeRef.from_dict(value), value.get("kind", ""), value.get("engine_name", ""),
            SourceLocation.from_dict(value.get("location")),
            [SymbolRef.from_dict(item) if isinstance(item, dict) else SymbolRef.unknown(str(item))
             for item in reads],
            [SymbolRef.from_dict(item) if isinstance(item, dict) else SymbolRef.unknown(str(item))
             for item in controls],
            [EffectIR.from_dict(item) for item in value.get("effects", [])],
            PrecisionSet.from_dict(value.get("precision")), value.get("story_relevance", "unknown"),
            value.get("contents", []), dict((key, value[key]) for key in value if key not in known),
        )


class EdgeIR(object):
    __slots__ = ("ref", "kind", "precision", "derivation", "metadata", "call_site")

    def __init__(self, ref=None, kind="", precision=None, derivation="", metadata=None,
                 call_site=None):
        self.ref = ref or EdgeRef()
        self.kind = str(kind or "")
        self.precision = precision or PrecisionSet()
        self.derivation = str(derivation or "")
        self.metadata = dict(metadata or {})
        self.call_site = call_site

    def to_dict(self):
        result = {
            "graph_id": self.ref.graph_id,
            "edge_id": self.ref.edge_id,
            "from": self.ref.source_node_id,
            "to": self.ref.target_node_id,
            "kind": self.kind,
            "precision": self.precision.to_dict(),
            "derivation": self.derivation,
        }
        result.update(dict(
            (key, _json_value(value)) for key, value in self.metadata.items()
        ))
        if self.call_site is not None:
            result["call"] = _json_value(self.call_site)
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        call = value.get("call")
        return cls(EdgeRef.from_dict(value), value.get("kind", ""),
                   PrecisionSet.from_dict(value.get("precision")),
                   value.get("derivation", ""),
                   dict((key, value[key]) for key in value
                        if key not in ("graph_id", "edge_id", "from", "to", "kind",
                                       "precision", "derivation", "call")),
                   CallSiteIR.from_dict(call) if isinstance(call, dict) else call)


class BlockIR(object):
    __slots__ = ("block_id", "kind", "control_kind", "entry_node", "terminator_node",
                 "location", "source_nodes", "contents", "reads", "control_reads",
                 "effects", "precision", "story_relevance", "metadata")

    def __init__(self, block_id="", kind="", control_kind="", entry_node="",
                 terminator_node="", location=None, source_nodes=(), contents=(), reads=(),
                 control_reads=(), effects=(), precision=None, story_relevance=(), metadata=None):
        self.block_id = str(block_id or "")
        self.kind = str(kind or "")
        self.control_kind = str(control_kind or "")
        self.entry_node = str(entry_node or "")
        self.terminator_node = str(terminator_node or "")
        self.location = location or SourceLocation()
        self.source_nodes = tuple(source_nodes or ())
        self.contents = tuple(contents or ())
        self.reads = tuple(reads or ())
        self.control_reads = tuple(control_reads or ())
        self.effects = tuple(effects or ())
        self.precision = precision or PrecisionSet()
        self.story_relevance = tuple(story_relevance or ())
        self.metadata = dict(metadata or {})

    def to_dict(self):
        result = {
            "block_id": self.block_id,
            "kind": self.kind,
            "control_kind": self.control_kind,
            "entry_node": self.entry_node,
            "terminator_node": self.terminator_node,
            "location": self.location.to_dict(),
            "source_nodes": list(self.source_nodes),
            "contents": [_json_value(item) for item in self.contents],
            "reads": [_json_value(item) for item in self.reads],
            "control_reads": [_json_value(item) for item in self.control_reads],
            "effects": [_json_value(item) for item in self.effects],
            "precision": self.precision.to_dict(),
            "story_relevance": list(self.story_relevance),
        }
        result.update(dict(
            (key, _json_value(value)) for key, value in self.metadata.items()
        ))
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        reads = value.get("reads", [])
        controls = value.get("control_reads", [])
        if isinstance(reads, (str, bytes)):
            reads = [reads]
        if isinstance(controls, (str, bytes)):
            controls = [controls]
        known = set(("block_id", "kind", "control_kind", "entry_node",
                     "terminator_node", "location", "source_nodes", "contents",
                     "reads", "control_reads", "effects", "precision",
                     "story_relevance"))
        metadata = dict((key, value[key]) for key in value if key not in known)
        return cls(value.get("block_id", ""), value.get("kind", ""),
                   value.get("control_kind", ""), value.get("entry_node", ""),
                   value.get("terminator_node", ""),
                   SourceLocation.from_dict(value.get("location")),
                   value.get("source_nodes", []), value.get("contents", []),
                   [SymbolRef.from_dict(item) if isinstance(item, dict) else SymbolRef.unknown(str(item))
                    for item in reads],
                   [SymbolRef.from_dict(item) if isinstance(item, dict) else SymbolRef.unknown(str(item))
                    for item in controls],
                   [EffectIR.from_dict(item) for item in value.get("effects", [])],
                   PrecisionSet.from_dict(value.get("precision")),
                   value.get("story_relevance", []), metadata)


class ScreenActionIR(object):
    """One statically described Screen Language action."""

    __slots__ = ("action_id", "kind", "guard", "target", "value", "arguments",
                 "children", "effects", "precision", "provenance", "reason")

    def __init__(self, action_id="", kind="unknown", guard=None, target=None,
                 value=None, arguments=(), children=(), effects=(), precision=None,
                 provenance="", reason=""):
        self.action_id = str(action_id or "")
        self.kind = str(kind or "unknown")
        self.guard = guard
        self.target = target
        self.value = value
        self.arguments = tuple(arguments or ())
        self.children = tuple(children or ())
        self.effects = tuple(effects or ())
        self.precision = precision or PrecisionSet()
        self.provenance = str(provenance or "")
        self.reason = str(reason or "")

    def to_dict(self):
        return {
            "action_id": self.action_id, "kind": self.kind,
            "guard": _json_value(self.guard), "target": _json_value(self.target),
            "value": _json_value(self.value),
            "arguments": [_json_value(item) for item in self.arguments],
            "children": [_json_value(item) for item in self.children],
            "effects": [_json_value(item) for item in self.effects],
            "precision": _json_value(self.precision),
            "provenance": self.provenance, "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        guard = value.get("guard")
        action_value = value.get("value")
        return cls(
            value.get("action_id", ""), value.get("kind", "unknown"),
            ExprIR.from_dict(guard) if isinstance(guard, dict) else guard,
            value.get("target"),
            ExprIR.from_dict(action_value) if isinstance(action_value, dict) else action_value,
            [ExprIR.from_dict(item) if isinstance(item, dict) else item
             for item in value.get("arguments", [])],
            [cls.from_dict(item) for item in value.get("children", [])],
            [EffectIR.from_dict(item) for item in value.get("effects", [])],
            PrecisionSet.from_dict(value.get("precision")),
            value.get("provenance", ""), value.get("reason", ""),
        )


class ScreenIR(object):
    __slots__ = ("screen_id", "name", "variant", "parameters", "location",
                 "actions", "precision", "provenance", "metadata")

    def __init__(self, screen_id="", name="", variant="", parameters=(),
                 location=None, actions=(), precision=None, provenance="", metadata=None):
        self.screen_id = str(screen_id or "")
        self.name = str(name or "")
        self.variant = str(variant or "")
        self.parameters = tuple(parameters or ())
        self.location = location or SourceLocation()
        self.actions = tuple(actions or ())
        self.precision = precision or PrecisionSet()
        self.provenance = str(provenance or "")
        self.metadata = dict(metadata or {})

    def to_dict(self):
        result = {
            "screen_id": self.screen_id, "name": self.name, "variant": self.variant,
            "parameters": [_json_value(item) for item in self.parameters],
            "location": _json_value(self.location),
            "actions": [_json_value(item) for item in self.actions],
            "precision": _json_value(self.precision), "provenance": self.provenance,
        }
        result.update(_json_value(self.metadata))
        return result

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        known = set(("screen_id", "name", "variant", "parameters", "location",
                     "actions", "precision", "provenance"))
        return cls(
            value.get("screen_id", ""), value.get("name", ""), value.get("variant", ""),
            [SymbolRef.from_dict(item) if isinstance(item, dict) else item
             for item in value.get("parameters", [])],
            SourceLocation.from_dict(value.get("location")),
            [ScreenActionIR.from_dict(item) for item in value.get("actions", [])],
            PrecisionSet.from_dict(value.get("precision")), value.get("provenance", ""),
            dict((key, value[key]) for key in value if key not in known),
        )


class ScreenAnalysis(object):
    """Static Screen Language evidence plus explicitly uncertain boundaries."""

    __slots__ = ("screens", "candidate_edges", "unknown_boundaries")

    def __init__(self, screens=(), candidate_edges=(), unknown_boundaries=()):
        self.screens = tuple(screens or ())
        self.candidate_edges = tuple(candidate_edges or ())
        self.unknown_boundaries = tuple(unknown_boundaries or ())

    def to_dict(self):
        return {
            "screens": [_json_value(item) for item in self.screens],
            "candidate_edges": [_json_value(item) for item in self.candidate_edges],
            "unknown_boundaries": [_json_value(item) for item in self.unknown_boundaries],
        }

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        return cls(
            [ScreenIR.from_dict(item) for item in value.get("screens", [])],
            [EdgeIR.from_dict(item) if isinstance(item, dict) else item
             for item in value.get("candidate_edges", [])],
            value.get("unknown_boundaries", []),
        )


class GraphIR(object):
    """Container for a typed StoryGraph schema v3 graph."""

    schema_version = 3

    __slots__ = ("graph_id", "engine", "provenance", "coverage", "audit", "entry_points",
                 "nodes", "edges", "return_sites", "blocks", "block_edges",
                 "block_return_sites", "indexes", "summary", "model", "screens",
                 "candidate_edges", "extra")

    def __init__(self, graph_id="", engine="", provenance=None, coverage=None, audit=None,
                 entry_points=None, nodes=(), edges=(), return_sites=(), blocks=(),
                 block_edges=(), block_return_sites=(), indexes=None, summary=None,
                 model=None, extra=None, screens=(), candidate_edges=()):
        self.graph_id = str(graph_id or "")
        self.engine = engine or ""
        self.provenance = dict(provenance or {})
        self.coverage = coverage or {}
        self.audit = audit or {}
        self.entry_points = dict(entry_points or {})
        self.nodes = tuple(nodes or ())
        self.edges = tuple(edges or ())
        self.return_sites = tuple(return_sites or ())
        self.blocks = tuple(blocks or ())
        self.block_edges = tuple(block_edges or ())
        self.block_return_sites = tuple(block_return_sites or ())
        self.indexes = indexes or {}
        self.summary = summary or {}
        self.model = model or {}
        self.screens = tuple(screens or ())
        self.candidate_edges = tuple(candidate_edges or ())
        self.extra = dict(extra or {})

    def to_dict(self):
        result = {
            "status": "completed",
            "engine": self.engine,
            "schema_version": 3,
            "graph_id": self.graph_id,
            "provenance": _json_value(self.provenance),
            "coverage": _json_value(self.coverage),
            "audit": _json_value(self.audit),
            "entry_points": _json_value(self.entry_points),
            "model": _json_value(self.model),
            "icfg": {
                "nodes": [_json_value(item) for item in self.nodes],
                "edges": [_json_value(item) for item in self.edges],
                "return_sites": [_json_value(item) for item in self.return_sites],
                "indexes": _json_value(self.indexes),
            },
            "narrative_graph": {
                "blocks": [_json_value(item) for item in self.blocks],
                "edges": [_json_value(item) for item in self.block_edges],
                "return_sites": [_json_value(item) for item in self.block_return_sites],
            },
            "summary": _json_value(self.summary),
            "screens": [_json_value(item) for item in self.screens],
            "candidate_edges": [_json_value(item) for item in self.candidate_edges],
        }
        reserved = set(("status", "schema_version", "engine", "graph_id", "provenance",
                        "coverage", "audit", "entry_points", "model", "icfg",
                        "narrative_graph", "summary", "screens", "candidate_edges",
                        "compatibility"))
        result.update(dict(
            (key, _json_value(value)) for key, value in self.extra.items()
            if key not in reserved
        ))
        return result


def stable_digest(value, prefix="", length=32):
    payload = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8", "surrogatepass")
    return prefix + hashlib.sha256(payload).hexdigest()[:length]
