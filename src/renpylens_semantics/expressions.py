"""Expression parsing and symbol extraction shared by the kernel layers."""

import ast

from .model import ExprIR, SymbolRef


def _source(node):
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def literal(node):
    try:
        value = ast.literal_eval(node)
    except Exception:
        return None, False
    if value is None or type(value) in (bool, int, float, str):
        return value, True
    return None, False


def parse(source, mode="eval"):
    try:
        return ast.parse(str(source or ""), mode=mode), None
    except Exception as error:
        return None, error


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, local_names=(), local_owner=""):
        self.local_names = set(local_names or ())
        self.local_owner = str(local_owner or "")
        self.symbols = []
        self._seen = set()

    def _add(self, symbol):
        key = repr(symbol.to_dict())
        if key not in self._seen:
            self._seen.add(key)
            self.symbols.append(symbol)

    def _name(self, name):
        if name in self.local_names:
            return SymbolRef.local(name, self.local_owner)
        return SymbolRef.store(name)

    def visit_Name(self, node):
        if node.id in ("None", "True", "False"):
            return
        if isinstance(getattr(node, "ctx", None), ast.Load):
            self._add(self._name(node.id))

    def visit_Attribute(self, node):
        if not isinstance(getattr(node, "ctx", None), ast.Load):
            # ``persistent.foo = ...`` and ``store.foo = ...`` carry a Load
            # context on the base Name even though the assignment itself is a
            # write.  Do not report the namespace object as a read.
            if isinstance(node.value, ast.Name) and node.value.id in ("persistent", "store"):
                return
            self.visit(node.value)
            return
        if isinstance(node.value, ast.Name) and node.value.id == "persistent":
            self._add(SymbolRef.persistent(node.attr))
            return
        if isinstance(node.value, ast.Name) and node.value.id == "store":
            self._add(SymbolRef.store(node.attr))
            return
        base = self._base_symbol(node.value)
        self._add(SymbolRef("attribute", base=base, member=node.attr,
                            precision="unknown"))
        self.visit(node.value)

    def visit_Subscript(self, node):
        if not isinstance(getattr(node, "ctx", None), ast.Load):
            self.generic_visit(node)
            return
        base = self._base_symbol(node.value)
        index_source = _source(node.slice)
        index = ExprIR(index_source, precision="unknown")
        self._add(SymbolRef("subscript", base=base, index=index,
                            precision="unknown"))
        self.visit(node.value)
        self.visit(node.slice)

    def visit_AugAssign(self, node):
        # Augmented assignment reads its target before writing it.
        target = node.target
        if isinstance(target, ast.Name):
            self._add(self._name(target.id))
        elif isinstance(target, ast.Attribute):
            if isinstance(target.value, ast.Name) and target.value.id == "persistent":
                self._add(SymbolRef.persistent(target.attr))
            elif isinstance(target.value, ast.Name) and target.value.id == "store":
                self._add(SymbolRef.store(target.attr))
            else:
                self._add(SymbolRef("attribute", base=self._base_symbol(target.value),
                                    member=target.attr, precision="unknown"))
                self.visit(target.value)
        elif isinstance(target, ast.Subscript):
            self._add(SymbolRef("subscript", base=self._base_symbol(target.value),
                                index=ExprIR(_source(target.slice), precision="unknown"),
                                precision="unknown"))
            self.visit(target.value)
            self.visit(target.slice)
        self.visit(node.value)

    def _base_symbol(self, node):
        if isinstance(node, ast.Name):
            return self._name(node.id)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "persistent":
                return SymbolRef.persistent(node.attr)
            if isinstance(node.value, ast.Name) and node.value.id == "store":
                return SymbolRef.store(node.attr)
            return SymbolRef("attribute", base=self._base_symbol(node.value),
                             member=node.attr, precision="unknown")
        if isinstance(node, ast.Subscript):
            return SymbolRef("subscript", base=self._base_symbol(node.value),
                             index=ExprIR(_source(node.slice), precision="unknown"),
                             precision="unknown")
        return SymbolRef.unknown(_source(node) or "*")

    def visit_Call(self, node):
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)


def read_symbols(source, local_names=(), local_owner=""):
    tree, error = parse(source, "eval")
    if error is not None:
        return [SymbolRef.unknown("*")], "unknown", error
    visitor = _SymbolVisitor(local_names, local_owner)
    visitor.visit(tree)
    precision = "unknown" if any(item.precision == "unknown" for item in visitor.symbols) else "exact"
    return sorted(visitor.symbols, key=lambda item: item.display()), precision, None


def statement_reads(source_text, local_names=(), local_owner=""):
    """Extract typed reads from a Python statement module.

    ``parse_expression_ir`` intentionally parses one expression.  Python
    nodes contain a statement suite, so this small companion keeps the same
    SymbolRef classification while walking an ``exec`` AST.
    """
    tree, error = parse(source_text, "exec")
    if error is not None:
        return (SymbolRef.unknown(),), "unknown"
    visitor = _SymbolVisitor(local_names, local_owner)
    visitor.visit(tree)
    precision = "unknown" if any(item.precision == "unknown" for item in visitor.symbols) else "exact"
    return tuple(sorted(visitor.symbols, key=lambda item: item.display())), precision


def parse_expression_ir(source, local_names=(), local_owner=""):
    source = str(source or "")
    symbols, precision, error = read_symbols(source, local_names, local_owner)
    value, known = (None, False)
    tree, parse_error = parse(source, "eval")
    if parse_error is None:
        value, known = literal(tree.body)
    if source.strip() in ("None", "True", "False"):
        value = {"None": None, "True": True, "False": False}[source.strip()]
        known = True
    if error is not None:
        precision = "unknown"
    return ExprIR(source, symbols, precision, known, value,
                  type(error).__name__ if error is not None else "")


class _LegacyReads(ast.NodeVisitor):
    """The v2 read spelling, retained only by the compatibility exporter."""

    def __init__(self):
        self.names = set()
        self.calls = []

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


def legacy_reads(source):
    if not source or source in ("True", "False", "None"):
        return []
    tree, error = parse(source, "eval")
    if error is not None:
        return []
    visitor = _LegacyReads()
    visitor.visit(tree)
    return sorted(visitor.names)


def expression_reads(source):
    return legacy_reads(source)


def content_reads(text):
    result = set()
    import re
    for expression in re.findall(r"(?<!\[)\[([^\[\]]+)\]", str(text or "")):
        result.update(legacy_reads(expression.split("!")[0].split(":")[0]))
    return sorted(result)


def source(node):
    return _source(node)
