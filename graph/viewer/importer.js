import { JSONParser } from '@streamparser/json';
import { StoryGraph } from './model.js';

// Streaming paths deliberately exclude icfg, legacy, screens and audit data.
// keepStack:false releases each emitted record instead of retaining its parent.
export async function importGraph(file, progress = () => {}) {
  const graph = new StoryGraph();
  const parser = new JSONParser({ keepStack: false, paths: [
    '$.schema_version', '$.status', '$.entry_points', '$.graph_id', '$.engine',
    '$.narrative_graph.blocks.*', '$.narrative_graph.edges.*',
    '$.narrative_graph.return_sites.*', '$.candidate_edges.*'
  ] });
  parser.onValue = ({ value, key, stack }) => {
    const parentKey = stack.at(-1)?.key;
    if (parentKey === 'blocks') graph.addBlock(value);
    else if (parentKey === 'edges') graph.addEdge(value);
    else if (parentKey === 'return_sites') graph.sites.set(value.call_block, value.continuation_block);
    else if (parentKey === 'candidate_edges') graph.addEdge(value, true);
    else graph.meta[key] = value;
  };
  let bytes = 0, reported = 0;
  const reader = file.stream().getReader();
  try {
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      parser.write(value); bytes += value.byteLength;
      const now = performance.now();
      if (now - reported > 100) { progress({ stage: '读取剧情', bytes, total: file.size }); reported = now; }
    }
    if (!parser.isEnded) parser.end();
  } finally { reader.releaseLock(); }
  if (graph.meta.status && graph.meta.status !== 'completed') throw new Error('此文件的剧情提取尚未完成。');
  progress({ stage: '整理连接', bytes, total: file.size });
  graph.finish();
  return graph;
}
