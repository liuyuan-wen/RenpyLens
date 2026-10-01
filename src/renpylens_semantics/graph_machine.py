"""Shared Shadow Machine migrated from the live Hook.

The implementation intentionally preserves the stage-0 predictor contract;
semantic and IR improvements are deferred to later stages.
"""


from .call_semantics import CallPlanError, evaluate_call, formal_entries, plan_call


def _debug_value(value, limit=160):
    try:
        text = str(value)
    except Exception:
        text = "<unprintable>"
    text = text.replace("\n", " ").replace("\r", " ")
    return text[:limit]

def _default_node_debug(node):
    if node is None:
        return {
            "type": "",
            "name": "",
            "filename": "",
            "line": "",
        }
    return {
        "type": node.__class__.__name__,
        "name": _debug_value(getattr(node, "name", "")),
        "filename": _debug_value(getattr(node, "filename", "")),
        "line": _debug_value(getattr(node, "linenumber", "")),
    }

def _default_menu_id(node):
    if node is None:
        return ""
    name = getattr(node, "name", None)
    if name:
        return _debug_value(name)
    filename = _debug_value(getattr(node, "filename", ""))
    line = _debug_value(getattr(node, "linenumber", ""))
    if not filename and not line:
        return ""
    return "%s:%s" % (filename, line)

def _default_format_text(renpy, source, resolve):
    if "[" not in source:
        return source
    substitutions = getattr(renpy, "substitutions", None)
    parser = getattr(substitutions, "parse", None)
    legacy = parser is None
    if legacy:
        parser = getattr(getattr(substitutions, "formatter", None), "parse", None)
    if parser is None:
        raise ValueError("substitution-parser-unavailable")
    parts = []
    for literal, code, conversion, spec in parser(source):
        if legacy:
            conversion, spec = spec, conversion
        parts.append(literal)
        if code is not None:
            parts.append(resolve(code, conversion, spec))
    return "".join(parts)

def _default_strip_text_tags(cleaned):
    import re as _tre
    for _ in range(3):
        new_cleaned = _tre.sub(r"\{[^{}]*\}", "", cleaned)
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned

    cleaned = _tre.sub(
        r"\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?",
        "",
        cleaned,
        flags=_tre.IGNORECASE,
    )
    return cleaned

def _default_character_id(expression, previous=""):
    """Preserve expressions, but keep literal speakers in the name namespace."""
    if not expression or expression == "narrator":
        return ""
    if expression == "extend":
        return previous
    import ast
    try:
        ast.literal_eval(expression)
    except (ValueError, SyntaxError):
        return expression
    return ""


# Compatibility aliases are kept for tests and legacy callers that extract
# the Hook's text-cleaning helper.  Predictor calls bind per-run services
# locally, so these defaults do not carry mutable runtime state.
_translator_format_text = _default_format_text
_translator_strip_text_tags = _default_strip_text_tags

def predict(
    renpy,
    current,
    limit=60,
    branch_limit=None,
    max_steps=1500,
    time_budget=0.05,
    choice_selector=None,
    input_provider=None,
    screen_selector=None,
    random_provider=None,
    stop_selector=None,
    state_names=None,
    label_observer=None,
    services=None,
):
    """Interpret a bounded script prefix without executing game Python.

    Only plain data and explicitly supported syntax enter the shadow store.
    Unknown operations end the prefix; they must never be skipped while
    continuing to report subsequent dialogue as belonging to this route.
    """
    import ast
    import operator
    import re
    import time
    try:
        import __builtin__ as builtin
    except ImportError:
        import builtins as builtin
    services = services or {}
    _translator_node_debug = services.get("node_debug", _default_node_debug)
    _translator_legacy_menu_id = services.get("menu_id", _default_menu_id)
    _translator_node_ref = services.get("node_ref")
    _translator_edge_id = services.get("edge_id")
    _translator_format_text = services.get("format_text", _default_format_text)
    _translator_strip_text_tags = services.get("strip_text_tags", _default_strip_text_tags)
    _translator_character_id = services.get("character_id", _default_character_id)
    _translator_prefetch_count = services.get("prefetch_count", 10)

    def canonical_node_id(node):
        if _translator_node_ref is not None:
            try:
                value = _translator_node_ref(node)
                candidate = ""
                if hasattr(value, "node_id") and value.node_id:
                    candidate = value.node_id
                elif isinstance(value, dict) and value.get("node_id"):
                    candidate = str(value["node_id"])
                elif isinstance(value, str) and value:
                    candidate = value
                if re.match(r"^N_[0-9a-f]{32}$", str(candidate or "")):
                    return candidate
            except Exception:
                pass
        return _translator_legacy_menu_id(node)

    def canonical_edge_id(source, target, kind, slot=""):
        if _translator_edge_id is not None:
            try:
                value = _translator_edge_id(source, target, kind, slot)
                if value:
                    return str(value)
            except Exception:
                pass
        return ""

    def _translator_menu_id(node):
        return canonical_node_id(node)

    def debug_node(node):
        result = _translator_node_debug(node)
        if not isinstance(result, dict):
            result = {"value": result}
        try:
            value = _translator_node_ref(node) if _translator_node_ref is not None else None
            if hasattr(value, "to_dict"):
                value = value.to_dict()
            if isinstance(value, dict):
                for key in ("graph_id", "node_id", "structural_id", "content_hash"):
                    if value.get(key):
                        result[key] = value[key]
        except Exception:
            pass
        return result

    def code_filename(code):
        filename = getattr(code, "co_filename", "") if code else ""
        if not filename:
            return ""
        filename = filename.replace("\\", "/")
        if filename.endswith((".pyc", ".pyo", ".rpyc")):
            filename = filename[:-1]
        return filename

    # Ren'Py replaces store.list/dict/set with revertable subclasses. Type
    # checks must also recognize ordinary objects from engine modules.
    list, tuple, dict, set = builtin.list, builtin.tuple, builtin.dict, builtin.set
    str, int, float, bool, object = builtin.str, builtin.int, builtin.float, builtin.bool, builtin.object
    len, abs, min, max, round, any, all = builtin.len, builtin.abs, builtin.min, builtin.max, builtin.round, builtin.any, builtin.all

    clock = getattr(time, "perf_counter", time.time)
    started = clock()
    slice_started = [started]
    slice_steps = [0]
    steps = [0]
    memo = {}
    shadow = {}
    last_message = services.get("last_current_msg") or {}
    speaker_state = [last_message.get("character_id", ""), last_message.get("who", "")]
    audio_scope = [False]
    missing = object()
    upcoming = []
    branches = []
    continuations = []
    node = current if type(current).__name__ == "Menu" else getattr(current, "next", None)
    debug = {
        "stop_reason": "next-none",
        "visited_nodes": 0,
        "prefetch_items": 0,
        "route_choices": [],
        "route_inputs": [],
        "route_screens": [],
        "route_random": [],
    }
    if branch_limit is None:
        # A final fixed-size batch may deliberately overfill the last
        # first-level branch by N-1 items.
        branch_limit = max(1, _translator_prefetch_count * 2 - 1)
    branch_limit = max(0, int(branch_limit))
    string_types = (str,)
    for extra_name in ("unicode", "basestring"):
        extra = getattr(builtin, extra_name, None)
        if extra is not None and extra not in string_types:
            string_types += (extra,)
    try:
        from future.types.newstr import newstr
        from future.types.newbytes import newbytes
        string_types += (newstr, newbytes)
    except Exception:
        pass

    number_types = (int, float, bool)
    extra_long = getattr(builtin, "long", None)
    if extra_long is not None and extra_long not in number_types:
        number_types += (extra_long,)
    try:
        from future.types.newint import newint
        number_types += (newint,)
    except Exception:
        pass

    scalar_types = string_types + number_types + (type(None),)
    containers = {list: list, tuple: tuple, dict: dict, set: set}
    try:
        from future.types.newdict import newdict
        from future.types.newlist import newlist
        containers[newdict] = dict
        containers[newlist] = list
    except Exception:
        pass
    for module_name in ("revertable", "python"):
        module = getattr(renpy, module_name, None)
        for name, base in (("RevertableList", list), ("RevertableDict", dict), ("RevertableSet", set)):
            kind = getattr(module, name, None)
            if kind is not None:
                containers[kind] = base

    class Stop(Exception):
        pass

    def tick():
        steps[0] += 1
        slice_steps[0] += 1
        if slice_steps[0] > max_steps or clock() - slice_started[0] > time_budget:
            raise Stop("work-budget")

    def reset_slice():
        slice_steps[0] = 0
        slice_started[0] = clock()

    def clone(value):
        tick()
        kind = type(value)
        if kind in scalar_types:
            return value
        if id(value) in memo:
            return memo[id(value)]
        base = containers.get(kind)
        if base is None:
            raise Stop("unsupported-value")
        if len(value) > 4096:
            raise Stop("value-budget")
        result = {} if base is dict else [] if base in (list, tuple) else set()
        memo[id(value)] = result
        if base is dict:
            for key, item in value.items():
                result[clone(key)] = clone(item)
        else:
            for item in value:
                if base is set:
                    result.add(clone(item))
                else:
                    result.append(clone(item))
        if base is tuple:
            result = tuple(result)
            memo[id(value)] = result
        memo[id(result)] = result
        return result

    def fork_value(value, copies=None):
        """Clone prediction-owned data while retaining immutable AST nodes."""
        if copies is None:
            copies = {}
        if value is missing or type(value) in scalar_types:
            return value
        value_id = id(value)
        if value_id in copies:
            return copies[value_id]
        base = containers.get(type(value))
        if base is None:
            return value
        result = {} if base is dict else [] if base in (list, tuple) else set()
        copies[value_id] = result
        if base is dict:
            for key, item in value.items():
                result[fork_value(key, copies)] = fork_value(item, copies)
        else:
            for item in value:
                if base is set:
                    result.add(fork_value(item, copies))
                else:
                    result.append(fork_value(item, copies))
        if base is tuple:
            result = tuple(result)
            copies[value_id] = result
        return result

    def read(name):
        if name not in shadow:
            if name.startswith("persistent."):
                shadow[name] = clone(renpy.store.persistent.__dict__.get(name[11:], None))
            else:
                shadow[name] = clone(renpy.store.__dict__[name])
        if shadow[name] is missing:
            raise Stop("missing-variable")
        return shadow[name]

    assignment_target = [None]

    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
              ast.Div: operator.truediv, ast.BitAnd: operator.and_, ast.BitOr: operator.or_}
    comparisons = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                   ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
                   ast.Is: operator.is_, ast.IsNot: operator.is_not,
                   ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}

    def operation(op, left, right):
        if isinstance(op, ast.Mult):
            sequence, count = (left, right) if isinstance(right, number_types) else (right, left)
            if isinstance(sequence, (list, tuple) + string_types) and len(sequence) * abs(count) > 4096:
                raise Stop("value-budget")
        if isinstance(op, ast.Mod) and isinstance(left, string_types):
            raise Stop("unsupported-format-operation")
        if type(op) not in binary:
            raise Stop("unsupported-operator")
        value = binary[type(op)](left, right)
        if isinstance(value, (list, tuple) + string_types) and len(value) > 4096:
            raise Stop("value-budget")
        return value

    def expression(tree):
        tick()
        kind = type(tree).__name__
        if kind in ("Constant", "Num", "Str", "NameConstant"):
            return clone(getattr(tree, "value", getattr(tree, "n", getattr(tree, "s", None))))
        if isinstance(tree, ast.Name):
            if tree.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[tree.id]
            if audio_scope[0]:
                audio = getattr(renpy.store, "audio", None)
                if audio is not None and tree.id in audio.__dict__:
                    return clone(audio.__dict__[tree.id])
            return read(tree.id)
        if isinstance(tree, (ast.List, ast.Tuple, ast.Set)):
            values = [expression(x) for x in tree.elts]
            return tuple(values) if isinstance(tree, ast.Tuple) else set(values) if isinstance(tree, ast.Set) else values
        if isinstance(tree, ast.Dict):
            return dict((expression(k), expression(v)) for k, v in zip(tree.keys, tree.values))
        if isinstance(tree, ast.BinOp):
            return operation(tree.op, expression(tree.left), expression(tree.right))
        if isinstance(tree, ast.UnaryOp):
            ops = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}
            return ops[type(tree.op)](expression(tree.operand))
        if isinstance(tree, ast.BoolOp):
            for part in tree.values:
                value = expression(part)
                if isinstance(tree.op, ast.And) and not value or isinstance(tree.op, ast.Or) and value:
                    return value
            return value
        if isinstance(tree, ast.Compare):
            left = expression(tree.left)
            for op, part in zip(tree.ops, tree.comparators):
                right = expression(part)
                if not comparisons[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(tree, ast.IfExp):
            return expression(tree.body if expression(tree.test) else tree.orelse)
        if isinstance(tree, ast.Subscript):
            return expression(tree.value)[expression(tree.slice)]
        if isinstance(tree, ast.Attribute) and isinstance(tree.value, ast.Name) and tree.value.id == "persistent" and not tree.attr.startswith("_"):
            return read("persistent." + tree.attr)
        if (isinstance(tree, ast.Attribute) and isinstance(tree.value, ast.Name)
                and tree.value.id in ("preferences", "config")
                and not tree.attr.startswith("_")):
            root_name = tree.value.id
            runtime = renpy.store.__dict__.get(root_name)
            native = (getattr(renpy.game, "preferences", None) if root_name == "preferences"
                      else getattr(renpy, "config", None))
            values = getattr(runtime, "__dict__", {})
            if runtime is not native or tree.attr not in values:
                raise Stop("custom-engine-object")
            return clone(values[tree.attr])
        if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name):
            pure = {"len": len, "abs": abs, "min": min, "max": max, "int": int,
                    "float": float, "str": str, "bool": bool, "round": round,
                    "any": any, "all": all}
            function = pure.get(tree.func.id)
            if function is not None and not tree.keywords and not getattr(tree, "starargs", None) and not getattr(tree, "kwargs", None):
                if tree.func.id in shadow or (tree.func.id in renpy.store.__dict__ and renpy.store.__dict__[tree.func.id] is not function):
                    raise Stop("overridden-builtin")
                return function(*[expression(arg) for arg in tree.args])
            if tree.func.id == "clamp":
                candidate = renpy.store.__dict__.get("clamp")
                code = getattr(candidate, "__code__", getattr(candidate, "func_code", None))
                names = set(getattr(code, "co_names", ())) if code else set()
                globals_dict = getattr(candidate, "__globals__", getattr(candidate, "func_globals", {}))
                if (candidate is None or code is None
                        or int(getattr(code, "co_argcount", -1)) != 3
                        or names - set(("min", "max"))
                        or globals_dict.get("min", min) is not min
                        or globals_dict.get("max", max) is not max
                        or tree.keywords
                        or getattr(tree, "starargs", None)
                        or getattr(tree, "kwargs", None)
                        or len(tree.args) != 3):
                    raise Stop("unsupported-clamp")
                value, lower, upper = [expression(arg) for arg in tree.args]
                return max(lower, min(value, upper))
        if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Attribute):
            random_namespace = tree.func.value
            if (isinstance(random_namespace, ast.Attribute)
                    and isinstance(random_namespace.value, ast.Name)
                    and random_namespace.value.id == "renpy"
                    and random_namespace.attr == "random"
                    and tree.func.attr == "randint"):
                if random_provider is None:
                    raise Stop("random-boundary")
                store_renpy = renpy.store.__dict__.get("renpy", None)
                runtime_rng = getattr(store_renpy, "random", None)
                rollback = getattr(renpy, "rollback", None)
                native_rng = getattr(rollback, "rng", None)
                if (runtime_rng is None or runtime_rng is not native_rng
                        or "renpy" in shadow
                        or len(tree.args) != 2 or tree.keywords
                        or getattr(tree, "starargs", None)
                        or getattr(tree, "kwargs", None)):
                    raise Stop("custom-random-handler")
                lower, upper = [expression(arg) for arg in tree.args]
                if not isinstance(lower, int) or not isinstance(upper, int):
                    raise Stop("unsupported-random-arguments")
                value = random_provider({
                    "method": "randint", "args": [lower, upper],
                })
                if not isinstance(value, int) or value < lower or value > upper:
                    raise Stop("unsupported-random-value")
                debug["route_random"].append({
                    "method": "randint", "args": [lower, upper], "value": value,
                })
                return value
            if (isinstance(tree.func.value, ast.Name)
                    and tree.func.value.id == "renpy"
                    and tree.func.attr == "input"):
                if input_provider is None:
                    raise Stop("interactive-input")
                exports_module = getattr(renpy, "exports", None)
                native = getattr(exports_module, "input", None)
                store_renpy = renpy.store.__dict__.get("renpy", None)
                runtime_input = getattr(store_renpy, "input", None)
                code = getattr(runtime_input, "__code__", getattr(runtime_input, "func_code", None))
                filename = code_filename(code)
                if (runtime_input is None or runtime_input is not native
                        or not filename.endswith(("renpy/exports/inputexports.py", "renpy/exports.py"))
                        or "renpy" in shadow):
                    raise Stop("custom-input-handler")
                if (getattr(tree, "starargs", None)
                        or getattr(tree, "kwargs", None)
                        or any(keyword.arg is None for keyword in tree.keywords)):
                    raise Stop("unsupported-input-arguments")
                args = [expression(arg) for arg in tree.args]
                kwargs = dict((keyword.arg, expression(keyword.value)) for keyword in tree.keywords)
                if not args and "prompt" not in kwargs:
                    raise Stop("unsupported-input-arguments")
                prompt = args[0] if args else kwargs.get("prompt", "")
                default = args[1] if len(args) > 1 else kwargs.get("default", "")
                value = input_provider({
                    "prompt": str(prompt),
                    "default": str(default or ""),
                    "target": assignment_target[0],
                })
                if not isinstance(value, string_types):
                    raise Stop("unsupported-input-value")
                debug["route_inputs"].append({
                    "prompt": str(prompt),
                    "default": str(default or ""),
                    "value": value,
                })
                return value
            if tree.func.attr in ("strip", "lstrip", "rstrip"):
                if (tree.keywords or getattr(tree, "starargs", None)
                        or getattr(tree, "kwargs", None) or len(tree.args) > 1):
                    raise Stop("unsupported-string-method")
                value = expression(tree.func.value)
                if not isinstance(value, string_types):
                    raise Stop("unsupported-string-method")
                args = [expression(arg) for arg in tree.args]
                return getattr(value, tree.func.attr)(*args)
            if tree.func.attr in ("lower", "upper"):
                if (tree.args or tree.keywords or getattr(tree, "starargs", None)
                        or getattr(tree, "kwargs", None)):
                    raise Stop("unsupported-string-method")
                value = expression(tree.func.value)
                if not isinstance(value, string_types):
                    raise Stop("unsupported-string-method")
                return getattr(value, tree.func.attr)()
        if kind == "Index":
            return expression(tree.value)
        if isinstance(tree, ast.Slice):
            return slice(*[expression(x) if x is not None else None for x in (tree.lower, tree.upper, tree.step)])
        # Never invoke even a seemingly harmless game function: its globals
        # and closures still refer to the real store.
        raise Stop("unsupported-expression:" + kind)

    def evaluate(source):
        return expression(ast.parse(source, mode="eval").body)

    def assign(target, value):
        if isinstance(target, ast.Name):
            shadow[target.id] = value
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "persistent" and not target.attr.startswith("_"):
            shadow["persistent." + target.attr] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            if len(target.elts) != len(value):
                raise Stop("unpack-mismatch")
            for item, part in zip(target.elts, value):
                assign(item, part)
        elif isinstance(target, ast.Subscript):
            expression(target.value)[expression(target.slice)] = value
        else:
            raise Stop("unsupported-assignment")

    def statements(body):
        for statement in body:
            tick()
            if isinstance(statement, ast.Assign):
                assignment_target[0] = (
                    statement.targets[0].id
                    if len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    else None
                )
                try:
                    value = expression(statement.value)
                finally:
                    assignment_target[0] = None
                for target in statement.targets:
                    assign(target, value)
            elif isinstance(statement, ast.AugAssign):
                old = expression(statement.target)
                value = operation(statement.op, old, expression(statement.value))
                if isinstance(old, list) and isinstance(statement.op, (ast.Add, ast.Mult)):
                    old[:] = value
                    value = old
                assign(statement.target, value)
            elif isinstance(statement, ast.If):
                statements(statement.body if expression(statement.test) else statement.orelse)
            elif isinstance(statement, ast.Pass):
                pass
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                function = call.func
                namespace = function.value if isinstance(function, ast.Attribute) else None
                if (isinstance(namespace, ast.Name)
                        and namespace.id == "renpy"
                        and function.attr == "end_replay"):
                    exports_module = getattr(renpy, "exports", None)
                    native = getattr(exports_module, "end_replay", None)
                    store_renpy = renpy.store.__dict__.get("renpy", None)
                    runtime = getattr(store_renpy, "end_replay", None)
                    code = getattr(runtime, "__code__", getattr(runtime, "func_code", None))
                    filename = code_filename(code)
                    if (runtime is None or runtime is not native
                            or not filename.endswith(("renpy/exports/contextexports.py", "renpy/exports.py"))
                            or call.args or call.keywords
                            or getattr(call, "starargs", None)
                            or getattr(call, "kwargs", None)
                            or "renpy" in shadow):
                        raise Stop("custom-end-replay-handler")
                    if read("_in_replay"):
                        raise Stop("replay-boundary")
                    continue
                if (isinstance(namespace, ast.Name)
                        and namespace.id == "renpy"
                        and function.attr == "pause"):
                    exports_module = getattr(renpy, "exports", None)
                    native = getattr(exports_module, "pause", None)
                    store_renpy = renpy.store.__dict__.get("renpy", None)
                    pause = getattr(store_renpy, "pause", None)
                    code = getattr(pause, "__code__", getattr(pause, "func_code", None))
                    filename = code_filename(code)
                    if (pause is None or pause is not native
                            or not filename.endswith(("renpy/exports/statementexports.py", "renpy/exports.py"))
                            or "renpy" in shadow):
                        raise Stop("custom-pause-handler")
                    if (getattr(call, "starargs", None)
                            or getattr(call, "kwargs", None)
                            or any(keyword.arg is None for keyword in call.keywords)):
                        raise Stop("unsupported-pause-arguments")
                    import inspect
                    try:
                        inspect.getcallargs(
                            pause,
                            *([None] * len(call.args)),
                            **dict((keyword.arg, None) for keyword in call.keywords)
                        )
                    except TypeError:
                        raise Stop("unsupported-pause-arguments")
                    for argument in call.args:
                        expression(argument)
                    for keyword in call.keywords:
                        expression(keyword.value)
                    continue
                if (isinstance(namespace, ast.Name)
                        and namespace.id == "renpy"
                        and function.attr == "block_rollback"):
                    exports_module = getattr(renpy, "exports", None)
                    native = getattr(exports_module, "block_rollback", None)
                    store_renpy = renpy.store.__dict__.get("renpy", None)
                    runtime = getattr(store_renpy, "block_rollback", None)
                    code = getattr(runtime, "__code__", getattr(runtime, "func_code", None))
                    filename = code_filename(code)
                    if (runtime is None or runtime is not native
                            or not filename.endswith(("renpy/exports/rollbackexports.py", "renpy/exports.py"))
                            or call.args or call.keywords
                            or getattr(call, "starargs", None)
                            or getattr(call, "kwargs", None)
                            or "renpy" in shadow):
                        raise Stop("custom-block-rollback-handler")
                    continue
                if (isinstance(namespace, ast.Name)
                        and namespace.id == "achievement"
                        and function.attr in ("grant", "sync", "register", "clear", "clear_all", "progress", "has")):
                    if (getattr(call, "starargs", None)
                            or getattr(call, "kwargs", None)
                            or any(keyword.arg is None for keyword in call.keywords)):
                        raise Stop("unsupported-achievement-arguments")
                    for argument in call.args:
                        expression(argument)
                    for keyword in call.keywords:
                        expression(keyword.value)
                    continue
                elif (not isinstance(namespace, ast.Attribute)
                        or not isinstance(namespace.value, ast.Name)
                        or namespace.value.id != "renpy"
                        or namespace.attr not in ("sound", "music")
                        or function.attr not in ("play", "queue", "stop", "set_volume", "set_pan", "set_pause")):
                    raise Stop("unsupported-python:Expr")
                store_renpy = renpy.store.__dict__.get("renpy", None)
                module = getattr(store_renpy, namespace.attr, None)
                if module is None:
                    module = getattr(renpy, namespace.attr, None)
                native = getattr(getattr(renpy, "audio", None), namespace.attr, None)
                stop = getattr(module, function.attr, None)
                code = getattr(stop, "__code__", getattr(stop, "func_code", None))
                filename = code_filename(code)
                if (stop is None or stop is not getattr(native, function.attr, None)
                        or not filename.endswith(("renpy/audio/sound.py", "renpy/audio/music.py"))
                        or "renpy" in shadow):
                    raise Stop("custom-audio-handler")
                if (getattr(call, "starargs", None)
                        or getattr(call, "kwargs", None)
                        or any(keyword.arg is None for keyword in call.keywords)):
                    raise Stop("unsupported-audio-arguments")
                import inspect
                try:
                    inspect.getcallargs(stop, *([None] * len(call.args)),
                                        **dict((keyword.arg, None) for keyword in call.keywords))
                except TypeError:
                    raise Stop("unsupported-audio-arguments")
                # Validate only the data arguments. Never stop real audio
                # while predicting the dialogue after this statement.
                for argument in call.args:
                    expression(argument)
                for keyword in call.keywords:
                    expression(keyword.value)
            elif isinstance(statement, ast.Expr):
                expression(statement.value)
            else:
                raise Stop("unsupported-python:" + type(statement).__name__)

    def text_value(source, strip=True, translate=False):
        if translate:
            translator = getattr(getattr(renpy, "translation", None), "translate_string", None)
            if translator is not None:
                source = translator(source)
        def resolve(code, conversion, spec):
            if spec or conversion and conversion != "s":
                raise Stop("unsupported-substitution-format")
            return str(evaluate(code))
        # Share syntax handling, while evaluating only in the shadow store.
        try:
            source = _translator_format_text(renpy, source, resolve)
        except ValueError as error:
            raise Stop(str(error))
        source = _translator_strip_text_tags(source)
        return source.strip() if strip else source

    def resolve_scene_text(statement):
        if type(statement).__name__ not in ("Show", "Scene"):
            return None
        imspec = getattr(statement, "imspec", None)
        if not imspec or imspec[1] is not None:
            return None
        name = tuple(imspec[0])
        images = getattr(getattr(getattr(renpy, "display", None), "image", None), "images", {})
        native = getattr(getattr(getattr(renpy, "text", None), "extras", None), "ParameterizedText", None)
        # Resolve the longest registered image name just as image lookup
        # does. Read its argument in the shadow store; never construct it.
        for length in range(len(name), 0, -1):
            image = images.get(name[:length])
            if image is None:
                continue
            if native is None or type(image) is not native or length == len(name):
                return None
            value = evaluate("".join(name[length:]))
            if not isinstance(value, string_types):
                raise Stop("unsupported-scene-text")
            return {"who": "", "what": text_value(value, translate=True), "italic": False}
        return None

    def unwrap_curried(candidate):
        curry_module = getattr(renpy, "curry", None)
        partial_type = getattr(curry_module, "Partial", None)
        curry_type = getattr(curry_module, "Curry", None)
        if partial_type is not None and type(candidate) is partial_type:
            if (candidate.func is partial_type and len(candidate.args) == 1
                    and not candidate.keywords):
                return candidate.args[0]
        elif curry_type is not None and type(candidate) is curry_type:
            if (candidate.callable is curry_type and len(candidate.args) == 1
                    and not candidate.kwargs):
                return candidate.args[0]
        return candidate

    def validate_transition(source):
        def transition_value(part):
            if isinstance(part, (ast.Tuple, ast.List)):
                for item in part.elts:
                    transition_value(item)
                return
            if not isinstance(part, ast.Call):
                expression(part)
                return
            # Native transition constructors affect presentation only.
            # Check identity and arguments without constructing them.
            name = part.func.id if isinstance(part.func, ast.Name) else None
            native = getattr(getattr(getattr(renpy, "display", None), "transition", None), name or "", None)
            constructor = renpy.store.__dict__.get(name)
            constructor = unwrap_curried(constructor)
            if (name not in ("Dissolve", "Fade", "Pixellate", "ImageDissolve", "CropMove", "MultipleTransition",
                             "AlphaDissolve", "PushMove", "ComposeTransition", "SubTransition")
                    or native is None or constructor is not native
                    or name in shadow):
                raise Stop("dynamic-transition")
            for argument in part.args:
                transition_value(argument)
            for keyword in part.keywords:
                transition_value(keyword.value)

        tree = ast.parse(source, mode="eval").body
        if any(isinstance(part, ast.Call) for part in ast.walk(tree)):
            transition_value(tree)

    def validate_image_transform(source):
        def transform_value(part):
            if isinstance(part, (ast.Tuple, ast.List)):
                for item in part.elts:
                    transform_value(item)
                return
            if not isinstance(part, ast.Call):
                expression(part)
                return

            name = part.func.id if isinstance(part.func, ast.Name) else None
            native_paths = {
                "Alpha": ("layout", "Alpha"),
                "Position": ("layout", "Position"),
                "Pan": ("motion", "Pan"),
                "Move": ("motion", "Move"),
                "Motion": ("motion", "Motion"),
                "Revolve": ("motion", "Revolve"),
                "Zoom": ("motion", "Zoom"),
                "RotoZoom": ("motion", "RotoZoom"),
                "FactorZoom": ("motion", "FactorZoom"),
                "SizeZoom": ("motion", "SizeZoom"),
                "Transform": ("transform", "Transform"),
                "Camera": ("transform", "Camera"),
            }
            path = native_paths.get(name)
            display = getattr(renpy, "display", None)
            module = getattr(display, path[0], None) if path else None
            native = getattr(module, path[1], None) if path else None
            constructor = unwrap_curried(renpy.store.__dict__.get(name))
            if (native is None or constructor is not native or name in shadow
                    or getattr(part, "starargs", None)
                    or getattr(part, "kwargs", None)
                    or any(keyword.arg is None for keyword in part.keywords)):
                raise Stop("dynamic-image-transform")
            for argument in part.args:
                transform_value(argument)
            for keyword in part.keywords:
                transform_value(keyword.value)

        tree = ast.parse(source, mode="eval").body
        if any(isinstance(part, ast.Call) for part in ast.walk(tree)):
            transform_value(tree)

    def passive_statement(statement):
        parsed = getattr(statement, "parsed", None)
        if not parsed:
            raise Stop("unparsed-user-statement")
        name = tuple(parsed[0])
        if name in (("show", "screen"), ("hide", "screen"), ("call", "screen")):
            execute = renpy.statements.get("execute", parsed)
            code = getattr(execute, "__code__", getattr(execute, "func_code", None))
            filename = code_filename(code)
            if not filename.endswith("renpy/common/000statements.rpy"):
                raise Stop("custom-statement-handler")
            data = parsed[1]
            if not isinstance(data, dict):
                raise Stop("unsupported-statement-data")

            screen_name = data.get("name")
            if data.get("expression", False):
                screen_name = evaluate(screen_name)
            if not isinstance(screen_name, string_types):
                raise Stop("unsupported-screen-name")

            arguments = data.get("arguments")
            positional, keywords = [], {}
            if arguments is not None:
                if (getattr(arguments, "starred_indexes", None)
                        or getattr(arguments, "doublestarred_indexes", None)
                        or getattr(arguments, "extrapos", None)
                        or getattr(arguments, "extrakw", None)):
                    raise Stop("unsupported-argument-unpacking")
                for key, source in arguments.arguments:
                    value = evaluate(source)
                    if key is None:
                        positional.append(value)
                    else:
                        keywords[key] = value
            if data.get("zorder") is not None:
                evaluate(data["zorder"])
            if data.get("transition_expr") is not None:
                validate_transition(data["transition_expr"])
            if name == ("call", "screen"):
                if screen_selector is None:
                    raise Stop("interactive-screen")
                selection = screen_selector({
                    "name": screen_name,
                    "args": positional,
                    "kwargs": keywords,
                    "evaluate": evaluate,
                })
                if not isinstance(selection, dict):
                    raise Stop("unsupported-screen-selection")
                record = {"name": screen_name}
                node_id = canonical_node_id(statement)
                if node_id.startswith("N_"):
                    record["node_id"] = node_id
                if "jump" in selection:
                    target = selection["jump"]
                    if not isinstance(target, string_types):
                        raise Stop("unsupported-screen-selection")
                    try:
                        label = renpy.game.script.lookup(target)
                    except Exception:
                        raise Stop("unsupported-screen-selection")
                    record["jump"] = target
                    debug["route_screens"].append(record)
                    return label
                if "value" in selection:
                    shadow["_return"] = clone(selection["value"])
                    record["value"] = selection["value"]
                    debug["route_screens"].append(record)
                    # A call-screen value returns to the statement's
                    # continuation, just as Ren'Py does for Return().
                    return getattr(statement, "next", None)
                if selection.get("end") is True:
                    record["end"] = True
                    debug["route_screens"].append(record)
                    raise Stop("screen-end")
                raise Stop("unsupported-screen-selection")
            return
        if name in (("window", "show"), ("window", "hide"), ("window", "auto")):
            execute = renpy.statements.get("execute", parsed)
            code = getattr(execute, "__code__", getattr(execute, "func_code", None))
            filename = code_filename(code)
            if not filename.endswith("renpy/common/000window.rpy"):
                raise Stop("custom-statement-handler")
            data = parsed[1]
            if name in (("window", "show"), ("window", "hide")):
                if data is not None:
                    validate_transition(data)
            else:
                if not isinstance(data, dict):
                    raise Stop("unsupported-statement-data")
                for key, source in data.items():
                    if key in ("show", "hide"):
                        validate_transition(source)
                    elif key == "auto":
                        evaluate(source)
                    else:
                        raise Stop("unsupported-statement-data")
            return
        if name not in (("pause",), ("play",), ("queue",), ("stop",),
                        ("play", "music"), ("play", "sound"), ("queue", "music"),
                        ("queue", "sound"), ("stop", "music"), ("stop", "sound"), ("voice",)):
            raise Stop("unsupported-user-statement:" + " ".join(name))
        execute = renpy.statements.get("execute", parsed)
        code = getattr(execute, "__code__", getattr(execute, "func_code", None))
        filename = code_filename(code)
        if not filename.endswith(("renpy/common/000statements.rpy", "renpy/common/00voice.rpy")):
            raise Stop("custom-statement-handler")
        data = parsed[1]
        if not isinstance(data, dict):
            raise Stop("unsupported-statement-data")
        for key in ("file", "delay", "fadeout", "fadein", "channel", "volume"):
            if data.get(key) is not None:
                audio_scope[0] = key == "file"
                try:
                    evaluate(data[key])
                finally:
                    audio_scope[0] = False

    def remember(frame, name):
        if name in frame:
            return
        if name in shadow:
            frame[name] = shadow[name]
        elif name in renpy.store.__dict__:
            frame[name] = read(name)
        else:
            frame[name] = missing

    def bind(label, args, kwargs, frame):
        parameters = getattr(label, "parameters", None)
        try:
            entries = formal_entries(parameters)
        except ValueError as error:
            raise Stop(str(error))
        if entries is None:
            if args or kwargs:
                raise Stop("unexpected-arguments")
            return
        if len(args) > len(entries):
            raise Stop("unexpected-arguments")
        values = {}
        for index, (name, default) in enumerate(entries):
            if index < len(args):
                if name in kwargs:
                    raise Stop("duplicate-argument")
                value = args[index]
            elif name in kwargs:
                value = kwargs.pop(name)
            elif default is not None:
                value = evaluate(default)
            else:
                raise Stop("missing-argument")
            values[name] = value
        if kwargs:
            raise Stop("unexpected-arguments")
        for name, value in values.items():
            remember(frame, name)
            shadow[name] = value

    def resolve_say(statement):
        item = resolve_say_value(statement)
        raw = getattr(statement, "who", None)
        if raw == "extend":
            item["character_id"], item["who"] = speaker_state
        else:
            item["character_id"] = _translator_character_id(raw) if item["who"] else ""
            speaker_state[:] = [item["character_id"], item["who"]]
        return item

    def resolve_say_value(statement):
        if getattr(statement, "arguments", None):
            raise Stop("say-arguments")
        clean = text_value(statement.what or "")
        raw_who = getattr(statement, "who", None)
        if raw_who == "extend":
            return {"who": speaker_state[1], "what": clean, "italic": False}
        who = raw_who or ""
        if raw_who in shadow:
            who = read(raw_who)
        elif raw_who and raw_who in renpy.store.__dict__:
            character = renpy.store.__dict__[raw_who]
            if type(character).__module__ == "renpy.character":
                who = character.__dict__.get("name")
                if character.__dict__.get("dynamic", False):
                    who = evaluate(who)
                if who is not None:
                    who = (text_value(character.__dict__.get("who_prefix", ""), strip=False, translate=True)
                           + text_value(who, strip=False, translate=True)
                           + text_value(character.__dict__.get("who_suffix", ""), strip=False, translate=True)).strip()
                return {
                    "who": who or "",
                    "what": clean,
                    "italic": bool(statement.what.strip().startswith("{i}")
                                   and statement.what.strip().endswith("{/i}")),
                }
            elif type(character) in string_types:
                who = read(raw_who)
            else:
                raise Stop("unsupported-speaker")
        elif raw_who:
            who = evaluate(raw_who)
        return {
            "who": text_value(who or ""),
            "what": clean,
            "italic": bool(
                statement.what.strip().startswith("{i}")
                and statement.what.strip().endswith("{/i}")
            ),
        }

    def advance(statement, active_frame_box):
        kind = type(statement).__name__
        next_node = getattr(statement, "next", None)
        if kind in ("Translate", "TranslateSay") and getattr(statement, "language", None) is None:
            language = getattr(getattr(renpy.game, "preferences", None), "language", None)
            if language is not None:
                translated = statement.lookup()
                if translated is not None and translated is not statement:
                    return translated
        if kind == "If":
            for condition, block in statement.entries:
                if condition is None or condition is True or evaluate(condition):
                    return block[0] if block else next_node
        elif kind == "While":
            if evaluate(statement.condition):
                return statement.block[0]
        elif kind in ("Jump", "Call"):
            target = statement.target if kind == "Jump" else statement.label
            if statement.expression:
                target = evaluate(target)
                if target.startswith("."):
                    target = getattr(statement, "global_label", "") + target
            label = renpy.game.script.lookup(target)
            if kind == "Call":
                arguments = getattr(statement, "arguments", None)
                call_site = plan_call(arguments, getattr(label, "parameters", None))
                try:
                    values = evaluate_call(call_site, evaluate)
                except CallPlanError as error:
                    raise Stop(str(error))
                active_frame = {}
                frames.append((next_node, active_frame))
                active_frame_box[0] = active_frame
                for name in ("_args", "_kwargs"):
                    remember(active_frame, name)
                    shadow[name] = None
                for name, value in values.items():
                    remember(active_frame, name)
                    shadow[name] = value
            elif getattr(label, "parameters", None) is not None:
                # Jumping into a parameterized label keeps the historical
                # boundary rule: there is no caller-provided local frame.
                bind(label, [], {}, active_frame_box[0])
            return label.next
        elif kind == "Return":
            shadow["_return"] = evaluate(statement.expression) if statement.expression else None
            if not frames:
                raise Stop("return-boundary")
            site, frame = frames.pop()
            for name, value in frame.items():
                if "." in name:
                    raise Stop("unsupported-dynamic-store")
                shadow[name] = missing if type(value).__name__ == "Delete" or value is missing else clone(value)
            active_frame_box[0] = dict(frames[-1][1]) if frames else {}
            if frames:
                frames[-1] = (frames[-1][0], active_frame_box[0])
            return renpy.game.script.lookup(site) if isinstance(site, string_types + (tuple,)) else site
        elif kind == "Python":
            if getattr(statement, "hide", False) or getattr(statement, "store", "store") != "store":
                raise Stop("unsupported-python-store")
            # As with label callbacks, prediction does not run runtime
            # notifications. Their registration alone must not block
            # supported assignments in the isolated shadow store.
            statements(ast.parse(statement.code.source).body)
        elif kind == "UserStatement":
            destination = passive_statement(statement)
            if destination is not None:
                return destination
        elif kind == "Label":
            bind(statement, [], {}, active_frame_box[0])
        elif kind in ("Scene", "Show", "Hide"):
            imspec = getattr(statement, "imspec", None)
            if imspec and imspec[1] is not None:
                evaluate(imspec[1])
            if imspec:
                for source in imspec[3]:
                    validate_image_transform(source)
        elif kind == "With":
            validate_transition(getattr(statement, "expr", "None"))
        elif kind not in ("Say", "TranslateSay", "Pass", "Init", "Translate", "EndTranslate"):
            raise Stop("unsupported-node:" + kind)
        return next_node

    def nested_menu_options(menu_node, active_frame_box, parent_path):
        """Return visible nested choices and their isolated branch states."""
        menu_id = _translator_menu_id(menu_node)
        base = fork_value((shadow, frames, speaker_state))
        set_expression = getattr(menu_node, "set", None)
        set_value = None
        if set_expression:
            set_value = evaluate(set_expression)
            if not isinstance(set_value, (list, set)):
                raise Stop("unsupported-menu-set")
        options = []
        for item_index, item in enumerate(getattr(menu_node, "items", None) or []):
            restored = fork_value(base)
            shadow.clear()
            shadow.update(restored[0])
            frames[:] = restored[1]
            speaker_state[:] = restored[2]
            active_frame_box[0] = frames[-1][1] if frames else {}
            if not item or len(item) < 3 or not item[2]:
                continue
            condition = item[1]
            if condition not in (None, True):
                if condition is False or not isinstance(condition, string_types) or not evaluate(condition):
                    continue
            raw_choice = item[0] or ""
            if set_value is not None and raw_choice in evaluate(set_expression):
                continue
            choice_path = list(parent_path) + [[menu_id, item_index]]
            choice_edge_id = canonical_edge_id(
                menu_node, item[2][0], "MENU_CHOICE", item_index
            )
            if set_expression:
                selected_set = evaluate(set_expression)
                if isinstance(selected_set, list):
                    selected_set.append(raw_choice)
                else:
                    selected_set.add(raw_choice)
            option = {
                "node": item[2][0],
                "state": fork_value((shadow, frames, speaker_state)),
                "choice": text_value(raw_choice),
                "choice_path": choice_path,
                "stop_node": getattr(menu_node, "next", None),
            }
            if _translator_node_ref is not None:
                option["edge_id"] = choice_edge_id
            options.append(option)
        restored = fork_value(base)
        shadow.clear()
        shadow.update(restored[0])
        frames[:] = restored[1]
        speaker_state[:] = restored[2]
        active_frame_box[0] = frames[-1][1] if frames else {}
        return options

    def resume_branch(task, active_frame_box):
        task["slices"] += 1
        restored = fork_value(task["state"])
        shadow.clear()
        shadow.update(restored[0])
        frames[:] = restored[1]
        speaker_state[:] = restored[2]
        active_frame_box[0] = frames[-1][1] if frames else {}
        reset_slice()
        branch_record = task["record"]
        branch_node = task["node"]
        try:
            while branch_node is not None and len(branch_record["items"]) < branch_limit:
                if branch_node is task.get("stop_node") and task.get("pending"):
                    following = task["pending"].pop(0)
                    branch_node = following["node"]
                    task["choice_path"] = following["choice_path"]
                    task["stop_node"] = following.get("stop_node")
                    if following.get("state") is not None:
                        restored = fork_value(following["state"])
                        shadow.clear()
                        shadow.update(restored[0])
                        frames[:] = restored[1]
                        speaker_state[:] = restored[2]
                        active_frame_box[0] = frames[-1][1] if frames else {}
                    task["node"] = branch_node
                    task["state"] = fork_value((shadow, frames, speaker_state))
                    continue
                checkpoint = fork_value((shadow, frames, speaker_state))
                tick()
                if type(branch_node).__name__ in ("Translate", "TranslateSay") and getattr(branch_node, "language", None) is None:
                    language = getattr(getattr(renpy.game, "preferences", None), "language", None)
                    if language is not None:
                        translated = branch_node.lookup()
                        if translated is not None and translated is not branch_node:
                            branch_node = translated
                            task["node"] = branch_node
                            task["state"] = fork_value((shadow, frames, speaker_state))
                            continue
                if type(branch_node).__name__ == "Menu":
                    options = nested_menu_options(
                        branch_node, active_frame_box, task.get("choice_path", []))
                    for option in options:
                        if option["choice"] and len(branch_record["items"]) < branch_limit:
                            choice_item = {
                                "who": "",
                                "character_id": "",
                                "what": option["choice"],
                                "italic": False,
                                "entry_type": "choice",
                                "choice_path": option["choice_path"],
                            }
                            if _translator_node_ref is not None:
                                choice_item["node_id"] = canonical_node_id(branch_node)
                                choice_item["edge_id"] = option.get("edge_id", "")
                            branch_record["items"].append(choice_item)
                    if len(branch_record["items"]) >= branch_limit or not options:
                        break
                    pending = task.setdefault("pending", [])
                    parent_stop = task.get("stop_node")
                    continuation = {
                        "node": getattr(branch_node, "next", None),
                        "state": None,
                        "choice_path": list(task.get("choice_path", [])),
                        "stop_node": parent_stop,
                    }
                    pending[0:0] = options[1:] + [continuation]
                    first = options[0]
                    branch_node = first["node"]
                    task["choice_path"] = first["choice_path"]
                    task["stop_node"] = first.get("stop_node")
                    restored = fork_value(first["state"])
                    shadow.clear()
                    shadow.update(restored[0])
                    frames[:] = restored[1]
                    speaker_state[:] = restored[2]
                    active_frame_box[0] = frames[-1][1] if frames else {}
                    task["node"] = branch_node
                    task["state"] = fork_value((shadow, frames, speaker_state))
                    continue
                if type(branch_node).__name__ in ("Say", "TranslateSay"):
                    say_item = resolve_say(branch_node)
                    if say_item["what"]:
                        say_item["entry_type"] = "dialogue"
                        if _translator_node_ref is not None:
                            say_item["node_id"] = canonical_node_id(branch_node)
                        say_item["choice_path"] = list(task.get("choice_path", []))
                        branch_record["items"].append(say_item)
                else:
                    scene_item = resolve_scene_text(branch_node)
                    if scene_item and scene_item["what"]:
                        scene_item["entry_type"] = "dialogue"
                        if _translator_node_ref is not None:
                            scene_item["node_id"] = canonical_node_id(branch_node)
                        scene_item["choice_path"] = list(task.get("choice_path", []))
                        branch_record["items"].append(scene_item)
                branch_node = advance(branch_node, active_frame_box)
                if branch_node is None and task.get("pending"):
                    following = task["pending"].pop(0)
                    branch_node = following["node"]
                    task["choice_path"] = following["choice_path"]
                    task["stop_node"] = following.get("stop_node")
                    if following.get("state") is not None:
                        restored = fork_value(following["state"])
                        shadow.clear()
                        shadow.update(restored[0])
                        frames[:] = restored[1]
                        speaker_state[:] = restored[2]
                        active_frame_box[0] = frames[-1][1] if frames else {}
                task["node"] = branch_node
                task["state"] = fork_value((shadow, frames, speaker_state))
            branch_record["complete"] = True
            branch_record["stop_reason"] = (
                "prefetch-limit"
                if branch_node is not None and len(branch_record["items"]) >= branch_limit
                else "next-none"
            )
            return False
        except Stop as error:
            reason = str(error)
            branch_record["stop_reason"] = reason
            branch_record["stop_node"] = debug_node(branch_node)
            if reason == "work-budget":
                shadow.clear()
                shadow.update(checkpoint[0])
                frames[:] = checkpoint[1]
                speaker_state[:] = checkpoint[2]
                task["node"] = branch_node
                task["state"] = fork_value((shadow, frames, speaker_state))
                branch_record["complete"] = False
                return task["slices"] < 16
            branch_record["complete"] = True
            return False
        except Exception as error:
            branch_record["complete"] = True
            branch_record["stop_reason"] = "prediction-error"
            branch_record["stop_node"] = debug_node(branch_node)
            branch_record["error"] = type(error).__name__ + ": " + str(error)[:160]
            return False

    def collect_menu(menu_node, active_frame_box):
        menu_id = _translator_menu_id(menu_node)
        base = fork_value((shadow, frames, speaker_state))
        reset_slice()
        set_value = None
        set_expression = getattr(menu_node, "set", None)
        if set_expression:
            set_value = evaluate(set_expression)
            if not isinstance(set_value, (list, set)):
                raise Stop("unsupported-menu-set")
        for item_index, item in enumerate(getattr(menu_node, "items", None) or []):
            reset_slice()
            restored = fork_value(base)
            shadow.clear()
            shadow.update(restored[0])
            frames[:] = restored[1]
            speaker_state[:] = restored[2]
            active_frame_box[0] = frames[-1][1] if frames else {}
            if not item or len(item) < 3 or not item[2]:
                continue
            condition = item[1]
            if condition not in (None, True):
                if condition is False or not isinstance(condition, string_types) or not evaluate(condition):
                    continue
            raw_choice = item[0] or ""
            if set_value is not None and raw_choice in evaluate(set_expression):
                continue
            choice_text = text_value(raw_choice)
            branch_record = {
                "menu_id": menu_id,
                "choice_index": item_index,
                "choice": choice_text,
                "items": [],
                "complete": True,
                "stop_reason": "next-none",
            }
            if _translator_node_ref is not None:
                branch_record["node_id"] = menu_id
                branch_record["edge_id"] = canonical_edge_id(
                    menu_node, item[2][0], "MENU_CHOICE", item_index
                )
            if set_expression:
                selected_set = evaluate(set_expression)
                if isinstance(selected_set, list):
                    selected_set.append(raw_choice)
                else:
                    selected_set.add(raw_choice)
            branches.append(branch_record)
            task = {
                "node": item[2][0],
                "state": fork_value((shadow, frames, speaker_state)),
                "record": branch_record,
                "choice_path": [[menu_id, item_index]],
                "stop_node": None,
                "pending": [],
                "slices": 0,
            }
            if resume_branch(task, active_frame_box):
                continuations.append(
                    lambda task=task: resume_branch(task, active_frame_box)
                )
        restored = fork_value(base)
        shadow.clear()
        shadow.update(restored[0])
        frames[:] = restored[1]
        speaker_state[:] = restored[2]
        active_frame_box[0] = frames[-1][1] if frames else {}

    def select_menu(menu_node):
        """Select one visible branch while retaining the current shadow state."""
        set_value = None
        set_expression = getattr(menu_node, "set", None)
        if set_expression:
            set_value = evaluate(set_expression)
            if not isinstance(set_value, (list, set)):
                raise Stop("unsupported-menu-set")

        choices = []
        for item_index, item in enumerate(getattr(menu_node, "items", None) or []):
            if not item or len(item) < 3 or not item[2]:
                continue
            condition = item[1]
            if condition not in (None, True):
                if condition is False or not isinstance(condition, string_types) or not evaluate(condition):
                    continue
            raw_choice = item[0] or ""
            if set_value is not None and raw_choice in set_value:
                continue
            choices.append({
                "choice_index": item_index,
                "choice": text_value(raw_choice),
                "raw_choice": raw_choice,
            })

        if not choices:
            return getattr(menu_node, "next", None)
        selected_index = choice_selector([
            dict({
                "menu_id": _translator_menu_id(menu_node),
                "choice_index": item["choice_index"],
                "choice": item["choice"],
            }, **({
                "node_id": _translator_menu_id(menu_node),
                "edge_id": canonical_edge_id(
                    menu_node, menu_node.items[item["choice_index"]][2][0],
                    "MENU_CHOICE", item["choice_index"],
                ),
            } if _translator_node_ref is not None else {}))
            for item in choices
        ])
        selected = next(
            (item for item in choices if item["choice_index"] == selected_index),
            None,
        )
        if selected is None:
            raise Stop("invalid-choice-selection")
        if set_value is not None:
            if isinstance(set_value, list):
                set_value.append(selected["raw_choice"])
            else:
                set_value.add(selected["raw_choice"])
        route_choice = {
            "menu_id": _translator_menu_id(menu_node),
            "dialogue_index": len(upcoming),
            "choice_index": selected["choice_index"],
            "choice": selected["choice"],
        }
        if _translator_node_ref is not None:
            route_choice["node_id"] = _translator_menu_id(menu_node)
            route_choice["edge_id"] = canonical_edge_id(
                    menu_node, menu_node.items[selected["choice_index"]][2][0],
                    "MENU_CHOICE", selected["choice_index"],
                )
        debug["route_choices"].append(route_choice)
        return menu_node.items[selected["choice_index"]][2][0]

    frames = []
    try:
        if type(current).__name__ not in ("Say", "TranslateSay", "Menu", "Show", "Scene", "With", "UserStatement"):
            raise Stop("unsupported-current-node:" + type(current).__name__)
        context = renpy.game.context()
        returns = list(getattr(context, "return_stack", []))
        dynamics = list(getattr(context, "dynamic_stack", []))
        # Dynamic dictionaries remain read-only until a return is reached.
        frames = [(site, dynamics[len(dynamics) - len(returns) + i] if len(dynamics) >= len(returns) else {})
                  for i, site in enumerate(returns)]
        active_frame = dict(frames[-1][1]) if frames else {}
        active_frame_box = [active_frame]
        if frames:
            frames[-1] = (frames[-1][0], active_frame)
        while node is not None and len(upcoming) < limit:
            if stop_selector is not None and stop_selector(node):
                raise Stop("target-node")
            tick()
            debug["visited_nodes"] += 1
            kind = type(node).__name__
            if kind == "Label" and label_observer is not None:
                label_observer(str(getattr(node, "name", "")))
            if kind in ("Translate", "TranslateSay") and getattr(node, "language", None) is None:
                language = getattr(getattr(renpy.game, "preferences", None), "language", None)
                if language is not None:
                    translated = node.lookup()
                    if translated is not None and translated is not node:
                        node = translated
                        continue
            if kind == "Menu":
                if choice_selector is not None:
                    node = select_menu(node)
                    continue
                collect_menu(node, active_frame_box)
                raise Stop("menu-boundary")
            if kind in ("Say", "TranslateSay"):
                say_item = resolve_say(node)
                if say_item["what"]:
                    if _translator_node_ref is not None:
                        say_item["node_id"] = canonical_node_id(node)
                    upcoming.append(say_item)
            else:
                scene_item = resolve_scene_text(node)
                if scene_item and scene_item["what"]:
                    if _translator_node_ref is not None:
                        scene_item["node_id"] = canonical_node_id(node)
                    upcoming.append(scene_item)
            node = advance(node, active_frame_box)
        debug["stop_reason"] = "prefetch-limit" if node is not None else "next-none"
    except Stop as error:
        debug["stop_reason"] = str(error)
    except Exception as error:
        debug["stop_reason"] = "prediction-error"
        debug["error"] = type(error).__name__ + ": " + str(error)[:160]
    debug["stop_node"] = debug_node(node)
    debug["prefetch_items"] = len(upcoming)
    debug["steps"] = steps[0]
    debug["return_stack"] = [debug_node(site) for site, _frame in frames]
    debug["elapsed_ms"] = round((clock() - started) * 1000, 3)
    debug["branch_count"] = len(branches)
    if state_names is not None:
        route_state = {}
        for name in state_names:
            if name in shadow and shadow[name] is not missing:
                route_state[name] = shadow[name]
            elif name.startswith("persistent."):
                persistent = getattr(renpy.store, "persistent", None)
                if persistent is not None and name[11:] in persistent.__dict__:
                    route_state[name] = persistent.__dict__[name[11:]]
            elif not name.startswith("persistent.") and name in renpy.store.__dict__:
                route_state[name] = renpy.store.__dict__[name]
        debug["route_state"] = route_state
    return upcoming, branches, debug, continuations
