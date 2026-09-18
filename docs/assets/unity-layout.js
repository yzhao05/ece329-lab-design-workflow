"use strict";
(() => {
  const text = (tag, value, className) => {
    const node = document.createElement(tag); node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const svgNode = (tag, attrs = {}, value) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const record = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  const strings = value => Array.isArray(value) && value.every(item=>typeof item==='string');
  function validSnapshot(value) {
    return record(value) && typeof value.design_id==='string' && Number.isInteger(value.revision) && value.revision>=0
      && Array.isArray(value.nodes) && value.nodes.every(n=>record(n) && typeof n.id==='string' && typeof n.label==='string')
      && new Set(value.nodes.map(n=>n.id)).size===value.nodes.length
      && Array.isArray(value.relations) && value.relations.every(r=>record(r)
        && ['source','target','relation','evidence'].every(k=>typeof r[k]==='string'))
      && Array.isArray(value.summary) && value.summary.every(s=>record(s) && typeof s.label==='string' && strings(s.items))
      && (value.flows===undefined || Array.isArray(value.flows) && value.flows.every(f=>record(f) && typeof f.operation==='string' && typeof f.feedback==='string'))
      && (value.questions===undefined || strings(value.questions)) && (value.notes===undefined || strings(value.notes));
  }
  function accept(current, incoming, designId, mode) {
    if (mode !== 'EMVR_DIRECT' || !validSnapshot(incoming) || incoming.design_id !== designId) return null;
    if (current?.design_id === designId && current.revision > incoming.revision) return current;
    return incoming;
  }
  const directions = {left:'左侧',right:'右侧',front:'前方',back:'后方',center:'中心'};
  function drawRelation(link, nodes) {
    const source = nodes.find(n=>n.id===link.source), target = nodes.find(n=>n.id===link.target);
    if (!source || !target || source===target || !Object.hasOwn(directions,link.relation)) return null;
    const group = text('figure','', 'unity-relation');
    group.append(text('figcaption',link.evidence));
    const svg = svgNode('svg',{viewBox:'0 0 360 230',role:'img','aria-label':link.evidence});
    let a={x:245,y:110},b={x:105,y:110};
    if(link.relation==='left') [a,b]=[b,a];
    if(link.relation==='front') [a,b]=[{x:180,y:55},{x:180,y:165}];
    if(link.relation==='back') [a,b]=[{x:180,y:165},{x:180,y:55}];
    if(link.relation==='center') [a,b]=[{x:180,y:105},{x:180,y:105}];
    if(link.relation!=='center') svg.append(svgNode('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:'unity-position-line'}));
    const drawObject=(node,point,isCenter=false)=>{
      svg.append(svgNode(node.kind==='observation'||isCenter?'circle':'rect',
        node.kind==='observation'||isCenter?{cx:point.x,cy:point.y,r:7,class:'unity-observer'}:
        {x:point.x-42,y:point.y-22,width:84,height:44,rx:9,class:'unity-object'}));
      svg.append(svgNode('text',{x:point.x,y:point.y+(node===source?50:-36),'text-anchor':'middle',class:'unity-node-id'},node===source?'②':'①'));
      // Labels stay in HTML so translations wrap without SVG clipping.
    };
    drawObject(target,b);drawObject(source,a,link.relation==='center');
    const reference=text('div','', 'unity-object-labels');
    reference.append(text('span','② '+source.label), text('span',directions[link.relation]), text('span','① '+target.label));
    group.append(svg,reference);
    return group;
  }
  function render(card, snapshot, mode, onClarify) {
    if (!card) return;
    card.hidden = mode !== 'EMVR_DIRECT';
    const summary=card.querySelector('[data-unity-summary]'), drawing=card.querySelector('[data-unity-drawing]');
    const questions=card.querySelector('[data-unity-questions]');
    summary.replaceChildren();drawing.replaceChildren();questions.replaceChildren();
    delete card.dataset.revision;
    snapshot = validSnapshot(snapshot) ? snapshot : null;
    if(card.hidden) return;
    if(!snapshot) {
      summary.append(text('p','尚无当前设计的已明确内容。','empty-copy'));
      drawing.append(text('p','位置未明确，示意图留白。','unity-empty'));
      return;
    }
    for(const section of snapshot.summary) {
      if(!Array.isArray(section.items) || !section.items.length) continue;
      summary.append(text('h3',section.label));
      const list=text('ul','');section.items.forEach(value=>list.append(text('li',value)));summary.append(list);
    }
    if(!summary.childNodes.length) summary.append(text('p','尚无当前设计的已明确内容。','empty-copy'));
    for(const link of snapshot.relations) {const figure=drawRelation(link,snapshot.nodes);if(figure)drawing.append(figure);}
    const shownRelations = drawing.childNodes.length;
    if(!shownRelations) drawing.append(text('p',snapshot.notes?.length ? '暂无可可靠绘制的位置关系。' : '位置未明确，示意图留白。','unity-empty'));
    for (const note of snapshot.notes || []) drawing.append(text('p',note,'unity-view-note'));
    for(const flow of snapshot.flows || []) {
      const row=text('div','', 'unity-flow');
      row.append(text('span',flow.operation),text('span','→'),text('span',flow.feedback));drawing.append(row);
    }
    if(shownRelations) drawing.prepend(text('p','俯视图 · 各图仅表示已明确的局部关系；图标代表组件，不指定朝向或完整房间布局。','unity-view-note'));
    const pending=snapshot.questions || [];
    if(pending.length) {
      questions.append(text('h3','待明确'));
      const list=text('ul','');pending.forEach(value=>list.append(text('li',value)));questions.append(list);
      const button=text('button','补充位置说明','ghost-button');button.type='button';
      button.addEventListener('click',()=>onClarify?.(pending[0]));questions.append(button);
    }
    card.dataset.revision=String(snapshot.revision);
  }
  window.ECE329UnityLayout={accept,render};
})();
