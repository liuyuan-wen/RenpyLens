"""Read Python function syntax, including functions inside archived .rpy files."""
import ast
import inspect
import textwrap


_syntax_cache = {}


def function_syntax(function, renpy):
    code = getattr(function, "__code__", getattr(function, "func_code", None))
    if code is None:
        return None
    if code in _syntax_cache:
        return _syntax_cache[code]
    _syntax_cache[code] = None
    syntax = None
    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (IOError, OSError, TypeError):
        loader = getattr(getattr(renpy, "loader", None), "load", None)
        if loader is None:
            return None
        try:
            filename = code.co_filename.replace("\\", "/")
            if filename.startswith("game/"):
                filename = filename[5:]
            stream = loader(filename)
            try:
                lines = stream.read().decode("utf-8-sig").splitlines()
            finally:
                stream.close()
            start = code.co_firstlineno - 1
            first = lines[start]
            indent = len(first) - len(first.lstrip())
            end = start + 1
            while end < len(lines):
                line = lines[end]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                end += 1
            source = textwrap.dedent("\n".join(lines[start:end]))
            if code.co_name == "<lambda>":
                # Continuation lines (including the closing parenthesis) need
                # not be indented more than a Ren'Py define statement.
                while end <= len(lines):
                    source = textwrap.dedent("\n".join(lines[start:end]))
                    try:
                        ast.parse(source[source.index("lambda"):], mode="eval")
                        break
                    except SyntaxError:
                        end += 1
        except (IOError, OSError, UnicodeError, IndexError):
            return None
    try:
        # Ren'Py's define statement can contain a lambda rather than a def.
        if code.co_name == "<lambda>" and "lambda" in source:
            source = source[source.index("lambda"):]
            syntax = ast.parse(source, mode="eval").body
        else:
            tree = ast.parse(source)
            syntax = next((item for item in tree.body if isinstance(item, ast.FunctionDef)), None)
    except (SyntaxError, ValueError):
        pass
    _syntax_cache[code] = syntax
    return syntax
