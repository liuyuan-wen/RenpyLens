"""Substitute syntax and fold closed expressions without running game code."""

import ast
import copy
import operator


_UNKNOWN = object()
_BUILTINS = {"abs": abs, "all": all, "any": any, "bool": bool,
             "float": float, "int": int, "len": len, "list": list,
             "max": max, "min": min, "round": round, "str": str,
             "tuple": tuple, "type": type, "range": range}
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Eq: operator.eq, ast.NotEq: operator.ne,
        ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.Is: operator.is_, ast.IsNot: operator.is_not,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


def _value(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        pass
    if isinstance(node, ast.Name):
        return _BUILTINS.get(node.id, _UNKNOWN)
    if isinstance(node, ast.UnaryOp):
        value = _value(node.operand)
        if value is _UNKNOWN:
            return _UNKNOWN
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
    if isinstance(node, ast.BinOp):
        left, right = _value(node.left), _value(node.right)
        function = _OPS.get(type(node.op))
        if left is not _UNKNOWN and right is not _UNKNOWN and function:
            return function(left, right)
    if isinstance(node, ast.BoolOp):
        for item in node.values:
            value = _value(item)
            if value is _UNKNOWN:
                return _UNKNOWN
            if isinstance(node.op, ast.And) and not value or isinstance(node.op, ast.Or) and value:
                return value
        return value
    if isinstance(node, ast.Compare):
        values = [_value(node.left)] + [_value(item) for item in node.comparators]
        if all(item is not _UNKNOWN for item in values):
            return all(_OPS[type(op)](values[i], values[i + 1]) for i, op in enumerate(node.ops))
    if isinstance(node, ast.IfExp):
        condition = _value(node.test)
        if condition is not _UNKNOWN:
            return _value(node.body if condition else node.orelse)
    if isinstance(node, ast.Subscript):
        container = _value(node.value)
        index_node = node.slice.value if isinstance(node.slice, getattr(ast, 'Index', ())) else node.slice
        index = _value(index_node)
        if container is not _UNKNOWN and index is not _UNKNOWN:
            return container[index]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = [_value(item) for item in node.args]
        if node.keywords or any(item is _UNKNOWN for item in args):
            return _UNKNOWN
        if node.func.id == '_' and len(args) == 1:
            return args[0]
        function = _BUILTINS.get(node.func.id)
        if function:
            return function(*args)
    return _UNKNOWN


def constant_value(source):
    try:
        value = _value(ast.parse(str(source), mode='eval').body)
        return (None, False) if value is _UNKNOWN or callable(value) else (value, True)
    except (SyntaxError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        return None, False


def substitute_source(source, bindings):
    replacements = {}
    for name, value in bindings.items():
        try:
            replacements[name] = ast.parse(str(value), mode='eval').body
        except SyntaxError:
            continue

    class Substitute(ast.NodeTransformer):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and node.id in replacements:
                return ast.copy_location(copy.deepcopy(replacements[node.id]), node)
            return node

    try:
        result = Substitute().visit(ast.parse(str(source), mode='eval'))
        ast.fix_missing_locations(result)
        value, known = constant_value(ast.unparse(result))
        return repr(value) if known else ast.unparse(result)
    except (SyntaxError, ValueError):
        return str(source)
