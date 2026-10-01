import { wrap as wrapText, wrapLabel } from './layout.js';
import { analyzeRoutes } from './route-analysis.js';
import { any, describeEffects, predicate, predicateText, readable } from './expressions.js';
import { navigationRegions } from './navigation.js';

// The player map is a semantic projection. Ren'Py control-flow blocks are
// useful to the developer view, but they are not story events for a player.
const glyphs = new Map();
const measureContext = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(1, 1).getContext('2d') : null;
if (measureContext) measureContext.font = '15px "Segoe UI", "Microsoft YaHei", sans-serif';
function measure(text) { let width = 0; for (const c of String(text)) { if (!glyphs.has(c)) glyphs.set(c, measureContext ? measureContext.measureText(c).width : c.charCodeAt(0) > 255 ? 15 : 8); width += glyphs.get(c); } return width; }
const wrap = (text, width, lines) => wrapText(text, width, lines, measure);
const labelLines = (edge, width, maxLines) => {
  const result = wrapLabel(edge.label || '', width, maxLines, measure);
  edge.lines = result.lines; edge.lineRoles = result.roles;
};
// Variable changes are shown on the branch label and in details. Rendering
// every assignment as a separate node creates the implementation graph again.
// Retain player decisions, real route splits and exploration boundaries.
const PLAYER_TYPES = new Set(['choice']);
const short = (value, max = 96) => { const text = String(value || '').replace(/\s+/g, ' ').trim(); return text.length > max ? `${text.slice(0, max - 1)}…` : text; };

function routeProjection(player) {
  const graph = player.flowGraph;
  const navigation = navigationRegions(graph);
  const playable = new Set([...graph.projected.values()].filter(b => PLAYER_TYPES.has(b.type)).map(b => b.id));
  for (const screen of navigation.stops) playable.add(screen);
  const destination = id => navigation.members.get(id) || id;
  const initial = analyzeRoutes(graph, playable, destination), byAnswer = new Map(), branchingBlocks = new Set();
  for (const route of initial.normal) {
    if (route.terminal === 'return' || navigation.members.has(route.from)) continue;
    if (!byAnswer.has(route.first.id)) byAnswer.set(route.first.id, []);
    byAnswer.get(route.first.id).push(route);
  }
  for (const routes of byAnswer.values()) {
    const targets = new Set(routes.map(route => destination(route.to || `terminal:${route.terminal}`)));
    if (targets.size > 1) for (const route of routes) for (const block of route.blocks) branchingBlocks.add(block);
  }
  const candidates = [...initial.forks].filter(id => branchingBlocks.has(id));
  const distances = new Map(), queue = [...playable];
  for (const id of queue) distances.set(id, 0);
  for (let i = 0; i < queue.length; i++) for (const edge of graph.out.get(queue[i]) || [])
    if (edge.to && !(edge.kind === 'SCREEN_RETURN_EDGE' && edge.derivation === 'call-screen-return-model')
      && !distances.has(edge.to)) { distances.set(edge.to, distances.get(queue[i]) + 1); queue.push(edge.to); }
  candidates.sort((a, b) => (distances.get(a) ?? Infinity) - (distances.get(b) ?? Infinity));
  const forks = new Set();
  for (const id of candidates) {
    const entrances = new Set(), seen = new Set([id]), pending = [id];
    for (let i = 0; i < pending.length && entrances.size < 2; i++) for (const edge of graph.inc.get(pending[i]) || []) {
      if (edge.kind === 'SCREEN_RETURN_EDGE' && edge.derivation === 'call-screen-return-model') continue;
      if (forks.has(edge.from)) entrances.add(edge.from);
      else if (playable.has(edge.from)) entrances.add(edge.kind === 'MENU_CHOICE' ? edge.id : navigation.members.get(edge.from) || edge.from);
      else if (!seen.has(edge.from)) { seen.add(edge.from); pending.push(edge.from); }
    }
    if (entrances.size > 1) forks.add(id);
  }
  for (const id of forks) playable.add(id);
  const { normal, backup } = forks.size ? analyzeRoutes(graph, playable, destination) : initial;
  const rawRoutes = [...normal, ...backup];
  const grouped = new Map();
  for (const route of rawRoutes) {
    if (!route.from) continue;
    const from = navigation.members.get(route.from) || route.from;
    const target = navigation.members.get(route.to) || route.to || `terminal:${route.terminal || 'unresolved'}`;
    // v2 screen scans can repeat the same action for different conditions or
    // call sites. Menu answers always retain their own identity.
    const answer = navigation.members.has(route.from) && !route.first.choice && route.first.screen && route.first.label
      ? `screen:${route.first.screen}:${route.first.label}:${JSON.stringify(route.first.effects)}` : route.first.id;
    const key = `${from}\0${target}\0${route.backupReason ? 'backup' : ''}\0${answer}`;
    if (!grouped.has(key)) grouped.set(key, []); grouped.get(key).push(route);
  }
  const nodes = [...graph.projected.values()].filter(b => PLAYER_TYPES.has(b.type) || forks.has(b.id)).map(b => {
    const node = graph.brief(b.id); node.type = b.type === 'condition' ? 'junction' : b.type;
    node.title = b.type === 'choice' ? player.title(b.id)
      : predicateText(predicate((graph.out.get(b.id) || []).find(edge => edge.kind === 'IF_BRANCH')?.condition), player.variableNames);
    node.note = b.type === 'choice' ? `选择 ${player.order.get(b.id)?.number || ''}` : '';
    node.special = false; return node;
  });
  nodes.push(...navigation.nodes);
  const routeDetails = new Map(), routesBySource = new Map(), endNodes = new Map(), edges = [], locations = new Map();
  for (const region of navigation.nodes) for (const block of region.blocks) locations.set(block, region.id);
  for (const [key, routes] of grouped) {
    const [from, target, backupGroup, answerId] = key.split('\0'); let to = target;
    if (target.startsWith('terminal:')) {
      const terminal = target.slice('terminal:'.length) || 'unresolved', sample = routes[0], endKey = `${from}:${terminal}`;
      if (!endNodes.has(endKey)) {
        const id = `${terminal}:${endKey}`; endNodes.set(endKey, id);
        const presentation = terminal === 'end'
          ? { type: 'end', title: '明确剧情结束', note: '脚本明确结束' }
          : terminal === 'return'
            ? { type: 'return', title: '返回调用处', note: '续接位置取决于调用上下文' }
          : terminal === 'loop'
            ? { type: 'boundary', title: '停留在重复流程', note: '' }
            : { type: 'boundary', title: '后续未解析', note: '无可见选项、动态目标或待核对路径' };
        if (!backupGroup) nodes.push({ id, ...presentation, hidden: terminal === 'return', terminal: true, detailId: sample.first?.id, detailKind: 'edge' });
      }
      to = endNodes.get(endKey);
    }
    const lines = [], hidden = [], sources = [], effects = [], calls = new Set();
    const cases = routes.map(route => ({ firstId: route.first?.id, fromBlock: route.from, toBlock: route.to, labels: route.labels,
      effects: route.effects, effectRecords: route.effectRecords, guard: route.guard,
      blocks: route.blocks, checks: route.checks, loops: route.loops, backupReason: route.backupReason, hidden: route.hidden,
      sources: route.sources.filter(Boolean) }));
    for (const route of routes) { const parts = [...route.labels]; const effectLines = [...new Set(route.effects)].filter(Boolean); if (effectLines.length) parts.push(`变化：${effectLines.join('；')}`); lines.push(parts.join('\n') || '继续'); hidden.push(...route.hidden); sources.push(...(route.first?.sources || [route.first?.id]).filter(Boolean)); effects.push(...effectLines); for (const c of route.calls) calls.add(c); }
    const uniqueLines = [...new Set(lines)], id = `${backupGroup ? 'backup' : 'route'}:${from}:${to}:${answerId}`;
    const hasVisibleChoice = routes.some(route => route.labels.some(label => label && label !== '无可见选项' && !label.startsWith('条件：')));
    const mapLines = uniqueLines.map(line => {
      const parts = line.split('\n');
      const choice = parts.find(x => !x.startsWith('条件：') && !x.startsWith('变化：'));
      const condition = parts.find(x => x.startsWith('条件：'));
      const change = parts.find(x => x.startsWith('变化：'));
      const visibleChoice = hasVisibleChoice && choice === '无可见选项' ? '' : choice;
      return [visibleChoice, condition && `条件：${short(condition.slice(3), 58)}`, change && `变化：${short(change.slice(3), 58)}`].filter(Boolean).join('\n') || '继续';
    });
    const displayLines = mapLines.some(line => line !== '继续') ? mapLines.filter(line => line !== '继续') : mapLines;
    const guard = any(routes.map(route => route.guard)), routeCondition = predicateText(guard, player.variableNames);
    const sharedEffects = [];
    for (const [index, effect] of routes[0].effectRecords.entries()) {
      if (!routes.every(route => route.effectRecords[index]
        && JSON.stringify(route.effectRecords[index]) === JSON.stringify(effect))) break;
      sharedEffects.push(effect);
    }
    const shown = [readable(routes[0].first.choice), routeCondition ? `条件：${routeCondition}` : '',
      ...[...new Set(routes.flatMap(route => route.loops.flatMap(loop => loop.exitGuards)
        .map(guard => predicateText(guard, player.variableNames)).filter(Boolean)))].map(text => `条件：循环退出时 ${text}`),
      ...describeEffects(sharedEffects, player.variableNames).filter(row => row.kind === 'effect').slice(0, 2)
        .map(row => `变化：${row.condition ? `若 ${row.condition}：` : ''}${row.text}`)].filter(Boolean).join('\n') || '继续';
    const hiddenUnique = [...new Map(hidden.map(item => [`${item.reason}|${item.path}|${item.line}`, item])).values()];
    const hiddenShown = hiddenUnique.slice(0, 8);
    if (hiddenUnique.length > hiddenShown.length) hiddenShown.push({ reason: `另有 ${hiddenUnique.length - 8} 个相同类型的内部节点`, path: '', line: null, id: '' });
    const fullLines = uniqueLines.slice(0, 24);
    if (uniqueLines.length > fullLines.length) fullLines.push(`另有 ${uniqueLines.length - 24} 条静态路径未展开`);
    const terminal = target.startsWith('terminal:') ? target.slice('terminal:'.length) : '';
    const edge = { id, from, to, choice: routes[0].first.choice, routeCondition, label: shown, routeLines: fullLines, mapLines: displayLines.slice(0, 8), hidden: hiddenShown, sources: [...new Set(sources)], effects: [...new Set(effects)].slice(0, 12), calls: [...calls], detailId: id, detailKind: 'route', slot: routes[0].first?.slot ?? 0, uncertain: hiddenUnique.length > 0, continuation: false, routeCount: routes.length, terminal };
    // Retrying the current choice is available in answer details; it does not
    // need a self-arrow that obscures the decision on the main map.
    if (!backupGroup && from !== to) edges.push(edge);
    routeDetails.set(id, { ...edge, cases, backup: !!backupGroup });
    for (const sourceId of edge.sources) {
      if (!routesBySource.has(sourceId)) routesBySource.set(sourceId, []);
      routesBySource.get(sourceId).push(id);
    }
    if (!backupGroup) { locations.set(from, from); locations.set(to, to);
    for (const sourceId of edge.sources) {
      const source = graph.links.get(sourceId);
      if (source) { locations.set(source.from, navigation.members.get(source.from) || (playable.has(source.from) ? source.from : id)); for (const folded of source.via || []) locations.set(folded, id); }
    }
    for (const route of routes) for (const block of route.blocks)
      if (!playable.has(block) && !locations.get(block)?.startsWith('exploration:')) locations.set(block, id);
    }
  }
  player.navigationDetails = new Map();
  player.wholeForks = forks;
  for (const region of navigation.nodes) {
    const choices = new Set(), seen = new Set([region.id]), queue = [region.id];
    for (let i = 0; i < queue.length; i++) for (const edge of edges.filter(edge => edge.from === queue[i])) {
      if (seen.has(edge.to)) continue; seen.add(edge.to);
      if (player.order.has(edge.to)) choices.add(edge.to);
      else if (forks.has(edge.to)) queue.push(edge.to);
    }
    region.note = `${choices.size} 处剧情选择`;
    player.navigationDetails.set(region.id, { ...region, routes: [...routeDetails.values()].filter(edge => edge.from === region.id) });
  }
  return { nodes, edges, routeDetails, routesBySource, locations, stats: { semanticNodes: nodes.length, semanticEdges: edges.length, hiddenRoutes: [...routeDetails.values()].filter(e => e.hidden.length).length } };
}

export function wholeGraph(player) {
  const { routesBySource, routeDetails, locations, ...result } = routeProjection(player);
  player.wholeLocations = locations; player.routeDetails = routeDetails; player.wholeRoutesBySource = routesBySource;
  return { mode: 'whole', player: true, title: '完整剧情图', anchor: player.first, choiceCount: player.catalog.length, ...result };
}

// Rank semantic events only. Keep one label per semantic connection; return
// overlays may use diagonal lines when that makes their destination clearer.
export function layoutWhole(view) {
  const map = new Map(view.nodes.map(n => [n.id, n])), out = new Map(view.nodes.map(n => [n.id, []]));
  const incoming = new Map(view.nodes.map(n => [n.id, []]));
  for (const e of view.edges) { out.get(e.from)?.push(e); incoming.get(e.to)?.push(e); }
  for (const list of out.values()) list.sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0));
  const visibleOut = new Map([...out].map(([id, edges]) => [id, edges.filter(e => e.terminal !== 'return')]));
  const color = new Map(), feedback = new Set(), order = [];
  const walk = id => { color.set(id, 1); for (const e of out.get(id) || []) { if (color.get(e.to) === 1) feedback.add(e.id); else if (!color.has(e.to)) walk(e.to); } color.set(id, 2); order.push(id); };
  const roots = [view.anchor, ...view.nodes.map(n => n.id).filter(id => id !== view.anchor)];
  for (const id of roots) if (!color.has(id)) walk(id); order.reverse();
  const rank = new Map(view.nodes.map(n => [n.id, 0])); for (const id of order) for (const e of out.get(id) || []) if (!feedback.has(e.id)) rank.set(e.to, Math.max(rank.get(e.to), rank.get(id) + 1));
  const main = new Set([view.anchor]), queue = [view.anchor];
  for (let i = 0; i < queue.length; i++) for (const edge of out.get(queue[i]) || [])
    if (!feedback.has(edge.id) && !main.has(edge.to)) { main.add(edge.to); queue.push(edge.to); }
  const joinsMain = new Set(main);
  for (const id of [...order].reverse()) if (!main.has(id)) {
    const children = (out.get(id) || []).filter(edge => !feedback.has(edge.id) && joinsMain.has(edge.to));
    if (children.length) {
      rank.set(id, Math.max(rank.get(id), Math.min(...children.map(edge => rank.get(edge.to))) - 1));
      joinsMain.add(id);
    }
  }
  for (const id of order) for (const edge of out.get(id) || []) if (!feedback.has(edge.id))
    rank.set(edge.to, Math.max(rank.get(edge.to), rank.get(id) + 1));
  for (const n of view.nodes) { if (n.hidden) { n.lines = []; n.width = 1; n.height = 1; continue; } if (n.type === 'junction') { n.lines = []; n.width = 24; n.height = 24; continue; } const diamond = n.type === 'choice' || n.type === 'condition'; n.lines = wrap(n.title, diamond ? 180 : 230, 3); n.width = diamond ? 270 : n.type === 'end' ? 210 : 240; n.height = diamond ? n.lines.length === 3 ? 128 : 104 : 54 + n.lines.length * 17; }
  for (const e of view.edges) { labelLines(e, 180, 2); e.labelWidth = e.label ? Math.min(210, Math.max(...e.lines.map(measure)) + 18) : 0; e.labelHeight = e.label ? e.lines.length * 16 + 8 : 0; }
  // Disconnected labels are separate playable scenes, not one giant rank.
  // Stack components vertically so the whole map stays narrow and readable.
  const undirected = new Map(view.nodes.map(n => [n.id, []])); for (const e of view.edges) { undirected.get(e.from)?.push(e.to); undirected.get(e.to)?.push(e.from); }
  const components = [], assigned = new Set();
  for (const n of view.nodes) { if (assigned.has(n.id)) continue; const ids = new Set([n.id]), queue = [n.id]; assigned.add(n.id); for (let i = 0; i < queue.length; i++) for (const next of undirected.get(queue[i]) || []) if (!assigned.has(next)) { assigned.add(next); ids.add(next); queue.push(next); } components.push(ids); }
  components.sort((a, b) => (a.has(view.anchor) ? -1 : b.has(view.anchor) ? 1 : 0));
  let y = 70, maxX = 0;
  const tracks = new Map(view.nodes.map(node => {
    const routes = visibleOut.get(node.id), forward = routes.filter(edge => !feedback.has(edge.id));
    const sameParent = new Map();
    for (const edge of incoming.get(node.id).filter(edge => !feedback.has(edge.id)))
      sameParent.set(edge.from, (sameParent.get(edge.from) || 0) + 1);
    return [node.id, Math.max(node.width + 64, forward.length * 250 - 40,
      Math.max(0, ...sameParent.values()) * 250 - 40)];
  }));
  const incomingSlot = new Map();
  for (const edge of view.edges) {
    const slot = edge.slot ?? 0;
    if (!incomingSlot.has(edge.to) || slot < incomingSlot.get(edge.to)) incomingSlot.set(edge.to, slot);
  }
  const visualRows = [], rowIndex = new Map();
  for (const component of components) {
    const localRows = [], placedX = new Map();
    for (const id of order) if (component.has(id) && !map.get(id).hidden) (localRows[rank.get(id)] ||= []).push(map.get(id));
    for (const row of localRows.filter(Boolean)) {
      // Keep parallel branches in their parents' left-to-right order. Answer
      // slots only order siblings, not successors of different decisions.
      const parentSlot = new Map(), parentX = new Map(row.map(n => {
        const parents = incoming.get(n.id).filter(e => rank.get(e.from) < rank.get(n.id) && placedX.has(e.from));
        parentSlot.set(n.id, parents.length ? Math.min(...parents.map(e => e.slot ?? 0)) : incomingSlot.get(n.id) ?? 0);
        return [n.id, parents.length ? parents.reduce((sum, e) => sum + placedX.get(e.from), 0) / parents.length : 760];
      }));
      row.sort((a, b) => parentX.get(a.id) - parentX.get(b.id)
        || parentSlot.get(a.id) - parentSlot.get(b.id)
        || Number(a.type !== 'choice') - Number(b.type !== 'choice'));
      // A rank is a real set of parallel story events. Do not wrap it into
      // artificial rows, which changes the apparent branch order.
      for (const current of [row]) {
        const total = current.reduce((sum, n) => sum + tracks.get(n.id), 0);
        let x = Math.max(760 - total / 2, 60);
        for (const n of current) { n.x = x + tracks.get(n.id) / 2; rowIndex.set(n.id, visualRows.length); placedX.set(n.id, n.x); x += tracks.get(n.id); maxX = Math.max(maxX, x); }
        visualRows.push({ nodes: current, componentEnd: false });
      }
    }
    if (visualRows.length) visualRows.at(-1).componentEnd = true;
  }
  const isFeedback = edge => feedback.has(edge.id);
  const layoutOut = new Map([...visibleOut].map(([id, edges]) => [id, edges.filter(edge => !isFeedback(edge))]));
  for (const row of visualRows) if (row.nodes.length === 1) {
    const node = row.nodes[0];
    const children = [...new Set(layoutOut.get(node.id).map(edge => edge.to))];
    const parents = [...new Set(incoming.get(node.id).filter(edge => edge.terminal !== 'return' && !isFeedback(edge)).map(edge => edge.from))];
    const neighbors = children.length > 1 ? children : parents.length > 1 ? parents : [];
    if (neighbors.length) {
      const xs = neighbors.map(id => map.get(id).x); node.x = (Math.min(...xs) + Math.max(...xs)) / 2;
    }
  }
  const forwardOut = layoutOut;
  const forwardIn = new Map(view.nodes.map(node => [node.id, incoming.get(node.id).filter(edge => edge.terminal !== 'return' && !isFeedback(edge))]));
  for (const node of view.nodes) {
    const routes = forwardOut.get(node.id), targets = new Map();
    for (const edge of routes) {
      if (!targets.has(edge.to)) targets.set(edge.to, []);
      targets.get(edge.to).push(edge);
    }
    for (const [target, answers] of targets) {
      const center = routes.length === 1 && forwardIn.get(target).length > 1
        ? node.x : map.get(target).x;
      for (const [i, edge] of answers.entries()) edge.x = center + (i - (answers.length - 1) / 2) * 250;
    }
    const ordered = [...routes].sort((a, b) => map.get(a.to).x - map.get(b.to).x
      || (a.slot ?? 0) - (b.slot ?? 0));
    const preferred = ordered.map(edge => edge.x);
    for (let i = 1; i < ordered.length; i++) {
      const previous = ordered[i - 1], edge = ordered[i];
      edge.x = Math.max(edge.x, previous.x + (previous.labelWidth + edge.labelWidth) / 2 + 28);
    }
    if (ordered.length > 1) {
      const shift = preferred.reduce((sum, x, i) => sum + x - ordered[i].x, 0) / ordered.length;
      for (const edge of ordered) edge.x += shift;
    }
  }
  const markerText = (edge, target, symbol) => {
    const destination = target.type === 'exploration' ? '自由探索'
      : symbol === '→' && target.type === 'choice' ? `${target.note} · ${short(target.title, 54)}`
        : target.note?.match(/选择\s*\d+/)?.[0] || target.title;
    const choice = readable(edge.choice), destinationText = `${symbol} ${destination}`;
    const text = [choice || destinationText, edge.routeCondition && `条件：${edge.routeCondition}`,
      choice && destinationText].filter(Boolean);
    const lines = [], lineRoles = [];
    for (const [i, value] of text.entries()) {
      const role = value.startsWith('条件：') ? 'condition' : 'primary';
      const wrapped = wrap(value, 180, i === 0 && (edge.choice || symbol === '→' && target.type === 'choice') ? 2 : 1);
      lines.push(...wrapped); lineRoles.push(...wrapped.map(() => role));
    }
    return { label: text.join('\n'), lines, lineRoles,
      labelWidth: Math.min(210, Math.max(...lines.map(measure)) + 18), labelHeight: lines.length * 16 + 8 };
  };
  // Reserve continuous lanes through intermediate ranks before sizing fork
  // and join rails. Long edges remain connected to their actual destination.
  const channelSpans = [], longTurns = new Map();
  for (const edge of view.edges.filter(edge => edge.terminal !== 'return' && !isFeedback(edge))) {
    const first = rowIndex.get(edge.from), last = rowIndex.get(edge.to);
    if (last <= first + 1) continue;
    const boxes = visualRows.slice(first + 1, last).flatMap(row => row.nodes.flatMap(node => [
      { left: node.x - node.width / 2 - 22, right: node.x + node.width / 2 + 22 },
      ...forwardOut.get(node.id).map(label => ({ left: label.x - label.labelWidth / 2 - 16,
        right: label.x + label.labelWidth / 2 + 16 }))
    ]));
    const occupied = channelSpans.filter(span => span.last > first && span.first < last && span.to !== edge.to);
    const targetX = map.get(edge.to).x;
    const candidates = [edge.x, targetX, ...boxes.flatMap(box => [box.left - 1, box.right + 1]),
      ...occupied.flatMap(span => [span.x - 24, span.x + 24])];
    candidates.sort((a, b) => Math.abs(a - edge.x) + Math.abs(a - targetX)
      - Math.abs(b - edge.x) - Math.abs(b - targetX));
    edge.channel = candidates.find(x => boxes.every(box => x < box.left || x > box.right)
      && occupied.every(span => Math.abs(span.x - x) >= 18));
    channelSpans.push({ x: edge.channel, first, last, from: edge.from, to: edge.to });
  }
  const forkRails = new Map(), joinRails = new Map();
  // Only routes with a common source or destination may share a rail.
  // Separate unrelated horizontal spans before placing their labels.
  const railLevels = groups => {
    const levels = [];
    for (const group of groups.sort((a, b) => a.left - b.left || a.right - b.right)) {
      if (group.left === group.right) { group.level = 0; continue; }
      let level = levels.findIndex(spans => spans.every(span => group.left > span.right + 20 || group.right < span.left - 20));
      if (level < 0) { level = levels.length; levels.push([]); }
      levels[level].push(group); group.level = level;
    }
  };
  for (const row of visualRows) {
    const forks = [], joins = [];
    for (const node of row.nodes) {
      const outgoing = forwardOut.get(node.id), entering = forwardIn.get(node.id);
      const fork = { left: Math.min(node.x, ...outgoing.map(edge => edge.x)), right: Math.max(node.x, ...outgoing.map(edge => edge.x)) };
      const join = { left: Math.min(node.x, ...entering.map(edge => edge.channel ?? edge.x)), right: Math.max(node.x, ...entering.map(edge => edge.channel ?? edge.x)) };
      forks.push(fork); joins.push(join); forkRails.set(node.id, fork); joinRails.set(node.id, join);
    }
    railLevels(forks); railLevels(joins);
    const turns = [], turnsByTarget = new Map();
    for (const node of row.nodes) {
      const routes = forwardOut.get(node.id).filter(edge => edge.channel !== undefined);
      for (const edge of routes) {
        if (!turnsByTarget.has(edge.to)) turnsByTarget.set(edge.to, []);
        turnsByTarget.get(edge.to).push(edge);
      }
    }
    for (const routes of turnsByTarget.values()) {
      const turn = { left: Math.min(...routes.flatMap(edge => [edge.x, edge.channel])),
        right: Math.max(...routes.flatMap(edge => [edge.x, edge.channel])) };
      turns.push(turn); for (const edge of routes) longTurns.set(edge.id, turn);
    }
    railLevels(turns);
    row.turnDepth = Math.max(0, ...turns.map(turn => turn.level)) * 18;
  }
  const corridors = new Map(), rowLabelTop = new Map(), rowLabelBottom = new Map();
  for (const row of visualRows) {
    const labelTop = 20 + Math.max(...row.nodes.map(node => forkRails.get(node.id).level)) * 18 + 14;
    rowLabelTop.set(row, labelTop);
    rowLabelBottom.set(row, Math.max(0, ...row.nodes.flatMap(node => forwardOut.get(node.id)
      .map(edge => labelTop + edge.labelHeight))));
  }
  for (const node of view.nodes) {
    const forward = forwardOut.get(node.id);
    const forwardHeight = Math.max(0, ...forward.map(edge => edge.labelHeight));
    const row = visualRows[rowIndex.get(node.id)], rowHeight = row ? Math.max(...row.nodes.map(node => node.height)) : node.height;
    const forkOffset = (rowHeight - node.height) / 2 + 20 + (forkRails.get(node.id)?.level || 0) * 18;
    const joinInset = Math.max(0, ...forward.map(edge => {
      const rail = joinRails.get(edge.to); return rail.left === rail.right ? 0 : 22 + rail.level * 18;
    }));
    corridors.set(node.id, { forkOffset,
      gap: Math.max(100, forward.length ? (rowHeight - node.height) / 2 + rowLabelTop.get(row)
        + forwardHeight + 36 + (row?.turnDepth || 0) + joinInset : 0) });
  }
  // Allocate label and rail space; overlay returns reserve no story rows.
  for (const row of visualRows) {
    const h = Math.max(...row.nodes.map(node => node.height));
    row.top = y; row.bottom = y + h;
    for (const node of row.nodes) node.y = y + h / 2;
    y += h + Math.max(...row.nodes.map(node => corridors.get(node.id).gap)) + (row.componentEnd ? 38 : 0);
  }
  for (const n of view.nodes.filter(n => n.hidden)) {
    const parent = map.get(incoming.get(n.id)[0]?.from);
    n.x = parent?.x ?? 60; n.y = parent?.y ?? 70;
  }
  for (const row of visualRows) {
    for (const node of row.nodes) for (const edge of forwardOut.get(node.id)) {
      edge.y = row.bottom + rowLabelTop.get(row) + edge.labelHeight / 2;
    }
  }
  // Returns are focus overlays. Plan all edges incident to one node together:
  // selecting a destination can reveal returns from several different sources.
  const returnPairs = new Map();
  for (const edge of view.edges.filter(edge => edge.terminal !== 'return' && isFeedback(edge))) {
    const key = `${edge.from}\0${edge.to}`;
    if (!returnPairs.has(key)) returnPairs.set(key, []);
    returnPairs.get(key).push(edge);
  }
  const replacements = new Map();
  view.edgeAliases = {};
  for (const members of returnPairs.values()) {
    if (members.length < 2) continue;
    members.sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0) || a.id.localeCompare(b.id));
    const first = members[0], id = `route:returns:${first.from}:${first.to}`;
    const merged = { ...first, id, choice: '', routeCondition: '', detailId: id, detailKind: 'return-group',
      returnMembers: members.map(edge => ({ id: edge.id, choice: edge.choice,
        condition: edge.routeCondition, label: edge.label, slot: edge.slot })),
      uncertain: members.some(edge => edge.uncertain), routeCount: members.reduce((sum, edge) => sum + (edge.routeCount || 1), 0) };
    feedback.add(id);
    for (const edge of members) { replacements.set(edge.id, merged); view.edgeAliases[edge.id] = id; }
  }
  const displayIds = new Set();
  view.edges = view.edges.map(edge => replacements.get(edge.id) || edge).filter(edge => {
    if (displayIds.has(edge.id)) return false;
    displayIds.add(edge.id); return true;
  });
  const returns = view.edges.filter(edge => edge.terminal !== 'return' && isFeedback(edge));
  for (const edge of returns) {
    edge.routeLabel = edge.label;
    if (edge.returnMembers) {
      const options = [...new Map(edge.returnMembers.map(member => [JSON.stringify([readable(member.choice), member.condition]), member])).values()];
      const lines = [], lineRoles = [];
      for (const option of options.slice(0, 4)) {
        const choice = readable(option.choice);
        const wrapped = wrap(`\u2022 ${choice || (option.condition ? `条件：${option.condition}` : '继续')}`, 210, 2);
        lines.push(...wrapped); lineRoles.push(...wrapped.map(() => 'primary'));
        if (choice && option.condition) {
          const condition = wrap(`条件：${option.condition}`, 210, 1);
          lines.push(...condition); lineRoles.push(...condition.map(() => 'condition'));
        }
      }
      if (options.length > 4) { lines.push(`另有 ${options.length - 4} 条返回分支`); lineRoles.push('annotation'); }
      const destination = markerText({ ...edge, choice: '' }, map.get(edge.to), '↶ 返回');
      lines.push(...destination.lines); lineRoles.push(...destination.lineRoles);
      edge.routeLabel = edge.returnMembers.map(member => member.label).join('\n');
      Object.assign(edge, { label: [...options.map(option => [readable(option.choice),
        option.condition && `条件：${option.condition}`].filter(Boolean).join('\n') || '继续'), destination.label].join('\n'), lines, lineRoles,
        labelWidth: Math.min(230, Math.max(...lines.map(measure)) + 18), labelHeight: lines.length * 19 + 12 });
    } else Object.assign(edge, markerText(edge, map.get(edge.to), '↶ 返回'));
  }
  const returnObstacles = [
    ...view.nodes.filter(node => !node.hidden).map(node => ({
      id: node.id, left: node.x - node.width / 2 - 6, right: node.x + node.width / 2 + 6,
      top: node.y - node.height / 2 - 6, bottom: node.y + node.height / 2 + 6
    })),
    ...view.edges.filter(edge => edge.terminal !== 'return' && !isFeedback(edge) && edge.label).map(edge => {
      const label = edge;
      return { id: edge.id, left: edge.x - label.labelWidth / 2 - 4,
        right: edge.x + label.labelWidth / 2 + 4,
        top: edge.y - label.labelHeight / 2 - 4, bottom: edge.y + label.labelHeight / 2 + 4 };
    })
  ];
  const crossesBox = (a, b, box) => {
    if (Math.max(a.x, b.x) < box.left || Math.min(a.x, b.x) > box.right
      || Math.max(a.y, b.y) < box.top || Math.min(a.y, b.y) > box.bottom) return false;
    let enter = 0, leave = 1;
    for (const [start, step, low, high] of [[a.x, b.x - a.x, box.left, box.right],
      [a.y, b.y - a.y, box.top, box.bottom]]) {
      if (!step) { if (start < low || start > high) return false; continue; }
      const first = (low - start) / step, last = (high - start) / step;
      enter = Math.max(enter, Math.min(first, last)); leave = Math.min(leave, Math.max(first, last));
      if (enter > leave) return false;
    }
    return true;
  };
  const cellSize = 260, obstacleCells = new Map();
  const cellsFor = box => {
    const cells = [];
    for (let x = Math.floor(box.left / cellSize); x <= Math.floor(box.right / cellSize); x++)
      for (let y = Math.floor(box.top / cellSize); y <= Math.floor(box.bottom / cellSize); y++) cells.push(`${x}:${y}`);
    return cells;
  };
  for (const box of returnObstacles) for (const cell of cellsFor(box)) {
    if (!obstacleCells.has(cell)) obstacleCells.set(cell, []);
    obstacleCells.get(cell).push(box);
  }
  const nearbyBoxes = box => [...new Set(cellsFor(box).flatMap(cell => obstacleCells.get(cell) || []))]
    .filter(other => other.left < box.right && other.right > box.left
      && other.top < box.bottom && other.bottom > box.top);
  const boxAt = (point, edge, padding = 4) => ({
    left: point.x - edge.labelWidth / 2 - padding, right: point.x + edge.labelWidth / 2 + padding,
    top: point.y - edge.labelHeight / 2 - padding, bottom: point.y + edge.labelHeight / 2 + padding
  });
  const clearancePoints = (source, target, edge, simultaneous) => {
    const boxes = [...nearbyBoxes({ left: Math.min(source.x, target.x) - 500,
      right: Math.max(source.x, target.x) + 500, top: Math.min(source.y, target.y) - 500,
      bottom: Math.max(source.y, target.y) + 500 }).filter(box => map.has(box.id)),
      ...simultaneous.map(other => boxAt(other, other))];
    const xs = new Set([source.x, target.x]), ys = new Set([source.y, target.y]);
    for (const box of boxes) {
      xs.add(box.left - edge.labelWidth / 2 - 5); xs.add(box.right + edge.labelWidth / 2 + 5);
      ys.add(box.top - edge.labelHeight / 2 - 5); ys.add(box.bottom + edge.labelHeight / 2 + 5);
    }
    return [...xs].flatMap(x => [...ys].map(y => ({ x, y })));
  };
  const angleTo = (node, other) => Math.atan2((other.y - node.y) / node.height,
    (other.x - node.x) / node.width);
  const portOffsets = new Map();
  for (const end of ['from', 'to']) {
    const groups = new Map();
    for (const edge of returns) {
      const node = map.get(edge[end]), other = map.get(edge[end === 'from' ? 'to' : 'from']);
      const key = `${node.id}:${Math.round(angleTo(node, other) / .48)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(edge);
    }
    for (const group of groups.values()) {
      group.sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0) || a.id.localeCompare(b.id));
      group.forEach((edge, index) => portOffsets.set(`${end}:${edge.id}`,
        (index - (group.length - 1) / 2) * Math.min(.15, .7 / group.length)));
    }
  }
  const port = (node, other, offset) => {
    const angle = angleTo(node, other) + offset, dx = Math.cos(angle), dy = Math.sin(angle);
    const scale = node.type === 'choice' || node.type === 'condition'
      ? Math.abs(dx) + Math.abs(dy) : Math.max(Math.abs(dx), Math.abs(dy));
    return { x: node.x + node.width / 2 * dx / scale,
      y: node.y + node.height / 2 * dy / scale };
  };
  const shelfPoints = new Map(), byTarget = new Map();
  for (const edge of returns) {
    if (!byTarget.has(edge.to)) byTarget.set(edge.to, []);
    byTarget.get(edge.to).push(edge);
  }
  for (const [id, edges] of byTarget) {
    const target = map.get(id), shelves = new Map();
    for (const edge of edges) {
      const source = map.get(edge.from);
      const side = source.y > target.y + target.height / 2 ? 1 : -1;
      if (!shelves.has(side)) shelves.set(side, []);
      shelves.get(side).push(edge);
    }
    for (const [side, group] of shelves) {
      if (group.length < 3) continue;
      group.sort((a, b) => map.get(a.from).x - map.get(b.from).x
        || (a.slot ?? 0) - (b.slot ?? 0) || a.id.localeCompare(b.id));
      const sourceCenter = group.reduce((sum, edge) => sum + map.get(edge.from).x, 0) / group.length;
      const center = (sourceCenter + target.x) / 2;
      const height = Math.max(...group.map(edge => edge.labelHeight));
      const widest = Math.max(...group.map(edge => edge.labelWidth));
      const columns = Math.min(4, group.length,
        Math.max(2, Math.floor((Math.abs(target.x - sourceCenter) + 300) / (widest + 12))));
      for (let row = 0; row * columns < group.length; row++) {
        const items = group.slice(row * columns, (row + 1) * columns);
        const width = items.reduce((sum, edge) => sum + edge.labelWidth, 0) + (items.length - 1) * 12;
        const y = target.y + side * (target.height / 2 + height / 2 + 26 + row * (height + 12));
        let x = center - width / 2;
        for (const edge of items) {
          shelfPoints.set(edge.id, { x: x + edge.labelWidth / 2, y });
          x += edge.labelWidth + 12;
        }
      }
    }
  }
  const intersection = (a, b, c, d) => {
    const dx = b.x - a.x, dy = b.y - a.y, ex = d.x - c.x, ey = d.y - c.y;
    const divisor = dx * ey - dy * ex;
    if (Math.abs(divisor) < 1e-6) return null;
    const u = ((c.x - a.x) * ey - (c.y - a.y) * ex) / divisor;
    const v = ((c.x - a.x) * dy - (c.y - a.y) * dx) / divisor;
    return u > .02 && u < .98 && v > .02 && v < .98
      ? { x: a.x + u * dx, y: a.y + u * dy } : null;
  };
  const occupied = new Map(), degrees = new Map();
  for (const edge of returns) for (const id of [edge.from, edge.to]) degrees.set(id, (degrees.get(id) || 0) + 1);
  returns.sort((a, b) => Math.max(degrees.get(b.from), degrees.get(b.to))
    - Math.max(degrees.get(a.from), degrees.get(a.to))
    || map.get(a.from).y - map.get(b.from).y || map.get(a.from).x - map.get(b.from).x
    || (a.slot ?? 0) - (b.slot ?? 0) || a.id.localeCompare(b.id));
  const occupyReturn = edge => {
    for (const id of [edge.from, edge.to]) {
      if (!occupied.has(id)) occupied.set(id, []);
      occupied.get(id).push(edge);
    }
  };
  // Sibling branches returning to their parent form one visual fan. Choose
  // their label row together; independent searches destroy its symmetry.
  for (const [id, edges] of byTarget) {
    const target = map.get(id), rows = new Map();
    for (const edge of edges) {
      const source = map.get(edge.from), row = rowIndex.get(source.id);
      if (source.y <= target.y || !forwardOut.get(id).some(route => route.to === source.id)) continue;
      if (!rows.has(row)) rows.set(row, []);
      rows.get(row).push(edge);
    }
    for (const [row, siblings] of rows) {
      if (siblings.length < 2 || new Set(siblings.map(edge => edge.from)).size !== siblings.length) continue;
      siblings.sort((a, b) => map.get(a.from).x - map.get(b.from).x);
      const sources = siblings.map(edge => map.get(edge.from));
      const height = Math.max(...siblings.map(edge => edge.labelHeight));
      const sourceY = sources[0].y, gap = sourceY - target.y;
      const labelRows = new Set([
        target.y + target.height / 2 + 6,
        target.y + target.height / 2 + height / 2 + 12,
        Math.min(...sources.map(node => node.y - node.height / 2)) - height / 2 - 12,
        ...[0, .18, .32, .46, .6, .74, .88].map(t => target.y + gap * t)
      ]);
      const simultaneous = [...new Set(siblings.flatMap(edge => [
        ...(occupied.get(edge.from) || []), ...(occupied.get(edge.to) || [])
      ]))];
      let best;
      for (const y of labelRows) for (const scale of [.55, .7, .85, 1, 1.15, 1.3, 1.5])
        for (const spread of [0, 40, 80, 120, 160, 200, 240]) {
          const plan = siblings.map((edge, i) => {
            const source = sources[i], point = {
              x: target.x + (source.x - target.x) * scale + Math.sign(source.x - target.x) * spread, y
            };
            const start = port(source, point, 0), end = port(target, point, 0);
            return { edge, point, box: boxAt(point, edge), points: [start, point, end] };
          });
          const overlap = (a, b) => a.left < b.right && a.right > b.left
            && a.top < b.bottom && a.bottom > b.top;
          if (plan.some((item, i) => nearbyBoxes(item.box).some(box => map.has(box.id))
            || plan.slice(i + 1).some(other => overlap(item.box, other.box))
            || simultaneous.some(edge => overlap(item.box, boxAt(edge, edge))))) continue;
          let score = 0, blocked = false;
          for (const item of plan) {
            const { edge, point, box, points } = item;
            score += nearbyBoxes(box).length * 30000;
            for (let i = 1; i < points.length; i++) {
              const a = points[i - 1], b = points[i];
              const span = { left: Math.min(a.x, b.x) - 1, right: Math.max(a.x, b.x) + 1,
                top: Math.min(a.y, b.y) - 1, bottom: Math.max(a.y, b.y) + 1 };
              score += Math.hypot(b.x - a.x, b.y - a.y) + 12;
              for (const obstacle of nearbyBoxes(span)) if (obstacle.id !== edge.from
                && obstacle.id !== edge.to && crossesBox(a, b, obstacle)) {
                if (map.has(obstacle.id)) blocked = true;
                else score += 100;
              }
              if (plan.some(other => other !== item && crossesBox(a, b, other.box))) blocked = true;
            }
            score += Math.hypot(point.x - map.get(edge.from).x, point.y - sourceY) * .15;
          }
          if (!blocked && (!best || score < best.score)) best = { plan, score };
        }
      if (!best) continue;
      for (const { edge, point, points } of best.plan) {
        edge.x = point.x; edge.y = point.y; edge.points = points; edge.junctions = [];
        edge.returnGroup = `${id}:${row}`;
        occupyReturn(edge);
      }
    }
  }
  for (const edge of returns) {
    if (edge.returnGroup) continue;
    const source = map.get(edge.from), target = map.get(edge.to);
    const length = Math.hypot(target.x - source.x, target.y - source.y) || 1;
    const normal = { x: -(target.y - source.y) / length,
      y: (target.x - source.x) / length };
    const simultaneous = [...new Set([...(occupied.get(edge.from) || []), ...(occupied.get(edge.to) || [])])];
    let best;
    const consider = (point, shelf = false) => {
      const box = boxAt(point, edge), overlaps = nearbyBoxes(box);
      const returnClashes = simultaneous.filter(other => {
        const placed = boxAt(other, other);
        return box.left < placed.right && box.right > placed.left && box.top < placed.bottom && box.bottom > placed.top;
      }).length;
      const nodeClashes = overlaps.filter(other => map.has(other.id)).length;
      const labelClashes = nodeClashes * 1000000 + returnClashes * 300000
        + (overlaps.length - nodeClashes) * 30000;
      const start = port(source, point, portOffsets.get(`from:${edge.id}`));
      const end = port(target, point, portOffsets.get(`to:${edge.id}`));
      const path = [start, point, end];
      let crossings = 0, nodeCrossings = 0;
      for (let i = 1; i < path.length; i++) {
        const a = path[i - 1], b = path[i];
        const span = { left: Math.min(a.x, b.x) - 1, right: Math.max(a.x, b.x) + 1,
          top: Math.min(a.y, b.y) - 1, bottom: Math.max(a.y, b.y) + 1 };
        for (const box of nearbyBoxes(span)) if (box.id !== source.id && box.id !== target.id
          && crossesBox(a, b, box)) {
          if (map.has(box.id)) nodeCrossings++;
          else crossings += 45;
        }
      }
      const pathLength = Math.hypot(point.x - start.x, point.y - start.y)
        + Math.hypot(end.x - point.x, end.y - point.y);
      let returnCrossings = 0;
      for (const other of simultaneous) for (let i = 1; i < path.length; i++)
        for (let j = 1; j < other.points.length; j++) {
          const crossing = intersection(path[i - 1], path[i], other.points[j - 1], other.points[j]);
          if (crossing && [source, target, map.get(other.from), map.get(other.to)]
            .every(node => Math.hypot(crossing.x - node.x, crossing.y - node.y)
              > Math.max(node.width, node.height) / 2 + 18)) returnCrossings++;
        }
      const shelfBonus = shelf
        && pathLength <= Math.max(length * 2, length + 180) ? 4000 : 0;
      const score = labelClashes + crossings + nodeCrossings * 1000 + returnCrossings * 650
        + pathLength + (pathLength - length) * 2 - shelfBonus;
      if (!best || nodeClashes < best.nodeClashes || nodeClashes === best.nodeClashes
        && (returnClashes < best.returnClashes || returnClashes === best.returnClashes && score < best.score))
        best = { point, score, path, nodeClashes, returnClashes, hard: overlaps.length + returnClashes };
    };
    const candidates = (steps, gaps) => {
      for (const t of steps) for (const gap of gaps)
        consider({ x: source.x + (target.x - source.x) * t + normal.x * gap,
          y: source.y + (target.y - source.y) * t + normal.y * gap });
    };
    if (shelfPoints.has(edge.id)) consider(shelfPoints.get(edge.id), true);
    if (!best || best.hard || best.score >= -1000)
      candidates([.18, .32, .46, .6, .74, .88], [0, -65, 65, -130, 130, -195, 195, -260, 260]);
    if (best.hard) candidates([.08, .2, .32, .44, .56, .68, .8, .92],
      [-280, 280, -350, 350, -420, 420]);
    if (best.hard) {
      const origin = shelfPoints.get(edge.id) || {
        x: (source.x + target.x) / 2, y: (source.y + target.y) / 2
      };
      for (const y of [-70, 70, -140, 140, -210, 210, -280, 280, -350, 350])
        for (const x of [0, -edge.labelWidth / 2 - 12, edge.labelWidth / 2 + 12,
          -edge.labelWidth - 24, edge.labelWidth + 24])
          consider({ x: origin.x + x, y: origin.y + y });
    }
    if (best.nodeClashes || best.returnClashes) {
      // Larger merged cards need the actual gaps between nodes; fixed offset
      // samples can miss a clear position in a crowded row.
      for (const point of clearancePoints(source, target, edge, simultaneous)) consider(point);
    }
    const [start, label, end] = best.path;
    const bend = Math.abs((label.x - start.x) * (end.y - start.y)
      - (label.y - start.y) * (end.x - start.x));
    edge.x = label.x; edge.y = label.y;
    edge.points = bend < .01 ? [start, end] : best.path;
    edge.junctions = [];
    occupyReturn(edge);
  }
  const blockingNodes = (a, b, edge) => {
    const span = { left: Math.min(a.x, b.x) - 1, right: Math.max(a.x, b.x) + 1,
      top: Math.min(a.y, b.y) - 1, bottom: Math.max(a.y, b.y) + 1 };
    return nearbyBoxes(span).filter(box => map.has(box.id)
      && box.id !== edge.from && box.id !== edge.to && crossesBox(a, b, box));
  };
  const clearReturnLeg = (a, b, edge) => blockingNodes(a, b, edge).length === 0;
  const routeLeg = (a, b, edge) => {
    const blockers = blockingNodes(a, b, edge);
    if (!blockers.length) return [a, b];
    const search = boxes => {
      const corners = boxes.flatMap(box => [
        { x: box.left - 10, y: box.top - 10 }, { x: box.right + 10, y: box.top - 10 },
        { x: box.left - 10, y: box.bottom + 10 }, { x: box.right + 10, y: box.bottom + 10 }
      ]);
      const points = [a, ...corners, b], destination = points.length - 1;
      const distance = points.map(() => Infinity), previous = points.map(() => -1), seen = new Set();
      distance[0] = 0;
      for (let step = 0; step < points.length; step++) {
        let at = -1;
        for (let i = 0; i < points.length; i++)
          if (!seen.has(i) && (at < 0 || distance[i] < distance[at])) at = i;
        if (at === destination || !Number.isFinite(distance[at])) break;
        seen.add(at);
        for (let next = 1; next < points.length; next++) {
          if (next === at || seen.has(next) || !clearReturnLeg(points[at], points[next], edge)) continue;
          const cost = distance[at] + Math.hypot(points[next].x - points[at].x,
            points[next].y - points[at].y) + (next === destination ? 0 : 12);
          if (cost < distance[next]) { distance[next] = cost; previous[next] = at; }
        }
      }
      if (!Number.isFinite(distance[destination])) return null;
      const path = [];
      for (let at = destination; at >= 0; at = previous[at]) path.push(points[at]);
      return path.reverse();
    };
    let path = search(blockers);
    if (!path) {
      const area = { left: Math.min(a.x, b.x) - 100, right: Math.max(a.x, b.x) + 100,
        top: Math.min(a.y, b.y) - 100, bottom: Math.max(a.y, b.y) + 100 };
      const neighbors = nearbyBoxes(area).filter(box => map.has(box.id)
        && box.id !== edge.from && box.id !== edge.to);
      path = search(neighbors);
    }
    return path || [a, b];
  };
  for (const edge of returns) {
    const start = edge.points[0], label = { x: edge.x, y: edge.y }, end = edge.points.at(-1);
    const first = routeLeg(start, label, edge), second = routeLeg(label, end, edge);
    const points = [...first.slice(0, -1), ...second];
    edge.points = points.length === 3 && Math.abs((label.x - start.x) * (end.y - start.y)
      - (label.y - start.y) * (end.x - start.x)) < .01 ? [start, end] : points;
  }
  const routeLength = points => points.slice(1).reduce((sum, point, i) =>
    sum + Math.hypot(point.x - points[i].x, point.y - points[i].y), 0);
  // A good label position can still need a long obstacle detour. Reconsider
  // only those routes against the actual geometry, keeping compact shelves.
  for (const edge of returns) {
    if (edge.returnGroup) continue;
    const current = routeLength(edge.points);
    const direct = Math.hypot(edge.points.at(-1).x - edge.points[0].x,
      edge.points.at(-1).y - edge.points[0].y);
    if (current <= direct * 1.3 || current < direct + 60) continue;
    const source = map.get(edge.from), target = map.get(edge.to);
    const centerDistance = Math.hypot(target.x - source.x, target.y - source.y);
    const normal = { x: -(target.y - source.y) / centerDistance,
      y: (target.x - source.x) / centerDistance };
    const maxDistance = Math.max(direct * 3, direct + 300);
    const scoreDistance = (distance, turns, overlaps) => distance + turns * 12
      + (current > maxDistance ? 0 : overlaps * 1000);
    const simultaneous = [...new Set([...(occupied.get(edge.from) || []),
      ...(occupied.get(edge.to) || [])])].filter(other => other !== edge);
    const currentOverlap = nearbyBoxes(boxAt(edge, edge)).filter(box => !map.has(box.id)).length;
    let best = { distance: current, points: edge.points, x: edge.x, y: edge.y,
      cost: scoreDistance(current, edge.points.length - 2, currentOverlap) };
    const positions = [-.2, 0, .2, .35, .5, .65, .8, 1, 1.2].flatMap(t =>
      [0, -60, 60, -120, 120, -180, 180, -240, 240, -320, 320, -400, 400, -480, 480]
        .map(gap => ({ x: source.x + (target.x - source.x) * t + normal.x * gap,
          y: source.y + (target.y - source.y) * t + normal.y * gap })));
    if (current > maxDistance) positions.push(...clearancePoints(source, target, edge, simultaneous));
    for (const point of positions) {
      const box = boxAt(point, edge);
      if (nearbyBoxes(box).some(other => map.has(other.id))) continue;
      if (simultaneous.some(other => {
        const placed = boxAt(other, other);
        return box.left < placed.right && box.right > placed.left
          && box.top < placed.bottom && box.bottom > placed.top;
      })) continue;
      const start = port(source, point, portOffsets.get(`from:${edge.id}`));
      const end = port(target, point, portOffsets.get(`to:${edge.id}`));
      const points = [...routeLeg(start, point, edge).slice(0, -1),
        ...routeLeg(point, end, edge)];
      if (points.slice(1).some((next, i) => blockingNodes(points[i], next, edge).length)) continue;
      const distance = routeLength(points);
      const candidateDirect = Math.hypot(end.x - start.x, end.y - start.y);
      if (distance > Math.max(candidateDirect * 3, candidateDirect + 300)) continue;
      const overlaps = nearbyBoxes(box).filter(other => !map.has(other.id)).length;
      const cost = scoreDistance(distance, points.length - 2, overlaps);
      if (cost < best.cost - 45) best = { distance, points, x: point.x, y: point.y, cost };
    }
    edge.x = best.x; edge.y = best.y; edge.points = best.points;
  }
  let routeMinX = Infinity, routeMaxX = -Infinity;
  for (const e of view.edges) {
    const a = map.get(e.from), b = map.get(e.to); if (!a || !b) continue;
    const start = { x: a.x, y: a.y + a.height / 2 }, end = { x: b.x, y: b.y - b.height / 2 };
    if (e.terminal === 'return') {
      const siblings = out.get(a.id), slot = siblings.indexOf(e);
      e.returnRoute = true; e.routeLabel = e.label; e.label = '↩ 返回调用处'; labelLines(e, 130, 1); e.labelWidth = Math.min(150, Math.max(...e.lines.map(measure)) + 18); e.labelHeight = 24;
      const side = a.x < (maxX + 60) / 2 ? -1 : 1, gap = 20;
      e.x = a.x + side * (a.width / 2 + gap + e.labelWidth / 2); e.y = a.y + (slot - (siblings.length - 1) / 2) * 24;
      const edgeStart = { x: a.x + side * a.width / 2, y: a.y + (slot - (siblings.length - 1) / 2) * 10 };
      const bend = { x: a.x + side * (a.width / 2 + gap * .5), y: e.y }, edgeEnd = { x: e.x - side * e.labelWidth / 2, y: e.y };
      e.points = [edgeStart, bend, edgeEnd];
    } else if (isFeedback(e)) {
      e.feedback = true;
    } else {
        const corridor = corridors.get(a.id);
        const fork = forkRails.get(a.id), join = joinRails.get(b.id);
        const forkY = start.y + corridor.forkOffset;
        const joinY = visualRows[rowIndex.get(b.id)].top - 22 - join.level * 18;
        e.forkY = forkY;
        const raw = [start, { x: start.x, y: forkY }, { x: e.x, y: forkY }];
        if (rowIndex.get(b.id) > rowIndex.get(a.id) + 1) {
          const row = visualRows[rowIndex.get(a.id)];
          const turnY = row.bottom + rowLabelBottom.get(row) + 16 + longTurns.get(e.id).level * 18;
          const channel = e.channel;
          raw.push({ x: e.x, y: turnY }, { x: channel, y: turnY }, { x: channel, y: joinY });
        } else raw.push({ x: e.x, y: joinY });
        raw.push({ x: end.x, y: joinY }, end);
        const points = raw.filter((point, i) => !i || point.x !== raw[i - 1].x || point.y !== raw[i - 1].y);
        e.points = points.filter((point, i) => !i || i === points.length - 1
          || !(points[i - 1].x === point.x && point.x === points[i + 1].x
            || points[i - 1].y === point.y && point.y === points[i + 1].y));
        e.junctions = [];
        if (layoutOut.get(a.id).length > 1) for (const x of new Set([start.x, e.x]))
          if (x > fork.left && x < fork.right) e.junctions.push({ x, y: forkY });
        if (forwardIn.get(b.id).length > 1) for (const x of new Set([end.x, e.x]))
          if (x > join.left && x < join.right) e.junctions.push({ x, y: joinY });
    }
    if (!e.feedback) {
      for (const p of e.points || []) { routeMinX = Math.min(routeMinX, p.x); routeMaxX = Math.max(routeMaxX, p.x); }
      routeMinX = Math.min(routeMinX, e.x - e.labelWidth / 2); routeMaxX = Math.max(routeMaxX, e.x + e.labelWidth / 2);
    }
  }
  const ordinaryLabels = view.edges.filter(edge => !edge.feedback && !edge.returnRoute && edge.labelWidth)
    .sort((a, b) => a.y - b.y || a.x - b.x);
  const placedLabels = [], placedCells = new Map();
  const labelBox = (edge, point) => ({ left: point.x - edge.labelWidth / 2 - 4,
    right: point.x + edge.labelWidth / 2 + 4, top: point.y - edge.labelHeight / 2 - 4,
    bottom: point.y + edge.labelHeight / 2 + 4 });
  const overlaps = (a, b) => a.left < b.right && a.right > b.left
    && a.top < b.bottom && a.bottom > b.top;
  for (const edge of ordinaryLabels) {
    const anchor = { x: edge.x, y: edge.y };
    const nearby = placedLabels.filter(other => Math.abs(other.y - edge.y) < 150
      && Math.abs(other.x - edge.x) < 350);
    const blocked = point => {
      const box = labelBox(edge, point);
      return cellsFor(box).some(cell => (placedCells.get(cell) || []).some(other => overlaps(box, other)))
        || nearbyBoxes(box).some(other => map.has(other.id));
    };
    if (blocked(anchor)) {
      const vertical = edge.points.slice(1).flatMap((point, index) => {
        const previous = edge.points[index];
        return point.x === anchor.x && previous.x === anchor.x
          ? [{ top: Math.min(point.y, previous.y), bottom: Math.max(point.y, previous.y) }] : [];
      });
      const onLine = point => point.x === anchor.x && vertical.some(segment =>
        point.y - edge.labelHeight / 2 >= segment.top
          && point.y + edge.labelHeight / 2 <= segment.bottom);
      const positions = [];
      for (const other of nearby) {
        const y = (edge.labelHeight + other.labelHeight) / 2 + 10;
        const x = (edge.labelWidth + other.labelWidth) / 2 + 10;
        positions.push({ x: anchor.x, y: other.y - y }, { x: anchor.x, y: other.y + y },
          { x: other.x - x, y: anchor.y }, { x: other.x + x, y: anchor.y });
      }
      for (const segment of vertical) positions.push(
        { x: anchor.x, y: segment.top + edge.labelHeight / 2 + 3 },
        { x: anchor.x, y: segment.bottom - edge.labelHeight / 2 - 3 });
      for (let row = 1; row <= 5; row++) for (const side of [-1, 1]) {
        positions.push({ x: anchor.x, y: anchor.y + side * row * (edge.labelHeight + 12) });
        positions.push({ x: anchor.x + side * row * (edge.labelWidth + 16), y: anchor.y });
      }
      const clear = positions.filter(point => !blocked(point));
      clear.sort((a, b) => (onLine(a) ? 0 : 60) + Math.hypot(a.x - anchor.x, a.y - anchor.y)
        - (onLine(b) ? 0 : 60) - Math.hypot(b.x - anchor.x, b.y - anchor.y));
      if (clear.length) {
        edge.x = clear[0].x; edge.y = clear[0].y;
        if (!onLine(clear[0])) edge.labelAnchor = anchor;
      }
    }
    placedLabels.push(edge);
    const box = labelBox(edge, edge);
    for (const cell of cellsFor(box)) {
      if (!placedCells.has(cell)) placedCells.set(cell, []);
      placedCells.get(cell).push(box);
    }
    routeMinX = Math.min(routeMinX, edge.x - edge.labelWidth / 2);
    routeMaxX = Math.max(routeMaxX, edge.x + edge.labelWidth / 2);
  }
  const regular = view.edges.filter(edge => !edge.feedback && !edge.returnRoute);
  const bounds = edge => ({ left: Math.min(...edge.points.map(point => point.x)),
    right: Math.max(...edge.points.map(point => point.x)),
    top: Math.min(...edge.points.map(point => point.y)),
    bottom: Math.max(...edge.points.map(point => point.y)) });
  const routes = regular.map(edge => ({ edge, box: bounds(edge) }));
  const crossings = [], crossingCells = new Set();
  for (let i = 0; i < routes.length; i++) for (let j = i + 1; j < routes.length; j++) {
    const a = routes[i], b = routes[j];
    if (a.edge.from === b.edge.from || a.edge.from === b.edge.to
      || a.edge.to === b.edge.from || a.edge.to === b.edge.to
      || a.box.right < b.box.left || b.box.right < a.box.left
      || a.box.bottom < b.box.top || b.box.bottom < a.box.top) continue;
    for (let ai = 1; ai < a.edge.points.length; ai++) for (let bi = 1; bi < b.edge.points.length; bi++) {
      const start = a.edge.points[ai - 1], end = a.edge.points[ai];
      const otherStart = b.edge.points[bi - 1], otherEnd = b.edge.points[bi];
      const crossing = intersection(start, end, otherStart, otherEnd);
      if (!crossing || ordinaryLabels.some(label =>
        Math.abs(crossing.x - label.x) < label.labelWidth / 2 + 10
        && Math.abs(crossing.y - label.y) < label.labelHeight / 2 + 10)) continue;
      const key = `${Math.round(crossing.x / 8)}:${Math.round(crossing.y / 8)}`;
      if (crossingCells.has(key)) continue;
      crossingCells.add(key);
      const horizontal = Math.abs(end.x - start.x) > Math.abs(end.y - start.y)
        || Math.abs(otherEnd.x - otherStart.x) > Math.abs(otherEnd.y - otherStart.y);
      crossings.push({ ...crossing, horizontal });
    }
  }
  const minX = Math.min(0, routeMinX, ...view.nodes.map(n => n.x - n.width / 2)), dx = 60 - minX; for (const n of view.nodes) n.x += dx; for (const e of view.edges) { e.x += dx; if (e.channel !== undefined) e.channel += dx; if (e.labelAnchor) e.labelAnchor.x += dx; for (const p of [...(e.points || []), ...(e.junctions || [])]) p.x += dx; }
  for (const crossing of crossings) crossing.x += dx;
  return { ...view, crossings, width: Math.max(maxX, routeMaxX) + 140 + dx, height: y + 60 };
}
