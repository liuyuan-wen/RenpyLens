"""Bounded effect summaries for game functions; never execute their bodies."""
import ast
import inspect
import types

from .function_source import function_syntax

_PLAIN_GETATTRIBUTE = (
    object.__getattribute__, getattr(types, "SimpleNamespace", object).__getattribute__,
)


class UnknownEffects(Exception):
    pass


class BoundFunction(object):
    def __init__(self, function, receiver):
        self.function = function
        self.receiver = receiver


try:
    scalar_types = (str, unicode, int, long, float, bool, type(None))
    string_types = (basestring,)
except NameError:
    scalar_types = (str, int, float, bool, type(None))
    string_types = (str,)


def plain_scalar(value):
    kind = type(value)
    return kind in scalar_types or (kind.__module__.startswith("future.types.")
        and kind.__name__ in ("newstr", "newint", "newbytes"))


def plain_container(value):
    kind = type(value)
    return kind in (list, tuple, dict, set) or (
        kind.__module__ == "renpy.revertable" and kind.__name__ in
        ("RevertableList", "RevertableDict", "RevertableSet"))


def summarize_call(function, arguments, keywords, renpy, tick, stack=(), check_global=None):
    """Return replaced roots, mutated roots and mutation identities.

    Values carry provenance through local aliases and loops. Reads of object
    fields use instance dictionaries, so descriptors are never invoked. Every
    possible loop element is checked; this is analysis, not a simulated call.
    """
    if isinstance(function, BoundFunction):
        arguments = [function.receiver] + arguments
        function = function.function
    if not isinstance(function, types.FunctionType) or function in stack or len(stack) >= 8:
        raise UnknownEffects()
    syntax = function_syntax(function, renpy)
    if not isinstance(syntax, ast.FunctionDef):
        raise UnknownEffects()
    if syntax.args.vararg or syntax.args.kwarg:
        raise UnknownEffects()
    if getattr(function, "__closure__", getattr(function, "func_closure", None)):
        raise UnknownEffects()
    globals_dict = getattr(function, "__globals__", getattr(function, "func_globals", {}))
    store = renpy.store.__dict__
    try:
        bound = inspect.getcallargs(function, *arguments, **keywords)
    except TypeError:
        raise UnknownEffects()
    parameter_names = [getattr(arg, "arg", getattr(arg, "id", "")) for arg in syntax.args.args]
    provided = set(parameter_names[:len(arguments)]) | set(keywords)
    local = dict((name, value if name in provided else ([value], set())) for name, value in bound.items())
    global_names = set()
    replaced, mutated = set(), set()
    mutated_ids = set()
    changed = set()

    def global_value(name):
        if name in changed:
            if name in global_names and name in local:
                return local[name][0], set((name,))
            raise UnknownEffects()
        candidate = globals_dict.get(name)
        if name not in globals_dict:
            builtins_dict = globals_dict.get("__builtins__", {})
            if isinstance(builtins_dict, types.ModuleType):
                builtins_dict = builtins_dict.__dict__
            if name in builtins_dict:
                return ([builtins_dict[name]], set())
        if name in store and store[name] is candidate:
            if check_global is not None:
                check_global(name)
            return ([candidate], set((name,)))
        if candidate is None and name not in globals_dict:
            raise UnknownEffects()
        return ([candidate], set())

    def field(base, name):
        values, roots = base
        result, paths = [], set()
        for item in values:
            tick()
            method = None
            if type(item) is types.ModuleType:
                data = item.__dict__
            else:
                for base in type(item).__mro__:
                    handler = base.__dict__.get("__getattribute__")
                    if handler is not None and handler not in _PLAIN_GETATTRIBUTE:
                        raise UnknownEffects()
                    descriptor = base.__dict__.get(name)
                    if isinstance(descriptor, types.FunctionType):
                        if method is None:
                            method = descriptor
                    elif descriptor is not None and hasattr(type(descriptor), "__get__"):
                        raise UnknownEffects()
                # Do not call a game object's __getattribute__ / property.
                try:
                    data = object.__getattribute__(item, "__dict__")
                except (AttributeError, TypeError):
                    raise UnknownEffects()
            if name not in data:
                if method is not None:
                    result.append(BoundFunction(method, ([item], roots)))
                    continue
                raise UnknownEffects()
            result.append(data[name])
        for root in roots:
            paths.add("persistent." + name if root == "persistent" else name if root == "store" else root)
        if check_global is not None:
            for path in paths:
                check_global(path)
        if paths & changed:
            raise UnknownEffects()
        return result, paths

    def safe_data(candidate, seen=None):
        tick()
        if plain_scalar(candidate):
            return
        if not plain_container(candidate) or len(candidate) > 4096:
            raise UnknownEffects()
        seen = seen or set()
        if id(candidate) in seen:
            return
        seen.add(id(candidate))
        children = list(candidate.items()) if isinstance(candidate, dict) else candidate
        for child in children:
            safe_data(child, seen)

    def data_values(base):
        values, roots = base
        items = []
        for candidate in values:
            if not plain_container(candidate) and not (plain_scalar(candidate) and isinstance(candidate, string_types)):
                raise UnknownEffects()
            tick()
            if len(candidate) > 4096:
                raise UnknownEffects()
            items.extend(list(candidate))
        return items, roots

    def checked_read(node):
        value = read(node)
        for candidate in value[0]:
            safe_data(candidate)
        return value

    def read(node):
        tick()
        if node is None:
            return ([None], set())
        if isinstance(node, ast.Name):
            if node.id in local and node.id not in global_names:
                return local[node.id]
            if node.id in ("True", "False", "None"):
                return ([{"True": True, "False": False, "None": None}[node.id]], set())
            return global_value(node.id)
        if isinstance(node, ast.Attribute):
            return field(read(node.value), node.attr)
        if type(node).__name__ in ("Str", "Num", "Constant", "NameConstant"):
            return ([ast.literal_eval(node)], set())
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            children = node.elts if hasattr(node, "elts") else node.keys + node.values
            roots = set()
            for child in children:
                roots.update(read(child)[1])
            # Literal containers are prediction-local; their children may alias.
            return ([[]], roots)
        if isinstance(node, ast.Subscript):
            checked_read(node.slice)
            return data_values(read(node.value))
        if type(node).__name__ == "Index":
            return read(node.value)
        if isinstance(node, ast.Slice):
            for child in (node.lower, node.upper, node.step):
                checked_read(child)
            return ([0], set())
        if isinstance(node, ast.Call):
            return call(node)
        if isinstance(node, (ast.BinOp, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.IfExp)):
            roots = set()
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    values, paths = read(child)
                    for candidate in values:
                        safe_data(candidate)
                    roots.update(paths)
            return ([0], roots)
        raise UnknownEffects()

    def write(target, value):
        if isinstance(target, ast.Name):
            if target.id in global_names:
                if globals_dict is not store:
                    raise UnknownEffects()
                replaced.add(target.id)
                local[target.id] = value
                changed.add(target.id)
            else:
                local[target.id] = value
        elif isinstance(target, ast.Attribute):
            values, roots = read(target.value)
            if not roots:
                raise UnknownEffects()
            if roots == set(("persistent",)):
                replaced.add("persistent." + target.attr)
                changed.add("persistent." + target.attr)
            elif roots == set(("store",)):
                replaced.add(target.attr)
                changed.add(target.attr)
            else:
                for item in values:
                    for base in type(item).__mro__:
                        handler = base.__dict__.get("__setattr__")
                        if handler is not None and handler is not object.__setattr__:
                            raise UnknownEffects()
                        descriptor = base.__dict__.get(target.attr)
                        if descriptor is not None and hasattr(type(descriptor), "__set__"):
                            raise UnknownEffects()
                mutated.update(roots)
                changed.update(roots)
                mutated_ids.update(id(item) for item in values)
        elif isinstance(target, ast.Subscript):
            checked_read(target.slice)
            values, roots = read(target.value)
            if not roots or not all(plain_container(item) for item in values):
                raise UnknownEffects()
            mutated.update(roots)
            changed.update(roots)
            mutated_ids.update(id(item) for item in values)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                write(child, value)
        else:
            raise UnknownEffects()

    def call(node):
        if getattr(node, "starargs", None) or getattr(node, "kwargs", None) or any(k.arg is None for k in node.keywords):
            raise UnknownEffects()
        args = [read(child) for child in node.args]
        kwargs = dict((child.arg, read(child.value)) for child in node.keywords)
        if isinstance(node.func, ast.Attribute):
            values, roots = read(node.func.value)
            method = node.func.attr
            if values and all(plain_container(item) for item in values):
                if method in ("append", "extend", "insert", "pop", "remove", "clear", "update", "add", "discard", "sort", "reverse", "setdefault"):
                    if not roots:
                        raise UnknownEffects()
                    if method == "sort" and (args or kwargs):
                        raise UnknownEffects()
                    if method in ("insert", "pop"):
                        for value in args + list(kwargs.values()):
                            for item in value[0]:
                                safe_data(item)
                    # Iterators, hash/equality methods and sort callbacks can
                    # themselves modify store state; plain data has no hooks.
                    if method not in ("append", "insert", "clear", "reverse", "pop"):
                        for item in values:
                            safe_data(item)
                        for value in args + list(kwargs.values()):
                            for item in value[0]:
                                safe_data(item)
                    mutated.update(roots)
                    changed.update(roots)
                    mutated_ids.update(id(item) for item in values)
                    return ([None], set())
                if method in ("items", "values", "keys", "get", "copy", "count", "index"):
                    for value in args + list(kwargs.values()):
                        for item in value[0]:
                            safe_data(item)
                    if method in ("count", "index"):
                        for item in values:
                            safe_data(item)
                    if method in ("items", "values", "keys") and all(isinstance(item, dict) for item in values):
                        items = []
                        for item in values:
                            items.extend(list(getattr(item, method)()))
                        return ([items], roots)
                    return ([object()], roots)
        candidates, _ = read(node.func)
        if len(candidates) != 1:
            raise UnknownEffects()
        candidate = candidates[0]
        if any(candidate is item for item in (len, abs, min, max, int, float, str, bool, round, any, all)):
            for value in args + list(kwargs.values()):
                for item in value[0]:
                    safe_data(item)
            return ([0], set())
        nested_replaced, nested_mutated, nested_ids = summarize_call(candidate, args, kwargs, renpy, tick, stack + (function,), check_global)
        replaced.update(nested_replaced)
        mutated.update(nested_mutated)
        changed.update(nested_replaced | nested_mutated)
        mutated_ids.update(nested_ids)
        # A result used by an operation remains opaque; standalone calls can
        # still use the nested function's complete write summary.
        return ([object()], set())

    def body(statements):
        for statement in statements:
            tick()
            if isinstance(statement, ast.Global):
                global_names.update(statement.names)
            elif isinstance(statement, ast.Assign):
                value = read(statement.value)
                for target in statement.targets:
                    write(target, value)
            elif isinstance(statement, ast.AugAssign):
                old = checked_read(statement.target)
                checked_read(statement.value)
                if any(plain_container(item) for item in old[0]):
                    mutated.update(old[1])
                    mutated_ids.update(id(item) for item in old[0] if plain_container(item))
                write(statement.target, old)
            elif isinstance(statement, ast.If):
                checked_read(statement.test)
                before = dict(local)
                before_changed = set(changed)
                body(statement.body)
                left = dict(local)
                left_changed = set(changed)
                local.clear()
                local.update(before)
                changed.clear()
                changed.update(before_changed)
                body(statement.orelse)
                changed.update(left_changed)
                for name, value in left.items():
                    other = local.get(name, value)
                    local[name] = (value[0] + other[0], value[1] | other[1])
            elif isinstance(statement, ast.For):
                values = data_values(read(statement.iter))
                before_changed = set(changed)
                write(statement.target, values)
                if values[0]:
                    body(statement.body)
                if values[1] & (changed - before_changed):
                    raise UnknownEffects()
                body(statement.orelse)
            elif isinstance(statement, ast.Return):
                read(statement.value)
            elif isinstance(statement, ast.Expr):
                read(statement.value)
            elif not isinstance(statement, (ast.Pass, ast.Break, ast.Continue)):
                raise UnknownEffects()

    body(syntax.body)
    return replaced, mutated, mutated_ids
