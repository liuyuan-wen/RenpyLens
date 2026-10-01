import { Renderer } from './renderer.js';
import { readable } from './player.js';

const $ = id => document.getElementById(id);
const el = (tag, text, className) => { const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e; };
const button = (text, action, className) => { const b=el('button',text,className);b.type='button';b.onclick=action;return b; };
let worker=null,sequence=0,generation=0,viewSequence=0,detailSequence=0,searchSequence=0;
let pending=new Map(),meta=null,current=null,history=[],currentDetail=null,detailHistory=[],importStarted=0,searchTimer;
let playerMode=true,directoryOffset=0,directorySequence=0;
const fragmentMode=()=>playerMode?'player':'fragment';
const metrics={imports:[],lastImport:null};
const renderer=new Renderer($('graph-canvas'),{select:onSelect,activate:activateNode,zoom:k=>$('zoom-level').textContent=`${Math.round(k*100)}%`});
const workspace=document.querySelector('.workspace');
const panes={
  sidebar:{element:$('sidebar'),shell:$('sidebar-pane'),handle:$('sidebar-resize'),property:'--sidebar-width',min:180,max:600,sign:1},
  details:{element:$('details'),shell:$('details-pane'),handle:$('details-resize'),property:'--details-width',min:260,max:700,sign:-1,open:false}
};
const reducedMotion=matchMedia('(prefers-reduced-motion:reduce)'),mobileSidebar=matchMedia('(max-width:650px)');
const paneStorage=name=>`renpylens.story-viewer.${name}-width`;
function storedPaneWidth(name){
  try{const width=Number(localStorage.getItem(paneStorage(name)));return width>0?width:null;}
  catch{return null;}
}
const panePreference={sidebar:storedPaneWidth('sidebar'),details:storedPaneWidth('details')};
function defaultPaneWidth(name){const w=workspace.clientWidth;
  return name==='sidebar'?(w<=900?185:w<=1350?224:250):w<=650?Math.min(340,w*.95):w<=1350?310:338;}
function paneLimit(name){const w=workspace.clientWidth,pane=panes[name];
  if(name==='sidebar'){
    const overlay=getComputedStyle(pane.shell).position==='absolute';
    const right=panes.details.open&&getComputedStyle(panes.details.shell).position!=='absolute'
      ?panes.details.element.getBoundingClientRect().width+8:0;
    return Math.max(pane.min,Math.min(pane.max,w-(overlay?48:368+right)));
  }
  const overlay=getComputedStyle(pane.shell).position==='absolute';
  const left=panes.sidebar.open&&!overlay
    ?panes.sidebar.element.getBoundingClientRect().width+8:0;
  return Math.max(pane.min,Math.min(pane.max,w-(overlay?48:368+left)));
}
function setPaneWidth(name,requested){const pane=panes[name],max=paneLimit(name);
  const width=Math.round(Math.min(max,Math.max(pane.min,requested)));
  workspace.style.setProperty(pane.property,`${width}px`);
  pane.handle.setAttribute('aria-valuemin',pane.min);
  pane.handle.setAttribute('aria-valuemax',Math.round(max));
  pane.handle.setAttribute('aria-valuenow',width);
  return width;
}
function syncPaneWidths(){
  setPaneWidth('details',panePreference.details??defaultPaneWidth('details'));
  setPaneWidth('sidebar',panePreference.sidebar??defaultPaneWidth('sidebar'));
  setPaneWidth('details',panePreference.details??defaultPaneWidth('details'));
}
function savePaneWidth(name){try{localStorage.setItem(paneStorage(name),panePreference[name]);}catch{}}
function setPaneOpen(name,open,{animate=true}={}){
  const pane=panes[name],shell=pane.shell;
  if(pane.open===open&&animate)return;
  shell.hidden=false;pane.element.hidden=false;
  const capture=()=>{const style=getComputedStyle(shell);return{width:style.width,opacity:style.opacity,transform:style.transform};};
  const before=capture();
  pane.motion?.cancel();pane.open=open;
  shell.classList.toggle('pane-closed',!open);shell.inert=!open;
  syncPaneWidths();if(name==='sidebar')syncSidebarToggle();
  const finish=()=>{shell.hidden=!pane.open;pane.element.hidden=!pane.open;shell.removeAttribute('data-animating');};
  if(!animate||reducedMotion.matches){pane.motion=null;finish();return;}
  // The shell clips fixed-width contents, so text does not reflow during a slide.
  const motion=shell.animate([before,capture()],{duration:160,easing:'cubic-bezier(.2,.8,.2,1)',fill:'both'});
  pane.motion=motion;shell.setAttribute('data-animating','');
  motion.finished.then(()=>{if(pane.motion!==motion)return;pane.motion=null;motion.cancel();finish();},()=>{});
}
for(const [name,pane] of Object.entries(panes)){
  let drag=null;
  pane.handle.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();
    drag={id:e.pointerId,x:e.clientX,width:pane.element.getBoundingClientRect().width};
    pane.handle.setPointerCapture(e.pointerId);document.body.classList.add('resizing-panes');});
  pane.handle.addEventListener('pointermove',e=>{if(drag?.id!==e.pointerId)return;
    panePreference[name]=setPaneWidth(name,drag.width+(e.clientX-drag.x)*pane.sign);});
  const finish=e=>{if(drag?.id!==e.pointerId)return;drag=null;
    document.body.classList.remove('resizing-panes');savePaneWidth(name);};
  pane.handle.addEventListener('pointerup',finish);
  pane.handle.addEventListener('pointercancel',finish);
  pane.handle.addEventListener('lostpointercapture',finish);
  pane.handle.addEventListener('keydown',e=>{
    if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;e.preventDefault();
    const direction=e.key==='ArrowRight'?1:-1,step=e.shiftKey?40:16;
    panePreference[name]=setPaneWidth(name,pane.element.getBoundingClientRect().width+direction*pane.sign*step);
    savePaneWidth(name);
  });
}
function sidebarVisible(){return document.body.classList.contains('sidebar-collapsed')===mobileSidebar.matches;}
function syncSidebarToggle(){const visible=panes.sidebar.open,label=`${visible?'收起':'展开'}左侧目录`;
  $('toggle-sidebar').setAttribute('aria-expanded',String(visible));$('toggle-sidebar').setAttribute('aria-label',label);$('toggle-sidebar').title=label;}
window.addEventListener('resize',()=>{
  setPaneOpen('sidebar',sidebarVisible(),{animate:false});setPaneOpen('details',panes.details.open,{animate:false});
});
reducedMotion.addEventListener('change',()=>{if(reducedMotion.matches)for(const [name,pane] of Object.entries(panes))setPaneOpen(name,pane.open,{animate:false});});
setPaneOpen('sidebar',sidebarVisible(),{animate:false});setPaneOpen('details',false,{animate:false});

function announce(s){$('announcer').textContent=s;}
function error(message){$('error').textContent=message;$('error').hidden=false;announce(message);}
function resetDetailHistory(){detailHistory=[];$('detail-back').hidden=true;}
function stopWorker(){generation++;viewSequence++;detailSequence++;searchSequence++;
  worker?.terminate();worker=null;for(const {reject} of pending.values())reject(new Error('cancelled'));pending.clear();}
function request(type,payload={}){
  if(!worker)return Promise.reject(new Error('cancelled'));
  const id=++sequence;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});worker.postMessage({id,type,payload});});
}
function setupWorker(){stopWorker();
  const blob=new Blob([$('worker-source').textContent],{type:'text/javascript'}),url=URL.createObjectURL(blob);
  worker=new Worker(url);URL.revokeObjectURL(url);
  worker.onmessage=({data})=>{if(data.type==='progress'){
    $('loading-title').textContent=data.result.stage;
    const {bytes,total}=data.result;$('progress-bar').style.width=`${Math.min(100,bytes/total*100)}%`;
    $('loading-progress').textContent=`${(bytes/1048576).toFixed(1)} / ${(total/1048576).toFixed(1)} MB`;return;
  }const entry=pending.get(data.id);if(!entry)return;pending.delete(data.id);
    if(data.type==='error')entry.reject(new Error(data.message));else entry.resolve(data.result);
  };
  worker.onerror=e=>{for(const {reject} of pending.values())reject(new Error(e.message||'后台处理失败'));pending.clear();};
}
function enabled(value){for(const id of ['search','overview','entry','player-mode','source-mode'])$(id).disabled=!value;}
function clearGraph(){meta=null;current=null;history=[];currentDetail=null;resetDetailHistory();renderer.view=null;renderer.selected=null;renderer.hover=null;
  renderer.marker=null;renderer.minimap=null;renderer.minimapDrag=null;renderer.mapImage=null;$('my-position').hidden=true;
  renderer.nodeMap=new Map();renderer.textCache.clear();renderer.pathCache.clear();renderer.animation=null;renderer.positionAnimation=null;
  renderer.index.cells.clear();renderer.index.all=[];renderer.index.wide=[];renderer.invalidate();
  $('sections').replaceChildren(el('p','导入后在这里切换分区','empty-note'));
  $('section-count').textContent='—';$('graph-summary').textContent='从选择开始，读懂每条分支。';
  setPaneOpen('details',false,{animate:false});
  for(const id of ['view-toolbar','legend','zoom-controls','stage-hint','accessible-nodes','search-results','view-busy','directory-pager'])$(id).hidden=true;
  $('detail-content').replaceChildren();$('node-list').replaceChildren();$('search-results').replaceChildren();$('search').value='';enabled(false);
}
async function openFile(file){if(!file)return;
  setupWorker();const gen=generation;clearGraph();$('welcome').hidden=true;$('loading').hidden=false;$('error').hidden=true;
  $('progress-bar').style.width='0%';$('loading-title').textContent='读取剧情';$('loading-progress').textContent='正在准备…';
  $('file-name').textContent=file.name;$('file-name').title=file.name;importStarted=performance.now();
  try{
    const imported=await request('import',{file});if(gen!==generation)return;meta=imported;
    enabled(true);$('graph-summary').textContent=`${meta.directory.total.toLocaleString()} 处选择 · 看清每个回答的后果`;
    playerMode=true;directoryOffset=0;updateMode();renderDirectory(meta.directory);$('loading-title').textContent='布局完整剧情图';
    await navigate({mode:'player',anchor:meta.firstChoice,context:[]},{push:false});
    if(gen!==generation)return;
    $('loading').hidden=true;$('welcome').hidden=true;
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(gen!==generation)return;
    metrics.lastImport={file:file.name,bytes:file.size,elapsedMs:performance.now()-importStarted,
      parseAndIndexMs:meta.importMs,layoutMs:renderer.view?.layoutMs,stats:meta.stats};metrics.imports.push(metrics.lastImport);
    announce(`已打开 ${file.name}，${meta.directory.total} 个选项。`);
  }catch(e){if(e.message==='cancelled'||gen!==generation)return;
    stopWorker();clearGraph();$('loading').hidden=true;$('welcome').hidden=false;error(e.message);}
}
function renderSections(){const fragment=document.createDocumentFragment();
  for(const s of [...meta.sections].sort((a,b)=>a.id.localeCompare(b.id))){
    const b=button('',()=>navigate({mode:'fragment',anchor:s.anchor,context:[]}));b.dataset.path=s.id;b.title=s.id;
    b.append(el('span',s.name,'section-name'),el('span',`${s.menus} 个选项 · ${s.blocks} 块`,'section-meta'));fragment.append(b);
  }$('sections').replaceChildren(fragment);
}
function updateMode(){
  $('player-mode').classList.toggle('active',playerMode);$('source-mode').classList.toggle('active',!playerMode);
  $('directory-heading').textContent=playerMode?'选择目录':'脚本分区';
  $('overview').lastChild.textContent=playerMode?' 查看完整剧情图':' 文件概览';
  $('entry').lastChild.textContent=playerMode?' 从第一个选择开始':' 从脚本入口开始';
  $('section-count').textContent=playerMode?meta.directory.total:meta.stats.sections;
  $('directory-pager').hidden=!playerMode;
  if(!playerMode)renderSections();
}
function renderDirectory(result){
  const fragment=document.createDocumentFragment();
  for(const item of result.items){const b=button('',()=>navigate({mode:'player',anchor:item.id,context:[]}));
    b.dataset.choice=item.id;b.title=item.title;
    b.append(el('span',`选择 ${item.number}`,'choice-number'),el('span',item.title,'choice-title'),
      el('span',item.options.slice(0,3).join(' / ')+(item.options.length>3?' / …':''),'choice-options'));fragment.append(b);}
  if(!result.items.length)fragment.append(el('p','没有提取到玩家选项，可用源文件视图浏览。','empty-note'));
  $('sections').replaceChildren(fragment);directoryOffset=result.offset;
  const prev=button('上一组',()=>loadDirectory(Math.max(0,result.offset-40)));prev.disabled=!result.offset;
  const next=button('下一组',()=>loadDirectory(result.offset+40));next.disabled=result.offset+40>=result.total;
  $('directory-pager').replaceChildren(prev,el('span',`${Math.min(result.offset+1,result.total)}–${Math.min(result.offset+40,result.total)}`),next);
  $('directory-pager').hidden=false;
}
async function loadDirectory(offset){const token=++directorySequence,gen=generation;
  try{const result=await request('directory',{offset});if(token===directorySequence&&gen===generation&&playerMode)renderDirectory(result);}
  catch(e){if(e.message!=='cancelled'&&gen===generation)error(e.message);}
}
async function switchMode(value){playerMode=value;updateMode();if(value)await loadDirectory(directoryOffset);
  await navigate({mode:fragmentMode(),anchor:current?.anchor||meta.firstChoice,context:current?.context||[]});
}
function snapshot(){return current?{payload:current,camera:{...renderer.camera},selected:renderer.selected}:null;}
async function navigate(payload,{push=true,restore=null}={}){
  resetDetailHistory();
  if(payload.mode==='player'&&renderer.view?.mode==='whole'){
    if(push){history.push(snapshot());if(history.length>30)history.shift();}
    current={...payload};
    if(restore){renderer.moveCamera(restore.camera,false);renderer.select(restore.selected);}
    else {renderer.focus(payload.anchor);renderer.select(payload.anchor);}
    for(const b of $('sections').children)b.classList.toggle('active',b.dataset.choice===payload.anchor);
    $('back').disabled=!history.length;$('search-results').hidden=true;return;
  }
  if(!worker)return;const token=++viewSequence,gen=generation,old=snapshot();
  if(payload.mode==='overview')payload={...payload,viewport:{width:renderer.width,height:renderer.height},
    currentPath:payload.currentPath||renderer.view?.nodes.find(n=>n.id===renderer.view?.anchor)?.path};
  detailSequence++;currentDetail=null;setPaneOpen('details',false);$('view-busy').hidden=false;
  $('search-results').hidden=true;$('error').hidden=true;
  try{const view=await request('view',payload);if(token!==viewSequence||gen!==generation)return;
    if(push&&old){history.push(old);if(history.length>30)history.shift();}
    current={...payload,anchor:payload.anchor||view.anchor};
    if(view.mode==='whole'||view.mode==='player'||view.mode==='story-overview'){if(!playerMode){playerMode=true;updateMode();await loadDirectory(directoryOffset);}}
    else if(view.mode==='fragment'||view.mode==='overview'){if(playerMode){playerMode=false;updateMode();}}
    if(view.number&&playerMode){const offset=Math.floor((view.number-1)/40)*40;if(offset!==directoryOffset)await loadDirectory(offset);}
    if(token!==viewSequence||gen!==generation)return;
    // Coordinates from an unrelated fragment are not a meaningful animation
    // origin. Only interpolate a re-layout of the same anchored fragment.
    const animate=!!old&&payload.mode==='fragment'&&renderer.view?.anchor===view.anchor;
    renderer.setView(view,{camera:restore?.camera,selected:restore?.selected,animate});
    if(view.mode==='whole'&&!restore){renderer.focus(payload.anchor||view.anchor,false);renderer.select(payload.anchor||view.anchor);}
    $('view-title').textContent=view.title;$('view-title').title=view.title;
    $('view-count').textContent=view.player?view.number?`选择 ${view.number}`:`${view.choiceCount} 处相连选择`:`${view.nodes.length} 个节点 · ${view.edges.length} 条连接`;
    $('back').disabled=!history.length;
    for(const id of ['view-toolbar','zoom-controls','accessible-nodes'])$(id).hidden=false;
    $('legend').hidden=view.player;$('stage-hint').hidden=true;
    if(view.mode==='whole')$('accessible-nodes').hidden=true;
    $('overview').classList.toggle('active',['overview','story-overview'].includes(view.mode));$('entry').classList.toggle('active',view.anchor===meta.firstChoice);
    const path=view.nodes.find(n=>n.id===view.anchor)?.path;
    for(const b of $('sections').children)b.classList.toggle('active',playerMode?b.dataset.choice===view.anchor:b.dataset.path===path);
    if(playerMode)$('sections').querySelector('.active')?.scrollIntoView({block:'nearest'});
    const fragment=document.createDocumentFragment();
    for(const n of view.nodes.filter(n=>!n.compact).slice(0,150))fragment.append(button(n.title,()=>{renderer.focus(n.id);renderer.select(n.id);onSelect(n);}));
    $('node-list').replaceChildren(fragment);announce(`${view.title}，${view.nodes.length} 个节点。`);
    if(restore?.selected){const item=view.nodes.find(n=>n.id===restore.selected)||view.edges.find(e=>e.id===restore.selected);
      if(item&&!item.preview&&!['story','boundary','section','candidate','candidate-page','overflow'].includes(item.type))onSelect(item);}
  }catch(e){if(e.message!=='cancelled'&&gen===generation)error(e.message);}
  finally{if(token===viewSequence)$('view-busy').hidden=true;}
}
function activateNode(n){if(!n)return;
  if(n.preview||n.type==='story')navigate({mode:'player',anchor:n.target,context:current.context||[]});
  else if(n.type==='boundary'&&n.target)navigate({mode:fragmentMode(),anchor:n.target,context:n.popContext?(current.context||[]).slice(0,-1):current.context||[]});
  else if(n.type==='section')navigate({mode:'fragment',anchor:n.target,context:[]});
  else if(n.type==='candidate'&&n.target)navigate({mode:'fragment',anchor:n.target,context:[]});
  else if(n.type==='candidate-page')navigate({mode:'candidates',anchor:n.target,offset:n.offset});
  else if(n.type==='call')showDetail('node',n.id);
}
function onSelect(item){if(!item){detailSequence++;currentDetail=null;resetDetailHistory();setPaneOpen('details',false);return;}
  if(item.summary){navigate({mode:'player',anchor:item.from,context:current.context||[]});return;}
  if(item.preview||item.type==='story'||item.type==='boundary'&&item.target||item.type==='section'||item.type==='candidate'||item.type==='candidate-page'){activateNode(item);return;}
  if(item.type==='overflow'){showDetail('node',item.target);return;}
  if(item.detailId)showDetail(item.detailKind||item.kind,item.detailId);
}
function displayEdge(id){return renderer.view?.edges.find(edge=>edge.id===(renderer.view.edgeAliases?.[id]||id));}
function openDetailChild(kind,id,activate,sourceEdge=null){
  if(currentDetail){detailHistory.push({...currentDetail,scrollTop:$('details').scrollTop,
    camera:{...renderer.camera},selected:renderer.selected});$('detail-back').hidden=false;}
  activate?.();showDetail(kind,id,0,true,null,sourceEdge);
}
async function showDetail(kind,id,page=0,keepHistory=false,restoreScroll=null,sourceEdge=null){
  if(!keepHistory)resetDetailHistory();
  const token=++detailSequence,gen=generation;
  setPaneOpen('details',true);
  if(page===0)$('detail-content').replaceChildren(el('p','正在读取…','source-location'));
  const edge=kind==='edge'?renderer.view?.edges.find(e=>e.id===id):null;
  try{const detail=await request('detail',{kind,id,page,player:playerMode,trace:edge?.trace,blocks:edge?.blocks,sourceEdge});if(token!==detailSequence||gen!==generation)return;
    if(!detail){setPaneOpen('details',false);return;}currentDetail={kind,id,page,sourceEdge};renderDetail(detail);
    if(restoreScroll!==null)$('details').scrollTop=restoreScroll;}
  catch(e){if(e.message!=='cancelled')error(e.message);}
}
function backDetail(){const previous=detailHistory.pop();if(!previous)return;
  $('detail-back').hidden=!detailHistory.length;
  renderer.moveCamera(previous.camera,false);renderer.select(previous.selected);
  showDetail(previous.kind,previous.id,previous.page,true,previous.scrollTop,previous.sourceEdge);
}
function section(root,title){const div=el('section',undefined,'detail-section');div.append(el('h3',title));root.append(div);return div;}
function foldout(root,title){const pane=el('details',undefined,'detail-foldout');pane.append(el('summary',title));root.append(pane);return pane;}
function choiceText(raw){const value=readable(raw);const dynamic=value.match(/^(\[[^\]\n]+\])\s+(.+)$/s);
  return {label:dynamic?dynamic[2]:value,dynamic:dynamic?.[1]||''};}
function impactRows(parent,rows){
  function append(target,row){const item=el('div',undefined,`effect-line${row.kind==='unknown'?' unknown':row.kind==='input'?' input-effect':''}`);
    if(row.condition)item.append(el('div',`若 ${row.condition}`,'impact-condition'));
    item.append(el('div',row.text));
    if(row.prompt)item.append(el('p',row.prompt,'input-prompt'));
    if(row.default!==undefined)item.append(el('div',`留空时使用：${row.default}`,'input-default'));
    target.append(item);
  }
  for(const row of rows.slice(0,6))append(parent,row);
  if(rows.length>6){const more=foldout(parent,`其它影响 · ${rows.length-6} 项`);
    more.addEventListener('toggle',()=>{if(!more.open||more.dataset.loaded)return;more.dataset.loaded='true';
      for(const row of rows.slice(6))append(more,row);});}
}
function rawEffects(parent,effects){for(const effect of effects){const guard=effect.originalGuard??effect.guard;
  parent.append(el('div',`${guard?`若 ${guard}：\n`:''}${effect.text}`,'raw-effect'));
  if(effect.source)parent.append(el('pre',effect.source,'raw-effect'));}}
function renderDetail(d){const root=document.createDocumentFragment();
  const menu=playerMode&&d.kind==='node'&&d.nodeType==='choice';
  const answer=playerMode&&d.kind==='edge'&&d.edgeType==='MENU_CHOICE';
  const heading=answer?choiceText(d.title):null;
  root.append(el('span',menu?'剧情选择':answer?'选项分支':d.kind==='return-group'?'返回路线':d.kind==='route'?'后续走向':d.kind==='edge'?'分支连接':'剧情节点','detail-kind'),
    el('h2',heading?.label||d.title,'detail-title'));
  if(d.kind==='return-group'){
    const s=section(root,`返回分支 · ${d.answers.length}`);
    for(const [index,answer] of d.answers.entries()){
      const row=button('',()=>openDetailChild('route',answer.id,()=>renderer.select(answer.id)),'menu-choice');
      row.append(el('span',String(index+1).padStart(2,'0'),'menu-choice-number'));
      const copy=el('span',undefined,'menu-choice-copy');copy.append(el('strong',choiceText(answer.choice).label||'继续'));
      if(answer.condition)copy.append(el('span',`条件：${answer.condition}`,'menu-choice-condition'));
      row.append(copy,el('span','→','menu-choice-arrow'));s.append(row);
    }
    s.append(button(`定位${d.title}`,()=>{renderer.focus(d.target);renderer.select(d.target);},'detail-button'));
  }
  if(heading?.dynamic)root.append(el('p',`游戏内动态文字：${heading.dynamic}`,'detail-dynamic'));
  if(playerMode&&d.kind==='node'&&d.nodeType==='choice')root.append(button('设为当前位置',()=>{
    renderer.marker=d.position;renderer.select(d.position);renderer.invalidate();$('my-position').hidden=false;
  },'detail-position'));
  if(d.path&&!playerMode)root.append(el('div',`${d.path}${d.line!==null?` : ${d.line}`:''}`,'source-location'));
  if(menu){const choices=d.connections.filter(c=>c.kind==='MENU_CHOICE');const s=section(root,`选项 · ${choices.length}`);
    for(const [index,connection] of choices.entries()){
      const text=choiceText(connection.choice||connection.title),row=button('',()=>openDetailChild('edge',connection.id),'menu-choice');
      row.append(el('span',String(index+1).padStart(2,'0'),'menu-choice-number'));
      const copy=el('span',undefined,'menu-choice-copy');copy.append(el('strong',text.label||'未命名选项'));
      if(connection.condition&&connection.condition!=='True')copy.append(el('span',`出现条件：${connection.appearance||connection.condition}`,'menu-choice-condition'));
      if(text.dynamic)copy.append(el('span',`游戏内动态文字：${text.dynamic}`,'menu-choice-dynamic'));
      row.append(copy,el('span','→','menu-choice-arrow'));s.append(row);
    }
  }
  if(playerMode&&d.kind==='node'&&renderer.view?.mode==='whole'){
    const incoming=renderer.view.edges.filter(edge=>edge.feedback&&edge.to===d.id);
    if(incoming.length){
      const s=foldout(root,`返回这里的路径 · ${incoming.length}`),groups=new Map();
      for(const edge of incoming){
        if(!groups.has(edge.from))groups.set(edge.from,[]);
        groups.get(edge.from).push(edge);
      }
      for(const [sourceId,edges] of groups){
        const source=renderer.nodeMap.get(sourceId);
        s.append(el('h4',source?.note||source?.title||'前序剧情','return-source'));
        for(const edge of edges){
          const label=edge.returnMembers?`${edge.returnMembers.length} 条返回分支`:choiceText(edge.choice).label||
            (edge.routeCondition?`条件：${edge.routeCondition}`:'返回此处');
          s.append(button(label,()=>openDetailChild(edge.detailKind||'route',edge.id,()=>{
            renderer.select(edge.id);renderer.focus(edge.from);
          }),'detail-button'));
        }
      }
    }
  }
  if(d.destinations){const s=section(root,'后续走向');
    for(const route of d.destinations){
      s.append(button(route.title,()=>openDetailChild('route',route.id,()=>renderer.select(route.id)),'detail-button'));
      if(route.condition)s.append(el('p',route.condition,'route-condition'));
    }
    if(d.activities.length){const more=foldout(root,'探索中的其它事件');
      for(const route of d.activities){more.append(button('查看事件',()=>openDetailChild('route',route.id),'detail-button'));
        if(route.condition)more.append(el('p',route.condition,'route-condition'));}}
  }
  if(d.condition&&d.condition!=='True')section(root,answer?'出现条件':'触发条件').append(el('p',
    d.edgeType==='MENU_FALLTHROUGH'?'所有选项都不显示时':playerMode?d.conditionText||d.condition:d.condition,'detail-text'));
  const visibleEffects=d.kind==='route'?[]:d.effects.filter(fx=>!(fx.unknown&&fx.reason==='presentation-statement'));
  if(visibleEffects.length){const s=section(root,playerMode?'选择后的影响':'变量与影响');
    if(playerMode)impactRows(s,d.impact||[]);else rawEffects(s,visibleEffects);}
  if(answer&&d.nextRoutes?.length){const s=section(root,'后续走向');for(const route of d.nextRoutes){
    const b=button(`查看结果 → ${route.title}`,()=>openDetailChild('route',route.id,()=>{
      const edge=displayEdge(route.id);
      if(edge){renderer.select(edge.id);renderer.focus(edge.from);}
    },d.id),'detail-button');s.append(b);
    if(route.conditions?.length)s.append(el('p',route.conditions.join('；'),'route-condition'));
  }}
  if(answer&&d.backupRoutes?.length){const s=foldout(root,'特殊条件下的其它去向');
    for(const route of d.backupRoutes){s.append(button(`查看 → ${route.title}`,()=>openDetailChild('route',route.id,null,d.id),'detail-button'));
      if(route.conditions?.length)s.append(el('p',route.conditions.join('；'),'route-condition'));}}
  if(d.kind==='route'&&d.paths?.length){const s=section(root,d.backup?'特殊条件下的连接':d.paths.length>1?`条件分支 · ${d.paths.length}`:'选择后的剧情');
    for(const path of d.paths){const item=el('div',undefined,'route-case');
      for(const label of path.labels.filter(label=>!label.startsWith('条件：')))item.append(el('strong',readable(label),'route-option'));
      if(!path.labels.length)item.append(el('strong','继续','route-option'));
      if(path.condition)item.append(el('p',`沿途条件：${path.condition}`,'route-condition'));
      impactRows(item,path.impact);
      if(path.dialogueFlags?.length)item.append(el('p',`背景对白随 ${path.dialogueFlags.join('、')} 的状态变化`,'dialogue-variation'));
      if(path.loops?.length){
        for(const loop of path.loops){
          if(loop.exits.length)item.append(el('p',`循环退出条件：${loop.exits.join('；或 ')}`,'route-condition'));
          const loops=foldout(item,'循环中可能重复的影响');impactRows(loops,loop.impact);
        }}
      s.append(item);
    }
    if(d.target&&renderer.view?.nodes.some(n=>n.id===d.target))s.append(button(
      `定位${d.title.replace(/ · .+$/,'').replace(/^通往/,'')}`,()=>{renderer.focus(d.target);renderer.select(d.target);},'detail-button'));
  }
  if(d.hidden?.length&&!playerMode){const s=section(root,'图中省略的内部控制流');
    const seen=new Set();for(const item of d.hidden){const key=`${item.reason}|${item.path}|${item.line}`;if(seen.has(key))continue;seen.add(key);s.append(el('div',`${item.reason}${item.path?` · ${item.path}:${item.line??'?'}`:''}`,'effect-line unknown'));}
  }
  if(d.calls.length){const s=section(root,playerMode?'进入后续剧情':'调用子流程');for(const call of d.calls){
    const b=button(`${playerMode&&d.calls.length===1?'定位这段剧情':call.label} ↗`,()=>navigate({mode:fragmentMode(),anchor:call.target,
      context:[...(current.context||[]),{call:call.call,continuation:call.continuation}]}),'detail-button');b.disabled=!call.target;s.append(b);
    if(!playerMode)s.append(el('p',call.continuation?'沿此入口查看时，返回位置与本次调用对应。':'此调用没有已知的返回位置。','source-location'));
  }}
  if(d.connections.length&&!menu){const s=section(root,`后续连接 · ${d.connections.length}`);
    let offset=0;const more=button('显示更多连接',appendConnections,'detail-button');
    function appendConnections(){more.remove();for(const connection of d.connections.slice(offset,offset+60)){
      const label=connection.title==='NEXT'?'继续':connection.title==='CONTINUATION'?'返回后':connection.title;
      s.append(button(label,()=>openDetailChild('edge',connection.id),'detail-button'));}offset+=60;if(offset<d.connections.length)s.append(more);}
    appendConnections();
  }
  if(d.kind==='edge'){
    const edge=renderer.view?.edges.find(e=>e.id===d.id),target=renderer.view?.nodes.find(n=>n.id===edge?.to);
    if(target?.target)root.append(button('继续浏览此分支 ↗',()=>activateNode(target),'detail-button'));
    if(edge){root.append(button('在图中查看这条分支',()=>{renderer.select(edge.id);const n=renderer.view.nodes.find(n=>n.id===edge.from);if(n)renderer.focus(n.id);},'detail-button'));}
  }
  if(d.candidates.length&&!playerMode){const s=section(root,'候选连接 · 尚未确认');
    s.append(button('在图中查看候选连接 ↗',()=>navigate({mode:'candidates',anchor:d.id}),'detail-button'));
    for(const candidate of d.candidates){s.append(el('p',`${candidate.title}\n${candidate.condition}\n${candidate.derivation}`,'detail-text'));
      if(candidate.target)s.append(button('查看候选目标 ↗',()=>navigate({mode:'fragment',anchor:candidate.target,context:[]}),'detail-button'));}
  }
  if(!playerMode&&(d.precision.topology!=='exact'||d.precision.state!=='exact')){
    const s=section(root,'分析状态');s.append(el('p',`连接：${precisionName(d.precision.topology)} · 变量：${precisionName(d.precision.state)}`,'source-location'));
  }
  if(d.lineCount){const textSection=playerMode?foldout(root,`剧情原文 · ${d.lineCount} 条`):section(root,`剧情原文 · ${d.lineCount} 条`);
    for(const line of d.lines){const row=el('div',undefined,'dialogue');if(line.who)row.append(el('div',line.who,'speaker'));
      if(line.condition)row.append(el('div',`此段对白对应：${line.condition}`,'source-location'));
      row.append(el('p',playerMode?readable(line.text):line.text),el('div',`${line.path}:${line.line??'?'} · 所属剧情块`,'source-location'));textSection.append(row);}
    if(d.pages>1){const pager=el('div',undefined,'pager');const prev=button('上一页',()=>showDetail(d.kind,d.id,d.page-1,true,null,d.sourceEdge));prev.disabled=d.page===0;
      const next=button('下一页',()=>showDetail(d.kind,d.id,d.page+1,true,null,d.sourceEdge));next.disabled=d.page+1===d.pages;
      pager.append(prev,el('span',`${d.page+1} / ${d.pages}`),next);textSection.append(pager);}
  }
  if(d.predecessors?.length){const s=foldout(root,`可能的来路 · ${d.predecessors.length}`);
    s.append(el('p','图上可能从这些选择到达这里，不代表你已经经历过。','source-location'));
    for(const prev of d.predecessors)s.append(button(`选择 ${prev.number} · ${prev.title}`,()=>navigate({mode:'player',anchor:prev.id,context:current.context||[]}),'detail-button'));
  }
  if(playerMode&&d.kind!=='return-group'){const dev=foldout(root,'开发者信息');
    if(d.kind==='route')for(const [index,path] of d.paths.entries()){
      if(d.paths.length>1)dev.append(el('p',`答案 ${index+1}`,'source-location'));rawEffects(dev,path.effectRecords);
      for(const loop of path.loops||[]){dev.append(el('p','循环内操作','source-location'));rawEffects(dev,loop.effects);}
    }else rawEffects(dev,visibleEffects);
    if(d.originalConditions?.length){const conditions=foldout(dev,'原始分支判断');
      for(const item of d.originalConditions)conditions.append(el('p',`${item.condition}\n${item.path}:${item.line??'?'}`,'raw-effect'));}
    if(menu)for(const connection of d.connections.filter(c=>c.kind!=='MENU_CHOICE'))dev.append(
      el('p',connection.kind==='MENU_FALLTHROUGH'?'备用路径：所有选项都不显示时继续':`内部连接：${connection.title}`,'source-location'));
    if(d.hidden?.length){const seen=new Set();for(const item of d.hidden){const key=`${item.reason}|${item.path}|${item.line}`;
      if(seen.has(key))continue;seen.add(key);dev.append(el('p',`${item.reason}${item.path?` · ${item.path}:${item.line??'?'}`:''}`,'source-location'));}}
    if(d.controls.length)dev.append(el('p',`判定变量：${d.controls.join(' · ')}`,'source-location'));
    if(d.candidates.length)dev.append(el('p',`候选连接：${d.candidates.length} 条（尚未确认）`,'source-location'));
    dev.append(el('pre',[`${d.path}:${d.line??'?'}`,d.id,...d.sources,...d.folded].join('\n')));
  }else if(!playerMode){
    if(d.controls.length)section(root,'判定变量').append(el('p',d.controls.join(' · '),'detail-text'));
    const source=el('details',undefined,'provenance');source.append(el('summary','开发者 · 来源与原始标识'),el('pre',[`${d.path}:${d.line??'?'}`,d.id,...d.sources,...d.folded].join('\n')));root.append(source);
  }
  $('detail-content').replaceChildren(root);if(d.page===0)$('details').scrollTop=0;
}
function precisionName(value){return({exact:'已解析',conservative:'保守推断',partial:'部分解析',unknown:'未知',external:'外部目标'})[value]||value;}
function highlight(text,query){const root=document.createDocumentFragment(),value=String(text??'');
  const needle=query.replace(/\s+/g,' ').trim(),lower=value.toLocaleLowerCase(),match=needle.toLocaleLowerCase();
  if(!match){root.append(document.createTextNode(value));return root;}
  let cursor=0,index;
  while((index=lower.indexOf(match,cursor))>=0){root.append(document.createTextNode(value.slice(cursor,index)),el('mark',value.slice(index,index+needle.length)));
    cursor=index+needle.length;}
  root.append(document.createTextNode(value.slice(cursor)));return root;
}
function hideSearchResults(){clearTimeout(searchTimer);searchSequence++;$('search-results').hidden=true;}
async function search(query,offset=0){const token=++searchSequence,gen=generation;
  if(!query.trim()){$('search-results').hidden=true;return;}
  try{const result=await request('search',{query,offset,player:playerMode});if(token!==searchSequence||gen!==generation)return;
    const root=document.createDocumentFragment();root.append(el('div',`${result.total} 个结果${result.total>50?' · 每页 50 条':''}`,'result-heading'));
    for(const match of result.results){const b=button('',async()=>{
      hideSearchResults();await navigate({mode:match.exactChoice?'player':fragmentMode(),anchor:match.anchor,context:[]});
      if(gen!==generation||current?.anchor!==match.anchor)return;
      renderer.select(match.anchor);showDetail('node',match.id);
    },'result');const title=el('strong');title.append(highlight(match.title,query));b.append(title);
      if(match.excerpt&&match.excerpt!==match.title){const excerpt=el('p');excerpt.append(highlight(match.excerpt,query));b.append(excerpt);}
      b.append(el('small',`${match.path}:${match.line??'?'}`));root.append(b);}
    if(offset||offset+50<result.total){const pager=el('div',undefined,'pager');if(offset)pager.append(button('上一页',()=>search(query,offset-50)));
      if(offset+50<result.total)pager.append(button('下一页',()=>search(query,offset+50)));root.append(pager);}
    $('search-results').replaceChildren(root);$('search-results').hidden=false;
  }catch(e){if(e.message!=='cancelled')error(e.message);}
}
$('open-file').onclick=$('welcome-open').onclick=()=>$('file-input').click();
$('file-input').onchange=e=>{const file=e.target.files[0];e.target.value='';openFile(file);};
$('cancel-import').onclick=()=>{stopWorker();clearGraph();$('loading').hidden=true;$('welcome').hidden=false;$('file-name').textContent='离线浏览器';announce('已取消导入');};
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchSequence++;const query=$('search').value;
  if(!query.trim())$('search-results').hidden=true;else searchTimer=setTimeout(()=>search(query),100);});
const reopenSearch=()=>{if($('search-results').hidden&&$('search').value.trim()&&worker)search($('search').value);};
$('search').addEventListener('focus',reopenSearch);
$('search').addEventListener('click',reopenSearch);
document.addEventListener('pointerdown',e=>{if(!$('search-results').contains(e.target)&&e.target!==$('search'))hideSearchResults();});
$('overview').onclick=()=>playerMode?renderer.fit():navigate({mode:'overview',anchor:current?.anchor});
$('my-position').onclick=()=>navigate({mode:'player',anchor:renderer.marker});
$('entry').onclick=()=>navigate({mode:fragmentMode(),anchor:playerMode?meta.firstChoice:meta.entries[0]?.id||meta.sections[0]?.anchor,context:[]});
$('player-mode').onclick=()=>switchMode(true);$('source-mode').onclick=()=>switchMode(false);
$('back').onclick=()=>{const old=history.pop();if(old)navigate(old.payload,{push:false,restore:old});};
$('detail-back').onclick=backDetail;
$('close-details').onclick=()=>{detailSequence++;currentDetail=null;resetDetailHistory();setPaneOpen('details',false);renderer.select(null);};
$('toggle-sidebar').onclick=()=>{document.body.classList.toggle('sidebar-collapsed');setPaneOpen('sidebar',sidebarVisible());};
$('fit').onclick=()=>renderer.fit();$('zoom-in').onclick=()=>renderer.zoomAt(1.25);$('zoom-out').onclick=()=>renderer.zoomAt(.8);
let dragDepth=0;
document.addEventListener('dragenter',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();dragDepth++;$('drop-overlay').hidden=false;}});
document.addEventListener('dragover',e=>{e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect='copy';});
document.addEventListener('dragleave',()=>{dragDepth--;if(dragDepth<=0)$('drop-overlay').hidden=true;});
document.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('drop-overlay').hidden=true;const file=e.dataTransfer.files[0];if(file)openFile(file);});
document.addEventListener('keydown',e=>{
  if(e.key==='/'&&document.activeElement.tagName!=='INPUT'&&meta){e.preventDefault();$('search').focus();}
  if(e.key==='Escape'){hideSearchResults();$('error').hidden=true;}
});
// Small diagnostic surface for the reproducible browser acceptance test. The
// underlying graph never leaves the worker and is not exposed on window.
window.storyViewer={get metrics(){return metrics;},get view(){return renderer.view;},get renderer(){return renderer;},
  get current(){return current;},get detail(){return currentDetail;},request,navigate,openFile};
