"""Ren'Py boundary helpers, script identity, and AST traversal."""

import hashlib
import json
import os
import re

from .model import NodeRef, stable_digest
from .symbols import parameter_names

try:
    string_types = (basestring,)
except NameError:
    string_types = (str, bytes)


def node_kind(node):
    return type(node).__name__ if node is not None else ""


def lookup(renpy, label):
    try:
        return renpy.game.script.lookup(label)
    except Exception:
        return None


def _name_path(value):
    if isinstance(value, (tuple, list)) and value:
        return value[0]
    text = str(value or "")
    match = re.match(r"^\(?['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else ""


def canonical_path(path, gamedir=""):
    path = str(path or "").replace("\\", "/")
    if gamedir and os.path.isabs(path):
        try:
            path = os.path.relpath(path, gamedir).replace("\\", "/")
        except ValueError:
            pass
    while path.startswith("./"):
        path = path[2:]
    if path.lower().startswith("game/"):
        path = path[5:]
    # Canonical source identities use one spelling for both compiled and
    # source scripts, including games that use an upper-case extension.
    if path.lower().endswith(".rpyc"):
        path = path[:-5] + ".rpy"
    elif path.lower().endswith(".rpy"):
        path = path[:-4] + ".rpy"
    return path


def node_source_path(node, gamedir=""):
    name_path = _name_path(getattr(node, "name", ""))
    filename = getattr(node, "filename", "")
    if filename:
        return canonical_path(filename, gamedir)
    return canonical_path(name_path, gamedir)


def all_nodes(renpy):
    statements = list(getattr(renpy.game.script, "all_stmts", None) or [])
    seen = set(id(node) for node in statements)
    index = 0
    while index < len(statements):
        node = statements[index]
        index += 1
        related = [getattr(node, "next", None)]
        kind = node_kind(node)
        if kind in ("Label", "While", "Translate"):
            related.extend(getattr(node, "block", None) or [])
        elif kind == "If":
            for _condition, block in getattr(node, "entries", None) or []:
                related.extend(block or [])
        elif kind == "Menu":
            for item in getattr(node, "items", None) or []:
                if item and len(item) >= 3:
                    related.extend(item[2] or [])
        for child in related:
            if child is not None and id(child) not in seen:
                seen.add(id(child))
                statements.append(child)
    return statements


def _children(node):
    kind = node_kind(node)
    if kind in ("Label", "While", "Translate"):
        return [("block", child) for child in (getattr(node, "block", None) or [])]
    if kind == "If":
        result = []
        for index, (_condition, block) in enumerate(getattr(node, "entries", None) or []):
            result.extend(("if:%d" % index, child) for child in (block or []))
        return result
    if kind == "Menu":
        result = []
        for index, item in enumerate(getattr(node, "items", None) or []):
            if item and len(item) >= 3:
                result.extend(("menu:%d" % index, child) for child in (item[2] or []))
        return result
    return []


def _content_text(value):
    """Normalize source text without erasing semantically meaningful spaces."""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _content_value(node):
    kind = node_kind(node)
    result = {"kind": kind}
    if kind == "Python":
        result["source"] = _content_text(getattr(getattr(node, "code", None), "source", ""))
    elif kind in ("Say", "TranslateSay"):
        result.update({"who": _content_text(getattr(node, "who", "") or ""),
                       "what": _content_text(getattr(node, "what", "") or "")})
    elif kind == "UserStatement":
        result["line"] = _content_text(getattr(node, "line", "") or "")
    elif kind in ("Jump", "Call"):
        result.update({"target": _content_text(getattr(node, "target" if kind == "Jump" else "label", "") or ""),
                       "expression": bool(getattr(node, "expression", False))})
        if kind == "Call":
            arguments = getattr(node, "arguments", None)
            result["arguments"] = [
                [str(key) if key is not None else None, _content_text(source)]
                for key, source in (getattr(arguments, "arguments", None) or [])
            ]
    elif kind == "Return":
        result["expression"] = _content_text(getattr(node, "expression", "") or "")
    elif kind == "If":
        result["conditions"] = [_content_text(condition) for condition, _block in (getattr(node, "entries", None) or [])]
    elif kind == "While":
        result["condition"] = _content_text(getattr(node, "condition", "True"))
    elif kind == "Menu":
        result["set"] = _content_text(getattr(node, "set", "") or "")
        result["items"] = [
            [_content_text(item[0] if len(item) > 0 else ""),
             _content_text(item[1] if len(item) > 1 else "")]
            for item in (getattr(node, "items", None) or []) if item
        ]
    elif kind == "Label":
        result["name"] = _content_text(getattr(node, "name", "") or "")
        result["parameters"] = parameter_names(getattr(node, "parameters", None))
    return result


class IdentityIndex(object):
    """Map live Ren'Py nodes to deterministic structural references."""

    def __init__(self, graph_id="", nodes=(), gamedir=""):
        self.graph_id = graph_id or ""
        self.gamedir = gamedir or ""
        self.refs = {}
        self.paths = {}
        self.aliases = {}
        self.scopes = {}
        self.fallback_nodes = []
        self.ambiguous_nodes = []
        self._build(list(nodes or ()))

    def _build(self, nodes):
        assigned = set()
        used_paths = {}

        def walk(node, path, scope=None):
            if node is None or id(node) in assigned:
                return
            occurrence = used_paths.get(path, 0)
            used_paths[path] = occurrence + 1
            if occurrence:
                path = "%s~%d" % (path, occurrence)
            assigned.add(id(node))
            self.paths[id(node)] = path
            self.scopes[id(node)] = scope
            children = _children(node)
            counts = {}
            for role, child in children:
                kind = node_kind(child)
                count_key = (role, kind)
                ordinal = counts.get(count_key, 0)
                counts[count_key] = ordinal + 1
                child_scope = scope
                if node_kind(node) == "Label":
                    child_scope = (set(parameter_names(getattr(node, "parameters", None))), node)
                walk(child, "%s/%s/%s:%d" % (path, role, kind, ordinal), child_scope)

        roots = [node for node in nodes if node_kind(node) in ("Label", "Translate")]
        roots.sort(key=lambda node: (
            0 if node_kind(node) == "Translate" else 1,
            str(getattr(node, "name", "") or getattr(node, "identifier", "")),
            str(getattr(node, "language", "")), node_source_path(node, self.gamedir),
            int(getattr(node, "linenumber", 0) or 0),
        ))
        for node in roots:
            if node_kind(node) == "Label":
                walk(node, "label:%s" % str(getattr(node, "name", "")),
                     (set(parameter_names(getattr(node, "parameters", None))), node))
            else:
                owner = str(getattr(node, "identifier", "") or getattr(node, "name", ""))
                language = str(getattr(node, "language", "") or "default")
                walk(node, "translation:%s:%s" % (owner, language), None)

        remaining = [node for node in nodes if id(node) not in assigned]
        remaining.sort(key=lambda node: (
            node_source_path(node, self.gamedir), node_kind(node),
            int(getattr(node, "linenumber", 0) or 0),
        ))
        root_counts = {}
        fallback_keys = {}
        for node in remaining:
            key = (node_source_path(node, self.gamedir), node_kind(node),
                   int(getattr(node, "linenumber", 0) or 0))
            ordinal = root_counts.get(key, 0)
            root_counts[key] = ordinal + 1
            fallback_keys.setdefault(key, []).append(id(node))
            self.fallback_nodes.append(id(node))
            walk(node, "file:%s/%s/line:%d/occurrence:%d" %
                 (key[0], key[1], key[2], ordinal), None)
        for values in fallback_keys.values():
            if len(values) > 1:
                self.ambiguous_nodes.extend(values)

        for node in nodes:
            path = self.paths.get(id(node), "file:%s/%s:0" % (node_source_path(node, self.gamedir), node_kind(node)))
            structural_id = stable_digest({"version": 1, "path": path, "kind": node_kind(node)}, "S_")
            node_id = "N_" + structural_id[2:]
            content_hash = stable_digest(_content_value(node), "C_", 64)
            ref = NodeRef(self.graph_id, node_id, structural_id, content_hash)
            self.refs[id(node)] = ref
            filename = str(getattr(node, "filename", "") or "")
            source_path = node_source_path(node, self.gamedir)
            line = str(getattr(node, "linenumber", "") or "")
            aliases = [str(getattr(node, "name", "") or "")]
            if filename or line:
                aliases.append("%s:%s" % (filename, line))
            if source_path or line:
                aliases.extend(("%s:%s" % (source_path, line),
                                "%s:%s" % ("game/" + source_path, line)))
            for alias in aliases:
                if alias and alias not in self.aliases:
                    self.aliases[alias] = ref

    def ref_for(self, node):
        return self.refs.get(id(node))

    def scope_for(self, node):
        """Return formal names and the owning label structural ID."""
        scope = self.scopes.get(id(node))
        if not scope:
            return (), ""
        names, label = scope
        owner = self.ref_for(label)
        return tuple(sorted(names)), owner.structural_id if owner is not None else ""

    def resolve(self, value):
        if value is None:
            return None
        text = str(value)
        for ref in self.refs.values():
            if ref.node_id == text or ref.structural_id == text:
                return ref
        return self.aliases.get(text)

    def edge_id(self, source, target, kind, slot=""):
        source_ref = self.ref_for(source) if not isinstance(source, str) else None
        target_ref = self.ref_for(target) if target is not None and not isinstance(target, str) else None
        source_id = source_ref.structural_id if source_ref else str(source or "")
        target_id = target_ref.structural_id if target_ref else (str(target or "") if target is not None else "")
        # Callers use both AST integer indexes and serialized string slots;
        # normalize them before hashing so Hook, GraphMachine, and StoryGraph
        # derive the same reference.
        slot = "" if slot is None else str(slot)
        return stable_digest({"version": 1, "from": source_id, "to": target_id,
                             "kind": kind, "slot": slot}, "E_")


def script_manifest(gamedir):
    """Return a deterministic source manifest and graph identity."""
    candidates = {}
    excluded_directory_names = set((
        "cache", "saves", "save", "tmp", "temp", ".renpy", "__pycache__",
    ))
    for root, _dirs, files in os.walk(gamedir or ""):
        _dirs[:] = [
            name for name in _dirs
            if name.lower() not in excluded_directory_names
            and not name.lower().startswith(("zz_renpylens_", ".renpylens-"))
        ]
        for filename in files:
            lowered = filename.lower()
            if not lowered.endswith((".rpy", ".rpyc")):
                continue
            relative = os.path.relpath(os.path.join(root, filename), gamedir).replace("\\", "/")
            path_parts = [part.lower() for part in relative.split("/")]
            if lowered.startswith(("zz_renpylens_", "_translator_hook")):
                continue
            if lowered == "_renpylens_semantics.zip":
                continue
            if any(part in excluded_directory_names for part in path_parts[:-1]):
                continue
            if any(part.startswith(("zz_renpylens_", ".renpylens-")) for part in path_parts):
                continue
            logical = canonical_path(relative)
            if logical in candidates and lowered.endswith(".rpyc"):
                continue
            try:
                digest = hashlib.sha256()
                with open(os.path.join(root, filename), "rb") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                candidates[logical] = {"path": logical, "size": os.path.getsize(os.path.join(root, filename)),
                                       "sha256": digest.hexdigest()}
            except (OSError, IOError):
                continue
    files = sorted(candidates.values(), key=lambda item: (item["path"].lower(), item["path"]))
    graph_id = stable_digest(files, "G_", 64)
    return {"sha256": graph_id[2:], "script_files": files}, graph_id


def graph_id_for(gamedir):
    return script_manifest(gamedir)[1]


def screen_source_records(renpy):
    """Extract adapter-neutral action records from registered SL AST objects.

    Ren'Py 7.x and 8.x expose slightly different registry and AST shapes, so
    this deliberately uses data attributes only.  It never invokes screen
    functions, Action constructors, or user callables.
    """
    display = getattr(renpy, "display", None)
    screen_module = getattr(display, "screen", None)
    registry = getattr(screen_module, "screens", None)
    if registry is None:
        registry = getattr(screen_module, "screens_by_name", None)
    if not hasattr(registry, "items"):
        return []

    def source_text(value):
        if isinstance(value, string_types):
            return value
        for attr in ("source", "code", "expression"):
            candidate = getattr(value, attr, None)
            if isinstance(candidate, string_types):
                return candidate
        return ""

    def children(value):
        result = []
        for attr in ("children", "block"):
            items = getattr(value, attr, None)
            if isinstance(items, (list, tuple)):
                result.extend(items)
        return result

    def walk(value, actions, uses, seen, guard=""):
        if value is None or id(value) in seen:
            return
        seen.add(id(value))
        for attr in ("action", "alternate", "hovered", "unhovered"):
            text = source_text(getattr(value, attr, None))
            if text:
                actions.append({"source": text, "attribute": attr, "guard": guard})
        keywords = getattr(value, "keywords", None)
        if hasattr(keywords, "items"):
            for key, item in keywords.items():
                if str(key) in ("action", "alternate", "hovered", "unhovered"):
                    text = source_text(item)
                    if text:
                        actions.append({"source": text, "attribute": str(key), "guard": guard})
        keyword_list = getattr(value, "keyword", None)
        if isinstance(keyword_list, (list, tuple)):
            for entry in keyword_list:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                key, item = entry[0], entry[1]
                if str(key) in ("action", "alternate", "hovered", "unhovered"):
                    text = source_text(item)
                    if text:
                        actions.append({"source": text, "attribute": str(key), "guard": guard})
        if type(value).__name__.lower().endswith("use"):
            used = getattr(value, "target", None) or getattr(value, "screen", None)
            if isinstance(used, string_types):
                uses.append(used)
            else:
                uses.append({"dynamic": source_text(used) or str(used or "")})
        entries = getattr(value, "entries", None)
        if isinstance(entries, (list, tuple)):
            earlier = []
            for entry in entries:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                condition = source_text(entry[0]) if entry[0] is not None else ""
                exclusions = " and ".join("not (%s)" % item for item in earlier)
                branch_guard = condition
                if exclusions and condition:
                    branch_guard = "(%s) and (%s)" % (exclusions, condition)
                elif exclusions:
                    branch_guard = exclusions
                if guard and branch_guard:
                    branch_guard = "(%s) and (%s)" % (guard, branch_guard)
                elif guard:
                    branch_guard = guard
                walk(entry[1], actions, uses, seen, branch_guard)
                if condition:
                    earlier.append(condition)
        for child in children(value):
            walk(child, actions, uses, seen, guard)

    records = []
    for key, registered in registry.items():
        name = key[0] if isinstance(key, tuple) and key else key
        variant = key[1] if isinstance(key, tuple) and len(key) > 1 else ""
        ast_root = (getattr(registered, "ast", None)
                    or getattr(registered, "screen", None)
                    or getattr(registered, "sl_screen", None))
        if ast_root is None:
            continue
        actions = []
        uses = []
        walk(ast_root, actions, uses, set())
        parameters = parameter_names(getattr(ast_root, "parameters", None)
                                     or getattr(registered, "parameters", None))
        location = getattr(ast_root, "location", None) or getattr(ast_root, "loc", None)
        location_path = getattr(ast_root, "filename", "")
        location_line = getattr(ast_root, "linenumber", None)
        if isinstance(location, (list, tuple)):
            if len(location) > 0:
                location_path = location[0]
            if len(location) > 1:
                location_line = location[1]
        records.append({
            "name": str(name or ""), "variant": str(variant or ""),
            "parameters": list(parameters or ()), "actions": actions, "uses": uses,
            "location": {
                "path": canonical_path(location_path,
                                       getattr(getattr(renpy, "config", None), "gamedir", "")),
                "line": location_line, "column": None,
            },
        })
    return sorted(records, key=lambda item: (item["name"], item["variant"]))
