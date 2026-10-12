"""Static Screen Language semantics and source-scan fallback evidence.

This module never evaluates Screen Python or calls Action constructors.  It
only inspects parsed syntax/data and converts recognized native actions into
typed IR.  The source scanner remains available for candidate evidence.
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
        if kind in ("SetVariable", "ToggleVariable", "SetScreenVariable", "ToggleScreenVariable", "SetLocalVariable", "ToggleLocalVariable"):
            variable = _literal_string(args[0]) if args else None
            scoped = "ScreenVariable" in kind or "LocalVariable" in kind
            target = SymbolRef.local(variable, local_owner) if scoped and variable else _symbol_from_name(variable, local_names, local_owner)
            toggle = kind.startswith("Toggle")
            if not toggle and len(args) > 1:
                value = expression(args[1])
            else:
                value = parse_expression_ir("not %s" % (variable or "__unknown__"),
                                            local_names, local_owner)
            precision = "unknown" if target.kind == "unknown" else value.precision
            effect = EffectIR(
                "toggle" if toggle else "assign", target,
                "toggle" if toggle else "set", value,
                tuple(value.reads) + ((target,) if toggle else ()),
                (target,), precision,
                "dynamic-variable-target" if target.kind == "unknown" else "",
            )
            return ScreenActionIR(
                action_id, "toggle_variable" if toggle else "set_variable",
                inherited_guard, target, value, effects=(effect,),
                precision=PrecisionSet("exact", precision, "exact"), provenance=provenance,
                reason=effect.reason,
            )
        if kind == "If" and len(args) >= 2:
            condition = expression(args[0])
            prefix = "(%s) and " % inherited_guard.source if inherited_guard else ""
            true_guard = parse_expression_ir(prefix + "(%s)" % condition.source, local_names, local_owner)
            false_guard = parse_expression_ir(prefix + "not (%s)" % condition.source,
                                              local_names, local_owner)
            children = [convert(args[1], item_path + ("true",), true_guard)]
            if len(args) >= 3:
                children.append(convert(args[2], item_path + ("false",), false_guard))
            return ScreenActionIR(
                action_id, "if", inherited_guard, arguments=(condition,), children=children,
                precision=PrecisionSet("exact", condition.precision, "exact"),
                provenance=provenance,
            )
        if kind in ("Play", "Stop", "Queue", "NullAction", "Hide", "Show", "ShowTransient",
                    "ToggleScreen", "ShowMenu", "Notify", "OpenURL"):
            return ScreenActionIR(action_id, "presentation", inherited_guard,
                                  arguments=tuple(expression(item) for item in args),
                                  precision=PrecisionSet(), provenance=provenance)
        if kind in ("MainMenu", "Quit"):
            return ScreenActionIR(action_id, "end", inherited_guard,
                                  arguments=tuple(expression(item) for item in args),
                                  precision=PrecisionSet(), provenance=provenance)
        if kind == "Rollback":
            effect = EffectIR("rollback", may_write=(SymbolRef.unknown(),), precision="conservative")
            return ScreenActionIR(action_id, "rollback", inherited_guard, effects=(effect,),
                                  precision=PrecisionSet("exact", "conservative", "exact"), provenance=provenance)
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
            "sl-ast", {"uses": list(record.get("uses", ()) or ()),
                       "parameter_declarations": list(record.get("parameter_declarations", ()) or ()),
                       "action_sources": list(record.get("actions", ()) or ())},
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
    parsed = getattr(node, "parsed", None)
    if isinstance(parsed, (tuple, list)) and len(parsed) > 1 and isinstance(parsed[1], dict) and parsed[1].get("expression"):
        from .static_values import constant_value
        name, known = constant_value(parsed[1].get("name", ""))
        if known and isinstance(name, str):
            return name, []
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


def screen_call_bindings(node, record):
    """Bind screen formals to call syntax, including closed argument unpacking."""
    from .call_semantics import argument_entries
    from .static_values import constant_value
    parsed = getattr(node, "parsed", None)
    data = parsed[1] if isinstance(parsed, (tuple, list)) and len(parsed) > 1 and isinstance(parsed[1], dict) else {}
    arguments = argument_entries(data.get("arguments"))
    if not data:
        name, _labels = call_screen(node)
        text = str(getattr(node, "line", "") or "")
        suffix = text[text.find(name) + len(name):].strip() if name else ""
        if suffix.startswith("("):
            import tokenize
            level, stop = 0, None
            for token in tokenize.generate_tokens(io.StringIO(suffix).readline):
                if token[1] == "(": level += 1
                elif token[1] == ")":
                    level -= 1
                    if not level:
                        stop = token[3][1]; break
            if stop is not None:
                call = ast.parse(name + suffix[:stop], mode="eval").body
                arguments = [(None, _source(value)) for value in call.args] + [(item.arg, _source(item.value)) for item in call.keywords]
    positional, keywords = [], {}
    for argument in arguments:
        if isinstance(argument, tuple):
            key, source = argument; unpacking = ""
        else:
            key, source, unpacking = argument.keyword, argument.expression.source, argument.unpacking
        if unpacking:
            value, known = constant_value(source)
            if not known:
                return {}, "dynamic-screen-argument-unpacking"
            if unpacking == "*" and isinstance(value, (tuple, list)):
                positional.extend(repr(item) for item in value)
            elif unpacking == "**" and isinstance(value, dict):
                keywords.update((name, repr(item)) for name, item in value.items())
            else:
                return {}, "invalid-screen-argument-unpacking"
        elif key is None:
            positional.append(source)
        else:
            if key in keywords: return {}, "duplicate-screen-argument:%s" % key
            keywords[key] = source
    declarations = record.get("parameter_declarations") or [(name, None) for name in record.get("parameters", ())]
    bindings = {}
    index, consumed = 0, set()
    for declaration in declarations:
        name, default = declaration[:2]
        kind = declaration[2] if len(declaration) > 2 else 1
        if kind == 2:
            bindings[name] = '[%s]' % ', '.join(positional[index:]); index = len(positional)
        elif kind == 4:
            bindings[name] = '{%s}' % ', '.join('%r: (%s)' % (key, source) for key, source in keywords.items() if key not in consumed)
            consumed.update(keywords)
        elif kind in (0, 1) and index < len(positional):
            if name in keywords: return bindings, "duplicate-screen-argument:%s" % name
            bindings[name] = positional[index]; index += 1
        elif kind != 0 and name in keywords:
            bindings[name] = keywords[name]; consumed.add(name)
        elif default is not None: bindings[name] = default
        else: return bindings, "missing-screen-argument:%s" % name
    if index < len(positional) or set(keywords) - consumed:
        return bindings, "unexpected-screen-arguments"
    return bindings, ""


def instantiate_screen(node, record, screen_id, call_id):
    """Specialize parsed actions for this invocation without evaluating a screen."""
    from .static_values import constant_value, substitute_source
    bindings, binding_error = screen_call_bindings(node, record)
    outcomes, unknown = [], []
    if binding_error:
        unknown.append(binding_error)
    for used in record.get("uses", ()):
        unknown.append("nested-screen-use:%s" % (used if isinstance(used, str) else "dynamic"))

    def paths(action):
        guard = action.guard.source if action.guard else "True"
        value, known = constant_value(guard)
        if known and not value:
            return []
        if action.kind == "list":
            states = [(None, guard, [])]
            for child in action.children:
                next_states = []
                for terminal, when, effects in states:
                    if terminal is not None:
                        next_states.append((terminal, when, effects)); continue
                    for end, condition, added in paths(child):
                        next_states.append((end, "(%s) and (%s)" % (when, condition), effects + added))
                states = next_states
            return states
        if action.kind == "if":
            states = [state for child in action.children for state in paths(child)]
            if len(action.children) == 1:
                states.append((None, "(%s) and not (%s)" % (guard, action.arguments[0].source), []))
            return states
        if action.kind == "unknown":
            unknown.append(action.reason or "unknown-action")
            effect = EffectIR("unknown", reason=action.reason, may_write=(SymbolRef.unknown(),), guard=action.guard)
            return [(None, guard, [effect])]
        if action.reason:
            unknown.append(action.reason)
        return [(action if action.kind in ("jump", "call", "return", "end", "rollback") else None,
                 guard, list(action.effects))]

    def loop_bindings(item):
        scopes = [bindings]
        def unpack(target, value, scope):
            if isinstance(target, ast.Name):
                scope[target.id] = repr(value)
            elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (list, tuple)) and len(target.elts) == len(value):
                for child, element in zip(target.elts, value): unpack(child, element, scope)
            else: raise ValueError("unsupported-screen-loop-target")
        for loop in item.get("loops", ()):
            expanded = []
            for scope in scopes:
                values, known = constant_value(substitute_source(loop["expression"], scope))
                if not known or not isinstance(values, (list, tuple, dict, str, range)):
                    unknown.append("dynamic-screen-loop"); return scopes
                try:
                    target = ast.parse(loop["variable"], mode="eval").body
                    for value in values:
                        bound = dict(scope); unpack(target, value, bound); expanded.append(bound)
                except (SyntaxError, ValueError):
                    unknown.append("unsupported-screen-loop-target"); return scopes
            scopes = expanded
        return scopes

    for index, item in enumerate(record.get("actions", ())):
        item = item if isinstance(item, dict) else {"source": item}
        if item.get("attribute", "action") not in ("action", "alternate"):
            continue
        for iteration, scope in enumerate(loop_bindings(item)):
            source = substitute_source(item["source"], scope)
            guard_source = substitute_source(item.get("guard") or "True", scope)
            guard = parse_expression_ir(guard_source)
            action = analyze_action_source(source, screen_id, (call_id, index, iteration), guard,
                                           (), screen_id, "sl-ast-bound")
            texts = []
            for text in item.get("texts", ()):
                when, known = constant_value(substitute_source(text.get("guard") or "True", scope))
                if known and not when: continue
                value, known = constant_value(substitute_source(text["source"], scope))
                if known and isinstance(value, str) and value not in texts: texts.append(value)
            caption = texts[0] if len(texts) == 1 else ""
            for terminal, when, effects in paths(action):
                value, known = constant_value(when)
                if known and not value: continue
                if terminal is None: continue
                outcomes.append({"action": terminal, "condition": "True" if known else when,
                                 "effects": effects, "choice": caption, "slot": len(outcomes),
                                 "screen": record["name"]})
    return outcomes, sorted(set(unknown))
