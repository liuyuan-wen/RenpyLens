import { all, any, negate, predicate, predicateText, expression, effectText, substitute } from './expressions.js';
import { Graph, alg } from '@dagrejs/graphlib';

const CALL = new Set(['CALL_EDGE', 'SCREEN_CALL', 'SCREEN_CALL_EDGE']);
const visibleEffects = node => (node.effects || []).filter(effect => !effect.unknown || effect.reason !== 'presentation-statement');

function dialogueJoins(graph, playable) {
  const cache = new Map(), visiting = new Set();
  function exit(id) {
    if (!id) return null;
    if (cache.has(id)) return cache.get(id);
    const node = graph.projected.get(id);
    if (!node) return null;
    if (playable.has(id) || node.effects.some(effect => !effect.unknown || effect.reason !== 'presentation-statement')
      || ['call', 'return', 'unknown'].includes(node.type) || node.control === 'While') return { target: id, blocks: new Set() };
    if (visiting.has(id)) return null;
    visiting.add(id);
    const outgoing = graph.out.get(id) || [], children = outgoing.map(edge =>
      edge.precision.topology === 'exact' && !CALL.has(edge.kind) && !visibleEffects(edge).length ? exit(edge.to) : null);
    let result = null;
    if (children.length && children.every(child => child && child.target === children[0].target)) {
      result = { target: children[0].target, blocks: new Set([id]) };
      for (let i = 0; i < children.length; i++) {
        for (const block of children[i].blocks) result.blocks.add(block);
        for (const block of outgoing[i].via || []) result.blocks.add(block);
      }
    }
    visiting.delete(id); cache.set(id, result); return result;
  }
  return exit;
}

// This is a small must-analysis, not route solving: an unconditional flag
// assignment establishes a fact; joins retain only facts true on every path.
function guaranteedFlagGroups(graph, flags) {
  const bits = new Map(flags.map((flag, index) => [flag, 1n << BigInt(index)])), states = new Map();
  const queue = [], queued = new Set();
  const push = (id, incoming) => {
    const old = states.get(id), next = old ? { must: old.must & incoming.must, any: old.any && incoming.any } : incoming;
    if (old && old.must === next.must && old.any === next.any) return;
    states.set(id, next); if (!queued.has(id)) { queue.push(id); queued.add(id); }
  };
  const roots = graph.entries.length ? graph.entries.map(entry => graph.anchor(entry.id))
    : [...graph.projected.keys()].filter(id => !graph.inc.get(id)?.length);
  for (const id of roots) push(id, { must: 0n, any: false });
  function transfer(state, effects) {
    let { must, any } = state;
    for (const effect of effects) {
      if (effect.unknown && effect.reason !== 'presentation-statement') { must = 0n; any = false; continue; }
      const bit = bits.get(effect.target); if (!bit) continue;
      const ast = expression(effect.expression);
      if (!effect.guard && effect.operator === 'set' && ast.type === 'literal' && ast.value === true) { must |= bit; any = true; }
      else { must &= ~bit; any = must !== 0n; }
    }
    return { must, any };
  }
  for (let head = 0; head < queue.length; head++) {
    const id = queue[head]; queued.delete(id);
    const node = graph.projected.get(id), after = transfer(states.get(id), node.effects);
    const outgoing = graph.out.get(id) || [];
    const unconditional = outgoing.some(edge => edge.kind === 'MENU_CHOICE' && predicate(edge.condition).key === 'True');
    for (const edge of outgoing) {
      if (!edge.to || (edge.kind === 'MENU_FALLTHROUGH' && unconditional)) continue;
      const next = CALL.has(edge.kind) || node.type === 'call' ? { must: 0n, any: false } : transfer(after, edge.effects);
      push(edge.to, next);
    }
  }
  return states;
}

export function analyzeRoutes(graph, playable, destination = id => id) {
  const joins = dialogueJoins(graph, playable), memo = new Map(), forks = new Set();
  const conditionedEffects = new WeakMap();
  function underCondition(effect, when) {
    const guard = all([when, predicate(effect.guard)]);
    if (guard.key === 'True' && !effect.guard) return effect;
    let variants = conditionedEffects.get(effect);
    if (!variants) { variants = new Map(); conditionedEffects.set(effect, variants); }
    if (!variants.has(guard.key)) variants.set(guard.key, { ...effect,
      guard: predicateText(guard, new Map(), true), guardPredicate: guard, originalGuard: effect.guard });
    return variants.get(guard.key);
  }
  const flow = new Graph(), cycles = new Map();
  for (const id of graph.projected.keys()) if (!playable.has(id)) flow.setNode(id);
  for (const edge of graph.links.values()) if (flow.hasNode(edge.from) && flow.hasNode(edge.to)) flow.setEdge(edge.from, edge.to);
  for (const ids of alg.tarjan(flow)) if (ids.length > 1 || flow.hasEdge(ids[0], ids[0])) {
    const members = new Set(ids), node = { id: `cycle:${ids[0]}`, type: 'condition', control: 'While', effects: [], controls: [], loopNodes: ids };
    const outgoing = ids.flatMap(id => graph.out.get(id) || []).filter(edge => !members.has(edge.to));
    const cycle = { node, outgoing }; for (const id of ids) cycles.set(id, cycle);
  }
  const nodeAt = id => cycles.get(id)?.node || graph.projected.get(id);
  const outgoingAt = id => cycles.get(id)?.outgoing || graph.out.get(id) || [];
  const canonical = id => cycles.get(id)?.node.id || id;
  const condition = edge => {
    if (edge.kind === 'MENU_FALLTHROUGH') {
      const choices = (graph.out.get(edge.from) || []).filter(edge => edge.kind === 'MENU_CHOICE').map(edge => predicate(edge.condition));
      return choices.some(guard => guard.key === 'True') ? predicate('True') : all(choices.map(negate));
    }
    return predicate(edge.condition);
  };
  function constants(effects, prior = new Map()) {
    const facts = new Map(prior);
    for (const effect of effects) {
      if (effect.unknown && effect.reason !== 'presentation-statement') { facts.clear(); continue; }
      if (!effect.target) continue;
      const ast = expression(effect.expression);
      if (effect.operator === 'set' && !effect.guard && ast.type === 'literal' && typeof ast.value === 'boolean') facts.set(effect.target, ast.value);
      else facts.delete(effect.target);
    }
    return facts;
  }
  function summaries(start) {
    if (memo.has(start)) return memo.get(start);
    const stack = [{ id: start, index: 0 }], active = new Set([canonical(start)]);
    while (stack.length) {
      const frame = stack.at(-1), node = nodeAt(frame.id), outgoing = outgoingAt(frame.id);
      if (playable.has(frame.id) || !node || !outgoing.length) {
        const terminal = playable.has(frame.id) ? '' : node?.loopNodes ? 'loop' : node?.type === 'return' ? 'return' : node?.type === 'end' ? 'end' : 'unresolved';
        memo.set(frame.id, new Map([[playable.has(frame.id) ? frame.id : `terminal:${terminal}`,
          { to: playable.has(frame.id) ? frame.id : null, terminal, guard: predicate('True'), plan: node && !playable.has(frame.id) ? { node, branches: [] } : null }]]));
        if (node?.loopNodes) for (const id of node.loopNodes) memo.set(id, memo.get(frame.id));
        active.delete(canonical(frame.id)); stack.pop(); continue;
      }
      let pending = false;
      while (frame.index < outgoing.length) {
        const edge = outgoing[frame.index++];
        if (!memo.has(edge.to) && !active.has(canonical(edge.to))) { active.add(canonical(edge.to)); stack.push({ id: edge.to, index: 0 }); pending = true; break; }
      }
      if (pending) continue;
      const results = new Map(), ownFacts = constants(node.effects);
      for (const edge of outgoing) {
        for (const [target, child] of memo.get(edge.to) || []) {
          const guard = node.loopNodes ? predicate('True') : substitute(all([condition(edge), substitute(child.guard, constants(edge.effects))]), ownFacts);
          if (guard.key === 'False') continue;
          let result = results.get(target);
          if (!result) { result = { to: child.to, terminal: child.terminal, guard, plan: { node, branches: [] } }; results.set(target, result); }
          else result.guard = any([result.guard, guard]);
          result.plan.branches.push({ edge, next: child.plan,
            exitGuard: node.loopNodes ? all([condition(edge), substitute(child.guard, constants(edge.effects))]) : null });
        }
      }
      if (node.type === 'condition' && node.control === 'If' && results.size > 1) {
        const targets = new Map();
        for (const [target, result] of results) {
          if (result.terminal === 'return') continue;
          for (const branch of result.plan.branches) {
            if (!targets.has(branch.edge.id)) targets.set(branch.edge.id, []);
            targets.get(branch.edge.id).push(destination(target));
          }
        }
        if (new Set([...targets.values()].flat()).size > 1
          && new Set([...targets.values()].map(ids => [...new Set(ids)].sort().join('\0'))).size > 1) forks.add(node.id);
      }
      memo.set(frame.id, results); if (node.loopNodes) for (const id of node.loopNodes) memo.set(id, results);
      active.delete(canonical(frame.id)); stack.pop();
    }
    return memo.get(start);
  }
  function collect(first, summary) {
    const plans = [], visited = new Set();
    function visit(plan) { if (!plan || visited.has(plan)) return; visited.add(plan);
      for (const branch of plan.branches) visit(branch.next); plans.push(plan); }
    visit(summary.plan); plans.reverse();
    const byNode = new Map(plans.map(plan => [plan.node.id, plan]));
    const reach = new Map(), factsAt = new Map(), effects = [...visibleEffects(first)], blocks = new Set(first.via || []),
      sources = new Set(first.sources || [first.id]), checks = [], hidden = [], calls = new Set(), loops = [];
    const track = (record, block) => {
      for (const id of record.sources || [record.id]) sources.add(id);
      for (const effect of visibleEffects(record).filter(effect => effect.unknown)) hidden.push({
        reason: effect.reason || '动态剧情影响未解析', id: record.id, path: block?.path, line: block?.line });
      if (record.precision?.topology !== 'exact') hidden.push({ reason: '此连接尚未完全解析',
        id: record.id, path: block?.path, line: block?.line });
    };
    track(first, graph.blocks.get(first.from));
    if (summary.plan) { reach.set(summary.plan, predicate('True')); factsAt.set(summary.plan, constants(first.effects)); }
    for (const plan of plans) {
      const when = reach.get(plan); if (!when || when.key === 'False') continue;
      const node = plan.node, facts = constants(node.effects, factsAt.get(plan)); blocks.add(node.id);
      if (node.loopNodes) {
        for (const id of node.loopNodes) {
          const block = graph.projected.get(id); blocks.add(id);
          for (const effect of visibleEffects(block).filter(effect => effect.unknown)) hidden.push({
            reason: effect.reason || '动态剧情影响未解析', id, path: block.path, line: block.line });
          for (const edge of graph.out.get(id) || []) { track(edge, block); for (const via of edge.via || []) blocks.add(via); }
        }
        loops.push({ blocks: node.loopNodes,
          effects: node.loopNodes.flatMap(id => [...visibleEffects(graph.projected.get(id)),
            ...(graph.out.get(id) || []).filter(edge => node.loopNodes.includes(edge.to)).flatMap(visibleEffects)]),
          exitGuards: plan.branches.map(branch => branch.exitGuard) });
        facts.clear();
      }
      for (const effect of visibleEffects(node)) {
        effects.push(underCondition(effect, when));
      }
      if (node.type === 'unknown') hidden.push({ reason: node.control === 'UserStatement' ? '自定义语句未解析' : '动态剧情影响未解析', id: node.id, path: node.path, line: node.line });
      for (const effect of visibleEffects(node).filter(effect => effect.unknown)) hidden.push({
        reason: effect.reason || '动态剧情影响未解析', id: node.id, path: node.path, line: node.line });
      if (node.type === 'call' || node.type === 'return') calls.add(node.id);
      const neutral = node.type === 'condition' && node.control === 'If' ? joins(node.id) : null;
      if (neutral && neutral.target !== node.id && (byNode.has(neutral.target) || neutral.target === summary.to)) {
        const next = byNode.get(neutral.target);
        for (const id of neutral.blocks) { blocks.add(id); for (const edge of graph.out.get(id) || []) track(edge, graph.blocks.get(id)); }
        checks.push({ flags: [...new Set([...neutral.blocks].flatMap(id => graph.blocks.get(id)?.controls || []))], blocks: [...neutral.blocks] });
        if (next) { reach.set(next, any([reach.get(next) || predicate('False'), when]));
          const old = factsAt.get(next); factsAt.set(next, old ? new Map([...old].filter(([name, value]) => facts.get(name) === value)) : facts); }
        continue;
      }
      for (const branch of plan.branches) {
        const edge = branch.edge, nextWhen = node.loopNodes ? when : all([when, substitute(condition(edge), facts)]);
        if (nextWhen.key === 'False') continue;
        track(edge, graph.blocks.get(edge.from));
        for (const id of edge.via || []) blocks.add(id);
        for (const effect of visibleEffects(edge)) effects.push(underCondition(effect, nextWhen));
        if (!branch.next) continue;
        reach.set(branch.next, any([reach.get(branch.next) || predicate('False'), nextWhen]));
        const incoming = constants(edge.effects, facts), old = factsAt.get(branch.next);
        factsAt.set(branch.next, old ? new Map([...old].filter(([name, value]) => incoming.get(name) === value)) : incoming);
      }
    }
    return { effects, blocks: [...blocks], sources: [...sources], checks, hidden, calls, loops };
  }
  const groups = [];
  for (const source of playable) { for (const first of graph.out.get(source) || []) {
    for (const summary of summaries(first.to).values()) {
      const guard = all([condition(first), substitute(summary.guard, constants(first.effects))]);
      if (guard.key === 'False') continue;
      groups.push({ from: source, first, to: summary.to, terminal: summary.terminal, guard, ...collect(first, summary) });
    }
  }}
  const factsByFlags = new Map(), normal = [], backup = [];
  const negativeFlags = guard => { const terms = guard.op === 'all' ? guard.terms : [guard];
    return terms.every(term => term.op === 'not' && term.child.flag) ? terms.map(term => term.child.flag).sort() : []; };
  for (const route of groups) {
    const flags = negativeFlags(route.guard), key = flags.join('\0');
    if (flags.length >= 2 && !factsByFlags.has(key)) factsByFlags.set(key, guaranteedFlagGroups(graph, flags));
  }
  function proven(guard, source) {
    const flags = [], visit = value => {
      if (value.flag) { flags.push(value.flag); return true; }
      return value.op === 'any' && value.terms.every(visit);
    };
    if (visit(guard) && factsByFlags.get([...new Set(flags)].sort().join('\0'))?.get(source)?.any) return predicate('True');
    return guard;
  }
  for (const route of groups) {
    const flags = negativeFlags(route.guard);
    const unconditional = (graph.out.get(route.from) || []).some(edge => edge.kind === 'MENU_CHOICE' && predicate(edge.condition).key === 'True');
    let reason = route.first.kind === 'MENU_FALLTHROUGH' && unconditional ? '无可见选项的备用连接'
      : route.first.kind === 'SCREEN_RETURN_EDGE' && route.first.derivation === 'call-screen-return-model'
        ? '未确认的界面返回' : '';
    if (flags.length >= 2) {
      const key = flags.join('\0');
      if (factsByFlags.get(key).get(route.from)?.any) reason = '需要清除已获得的标记';
    }
    if (!reason) route.guard = proven(route.guard, route.from);
    route.labels = [route.first.choice || (route.first.kind === 'MENU_FALLTHROUGH' ? '无可见选项' : ''),
      route.guard.key !== 'True' ? `条件：${predicateText(route.guard, new Map(), true)}` : ''].filter(Boolean);
    route.effectRecords = route.effects; route.effects = route.effects.map(effectText);
    route.backupReason = reason; (reason ? backup : normal).push(route);
  }
  return { normal, backup, forks };
}
