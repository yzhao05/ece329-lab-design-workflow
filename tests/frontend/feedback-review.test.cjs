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
  const els={}, calls=[], alerts=[];
  let finish;
  const content={summary:'原经验摘要',trigger:'原适用条件',recommendation:'原处理建议',verification:'原验证方法'};
  const item={id:'a'.repeat(32),version:1,status,content,evidence:{scope:'global'},reviews:[]};
  const context={window:{ECE329_CONFIG:{API_BASE_URL:'http://local.test'},alert:message=>alerts.push(message),listeners:{},addEventListener(name,fn){const old=this.listeners[name];this.listeners[name]=()=>{old?.();fn();};}},
    document:{getElementById:id=>els[id]??=new Element('div'),createElement:tag=>new Element(tag)},
    AbortController,setTimeout,clearTimeout,
    fetch:async (url,options)=>{
      calls.push({url,options});
      if(options.method==='POST') return new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({status:'active',version:2})});});
      return {ok:true,json:async()=>getResponse ? getResponse(url) : {experiences:[item]}};
    }};
  context.document.querySelectorAll=selector=>Object.values(els).flatMap(el=>el.querySelectorAll('div')).filter(el=>selector==='.review-evidence' && el.className==='review-evidence');
  vm.createContext(context);
  for(const file of ['usage-ui','feedback-diagnostics','review-evidence','feedback-review']) vm.runInContext(fs.readFileSync(path.resolve(__dirname,`../../docs/assets/${file}.js`),'utf8'),context);
  els.reviewToken.value='test-only';
  els.reviewFilter.value=filter;
  const find=id=>els.reviewCards.querySelectorAll('textarea').concat(els.reviewCards.querySelectorAll('select')).find(el=>el.id.startsWith(id));
  return {els,calls,alerts,context,item,find,finish:()=>finish(),button:()=>els.reviewCards.querySelectorAll('button').find(el=>el.textContent==='启用经验')};
}

for (const token of ['本密码', 'épassword', 'a'.repeat(1025), 'abc\nxyz', 'abc\u0000xyz', '   ']) {
  test(`malformed maintainer password is rejected locally (${JSON.stringify(token.slice(0,12))})`,async()=>{
    const h=harness();h.els.reviewToken.value=token;
    await h.els.reviewLogin.fire('submit');await flush();
    assert.deepEqual(h.alerts,['密码错误']);
    assert.equal(h.calls.length,0);
    assert.equal(h.els.reviewStatus.textContent,'');
    assert.equal(h.els.reviewToken.focused,true);
    assert.equal(h.els.reviewToken.value,token);
    h.els.reviewToken.value='correct-token';
    await h.els.reviewLogin.fire('submit');await flush();
    assert.equal(h.calls.length,1);
    assert.equal(h.els.reviewCards.children.length,1);
  });
}

for (const status of [401,403,431]) test(`HTTP ${status} shows a simple password popup even with a non-JSON body`,async()=>{
  const h=harness();h.context.fetch=async()=>({status,ok:false,json:async()=>{throw Error('Unexpected HTML');}});
  h.context.window.ECE329I18n={language:'en'};
  await h.els.reviewUsage.fire('click');await flush();
  assert.deepEqual(h.alerts,['Incorrect password']);
  assert.equal(h.els.reviewStatus.textContent,'');
});

test('network failures are not reported as incorrect passwords',async()=>{
  const h=harness();h.context.fetch=async()=>{throw new TypeError('Failed to fetch');};
  await h.els.reviewLogin.fire('submit');await flush();
  assert.deepEqual(h.alerts,[]);
  assert.match(h.els.reviewStatus.textContent,/无法连接课程服务/);
  assert.doesNotMatch(h.els.reviewStatus.textContent,/TypeError|Failed to fetch|密码错误/);
});

test('valid long ASCII credentials are sent intact',async()=>{
  const h=harness();const token='a'.repeat(1024);h.els.reviewToken.value=token;
  await h.els.reviewLogin.fire('submit');await flush();
  assert.deepEqual(h.alerts,[]);
  assert.equal(h.calls[0].options.headers['X-ECE329-Feedback-Admin-Token'],token);
});

test('a late authentication failure cannot interrupt an edited credential',async()=>{
  const h=harness();let finish;
  h.context.fetch=()=>new Promise(resolve=>finish=resolve);
  await h.els.reviewLogin.fire('submit');await flush();
  h.els.reviewToken.value='new-token';await h.els.reviewToken.fire('input');
  finish({status:401,ok:false});await flush();
  assert.deepEqual(h.alerts,[]);
});

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
  assert.ok(h.els.reviewCards.querySelectorAll('button').some(b=>b.textContent==='重试分析'));
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
  assert.equal(details.querySelectorAll('pre').length,0);
  const raw=rootPanels(h)[1];
  assert.equal(raw.open,false);assert.equal(raw.querySelectorAll('pre').length,1);
  assert.equal(h.calls.length,2);await details.fire('toggle');
  assert.equal(h.calls.length,2);await details.fire('toggle');assert.equal(h.calls.length,2);
  await h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='查看关联经验').fire('click');await flush();
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
  const retry=h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='重试分析');
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
  const saving=h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='重试分析').fire('click');await flush();
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
  ['candidate',['启用经验','停止经验']], ['active',['批准修订并启用','停止经验']], ['stopped',['重新启用']],
  ['rejected',['重新启用']], ['disabled',['重新启用']], ['deleted',['重新启用']]
]) test(`experience state ${status} exposes only the unified lifecycle actions`,async()=>{
  const h=harness({status});await h.els.reviewLogin.fire('submit');await flush();
  const buttons=h.els.reviewCards.querySelectorAll('button').filter(b=>['启用经验','停止经验','重新启用','批准修订并启用'].includes(b.textContent));
  assert.deepEqual(buttons.map(e=>e.textContent),labels);
  assert.equal(h.find('content-').disabled,false);
  assert.equal(h.find('scope-').disabled,false);
  if (status==='active') {
    assert.match(h.find('note-').placeholder,/停止时请填写具体原因/);
    await buttons.find(b=>b.textContent==='停止经验').fire('click');
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


test('folds, refresh, language changes and filters preserve isolated review drafts',async()=>{
  const h=harness();await h.els.reviewLogin.fire('submit');await flush();
  h.find('content-').value='{"summary":"manual draft"}';h.find('note-').value='人工意见';h.find('original-').value='原文';h.find('corrected-').value='修正';
  const [read,raw]=rootPanels(h);
  await read.querySelectorAll('button')[0].fire('click');assert.equal(read.open,false);assert.equal(raw.open,false);
  await raw.querySelectorAll('button')[0].fire('click');assert.equal(raw.open,true);assert.equal(read.open,false);
  h.context.window.ECE329I18n={language:'en'};h.context.window.listeners['ece329:language-changed']();
  assert.equal(h.find('note-').value,'人工意见');
  const [readEn,rawEn]=rootPanels(h);assert.equal(readEn.open,false);assert.equal(rawEn.open,true);
  await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.find('content-').value,'{"summary":"manual draft"}');assert.equal(h.find('note-').value,'人工意见');
  const firstId=h.item.id;h.item.id='b'.repeat(32);
  await h.els.reviewFilter.fire('change');await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.find('note-').value,'');
  h.item.id=firstId;await h.els.reviewLogin.fire('submit');await flush();assert.equal(h.find('note-').value,'人工意见');
  await h.els.reviewLogout.fire('click');h.els.reviewToken.value='new-user';await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.find('note-').value,'');
});

test('refreshing a newer version keeps drafts but requires explicit version acknowledgement',async()=>{
  const h=harness();await h.els.reviewLogin.fire('submit');await flush();h.find('note-').value='批准';
  h.item.version=2;await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.find('note-').value,'批准');await h.button().fire('click');
  assert.match(h.els.reviewStatus.textContent,/核对新证据/);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
  await h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='已核对新版本，继续使用草稿').fire('click');
  const saving=h.button().fire('click');await flush();assert.equal(JSON.parse(h.calls.at(-1).options.body).version,2);h.finish();await saving;
});


const rootPanels=h=>h.els.reviewCards.querySelectorAll('div').find(el=>el.className==='review-evidence').children.filter(el=>el.tag==='details');
const treeText=el=>[el.textContent,...el.children.map(treeText)].join(' ');
const visibleText=el=>el.tag==='details'&&!el.open?treeText(el.children[0]):[el.textContent,...el.children.map(visibleText)].join(' ');
const eventPanel=h=>rootPanels(h)[0].querySelectorAll('section').find(el=>el.className.includes('evidence-event-summary'));
function confirmationFixture() {
  const data=evidenceFixture();
  const action={action_id:'action_9_old',type:'CONFIRM_STAGE_OR_MODIFY',stage:'IDEA_BRAINSTORMING',
    status:'PENDING',question:'请核对实验设计边界的完整原文。',proposal:{items:[{field:'observations',value:'电磁波的传播速度与波长'}]},
    answer_fields:['research_object','observations'],allowed_intents:['ACCEPT_PREVIOUS_PROPOSAL'],
    editable_field_bindings:[{canonical_field:'observations',visible_labels:['观察量']}],created_at_revision:9};
  const confirmation={topic_lock:{locked:false,preserved_fields:[]},latest_field_provenance:{}};
  const before={revision:9,stage:'IDEA_BRAINSTORMING',completed_stages:[],pending_excerpt:JSON.stringify(action),confirmation_excerpt:JSON.stringify(confirmation)};
  const after={...before,revision:10,pending_excerpt:JSON.stringify({...action,action_id:'action_10_new',created_at_revision:10}),
    confirmation_excerpt:JSON.stringify({...confirmation,topic_lock:{...confirmation.topic_lock,preserved_fields:['IDEA_BRAINSTORMING']}}),completion_error:'请先结合当前VR实验回答这一问。'};
  data.evidence.event_chain[0].assistant=action.question+'\n'+action.proposal.items[0].value;
  data.evidence.event_chain[0].recorded_fields={assistant:true};
  Object.assign(data.evidence.event_chain[1],{state_before:before,state_after:after,resolved_intent:{intent:'ACCEPT_PREVIOUS_PROPOSAL',advance_requested:true}});
  data.evidence.current_state={...after,revision:11,updated_at:'later'};
  delete data.evidence.current_state.completion_error;
  return data;
}

test('confirmation summary preserves changed action identity without repeating snapshots or dialogue',async()=>{
  const h=harness();h.item.evidence=confirmationFixture();
  await h.els.reviewLogin.fire('submit');await flush();
  const panel=eventPanel(h),text=visibleText(panel);
  for(const expected of ['9 → 10','接受前一方案','记录了推进请求','阶段、确认问题、方案内容未变化','待办标识已更换','待处理','该轮回复正文为空','未记录结构化的具体缺项'])assert.ok(text.includes(expected),expected);
  assert.doesNotMatch(text,/action_9_old|action_10_new|editable_field_bindings|allowed_intents|research_object|请核对实验设计边界的完整原文|电磁波的传播速度与波长|继续。/);
  const folds=panel.querySelectorAll('details');assert.deepEqual(folds.map(el=>el.open),[false,false]);
  assert.match(treeText(folds[0]),/action_9_old/);assert.match(treeText(folds[0]),/action_10_new/);
  assert.match(treeText(folds[0]),/confirmation.topic_lock.preserved_fields/);
  assert.match(treeText(folds[1]),/提交时状态与问题轮结束状态一致/);
  assert.doesNotMatch(treeText(folds[1]),/电磁波的传播速度与波长/);
  assert.equal(visibleText(rootPanels(h)[0]).split('请核对实验设计边界的完整原文。').length-1,1);
  assert.equal(visibleText(rootPanels(h)[0]).split('继续。').length-1,1);
});

test('structured comparison ignores object key order and runtime timestamps, not proposal values or action IDs',async()=>{
  const h=harness();h.item.evidence=confirmationFixture();
  const snapshot=h.item.evidence.evidence,original=JSON.parse(snapshot.current_state.pending_excerpt);
  snapshot.current_state.pending_excerpt=JSON.stringify(Object.fromEntries(Object.entries({...original,created_at_revision:99,updated_at:'tomorrow'}).reverse()));
  await h.els.reviewLogin.fire('submit');await flush();
  assert.match(treeText(eventPanel(h).querySelectorAll('details')[1]),/提交时状态与问题轮结束状态一致/);
  snapshot.current_state.pending_excerpt=JSON.stringify({...original,action_id:'action_11_changed',proposal:{revision:'experimental variable'}});
  await h.els.reviewLogin.fire('submit');await flush();
  const submission=treeText(eventPanel(h).querySelectorAll('details')[1]);
  assert.doesNotMatch(submission,/提交时状态与问题轮结束状态一致/);
  assert.match(submission,/pending_action.action_id/);assert.match(submission,/experimental variable/);
});

test('unknown and truncated states cannot support equality or invented missing fields',async()=>{
  const h=harness();h.item.evidence=confirmationFixture();
  const snapshot=h.item.evidence.evidence,turn=snapshot.event_chain[1];
  turn.state_before.pending_excerpt_truncated=true;turn.state_after.pending_excerpt_truncated=true;
  snapshot.current_state={...turn.state_after};delete turn.resolved_intent;
  await h.els.reviewLogin.fire('submit');await flush();
  const panel=eventPanel(h),text=visibleText(panel);
  assert.doesNotMatch(text,/确认问题、方案内容未变化|接受前一方案|记录了推进请求/);
  assert.match(text,/待办状态摘录已截断/);
  assert.doesNotMatch(treeText(panel.querySelectorAll('details')[1]),/提交时状态与问题轮结束状态一致/);
  turn.state_after.missing_fields=['observation_location'];
  await h.els.reviewLogin.fire('submit');await flush();
  assert.match(visibleText(eventPanel(h)),/检查明确记录的缺项\s+observation_location/);
  assert.doesNotMatch(visibleText(eventPanel(h)),/research_object/);
});

test('nested state folds retain language state, do not affect drafts and reset for another record',async()=>{
  const h=harness();h.item.evidence=confirmationFixture();await h.els.reviewLogin.fire('submit');await flush();
  h.find('note-').value='手写审阅草稿';
  await eventPanel(h).querySelectorAll('details')[0].querySelectorAll('button')[0].fire('click');
  h.context.window.ECE329I18n={language:'en'};h.context.window.listeners['ece329:language-changed']();
  const panel=eventPanel(h);assert.deepEqual(panel.querySelectorAll('details').map(el=>el.open),[true,false]);
  assert.match(visibleText(panel),/pending-action ID changed/);
  assert.equal(h.find('note-').value,'手写审阅草稿');
  h.item.id='d'.repeat(32);h.item.evidence={evidence:{}};
  await h.els.reviewLogin.fire('submit');await flush();
  assert.doesNotMatch(treeText(eventPanel(h)),/action_9_old|action_10_new/);
  assert.deepEqual(eventPanel(h).querySelectorAll('details').map(el=>el.open),[false,false]);
  assert.equal(h.find('note-').value,'');
});
function evidenceFixture() {
  const before={stage:'IDEA_BRAINSTORMING',pending_excerpt:JSON.stringify({type:'CONFIRM_OR_MODIFY',question:'请确认实验边界'})};
  return {message:'继续没有推进 <img src=x onerror=alert(1)>',category:'answered_pending',
    evidence:{evidence_schema_version:2,event_chain:[
      {position:'before',revision:1,user:'我的原文',recorded_fields:{assistant:false}},
      {position:'reported',revision:2,mode:'EMVR_DIRECT',stage:'IDEA_BRAINSTORMING',user:'继续。',assistant:'',recorded_fields:{assistant:true},
       warnings:['还需补充'],student_task:'明确观察位置',resolved_intent:{intent:'ACCEPT_PREVIOUS_PROPOSAL'},
       state_before:before,state_after:{...before,completion_error:'缺少观察位置'},truncated_fields:['user']}
    ],current_state:{stage:'STUDENT_SYNTHESIS_OR_EMVR_OUTPUT'}},
    extraction_analysis:{diagnosis:{facts:[{evidence_ref:'turn:2',observation:'Agent 认为阶段未前进'}],hypotheses:['可能未承接确认'],expected_behavior:'应继续推进',unknowns:['未确定根因'],applicability:'确认后',exceptions:'用户要求修改'},validation_status:'not_replayed'},
    analysis_attempts:[{attempt:3,provider:'openai',model:'gpt-5.4-mini',phase:'check',status:'failed',code:'model_output_invalid'},{attempt:4,status:'completed'}],
    attachments:[{role:'problem',data_url:'data:image/png;base64,test'}]};
}

for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT'])test(`readable analysis and conversation opt into translation without changing IDs or editors: ${mode}`,async()=>{
  const h=harness();h.item.evidence=evidenceFixture();h.item.evidence.evidence.mode=mode;
  await h.els.reviewLogin.fire('submit');await flush();
  const opted=h.els.reviewCards.querySelectorAll('p').filter(el=>Object.hasOwn(el,'data-i18n-translate')).map(el=>el.textContent);
  assert.ok(opted.includes('应继续推进'));
  assert.ok(opted.includes('Agent 认为阶段未前进'));
  assert.ok(opted.includes(h.item.content.trigger));
  for(const text of ['继续。','还需补充','明确观察位置',h.item.evidence.message])assert.ok(opted.includes(text),text);
  assert.ok(!opted.includes('turn:2'));
  assert.equal(h.find('content-').value,JSON.stringify(h.item.content,null,2));
});

test('diagnostic debug fold survives language rendering and does not leak to another record',async()=>{
  const h=harness();h.item.evidence=evidenceFixture();await h.els.reviewLogin.fire('submit');await flush();
  const attempt=()=>h.els.reviewCards.querySelectorAll('div').find(el=>el.className==='feedback-attempt');
  await attempt().querySelectorAll('button')[0].fire('click');
  h.context.window.ECE329I18n={language:'en'};h.context.window.listeners['ece329:language-changed']();
  assert.equal(attempt().querySelectorAll('details')[0].open,true);
  assert.match(treeText(attempt()),/Debug details/);
  h.item.id='e'.repeat(32);await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(attempt().querySelectorAll('details')[0].open,false);
});

test('readable evidence separates missing and empty bodies, historical states and extraction attempts',async()=>{
  const h=harness();h.item.evidence=evidenceFixture();await h.els.reviewLogin.fire('submit');await flush();
  const readable=h.els.reviewCards.querySelectorAll('details')[0],text=treeText(readable);
  for(const expected of ['回复正文未记录','该轮回复正文为空','阶段、确认问题未变化','待办标识未记录完整','总结 PDF','尚未进行回放验证','以下记录已截断','不是原对话故障的发生过程','OpenAI / gpt-5.4-mini','检查环节','未记录 / 未记录','<img src=x onerror=alert(1)>'])assert.ok(text.includes(expected),expected);
  assert.equal(readable.querySelectorAll('img').length,1);
  const source=readable.children[1];assert.equal(h.els.reviewCards.querySelectorAll('div').find(e=>e.className==='review-evidence')['data-i18n-ignore'],'');
  h.context.window.ECE329I18n={language:'en'};h.context.window.listeners['ece329:language-changed']();
  const english=treeText(h.els.reviewCards.querySelectorAll('details')[0]);
  for(const expected of ['Conversation and state','Reply body not recorded','This turn has an empty reply body','Stage, Confirmation question unchanged','继续。','可能未承接确认','No replay validation has been performed'])assert.ok(english.includes(expected),expected);
  assert.equal(h.calls.length,1); // No translation/model call.
});

test('raw JSON retains all fields and images when copied; displayed JSON omits base64 only',async()=>{
  const h=harness();h.item.evidence=evidenceFixture();h.item.reviews=[{note:'原审阅意见'}];h.item.extra={unknown_field:42};let copied;
  h.context.navigator={clipboard:{writeText:async text=>{copied=text;}}};
  await h.els.reviewLogin.fire('submit');await flush();
  const raw=rootPanels(h)[1];assert.equal(raw.open,false);
  assert.ok(!raw.querySelectorAll('pre')[0].textContent.includes('base64,test'));
  await raw.querySelectorAll('button').find(b=>b.textContent==='复制 JSON').fire('click');
  assert.deepEqual(JSON.parse(copied),h.item);
});

function captureDownloads(h) {
  const files=[],cleanup=[],revoked=[];
  h.context.Blob=Blob;
  h.context.URL={createObjectURL:blob=>{files.push({blob});return `blob:test-${files.length}`;},revokeObjectURL:url=>revoked.push(url)};
  h.context.document.body=new Element('body');
  const create=h.context.document.createElement;
  h.context.document.createElement=tag=>{
    const el=create(tag);
    if(tag==='a') {
      el.click=()=>{files.at(-1).filename=el.download;};
      el.remove=()=>{h.context.document.body.children=h.context.document.body.children.filter(x=>x!==el);};
    }
    return el;
  };
  h.context.setTimeout=(fn,ms)=>ms===1000?cleanup.push(fn):setTimeout(fn,ms);
  return {files,cleanup,revoked};
}

for(const status of ['candidate','active','stopped','rejected','disabled','deleted'])
for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT'])
test(`experience export includes original context for ${status} / ${mode} without saving drafts`,async()=>{
  const h=harness({status});const d=captureDownloads(h);
  h.item.evidence=evidenceFixture();h.item.evidence.mode=mode;
  h.item.reviews=[{version:1,note:'原始意见',content:{previous:{summary:'旧版本'}}}];
  h.item.validations=[{report:{status:'passed'}}];h.item.evaluations=[{report:{status:'not_tested'}}];
  h.item.usage={total_tokens:123};h.item.extra={unknown_field:42};
  await h.els.reviewLogin.fire('submit');await flush();
  h.find('content-').value='invalid JSON draft';h.find('note-').value='尚未提交的意见';
  const button=h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='导出经验与上下文（JSON）');
  assert.ok(button && !button.disabled);
  for(const language of ['en','zh']) {
    h.context.window.ECE329I18n={language};h.context.window.listeners['ece329:language-changed']();
    await button.fire('click');
    const file=d.files.at(-1);
    assert.equal(file.filename,`ece329-experience-${h.item.id}-v1.json`);
    assert.deepEqual(JSON.parse(await file.blob.text()),h.item);
    assert.ok(!(await file.blob.text()).includes('test-only')); // Credential is not in the export.
    assert.equal(h.find('content-').value,'invalid JSON draft');
    assert.equal(h.find('note-').value,'尚未提交的意见');
  }
  assert.equal(h.calls.length,1); // No save, model request or extra backend call.
  assert.equal(h.context.document.body.children.length,0);
  d.cleanup.forEach(fn=>fn());assert.equal(d.revoked.length,2);
});

test('record replacement and logout invalidate old export buttons',async()=>{
  const h=harness();const d=captureDownloads(h);
  await h.els.reviewLogin.fire('submit');await flush();
  const old=h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='导出经验与上下文（JSON）');
  h.item.id='b'.repeat(32);await h.els.reviewLogin.fire('submit');await flush();
  await old.fire('click');assert.equal(d.files.length,0);
  const current=h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='导出经验与上下文（JSON）');
  await current.fire('click');assert.equal(JSON.parse(await d.files[0].blob.text()).id,'b'.repeat(32));
  await h.els.reviewLogout.fire('click');await current.fire('click');assert.equal(d.files.length,1);
});

test('download failure preserves review edits and shows a recoverable message',async()=>{
  const h=harness();captureDownloads(h);
  h.context.URL.createObjectURL=()=>{throw Error('unavailable');};
  await h.els.reviewLogin.fire('submit');await flush();h.find('note-').value='保留草稿';
  await h.els.reviewCards.querySelectorAll('button').find(b=>b.textContent==='导出经验与上下文（JSON）').fire('click');
  assert.equal(h.els.reviewStatus.textContent,'导出失败，请重试。');assert.equal(h.find('note-').value,'保留草稿');
});

for(const language of ['zh','en']) test(`malformed legacy evidence does not block review or lose raw records: ${language}`,async()=>{
  const h=harness();h.context.window.ECE329I18n={language};
  h.item.evidence=evidenceFixture();
  h.item.evidence.evidence.event_chain.unshift(null,42,'legacy');
  h.item.evidence.analysis_attempts=[null,false,{attempt:2,status:'completed'}];
  let copied;h.context.navigator={clipboard:{writeText:async text=>{copied=text;}}};
  await h.els.reviewLogin.fire('submit');await flush();
  const panels=rootPanels(h);
  assert.ok(h.find('content-'));
  assert.match(treeText(panels[0]),language==='en'?/unsupported format/:/部分记录格式异常/);
  assert.match(treeText(panels[0]),/继续。/);
  await panels[1].querySelectorAll('button').find(b=>b.textContent===(language==='en'?'Copy JSON':'复制 JSON')).fire('click');
  assert.deepEqual(JSON.parse(copied),h.item);
  h.find('note-').value='保留审阅草稿';
  h.item.evidence.evidence.event_chain=[];
  h.item.evidence.evidence.reported_turn=null;
  h.item.evidence.evidence.recent_turns=[null,{user:'最近的对话',assistant:''}];
  await h.els.reviewLogin.fire('submit');await flush();
  assert.equal(h.find('note-').value,'保留审阅草稿');
  assert.match(treeText(h.els.reviewCards.querySelectorAll('details')[0]),/最近的对话/);
});

test('legacy empty excerpts are not asserted to be empty original replies and do not borrow submission state',async()=>{
  const h=harness();h.item.evidence={evidence:{reported_turn:{assistant:'',user:'continue'},current_state:{stage:'HYPOTHESIS'}}};
  await h.els.reviewLogin.fire('submit');await flush();const text=treeText(h.els.reviewCards.querySelectorAll('details')[0]);
  assert.match(text,/原始正文是否为空未记录/);assert.match(text,/截断信息和历史状态仍为未知/);assert.doesNotMatch(text,/该轮回复正文为空|阶段未变化/);
});

test('an untouched editor refreshes to the latest server content without a spurious draft',async()=>{
  const h=harness();await h.els.reviewLogin.fire('submit');await flush();h.item.version=2;h.item.content.summary='服务器更新';
  await h.els.reviewLogin.fire('submit');await flush();assert.match(h.find('content-').value,/服务器更新/);
  assert.ok(!h.els.reviewCards.querySelectorAll('button').some(b=>b.textContent==='已核对新版本，继续使用草稿'));
});


test('identical screenshots in different context roles retain all role previews',async()=>{
  const h=harness();h.item.evidence=evidenceFixture();const url=h.item.evidence.attachments[0].data_url;
  h.item.evidence.attachments=['problem','before','after'].map(role=>({role,data_url:url}));
  await h.els.reviewLogin.fire('submit');await flush();
  assert.deepEqual(h.els.reviewCards.querySelectorAll('img').map(img=>img.alt),['问题对话截图','前文截图','后文截图']);
});


test('migrated legacy evidence shows upgrade limits without fabricating missing history',async()=>{
  const h=harness();h.item.evidence={evidence:{evidence_schema_version:2,evidence_migration:{version:1,conflicting_fields_preserved:['turn:2:assistant']},reported_turn:{revision:2,assistant:'',recorded_fields:{},truncation_unknown_fields:['assistant']}}};
  await h.els.reviewLogin.fire('submit');await flush();const text=treeText(h.els.reviewCards.querySelectorAll('details')[0]);
  assert.match(text,/历史证据已自动升级/);assert.match(text,/部分历史字段不一致/);assert.match(text,/原始正文是否为空未记录/);assert.doesNotMatch(text,/该轮回复正文为空/);
});


for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) test(`migration and provider diagnostics switch both ways without translating evidence: ${mode}`,async()=>{
  const h=harness();h.item.evidence=evidenceFixture();
  h.item.evidence.evidence.event_chain[1].mode=mode;
  h.item.evidence.evidence.evidence_migration={version:1,conflicting_fields_preserved:['turn:2:assistant']};
  h.item.evidence.analysis_attempts=[{attempt:1,provider:'deepseek',model:'deepseek-flash',phase:'check',status:'failed',code:'model_connection_error'}];
  await h.els.reviewLogin.fire('submit');await flush();
  assert.match(treeText(h.els.reviewCards.querySelectorAll('details')[0]),/模型连接失败/);
  h.context.window.ECE329I18n={language:'en'};h.context.window.listeners['ece329:language-changed']();
  const english=treeText(h.els.reviewCards.querySelectorAll('details')[0]);
  for(const label of ['Historical evidence was upgraded automatically','Some historical fields conflict','Model connection failed','Checking','Reply body not recorded',mode==='GUIDED_DESIGN'?'Guided mode':'EMVR mode','继续。'])assert.ok(english.includes(label),label);
  assert.doesNotMatch(english,/历史证据已自动升级|模型连接失败|问题前后对话/);
  h.context.window.ECE329I18n.language='zh';h.context.window.listeners['ece329:language-changed']();
  assert.match(treeText(h.els.reviewCards.querySelectorAll('details')[0]),/历史证据已自动升级/);
  assert.equal(h.calls.length,1);
});

for(const action of ['logout','token','reload','language'])test(`pending display translation is cancelled and cannot return after ${action}`,async()=>{
  const h=harness();await h.els.reviewLogin.fire('submit');await flush();
  const originalFetch=h.context.fetch;let resolve,signal,count=0;
  h.context.fetch=(url,options)=>{
    if(!url.endsWith('/v1/localization'))return originalFetch(url,options);
    count++;signal=options.signal;return new Promise(done=>resolve=done);
  };
  const pending=h.context.window.requestDisplayTranslation(['分析正文'],'en');
  const rejected=assert.rejects(pending,/Review context changed/);
  if(action==='logout')await h.els.reviewLogout.fire('click');
  if(action==='token'){h.els.reviewToken.value='another-token';await h.els.reviewToken.fire('input');}
  if(action==='reload'){await h.els.reviewLogin.fire('submit');await flush();}
  if(action==='language')h.context.window.listeners['ece329:language-changed']();
  assert.equal(signal.aborted,true);
  resolve({ok:true,json:async()=>({translations:['Stale analysis']})});await rejected;
  assert.equal(count,1);
});

for(const translations of [[42],{length:1,0:'Fake array'},[''],['仍是中文'],[null],[{}]])test(`malformed display translation stops once: ${JSON.stringify(translations)}`,async()=>{
  const h=harness();let calls=0;
  h.context.fetch=async()=>{calls++;return {ok:true,json:async()=>({translations})};};
  await assert.rejects(h.context.window.requestDisplayTranslation(['分析正文'],'en'),/Incomplete translation/);
  assert.equal(calls,1);
});
