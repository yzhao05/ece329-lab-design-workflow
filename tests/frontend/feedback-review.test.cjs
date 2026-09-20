"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.listeners={}; this.value=''; this.disabled=false; this.dataset={}; }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(name, handler) { this.listeners[name]=handler; }
  async fire(name) { return this.listeners[name]?.({preventDefault(){}}); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  focus() { this.focused=true; }
  querySelectorAll(tag) { return this.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...child.querySelectorAll(tag)]); }
}
const flush = () => new Promise(resolve=>setImmediate(resolve));
function harness({filter='candidate', status='candidate', getResponse}={}) {
  const els={}, calls=[];
  let finish;
  const content={summary:'原经验摘要',trigger:'原适用条件',recommendation:'原处理建议',verification:'原验证方法'};
  const item={id:'a'.repeat(32),version:1,status,content,evidence:{scope:'global'},reviews:[]};
  const context={window:{ECE329_CONFIG:{API_BASE_URL:'http://local.test'},addEventListener(){}},
    document:{getElementById:id=>els[id]??=new Element('div'),createElement:tag=>new Element(tag)},
    AbortController,setTimeout,clearTimeout,
    fetch:async (url,options)=>{
      calls.push({url,options});
      if(options.method==='POST') return new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({status:'active',version:2})});});
      return {ok:true,json:async()=>getResponse ? getResponse(url) : {experiences:[item]}};
    }};
  vm.createContext(context);
  for(const file of ['usage-ui','feedback-review']) vm.runInContext(fs.readFileSync(path.resolve(__dirname,`../../docs/assets/${file}.js`),'utf8'),context);
  els.reviewToken.value='test-only';
  els.reviewFilter.value=filter;
  const find=id=>els.reviewCards.querySelectorAll('textarea').concat(els.reviewCards.querySelectorAll('select')).find(el=>el.id.startsWith(id));
  return {els,calls,find,finish:()=>finish(),button:()=>els.reviewCards.querySelectorAll('button').find(el=>el.textContent==='启用经验')};
}

test('usage button uses the maintainer route, pagination and logout isolation',async()=>{
  const usage={run_count:1,call_count:2,input_tokens:2000,output_tokens:200,estimated_cost_usd:0.005,active_ms:61000};
  const h=harness({getResponse:url=>url.includes('/usage?')?{total:51,durable:true,designs:Array.from({length:50},(_,i)=>({design_id:`design-${i}`,created:1,mode:'EMVR_DIRECT',complete:1,usage:{...usage,dialogue:usage,feedback:usage}}))}:{experiences:[]}});
  await h.els.reviewUsage.fire('click');await flush();
  assert.ok(h.calls[0].url.endsWith('/v1/feedback/usage?offset=0'));
  assert.equal(h.calls[0].options.headers['X-ECE329-Feedback-Admin-Token'],'test-only');
  assert.equal(h.els.reviewNext.disabled,false);
  const text=h.els.reviewCards.querySelectorAll('p').map(el=>el.textContent).join(' ');
  assert.match(text,/2000/);assert.match(text,/0.00500000/);assert.match(text,/1分 1秒/);
  await h.els.reviewNext.fire('click');await flush();
  assert.ok(h.calls[1].url.endsWith('/usage?offset=50'));
  await h.els.reviewLogout.fire('click');
  assert.equal(h.els.reviewCards.children.length,0);
});

test('manual notes start blank and never overwrite the JSON; all field edits are submitted',async()=>{
  const h=harness(); await h.els.reviewLogin.fire('submit'); await flush();
  assert.equal(h.find('original-').value,'');
  assert.notEqual(h.find('original-').readOnly,true);
  assert.equal(h.find('corrected-').value,'');
  assert.equal(h.els.reviewCards.querySelectorAll('select').some(e=>e.id?.startsWith('correction-field-')),false);
  const before=h.find('content-').value;
  h.find('original-').value='summary 和 trigger 中的不当片段';
  h.find('corrected-').value='人工修改说明';
  await h.find('corrected-').fire('change');
  assert.equal(h.find('content-').value,before);
  const edited={...JSON.parse(before),summary:'人工修正摘要',trigger:'人工修正适用条件'};
  h.find('content-').value=JSON.stringify(edited); h.find('note-').value='修改后采用';
  const saving=h.button().fire('click'); await flush();
  const body=JSON.parse(h.calls.find(call=>call.options.method==='POST').options.body);
  assert.deepEqual(body.content,edited);
  assert.deepEqual(body.note,{original:'summary 和 trigger 中的不当片段',corrected:'人工修改说明',opinion:'修改后采用'});
  await h.button().fire('click');
  assert.equal(h.calls.filter(call=>call.options.method==='POST').length,1);
  h.finish(); await saving;
});

for(const opinion of ['批准','没问题继续','Looks fine']) test(`blank corrections allow short natural opinion: ${opinion}`,async()=>{
  const h=harness();await h.els.reviewLogin.fire('submit');await flush();
  h.find('note-').value=opinion;
  const saving=h.button().fire('click');await flush();
  const body=JSON.parse(h.calls.find(c=>c.options.method==='POST').options.body);
  assert.deepEqual(body.note,{original:'',corrected:'',opinion});
  assert.equal(body.decision,'approve');
  h.finish();await saving;
});

test('partial correction and empty opinion block submission without discarding drafts',async()=>{
  const h=harness();await h.els.reviewLogin.fire('submit');await flush();
  h.find('original-').value='原文';h.find('note-').value='批准';
  await h.button().fire('click');assert.match(h.els.reviewStatus.textContent,/成对填写/);
  h.find('corrected-').value='修正';h.find('note-').value='  ';
  await h.button().fire('click');assert.match(h.els.reviewStatus.textContent,/请填写处理意见/);
  assert.equal(h.find('original-').value,'原文');
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
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

test('feedback evidence opens and loads by default without duplicate toggle requests',async()=>{
  const id='b'.repeat(32),experienceId='d'.repeat(32);
  const h=harness({filter:'feedback:all',getResponse:url=>url.endsWith('/'+id)
    ? {evidence:{extraction_candidate:{summary:'证据不足，未生成规则'}}}
    : url.includes('/experiences?') ? {experiences:[]} :
      {feedback:[{id,design_id:'design-a',message:'反馈',attempts:1,status:'duplicate',experience:{id:experienceId,status:'active'}}],
        total:1,filtered_total:1,counts:{duplicate:1},durable:true}});
  await h.els.reviewLogin.fire('submit');await flush();
  const details=h.els.reviewCards.querySelectorAll('details')[0];
  assert.equal(details.open,true);
  assert.equal(details.querySelectorAll('pre').length,1);
  assert.equal(h.calls.length,2);await details.fire('toggle');
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
  assert.equal(h.calls.filter(c=>c.options.method!=='POST' && c.url.includes('?')).length,2);
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
  assert.equal(h.calls.length,3);
  assert.equal(h.els.reviewStatus.textContent,'令牌已清除。');
});

for(const invalid of ['{invalid','null','[]']) test(`invalid JSON blocks approval and retains manual notes: ${invalid}`,async()=>{
  const h=harness(); await h.els.reviewLogin.fire('submit'); await flush();
  h.find('content-').value=invalid; h.find('original-').value='原文';
  h.find('corrected-').value='应保留的修正';h.find('note-').value='批准';
  await h.button().fire('click');
  assert.equal(h.find('corrected-').value,'应保留的修正');
  assert.match(h.els.reviewStatus.textContent,/格式有误/);
  assert.equal(h.calls.filter(call=>call.options.method==='POST').length,0);
});

test('logout while saving does not display a late success or reload private evidence',async()=>{
  const h=harness(); await h.els.reviewLogin.fire('submit'); await flush();
  h.find('original-').value='原文'; h.find('corrected-').value='修正后的内容'; h.find('note-').value='修改后采用这个规则';
  const saving=h.button().fire('click'); await flush();
  await h.els.reviewLogout.fire('click'); h.finish(); await saving;
  assert.equal(h.els.reviewCards.children.length,0);
  assert.equal(h.els.reviewToken.value,'');
  assert.equal(h.els.reviewStatus.textContent,'令牌已清除。');
  assert.equal(h.calls.length,2);
});

for(const [status, labels] of [
  ['candidate',['启用经验','停止经验']], ['active',['停止经验']], ['stopped',['重新启用']],
  ['rejected',['重新启用']], ['disabled',['重新启用']], ['deleted',['重新启用']]
]) test(`experience state ${status} exposes only the unified lifecycle actions`,async()=>{
  const h=harness({status});await h.els.reviewLogin.fire('submit');await flush();
  const buttons=h.els.reviewCards.querySelectorAll('button');
  assert.deepEqual(buttons.map(e=>e.textContent),labels);
  assert.equal(h.find('content-').disabled,status==='active');
  assert.equal(h.find('scope-').disabled,status==='active');
  if (status==='active') {
    assert.match(h.find('note-').placeholder,/停止时请填写具体原因/);
    await buttons[0].fire('click');
    assert.equal(h.els.reviewStatus.textContent,'请填写停止原因。');
    assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
  }
  h.find('note-').value='暂时停用，等待进一步核对';
  const button=buttons.find(b=>b.textContent==='停止经验') || buttons[0];
  const saving=button.fire('click');await flush();
  const body=JSON.parse(h.calls.find(c=>c.options.method==='POST').options.body);
  assert.equal(body.decision,labels.includes('停止经验')?'stop':'approve');
  if(body.decision==='stop') {assert.equal(body.content,undefined);assert.equal(body.scope,undefined);}
  h.finish();await saving;
});

test('feedback outcome and related experience lifecycle are shown separately',async()=>{
  const h=harness({filter:'feedback:all',getResponse:()=>({feedback:[{
    id:'b'.repeat(32),status:'candidate',message:'反馈',attempts:1,
    experience:{id:'c'.repeat(32),status:'stopped'}
  }],counts:{candidate:1},total:1,filtered_total:1,durable:true})});
  await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.els.reviewCards.querySelectorAll('h2')[0].textContent,'分析完成，已生成经验');
  assert.ok(h.els.reviewCards.querySelectorAll('p').some(e=>e.textContent==='关联经验：已停止'));
});
