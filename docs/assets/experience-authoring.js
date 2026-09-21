"use strict";
(() => {
  const node=(tag,text='')=>{const el=document.createElement(tag);el.textContent=text;return el;};
  const button=(text,fn)=>{const el=node('button',text);el.type='button';el.className='ghost-button';el.addEventListener('click',fn);return el;};
  const labels={selected:['检索候选','Retrieved candidate'],injected:['实际注入','Actually injected'],
    not_injected:['未注入本次请求','Not injected into this request'],applicable:['语义及状态适用','Intent and state match'],
    intent_not_matched:['意图不适用','Intent does not match'],candidate_missing:['没有未执行候选','No uncommitted candidate'],
    candidate_declined:['已结束被拒绝候选','Declined candidate ended'],stage_complete:['阶段检查通过','Stage completeness passed'],
    stage_incomplete:['阶段仍有缺项','Stage has missing information'],next_task_added:['已补入具体下一步任务','Concrete next task added'],
    execution_verified:['执行断言通过','Execution assertions passed'],execution_failed:['执行验证未通过，未自动重试','Execution verification failed; no automatic retry'],
    scope_mismatch:['范围不适用','Scope mismatch'],stage_mismatch:['阶段不适用','Stage mismatch'],mode_mismatch:['模式不适用','Mode mismatch'],
    not_active:['经验未启用','Rule is not active'],not_relevant:['相关性不足','Insufficient relevance'],
    budget_excluded:['完整规则超出预算，未注入','Whole rule exceeds budget; not injected'],candidate_limit:['候选数量达到上限','Candidate limit reached'],
    conflict_blocked:['规则冲突，未执行','Conflicting rules; execution blocked'],equivalent_rule_coalesced:['等效规则已合并执行','Equivalent rule coalesced'],
    version_or_status_changed:['版本或启用状态已变化','Version or active status changed'],execution_limit:['达到单轮执行上限','Per-turn execution limit'],
    no_progress_blocked:['相同状态无进展，已停止修正','No progress in identical state; repair stopped'],
    business_state_changed:['业务状态已变化','Business state changed'],no_progress:['业务状态无进展','No business progress'],
    completion_passed:['推进前检查通过','Pre-advance completeness passed'],completion_blocked:['推进被完整性检查阻止','Advance blocked by completeness'],
    retrieval_totals:['检索统计','Retrieval totals'],
    configuration_or_store_unavailable:['检索配置或存储不可用','Retrieval configuration or store unavailable'],
    backend_failure:['后端处理失败；没有自动重复修正','Backend processing failed; no automatic repair retry']};
  const t=pair=>pair[window.ECE329I18n?.language==='en'?1:0];
  const views=new WeakMap();
  function execution(host,records,openRule) {
    if(openRule)host.openExperienceRule=openRule;
    views.set(host,records);host.className='experience-execution';host.setAttribute('data-i18n-ignore','');host.replaceChildren();
    for(const record of records) {
      const card=node('article');card.className='experience-card';
      card.append(node('h3',`${record.design_id} · ${new Date(record.created*1000).toISOString()}`));
      const conclusions=record.events.filter(r=>['verification','conflict'].includes(r.step));
      card.append(node('p',conclusions.length?conclusions.map(r=>`${r.rule_id} v${r.version}: ${t(labels[r.code]||['未识别记录类型','Unrecognized record type'])}`).join('\n'):
        t(['本轮未执行可验证的规则动作','No verifiable rule action was executed in this turn'])));
      if(host.openExperienceRule)for(const id of new Set(record.events.map(r=>r.rule_id).filter(id=>/^EXP-[a-f0-9]{32}$/.test(id||'')))) {
        card.append(button(`${t(['查看或停止规则','Review or stop rule'])} ${id}`,()=>host.openExperienceRule(id.slice(4))));
      }
      const detail=node('details');detail.append(node('summary',t(['执行链路与技术详情','Execution chain and technical details'])));
      for(const row of record.events)detail.append(node('p',`${row.rule_id||''} ${row.version||''} · ${t(labels[row.code]||['未识别记录类型','Unrecognized record type'])}`));
      detail.append(node('pre',JSON.stringify(record.events,null,2)));card.append(detail);host.append(card);
    }
    if(!records.length)host.append(node('p',t(['尚无经验执行记录','No experience execution records'])));
  }
  function mount(host,{item,controls,request,isCurrent,getVersion,saved,capture,status}) {
    const state=JSON.parse(JSON.stringify(saved||{}));
    const panel=node('section');panel.className='rule-authoring';
    const draft=node('textarea');draft.rows=12;draft.value=state.text||'';draft.setAttribute('aria-label','Rule revision draft JSON');draft.setAttribute('data-i18n-ignore','');
    const detail=node('details');detail.open=state.open===true;
    detail.append(node('summary','修订草案与前后差异（尚未批准）'));
    const before=node('pre'),after=node('pre'),changed=node('pre'),source=node('pre');
    for(const el of [before,after,changed,source])el.setAttribute('data-i18n-ignore','');
    const notice=node('p');notice.setAttribute('role','status');
    const report=node('details');report.append(node('summary','回放验证详情（非真实模型测试）'));
    const reportBody=node('pre');reportBody.setAttribute('data-i18n-ignore','');report.append(reportBody);
    const draw=()=>{before.textContent=state.baseline||'';after.textContent=draft.value;
      source.textContent=state.source?JSON.stringify(state.source,null,2):'';
      try{const a=JSON.parse(state.baseline||'{}'),b=JSON.parse(draft.value||'{}');changed.textContent=JSON.stringify(Object.fromEntries([...new Set([...Object.keys(a),...Object.keys(b)])].filter(k=>JSON.stringify(a[k])!==JSON.stringify(b[k])).map(k=>[k,{before:a[k],after:b[k]}])),null,2);}catch{changed.textContent='';}
      reportBody.textContent=state.validation?JSON.stringify(state.validation.report,null,2):'';};
    let busy=false;
    const note=()=>({original:controls.original.value.trim(),corrected:controls.corrected.value.trim(),opinion:controls.note.value.trim()});
    const call=async(action,extra={})=>{
      if(busy||!isCurrent())return;
      if(getVersion()!==item.version){status.textContent='记录版本已更新，草稿已保留。请核对新证据后再提交。';return;}
      busy=true;
      const baseline=controls.content.value;
      const testedScope=controls.scope.value;
      try {
        const result=await request(`/${item.id}/${action}`,{method:'POST',body:JSON.stringify({version:getVersion(),content:JSON.parse(baseline),scope:controls.scope.value,note:note(),...extra})});
        if(!isCurrent())return;
        if(action==='replay') {
          state.validation=result;state.validatedText=baseline;state.validatedScope=testedScope;
          notice.textContent=result.report.status==='passed'?'隔离模拟回放通过；不代表真实模型测试通过。':'回放未通过，不能批准执行规则。';
          if(result.report.historical?.status==='unavailable')notice.textContent+=' 历史快照不完整，无法完整回放问题轮。';
        } else {
          state.baseline=baseline;state.baselineScope=testedScope;state.draftId=result.id;state.sourceVersion=result.base_version;
          state.restoreScope=result.scope;state.validation=null;
          draft.value=JSON.stringify(result.content,null,2);state.text=draft.value;detail.open=true;
          notice.textContent=result.source?.unsupported_actions?.length?'包含尚不支持的动作，需要代码维护；请查看详情。':'已生成草案；当前生效版本未改变。';
          state.source=result.source;
        }
        draw();capture();window.ECE329I18n?.refresh();
      } catch(error){if(isCurrent()){status.textContent=error.message;window.ECE329I18n?.refresh();}}
      finally{busy=false;}
    };
    const apply=(force=false)=>{
      if(!draft.value.trim())return;
      try{JSON.parse(draft.value);}catch{notice.textContent='经验 JSON 格式有误，请先修正；已填写的修正内容仍保留。';return;}
      if(!force&&(controls.content.value!==state.baseline||state.baselineScope!==undefined&&controls.scope.value!==state.baselineScope)){notice.textContent='JSON 或适用范围已被手动修改，未覆盖。请合并内容，或明确选择用草案替换。';override.hidden=false;window.ECE329I18n?.refresh();return;}
      controls.content.value=draft.value;
      if(state.restoreScope)controls.scope.value=state.restoreScope;
      state.validation=null;override.hidden=true;notice.textContent='草案已填入编辑器；仍需回放并批准。';capture();window.ECE329I18n?.refresh();
    };
    const override=button('确认用草案替换当前 JSON',()=>apply(true));override.hidden=true;
    detail.append(node('h4','变更字段'),changed,node('h4','修改前'),before,node('h4','修订草案（可编辑 JSON）'),draft,node('h4','修改后'),after,source,
                  button('将草案填入 JSON',()=>apply()),override);
    const versions=node('select');versions.setAttribute('aria-label','Historical rule version');
    const available=[...new Set((item.reviews||[]).flatMap(r=>[r.version-1,r.version]))].sort((a,b)=>a-b);
    for(const version of available){const option=node('option',`v${version}`);option.value=String(version);versions.append(option);}
    panel.append(button('根据审阅意见生成修订草案',()=>call('draft')),detail,
                 button('隔离回放当前 JSON',()=>call('replay')),versions,
                 button('从历史版本生成恢复草案',()=>call('restore',{restore_version:Number(versions.value)})),notice,report,
                 node('p','恢复规则不会撤销已经发生的设计修改；规则修改后必须重新验证。'));
    draft.addEventListener('input',()=>{state.text=draft.value;draw();capture();});
    detail.addEventListener('toggle',()=>{state.open=detail.open;capture();});
    for(const control of [controls.content,controls.scope])control.addEventListener('input',()=>{state.validation=null;capture();});
    draw();host.append(panel);
    return {capture:()=>({...state,text:draft.value,open:detail.open}),
      approval:()=>({...(state.validation&&state.validatedText===controls.content.value&&state.validatedScope===controls.scope.value?{validation_id:state.validation.id}:{}),
        ...(state.draftId&&state.sourceVersion===getVersion()?{draft_id:state.draftId}:{})})};
  }
  window.ECE329RuleAuthoring={mount,execution};
  window.addEventListener('ece329:language-changed',()=>{for(const host of document.querySelectorAll('.experience-execution'))if(views.has(host))execution(host,views.get(host));});
})();
