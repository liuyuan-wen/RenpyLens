"""One script text source for live dialogue, shadow prediction and bulk scan."""
import ast
import re

from .function_source import function_syntax

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


def filtered_text(renpy, text):
    """Interpret common pure text filters without invoking game Python."""
    function = getattr(getattr(renpy, "config", None), "say_menu_text_filter", None)
    if function is None:
        return text
    syntax = function_syntax(function, renpy)
    if syntax is None:
        return text
    arguments = syntax.args.args
    if len(arguments) != 1:
        return text
    name = getattr(arguments[0], "arg", getattr(arguments[0], "id", ""))
    globals_dict = getattr(function, "__globals__", getattr(function, "func_globals", {}))

    def value(node):
        if isinstance(node, ast.Name) and node.id == name:
            return text
        if type(node).__name__ in ("Str", "Num", "Constant"):
            return ast.literal_eval(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return value(node.left) + value(node.right)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if globals_dict.get(node.value.id) is re and node.attr in ("I", "IGNORECASE", "M", "MULTILINE", "S", "DOTALL"):
                return getattr(re, node.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if getattr(node, "starargs", None) or getattr(node, "kwargs", None) or any(k.arg is None for k in node.keywords):
                raise ValueError("dynamic-filter-call")
            args = [value(item) for item in node.args]
            kwargs = dict((item.arg, value(item.value)) for item in node.keywords)
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and globals_dict.get(receiver.id) is re and node.func.attr == "sub":
                # Only literal replacement strings: no replacement callbacks.
                replacement = args[1] if len(args) > 1 else kwargs.get("repl")
                if not isinstance(replacement, string_types):
                    raise ValueError("filter-callback")
                return re.sub(*args, **kwargs)
            receiver = value(receiver)
            if isinstance(receiver, string_types) and node.func.attr in ("replace", "strip", "lstrip", "rstrip", "lower", "upper"):
                return getattr(receiver, node.func.attr)(*args, **kwargs)
        raise ValueError("unknown-text-filter")

    try:
        if isinstance(syntax, ast.Lambda):
            return value(syntax.body)
        body = [item for item in syntax.body if not (isinstance(item, ast.Expr)
                and type(item.value).__name__ in ("Str", "Constant")
                and isinstance(ast.literal_eval(item.value), string_types))]
        if len(body) == 1 and isinstance(body[0], ast.Return):
            return value(body[0].value)
    except (ValueError, TypeError, re.error, IndexError):
        pass
    return text


def say_segment(renpy, statement, clean, displayed=None):
    """Keep character affixes out of the translation key, but preserve display."""
    raw = getattr(statement, "what", "") or ""
    filtered = filtered_text(renpy, raw)
    source = clean(filtered)
    character = getattr(renpy, "store", None)
    character = getattr(character, "__dict__", {}).get(getattr(statement, "who", None) or "narrator")
    properties = character.__dict__ if type(character).__module__ == "renpy.character" else {}
    prefix = clean(properties.get("what_prefix", ""), strip=False)
    suffix = clean(properties.get("what_suffix", ""), strip=False)
    expected = (prefix + source + suffix).strip()
    if displayed is None:
        displayed = expected
    elif displayed != expected:
        # An unsupported filter or custom character changed the actual content.
        # Keep that content rather than reusing a translation for different words.
        source, prefix, suffix = displayed, "", ""
    styled = properties.get("what_prefix", "") + filtered + properties.get("what_suffix", "")
    return {"source": source, "what": source, "display_text": displayed,
            "display_prefix": prefix, "display_suffix": suffix,
            "italic": styled.strip().startswith("{i}") and styled.strip().endswith("{/i}")}
