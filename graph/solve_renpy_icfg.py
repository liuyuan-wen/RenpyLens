"""Bounded pushdown search over the semantic ICFG; no game code is evaluated.

Strict mode stops at unsupported effects. --allow-unknown finds structural
candidates with explicit uncertainty, never a claim of save reconstruction.
"""

import argparse
import ast
import heapq
import itertools
import json
import operator
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from renpylens_semantics.serialization import load_graph, semantic_view


UNKNOWN = object()
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
       ast.Mod: operator.mod}
COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
           ast.Is: operator.is_, ast.IsNot: operator.is_not}
NO_STORE_CALL_PREFIXES = ('renpy.pause', 'renpy.end_replay', 'renpy.music.')
PURE_SCALAR_CALLS = {'abs', 'bool', 'clamp', 'float', 'int', 'len', 'max',
                     'min', 'round', 'str'}


def scalar_expression_is_pure(node):
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name) and child.func.id in PURE_SCALAR_CALLS:
            continue
        if isinstance(child.func, ast.Attribute) and child.func.attr == 'strip':
            continue
        return False
    return True


def evaluate(source, state):
    """Evaluate plain scalar expressions only. Unknown never means False."""
    def walk(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float, str, bool, type(None)):
            return node.value
        if isinstance(node, ast.Name):
            return state.get(node.id, UNKNOWN)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'store':
            return state.get(node.attr, UNKNOWN)
        if isinstance(node, ast.BoolOp):
            values = [walk(value) for value in node.values]
            if isinstance(node.op, ast.And) and any(v is not UNKNOWN and not v for v in values):
                return False
            if isinstance(node.op, ast.Or) and any(v is not UNKNOWN and v for v in values):
                return True
            if UNKNOWN in values:
                return UNKNOWN
            return values[-1]
        if isinstance(node, ast.UnaryOp):
            value = walk(node.operand)
            if value is UNKNOWN:
                return UNKNOWN
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub):
                return -value
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            left, right = walk(node.left), walk(node.right)
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            if isinstance(node.op, ast.Mult) and (isinstance(left, str) or isinstance(right, str)):
                return UNKNOWN
            value = OPS[type(node.op)](left, right)
            if isinstance(value, str) and len(value) > 4096:
                return UNKNOWN
            return value
        if isinstance(node, ast.Compare):
            left = walk(node.left)
            unknown = False
            for op, rhs in zip(node.ops, node.comparators):
                right = walk(rhs)
                if left is UNKNOWN or right is UNKNOWN or type(op) not in COMPARE:
                    unknown = True
                elif not COMPARE[type(op)](left, right):
                    return False
                left = right
            return UNKNOWN if unknown else True
        if isinstance(node, ast.Call):
            args = [walk(argument) for argument in node.args]
            if UNKNOWN in args or node.keywords:
                return UNKNOWN
            if isinstance(node.func, ast.Name):
                functions = {
                    'abs': abs, 'bool': bool, 'float': float, 'int': int,
                    'len': len, 'max': max, 'min': min, 'round': round,
                    'str': str,
                    'clamp': lambda value, low, high: min(max(value, low), high),
                }
                function = functions.get(node.func.id)
                if function is not None:
                    try:
                        return function(*args)
                    except (ValueError, TypeError, ArithmeticError):
                        return UNKNOWN
            if (isinstance(node.func, ast.Attribute) and
                    node.func.attr == 'strip' and not args):
                value = walk(node.func.value)
                return value.strip() if isinstance(value, str) else UNKNOWN
        return UNKNOWN
    try:
        return walk(ast.parse(source or 'None', mode='eval').body)
    except (ValueError, TypeError, SyntaxError, ArithmeticError):
        return UNKNOWN


def execute_python(source, state):
    """Interpret ordered scalar statements, including nested if, in a copy."""
    result = dict(state)
    def run(body):
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.If):
                condition = evaluate(ast.unparse(stmt.test), result)
                if condition is UNKNOWN:
                    return False
                if not run(stmt.body if condition else stmt.orelse):
                    return False
                continue
            if isinstance(stmt, ast.Assign) and all(isinstance(t, ast.Name) for t in stmt.targets):
                value = evaluate(ast.unparse(stmt.value), result)
                if value is UNKNOWN:
                    if not scalar_expression_is_pure(stmt.value):
                        return False
                    for target in stmt.targets:
                        result.pop(target.id, None)
                else:
                    for target in stmt.targets:
                        result[target.id] = value
                continue
            if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
                old = result.get(stmt.target.id, UNKNOWN)
                value = evaluate(ast.unparse(stmt.value), result)
                if type(stmt.op) not in OPS or not scalar_expression_is_pure(stmt.value):
                    return False
                if old is UNKNOWN or value is UNKNOWN:
                    result.pop(stmt.target.id, None)
                    continue
                value = evaluate('left ' + {ast.Add: '+', ast.Sub: '-', ast.Mult: '*',
                                          ast.Div: '/', ast.FloorDiv: '//', ast.Mod: '%'}[type(stmt.op)] + ' right',
                                 {'left': old, 'right': value})
                if value is UNKNOWN:
                    return False
                result[stmt.target.id] = value
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                try:
                    call_name = ast.unparse(stmt.value.func)
                except Exception:
                    call_name = ''
                if any(call_name.startswith(prefix)
                       for prefix in NO_STORE_CALL_PREFIXES):
                    continue
            return False
        return True
    try:
        success = run(ast.parse(source).body)
    except (SyntaxError, ValueError, TypeError, ArithmeticError):
        success = False
    return result if success else None


def compare_state(state, target):
    differences = []
    unknown = []
    matched = 0
    for name, value in target.items():
        if name not in state:
            unknown.append(name)
        elif type(state[name]) is type(value) and state[name] == value:
            matched += 1
        else:
            differences.append({'variable': name, 'expected': value, 'actual': state[name]})
    return {'matched': matched, 'differences': differences, 'unknown_variables': unknown}


def replay_best_effort(path, target_node, nodes, blocks, baseline):
    """Replay a chosen structural path while ignoring unsupported effects."""
    state = dict(baseline)
    for step in path:
        source_nodes = blocks[step['block']]['source_nodes']
        offset = source_nodes.index(step['node'])
        for node_id in source_nodes[offset:]:
            if node_id == target_node:
                return state
            node = nodes[node_id]
            if node['kind'] == 'Python':
                updated = execute_python(node.get('source', ''), state)
                if updated is not None:
                    state = updated
                continue
            for write in node.get('writes', []):
                if write.get('op') != 'set':
                    continue
                value = (write.get('value') if write.get('value_known')
                         else evaluate(write.get('expr'), state))
                if value is not UNKNOWN:
                    state[write['var']] = value
    return state


def build_route_events(path, target_node, nodes, blocks):
    """Expand a basic-block path into ordered dialogue and choice events."""
    events = []
    for step in path:
        via = step.get('via') or {}
        if via.get('kind') == 'MENU_CHOICE':
            source = nodes[via['from']]
            events.append({
                'kind': 'choice',
                'menu': source.get('engine_name'),
                'choice_index': via.get('choice_index'),
                'choice': via.get('choice'),
                'location': source.get('location'),
            })
        source_nodes = blocks[step['block']]['source_nodes']
        offset = source_nodes.index(step['node'])
        for node_id in source_nodes[offset:]:
            if node_id == target_node:
                return events
            node = nodes[node_id]
            for content in node.get('contents', []):
                if content.get('kind') == 'dialogue':
                    events.append({
                        'kind': 'dialogue',
                        'who': content.get('who'),
                        'what': content.get('what'),
                        'node': node_id,
                        'location': node.get('location'),
                    })
    return events


def solve(graph, start_node, target_node, baseline, target, allow_unknown=False,
          max_states=50000, time_budget=30, max_stack=32,
          preferred_choices=None, max_variants=8, max_target_candidates=16):
    nodes = {n['node_id']: n for n in graph['icfg']['nodes']}
    blocks = {b['block_id']: b for b in graph['narrative_graph']['blocks']}
    owners = {n: b['block_id'] for b in blocks.values() for n in b['source_nodes']}
    if start_node not in owners or target_node not in owners:
        raise ValueError('Start or target node missing from ICFG')
    outgoing = defaultdict(list)
    for edge in graph['icfg']['edges']:
        outgoing[edge['from']].append(edge)
    sites = {s['call_node']: s['continuation'] for s in graph['icfg']['return_sites']}
    # Relaxed distances are a heuristic ONLY. Return edges are never fabricated
    # in the searched graph; only a concrete frame can supply a return address.
    reverse = defaultdict(set)
    continuations = {owners[n] for n in sites.values() if n in owners}
    for edge in graph['icfg']['edges']:
        if edge['to'] in owners:
            reverse[owners[edge['to']]].add(owners[edge['from']])
    return_blocks = {owners[n] for n in nodes if nodes[n]['kind'] == 'Return'}
    for continuation in continuations:
        reverse[continuation].update(return_blocks)
    distance = {owners[target_node]: 0}
    queue = deque(distance)
    while queue:
        dest = queue.popleft()
        for predecessor in reverse[dest]:
            if predecessor not in distance:
                distance[predecessor] = distance[dest] + 1
                queue.append(predecessor)

    def scalar_state(values):
        return {k: v for k, v in values.items() if type(v) in (int, float, bool, str, type(None))}
    target = scalar_state(target)
    desired_markers = {
        name: value for name, value in target.items()
        if (name not in baseline or type(baseline[name]) is not type(value) or
            baseline[name] != value)
    }
    marker_bits = {name: 1 << index
                   for index, name in enumerate(sorted(desired_markers))}
    reconstructable_markers = set()
    potential = {block_id: 0 for block_id in blocks}
    for block_id, block in blocks.items():
        mask = 0
        for node_id in block['source_nodes']:
            for write in nodes[node_id].get('writes', []):
                name = write.get('var')
                expected = desired_markers.get(name, UNKNOWN)
                if (expected is not UNKNOWN and write.get('op') == 'set' and
                        write.get('value_known') and
                        type(write.get('value')) is type(expected) and
                        write.get('value') == expected):
                    mask |= marker_bits[name]
                    reconstructable_markers.add(name)
        potential[block_id] = mask
    potential_predecessors = defaultdict(set)
    for edge in graph['narrative_graph'].get('edges', []):
        if edge.get('to') in blocks:
            potential_predecessors[edge['to']].add(edge['from'])
    for site in graph['narrative_graph'].get('return_sites', []):
        continuation = site.get('continuation_block')
        call_block = site.get('call_block')
        if continuation in blocks and call_block in blocks:
            potential_predecessors[continuation].add(call_block)
    potential_queue = deque(block_id for block_id, mask in potential.items() if mask)
    while potential_queue:
        child = potential_queue.popleft()
        child_mask = potential[child]
        for predecessor in potential_predecessors[child]:
            combined = potential[predecessor] | child_mask
            if combined != potential[predecessor]:
                potential[predecessor] = combined
                potential_queue.append(predecessor)
    serial = itertools.count()
    heap = []
    seen = set()
    variant_counts = defaultdict(int)
    records = []
    frontiers = []
    generated = 0
    started = time.monotonic()
    preferred_choices = preferred_choices or {}

    def enqueue(node_id, stack, state, has_uncertainty, issues, parent, edge,
                depth, route_penalty, fulfilled, markers):
        nonlocal generated
        if node_id not in owners or len(stack) > max_stack:
            return
        block_id = owners[node_id]
        if block_id not in distance:
            return
        key = (node_id, stack, tuple(sorted(state.items())), has_uncertainty,
               markers)
        control_point = (node_id, stack, has_uncertainty)
        if (key in seen or generated >= max_states or
                variant_counts[control_point] >= max_variants):
            return
        seen.add(key)
        variant_counts[control_point] += 1
        generated += 1
        record_id = len(records)
        records.append({'node': node_id, 'parent': parent, 'edge': edge,
                        'has_uncertainty': has_uncertainty,
                        'issues': issues,
                        'route_penalty': route_penalty,
                        'fulfilled': fulfilled,
                        'markers': markers})
        # Prefer proximity; constraints are checked against state, never by
        # injecting the target save's values into execution.
        priority = (distance[block_id] + depth * .001 + route_penalty * 100
                    - len(markers) * 1000)
        heapq.heappush(heap, (priority, next(serial), record_id, stack, state,
                             depth, fulfilled, markers))

    enqueue(start_node, (), scalar_state(baseline), False, [], None, None, 0, 0,
            frozenset(), frozenset())
    expanded = 0
    found = None
    target_candidates = 0
    while heap and time.monotonic() - started < time_budget:
        _, _, rid, stack, state, depth, fulfilled, markers = heapq.heappop(heap)
        record = records[rid]
        route_penalty = record['route_penalty']
        expanded += 1
        block = blocks[owners[record['node']]]
        source_nodes = block['source_nodes']
        offset = source_nodes.index(record['node'])
        state = dict(state)
        has_uncertainty = record['has_uncertainty']
        problems = []
        stopped = False
        reached_target = False
        for node_id in source_nodes[offset:]:
            node = nodes[node_id]
            if node_id == target_node:
                candidate = (rid, stack, state, has_uncertainty, problems, markers)
                target_candidates += 1
                if (found is None or len(markers) > len(found[5]) or
                        (len(markers) == len(found[5]) and
                         record['route_penalty'] < records[found[0]]['route_penalty'])):
                    found = candidate
                reached_target = True
                break
            effect = node.get('unknown_effect', {})
            issue = None
            if node['kind'] == 'Python':
                updated = execute_python(node.get('source', ''), state)
                if updated is None:
                    issue = 'unsupported-python'
                else:
                    state = updated
            elif effect and effect.get('may_write') not in (None, []):
                issue = effect.get('reason', 'unknown-effect')
            else:
                for write in node.get('writes', []):
                    if write['op'] != 'set':
                        issue = 'unsupported-write'
                        break
                    value = write.get('value') if write.get('value_known') else evaluate(write.get('expr'), state)
                    if value is UNKNOWN:
                        issue = 'unknown-write-value'
                        break
                    state[write['var']] = value
            for write in node.get('writes', []):
                variable = write.get('var')
                expected = desired_markers.get(variable, UNKNOWN)
                actual = state.get(variable, UNKNOWN)
                if (expected is not UNKNOWN and actual is not UNKNOWN and
                        type(actual) is type(expected) and actual == expected):
                    markers = markers | {variable}
                elif expected is not UNKNOWN and actual is not UNKNOWN:
                    markers = markers - {variable}
            if issue:
                problem = {'node': node_id, 'location': node.get('location'), 'reason': issue}
                problems.append(problem)
                has_uncertainty = True
                if not allow_unknown:
                    frontiers.append(problem)
                    stopped = True
                    break
                # Invalidate only the variables a partial effect may write.
                # A separate optimistic replay is produced after a path is
                # found, without weakening the verified result.
                may_write = effect.get('may_write')
                if isinstance(may_write, list):
                    for variable in may_write:
                        state.pop(variable, None)
                else:
                    state.clear()
        if reached_target:
            if target_candidates >= max_target_candidates:
                break
            continue
        if found and target_candidates >= max_target_candidates:
            break
        if stopped:
            continue
        last = nodes[source_nodes[-1]]
        if last['kind'] == 'Return':
            if stack:
                enqueue(stack[-1], stack[:-1], state, has_uncertainty, problems, rid,
                        {'kind': 'RETURN_POP', 'from': last['node_id'], 'to': stack[-1]},
                        depth + 1, route_penalty, fulfilled, markers)
            continue
        candidate_edges = outgoing[last['node_id']]
        menu_preferred = preferred_choices.get(last.get('engine_name'), set())
        if menu_preferred:
            candidate_edges = sorted(
                candidate_edges,
                key=lambda edge: (
                    edge.get('kind') != 'MENU_CHOICE' or
                    edge.get('choice_index') not in menu_preferred,
                    edge.get('edge_id', ''),
                ),
            )
        achieved_mask = 0
        for name in markers:
            achieved_mask |= marker_bits.get(name, 0)
        edge_potential = {
            edge.get('edge_id'): (
                potential.get(owners.get(edge.get('to')), 0) & ~achieved_mask
            ).bit_count()
            for edge in candidate_edges
        }
        best_edge_potential = max(edge_potential.values(), default=0)
        for edge in candidate_edges:
            if edge['kind'] == 'MENU_FALLTHROUGH':
                visibility = [
                    evaluate(candidate.get('condition', 'True'), state)
                    for candidate in outgoing[last['node_id']]
                    if candidate['kind'] == 'MENU_CHOICE'
                ]
                if any(value is not UNKNOWN and bool(value) for value in visibility):
                    continue
            edge_state = dict(state)
            edge_problems = list(problems)
            edge_uncertainty = has_uncertainty
            condition = evaluate(edge.get('condition', 'True'), state)
            if condition is not UNKNOWN and not condition:
                continue
            uncertain = condition is UNKNOWN or edge.get('precision') not in ('exact', None)
            if edge.get('menu_set') or edge.get('writes'):
                uncertain = True  # Container effects need the full executor.
                edge_state.clear()
            if uncertain:
                problem = {'edge': edge['edge_id'], 'reason': 'unverified-edge',
                           'condition': edge.get('condition')}
                if not allow_unknown:
                    frontiers.append(problem)
                    continue
                edge_problems.append(problem)
                edge_uncertainty = True
            next_stack = stack
            if edge['kind'] == 'CALL_EDGE':
                continuation = sites.get(last['node_id'])
                if continuation is None:
                    frontiers.append({'node': last['node_id'], 'reason': 'missing-return-site'})
                    continue
                next_stack = stack + (continuation,)
            if edge['kind'] == 'SCREEN_CALL_EDGE':
                # Screen Call has context behavior not represented by CALL_EDGE.
                frontiers.append({'edge': edge['edge_id'], 'reason': 'unsupported-screen-call-context'})
                continue
            edge_penalty = route_penalty
            edge_penalty += best_edge_potential - edge_potential[edge.get('edge_id')]
            next_fulfilled = fulfilled
            if edge['kind'] == 'MENU_CHOICE':
                menu_name = last.get('engine_name')
                preferred = preferred_choices.get(menu_name)
                if preferred and edge.get('choice_index') in preferred:
                    next_fulfilled = fulfilled | {menu_name}
                elif preferred and menu_name not in fulfilled:
                    edge_penalty += 1
            enqueue(edge['to'], next_stack, edge_state, edge_uncertainty,
                    edge_problems, rid, edge, depth + 1, edge_penalty,
                    next_fulfilled, markers)

    result = {'status': 'search-incomplete', 'expanded_states': expanded,
              'generated_states': generated, 'elapsed_seconds': round(time.monotonic()-started, 3),
              'unknown_frontier': frontiers[:100], 'target_reached': found is not None,
              'verified': False, 'limits': {'states': max_states, 'seconds': time_budget, 'stack': max_stack}}
    result['limits']['variants_per_control_point'] = max_variants
    result['limits']['target_candidates'] = max_target_candidates
    result['target_candidates_evaluated'] = target_candidates
    if found:
        rid, stack, state, has_uncertainty, tail_problems, markers = found
        path = []
        issue_groups = []
        while rid is not None:
            record = records[rid]
            path.append({'node': record['node'], 'block': owners[record['node']], 'via': record['edge']})
            issue_groups.append(record['issues'])
            rid = record['parent']
        issues = [issue for group in reversed(issue_groups) for issue in group]
        issues.extend(tail_problems)
        ordered_path = list(reversed(path))
        optimistic_state = replay_best_effort(
            ordered_path, target_node, nodes, blocks, scalar_state(baseline)
        )
        best_effort = compare_state(optimistic_state, target)
        certified = compare_state({} if has_uncertainty else state, target)
        result.update(path=ordered_path, uncertainty=issues,
                      target_state=certified,
                      best_effort_target_state=best_effort,
                      route_events=build_route_events(
                          ordered_path, target_node, nodes, blocks
                      ),
                      final_call_stack=list(stack))
        result['route_penalty'] = records[found[0]]['route_penalty']
        result['matched_route_markers'] = sorted(markers)
        result['matched_route_marker_count'] = len(result['matched_route_markers'])
        result['desired_route_marker_count'] = len(desired_markers)
        result['model_reconstructable_marker_count'] = len(reconstructable_markers)
        result['model_unrepresented_markers'] = sorted(
            set(desired_markers) - reconstructable_markers
        )
        comparison = result['target_state']
        result['verified'] = not issues and not comparison['differences'] and not comparison['unknown_variables']
        result['status'] = 'verified-model-path' if result['verified'] else 'candidate-requires-validation'
    else:
        result['stop_reason'] = ('unknown-frontier' if not heap and frontiers else
                                 'budget-or-model-boundary')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('graph')
    parser.add_argument('--save-analysis', required=True)
    parser.add_argument('--allow-unknown', action='store_true')
    parser.add_argument('--ignore-confirmed-choices', action='store_true')
    parser.add_argument('--max-states', type=int, default=50000)
    parser.add_argument('--max-variants', type=int, default=8)
    parser.add_argument('--max-target-candidates', type=int, default=16)
    parser.add_argument('--time-budget', type=float, default=30)
    parser.add_argument('--output', default='tests/icfg_route_candidate.json')
    parser.add_argument('--route-output')
    args = parser.parse_args()
    graph = semantic_view(load_graph(args.graph))
    analysis = json.loads(Path(args.save_analysis).read_text(encoding='utf-8'))
    target = next(n['node_id'] for n in graph['icfg']['nodes']
                  if n['engine_name'] == analysis['current']['name'])
    preferred_choices = {
        menu['menu']['name']: set(
            menu.get('confirmed_choices') or menu.get('supported_choices') or []
        )
        for menu in analysis.get('menus', [])
        if menu.get('confirmed_choices') or menu.get('supported_choices')
    } if not args.ignore_confirmed_choices else {}
    comparison_target = analysis.get('route_variables') or analysis['saved_state']
    result = solve(graph, graph['entry_points']['start'], target,
                   analysis['baseline_state'], comparison_target,
                   args.allow_unknown, args.max_states, args.time_budget,
                   preferred_choices=preferred_choices,
                   max_variants=args.max_variants,
                   max_target_candidates=args.max_target_candidates)
    result['comparison_scope'] = (
        'route_variables' if analysis.get('route_variables') else 'saved_state'
    )
    result['target_node'] = target
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    route_events = result.pop('route_events', [])
    route_output = (Path(args.route_output) if args.route_output else
                    output.with_name(output.stem + '_route.jsonl'))
    route_output.parent.mkdir(parents=True, exist_ok=True)
    route_output.write_text(
        ''.join(json.dumps(event, ensure_ascii=False) + '\n'
                for event in route_events),
        encoding='utf-8', newline='\n'
    )
    result['route_output'] = str(route_output.resolve())
    result['route_event_count'] = len(route_events)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in
                      ('path', 'uncertainty', 'target_state',
                       'best_effort_target_state', 'unknown_frontier')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
