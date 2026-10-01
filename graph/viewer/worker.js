import { importGraph } from './importer.js';
import { layout } from './layout.js';
import { PlayerGraph, readable } from './player.js';
import { wholeGraph, layoutWhole } from './whole.js';
import { releaseExpressionCaches } from './expressions.js';

let graph, player;
const cache = new Map();
const displayPosition = id => cache.get('whole')?.edgeAliases?.[id] || id;
function drawingView(view) {
  if (view.mode !== 'whole') return view;
  return { ...view, edges: view.edges.map(({ routeLines, mapLines, hidden, sources, effects, calls, ...edge }) => edge) };
}
self.onmessage = async ({ data: request }) => {
  const { id, type, payload = {} } = request;
  try {
    let result;
    if (type === 'import') {
      const start = performance.now();
      graph = await importGraph(payload.file, progress => self.postMessage({ id, type: 'progress', result: progress }));
      player = new PlayerGraph(graph);
      cache.clear();
      result = { stats: graph.stats, entries: graph.entries, sections: [...graph.sections.values()],
        firstChoice: player.first, directory: player.directory(), importMs: performance.now() - start };
    } else if (!graph) throw new Error('请先导入剧情图。');
    else if (type === 'view') {
      const key = payload.mode === 'player' ? 'whole' : JSON.stringify(payload);
      if (cache.has(key)) { result = cache.get(key); cache.delete(key); cache.set(key, result); }
      else {
        const start = performance.now();
        result = payload.mode === 'player' ? layoutWhole(wholeGraph(player)) : layout(
          payload.mode === 'story-overview' ? player.overview(payload)
          : payload.mode === 'overview' ? { ...graph.overview(), viewport: payload.viewport, currentPath: payload.currentPath }
          : payload.mode === 'candidates' ? graph.candidateView(payload) : graph.fragment(payload));
        result.layoutMs = performance.now() - start;
        cache.set(key, result); if (cache.size > 10) cache.delete(cache.keys().next().value);
      }
    } else if (type === 'directory') result = player.directory(payload);
    else if (type === 'search') {
      const number = /^(?:选择\s*#?\s*|#)?(\d+)$/u.exec(payload.query.trim());
      if (number) {
        const choice = player.catalog.find(choice => choice.number === Number(number[1]));
        result = { results: choice ? [{ id: choice.id, anchor: choice.id, number: choice.number,
          title: `选择 ${choice.number} · ${choice.title}`, excerpt: choice.options.join(' / '),
          path: choice.path, line: choice.line, exactChoice: true }] : [], total: choice ? 1 : 0, offset: 0 };
      } else {
        result = graph.search(payload.query, payload.offset, payload.player ? player.replayOnly : undefined);
        if (payload.player) for (const row of result.results) {
          row.anchor = displayPosition(player.wholeLocations?.get(row.id) || player.locate(row.id));
          const b = graph.blocks.get(row.id), choice = player.order.get(row.id);
          row.title = choice ? `选择 ${choice.number} · ${player.title(row.id)}`
            : readable(b.contents.find(c => c.text)?.text || b.title);
          const cleanExcerpt = readable(row.excerpt);
          if (cleanExcerpt.toLocaleLowerCase().includes(payload.query.trim().replace(/\s+/g, ' ').toLocaleLowerCase()))
            row.excerpt = cleanExcerpt;
        }
      }
    }
    else if (type === 'detail') {
      if (payload.player && payload.kind === 'return-group') {
        const edge = cache.get('whole')?.edges.find(edge => edge.id === payload.id);
        const target = cache.get('whole')?.nodes.find(node => node.id === edge?.to);
        if (edge) result = { kind: 'return-group', id: edge.id, title: `返回 ${target.type === 'exploration'
          ? '自由探索' : target.note?.match(/选择\s*\d+/)?.[0] || target.title}`,
          target: edge.to, answers: edge.returnMembers, path: '', line: null,
          effects: [], calls: [], candidates: [], controls: [], reads: [], connections: [], sources: [],
          folded: [], hidden: [], lines: [], lineCount: 0, pages: 1, page: 0, condition: '',
          precision: { topology: edge.uncertain ? 'partial' : 'exact', state: 'exact', content: 'exact' } };
      } else result = payload.player ? player.detail(payload.kind, payload.id, payload.page || 0, payload.trace, payload.blocks, payload.sourceEdge)
        : graph.detail(payload.kind, payload.id, payload.page);
      if(result)result.position=displayPosition(payload.kind==='return-group'||payload.kind==='route'||payload.kind==='edge'
        ?payload.id:player.wholeLocations?.get(payload.id)||graph.anchor(payload.id));
    }
    else throw new Error('未知请求。');
    self.postMessage({ id, type, result: type === 'view' ? drawingView(result) : result });
  } catch (error) { self.postMessage({ id, type: 'error', message: error.message || String(error) }); }
  finally { releaseExpressionCaches(); }
};
