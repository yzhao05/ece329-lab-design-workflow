const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.value='';this.textContent='';this.open=false;this.hidden=false;}
  set innerHTML(_){throw Error('Unsafe HTML rendering');}
  setAttribute(k,v){this[k]=v;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  async fire(name){for(const fn of this.listeners[name]||[])await fn({preventDefault(){}});}
  all(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.all(tag)]);}
}
function setup(){
  const host=new Element('div'),listeners={},window={ECE329I18n:{language:'zh',refresh(){}},addEventListener(n,f){listeners[n]=f;}};
  const context={window,document:{createElement:t=>new Element(t),querySelectorAll:()=>[host]}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../docs/assets/experience-authoring.js'),'utf8'),context);
  const controls=Object.fromEntries(['content','original','corrected','note','scope'].map(k=>[k,new Element('textarea')]));
  controls.content.value='{"summary":"original"}';controls.note.value='改善总结';controls.scope.value='global';
  const item={id:'a'.repeat(32),version:1,reviews:[{version:2}]},status=new Element('p');
  return {host,window,controls,item,status,listeners,api:window.ECE329RuleAuthoring,
    button:text=>host.all('button').find(b=>b.textContent===text)};
}
test('draft generation cannot overwrite concurrent JSON edits or approve automatically',async()=>{
  const h=setup();let finish,calls=[];
  const state=h.api.mount(h.host,{...h,request:(url,o)=>{calls.push(url);return new Promise(r=>finish=r);},isCurrent:()=>true,getVersion:()=>1,capture(){}});
  const pending=h.button('根据审阅意见生成修订草案').fire('click');
  h.controls.content.value='{"summary":"manual edit"}';
  finish({id:'draft1',base_version:1,content:{summary:'<script>unsafe</script>'},source:{kind:'model_revision'},scope:'global'});
  await pending;
  assert.equal(h.controls.content.value,'{"summary":"manual edit"}');
  await h.button('将草案填入 JSON').fire('click');
  assert.equal(h.controls.content.value,'{"summary":"manual edit"}');
  assert.equal(h.button('确认用草案替换当前 JSON').hidden,false);
  await h.button('确认用草案替换当前 JSON').fire('click');
  assert.match(h.controls.content.value,/<script>/);
  assert.equal(calls.length,1);assert.match(calls[0],/\/draft$/);
  assert.equal(state.approval().validation_id,undefined);
});
test('validation stays bound to tested JSON and scope and draft survives record refresh',async()=>{
  const h=setup();
  const options={...h,isCurrent:()=>true,getVersion:()=>1,capture(){},request:async()=>({id:'test1',report:{status:'passed',historical:{status:'unavailable'}}})};
  const state=h.api.mount(h.host,options);
  await h.button('隔离回放当前 JSON').fire('click');
  assert.equal(state.approval().validation_id,'test1');
  const saved=state.capture();h.host.replaceChildren();
  const restored=h.api.mount(h.host,{...options,saved});
  assert.equal(restored.approval().validation_id,'test1');
  h.controls.scope.value='session';assert.equal(restored.approval().validation_id,undefined);
  h.controls.scope.value='global';h.controls.content.value='{"summary":"changed"}';
  assert.equal(restored.approval().validation_id,undefined);
});
test('late results after a record switch are ignored',async()=>{
  const h=setup();let finish,current=true;
  const state=h.api.mount(h.host,{...h,request:()=>new Promise(r=>finish=r),isCurrent:()=>current,getVersion:()=>1,capture(){}});
  const pending=h.button('根据审阅意见生成修订草案').fire('click');current=false;
  finish({id:'stale',base_version:1,content:{summary:'wrong record'}});await pending;
  assert.equal(state.capture().text,'');assert.equal(state.approval().draft_id,undefined);
});
test('applying a draft cannot silently overwrite a newly selected scope',async()=>{
  const h=setup();
  h.api.mount(h.host,{...h,isCurrent:()=>true,getVersion:()=>1,capture(){},
    request:async()=>({id:'draft',base_version:1,content:{summary:'new'},scope:'global'})});
  await h.button('根据审阅意见生成修订草案').fire('click');
  h.controls.scope.value='session';
  await h.button('将草案填入 JSON').fire('click');
  assert.equal(h.controls.scope.value,'session');
  assert.equal(h.controls.content.value,'{"summary":"original"}');
  assert.equal(h.button('确认用草案替换当前 JSON').hidden,false);
});
test('execution conclusions use fixed bilingual mappings and preserve raw evidence',()=>{
  const h=setup(),rows=[{design_id:'d',created:1,events:[{step:'verification',code:'execution_verified',rule_id:'EXP-a',version:2,raw:'<img onerror=bad>'}]}];
  h.api.execution(h.host,rows);
  assert.ok(h.host.all('p').some(n=>n.textContent.includes('执行断言通过')));
  h.window.ECE329I18n.language='en';h.listeners['ece329:language-changed']();
  assert.ok(h.host.all('p').some(n=>n.textContent.includes('Execution assertions passed')));
  assert.ok(h.host.all('pre')[0].textContent.includes('<img onerror=bad>'));
  assert.equal(h.host.all('details')[0].open,false);
});
