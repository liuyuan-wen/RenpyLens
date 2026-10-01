"""Symbol normalization and lexical parameter scopes."""

import re

from .model import SymbolRef


def persistent_name(name):
    name = str(name or "")
    return name[11:] if name.startswith("persistent.") else None


def is_identifier(name):
    return bool(re.match(r"^[A-Za-z_]\w*$", str(name or "")))


def parameter_names(parameters):
    names = []
    for attribute in ("parameters", "positional"):
        values = getattr(parameters, attribute, None) or []
        if isinstance(values, dict):
            values = list(values.values())
            values.sort(key=lambda item: (getattr(item, "position", 0),
                                          str(getattr(item, "name", ""))))
        for item in values:
            name = item[0] if isinstance(item, (tuple, list)) and item else getattr(item, "name", item)
            if name is not None and str(name) not in names:
                names.append(str(name))
    for attribute in ("extrapos", "extrakw"):
        name = getattr(parameters, attribute, None)
        if name and str(name) not in names:
            names.append(str(name))
    if not names:
        for attribute in ("name", "varname"):
            name = getattr(parameters, attribute, None)
            if name and str(name) not in names:
                names.append(str(name))
    return names


class SymbolTable(object):
    """Resolve bare names with an optional label-local parameter scope."""

    def __init__(self, local_names=(), owner=""):
        self.local_names = set(str(name) for name in (local_names or ()))
        self.owner = str(owner or "")

    def resolve(self, name):
        name = str(name or "")
        if name in self.local_names:
            return SymbolRef.local(name, self.owner)
        if name.startswith("persistent."):
            return SymbolRef.persistent(name[11:])
        return SymbolRef.store(name)
