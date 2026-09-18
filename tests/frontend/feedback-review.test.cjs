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
function harness({filter='candidate', getResponse}={}) {
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
      return {ok:true,json:async()=>getResponse ? getResponse(url) : {experiences:[item]}};
    }};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../docs/assets/feedback-review.js'),'utf8'),context);
  els.reviewToken.value='test-only';
  els.reviewFilter.value=filter;
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

test('all feedback shows both submissions even when no experiences were extracted',async()=>{
  const feedback=[{id:'b'.repeat(32),design_id:'design-a',message:'第一条反馈',attempts:1,status:'no_learning'},
    {id:'c'.repeat(32),design_id:'design-a',message:'第二条反馈',attempts:2,max_attempts:10,status:'failed',error:'分析未完成',can_retry:true}];
  const h=harness({filter:'feedback:all',getResponse:()=>({feedback,total:2,filtered_total:2,counts:{no_learning:1,failed:1},durable:true})});
  await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.els.reviewCards.children.length,2);
  assert.match(h.els.reviewStatus.textContent,/反馈总数：2/);
  assert.ok(h.els.reviewCards.querySelectorAll('p').some(item=>item.textContent.endsWith('2/10')));
  assert.ok(h.calls[0].url.includes('/v1/feedback/tickets?'));
  assert.equal(h.els.reviewCards.querySelectorAll('textarea').length,0);
  assert.equal(h.els.reviewCards.querySelectorAll('button')[0].textContent,'重试分析');
  assert.equal(h.els.reviewNext.disabled,true);
});

test('feedback evidence loads on demand and linked experience uses an exact ID',async()=>{
  const id='b'.repeat(32),experienceId='d'.repeat(32);
  const h=harness({filter:'feedback:all',getResponse:url=>url.endsWith('/'+id)
    ? {evidence:{extraction_candidate:{summary:'证据不足，未生成规则'}}}
    : url.includes('/experiences?') ? {experiences:[]} :
      {feedback:[{id,design_id:'design-a',message:'反馈',attempts:1,status:'duplicate',experience:{id:experienceId,status:'active'}}],
        total:1,filtered_total:1,counts:{duplicate:1},durable:true}});
  await h.els.reviewLogin.fire('submit');await flush();
  const details=h.els.reviewCards.querySelectorAll('details')[0];
  assert.equal(h.calls.length,1); details.open=true;await details.fire('toggle');
  assert.equal(h.calls.length,2);await details.fire('toggle');assert.equal(h.calls.length,2);
  await h.els.reviewCards.querySelectorAll('button')[0].fire('click');await flush();
  assert.ok(h.calls[2].url.endsWith('?experience_id='+experienceId));
});

test('an empty experience filter explains that feedback may still exist',async()=>{
  const h=harness({getResponse:()=>({experiences:[]})});
  await h.els.reviewLogin.fire('submit');await flush();
  assert.match(h.els.reviewCards.children[0].textContent,/不代表没有收到反馈/);
});

test('maintainer retry double click sends one request and refreshes without a design turn',async()=>{
  const id='b'.repeat(32);
  const h=harness({filter:'feedback:failed',getResponse:()=>({feedback:[{
    id,design_id:'design-a',message:'retry',attempts:2,max_attempts:10,status:'failed',can_retry:true
  }],total:1,filtered_total:1,counts:{failed:1},durable:true})});
  await h.els.reviewLogin.fire('submit');await flush();
  const retry=h.els.reviewCards.querySelectorAll('button')[0];
  const saving=retry.fire('click');await flush();await retry.fire('click');
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);
  assert.ok(h.calls.at(-1).url.endsWith(`/v1/feedback/tickets/${id}/retry`));
  h.finish();await saving;
  assert.equal(h.calls.filter(c=>c.options.method!=='POST').length,2);
  assert.ok(h.calls.every(c=>!c.url.includes('/v1/designs/')));
});

test('logging out during maintainer retry prevents late data from reappearing',async()=>{
  const h=harness({filter:'feedback:failed',getResponse:()=>({feedback:[{
    id:'b'.repeat(32),design_id:'design-a',message:'retry',attempts:2,max_attempts:10,status:'failed',can_retry:true
  }],total:1,filtered_total:1,counts:{failed:1},durable:true})});
  await h.els.reviewLogin.fire('submit');await flush();
  const saving=h.els.reviewCards.querySelectorAll('button')[0].fire('click');await flush();
  await h.els.reviewLogout.fire('click');h.finish();await saving;
  assert.equal(h.els.reviewCards.children.length,0);
  assert.equal(h.calls.length,2);
  assert.equal(h.els.reviewStatus.textContent,'令牌已清除。');
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
