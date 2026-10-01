"""Static Screen Language semantics and source-scan fallback evidence.

This module never evaluates Screen Python or calls Action constructors.  It
only inspects parsed syntax/data and converts recognized native actions into
typed IR.  The source scanner remains available for legacy v2 evidence.
"""

import ast
import io
import os
import re

from .expressions import parse_expression_ir
from .model import (EffectIR, PrecisionSet, ScreenActionIR, ScreenAnalysis,
                    ScreenIR, SourceLocation, SymbolRef, stable_digest)


def _source(node, original=""):
    if original:
        segment = getattr(ast, "get_source_segment", lambda *_args: None)(original, node)
        if segment:
            return segment
    try:
        return ast.unparse(node)
    except Exception:
        if isinstance(node, ast.Str):
            return repr(node.s)
        if isinstance(node, ast.Name):
            return node.id
        if hasattr(ast, "Num") and isinstance(node, ast.Num):
            return repr(node.n)
        return ""


def _literal_string(node):
    if isinstance(node, ast.Str):
        return node.s
    if hasattr(ast, "Constant") and isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _symbol_from_name(name, local_names=(), local_owner=""):
    name = str(name or "")
    if name.startswith("persistent."):
        return SymbolRef.persistent(name.split(".", 1)[1])
    if name.startswith("store."):
        return SymbolRef.store(name.split(".", 1)[1])
    if name in set(local_names or ()):
        return SymbolRef.local(name, local_owner)
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return SymbolRef.store(name)
    return SymbolRef.unknown(name or "*", "unknown")


def _action_id(screen_id, path, source):
    return stable_digest({"screen": screen_id, "path": list(path), "source": source}, "A_")


def analyze_action_source(source, screen_id="", path=(), guard=None,
                          local_names=(), local_owner="", provenance="sl-ast"):
    """Parse a Screen Action expression without constructing or executing it."""
    source = str(source or "").strip()
    try:
        tree = ast.parse(source, mode="eval").body
    except Exception as error:
        return ScreenActionIR(
            _action_id(screen_id, path, source), "unknown", guard=guard,
            precision=PrecisionSet("unknown", "unknown", "exact"),
            provenance=provenance, reason="action-parse-error:%s" % error,
        )

    def expression(node):
        return parse_expression_ir(_source(node, source), local_names, local_owner)

    def convert(node, item_path, inherited_guard):
        node_source = _source(node, source)
        action_id = _action_id(screen_id, item_path, node_source)
        if isinstance(node, (ast.List, ast.Tuple)):
            children = tuple(convert(item, item_path + (index,), inherited_guard)
                             for index, item in enumerate(node.elts))
            precision = "unknown" if any(
                child.precision.topology == "unknown" for child in children
            ) else "exact"
            return ScreenActionIR(action_id, "list", inherited_guard,
                                  children=children,
                                  precision=PrecisionSet(precision, precision, "exact"),
                                  provenance=provenance)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return ScreenActionIR(
                action_id, "unknown", inherited_guard,
                arguments=(expression(node),),
                precision=PrecisionSet("unknown", "unknown", "exact"),
                provenance=provenance, reason="custom-or-dynamic-action",
            )
        kind = node.func.id
        args = list(node.args)
        if kind in ("Jump", "Call"):
            target = _literal_string(args[0]) if args else None
            arguments = tuple(expression(item) for item in args)
            exact = target is not None
            return ScreenActionIR(
                action_id, kind.lower(), inherited_guard, target, None, arguments,
                precision=PrecisionSet("exact" if exact else "unknown",
                                       "exact" if exact else "unknown", "exact"),
                provenance=provenance,
                reason="" if exact else "dynamic-action-target",
            )
        if kind == "Return":
            value = expression(args[0]) if args else parse_expression_ir("None")
            target = SymbolRef.store("_return")
            effect = EffectIR("return_value", target, "set", value, value.reads,
                              (target,), value.precision)
            return ScreenActionIR(action_id, "return", inherited_guard, value=value,
                                  effects=(effect,), precision=PrecisionSet(),
                                  provenance=provenance)
        if kind in ("SetVariable", "ToggleVariable"):
            variable = _literal_string(args[0]) if args else None
            target = _symbol_from_name(variable, local_names, local_owner)
            if kind == "SetVariable" and len(args) > 1:
                value = expression(args[1])
            else:
                value = parse_expression_ir("not %s" % (variable or "__unknown__"),
                                            local_names, local_owner)
            precision = "unknown" if target.kind == "unknown" else value.precision
            effect = EffectIR(
                "toggle" if kind == "ToggleVariable" else "assign", target,
                "toggle" if kind == "ToggleVariable" else "set", value,
                tuple(value.reads) + ((target,) if kind == "ToggleVariable" else ()),
                (target,), precision,
                "dynamic-variable-target" if target.kind == "unknown" else "",
            )
            return ScreenActionIR(
                action_id, "toggle_variable" if kind == "ToggleVariable" else "set_variable",
                inherited_guard, target, value, effects=(effect,),
                precision=PrecisionSet("exact", precision, "exact"), provenance=provenance,
                reason=effect.reason,
            )
        if kind == "If" and len(args) >= 2:
            condition = expression(args[0])
            true_guard = condition
            false_guard = parse_expression_ir("not (%s)" % condition.source,
                                              local_names, local_owner)
            children = [convert(args[1], item_path + ("true",), true_guard)]
            if len(args) >= 3:
                children.append(convert(args[2], item_path + ("false",), false_guard))
            return ScreenActionIR(
                action_id, "if", inherited_guard, arguments=(condition,), children=children,
                precision=PrecisionSet("exact", condition.precision, "exact"),
                provenance=provenance,
            )
        return ScreenActionIR(
            action_id, "unknown", inherited_guard,
            arguments=tuple(expression(item) for item in args),
            precision=PrecisionSet("unknown", "unknown", "exact"),
            provenance=provenance, reason="unsupported-action:%s" % kind,
        )

    return convert(tree, tuple(path or ()), guard)


def analyze_screen_sources(records):
    """Build ScreenIR from adapter-neutral parsed Screen Language records.

    Each record contains name/variant/location/parameters and action source
    strings.  Ren'Py-version-specific extraction belongs in renpy_adapter.
    """
    screens = []
    unknown = []
    for record in records or ():
        name = str(record.get("name", ""))
        variant = str(record.get("variant", "") or "")
        screen_id = stable_digest({"name": name, "variant": variant}, "SC_")
        parameters = tuple(record.get("parameters", ()) or ())
        local_owner = screen_id
        local_symbols = tuple(SymbolRef.local(item, local_owner) for item in parameters)
        actions = []
        screen_unknown_start = len(unknown)
        for index, action in enumerate(record.get("actions", ()) or ()):
            action_source = action.get("source", action) if isinstance(action, dict) else action
            guard_source = action.get("guard", "") if isinstance(action, dict) else ""
            guard = (parse_expression_ir(guard_source, parameters, local_owner)
                     if guard_source else None)
            item = analyze_action_source(action_source,
                                         screen_id, (index,), guard, parameters, local_owner,
                                         "sl-ast")
            actions.append(item)
            if item.kind == "unknown" or item.reason:
                unknown.append({"screen_id": screen_id, "action_id": item.action_id,
                                "reason": item.reason or "unknown-action"})
        screens.append(ScreenIR(
            screen_id, name, variant, local_symbols,
            SourceLocation.from_dict(record.get("location")), actions,
            PrecisionSet("unknown" if len(unknown) > screen_unknown_start else "exact",
                         "unknown" if len(unknown) > screen_unknown_start else "exact", "exact"),
            "sl-ast", {"uses": list(record.get("uses", ()) or ())},
        ))
    return ScreenAnalysis(screens, (), unknown)


def screen_jump_actions(renpy_module):
    declarations = re.compile(
        r"^(\s*)screen\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\s*\([^)]*\))?\s*:\s*$"
    )
    actions = re.compile(r"\b(Jump|Call)\(\s*(['\"])([^'\"]+)\2\s*\)")
    result = {}
    gamedir = getattr(getattr(renpy_module, "config", None), "gamedir", "")
    for root, _dirs, files in os.walk(gamedir):
        for filename in files:
            if not filename.lower().endswith(".rpy"):
                continue
            path = os.path.join(root, filename)
            try:
                with io.open(path, "r", encoding="utf-8", errors="replace") as stream:
                    lines = stream.readlines()
                if lines and lines[0].startswith(u"\ufeff"):
                    lines[0] = lines[0][1:]
            except (OSError, IOError):
                continue
            index = 0
            while index < len(lines):
                match = declarations.match(lines[index].rstrip("\r\n"))
                index += 1
                if match is None:
                    continue
                indent = len(match.group(1).expandtabs(8))
                screen_name = match.group(2)
                conditions = []
                branches = {}
                found = result.setdefault(screen_name, [])
                while index < len(lines):
                    line = lines[index]
                    stripped = line.strip()
                    body_indent = len(line.expandtabs(8)) - len(line.lstrip(" \t").expandtabs(8))
                    if stripped and not stripped.startswith("#") and body_indent <= indent:
                        break
                    index += 1
                    if not stripped or stripped.startswith("#"):
                        continue
                    while conditions and conditions[-1][0] >= body_indent:
                        conditions.pop()
                    conditional = re.match(r"(?:if|elif)\s+(.+):\s*$", stripped)
                    if conditional is not None:
                        condition = conditional.group(1)
                        if stripped.startswith("elif "):
                            earlier = branches.get(body_indent, [])
                            condition = "not any([%s]) and (%s)" % (
                                ", ".join("(" + item + ")" for item in earlier),
                                condition,
                            )
                            branches.setdefault(body_indent, []).append(conditional.group(1))
                        else:
                            branches[body_indent] = [condition]
                        conditions.append((body_indent, condition))
                        continue
                    if re.match(r"else\s*:\s*$", stripped):
                        earlier = branches.get(body_indent, [])
                        condition = "not any([%s])" % ", ".join(
                            "(" + item + ")" for item in earlier
                        )
                        conditions.append((body_indent, condition))
                        continue
                    for action in actions.finditer(line):
                        record = {
                            "action": action.group(1).upper(),
                            "label": action.group(3),
                            "condition": " and ".join(
                                "(" + item[1] + ")" for item in conditions
                            ) or "True",
                        }
                        if record not in found:
                            found.append(record)
    return result


def call_screen(node, lookup=None):
    source_text = str(getattr(node, "line", "") or "").strip()
    match = re.match(r"call\s+screen\s+([A-Za-z_][A-Za-z0-9_]*)\b", source_text)
    if match is None:
        return None, []
    labels = []
    for string in re.finditer(r"(['\"])([^'\"]+)\1", source_text):
        label = string.group(2)
        if lookup is None or lookup(label) is not None:
            if label not in labels:
                labels.append(label)
    return match.group(1), labels
