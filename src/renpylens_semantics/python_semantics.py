"""Static Python statement semantics for StoryGraph."""

import ast

from .expressions import literal, parse_expression_ir, source, statement_reads
from .model import EffectIR, SymbolRef


_PURE_CALLS = set(("abs", "bool", "clamp", "float", "int", "len", "max",
                   "min", "round", "str", "Fade", "Dissolve", "ImageDissolve", "CropMove"))
_NO_STORE_CALL_PREFIXES = (
    "renpy.pause", "renpy.end_replay", "renpy.music.",
    "renpy.random.", "renpy.input",
)


def call_may_write_store(name):
    return not (name in _PURE_CALLS or
                any(name.startswith(prefix) if prefix.endswith('.') else name == prefix
                    for prefix in _NO_STORE_CALL_PREFIXES))


class _Reads(ast.NodeVisitor):
    def __init__(self):
        self.names = set()
        self.calls = []

    def visit_FunctionDef(self, node):
        for expression in list(node.decorator_list) + list(node.args.defaults) + list(getattr(node.args, 'kw_defaults', ())):
            if expression is not None:
                self.visit(expression)

    def visit_ClassDef(self, node):
        for expression in list(node.bases) + list(node.decorator_list):
            self.visit(expression)
        for statement in node.body:
            self.visit(statement)

    def visit_Name(self, node):
        if isinstance(getattr(node, "ctx", None), ast.Load):
            self.names.add(node.id)

    def visit_Attribute(self, node):
        if (isinstance(node.value, ast.Name) and node.value.id == "store"
                and isinstance(getattr(node, "ctx", None), ast.Load)):
            self.names.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node):
        try:
            name = ast.unparse(node.func)
        except Exception:
            name = type(node.func).__name__
        self.calls.append(name)
        if isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)


def reads(tree):
    visitor = _Reads()
    if tree is not None:
        visitor.visit(tree)
    return visitor.names, visitor.calls


def target_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "store"):
        return node.attr
    return None


def _write(target, operation, value_node, guard=None):
    name = target_name(target)
    if name is None:
        return None
    value, known = literal(value_node)
    result = {
        "var": name,
        "op": operation,
        "expr": source(value_node),
        "value_known": known,
    }
    if known:
        result["value"] = value
    if guard:
        result["guard"] = guard
    return result


def analyze(source_text):
    """Analyze Python reads and writes without executing code."""
    source_text = str(source_text or "")
    reads_set = set()
    control_reads = set()
    writes = []
    calls = []
    unsupported = []
    string_names = set()
    effectful_calls = []
    try:
        body = ast.parse(source_text).body
    except Exception as error:
        return {
            "reads": [], "control_reads": [], "writes": [],
            "precision": "unknown",
            "unknown_effect": {
                "may_read": "*", "may_write": "*", "source": source_text,
                "reason": "parse-error:" + type(error).__name__,
            },
        }

    def scan(statements, guard=None):
        for statement in statements:
            statement_reads, statement_calls = reads(statement.test if isinstance(statement, ast.If) else statement)
            reads_set.update(statement_reads)
            calls.extend(statement_calls)
            string_methods = set('%s.%s' % (name, method) for name in string_names
                                 for method in ('strip', 'lstrip', 'rstrip', 'lower', 'upper', 'replace', 'title'))
            effectful_calls.extend(name for name in statement_calls if call_may_write_store(name) and name not in string_methods)
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    name = target_name(target)
                    value = statement.value
                    literal_value, known = literal(value)
                    input_call = isinstance(value, ast.Call) and source(value.func) == 'renpy.input'
                    string_method = isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and isinstance(value.func.value, ast.Name) and value.func.value.id in string_names
                    if name:
                        if input_call or known and isinstance(literal_value, str) or string_method:
                            string_names.add(name)
                        else:
                            string_names.discard(name)
                    effect = _write(target, "set", statement.value, guard)
                    if effect is None:
                        unsupported.append(type(target).__name__)
                    else:
                        writes.append(effect)
            elif hasattr(ast, "AnnAssign") and isinstance(statement, ast.AnnAssign):
                effect = _write(statement.target, "set", statement.value, guard)
                if effect is not None:
                    writes.append(effect)
                else:
                    unsupported.append("AnnAssign")
            elif isinstance(statement, ast.AugAssign):
                name = target_name(statement.target)
                if name:
                    reads_set.add(name)
                effect = _write(
                    statement.target, type(statement.op).__name__.lower(),
                    statement.value, guard,
                )
                if effect is not None:
                    writes.append(effect)
                else:
                    unsupported.append("AugAssign")
            elif isinstance(statement, ast.If):
                condition = source(statement.test)
                condition_reads, condition_calls = reads(statement.test)
                control_reads.update(condition_reads)
                calls.extend(condition_calls)
                before = set(string_names)
                scan(statement.body, "(%s) and (%s)" % (guard, condition) if guard else condition)
                after_true = set(string_names)
                string_names.clear(); string_names.update(before)
                negative = "not (%s)" % condition
                scan(statement.orelse, "(%s) and (%s)" % (guard, negative) if guard else negative)
                string_names.intersection_update(after_true)
            elif isinstance(statement, (ast.Expr, ast.Pass)):
                pass
            elif isinstance(statement, ast.FunctionDef):
                pass
            else:
                unsupported.append(type(statement).__name__)

    scan(body)
    unknown = bool(effectful_calls or unsupported)
    result = {
        "reads": sorted(reads_set),
        "control_reads": sorted(control_reads),
        "writes": writes,
        "precision": "partial" if unknown and (reads_set or writes) else "unknown" if unknown else "exact",
    }
    if unknown:
        result["unknown_effect"] = {
            "known_reads": sorted(reads_set),
            "known_writes": sorted(set(item["var"] for item in writes)),
            "may_read": "*" if effectful_calls else [],
            "may_write": "*",
            "calls": sorted(set(effectful_calls)),
            "unsupported": sorted(set(unsupported)),
            "source": source_text,
            "reason": "python-effect",
        }
    result["effect_precision"] = result["precision"]
    return result


python_semantics = analyze


def _typed_symbol(name, local_names=(), local_owner=""):
    text = str(name or "")
    if text == "*":
        return SymbolRef.unknown()
    if text.startswith("persistent."):
        return SymbolRef.persistent(text[11:])
    if text.startswith("local."):
        return SymbolRef.local(text[6:], local_owner)
    if text in set(local_names or ()):
        return SymbolRef.local(text, local_owner)
    if "." in text:
        base, member = text.rsplit(".", 1)
        return SymbolRef("attribute", base=_typed_symbol(base, local_names, local_owner),
                         member=member, precision="unknown")
    return SymbolRef.store(text)


def _target_symbol(node, local_names=(), local_owner=""):
    if isinstance(node, ast.Name):
        return _typed_symbol(node.id, local_names, local_owner)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "persistent":
            return SymbolRef.persistent(node.attr)
        if isinstance(node.value, ast.Name) and node.value.id == "store":
            return SymbolRef.store(node.attr)
        return SymbolRef("attribute", base=_target_symbol(node.value, local_names, local_owner),
                         member=node.attr, precision="unknown")
    if isinstance(node, ast.Subscript):
        return SymbolRef("subscript", base=_target_symbol(node.value, local_names, local_owner),
                         index=parse_expression_ir(source(node.slice), local_names, local_owner),
                         precision="unknown")
    return SymbolRef.unknown(type(node).__name__)


class _IRWriter(ast.NodeVisitor):
    def __init__(self, local_names=(), local_owner=""):
        self.local_names = local_names
        self.local_owner = local_owner
        self.effects = []
        self.guard = ""

    def _append(self, target, operator, value_node):
        expression = parse_expression_ir(source(value_node), self.local_names, self.local_owner)
        precision = expression.precision if expression.precision != "exact" else target.precision
        self.effects.append(EffectIR(
            "assign", target, operator, expression, expression.reads, (), precision,
            "", expression.constant_known, expression.constant_value,
            parse_expression_ir(self.guard, self.local_names, self.local_owner) if self.guard else None,
        ))

    def visit_If(self, node):
        parent = self.guard
        condition = source(node.test)
        self.guard = "(%s) and (%s)" % (parent, condition) if parent else condition
        for statement in node.body:
            self.visit(statement)
        negative = "not (%s)" % condition
        self.guard = "(%s) and (%s)" % (parent, negative) if parent else negative
        for statement in node.orelse:
            self.visit(statement)
        self.guard = parent

    def visit_FunctionDef(self, node):
        pass

    def visit_ClassDef(self, node):
        pass

    def visit_Assign(self, node):
        for target in node.targets:
            self._append(_target_symbol(target, self.local_names, self.local_owner), "set", node.value)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self._append(_target_symbol(node.target, self.local_names, self.local_owner), "set", node.value)

    def visit_AugAssign(self, node):
        self._append(_target_symbol(node.target, self.local_names, self.local_owner),
                     type(node.op).__name__.lower(), node.value)


def _typed_writes(source_text, local_names=(), local_owner=""):
    try:
        tree = ast.parse(str(source_text or ""))
    except Exception:
        return ()
    writer = _IRWriter(local_names, local_owner)
    writer.visit(tree)
    return tuple(writer.effects)


class _IRControlReader(ast.NodeVisitor):
    def __init__(self, local_names=(), local_owner=""):
        self.local_names = local_names
        self.local_owner = local_owner
        self.reads = []

    def _add(self, node):
        self.reads.extend(parse_expression_ir(
            source(node), self.local_names, self.local_owner
        ).reads)

    def visit_If(self, node):
        self._add(node.test)
        self.generic_visit(node)

    def visit_While(self, node):
        self._add(node.test)
        self.generic_visit(node)


def analyze_ir(source_text, local_names=(), local_owner=""):
    """Build typed statement semantics from shared Python analysis."""
    analysis = analyze(source_text)
    reads, read_precision = statement_reads(source_text, local_names, local_owner)
    if read_precision == "unknown" and not reads:
        reads = (SymbolRef.unknown(),)
    controls = []
    try:
        control_tree = ast.parse(str(source_text or ""))
        control_reader = _IRControlReader(local_names, local_owner)
        control_reader.visit(control_tree)
        controls.extend(control_reader.reads)
    except Exception:
        controls.extend(_typed_symbol(name, local_names, local_owner)
                        for name in analysis.get("control_reads", []))
    deduped_controls = {}
    for item in controls:
        deduped_controls[repr(item.to_dict())] = item
    controls = tuple(sorted(deduped_controls.values(), key=lambda item: item.display()))
    effects = list(_typed_writes(source_text, local_names, local_owner))
    for write in analysis.get("writes", []):
        # The AST writer covers ordinary assignments and additionally keeps
        # persistent/attribute/subscript targets typed.  Do not duplicate the
        # same analysis effect when it is already represented.
        if effects:
            continue
        expression = parse_expression_ir(write.get("expr", ""), local_names, local_owner)
        target = _typed_symbol(write.get("var", ""), local_names, local_owner)
        precision = expression.precision if expression.precision != "exact" else target.precision
        effects.append(EffectIR(
            "assign", target, write.get("op", "set"), expression,
            expression.reads, (), precision, "",
            write.get("value_known", False), write.get("value"),
        ))
    unknown = analysis.get("unknown_effect")
    typed_unknown_boundary = bool(unknown) and (
        not effects
        or bool(unknown.get("calls"))
        or any(item not in ("Attribute", "Subscript", "AugAssign")
               for item in (unknown.get("unsupported") or ()))
    )
    if unknown and typed_unknown_boundary:
        def symbols(value):
            if value == "*":
                return (SymbolRef.unknown(),)
            return tuple(_typed_symbol(name, local_names, local_owner)
                         for name in (value or ()))
        effects.append(EffectIR(
            "unknown", None, "", parse_expression_ir(
                unknown.get("source", ""), local_names, local_owner
            ), symbols(unknown.get("may_read")), symbols(unknown.get("may_write")),
            "unknown", unknown.get("reason", ""),
        ))
    return {
        "reads": reads,
        "control_reads": controls,
        "effects": tuple(effects),
        "precision": "unknown" if any(effect.kind == "unknown" for effect in effects)
        else "partial" if any(effect.precision != "exact" for effect in effects) else "exact",
    }
