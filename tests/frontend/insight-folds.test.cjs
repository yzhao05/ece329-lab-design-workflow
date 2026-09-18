"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(blockStorage=false) {
  const writes=[];
  const summary={listeners:[],addEventListener(name,fn){if(name==='click')this.listeners.push(fn);}};
  const details={open:false,isConnected:true,id:'qualityTraceability',dataset:{},
    classList:{add(){}},querySelector:()=>summary};
  const panel={querySelector:()=>null,querySelectorAll(selector){return selector.startsWith('details')?[details]:[];}};
  const context={window:{},sessionStorage:{getItem(){if(blockStorage)throw Error('blocked');return null;},
    setItem(key,value){if(blockStorage)throw Error('blocked');writes.push(JSON.parse(value));}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../docs/assets/insight-folds.js'),'utf8'),context);
  const enhance=scope=>context.window.ECE329InsightFolds.enhance(panel,scope);
  const click=(prevented=false)=>{summary.listeners.forEach(fn=>fn({defaultPrevented:prevented}));if(!prevented)details.open=!details.open;};
  return {details,summary,writes,enhance,click};
}
test('native sections keep independent preferences for each design and mode',()=>{
  const h=harness();h.enhance('a:EMVR');h.click();assert.equal(h.details.open,true);
  h.enhance('b:EMVR');assert.equal(h.details.open,false);
  h.enhance('a:EMVR');assert.equal(h.details.open,true);
  h.enhance('a:GUIDED');assert.equal(h.details.open,false);
  h.enhance('a:EMVR');assert.equal(h.details.open,true);
  assert.equal(h.summary.listeners.length,1);
});
test('rendering does not save defaults or repeatedly write preference storage',()=>{
  const h=harness();
  for(let i=0;i<100;i++)h.enhance(i%2?'a:EMVR':'b:EMVR');
  assert.equal(h.writes.length,0);assert.equal(h.summary.listeners.length,1);
  h.click();assert.equal(h.writes.length,1);
  for(let i=0;i<100;i++)h.enhance('a:EMVR');
  assert.equal(h.writes.length,1);
});
test('cancelled and detached activation cannot overwrite live choices',()=>{
  const h=harness();h.enhance('a');h.click(true);assert.equal(h.writes.length,0);
  h.details.isConnected=false;h.click();assert.equal(h.writes.length,0);
});
test('blocked browser storage retains in-memory choices without interrupting rendering',()=>{
  const h=harness(true);h.enhance('a');h.click();h.enhance('b');h.enhance('a');
  assert.equal(h.details.open,true);
});
