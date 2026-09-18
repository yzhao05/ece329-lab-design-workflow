"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.listeners={}; this.value=''; this.disabled=false; }
  addEventListener(name, handler) { this.listeners[name]=handler; }
  async fire(name) { return this.listeners[name]?.({preventDefault(){}}); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  focus() { this.focused=true; }
  querySelectorAll(tag) { return this.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...child.querySelectorAll(tag)]); }
}
const flush = () => new Promise(resolve=>setImmediate(resolve));
function harness() {
  const els={}, calls=[];
  let finish;
  const content={summary:'原经验摘要',trigger:'原适用条件',recommendation:'原处理建议',verification:'原验证方法'};
  const item={id:'a'.repeat(32),version:1,status:'candidate',content,evidence:{scope:'global'},reviews:[]};
  const context={window:{ECE329_CONFIG:{API_BASE_URL:'http://local.test'}},
    document:{getElementById:id=>els[id]??=new Element('div'),createElement:tag=>new Element(tag)},
    AbortController,setTimeout,clearTimeout,
    fetch:async (url,options)=>{
      calls.push({url,options});
      if(options.method==='POST') return new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({status:'active',version:2})});});
      return {ok:true,json:async()=>({experiences:[item]})};
    }};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../docs/assets/feedback-review.js'),'utf8'),context);
  els.reviewToken.value='test-only';
  const find=id=>els.reviewCards.querySelectorAll('textarea').concat(els.reviewCards.querySelectorAll('select')).find(el=>el.id.startsWith(id));
  return {els,calls,find,finish:()=>finish(),button:()=>els.reviewCards.querySelectorAll('button').find(el=>el.textContent==='启用经验')};
}

test('switching correction fields preserves every edit in the submitted rule and structured opinion',async()=>{
  const h=harness(); await h.els.reviewLogin.fire('submit'); await flush();
  const field=h.find('correction-field-'), corrected=h.find('corrected-');
  corrected.value='人工修正摘要'; field.value='trigger'; await field.fire('change');
  corrected.value='人工修正适用条件'; field.value='recommendation'; await field.fire('change');
  corrected.value='人工修正处理建议'; h.find('note-').value='修改后采用，修正错误归因。';
  const saving=h.button().fire('click'); await flush();
  const body=JSON.parse(h.calls.find(call=>call.options.method==='POST').options.body);
  assert.equal(body.content.summary,'人工修正摘要');
  assert.equal(body.content.trigger,'人工修正适用条件');
  assert.equal(body.note.field,'recommendation');
  assert.equal(body.note.corrected,'人工修正处理建议');
  assert.equal(body.note.opinion,'修改后采用，修正错误归因。');
  assert.equal(body.note.basis,undefined);
  await h.button().fire('click');
  assert.equal(h.calls.filter(call=>call.options.method==='POST').length,1);
  h.finish(); await saving;
});

test('invalid JSON blocks a field switch without losing correction text',async()=>{
  const h=harness(); await h.els.reviewLogin.fire('submit'); await flush();
  const field=h.find('correction-field-');
  h.find('content-').value='{invalid'; h.find('corrected-').value='应保留的修正';
  field.value='trigger'; await field.fire('change');
  assert.equal(field.value,'summary');
  assert.equal(h.find('corrected-').value,'应保留的修正');
  assert.match(h.els.reviewStatus.textContent,/格式有误/);
  assert.equal(h.calls.filter(call=>call.options.method==='POST').length,0);
});

test('logout while saving does not display a late success or reload private evidence',async()=>{
  const h=harness(); await h.els.reviewLogin.fire('submit'); await flush();
  h.find('corrected-').value='修正后的内容'; h.find('note-').value='修改后采用这个规则';
  const saving=h.button().fire('click'); await flush();
  await h.els.reviewLogout.fire('click'); h.finish(); await saving;
  assert.equal(h.els.reviewCards.children.length,0);
  assert.equal(h.els.reviewToken.value,'');
  assert.equal(h.els.reviewStatus.textContent,'令牌已清除。');
  assert.equal(h.calls.length,2);
});
