"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
class Element {
  constructor(tag){this.tag=tag;this.childNodes=[];this.attrs={};this.dataset={};this.listeners={};}
  append(...nodes){this.childNodes.push(...nodes);} prepend(...nodes){this.childNodes.unshift(...nodes);}
  replaceChildren(...nodes){this.childNodes=nodes;} setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.listeners[k]=v;} querySelector(k){return this.sections[k];}
}
function harness(){
  const context={window:{},document:{createElement:t=>new Element(t),createElementNS:(ns,t)=>new Element(t)}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../docs/assets/unity-layout.js'),'utf8'),context);
  const card=new Element('section');card.sections=Object.fromEntries(['summary','drawing','questions'].map(k=>['[data-unity-'+k+']',new Element('div')]));
  return {...context.window.ECE329UnityLayout,card};
}
const scene=()=>({design_id:'test',revision:2,summary:[{label:'实验对象',items:['电流环','电流滑块']}],nodes:[{id:'a',label:'电流环'},{id:'b',label:'电流滑块'}],relations:[{source:'b',target:'a',relation:'right',evidence:'电流滑块位于电流环右侧'}],questions:[]});
function descendants(node){return [node,...node.childNodes.flatMap(descendants)];}
test('guided hides the entire card and removes a previous EMVR diagram',()=>{
 const h=harness();h.render(h.card,scene(),'EMVR_DIRECT');assert.equal(h.card.hidden,false);
 assert.ok(descendants(h.card.sections['[data-unity-drawing]']).some(n=>n.tag==='svg'));
 h.render(h.card,scene(),'GUIDED_DESIGN');assert.equal(h.card.hidden,true);
 assert.equal(h.card.sections['[data-unity-drawing]'].childNodes.length,0);
});
test('unknown locations stay blank and ask without sending or deciding anything',()=>{
 const h=harness(),s={...scene(),relations:[],questions:['请明确电流滑块的位置。']};let question;
 h.render(h.card,s,'EMVR_DIRECT',q=>question=q);
 assert.equal(descendants(h.card.sections['[data-unity-drawing]']).some(n=>n.tag==='svg'),false);
 const button=descendants(h.card.sections['[data-unity-questions]']).find(n=>n.tag==='button');
 assert.equal(question,undefined);button.listeners.click();assert.equal(question,s.questions[0]);
});
test('clears, new designs, missing snapshots and late replies never merge stale nodes',()=>{
 const h=harness(),old=scene();assert.equal(h.accept(old,null,'test','EMVR_DIRECT'),null);
 assert.equal(h.accept(old,old,'other','EMVR_DIRECT'),null);
 assert.equal(h.accept(old,old,'test','GUIDED_DESIGN'),null);
 assert.equal(h.accept(old,{...old,revision:1},'test','EMVR_DIRECT'),old);
 const cleared={...old,revision:3,nodes:[],relations:[]};
 assert.equal(h.accept(old,cleared,'test','EMVR_DIRECT'),cleared);
 h.render(h.card,old,'EMVR_DIRECT');h.render(h.card,cleared,'EMVR_DIRECT');
 assert.equal(descendants(h.card.sections['[data-unity-drawing]']).some(n=>n.tag==='svg'),false);
});
test('invalid references are ignored and text is rendered as text, not markup',()=>{
 const h=harness(),s=scene();s.summary[0].items=['<img src=x onerror=alert(1)>'];s.relations[0].target='missing';
 h.render(h.card,s,'EMVR_DIRECT');
 assert.ok(descendants(h.card.sections['[data-unity-summary]']).some(n=>n.textContent==='<img src=x onerror=alert(1)>'));
 assert.equal(descendants(h.card.sections['[data-unity-drawing]']).some(n=>n.tag==='svg'),false);
});

for(const patch of [{nodes:[null]},{nodes:[{id:'a',label:'A'},{id:'a',label:'B'}]},
  {relations:[null]},{summary:[null]},{summary:[{label:'Objects',items:[null]}]},
  {flows:[null]},{questions:'bad'},{notes:[null]},{revision:-1}]) test('malformed diagram cannot crash the chat: '+JSON.stringify(patch),()=>{
 const h=harness(),bad={...scene(),...patch};
 assert.equal(h.accept(null,bad,'test','EMVR_DIRECT'),null);
 h.render(h.card,scene(),'EMVR_DIRECT');
 assert.doesNotThrow(()=>h.render(h.card,bad,'EMVR_DIRECT'));
 assert.equal(h.card.dataset.revision,undefined);
 assert.equal(descendants(h.card.sections['[data-unity-drawing]']).some(n=>n.tag==='svg'),false);
});
test('unrenderable written positions display a note instead of repeatedly asking for them',()=>{
 const h=harness(),s={...scene(),relations:[],notes:['已有位置说明未全部绘出，请以文字为准；无需重复确认已说明的位置。'],questions:[]};
 h.render(h.card,s,'EMVR_DIRECT');
 assert.equal(h.card.sections['[data-unity-questions]'].childNodes.length,0);
 assert.ok(descendants(h.card.sections['[data-unity-drawing]']).some(n=>n.textContent===s.notes[0]));
});
