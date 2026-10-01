import { singleLine } from './model.js';
import { readable, variableNames, describeEffects, predicate, predicateText } from './expressions.js';
import { normalPlayGraph } from './normal-play.js';
export { readable } from './expressions.js';

const CALL = new Set(['CALL_EDGE', 'SCREEN_CALL', 'SCREEN_CALL_EDGE']);
// Presentation only: the original strings remain in the graph and details.

export class PlayerGraph {
  constructor(graph) {
    this.graph = graph;
    this.variableNames = variableNames(graph);
    this.catalog = []; this.order = new Map(); this.previous = new Map();
    this.nearestCache = new Map(); this.contextCache = new Map();
    // Follow script connections in branch order. Numbering is a navigation
    // reference, not a claim that mutually exclusive scenes happen in sequence.
    const seen = new Set();
    const visit = start => {
      const stack = [start];
      for (let cursor = 0; cursor < stack.length; cursor++) {
        const id = stack[cursor]; if (seen.has(id)) continue; seen.add(id);
        const b = graph.projected.get(id); if (!b) continue;
        if (b.type === 'choice') {
          const item = { id, number: this.catalog.length + 1, title: this.title(id),
            options: (graph.out.get(id) || []).filter(e => e.kind === 'MENU_CHOICE').map(e => readable(e.choice)),
            path: b.path, line: b.line };
          this.catalog.push(item); this.order.set(id, item);
        }
        const out = graph.out.get(id) || [];
        for (const e of out) if (e.to && !seen.has(e.to)) stack.push(e.to);
      }
    };
    for (const e of graph.entries) visit(graph.anchor(e.id));
    for (const b of graph.projected.values()) if (b.type === 'choice') visit(b.id);
    const normal = normalPlayGraph(graph);
    this.flowGraph = normal.graph; this.replayOnly = normal.excludedBlocks;
    this.catalog = this.catalog.filter(item => !normal.excluded.has(item.id));
    for (const item of this.catalog) item.options = (this.flowGraph.out.get(item.id) || [])
      .filter(edge => edge.kind === 'MENU_CHOICE').map(edge => readable(edge.choice));
    for (const id of normal.excluded) this.order.delete(id);
    this.first = this.catalog[0]?.id || graph.entries[0]?.id || graph.projected.keys().next().value;
  }
  title(id) {
    if (this.navigationDetails?.has(id)) return this.navigationDetails.get(id).title;
    const b = this.graph.blocks.get(id); if (!b) return '后续剧情';
    if (b.type !== 'choice') return ['effect','condition'].includes(b.type) ? b.title : readable(b.title || b.contents.find(c => c.text)?.text || '剧情片段');
    if (b.title && !b.title.startsWith('选择 · ')) return readable(b.title);
    const context = this.contextTitle(id);
    if (context) return context;
    const options = (this.graph.out.get(id) || []).filter(e => e.kind === 'MENU_CHOICE').map(e => readable(e.choice));
    return options.length ? options.join(' / ') : '选择';
  }
  contextTitle(id, full = false) {
    if (this.contextCache.has(id)) return this.contextText(id, full);
    const seen = new Set([id]); let frontier = [id], result = '';
    while (frontier.length) {
      const next = [], texts = new Set();
      for (const at of frontier) {
        const block = this.graph.blocks.get(at);
        if (at !== id && block?.control === 'Menu') continue;
        const text = block?.contents?.findLast?.(item => item.text)?.text;
        if (at !== id && text) { texts.add(readable(text).replace(/\s+/g, ' ').trim()); continue; }
        for (const edge of this.graph.rawIn?.get(at) || []) {
          if (edge.kind === 'MENU_FALLTHROUGH' || CALL.has(edge.kind) || seen.has(edge.from)) continue;
          seen.add(edge.from); next.push(edge.from);
        }
      }
      if (texts.size) { if (texts.size === 1) result = [...texts][0]; break; }
      frontier = next;
    }
    this.contextCache.set(id, result);
    return this.contextText(id, full);
  }
  contextText(id, full) {
    const result = this.contextCache.get(id);
    return !full && result.length > 96 ? `${result.slice(0, 95)}…` : result;
  }
  directory({ offset = 0, query = '' } = {}) {
    const q = query.trim().toLocaleLowerCase();
    const items = q ? this.catalog.filter(n => `${n.title}\n${n.options.join('\n')}`.toLocaleLowerCase().includes(q)) : this.catalog;
    return { items: items.slice(offset, offset + 40), total: items.length, offset };
  }
  // The nearest decision in reverse control flow, used when searching dialogue
  // or an effect. Never traverse back out of a callee to invent a caller.
  locate(id) {
    if (this.order.has(id)) return id;
    const queue = [this.graph.anchor(id)], seen = new Set(queue);
    for (let i = 0; i < queue.length; i++) {
      const at = queue[i]; if (this.order.has(at)) return at;
      for (const e of this.graph.inc.get(at) || []) if (!CALL.has(e.kind) && !seen.has(e.from)) { seen.add(e.from); queue.push(e.from); }
    }
    return this.graph.anchor(id);
  }
  predecessors(id) {
    const found=[],queue=(this.graph.inc.get(id)||[]).filter(e=>!CALL.has(e.kind)).map(e=>e.from),seen=new Set([id]);
    for(let i=0;i<queue.length;i++){const at=queue[i];if(seen.has(at))continue;seen.add(at);
      if(this.order.has(at)){found.push({id:at,title:this.title(at),number:this.order.get(at).number});continue;}
      for(const e of this.graph.inc.get(at)||[])if(!CALL.has(e.kind))queue.push(e.from);
    }return found.sort((a,b)=>a.number-b.number);
  }
  brief(id, anchor) {
    const n = this.graph.brief(id), menu = this.order.get(id);
    n.title = this.title(id);
    if (menu) { n.number = menu.number; n.note = id === anchor ? `选择 ${menu.number} · 比较下方分支` : `选择 ${menu.number} · 点击继续`;
      n.preview = id !== anchor; n.target = n.preview ? id : undefined; }
    if (n.type === 'junction' || n.type === 'entry') { n.type = 'junction'; n.title = ''; n.note = ''; n.compact = true; }
    if (n.type === 'call') { n.title = this.callTitle(id); n.note = '点击查看这段剧情'; }
    if (n.type === 'return') n.title = '这段剧情结束';
    if (n.type === 'unknown') { n.title = '此处有未解析的剧情影响'; n.note = '点击查看原文'; }
    return n;
  }
  callTitle(id) {
    const edge = (this.graph.out.get(id) || []).find(e => CALL.has(e.kind));
    if (!edge?.to) return '进入另一段剧情';
    const targets = this.nextChoices(edge.to);
    return targets[0] ? this.title(targets[0]) : '进入另一段剧情';
  }
  nextChoices(start) {
    if (this.nearestCache?.has(start)) return this.nearestCache.get(start);
    const queue = [start], seen = new Set(), found = [];
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i]; if (!id || seen.has(id)) continue; seen.add(id);
      if (this.order.has(id)) { found.push(id); continue; }
      for (const e of this.graph.out.get(id) || []) if (e.to) queue.push(e.to);
    }
    this.nearestCache?.set(start, found); return found;
  }
  fragment({ anchor = this.first, context = [] } = {}) {
    const g = this.graph; anchor = g.anchor(anchor);
    const nodes = [], edges = [], included = new Set(), queue = [anchor];
    const add = id => { if (!included.has(id)) { included.add(id); nodes.push(this.brief(id, anchor)); } };
    add(anchor);
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      if (id !== anchor && this.order.has(id)) continue;
      const outgoing = (g.out.get(id) || []).filter(e => !CALL.has(e.kind));
      for (const e of outgoing) {
        if (edges.length >= 650 || nodes.length >= 148) {
          const overflowId = `player-overflow:${id}`;
          if (!nodes.some(n => n.id === overflowId)) {
            nodes.push({ id: overflowId, type: 'overflow', title: '更多后续分支', target: id });
            edges.push({ id: overflowId, from: id, to: overflowId, label: '点击展开', uncertain: true });
          }
          break;
        }
        let to = e.to;
        if (!to) {
          to = `player-unknown:${e.id}`;
          nodes.push({ id: to, type: 'unknown', title: '后续剧情尚未解析', detailId: e.id, detailKind: 'edge' });
        } else if (!included.has(to)) {
          add(to); queue.push(to);
        }
        const condition=e.condition==='True'?'':e.condition==='no-visible-choice'?'无可见选项':e.condition;
        let from = id, label = [readable(e.choice)||(e.kind==='CONTINUATION'?'返回后':''),condition,
          ...e.effects.map(f=>(f.guard?`若 ${f.guard}：`:'')+f.text)].filter(Boolean).join('\n');
        if (id === anchor && g.blocks.get(id).type === 'choice') {
          from = `option:${e.id}`;
          nodes.push({ id: from, type: 'option', title: label || '继续', detailId: e.id, detailKind: 'edge',
            uncertain: e.precision.topology !== 'exact', slot: e.slot, special: e.kind === 'MENU_FALLTHROUGH' });
          edges.push({ id: `option-entry:${e.id}`, from: id, to: from, label: '', slot: e.slot, detailId: e.id });
          label = '';
        }
        edges.push({ id: e.id, from, to, label,
          uncertain: e.precision.topology !== 'exact', continuation: e.kind === 'CONTINUATION',
          detailId: e.id, folded: e.via.length, slot: e.slot });
      }
      if (g.blocks.get(id).control === 'Return' && context.at(-1)?.continuation) {
        const target = context.at(-1).continuation, portal = `player-return:${id}`;
        nodes.push({ id: portal, type: 'boundary', title: this.title(target), target, popContext: true, note: '返回后继续' });
        edges.push({ id: portal, from: id, to: portal, label: '继续', uncertain: false });
      }
    }
    // Remove only single-in/single-out structural nodes with no effects or
    // condition. Every original connection remains available in edge details.
    const removed = new Set();
    for (const n of nodes) {
      if (!n.compact || n.id === anchor || g.blocks.get(n.id)?.effects.length) continue;
      const ins = edges.filter(e => e.to === n.id), outs = edges.filter(e => e.from === n.id);
      if (ins.length !== 1 || outs.length !== 1 || ins[0] === outs[0]) continue;
      const a = ins[0], b = outs[0]; if (a.from === b.to) continue;
      a.to = b.to; a.label = [a.label, b.label].filter(Boolean).join('\n');
      a.trace = [...a.trace || [a.id], ...b.trace || [b.id]];
      a.blocks = [...(a.blocks || g.links.get(a.id)?.via || []), n.id, ...(b.blocks || g.links.get(b.id)?.via || [])];
      a.uncertain ||= b.uncertain; a.continuation ||= b.continuation;
      edges.splice(edges.indexOf(b), 1); removed.add(n.id);
    }
    const menus = nodes.filter(n => n.type === 'choice');
    return { mode: 'player', anchor, context, nodes: nodes.filter(n => !removed.has(n.id)), edges,
      title: this.title(anchor), choiceCount: menus.length, total: this.catalog.length,
      number: this.order.get(anchor)?.number, player: true };
  }
  overview({ anchor = this.first } = {}) {
    if(!this.catalog.length) return this.fragment({anchor});
    anchor = this.order.has(anchor) ? anchor : this.locate(anchor);
    if (!this.order.has(anchor)) anchor = this.first;
    const selected = new Set(), queue = [anchor], nodes = [], edges = [];
    for (let i = 0; i < queue.length && selected.size < 18; i++) {
      const id = queue[i]; if (selected.has(id) || !this.order.has(id)) continue;
      selected.add(id);
      for (const e of this.graph.out.get(id) || []) for (const to of this.nextChoices(e.to)) if (!selected.has(to)) queue.push(to);
    }
    const add = (id, boundary = false) => {
      if (nodes.some(n => n.id === id)) return;
      const item = this.order.get(id);
      nodes.push({ id, type: 'story', title: this.title(id), number: item?.number, target: id,
        note: boundary ? '继续查看后续选择 ↗' : `${item?.options.length || 0} 个选项 · 点击看后果`, current: id === anchor });
    };
    for (const id of selected) add(id);
    for (const id of selected) {
      const targets = new Map();
      for (const e of this.graph.out.get(id) || []) for (const to of this.nextChoices(e.to)) {
        if (!targets.has(to)) targets.set(to, []); targets.get(to).push(e.id);
      }
      for (const [to, sources] of targets) {
        if (nodes.length >= 148 || edges.length >= 700) {
          const portal=`story-overflow:${id}`;
          nodes.push({id:portal,type:'story',title:'更多相连选择',target:id,number:this.order.get(id)?.number,note:'展开这处选择继续查看 ↗'});
          edges.push({id:portal,from:id,to:portal,label:'',uncertain:true,summary:true});break;
        }
        add(to, !selected.has(to));
        edges.push({ id: `story:${id}:${to}`, from: id, to, label: '', uncertain: true,
          detailId: sources[0], trace: sources, summary: true });
      }
    }
    return { mode: 'story-overview', anchor, nodes, edges, title: '选择地图', player: true,
      total: this.catalog.length, choiceCount: nodes.length };
  }
  // Flatten trace only on demand; do not duplicate all folded text in views.
  detail(kind, id, page, trace = [], blocks = [], sourceEdge = null) {
    const g = this.graph;
    if (kind === 'node' && this.navigationDetails?.has(id)) {
      const region = this.navigationDetails.get(id);
      const destination = route => ({ id: route.id, title: this.order.has(route.to)
        ? `选择 ${this.order.get(route.to).number} · ${this.title(route.to)}`
        : route.to === id ? '继续探索' : this.title(route.to), condition: route.routeCondition });
      const routes = region.routes.filter(route => !route.backup && route.from !== route.to && route.terminal !== 'return');
      return { kind, id, title: region.title, nodeType: 'exploration', path: region.path, line: region.line,
        destinations: routes.map(destination), activities: region.routes.filter(route => !route.backup && route.from === route.to).map(destination),
        effects: [], impact: [], connections: [], calls: [], candidates: [], controls: [], reads: [],
        sources: [...new Set(region.routes.flatMap(route => route.cases.flatMap(item => item.sources)))],
        folded: region.blocks, hidden: [], lines: [], lineCount: 0, pages: 1, page,
        precision: { topology: 'conservative', state: 'conservative', content: 'exact' } };
    }
    if (kind === 'route') {
      const route = this.routeDetails?.get(id);
      if (!route) return null;
      const cases = sourceEdge ? route.cases.filter(item => item.firstId === sourceEdge) : route.cases;
      const paths = cases.map(item => ({ labels: item.labels, effects: item.effects, effectRecords: item.effectRecords,
        condition: predicateText(item.guard, this.variableNames), impact: describeEffects(item.effectRecords, this.variableNames),
        dialogueFlags: [...new Set(item.checks.flatMap(check => check.flags))].map(flag => this.variableNames.get(flag) || flag),
        loops: item.loops.map(loop => ({ effects: loop.effects, blocks: loop.blocks, impact: describeEffects(loop.effects, this.variableNames),
          exits: [...new Set(loop.exitGuards.map(guard => predicateText(guard, this.variableNames)).filter(Boolean))] })),
        backupReason: item.backupReason }));
      const sources = [...new Set(cases.flatMap(item => item.sources))];
      const hidden = [...new Map(cases.flatMap(item => item.hidden).map(item =>
        [`${item.reason}|${item.path}|${item.line}`, item])).values()];
      const source = g.links.get(sources[0]) || g.rawEdges.get(sources[0]);
      const folded = [...new Set(cases.flatMap(item => item.blocks))];
      const originalConditions = sources.map(id => g.rawEdges.get(id)).filter(edge => edge?.condition && edge.condition !== 'True')
        .map(edge => ({ condition: edge.condition, id: edge.id, path: g.blocks.get(edge.from)?.path, line: g.blocks.get(edge.from)?.line }));
      const lines = folded.flatMap(key => { const b = g.blocks.get(key);
        const incoming = g.rawIn.get(key) || [], condition = incoming.length === 1 && incoming[0].condition !== 'True' ? incoming[0].condition : '';
        return b ? b.contents.map(c => ({ ...c, condition, path: b.path, line: b.line, blockId: b.id })) : []; });
      const destination = route.terminal === 'return' ? '返回调用处' : route.terminal === 'end' ? '剧情结束'
        : route.terminal === 'loop' ? '停留在重复流程'
        : route.terminal ? '后续未解析' : this.navigationDetails?.has(route.to) ? '进入自由探索'
          : this.wholeForks?.has(route.to) ? '后续条件判断'
          : `${route.from === route.to ? '重新选择' : '通往选择'} ${this.order.get(route.to)?.number || '?'}`;
      return { kind, id, title: paths.length > 1 ? `${destination} · ${paths.length} 条路径` : destination,
        sourceEdge, paths, backup: route.backup, target: route.terminal ? null : route.to, path: source ? g.blocks.get(source.from)?.path : '',
        line: source ? g.blocks.get(source.from)?.line : null, effects: cases.flatMap(item => item.effectRecords),
        precision: { topology: hidden.length ? 'partial' : 'exact', state: cases.some(item => item.effectRecords.some(effect => effect.unknown)) ? 'partial' : 'exact', content: 'exact' }, calls: [], candidates: [],
        sources, folded, originalConditions, lines: lines.slice(page * 60, (page + 1) * 60), lineCount: lines.length,
        page, pages: Math.max(1, Math.ceil(lines.length / 60)), condition: '',
        connections: [], controls: [], reads: [], hidden, routeCount: cases.length };
    }
    if (kind !== 'edge' || !trace.length) {
      const detail = g.detail(kind, id, page); if (!detail) return detail;
      if (kind === 'node') detail.connections = detail.connections.filter(edge => this.flowGraph.links.has(edge.id));
      if (kind !== 'edge') {
        detail.title = detail.nodeType === 'choice' && detail.title.startsWith('选择 · ')
          ? this.contextTitle(id, true) || this.title(id) : this.title(id);
        detail.predecessors = this.predecessors(id);
        if (this.wholeForks?.has(id)) {
          const routes = [...this.routeDetails.values()].filter(route => route.from === id && !route.backup && route.terminal !== 'return');
          detail.destinations = routes.map(route => ({ id: route.id, condition: route.routeCondition,
            title: this.order.has(route.to) ? `选择 ${this.order.get(route.to).number} · ${this.title(route.to)}`
              : this.navigationDetails?.has(route.to) ? '自由探索' : this.wholeForks.has(route.to) ? '后续条件判断' : route.terminal ? '此段流程结束' : this.title(route.to) }));
          detail.activities = []; detail.connections = [];
        }
      } else if (detail.edgeType === 'MENU_CHOICE') {
        detail.title = readable(g.links.get(id)?.choice || detail.title);
        const destinations = (this.wholeRoutesBySource?.get(id) || []).map(routeId => {
          const route = this.routeDetails.get(routeId);
          const conditions = route.cases.filter(item => item.firstId === id).map(item => predicateText(item.guard, this.variableNames)).filter(Boolean);
          return { id: routeId, backup: route.backup, conditions: [...new Set(conditions)], title: route.terminal === 'end' ? '剧情结束'
            : route.terminal === 'return' ? '返回调用处'
              : route.terminal === 'loop' ? '停留在重复流程'
              : route.terminal ? '后续未解析' : route.from === route.to ? `重新选择 ${this.order.get(route.to)?.number || '?'}` : this.title(route.to) };
        });
        detail.nextRoutes = destinations.filter(route => !route.backup);
        detail.backupRoutes = destinations.filter(route => route.backup);
      }
      detail.impact = describeEffects(detail.effects, this.variableNames);
      detail.conditionText = predicateText(predicate(detail.condition), this.variableNames);
      for (const connection of detail.connections) connection.appearance = predicateText(predicate(connection.condition), this.variableNames);
      return detail;
    }
    const d = g.detail('edge', trace[0], 0); if (!d) return null;
    const all = trace.map(key => g.links.get(key)).filter(Boolean);
    const ids = blocks.length ? blocks : all.flatMap(e => e.via);
    const lines = ids.flatMap(key => { const b = g.blocks.get(key); return b.contents.map(c => ({ ...c, path: b.path, line: b.line })); });
    const conditions = all.map(e => e.condition).filter(c => c && c !== 'True'), effects = all.flatMap(e => e.effects);
    return { ...d, title: all.map(e => g.edgeLabel(e)).filter(Boolean).join('\n') || '继续剧情',
      condition: conditions.join('\n'), conditionText: conditions.map(condition => predicateText(predicate(condition), this.variableNames)).join('\n'),
      effects, impact: describeEffects(effects, this.variableNames), folded: ids, sources: all.flatMap(e => e.sources),
      lines: lines.slice(page * 60, (page + 1) * 60), lineCount: lines.length, page, pages: Math.max(1, Math.ceil(lines.length / 60)) };
  }
}
