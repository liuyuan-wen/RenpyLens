import { Graph, alg } from '@dagrejs/graphlib';

const JUMPS = new Set(['SCREEN_JUMP_EDGE', 'SCREEN_JUMP', 'SCREEN_ARGUMENT_JUMP']);

// A navigation screen is a player interaction, not transparent dialogue.
// Screens connected by a menu-free loop share one exploration area; their
// individual actions and conditions still remain separate route records.
export function navigationRegions(graph) {
  const flow = new Graph(), stops = new Set();
  for (const node of graph.projected.values()) {
    if (node.type === 'choice') continue;
    flow.setNode(node.id);
    if ((graph.out.get(node.id) || []).some(edge => JUMPS.has(edge.kind))) stops.add(node.id);
  }
  for (const edge of graph.links.values())
    if (flow.hasNode(edge.from) && flow.hasNode(edge.to)) flow.setEdge(edge.from, edge.to);
  const members = new Map(), nodes = [];
  for (const blocks of alg.tarjan(flow)) {
    const screens = blocks.filter(id => stops.has(id)).sort((a, b) => {
      const left = graph.blocks.get(a), right = graph.blocks.get(b);
      return left.path.localeCompare(right.path) || left.line - right.line || a.localeCompare(b);
    });
    if (!screens.length) continue;
    const first = graph.blocks.get(screens[0]), id = `exploration:${first.id}`;
    for (const screen of screens) members.set(screen, id);
    nodes.push({ id, type: 'exploration', title: '自由探索', path: first.path, line: first.line,
      screens, blocks, detailId: id, detailKind: 'node' });
  }
  return { stops, members, nodes };
}
