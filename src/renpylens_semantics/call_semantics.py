"""Call and label semantics shared by graph construction and shadow execution."""

from .expressions import parse_expression_ir
from .model import (CallArgumentIR, CallSiteIR, EffectIR, FormalBindingIR,
                    NodeRef, PrecisionSet, SymbolRef, merge_precision)
from .symbols import parameter_names


def label_parameter_names(parameters):
    return parameter_names(parameters)


def formal_bindings(parameters, positional=(), keywords=None):
    """Describe currently known formal bindings without evaluating arguments.

    Stage 4 adds value-aware binding.  This helper intentionally returns a
    structural record so v3 can represent the uncertainty explicitly.
    """
    names = label_parameter_names(parameters)
    keywords = keywords or {}
    bindings = []
    for index, name in enumerate(names):
        if index < len(positional or ()):
            bindings.append({"formal": name, "actual": positional[index], "precision": "unknown"})
        elif name in keywords:
            bindings.append({"formal": name, "actual": keywords[name], "precision": "unknown"})
        else:
            bindings.append({"formal": name, "actual": None, "precision": "unknown"})
    return bindings


def formal_entries(parameters):
    """Return ``(name, default_source)`` pairs for a Ren'Py label.

    The helper only enumerates the frame shape.  GraphMachine remains
    responsible for evaluating defaults and mutating its shadow frame.
    """
    if parameters is None:
        return None
    # Ren'Py's newer Parameters object exposes ``parameters`` while older
    # engines keep the same ordered ``(name, default)`` pairs in
    # ``positional``.  Treat both as one semantic declaration sequence.
    params = getattr(parameters, "parameters", None)
    if params is None:
        params = getattr(parameters, "positional", None)
    if isinstance(params, dict):
        entries = []
        values = list(params.values())
        if values and all(getattr(param, "position", None) is not None for param in values):
            values.sort(key=lambda param: getattr(param, "position"))
        for param in values:
            if int(param.kind) not in (0, 1):
                raise ValueError("unsupported-parameter-kind")
            default = param.default
            entries.append((param.name, None if default is param.empty else default))
        return entries
    if getattr(parameters, "extrapos", None) or getattr(parameters, "extrakw", None):
        raise ValueError("unsupported-parameter-kind")
    return list(params or [])


class CallPlanError(ValueError):
    """A structural call error with the stop reason used by GraphMachine."""


def argument_entries(arguments, local_names=(), local_owner=""):
    """Parse Ren'Py call arguments in source order without evaluating them."""
    result = []
    arguments = arguments or None
    if arguments is None:
        return result
    starred = set(getattr(arguments, "starred_indexes", None) or ())
    doublestarred = set(getattr(arguments, "doublestarred_indexes", None) or ())
    for index, item in enumerate(getattr(arguments, "arguments", None) or []):
        key, source = item
        unpacking = "*" if index in starred else "**" if index in doublestarred else ""
        expression = parse_expression_ir(source, local_names, local_owner)
        result.append(CallArgumentIR(index, key, expression, unpacking,
                                     expression.precision))
    if getattr(arguments, "extrapos", None):
        expression = parse_expression_ir(arguments.extrapos, local_names, local_owner)
        result.append(CallArgumentIR(len(result), None, expression, "*", "unknown"))
    if getattr(arguments, "extrakw", None):
        expression = parse_expression_ir(arguments.extrakw, local_names, local_owner)
        result.append(CallArgumentIR(len(result), None, expression, "**", "unknown"))
    return result


def _target_ref(target):
    if target is None or isinstance(target, NodeRef):
        return target
    if hasattr(target, "node_id"):
        return target
    return None


def plan_call(arguments, parameters, caller_locals=(), caller_owner="",
              callee_owner="", target=None, continuation=None, dynamic_target=False):
    """Build a CallSiteIR and never execute a game expression.

    Actual arguments and defaults are deliberately parsed in caller scope.  It
    matches GraphMachine's existing pre-bind evaluation order: all values are
    known before the callee local frame is written.
    """
    actuals = argument_entries(arguments, caller_locals, caller_owner)
    topology = "unknown" if dynamic_target else "exact"
    state_values = [item.precision for item in actuals] or ["exact"]
    reason = "dynamic-call-target" if dynamic_target else ""
    bindings = []
    effects = []
    if dynamic_target:
        return CallSiteIR(
            _target_ref(target), True, actuals, (), _target_ref(continuation), (),
            PrecisionSet(topology, merge_precision(state_values), "exact"), reason,
        )
    try:
        entries = formal_entries(parameters)
    except ValueError as error:
        entries = []
        reason = str(error)
        state_values.append("unknown")
    unpacked = [item for item in actuals if item.unpacking]
    if unpacked:
        reason = reason or "unsupported-argument-unpacking"
        state_values.append("unknown")

    positional = [item for item in actuals if item.keyword is None and not item.unpacking]
    keyword_items = [item for item in actuals
                     if item.keyword is not None and not item.unpacking]
    keyword_counts = {}
    for item in keyword_items:
        keyword_counts[item.keyword] = keyword_counts.get(item.keyword, 0) + 1
    duplicate_keywords = set(key for key, count in keyword_counts.items() if count > 1)
    keywords = dict((item.keyword, item) for item in keyword_items)
    if duplicate_keywords:
        reason = reason or "duplicate-argument"
        state_values.append("unknown")
    if entries is None:
        if actuals:
            reason = reason or "unexpected-arguments"
            state_values.append("unknown")
        entries = []
    elif len(positional) > len(entries):
        reason = reason or "unexpected-arguments"
        state_values.append("unknown")

    used_keywords = set()
    for index, entry in enumerate(entries):
        name, default_source = entry
        formal = SymbolRef.local(name, callee_owner)
        source_kind = "missing"
        argument_index = None
        expression = None
        binding_reason = ""
        if index < len(positional):
            actual = positional[index]
            source_kind, argument_index, expression = "positional", actual.index, actual.expression
            if name in keywords or name in duplicate_keywords:
                binding_reason = "duplicate-argument"
        elif name in keywords:
            actual = keywords[name]
            used_keywords.add(name)
            source_kind, argument_index, expression = "keyword", actual.index, actual.expression
            if name in duplicate_keywords:
                binding_reason = "duplicate-argument"
        elif default_source is not None:
            source_kind = "default"
            expression = parse_expression_ir(default_source, caller_locals, caller_owner)
        else:
            binding_reason = "missing-argument"
        precision = expression.precision if expression is not None else "unknown"
        if binding_reason:
            reason = reason or binding_reason
            precision = "unknown"
        binding = FormalBindingIR(formal, source_kind, argument_index, expression,
                                  precision, binding_reason)
        bindings.append(binding)
        state_values.append(precision)
        effects.append(EffectIR("bind_parameter", formal, "set", expression,
                                expression.reads if expression is not None else (),
                                (formal,), precision, binding_reason,
                                expression.constant_known if expression is not None else False,
                                expression.constant_value if expression is not None else None))
    extra_keywords = sorted(set(keywords) - used_keywords)
    if extra_keywords:
        reason = reason or "unexpected-arguments"
        state_values.append("unknown")
    if reason:
        state_values.append("unknown")
    continuation = _target_ref(continuation)
    return CallSiteIR(
        _target_ref(target), dynamic_target, actuals, bindings, continuation, effects,
        PrecisionSet(topology, merge_precision(state_values), "exact"), reason,
    )


def evaluate_call(site, evaluate):
    """Evaluate a valid CallSiteIR in source order and return local values."""
    if site.reason:
        raise CallPlanError(site.reason)
    values = {}
    evaluated = {}
    for argument in site.arguments:
        if argument.unpacking:
            raise CallPlanError("unsupported-argument-unpacking")
        evaluated[argument.index] = evaluate(argument.expression.source)
    for binding in site.formal_bindings:
        if binding.reason:
            raise CallPlanError(binding.reason)
        if binding.source_kind in ("positional", "keyword"):
            values[binding.formal.name] = evaluated[binding.argument_index]
        elif binding.source_kind == "default":
            values[binding.formal.name] = evaluate(binding.expression.source)
        else:
            raise CallPlanError("missing-argument")
    return values
