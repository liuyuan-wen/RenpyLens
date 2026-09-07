# -*- coding: utf-8 -*-
"""Statically extract character display names from Ren'Py source files."""

from __future__ import annotations

import argparse
import ast
import io
import json
import pickle
import re
import sys
import tokenize
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


_CHARACTER_CLASSES = {
    "Character",
    "DynamicCharacter",
    "ADVCharacter",
    "NVLCharacter",
}
_IGNORED_TOKEN_TYPES = {
    tokenize.ENCODING,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.NEWLINE,
    tokenize.NL,
}
_CONSTANT_ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*(?:define|default)\s+([A-Za-z_]\w*)\s*=\s*(.+?)\s*$"
)
_ASSIGNMENT_PREFIX_RE = re.compile(
    r"(?:(?:define|default)\s+)?([A-Za-z_]\w*)\s*=\s*(?:[A-Za-z_]\w*\s*\.\s*)*$"
)
_INTERPOLATION_RE = re.compile(
    r"\[([A-Za-z_]\w*)(?:![^\]:]+)?(?::[^\]]*)?\]"
)


class CharacterExtractionError(ValueError):
    """Raised when an input cannot be scanned as a supported Ren'Py source."""


@dataclass(frozen=True)
class CharacterDefinition:
    """A name-bearing Ren'Py Character call found in source code."""

    name: str | None
    expression: str
    variable: str
    character_class: str
    source: str
    line: int
    dynamic: bool = False


@dataclass(frozen=True)
class _SourceFile:
    name: str
    text: str


class _IndexUnpickler(pickle.Unpickler):
    """Load primitive RPA indexes without allowing global object creation."""

    def find_class(self, module, name):  # pragma: no cover - only used by bad input
        if module in {"builtins", "__builtin__"} and name == "bytes":
            return bytes
        raise pickle.UnpicklingError(f"unsupported object in RPA index: {module}.{name}")


def _safe_unpickle(data: bytes):
    return _IndexUnpickler(io.BytesIO(data), encoding="bytes").load()


def _decode_source(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _archive_name(value) -> str:
    if isinstance(value, bytes):
        for encoding in ("utf-8", "cp1252"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def _entry_prefix(value) -> bytes:
    if not value:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("latin-1")


def _read_rpa_sources(
    path: Path,
    source_prefix: str = "",
    include_translations: bool = False,
) -> list[_SourceFile]:
    try:
        archive_size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(40)
            if header.startswith(b"RPA-3.0 "):
                index_offset = int(header[8:24], 16)
                key = int(header[25:33], 16)
            elif header.startswith(b"RPA-2.0 "):
                index_offset = int(header[8:24], 16)
                key = 0
            else:
                raise CharacterExtractionError(
                    f"unsupported RPA format in {path}; only RPA-2.0 and RPA-3.0 are supported"
                )

            handle.seek(index_offset)
            index = _safe_unpickle(zlib.decompress(handle.read()))
            if not isinstance(index, dict):
                raise CharacterExtractionError(f"invalid RPA index in {path}")

            result: list[_SourceFile] = []
            for raw_name, raw_segments in index.items():
                internal_name = _archive_name(raw_name).replace("\\", "/")
                lower_name = internal_name.lower()
                if not lower_name.endswith(".rpy"):
                    continue
                if not include_translations and (
                    lower_name.startswith("tl/") or "/tl/" in lower_name
                ):
                    continue
                if not isinstance(raw_segments, (list, tuple)):
                    continue

                chunks: list[bytes] = []
                for raw_segment in raw_segments:
                    if not isinstance(raw_segment, (list, tuple)) or len(raw_segment) < 2:
                        raise CharacterExtractionError(
                            f"invalid entry for {internal_name} in {path}"
                        )
                    offset = int(raw_segment[0]) ^ key
                    length = int(raw_segment[1]) ^ key
                    if offset < 0 or length < 0 or offset + length > archive_size:
                        raise CharacterExtractionError(
                            f"invalid data range for {internal_name} in {path}"
                        )
                    prefix = _entry_prefix(raw_segment[2]) if len(raw_segment) >= 3 else b""
                    handle.seek(offset)
                    chunks.append(prefix + handle.read(length))

                display_name = f"{source_prefix}{path.name}::{internal_name}"
                result.append(_SourceFile(display_name, _decode_source(b"".join(chunks))))
            return result
    except CharacterExtractionError:
        raise
    except (OSError, ValueError, TypeError, pickle.UnpicklingError, zlib.error) as exc:
        raise CharacterExtractionError(f"failed to read RPA archive {path}: {exc}") from exc


def _is_translation_path(relative_path: Path) -> bool:
    lowered = [part.lower() for part in relative_path.parts]
    return "tl" in lowered


def _collect_sources(path: Path, include_translations: bool) -> list[_SourceFile]:
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".rpy":
            return [_SourceFile(path.name, _decode_source(path.read_bytes()))]
        if suffix == ".rpa":
            return _read_rpa_sources(path, include_translations=include_translations)
        raise CharacterExtractionError("input must be an .rpy file, an .rpa file, or a directory")

    if not path.is_dir():
        raise CharacterExtractionError(f"input does not exist: {path}")

    scan_root = path / "game" if (path / "game").is_dir() else path
    result: list[_SourceFile] = []
    for source_path in sorted(scan_root.rglob("*.rpy")):
        relative = source_path.relative_to(scan_root)
        if not include_translations and _is_translation_path(relative):
            continue
        result.append(_SourceFile(relative.as_posix(), _decode_source(source_path.read_bytes())))
    for archive_path in sorted(scan_root.rglob("*.rpa")):
        relative = archive_path.relative_to(scan_root)
        prefix = relative.parent.as_posix()
        if prefix == ".":
            prefix = ""
        elif prefix:
            prefix += "/"
        result.extend(
            _read_rpa_sources(
                archive_path,
                source_prefix=prefix,
                include_translations=include_translations,
            )
        )
    return result


def _static_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left, constants)
        right = _static_string(node.right, constants)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, ast.Call) and node.args:
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if function_name in {"_", "__", "translate_string"}:
            return _static_string(node.args[0], constants)
    return None


def _collect_constants(sources: Iterable[_SourceFile]) -> dict[str, str]:
    expressions: dict[str, ast.AST] = {}
    for source in sources:
        for match in _CONSTANT_ASSIGNMENT_RE.finditer(source.text):
            try:
                expressions[match.group(1)] = ast.parse(match.group(2), mode="eval").body
            except SyntaxError:
                continue

    constants: dict[str, str] = {}
    pending = dict(expressions)
    while pending:
        changed = False
        for variable, expression in list(pending.items()):
            value = _static_string(expression, constants)
            if value is not None:
                constants[variable] = value
                del pending[variable]
                changed = True
        if not changed:
            break
    return constants


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", text):
        offsets.append(match.end())
    return offsets


def _absolute_offset(offsets: list[int], position: tuple[int, int]) -> int:
    row, column = position
    return offsets[row - 1] + column


def _find_closing_parenthesis(text: str, opening: int) -> int | None:
    depth = 0
    quote = ""
    triple = False
    escaped = False
    comment = False
    index = opening
    while index < len(text):
        char = text[index]
        if comment:
            if char == "\n":
                comment = False
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif triple and text.startswith(quote * 3, index):
                quote = ""
                triple = False
                index += 2
            elif not triple and char == quote:
                quote = ""
            index += 1
            continue
        if char == "#":
            comment = True
        elif char in {"'", '"'}:
            quote = char
            triple = text.startswith(char * 3, index)
            if triple:
                index += 2
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _tokenize(text: str) -> list[tokenize.TokenInfo]:
    tokens: list[tokenize.TokenInfo] = []
    generator = tokenize.generate_tokens(io.StringIO(text).readline)
    try:
        tokens.extend(generator)
    except (IndentationError, tokenize.TokenError):
        pass
    return tokens


def _previous_significant(tokens: list[tokenize.TokenInfo], index: int):
    for token in reversed(tokens[:index]):
        if token.type not in _IGNORED_TOKEN_TYPES and token.type != tokenize.COMMENT:
            return token
    return None


def _next_significant(tokens: list[tokenize.TokenInfo], index: int):
    for token in tokens[index + 1 :]:
        if token.type not in _IGNORED_TOKEN_TYPES and token.type != tokenize.COMMENT:
            return token
    return None


def _resolve_interpolation(value: str, constants: dict[str, str]) -> tuple[str, bool]:
    dynamic = False

    def replace(match: re.Match) -> str:
        nonlocal dynamic
        dynamic = True
        return constants.get(match.group(1), match.group(0))

    return _INTERPOLATION_RE.sub(replace, value), dynamic


def _variable_before_call(text: str, absolute_call: int) -> str:
    line_start = text.rfind("\n", 0, absolute_call) + 1
    prefix = text[line_start:absolute_call]
    match = _ASSIGNMENT_PREFIX_RE.search(prefix)
    return match.group(1) if match else ""


def _definitions_from_source(
    source: _SourceFile,
    constants: dict[str, str],
) -> list[CharacterDefinition]:
    text = source.text
    offsets = _line_offsets(text)
    tokens = _tokenize(text)
    result: list[CharacterDefinition] = []

    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in _CHARACTER_CLASSES:
            continue
        previous = _previous_significant(tokens, index)
        if previous and previous.type == tokenize.NAME and previous.string in {"def", "class"}:
            continue
        following = _next_significant(tokens, index)
        if following is None or following.type != tokenize.OP or following.string != "(":
            continue

        opening = _absolute_offset(offsets, following.start)
        closing = _find_closing_parenthesis(text, opening)
        if closing is None:
            continue
        arguments = text[opening + 1 : closing]
        try:
            call = ast.parse(f"_character({arguments})", mode="eval").body
        except SyntaxError:
            continue

        name_node = call.args[0] if call.args else None
        if name_node is None:
            for keyword in call.keywords:
                if keyword.arg == "name":
                    name_node = keyword.value
                    break
        if name_node is None or (
            isinstance(name_node, ast.Constant) and name_node.value is None
        ):
            continue

        expression = ast.get_source_segment(f"_character({arguments})", name_node) or ""
        value = _static_string(name_node, constants)
        dynamic = token.string == "DynamicCharacter"
        if dynamic and isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
            value = constants.get(name_node.value)
        if value is not None:
            value, interpolated = _resolve_interpolation(value, constants)
            dynamic = dynamic or interpolated
            value = value.strip()
            if not value:
                value = None

        absolute_call = _absolute_offset(offsets, token.start)
        result.append(
            CharacterDefinition(
                name=value,
                expression=expression.strip(),
                variable=_variable_before_call(text, absolute_call),
                character_class=token.string,
                source=source.name,
                line=token.start[0],
                dynamic=dynamic,
            )
        )
    return result


def extract_character_definitions(
    path: str | Path,
    *,
    include_translations: bool = False,
) -> list[CharacterDefinition]:
    """Return all statically discoverable name-bearing Character calls.

    ``path`` may point to one ``.rpy`` file, one RPA-2.0/RPA-3.0 ``.rpa``
    archive, a Ren'Py ``game`` directory, or a game root containing ``game``.
    Compiled ``.rpyc`` files are intentionally ignored.
    """

    input_path = Path(path).expanduser().resolve()
    sources = _collect_sources(input_path, include_translations)
    constants = _collect_constants(sources)
    definitions: list[CharacterDefinition] = []
    for source in sources:
        definitions.extend(_definitions_from_source(source, constants))
    return definitions


def extract_character_names(
    path: str | Path,
    *,
    include_translations: bool = False,
) -> list[str]:
    """Return unique resolved display names, preserving source order."""

    names: list[str] = []
    seen: set[str] = set()
    for definition in extract_character_definitions(
        path, include_translations=include_translations
    ):
        if definition.name is None or definition.name in seen:
            continue
        seen.add(definition.name)
        names.append(definition.name)
    return names


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract character names from Ren'Py .rpy/.rpa files."
    )
    parser.add_argument("path", help=".rpy/.rpa file, game directory, or game root")
    parser.add_argument(
        "--include-translations",
        action="store_true",
        help="also scan scripts below tl/ directories",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="output definitions with source locations as JSON",
    )
    args = parser.parse_args(argv)
    try:
        definitions = extract_character_definitions(
            args.path,
            include_translations=args.include_translations,
        )
    except CharacterExtractionError as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps([asdict(item) for item in definitions], ensure_ascii=False, indent=2))
    else:
        seen: set[str] = set()
        for item in definitions:
            if item.name is not None and item.name not in seen:
                seen.add(item.name)
                print(item.name)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
