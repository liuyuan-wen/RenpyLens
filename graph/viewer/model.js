import { inputScriptEffects, mergeInputScript } from './expressions.js';

// Pure graph adaptation. No Ren'Py expressions are evaluated by the viewer.
export const LIMITS = Object.freeze({ initial: 150, nodes: 300, edges: 800, page: 60 });
const CALL = new Set(['CALL_EDGE', 'SCREEN_CALL', 'SCREEN_CALL_EDGE']);
const OPS = { set: '=', add: '+=', sub: '-=', mult: '*=', mul: '*=', div: '/=',
  floordiv: '//=', mod: '%=', pow: '**=', menu_set_add: '添加' };
export const shortFile = (path) => String(path || '').replaceAll('\\', '/').split('/').pop() || '未知来源';
export const singleLine = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
export const clip = (s, n = 64) => s.length > n ? s.slice(0, n - 1) + '…' : s;

export function symbol(s) {
  if (typeof s === 'string') return s;
  if (!s) return '';
  if (s.kind === 'attribute') return `${symbol(s.base)}.${s.member || s.name}`;
  if (s.kind === 'subscript') return `${symbol(s.base)}[${s.index?.source ?? s.index ?? '?'}]`;
  return `${['persistent', 'local'].includes(s.kind) ? s.kind + '.' : ''}${s.name || s.member || '?'}`;
}
export function precision(p) {
  if (typeof p === 'string') return { topology: p, state: p, content: p };
  return { topology: 'exact', state: 'exact', content: 'exact', ...p };
}
const precisionPool = new Map();
function sharedPrecision(value) {
  const key = JSON.stringify(value);
  if (!precisionPool.has(key)) precisionPool.set(key, Object.freeze(value));
  return precisionPool.get(key);
}
export function effects(record) {
  // v3 effects supersede v2 writes; reading both would duplicate assignments.
  return (record.effects ?? record.writes ?? []).map(e => {
    const target = symbol(e.target ?? e.var);
    const knownValue = e.value_known ? typeof e.value === 'boolean' ? e.value ? 'True' : 'False'
      : e.value === null ? 'None' : JSON.stringify(e.value) : '';
    const expression = (e.expression?.source ?? e.expr ?? '') || knownValue;
    const unknown = e.kind === 'unknown' || (!target && e.precision === 'unknown');
    const rawOperator = e.operator ?? e.op ?? 'set';
    const operator = ({ '=': 'set', '+=': 'add', '-=': 'sub', '*=': 'mul', '/=': 'div' })[rawOperator] || rawOperator;
    const value = expression || knownValue || '?';
    const guard = e.guard?.source ?? e.guard ?? '';
    const text = unknown ? `未知影响 · ${e.reason || '动态代码'}`
      : target ? `${target} ${OPS[operator] || operator} ${value}`
      : `${e.kind || '操作'} ${expression}`;
    return { text, target, expression, operator, guard: String(guard), unknown,
      reason: e.reason || '', source: e.source || '', sourceNode: e.source_node,
      precision: e.precision || 'exact' };
  });
}

export class StoryGraph {
  constructor() {
    this.blocks = new Map(); this.rawEdges = new Map(); this.sites = new Map();
    this.meta = {}; this.candidates = []; this.owner = new Map(); this.projected = new Map();
    this.links = new Map(); this.out = new Map(); this.inc = new Map(); this.sections = new Map();
    this.rawOut = new Map(); this.rawIn = new Map(); this.destination = new Map();
    this.strings = new Map();
  }
  intern(value) {
    if (!this.strings.has(value)) this.strings.set(value, value);
    return this.strings.get(value);
  }
  addBlock(r) {
    if (!r.block_id) throw new Error('剧情块缺少 block_id。');
    if (this.blocks.has(r.block_id)) throw new Error(`重复的剧情块：${r.block_id}`);
    const loc = r.location || {};
    const p = precision(r.precision);
    if (r.effect_precision) p.state = r.effect_precision;
    const fx = effects(r);
    const consumed = new Set();
    for (const e of r.unknown_effects || []) {
      const data = e.effect || e, reason = data.reason || '未解析操作', source = data.source || '';
      const recovered = reason === 'python-effect' && source ? inputScriptEffects(source) : null;
      if (recovered && mergeInputScript(fx, recovered, consumed)) continue;
      if (!fx.some(x => x.unknown && x.reason === reason && x.source === source && x.sourceNode === e.source_node))
        fx.push({ text: `未知影响 · ${reason}`, unknown: true, reason, target: '', guard: '',
          source, sourceNode: e.source_node, unsupported: data.unsupported || [], calls: data.calls || [] });
    }
    for (const effect of fx) for (const key of ['target', 'expression', 'operator', 'guard', 'text', 'reason'])
      if (effect[key]) effect[key] = this.intern(effect[key]);
    const block = { id: this.intern(r.block_id), control: this.intern(r.control_kind || ''), originalKind: this.intern(r.kind),
      path: this.intern(String(loc.path ?? loc.filename ?? '').replaceAll('\\', '/')), line: loc.line ?? null,
      contents: (r.contents || []).map(c => ({ who: this.intern(c.raw_who ?? c.who ?? ''),
        text: c.raw_what ?? c.raw_text ?? c.what ?? c.text ?? '', kind: this.intern(c.kind || 'dialogue') })),
      effects: fx, precision: sharedPrecision(p), sourceNodesJSON: JSON.stringify(r.source_nodes || []),
      reads: (r.reads || []).map(value => this.intern(symbol(value))), controls: (r.control_reads || []).map(value => this.intern(symbol(value))) };
    this.blocks.set(block.id, block);
    for (const id of r.source_nodes || []) this.owner.set(id, block.id);
  }
  addEdge(r, candidate = false) {
    const p = sharedPrecision(precision(r.precision));
    const edge = { id: this.intern(r.edge_id), from: this.intern(r.from), to: r.to ? this.intern(r.to) : null, kind: this.intern(r.kind || 'NEXT'),
      choice: r.raw_choice ?? r.choice ?? '', condition: this.intern(r.condition ?? r.condition_ir?.source ?? ''),
      slot: r.kind === 'MENU_FALLTHROUGH' ? 1e6 : r.choice_index ?? r.branch_index ?? 0, effects: effects(r), precision: p,
      sourceEdge: this.intern(r.source_edge || r.edge_id), derivation: r.derivation || '',
      label: r.label || '', screen: r.screen || r.screen_id || '', candidate: candidate || !!r.candidate_edge };
    if (!edge.id) throw new Error('剧情连接缺少 edge_id。');
    if (candidate) this.candidates.push(edge);
    else this.rawEdges.set(edge.id, edge);
  }
  finish() {
    if (![2, 3].includes(this.meta.schema_version)) throw new Error('仅支持 StoryGraph schema v2 或 v3。');
    if (!this.blocks.size) throw new Error('文件没有 narrative_graph 剧情块。请选择完整 StoryGraph 或压缩剧情图。');
    const append = (map, key, value) => { if (!map.has(key)) map.set(key, []); map.get(key).push(value); };
    for (const b of this.blocks.values()) {
      this.rawOut.set(b.id, []); this.rawIn.set(b.id, []);
      if (!this.sections.has(b.path)) this.sections.set(b.path, { id: b.path, name: shortFile(b.path),
        blocks: 0, menus: 0, anchor: b.id, line: b.line ?? Infinity });
      const s = this.sections.get(b.path); s.blocks++; if (b.control === 'Menu') s.menus++;
      if ((b.line ?? Infinity) < s.line) { s.anchor = b.id; s.line = b.line; }
    }
    for (const e of this.rawEdges.values()) {
      if (!this.blocks.has(e.from) || (e.to && !this.blocks.has(e.to)))
        throw new Error(`连接引用了不存在的剧情块：${e.id}`);
      append(this.rawOut, e.from, e);
      if (e.to) append(this.rawIn, e.to, e);
    }
    for (const list of this.rawOut.values()) list.sort((a, b) => a.slot - b.slot);
    this.entries = Object.entries(this.meta.entry_points || {}).map(([name, id]) =>
      ({ name, id: this.owner.get(id) || (this.blocks.has(id) ? id : null) })).filter(e => e.id);
    const keep = new Set(this.entries.map(e => e.id));
    for (const s of this.sections.values()) keep.add(s.anchor);
    for (const [call, continuation] of this.sites) {
      if (this.blocks.has(call)) keep.add(call);
      if (this.blocks.has(continuation)) keep.add(continuation);
    }
    for (const b of this.blocks.values()) {
      const incoming = this.rawIn.get(b.id), outgoing = this.rawOut.get(b.id);
      const unknown = b.effects.some(e => e.unknown && e.reason !== 'presentation-statement');
      if (['Menu', 'If', 'While', 'Call', 'Return', 'UserStatement'].includes(b.control)
        || b.effects.some(e => !e.unknown) || unknown || b.precision.topology === 'unknown'
        || incoming.length !== 1 || outgoing.length !== 1
        || outgoing.some(e => !e.to || e.candidate || e.effects.length || !['NEXT', 'JUMP_EDGE'].includes(e.kind)
          || (e.condition && e.condition !== 'True') || e.precision.topology !== 'exact')
        || incoming.some(e => this.blocks.get(e.from).path !== b.path)
        || outgoing.some(e => e.to && this.blocks.get(e.to).path !== b.path)) keep.add(b.id);
    }
    // A pure cycle has no natural boundary. Keep one anchor, without unrolling it.
    const visited = new Set();
    for (const b of this.blocks.values()) {
      let cur = b.id; const chain = new Set();
      while (cur && !keep.has(cur) && !visited.has(cur)) {
        if (chain.has(cur)) { keep.add(cur); break; }
        chain.add(cur); cur = this.rawOut.get(cur)[0]?.to;
      }
      for (const id of chain) visited.add(id);
    }
    for (const id of keep) {
      const b = this.blocks.get(id);
      if (!b) continue;
      b.type = this.classify(b); b.title = this.title(b);
      this.projected.set(id, b); this.out.set(id, []); this.inc.set(id, []);
      this.destination.set(id, id);
    }
    for (const id of keep) {
      for (const first of this.rawOut.get(id) || []) {
        let cur = first.to; const via = [], sources = [first.id];
        while (cur && !keep.has(cur)) {
          via.push(cur); const next = this.rawOut.get(cur)[0];
          sources.push(next.id); cur = next.to;
        }
        for (const v of via) this.destination.set(v, cur || id);
        this.addLink({ ...first, to: cur, via, sources });
      }
    }
    // These are continuation relations, not claims that the callee must return.
    for (const [call, continuation] of this.sites) if (keep.has(call) && keep.has(continuation)) {
      this.addLink({ id: `continuation:${call}`, from: call, to: continuation,
        kind: 'CONTINUATION', condition: '', choice: '', effects: [], via: [], sources: [],
        precision: precision('conservative'), label: '返回后', slot: 1e6 });
    }
    // Candidate topology is supplemental in full v3 files. It is not folded into
    // the authoritative graph; the UI offers it explicitly in node details.
    this.candidateByBlock = new Map();
    for (const e of this.candidates) {
      e.from = this.owner.get(e.from) || e.from; e.to = this.owner.get(e.to) || e.to;
      if (this.blocks.has(e.from)) append(this.candidateByBlock, e.from, e);
    }
    // Source ownership has been resolved; original IDs remain on each block.
    this.owner.clear(); this.strings.clear();
    this.searchRows = [];
    for (const b of this.blocks.values()) {
      const outgoing = this.rawOut.get(b.id) || [];
      const text = this.searchText(b);
      this.searchRows.push({ id: b.id, lower: text.toLocaleLowerCase(),
        choices: outgoing.map(e => e.choice).join('\n').toLocaleLowerCase(),
        variables: [...b.effects.map(e => e.target), ...b.controls].join('\n').toLocaleLowerCase(),
        title: (b.title || '').toLocaleLowerCase() });
    }
    this.stats = { blocks: this.blocks.size, nodes: this.projected.size, edges: this.links.size,
      menus: [...this.blocks.values()].filter(b => b.control === 'Menu').length,
      sections: this.sections.size, schema: this.meta.schema_version, candidates: this.candidates.length };
    return this;
  }
  classify(b) {
    if (b.control === 'Menu') return 'choice';
    if (['If', 'While'].includes(b.control)) return 'condition';
    if (b.control === 'Call' || (this.rawOut.get(b.id) || []).some(e => CALL.has(e.kind))) return 'call';
    if (b.control === 'Return') return 'return';
    if (b.effects.some(e => !e.unknown)) return 'effect';
    if (b.effects.some(e => e.unknown && e.reason !== 'presentation-statement')
      || b.precision.topology === 'unknown') return 'unknown';
    if (!this.rawOut.get(b.id)?.length) return 'end';
    if (this.rawIn.get(b.id)?.length > 1) return 'junction';
    return 'entry';
  }
  title(b) {
    if (b.type === 'choice') {
      const caption = b.contents.find(c => c.kind === 'menu_caption');
      if (caption?.text) return singleLine(caption.text);
      let cur = b.id; const seen = new Set([cur]);
      for (;;) {
        const edges = this.rawIn.get(cur);
        if (edges.length !== 1) break;
        const e = edges[0], prev = this.blocks.get(e.from);
        if (seen.has(prev.id) || this.rawOut.get(prev.id).length !== 1
          || !['NEXT', 'JUMP_EDGE'].includes(e.kind) || e.condition && e.condition !== 'True'
          || ['Menu', 'If', 'While', 'Call', 'Return'].includes(prev.control)) break;
        const line = prev.contents.findLast(c => c.kind === 'dialogue' && c.text);
        if (line) return singleLine(line.text);
        seen.add(prev.id); cur = prev.id;
      }
      return `选择 · ${shortFile(b.path)}:${b.line ?? '?'}`;
    }
    if (b.type === 'condition') {
      const tests = [...new Set((this.rawOut.get(b.id) || []).map(e => e.condition).filter(Boolean))];
      return b.control === 'While' ? `循环判断\n${tests[0] || b.controls.join(', ')}`
        : `条件判断\n${tests[0] || b.controls.join(', ')}`;
    }
    if (b.type === 'effect') return b.effects.filter(e => !e.unknown).map(e => e.text).slice(0, 3).join('\n');
    if (b.type === 'call') return `调用子流程\n${(this.rawOut.get(b.id) || []).find(e => CALL.has(e.kind))?.label || shortFile(b.path)}`;
    if (b.type === 'unknown') return b.control === 'UserStatement' ? '交互 / 自定义流程' : '未解析流程';
    return ({ entry: '片段入口', junction: '分支汇合', return: '返回', end: '流程末端' })[b.type] || b.control;
  }
  addLink(e) {
    this.links.set(e.id, e); this.out.get(e.from)?.push(e);
    if (e.to) this.inc.get(e.to)?.push(e);
  }
  anchor(id) { return this.destination.get(id) || (this.projected.has(id) ? id : this.entries[0]?.id || this.projected.keys().next().value); }
  brief(id) {
    const b = this.projected.get(id);
    return { id, type: b.type, title: b.title, path: b.path, line: b.line,
      effectCount: b.effects.filter(e => !e.unknown).length, dialogueCount: b.contents.length,
      uncertain: b.precision.topology !== 'exact' || b.effects.some(e => e.unknown && e.reason !== 'presentation-statement'),
      note: b.type === 'effect' && b.effects.filter(e => !e.unknown).length > 3
        ? `另有 ${b.effects.filter(e => !e.unknown).length - 3} 项变化` : '',
      detailId: id };
  }
  edgeLabel(e) {
    const condition = e.condition === 'True' ? '' : e.condition === 'no-visible-choice' ? '无可见选项' : e.condition;
    return [e.choice || (e.kind === 'CONTINUATION' ? '返回后' : ''), condition,
      ...e.effects.map(f => (f.guard ? `若 ${f.guard}：` : '') + f.text)].filter(Boolean).join('\n');
  }
  fragment({ anchor, limit = LIMITS.initial, context = [] } = {}) {
    // Reserve one boundary/overflow node for every selected source. The node
    // limit includes these controls, not only the underlying story nodes.
    anchor = this.anchor(anchor); limit = Math.max(1, Math.min(limit, LIMITS.nodes / 2));
    const selected = new Set(), queue = [anchor]; let head = 0, budget = 0;
    // Conservative budget leaves room for every outgoing boundary. No edge is
    // silently removed when a high-degree screen reaches the segment limit.
    while (head < queue.length && selected.size < limit) {
      const id = queue[head++]; if (selected.has(id)) continue;
      const links = (this.out.get(id) || []).filter(e => !CALL.has(e.kind));
      if (selected.size && budget + links.length > LIMITS.edges - 50) continue;
      selected.add(id); budget += links.length;
      for (const e of links) if (e.to && !selected.has(e.to)) queue.push(e.to);
    }
    const nodes = [...selected].map(id => this.brief(id)), edges = [], groups = [];
    const boundary = (id, title, target, type = 'boundary', extra = {}) => {
      if (!nodes.some(n => n.id === id)) nodes.push({ id, title, target, type, ...extra });
      return id;
    };
    let remainingSources = selected.size;
    for (const id of selected) {
      const links = this.out.get(id) || [];
      const outgoing = links.filter(e => !CALL.has(e.kind));
      let overflow = [];
      for (const e of outgoing) {
        if (edges.length >= LIMITS.edges - remainingSources || (!selected.has(e.to) && nodes.length >= LIMITS.nodes - remainingSources)) {
          overflow.push(e.id); continue;
        }
        const target = selected.has(e.to) ? e.to : boundary(`boundary:${e.id}`,
          e.to ? `继续浏览\n${shortFile(this.blocks.get(e.to)?.path)}` : '目标未解析', e.to,
          e.to ? 'boundary' : 'unknown', { detailId: e.id, detailKind: 'edge' });
        edges.push({ id: e.id, from: id, to: target, label: this.edgeLabel(e),
          uncertain: e.candidate || e.precision.topology !== 'exact', continuation: e.kind === 'CONTINUATION',
          detailId: e.id, folded: e.via.length, slot: e.slot });
      }
      if (overflow.length) {
        const target = boundary(`overflow:${id}`, `另有 ${overflow.length} 条连接\n点击查看`, id, 'overflow');
        edges.push({ id: `overflow-edge:${id}`, from: id, to: target, label: '', uncertain: true });
        groups.push({ id, count: overflow.length });
      }
      if (this.blocks.get(id).control === 'Return' && context.length && context.at(-1).continuation) {
        const frame = context.at(-1);
        const target = boundary(`return:${id}`, '返回调用处', frame.continuation, 'boundary', { popContext: true });
        edges.push({ id: `return-edge:${id}`, from: id, to: target, label: '匹配当前调用', uncertain: false });
      }
      remainingSources--;
    }
    return { mode: 'fragment', anchor, context, nodes, edges, groups,
      title: shortFile(this.blocks.get(anchor)?.path), total: this.projected.size };
  }
  overview() {
    const nodes = [...this.sections.values()].sort((a,b) => a.id.localeCompare(b.id)).map(s => ({
      id: `section:${s.id}`, type: 'section', title: s.name, path: s.id, target: s.anchor,
      note: `${s.menus} 个选项 · ${s.blocks} 个剧情块` }));
    const merged = new Map();
    for (const e of this.links.values()) if (e.to) {
      const a = this.blocks.get(e.from).path, b = this.blocks.get(e.to).path;
      if (a === b) continue;
      const key = JSON.stringify([a,b]);
      if (!merged.has(key)) merged.set(key, { id: `overview:${merged.size}`, from: `section:${a}`,
        to: `section:${b}`, count: 0, label: '', uncertain: false });
      merged.get(key).count++;
    }
    for (const e of merged.values()) e.label = `${e.count} 条连接`;
    return { mode: 'overview', title: '全局概览', nodes, edges: [...merged.values()] };
  }
  candidateView({ anchor, offset = 0 }) {
    anchor = this.anchor(anchor);
    const all = this.candidateByBlock.get(anchor) || [], nodes = [this.brief(anchor)], edges = [];
    for (const e of all.slice(offset, offset + 30)) {
      const id = `candidate:${e.id}`, target = this.blocks.has(e.to) ? this.anchor(e.to) : null;
      nodes.push({ id, type: 'candidate', title: e.label || e.choice || e.kind, target,
        note: target ? '候选目标 · 点击查看' : '目标未解析', uncertain: true });
      edges.push({ id: `candidate-link:${e.id}`, from: anchor, to: id,
        label: [e.condition, e.derivation].filter(Boolean).join('\n'), uncertain: true,
        detailId: anchor, detailKind: 'node' });
    }
    for (const next of [offset - 30, offset + 30]) if (next >= 0 && next < all.length) {
      const id = `candidate-page:${next}`;
      nodes.push({ id, type: 'candidate-page', title: next < offset ? '上一页候选连接' : '下一页候选连接', target: anchor, offset: next });
      edges.push({ id: `candidate-page-link:${next}`, from: anchor, to: id, label: '', uncertain: true });
    }
    return { mode: 'candidates', anchor, nodes, edges, title: `候选连接 · ${shortFile(this.blocks.get(anchor).path)}`, total: all.length };
  }
  search(query, offset = 0, excluded = new Set()) {
    const needle = singleLine(query).toLocaleLowerCase(); if (!needle) return { results: [], total: 0 };
    const matched = [];
    for (const row of this.searchRows) {
      if (excluded.has(row.id)) continue;
      const index = row.lower.indexOf(needle); if (index < 0) continue;
      const rank = row.choices.includes(needle) ? 0 : row.variables.includes(needle) ? 1
        : row.title.includes(needle) ? 2 : 3;
      matched.push({ row, index, rank });
    }
    matched.sort((a,b) => a.rank - b.rank);
    const results = [];
    for (const { row, index } of matched.slice(offset, offset + 50)) {
      const b = this.blocks.get(row.id), text = this.searchText(b);
      const lineStart = text.lastIndexOf('\n', index - 1) + 1;
      const lineEnd = text.indexOf('\n', index);
      const line = text.slice(lineStart, lineEnd < 0 ? undefined : lineEnd);
      const localIndex = index - lineStart;
      const start = Math.max(0, localIndex - 45);
      const end = Math.min(line.length, localIndex + needle.length + 95);
      results.push({ id: row.id, anchor: this.anchor(row.id), path: b.path, line: b.line,
        title: b.title || `对白 · ${shortFile(b.path)}:${b.line ?? '?'}`,
        excerpt: `${start ? '…' : ''}${singleLine(line.slice(start, end))}${end < line.length ? '…' : ''}` });
    }
    return { results, total: matched.length, offset };
  }
  searchText(block) {
    const outgoing = this.rawOut.get(block.id) || [];
    return [block.title || '', block.path, block.line, ...block.contents.map(c => [c.who,c.text].filter(Boolean).join(' ')),
      ...block.effects.map(e => e.text), ...block.controls,
      ...outgoing.flatMap(e => [e.choice, e.condition, ...e.effects.map(f => f.text)])].join('\n');
  }
  detail(kind, id, page = 0) {
    const edge = kind === 'edge' ? this.links.get(id) : null;
    const block = kind !== 'edge' ? this.blocks.get(id) : null;
    if (!edge && !block) return null;
    const blocks = edge ? edge.via.map(id => this.blocks.get(id)) : [block];
    const lines = []; let count = 0;
    for (const b of blocks) for (const c of b.contents) {
      if (count >= page * LIMITS.page && lines.length < LIMITS.page)
        lines.push({ ...c, path: b.path, line: b.line, blockId: b.id });
      count++;
    }
    const calls = block ? (this.out.get(id) || []).filter(e => CALL.has(e.kind)).map(e => ({
      target: e.to, label: e.label || '进入子流程', call: id, continuation: this.sites.get(id) || null })) : [];
    const candidates = block ? (this.candidateByBlock.get(id) || []).map(e => ({
      id: e.id, target: this.blocks.has(e.to) ? this.anchor(e.to) : null,
      title: e.choice || e.label || e.kind, condition: e.condition, derivation: e.derivation })) : [];
    return { kind, id, nodeType: block?.type, edgeType: edge?.kind,
      title: block ? (block.title || singleLine(block.contents[0]?.text) || '剧情片段') : this.edgeLabel(edge) || '剧情连接',
      path: block?.path ?? this.blocks.get(edge?.from)?.path,
      line: block?.line ?? this.blocks.get(edge?.from)?.line, effects: block?.effects || edge.effects,
      precision: block?.precision || edge.precision, calls, candidates,
      sources: edge ? [...new Set([...edge.sources, ...edge.sources.map(id => this.rawEdges.get(id)?.sourceEdge).filter(Boolean)])] : JSON.parse(block.sourceNodesJSON),
      folded: edge?.via || [], lines, lineCount: count, page, pages: Math.ceil(count / LIMITS.page),
      condition: edge?.condition === 'no-visible-choice' ? '无可见选项' : edge?.condition,
      connections: block ? (this.out.get(id) || []).map(e => ({ id: e.id, target: e.to,
        kind: e.kind, choice: e.choice, condition: e.condition,
        title: this.edgeLabel(e) || e.label || e.kind, call: CALL.has(e.kind) })) : [],
      controls: block?.controls || [], reads: block?.reads || [] };
  }
}
