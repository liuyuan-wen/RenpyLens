import dagre from '@dagrejs/dagre';

const measureContext = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(1, 1).getContext('2d') : null;
if (measureContext) measureContext.font = '14px "Segoe UI", "Microsoft YaHei", sans-serif';
const measure = text => measureContext ? measureContext.measureText(text).width
  : [...text].reduce((n, c) => n + (c.charCodeAt(0) > 255 ? 14 : 7.5), 0);

export function wrap(text, width, maxLines = 3, measureText = measure) {
  const lines = []; let line = '';
  const tokens = String(text).match(/\n|[^\S\n]+|[\u2e80-\uffff]|[^\s\u2e80-\uffff]+/gu) || [];
  for (const token of tokens) {
    const pieces = token !== '\n' && measureText(token) > width ? [...token] : [token];
    for (const piece of pieces) {
      if(piece==='\n'){lines.push(line.trimEnd());line='';}
      else if(line && measureText(line+piece)>width){lines.push(line.trimEnd());line=piece.trimStart();}
      else line+=line?piece:piece.trimStart();
    }
    if(lines.length>maxLines)break;
  }
  if (line || !lines.length) lines.push(line.trimEnd());
  if(lines.length>maxLines){lines.length=maxLines;lines[maxLines-1]=lines[maxLines-1].replace(/[.…]+$/,'')+'…';}
  return lines;
}

export function wrapLabel(text, width, maxLines = 3, measureText = measure) {
  const lines = wrap(text, width, maxLines, measureText), roles = [];
  for (const segment of String(text).split('\n')) {
    const role = segment.startsWith('条件：') ? 'condition'
      : segment.startsWith('变化：') ? 'effect' : 'primary';
    for (const ignored of wrap(segment, width, maxLines + 1, measureText)) {
      if (roles.length === lines.length) break;
      const line = lines[roles.length].trim();
      const annotation = role === 'primary' && roles.length > 0
        && /^\([^()\n]{1,80}\)$/.test(line) && segment.trimEnd().endsWith(line);
      roles.push(annotation ? 'annotation' : role);
    }
    if (roles.length === lines.length) break;
  }
  return { lines, roles };
}

export function layout(view) {
  if (view.mode === 'overview' || view.mode === 'story-overview') return layoutOverview(view);
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setGraph({ rankdir: 'TB', ranksep: 86, nodesep: 44, edgesep: 22,
    marginx: 50, marginy: 50, ranker: 'longest-path' });
  g.setDefaultEdgeLabel(() => ({}));
  // Route feedback connections outside the forward flow so loops keep their
  // direction without occupying the lanes used to read the next decision.
  const outgoing=new Map(view.nodes.map(n=>[n.id,[]]));
  for(const e of view.edges)outgoing.get(e.from)?.push(e);
  const color=new Map(),feedback=new Set();
  const walk=id=>{color.set(id,1);for(const e of outgoing.get(id)||[]){
    if(color.get(e.to)===1)feedback.add(e.id);else if(!color.has(e.to))walk(e.to);
  }color.set(id,2);};
  for(const n of view.nodes)if(!color.has(n.id))walk(n.id);
  for (const node of view.nodes) {
    const diamond = ['choice', 'condition'].includes(node.type);
    node.lines = wrap(node.title, diamond ? view.player ? 230 : 178 : view.player ? 270 : 232, view.player ? 5 : 3);
    node.width = diamond ? 320 : node.type === 'junction' ? 170 : 272;
    node.height = diamond ? 190 : node.type === 'junction' ? 70 : 80 + node.lines.length * 16;
    if(view.player){node.width=diamond?node.preview?240:380:260;node.height=diamond?Math.max(160,90+node.lines.length*25):76+node.lines.length*20;
      node.lines=wrap(node.title,node.type==='option'?150:node.preview?132:diamond?210:220,node.type==='option'?12:5);
      node.height=diamond?Math.max(160,90+node.lines.length*25):60+node.lines.length*20;
      if(node.type==='option'){node.width=170;node.height=36+node.lines.length*20;}}
    if(node.compact){node.width=14;node.height=14;node.lines=[];}
    g.setNode(node.id, { width: node.width, height: node.height });
  }
  for (const e of view.edges) {
    e.lines = wrap(e.label || '', view.player ? 250 : 206, view.player ? 12 : 3);
    const width = e.label ? Math.max(...e.lines.map(measure)) + 24 : 0;
    const height = e.label ? e.lines.length * 19 + 12 : 0;
    e.labelWidth=width;e.labelHeight=height;
    if(!feedback.has(e.id))g.setEdge(e.from, e.to, { width, height, labelpos: 'c', weight: 1 }, e.id);
  }
  const branches = new Map(view.edges.map(e => [e.id, e]));
  const controls = new Set(view.nodes.filter(n => ['choice', 'condition'].includes(n.type)).map(n => n.id));
  const priority=new Map();
  if(view.player){const outgoing=new Map();for(const e of view.edges){if(!outgoing.has(e.from))outgoing.set(e.from,[]);outgoing.get(e.from).push(e.to);}
    for(const n of view.nodes.filter(n=>n.type==='option').sort((a,b)=>a.slot-b.slot)){
      const queue=[n.id];for(let i=0;i<queue.length;i++){const id=queue[i];if(id===view.anchor||priority.has(id))continue;priority.set(id,n.slot);queue.push(...outgoing.get(id)||[]);}
    }
  }
  const layoutOptions = { customOrder(graph, order) {
    // Order the normalized edge lanes, not shared destination nodes. This also
    // handles options that merge or whose destinations occupy different ranks.
    const lanes = new Map();
    for (const id of graph.nodes()) {
      const n = graph.node(id), edge = branches.get(n.edgeObj?.name);
      if (!edge || !controls.has(edge.from) || n.edgeObj.v !== edge.from) continue;
      const key = `${edge.from}\0${n.rank}`;
      if (!lanes.has(key)) lanes.set(key, []);
      lanes.get(key).push({ id, slot: edge.slot ?? 0 });
    }
    const constraints = [];
    for(const n of view.nodes.filter(n=>n.type==='choice')){
      const options=view.edges.filter(e=>e.from===n.id&&view.nodes.find(t=>t.id===e.to)?.type==='option').sort((a,b)=>a.slot-b.slot);
      for(let i=1;i<options.length;i++)constraints.push({left:options[i-1].to,right:options[i].to});
    }
    for (const lane of lanes.values()) {
      lane.sort((a, b) => a.slot - b.slot);
      for (let i = 1; i < lane.length; i++) constraints.push({ left: lane[i - 1].id, right: lane[i].id });
    }
    if(priority.size){const ranks=new Map();for(const id of graph.nodes()){
      const n=graph.node(id),p=priority.get(id)??priority.get(n.edgeObj?.v);
      if(p===undefined)continue;if(!ranks.has(n.rank))ranks.set(n.rank,[]);ranks.get(n.rank).push({id,p});}
      for(const row of ranks.values()){row.sort((a,b)=>a.p-b.p);for(let i=1;i<row.length;i++)if(row[i-1].p!==row[i].p)constraints.push({left:row[i-1].id,right:row[i].id});}
    }
    // Dagre 3.1.1's optimal-order sweep can leave normalized dummy nodes without
    // coordinates in dense converging graphs. Start from its complete initial
    // ordering, then apply our branch constraints with a stable topological sort.
    order(graph, { disableOptimalOrderHeuristic: true });
    const rows=new Map();for(const id of graph.nodes()){const n=graph.node(id);
      if(!rows.has(n.rank))rows.set(n.rank,[]);rows.get(n.rank).push(id);}
    for(const row of rows.values()){
      row.sort((a,b)=>graph.node(a).order-graph.node(b).order);
      const index=new Map(row.map((id,i)=>[id,i])),degree=new Map(row.map(id=>[id,0])),next=new Map(row.map(id=>[id,new Set()]));
      for(const {left,right} of constraints)if(index.has(left)&&index.has(right)&&left!==right&&!next.get(left).has(right)){
        next.get(left).add(right);degree.set(right,degree.get(right)+1);}
      const ready=row.filter(id=>!degree.get(id)),ordered=[];
      while(ready.length){const id=ready.shift();ordered.push(id);for(const to of next.get(id)){
        degree.set(to,degree.get(to)-1);if(!degree.get(to)){ready.push(to);ready.sort((a,b)=>index.get(a)-index.get(b));}}}
      if(ordered.length!==row.length)throw new Error('剧情分支排序发生冲突。');
      ordered.forEach((id,i)=>graph.node(id).order=i);
    }
  } };
  dagre.layout(g, layoutOptions);
  // Longest-path ranks pull options above their siblings when one consequence
  // has extra steps. Extend only their outgoing spans so all answers share a
  // row, then apply the same source-order constraints to the aligned lanes.
  const options=view.nodes.filter(n=>n.type==='option');
  if(options.length){const rank=Math.min(...options.map(n=>g.node(n.id).rank));let changed=false;
    for(const n of options){const delta=(g.node(n.id).rank-rank)/2;if(delta>0){
      for(const e of g.outEdges(n.id))g.edge(e).minlen=1+delta;changed=true;}}
    if(changed)dagre.layout(g,layoutOptions);
  }
  for (const node of view.nodes) Object.assign(node, g.node(node.id));
  let loopIndex=0,extraWidth=0;
  for (const e of view.edges) {
    if(feedback.has(e.id)){
      const a=g.node(e.from),b=g.node(e.to),x=g.graph().width+35+(loopIndex++%8)*34;
      const p={x:a.x+a.width/2,y:a.y},q={x:b.x+b.width/2,y:b.y};
      e.points=e.from===e.to?[p,{x,y:a.y},{x,y:a.y-a.height/2-35},{x:b.x,y:a.y-a.height/2-35},{x:b.x,y:a.y-a.height/2}]
        :[p,{x,y:a.y},{x,y:b.y},q];
      e.x=x;e.y=(a.y+b.y)/2;e.loop=true;
      if(!e.label){e.label='返回前面的剧情';e.lines=wrap(e.label,180,2);e.labelWidth=160;e.labelHeight=31;}
      extraWidth=Math.max(extraWidth,x+e.labelWidth/2+25-g.graph().width);continue;
    }
    const route = g.edge({ v: e.from, w: e.to, name: e.id });
    e.points = route.points; e.x = route.x; e.y = route.y;
    e.labelWidth = route.width; e.labelHeight = route.height;
  }
  view.width = g.graph().width+extraWidth; view.height = g.graph().height;
  return view;
}

// File partitions often include many isolated utility scripts. A single DAG
// puts these in one very wide rank and makes every title illegible at fit zoom.
// A compact atlas preserves all partitions; connections appear behind cards
// and are emphasized on hover. Detailed story views still use layered Dagre.
function layoutOverview(view) {
  if(view.mode==='story-overview'){
    const cols=3,byId=new Map(view.nodes.map(n=>[n.id,n]));
    view.nodes.sort((a,b)=>a.number-b.number);
    view.nodes.forEach((n,i)=>{n.width=280;n.height=166;n.x=60+n.width/2+(i%cols)*340;n.y=60+n.height/2+Math.floor(i/cols)*230;n.lines=wrap(n.title,252,5);});
    for(const e of view.edges){const a=byId.get(e.from),b=byId.get(e.to);const down=b.y>a.y;
      const p={x:a.x,y:a.y+(down?a.height/2:-a.height/2)},q={x:b.x,y:b.y+(down?-b.height/2:b.height/2)};
      e.points=[p,{x:p.x,y:p.y+(down?32:-32)},{x:q.x,y:q.y+(down?-32:32)},q];e.lines=[];e.label='';e.labelWidth=0;e.labelHeight=0;}
    view.width=1120;view.height=120+Math.ceil(view.nodes.length/cols)*230-64;return view;
  }
  const aspect = Math.max(1, Math.min(2.8, (view.viewport?.width || 1400) / (view.viewport?.height || 850)));
  const cols = Math.max(2, Math.round(Math.sqrt(view.nodes.length * aspect / 2.15)));
  const incoming = new Map(view.nodes.map(n => [n.id, 0])), out = new Map();
  for (const e of view.edges) { incoming.set(e.to, (incoming.get(e.to) || 0) + 1);
    if(!out.has(e.from))out.set(e.from,[]);out.get(e.from).push(e.to); }
  const queue = view.nodes.filter(n => incoming.get(n.id) === 0 && out.has(n.id)).map(n => n.id);
  const seen = new Set(), ordered = [], byId = new Map(view.nodes.map(n => [n.id,n]));
  for(let i=0;i<queue.length;i++){const id=queue[i];if(seen.has(id))continue;seen.add(id);ordered.push(byId.get(id));queue.push(...out.get(id)||[]);}
  for(const n of view.nodes)if(!seen.has(n.id))ordered.push(n);
  view.nodes=ordered;
  ordered.forEach((n,i)=>{n.width=190;n.height=82;n.x=40+n.width/2+(i%cols)*218;n.y=40+n.height/2+Math.floor(i/cols)*114;
    n.lines=wrap(n.title,168,2);n.current=n.path===view.currentPath;});
  for(const e of view.edges){const a=byId.get(e.from),b=byId.get(e.to),dy=b.y-a.y;
    const p={x:a.x,y:a.y+(dy>=0?a.height/2:-a.height/2)},q={x:b.x,y:b.y+(dy>=0?-b.height/2:b.height/2)};
    e.points=[p,{x:p.x,y:(p.y+q.y)/2},{x:q.x,y:(p.y+q.y)/2},q];e.lines=[];e.label='';e.labelWidth=0;e.labelHeight=0;}
  view.width=80+cols*218-28;view.height=80+Math.ceil(ordered.length/cols)*114-32;
  return view;
}
