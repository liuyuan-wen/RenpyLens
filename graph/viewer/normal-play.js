import { expression, isReplayVariable, normalPlayCondition } from './expressions.js';
import { parser } from '@lezer/python';

function writesReplay(effect) {
  if (isReplayVariable(effect.target)) return true;
  if (!effect.unknown || !effect.source) return false;
  let writes = false;
  parser.parse(effect.source).iterate({ enter(ref) {
    if (ref.name !== 'AssignStatement') return;
    const target = ref.node.firstChild;
    if (target && isReplayVariable(effect.source.slice(target.from, target.to))) writes = true;
  } });
  return writes;
}

export function normalPlayGraph(graph) {
  const nodes = [...graph.projected.values()], links = [...graph.links.values()];
  const excluded = new Set(), excludedBlocks = new Set();
  // An explicit game write overrides the engine default. Keep that game's
  // flow unchanged rather than guessing how its custom replay works.
  if ([...graph.blocks.values(), ...graph.rawEdges.values()].some(record =>
    record.effects.some(writesReplay)))
    return { graph, excluded, excludedBlocks };
  const effects = records => records.flatMap(effect => {
    const ast = expression(effect.source || effect.expression);
    if (!effect.target && ast.type === 'call' && ast.name === 'renpy.end_replay' && !ast.args.length) return [];
    const guard = normalPlayCondition(effect.guard);
    return guard === 'False' ? [] : [{ ...effect, guard: guard === 'True' ? '' : guard }];
  });
  const outgoing = new Map(nodes.map(node => [node.id, []]));
  const rejected = [];
  const accepted = links.flatMap(edge => {
    const condition = edge.kind === 'MENU_FALLTHROUGH' ? edge.condition : normalPlayCondition(edge.condition);
    if (condition === 'False' && condition !== edge.condition) { if (edge.to) rejected.push(edge.to); return []; }
    const next = { ...edge, condition, originalCondition: edge.condition, effects: effects(edge.effects) };
    outgoing.get(edge.from).push(next); return [next];
  });
  const reach = (roots, outgoing) => {
    const reached = new Set(roots), queue = [...reached];
    for (let i = 0; i < queue.length; i++) for (const edge of outgoing.get(queue[i]) || [])
      if (edge.to && !reached.has(edge.to)) { reached.add(edge.to); queue.push(edge.to); }
    return reached;
  };
  // Only remove descendants of a proven replay branch. Other disconnected
  // scenes, including isolated cycles, remain independent normal-play roots.
  const candidates = reach(rejected, graph.out);
  const roots = [...graph.entries.map(entry => graph.anchor(entry.id)),
    ...nodes.filter(node => !candidates.has(node.id) || !graph.inc.get(node.id)?.length).map(node => node.id)];
  const reached = reach(roots, outgoing);
  for (const node of nodes) if (!reached.has(node.id)) { excluded.add(node.id); excludedBlocks.add(node.id); }
  const result = Object.create(graph);
  result.projected = new Map(nodes.filter(node => reached.has(node.id)).map(node =>
    [node.id, { ...node, effects: effects(node.effects) }]));
  result.links = new Map(); result.out = new Map(); result.inc = new Map();
  const retainedBlocks = new Set(reached);
  for (const edge of accepted) if (reached.has(edge.from)) {
    result.links.set(edge.id, edge);
    if (!result.out.has(edge.from)) result.out.set(edge.from, []);
    result.out.get(edge.from).push(edge);
    if (edge.to) {
      if (!result.inc.has(edge.to)) result.inc.set(edge.to, []);
      result.inc.get(edge.to).push(edge);
    }
    for (const id of edge.via || []) retainedBlocks.add(id);
  }
  for (const edge of links) for (const id of edge.via || [])
    if (!retainedBlocks.has(id)) excludedBlocks.add(id);
  return { graph: result, excluded, excludedBlocks };
}
