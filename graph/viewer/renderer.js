const PALETTE = {
  choice: ['#deecff', '#91aac9', '#42638a'], condition: ['#fff0db', '#c7b18e', '#977344'],
  effect: ['#e6f4e9', '#9dbca5', '#557b61'], entry: ['#eee8f8', '#b2a5c9', '#7b6897'],
  boundary: ['#f0ebf8', '#afa0c5', '#796595'], section: ['#edeaf7', '#a8a0c3', '#73658f'],
  call: ['#eee9f7', '#b3a6c9', '#7d6996'], return: ['#eff2f6', '#aeb8c5', '#78879a'],
  junction: ['#f5f7fa', '#b9c3cf', '#8c99aa'], end: ['#eef2f6', '#adb9c7', '#7b8b9d'],
  unknown: ['#fbf0e4', '#c9b296', '#a18666'], overflow: ['#eee8f8', '#b2a5c9', '#7b6897'],
  candidate: ['#faf0e5', '#c9b296', '#a18666'], 'candidate-page': ['#eee8f8', '#b2a5c9', '#7b6897'],
  story: ['#e8f1ff', '#a5bad6', '#527398'], option: ['#ffffff','#a5bad6','#527398'],
  exploration: ['#eee8f8', '#b2a5c9', '#7b6897']
};
const NAMES = { choice: '玩家选择', condition: '自动判断', effect: '变量变化', entry: '入口',
  boundary: '继续探索 ↗', call: '子流程 ↗', return: '返回', end: '末端', unknown: '未知边界',
  junction: '汇合', exploration: '地点切换', section: '脚本分区 ↗', overflow: '展开连接 ↗', candidate: '候选连接', 'candidate-page': '更多候选 ↗' };
const intersects = (a,b) => a.x <= b.x+b.w && a.x+a.w >= b.x && a.y <= b.y+b.h && a.y+a.h >= b.y;

class SpatialIndex {
  constructor() { this.cells = new Map(); this.all = []; this.wide = []; }
  insert(item, bounds) {
    const record = { item, bounds }; this.all.push(record);
    const x0=Math.floor(bounds.x/400),x1=Math.floor((bounds.x+bounds.w)/400);
    const y0=Math.floor(bounds.y/400),y1=Math.floor((bounds.y+bounds.h)/400);
    if (item.kind==='edge'||(x1-x0+1)*(y1-y0+1)>24) { this.wide.push(record); return; }
    for(let x=x0;x<=x1;x++) for(let y=y0;y<=y1;y++) {
      const key=`${x},${y}`; if(!this.cells.has(key))this.cells.set(key,[]);this.cells.get(key).push(record);
    }
  }
  query(rect) {
    const x0=Math.floor(rect.x/400),x1=Math.floor((rect.x+rect.w)/400);
    const y0=Math.floor(rect.y/400),y1=Math.floor((rect.y+rect.h)/400);
    if ((x1-x0+1)*(y1-y0+1)>2000)return this.all.filter(r=>intersects(r.bounds,rect)).map(r=>r.item);
    const records=new Set();
    for(let x=x0;x<=x1;x++)for(let y=y0;y<=y1;y++)for(const r of this.cells.get(`${x},${y}`)||[])records.add(r);
    const result=[...records].filter(r=>intersects(r.bounds,rect)).map(r=>r.item);
    for(const r of this.wide)if(intersects(r.bounds,rect))result.push(r.item);
    return result;
  }
}

function roundRect(ctx,x,y,w,h,r=9) { ctx.beginPath();ctx.roundRect(x,y,w,h,r); }
function linePath(points) {
  const path=new Path2D(),line=points.filter((point,i)=>!i||point.x!==points[i-1].x||point.y!==points[i-1].y);
  if(!line.length)return path;
  path.moveTo(line[0].x,line[0].y);
  for(let i=1;i<line.length-1;i++) {
    const a=line[i-1],b=line[i],c=line[i+1],before=Math.hypot(a.x-b.x,a.y-b.y),after=Math.hypot(c.x-b.x,c.y-b.y);
    const radius=Math.min(8,before/2,after/2);
    path.lineTo(b.x+(a.x-b.x)*radius/before,b.y+(a.y-b.y)*radius/before);
    path.quadraticCurveTo(b.x,b.y,b.x+(c.x-b.x)*radius/after,b.y+(c.y-b.y)*radius/after);
  }
  if(line.length>1)path.lineTo(line.at(-1).x,line.at(-1).y);
  return path;
}
export class Renderer {
  constructor(canvas,{select,zoom,activate}) {
    this.canvas=canvas;this.ctx=canvas.getContext('2d',{alpha:false});this.selectCallback=select;
    this.activate=activate;this.zoomCallback=zoom;this.camera={x:0,y:0,k:1};this.view=null;
    this.index=new SpatialIndex();this.selected=null;this.hover=null;this.neighbors=new Set();
    this.reduced=matchMedia('(prefers-reduced-motion: reduce)');this.frames=[];this.textCache=new Map();this.pathCache=new Map();
    new ResizeObserver(()=>this.resize()).observe(canvas);
    this.bind();
  }
  resize(){const r=this.canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);
    if(this.width===r.width&&this.height===r.height&&this.dpr===dpr)return;
    this.width=r.width;this.height=r.height;this.dpr=dpr;
    this.canvas.width=Math.round(r.width*dpr);this.canvas.height=Math.round(r.height*dpr);
    // Resizing clears the canvas; paint before the browser presents that empty buffer.
    this.draw(performance.now());}
  bind(){
    this.canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;
      const local=this.local(e),m=this.minimap;
      if(m&&local.x>=m.x&&local.x<=m.x+m.w&&local.y>=m.y&&local.y<=m.y+m.h){
        const frame=m.frame,insideFrame=local.x>=frame.x-5&&local.x<=frame.x+frame.w+5&&local.y>=frame.y-6&&local.y<=frame.y+frame.h+6;
        if(!insideFrame){
          const wx=(local.x-m.x)/m.sx,wy=(local.y-m.y)/m.sy;
          this.moveCamera({k:this.camera.k,x:this.width/2-wx*this.camera.k,y:this.height/2-wy*this.camera.k},false);
        }
        this.animation=null;
        this.minimapDrag={id:e.pointerId,x:e.clientX,y:e.clientY,camera:{...this.camera}};
        this.canvas.setPointerCapture(e.pointerId);this.canvas.style.cursor='grabbing';return;
      }
      this.animation=null;this.pointer={id:e.pointerId,x:e.clientX,y:e.clientY,lastX:e.clientX,lastY:e.clientY,moved:false};
      this.canvas.setPointerCapture(e.pointerId);this.canvas.style.cursor='grabbing';});
    this.canvas.addEventListener('pointermove',e=>{
      if(this.minimapDrag?.id===e.pointerId){
        const drag=this.minimapDrag,m=this.minimap,k=drag.camera.k;
        const x=drag.camera.x-(e.clientX-drag.x)*k/m.sx,y=drag.camera.y-(e.clientY-drag.y)*k/m.sy;
        this.camera.x=Math.max(this.width/2-this.view.width*k,Math.min(this.width/2,x));
        this.camera.y=Math.max(this.height/2-this.view.height*k,Math.min(this.height/2,y));
        this.invalidate();return;
      }
      if(this.pointer&&this.pointer.id===e.pointerId){const p=this.pointer;
        if(Math.hypot(e.clientX-p.x,e.clientY-p.y)>4)p.moved=true;
        if(p.moved){this.camera.x+=e.clientX-p.lastX;this.camera.y+=e.clientY-p.lastY;this.invalidate();}
        p.lastX=e.clientX;p.lastY=e.clientY;return;}
      const local=this.local(e),m=this.minimap;
      if(m&&local.x>=m.x&&local.x<=m.x+m.w&&local.y>=m.y&&local.y<=m.y+m.h){
        if(this.hover){this.hover=null;this.invalidate();}
        const frame=m.frame,insideFrame=local.x>=frame.x-5&&local.x<=frame.x+frame.w+5&&local.y>=frame.y-6&&local.y<=frame.y+frame.h+6;
        this.canvas.style.cursor=insideFrame?'grab':'pointer';return;
      }
      const hit=this.hit(local);const id=hit?.id||null;
      if(id!==this.hover){this.hover=id;this.canvas.style.cursor=hit?'pointer':'grab';this.invalidate();}
    });
    this.canvas.addEventListener('pointerup',e=>{
      if(this.minimapDrag?.id===e.pointerId){this.minimapDrag=null;this.canvas.style.cursor='grab';return;}
      if(!this.pointer||this.pointer.id!==e.pointerId)return;
      if(!this.pointer.moved){const hit=this.hit(this.local(e));this.select(hit?.id||null);this.selectCallback(hit);}
      this.pointer=null;this.canvas.style.cursor='grab';
    });
    this.canvas.addEventListener('pointercancel',()=>{this.pointer=null;this.minimapDrag=null;this.canvas.style.cursor='grab';});
    this.canvas.addEventListener('pointerleave',()=>{if(!this.pointer&&!this.minimapDrag&&this.hover){this.hover=null;this.invalidate();}});
    this.canvas.addEventListener('dblclick',e=>{const local=this.local(e),m=this.minimap;if(m&&local.x>=m.x&&local.x<=m.x+m.w&&local.y>=m.y&&local.y<=m.y+m.h)return;const hit=this.hit(local);if(hit?.kind==='node')this.activate(hit);});
    this.canvas.addEventListener('wheel',e=>{if(!this.view)return;e.preventDefault();
      this.zoomAt(Math.exp(-e.deltaY*.0015),this.local(e));},{passive:false});
    this.canvas.addEventListener('keydown',e=>{if(!this.view)return;
      const movement={ArrowLeft:[60,0],ArrowRight:[-60,0],ArrowUp:[0,60],ArrowDown:[0,-60]}[e.key];
      if(movement){e.preventDefault();this.animation=null;this.camera.x+=movement[0];this.camera.y+=movement[1];this.invalidate();}
      if(e.key==='+'||e.key==='=')this.zoomAt(1.25);if(e.key==='-')this.zoomAt(.8);
      if(e.key==='0')this.fit();
    });
  }
  local(e){const r=this.canvas.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};}
  world(p){return{x:(p.x-this.camera.x)/this.camera.k,y:(p.y-this.camera.y)/this.camera.k};}
  zoomAt(factor,p={x:this.width/2,y:this.height/2}){this.animation=null;
    const world=this.world(p),k=Math.min(2.5,Math.max(.00001,this.camera.k*factor));
    this.camera={x:p.x-world.x*k,y:p.y-world.y*k,k};this.invalidate();this.zoomCallback(k);}
  moveCamera(target,animate=true){
    if(animate&&!this.reduced.matches)this.animation={start:performance.now(),from:{...this.camera},to:target};
    else{this.camera={...target};this.animation=null;}this.zoomCallback(target.k);this.invalidate();
  }
  fit(animate=true){if(!this.view)return;
    const widthK=(this.width-100)/this.view.width;
    if(this.view.mode==='whole'){
      // A full story can be tens of thousands of pixels tall. Fitting both
      // axes would reduce it to an unreadable one-percent strip, so the
      // overview fits the horizontal structure and starts at the entry.
      const k=Math.max(.18,Math.min(1.2,widthK));
      this.moveCamera({k,x:(this.width-this.view.width*k)/2,y:86},animate);return;
    }
    const k=Math.max(.00001,Math.min(1.2,widthK,(this.height-150)/this.view.height));
    this.moveCamera({k,x:(this.width-this.view.width*k)/2,y:70+(this.height-140-this.view.height*k)/2},animate);}
  focus(id,animate=true){id=this.view?.edgeAliases?.[id]||id;const n=this.nodeMap.get(id)||this.view?.edges.find(n=>n.id===id);if(!n)return;
    if(this.view.mode==='whole'&&n.type==='choice'){
      const children=this.view.edges.filter(e=>e.from===id).map(e=>this.nodeMap.get(e.to)).filter(n=>n&&!n.hidden),group=[n,...children];
      const left=Math.min(...group.map(n=>n.x-n.width/2)),right=Math.max(...group.map(n=>n.x+n.width/2));
      const k=Math.min(.95,(this.width-70)/(right-left));
      this.moveCamera({k,x:this.width/2-(left+right)/2*k,y:150-n.y*k},animate);return;
    }
    const k=Math.min(.95,Math.max(.65,this.camera.k));
    const top=n.type==='exploration'&&this.view.mode==='whole'
      ?Math.max(210,this.height*.25):Math.max(155,this.height*.21);
    this.moveCamera({k,x:this.width/2-n.x*k,y:top-n.y*k},animate);}
  readView(animate=false){
    const view=this.view,n=this.nodeMap.get(view.anchor);if(!n)return this.fit(animate);
    const k=Math.min(1,Math.max(.6,(this.width-60)/view.width));
    if(view.mode==='story-overview')return this.moveCamera({k,x:(this.width-view.width*k)/2,y:135},animate);
    this.moveCamera({k,x:(this.width-view.width*k)/2,y:110-(n.y-n.height/2)*k},animate);
  }
  setView(view,{camera,selected,animate=false}={}){
    this.pathCache.clear();
    this.labelOcclusionKey=null;
    this.minimap=null;this.minimapDrag=null;this.mapImage=null;
    const previous=this.view;this.view=view;this.nodeMap=new Map(view.nodes.map(n=>[n.id,n]));
    this.index=new SpatialIndex();
    for(const node of view.nodes){node.kind='node';if(node.hidden)continue;this.index.insert(node,{x:node.x-node.width/2-8,y:node.y-node.height/2-8,w:node.width+16,h:node.height+16});}
    for(const edge of view.edges){edge.kind='edge';
      if(view.player&&edge.returnRoute)continue;
      // Dagre intersects rectangles. Re-anchor a diamond on its actual outline.
      if(view.mode!=='whole'&&!edge.feedback&&!edge.compactRoute&&!edge.returnRoute) for(const [index,nodeId,nextIndex] of [[0,edge.from,1],[edge.points.length-1,edge.to,edge.points.length-2]]){
        const n=this.nodeMap.get(nodeId);if(!['choice','condition'].includes(n?.type)||edge.points.length<2)continue;
        const p=edge.points[nextIndex],dx=p.x-n.x,dy=p.y-n.y;
        const scale=1/(Math.abs(dx)/(n.width/2)+Math.abs(dy)/(n.height/2)||1);
        edge.points[index]={x:n.x+dx*scale,y:n.y+dy*scale};
      }
      const xs=edge.points.map(p=>p.x),ys=edge.points.map(p=>p.y);
      const x=Math.min(...xs,(edge.x||0)-edge.labelWidth/2),y=Math.min(...ys,(edge.y||0)-edge.labelHeight/2);
      const x2=Math.max(...xs,(edge.x||0)+edge.labelWidth/2),y2=Math.max(...ys,(edge.y||0)+edge.labelHeight/2);
      this.index.insert(edge,{x:x-12,y:y-12,w:x2-x+24,h:y2-y+24});
    }
    this.feedbackDegree=new Map();this.feedbackIncoming=new Map();
    for(const edge of view.edges)if(edge.feedback){
      for(const id of [edge.from,edge.to])
        this.feedbackDegree.set(id,(this.feedbackDegree.get(id)||0)+1);
      this.feedbackIncoming.set(edge.to,(this.feedbackIncoming.get(edge.to)||0)+1);
    }
    this.select(selected||null);this.hover=null;
    this.positionAnimation=animate&&previous&&!this.reduced.matches?{
      start:performance.now(),nodes:new Map(previous.nodes.map(n=>[n.id,{x:n.x,y:n.y}])),
      edges:new Map(previous.edges.map(e=>[e.id,{x:e.x,y:e.y}]))}:null;
    if(camera)this.moveCamera(camera,false);else if(view.player)this.readView();else if(view.mode==='overview')this.fit(false);else this.focus(view.anchor,false);
    this.invalidate();
  }
  select(id){id=this.view?.edgeAliases?.[id]||id;this.selected=id;this.neighbors=new Set(id?[id]:[]);
    for(const e of this.view?.edges||[])if(e.id===id||e.from===id||e.to===id){this.neighbors.add(e.id);this.neighbors.add(e.from);this.neighbors.add(e.to);}
    this.invalidate();}
  hoveredEdge(e){return this.hover===e.id||this.hover===e.from||this.hover===e.to;}
  connectedEdge(e){return this.selected&&this.neighbors.has(e.id)||this.hoveredEdge(e);}
  denseReturn(e){
    if(!e.feedback||this.selected===e.id||this.hover===e.id)return false;
    if(this.hover===e.from&&this.hover!==e.to&&(this.feedbackDegree.get(e.from)||0)<8)return false;
    if(this.selected===e.from&&this.selected!==e.to&&(this.feedbackDegree.get(e.from)||0)<8)return false;
    if(this.selected===e.to||this.hover===e.to)return true;
    return [this.selected,this.hover].some(id=>(id===e.from||id===e.to)
      &&(this.feedbackDegree.get(id)||0)>=8);
  }
  foregroundEdge(e){return this.connectedEdge(e)&&!this.denseReturn(e);}
  edgeLayer(e){return (e.feedback?4:0)+(e.id===this.hover?2:0)+(e.id===this.selected?1:0);}
  visibleEdge(e){
    const focused=this.connectedEdge(e);
    if(this.view.player&&e.returnRoute)return false;
    if((e.feedback||e.returnRoute)&&!focused)return false;
    if(e.compactRoute&&this.view.mode==='whole'&&this.camera.k<.5&&!focused)return false;
    return this.view.mode!=='story-overview'||focused;
  }
  returnLabelOccluders(){
    const key=`${this.selected}\0${this.hover}`;
    if(this.labelOcclusionKey!==key){
      this.labelOcclusionKey=key;
      this.labelOcclusion=this.view.edges.filter(edge=>edge.feedback&&this.connectedEdge(edge)&&!this.denseReturn(edge));
    }
    return this.labelOcclusion;
  }
  visibleLabel(e){
    if(!e.label||this.camera.k<.38||!this.visibleEdge(e)||this.denseReturn(e))return false;
    if(this.view.mode==='whole'&&!e.feedback&&!e.returnRoute)
      return !this.returnLabelOccluders().some(other=>
        Math.abs(e.x-other.x)<(e.labelWidth+other.labelWidth)/2+4
        &&Math.abs(e.y-other.y)<(e.labelHeight+other.labelHeight)/2+4);
    return true;
  }
  hit(p){if(!this.view||this.positionAnimation)return null;const w=this.world(p),r=9/this.camera.k;
    const items=this.index.query({x:w.x-r,y:w.y-r,w:2*r,h:2*r}).filter(item=>item.kind!=='edge'||this.visibleEdge(item));
    if(this.view.mode==='whole'&&this.camera.k>=.38)
      for(const e of items.filter(x=>x.kind==='edge'&&this.foregroundEdge(x)).sort((a,b)=>this.edgeLayer(a)-this.edgeLayer(b)).reverse())
        if(e.label&&Math.abs(w.x-e.x)<e.labelWidth/2+3&&Math.abs(w.y-e.y)<e.labelHeight/2+3)return e;
    for(const n of items.filter(x=>x.kind==='node').reverse()){
      const dx=Math.abs(w.x-n.x)/(n.width/2),dy=Math.abs(w.y-n.y)/(n.height/2);
      if(['choice','condition'].includes(n.type)?dx+dy<=1:dx<=1&&dy<=1)return n;
    }
    const edges=items.filter(x=>x.kind==='edge').reverse();
    for(const e of edges)
      if(this.visibleLabel(e)&&Math.abs(w.x-e.x)<e.labelWidth/2+3&&Math.abs(w.y-e.y)<e.labelHeight/2+3)return e;
    for(const e of edges){
      // Path2D uses the smoothed curve, so visible curves and hit targets agree.
      this.ctx.save();this.ctx.setTransform(1,0,0,1,0,0);this.ctx.lineWidth=r*2;
      const hit=this.ctx.isPointInStroke(this.pathFor(e),w.x,w.y);this.ctx.restore();if(hit)return e;
    }return null;
  }
  invalidate(){if(this.raf)return;this.raf=requestAnimationFrame(t=>{this.raf=0;this.draw(t);});}
  pathFor(e){let path=this.pathCache.get(e.id);if(!path){path=linePath(e.points);if(this.pathCache.size>=1000)this.pathCache.delete(this.pathCache.keys().next().value);this.pathCache.set(e.id,path);}return path;}
  draw(now){
    const started=performance.now(),ctx=this.ctx;if(!this.width||!this.height)return;
    if(this.animation){const t=Math.min(1,(now-this.animation.start)/160),q=1-(1-t)**3;
      for(const key of ['x','y','k'])this.camera[key]=this.animation.from[key]+(this.animation.to[key]-this.animation.from[key])*q;
      if(t===1)this.animation=null;else this.invalidate();}
    let transition=1;
    if(this.positionAnimation){transition=Math.min(1,(now-this.positionAnimation.start)/160);if(transition===1)this.positionAnimation=null;else this.invalidate();}
    ctx.setTransform(this.dpr,0,0,this.dpr,0,0);ctx.fillStyle='#ffffff';ctx.fillRect(0,0,this.width,this.height);
    if(!this.view)return;
    const {x,y,k}=this.camera;ctx.translate(x,y);ctx.scale(k,k);
    const viewport={x:(-x-40)/k,y:(-y-40)/k,w:(this.width+80)/k,h:(this.height+80)/k};
    const visible=this.positionAnimation?[...this.view.edges,...this.view.nodes]:this.index.query(viewport);
    const offset=n=>{const old=this.positionAnimation?.nodes.get(n.id);return old?{x:(old.x-n.x)*(1-transition),y:(old.y-n.y)*(1-transition)}:{x:0,y:0};};
    const foreground=[];
    for(const e of visible)if(e.kind==='edge'){
      if(this.foregroundEdge(e))foreground.push(e);
      else this.drawEdge(e,k,offset,transition,false);
    }
    for(const n of visible)if(n.kind==='node')this.drawNode(n,k,offset(n));
    if(this.view.crossings&&!this.positionAnimation&&k>=.4){
      ctx.save();ctx.lineWidth=1.25/k;ctx.strokeStyle='#8a96a7';ctx.fillStyle='#fff';
      const radius=6/k,span=7/k;
      for(const crossing of this.view.crossings){
        if(crossing.x<viewport.x||crossing.x>viewport.x+viewport.w
          ||crossing.y<viewport.y||crossing.y>viewport.y+viewport.h)continue;
        ctx.beginPath();ctx.arc(crossing.x,crossing.y,radius,0,Math.PI*2);ctx.fill();
        ctx.beginPath();
        if(crossing.horizontal){ctx.moveTo(crossing.x-span,crossing.y);
          ctx.quadraticCurveTo(crossing.x,crossing.y-12/k,crossing.x+span,crossing.y);}
        else{ctx.moveTo(crossing.x,crossing.y-span);
          ctx.quadraticCurveTo(crossing.x+12/k,crossing.y,crossing.x,crossing.y+span);}
        ctx.stroke();
      }
      ctx.restore();
    }
    foreground.sort((a,b)=>this.edgeLayer(a)-this.edgeLayer(b));
    for(const e of foreground)this.drawEdge(e,k,offset,transition,false);
    for(const e of visible)if(e.kind==='edge'&&!this.foregroundEdge(e))this.drawEdgeLabel(e,k,transition);
    if(this.marker){const n=this.nodeMap.get(this.marker)||this.view.edges.find(e=>e.id===this.marker);if(n){ctx.strokeStyle='#db4057';ctx.lineWidth=3/k;ctx.beginPath();ctx.arc(n.x,n.y,Math.max(n.width||30,n.height||30)/2+12/k,0,Math.PI*2);ctx.stroke();}}
    for(const e of foreground)this.drawEdgeLabel(e,k,transition);
    this.drawMinimap();
    this.lastDrawMs=performance.now()-started;
    if(this.measureFrames){this.frames.push({at:now,drawMs:this.lastDrawMs});if(this.frames.length>3000)this.frames.shift();}
  }
  drawMinimap(){
    const c=this.ctx,w=156,h=220,x=this.width-w-18,y=this.height-h-64;
    const sx=w/this.view.width,sy=h/this.view.height;
    const a=this.world({x:0,y:0}),b=this.world({x:this.width,y:this.height});
    const frameHeight=Math.max(8,(b.y-a.y)*sy);
    const frame={x:x+a.x*sx,y:y+(a.y+b.y)*sy/2-frameHeight/2,w:(b.x-a.x)*sx,h:frameHeight};
    this.minimap={x,y,w,h,sx,sy,frame};
    if(!this.mapImage){const image=document.createElement('canvas');image.width=w*2;image.height=h*2;const t=image.getContext('2d',{willReadFrequently:true});t.scale(2,2);t.fillStyle='#fafbfd';t.fillRect(0,0,w,h);
      t.strokeStyle='#c8d2df';t.lineWidth=.4;for(const e of this.view.edges){if(e.feedback||this.view.player&&e.returnRoute)continue;t.beginPath();e.points.forEach((p,i)=>i?t.lineTo(p.x*sx,p.y*sy):t.moveTo(p.x*sx,p.y*sy));t.stroke();}
      for(const n of this.view.nodes){if(n.hidden)continue;t.fillStyle=n.type==='choice'?'#527ead':'#97b6a4';t.fillRect(n.x*sx,n.y*sy,Math.max(1,n.width*sx),Math.max(.6,n.height*sy));}this.mapImage=image;}
    c.setTransform(this.dpr,0,0,this.dpr,0,0);c.globalAlpha=1;c.drawImage(this.mapImage,x,y,w,h);c.strokeStyle='#aab9ca';c.lineWidth=1;c.strokeRect(x,y,w,h);
    c.save();c.beginPath();c.rect(x,y,w,h);c.clip();c.fillStyle='#3c78b52b';c.strokeStyle='#397abd';
    c.fillRect(frame.x,frame.y,frame.w,frame.h);c.strokeRect(frame.x,frame.y,frame.w,frame.h);
    c.strokeStyle='#648bb9';c.lineWidth=.8;
    for(const edge of this.view.edges)if(edge.feedback&&this.visibleEdge(edge)){
      c.globalAlpha=this.denseReturn(edge) ? .35 : 1;
      c.beginPath();edge.points.forEach((p,i)=>i?c.lineTo(x+p.x*sx,y+p.y*sy):c.moveTo(x+p.x*sx,y+p.y*sy));c.stroke();
    }c.globalAlpha=1;
    const n=this.nodeMap.get(this.marker)||this.view.edges.find(e=>e.id===this.marker);if(n){c.fillStyle='#db4057';c.beginPath();c.arc(x+n.x*sx,y+n.y*sy,4,0,Math.PI*2);c.fill();}c.restore();
  }
  drawEdge(e,k,offset,transition,withLabel=true){const c=this.ctx,active=this.neighbors.has(e.id),hover=this.hoveredEdge(e);
    if(!this.visibleEdge(e))return;
    const quiet=this.denseReturn(e);
    c.globalAlpha=quiet ? .38 : this.view.mode==='overview'&&!active&&!hover ? .24 : 1;
    c.strokeStyle=quiet?'#8ca5bf':active||hover?'#648bb9':'#8a96a7';
    c.lineWidth=(quiet?1:active||hover?2:1.25)/Math.max(k,.6);
    c.setLineDash(quiet?[3,7]:e.feedback||e.compactRoute||e.returnRoute||e.uncertain||e.continuation?[6,5]:[]);
    let points=e.points,path=this.pathFor(e);
    if(this.positionAnimation){const a=offset(this.nodeMap.get(e.from)),b=offset(this.nodeMap.get(e.to));
      points=e.points.map((p,i)=>{const t=i/(e.points.length-1||1);return{x:p.x+a.x*(1-t)+b.x*t,y:p.y+a.y*(1-t)+b.y*t};});path=linePath(points);}
    c.stroke(path);c.setLineDash([]);
    c.fillStyle=c.strokeStyle;
    for(const point of e.junctions||[]){c.beginPath();c.arc(point.x,point.y,2.3/Math.max(k,.75),0,Math.PI*2);c.fill();}
    const last=points.at(-1),prev=points.at(-2)||last,angle=Math.atan2(last.y-prev.y,last.x-prev.x);
    c.fillStyle=c.strokeStyle;c.beginPath();c.moveTo(last.x,last.y);
    const size=8/Math.max(k,.7);c.lineTo(last.x-size*Math.cos(angle-.4),last.y-size*Math.sin(angle-.4));
    c.lineTo(last.x-size*Math.cos(angle+.4),last.y-size*Math.sin(angle+.4));c.closePath();c.fill();
    if(withLabel)this.drawEdgeLabel(e,k,transition);
    c.globalAlpha=1;
  }
  drawEdgeLabel(e,k,transition){
    if(!this.visibleLabel(e))return;
    const c=this.ctx,active=this.neighbors.has(e.id),hover=this.hoveredEdge(e),old=this.positionAnimation?.edges.get(e.id);
    c.globalAlpha=this.view.mode==='overview'&&!active&&!hover ? .24 : 1;
      const x=e.x+(old?(old.x-e.x)*(1-transition):0),y=e.y+(old?(old.y-e.y)*(1-transition):0);
      if(e.labelAnchor){c.beginPath();c.moveTo(e.labelAnchor.x,e.labelAnchor.y);
        c.lineTo(x,y);c.strokeStyle=active||hover?'#648bb9':'#b4bfcc';
        c.lineWidth=1/Math.max(k,.7);c.setLineDash([3,3]);c.stroke();c.setLineDash([]);}
      roundRect(c,x-e.labelWidth/2,y-e.labelHeight/2,e.labelWidth,e.labelHeight,10);
      c.fillStyle=e.feedback?'#fffaf1':e.compactRoute?'#f7f2fb':e.returnRoute?'#f2f3f6':'#ffffff';c.fill();c.strokeStyle=e.feedback?(active||hover?'#c58d52':'#d8b98f'):e.compactRoute?(active||hover?'#9a82b7':'#c5b7d5'):e.returnRoute?(active||hover?'#9aa7b8':'#c4ccd6'):(active?'#99b4d6':'#e1e6ed');c.lineWidth=1;c.stroke();
      c.textAlign='center';c.textBaseline='middle';
      e.lines.forEach((line,i)=>{
        const role=e.lineRoles?.[i] || (line.startsWith('变化：')?'effect':line.startsWith('条件：')?'condition':i===0?'primary':'annotation');
        c.font=`${e.feedback||e.compactRoute||e.returnRoute||role==='primary'?13:12}px "Segoe UI","Microsoft YaHei",sans-serif`;
        c.fillStyle=e.feedback?'#8a633b':e.compactRoute?'#725d8c':e.returnRoute?'#6f7e91':role==='effect'?'#557b61':role==='condition'?'#977344':role==='primary'?'#46576c':'#8492a4';
        c.fillText(line,x,y+(i-(e.lines.length-1)/2)*19);
      });
    c.globalAlpha=1;
  }
  drawNode(n,k,off){if(n.hidden)return;const c=this.ctx,x=n.x+off.x,y=n.y+off.y;
    const [fill,stroke,accent]=PALETTE[n.type]||PALETTE.entry,active=this.selected===n.id,hover=this.hover===n.id;
    const diamond=['choice','condition'].includes(n.type);
    if(n.compact){c.fillStyle=active?'#477ba9':'#a8b7ca';c.beginPath();c.arc(x,y,5,0,Math.PI*2);c.fill();return;}
    c.globalAlpha=1;c.fillStyle=n.special?'#f6f7fa':fill;c.strokeStyle=active?'#477ba9':stroke;c.lineWidth=(active?2.5:hover?2:1.15)/Math.max(k,.75);
    c.setLineDash(n.uncertain||n.type==='boundary'||n.type==='unknown'?[5,4]:[]);
    if(active){c.shadowColor='#658eb52b';c.shadowBlur=16;}
    if(diamond){c.beginPath();c.moveTo(x,y-n.height/2);c.lineTo(x+n.width/2,y);c.lineTo(x,y+n.height/2);c.lineTo(x-n.width/2,y);c.closePath();}
    else roundRect(c,x-n.width/2,y-n.height/2,n.width,n.height,n.type==='junction'?22:12);
    c.fill();c.stroke();c.shadowBlur=0;c.setLineDash([]);
    if(n.type==='junction'){c.globalAlpha=1;return;}
    if(n.type==='section'){
      c.textAlign='center';c.textBaseline='middle';c.fillStyle='#485973';
      c.font=`${Math.max(13,11/k)}px "Segoe UI","Microsoft YaHei",sans-serif`;
      const key=`${c.font}:${n.title}`;let lines=this.textCache.get(key);
      if(!lines){lines=[];let line='';for(const ch of n.title){
        if(c.measureText(line+ch).width>n.width-18){lines.push(line);line=ch;if(lines.length===2){lines[1]=lines[1].slice(0,-2)+'…';line='';break;}}
        else line+=ch;}if(line)lines.push(line);if(this.textCache.size>1200)this.textCache.clear();this.textCache.set(key,lines);}
      lines.forEach((line,i)=>c.fillText(line,x,y-8+(i-(lines.length-1)/2)*Math.max(18,12/k)));
      c.fillStyle='#8b83a0';c.font=`${Math.max(10,9/k)}px "Segoe UI","Microsoft YaHei",sans-serif`;
      c.fillText(k<.8?n.note.split(' · ')[0]:n.note,x,y+n.height/2-16,n.width-14);
      if(n.current){c.beginPath();c.arc(x-n.width/2+10,y-n.height/2+10,3,0,Math.PI*2);c.fillStyle='#799b83';c.fill();}
      return;
    }
    if(k<.3){c.globalAlpha=1;return;}
    c.textAlign='center';c.textBaseline='middle';
    c.font='10px "Segoe UI","Microsoft YaHei",sans-serif';c.fillStyle=accent;
    const top=y-(n.lines.length-1)*10-22;
    const nodeName=n.terminal?(n.type==='end'?'明确结束':n.type==='boundary'?'后续未解析':NAMES[n.type]||''):NAMES[n.type]||'';
    c.fillText(n.special?'自动继续 · 非玩家选项':n.type==='story'?`选择 ${n.number}`:n.preview?'接下来':nodeName,x,top);
    c.font=`${this.view.player?15:14}px "Segoe UI","Microsoft YaHei",sans-serif`;c.fillStyle='#3f5064';
    n.lines.forEach((line,i)=>c.fillText(line,x,y+(i-(n.lines.length-1)/2)*20));
    if(n.note&&k>=.5){c.font='10px "Segoe UI","Microsoft YaHei",sans-serif';c.fillStyle=accent;
      const count=this.feedbackIncoming.get(n.id)||0;
      const note=count>0&&(active||hover)?`${n.note} · ${count} 条返回`:n.note;
      c.fillText(note,x,y+(n.lines.length-1)*10+23,n.width-16);}
    else if(n.uncertain&&k>=.6&&!diamond&&!this.view.player){c.font='10px "Segoe UI","Microsoft YaHei",sans-serif';c.fillStyle='#b89a73';c.fillText('部分信息未确认',x,y+(n.lines.length-1)*10+23);}
    c.globalAlpha=1;
  }
}
