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
    condition_not_matched:['结构化条件不满足','Structured conditions do not match'],
    missing_fields_asked:['已询问校验确认的缺项','Asked for validator-confirmed missing information'],
    advanced_after_checks:['完成检查后推进','Advanced after completeness checks'],
    base_workflow_preserved:['修正规则未执行，保留用户意图走基础流程','Rule repair skipped; user intent retained in the base workflow'],
    backend_failure:['后端处理失败；没有自动重复修正','Backend processing failed; no automatic repair retry']};
  const t=pair=>pair[window.ECE329I18n?.language==='en'?1:0];
  const views=new WeakMap();
  const authorViews=new WeakMap();
  function execution(host,records,openRule,statistics) {
    if(openRule)host.openExperienceRule=openRule;
    if(statistics)host.executionStatistics=statistics;
    views.set(host,records);host.className='experience-execution';host.setAttribute('data-i18n-ignore','');host.replaceChildren();
    if(host.executionStatistics){
      const stats=node('details');stats.append(node('summary',t(['最近 1000 轮：按规则版本统计执行结果','Last 1,000 turns: execution outcomes by rule version'])));
      for(const row of host.executionStatistics.rules||[])stats.append(node('p',`${row.id} v${row.version} / ${row.mode}: ${t(['执行断言通过率','Execution assertion pass rate'])} ${row.verified_rate===null?t(['未测试','Not tested']):(row.verified_rate*100).toFixed(1)+'%'}; ${Object.entries(row.counts).map(([code,n])=>`${t(labels[code]||['未识别记录类型','Unrecognized record type'])}: ${n}`).join('; ')}`));
      host.append(stats);
    }
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
    const summary=node('p');summary.setAttribute('data-i18n-ignore','');
    const conditionPreview=node('p');conditionPreview.setAttribute('data-i18n-ignore','');
    const confirmed=node('input');confirmed.type='checkbox';confirmed.checked=state.conditionsConfirmedText===controls.content.value;
    const confirmation=node('label');confirmation.append(confirmed,node('span','我已核对实际执行条件；没有被遗漏的文字限制'));
    const config=node('details');config.append(node('summary','结构化条件与受控动作'));
    config.append(node('p','必要条件：有未提交候选；明确拒绝并要求继续；本轮没有新修改。所有动作保留阶段检查与用户确认。'));
    const pendingType=node('select');
    pendingType.multiple=true;pendingType.size=3;
    for(const [value,label] of [['CONFIRM_STAGE_OR_MODIFY','阶段确认待办'],['CONFIRM_OR_MODIFY','方案确认待办'],['ANSWER_STAGE_QUESTION','阶段问题待办']]) {
      const option=node('option',label);option.value=value;pendingType.append(option);
    }
    const repeat=node('input');repeat.type='number';repeat.min='0';repeat.max='10';repeat.value='0';
    const style=node('select');for(const [value,label] of [['status_and_task','已完成动作与下一步任务'],['task_only','只显示有效的下一步任务']]){const option=node('option',label);option.value=value;style.append(option);}
    style.value='status_and_task';
    const unmapped=node('textarea');unmapped.rows=2;
    let initialExecution={};try{initialExecution=JSON.parse(controls.content.value).execution||{};}catch{}
    const editorState=state.editor||{pendingTypes:initialExecution.conditions?.pending_types||[],
      repeat:initialExecution.conditions?.min_repeat_count||0,style:initialExecution.actions?.find(a=>a.name==='ensure_next_task')?.parameters?.style||'status_and_task',
      unmapped:(initialExecution.unmapped_conditions||[]).join('\n')};
    const selectedTypes=editorState.pendingTypes||(editorState.pendingType?[editorState.pendingType]:[]);
    for(const option of pendingType.children)option.selected=selectedTypes.includes(option.value);
    repeat.value=String(editorState.repeat);style.value=editorState.style;unmapped.value=editorState.unmapped;
    const readEditor=()=>({pendingTypes:Array.from(pendingType.children).filter(o=>o.selected).map(o=>o.value),repeat:Number(repeat.value),style:style.value,unmapped:unmapped.value});
    state.editorSourceText??=controls.content.value;
    const loadEditor=()=>{
      let execution;try{execution=JSON.parse(controls.content.value).execution||{};}catch{notice.textContent='请先修正 JSON。';window.ECE329I18n?.refresh();return;}
      for(const option of pendingType.children)option.selected=(execution.conditions?.pending_types||[]).includes(option.value);
      repeat.value=String(execution.conditions?.min_repeat_count||0);
      style.value=execution.actions?.find(a=>a.name==='ensure_next_task')?.parameters?.style||'status_and_task';
      unmapped.value=(execution.unmapped_conditions||[]).join('\n');
      state.editor=readEditor();state.editorSourceText=controls.content.value;capture();
    };
    config.append(node('p','缺项询问是必要行为，不能关闭。旧规则未列出此动作时，也会依据阶段检查提出具体问题。'));
    for(const [label,control] of [['待办类型限制（可多选；不选择表示不限）',pendingType],['至少重复次数（0–10）',repeat],['回复方式',style],['尚未转换的文字限制（每行一项；非空时不能启用执行）',unmapped]]) {
      const wrapper=node('label',label);wrapper.append(control);config.append(wrapper);
      control.addEventListener('input',()=>{state.editor=readEditor();capture();});
    }
    const draw=()=>{before.textContent=state.baseline||'';after.textContent=draft.value;
      source.textContent=state.source?JSON.stringify(state.source,null,2):'';
      try{const a=JSON.parse(state.baseline||'{}'),b=JSON.parse(draft.value||'{}');changed.textContent=JSON.stringify(Object.fromEntries([...new Set([...Object.keys(a),...Object.keys(b)])].filter(k=>JSON.stringify(a[k])!==JSON.stringify(b[k])).map(k=>[k,{before:a[k],after:b[k]}])),null,2);}catch{changed.textContent='';}
      const evalResult=state.evaluation||item.evaluations?.[0];
      reportBody.textContent=JSON.stringify({executor:state.validation?.report||null,model_evaluation:evalResult||null},null,2);
      const names={passed:['通过','Passed'],failed:['未通过','Failed'],not_tested:['未测试','Not tested'],not_applicable:['不适用','Not applicable'],unavailable:['无法完整回放','Full replay unavailable'],insufficient_coverage:['覆盖不足','Insufficient coverage']};
      const approvedId=item.reviews?.find(r=>r.version===item.version)?.content?.source?.validation_id;
      const approvedValidation=item.validations?.find(v=>v.id===approvedId);
      let unchanged=false;try{unchanged=JSON.stringify(JSON.parse(controls.content.value))===JSON.stringify(item.content)&&controls.scope.value===(item.evidence?.scope||'global');}catch{}
      const v=state.validatedText===controls.content.value&&state.validatedScope===controls.scope.value?state.validation?.report:unchanged?approvedValidation?.report:null;
      const e=state.evaluatedText===controls.content.value&&state.evaluation?.base_version===getVersion()?state.evaluation.report:null;
      try{const value=JSON.parse(controls.content.value),x=value.execution;
        conditionPreview.textContent=x?`${t(['实际执行条件：明确拒绝未提交候选并要求继续；本轮没有新修改。模式与阶段：','Actual conditions: explicitly reject uncommitted changes and proceed; no new edit. Modes and stages: '])}${value.modes?.join(', ')} / ${value.stages?.join(', ')}\n${t(['待办类型 / 至少重复次数','Pending types / minimum repeat count'])}: ${x.conditions?.pending_types?.join(', ')||t(['不限','Any'])} / ${x.conditions?.min_repeat_count||0}\n${t(['文字 trigger 仅作说明；未转换的限制不得忽略：','Text trigger is explanatory; unmapped restrictions cannot be ignored: '])}${(x.unmapped_conditions||[]).join('; ')||t(['未填写','None listed'])}`:t(['当前为建议类经验，没有执行动作。','This is advisory experience without executable actions.']);
        if(x)conditionPreview.textContent+='\n'+t(['阶段不完整时必须询问具体缺项；旧规则也适用。','An incomplete stage must ask for specific missing information; this also applies to legacy rules.']);
      }catch{conditionPreview.textContent=t(['请先修正 JSON。','Correct the JSON first.']);}
      summary.textContent=`${t(['执行器测试','Executor tests'])}: ${t(names[v?.status]||names.not_tested)}; ${t(['语义评测','Semantic evaluation'])}: ${t(names[e?.semantic_status]||names.not_tested)}; ${t(['完整流程回放','Full workflow replay'])}: ${t(names[e?.full_flow_status]||names.not_tested)}`;
      if(e)summary.textContent+=`\n${t(['模型判断后的受控流程检查','Controlled workflow checks after model classification'])}: ${t(names[e.controlled_flow_status]||names.not_tested)}`;
      if(v?.coverage)summary.textContent+=`\n${t(['正例执行 / 反例检查 / 未覆盖范围','Executed positives / checked negatives / uncovered scopes'])}: ${v.coverage.positive_executed} / ${v.coverage.negative_checked} / ${v.coverage.uncovered.length}`;
      if(e?.usage)summary.textContent+=`\n${t(['模型评测用量','Model evaluation usage'])}: ${e.usage.total_tokens??t(['未记录','Not recorded'])} tokens; USD ${e.usage.estimated_cost_usd??t(['未记录','Not recorded'])}`;
      if(e)summary.textContent+=`\n${t(['评测次数','Evaluation attempts'])}: ${e.attempt} / ${e.max_runs}`;
      if(e?.status==='failed')summary.textContent+=`\n${t(['模型评测请求或校验失败，请展开详情；未自动重试。','Evaluation request or validation failed; expand details. No automatic retry.'])}`;
    };
    let busy=false;
    const note=()=>({original:controls.original.value.trim(),corrected:controls.corrected.value.trim(),opinion:controls.note.value.trim()});
    const call=async(action,extra={})=>{
      if(busy||!isCurrent())return;
      if(action==='configure'&&state.editorSourceText!==controls.content.value){notice.textContent='JSON 已更改，请先从当前 JSON 载入结构化设置，再生成草案。';window.ECE329I18n?.refresh();return;}
      if(getVersion()!==item.version){status.textContent='记录版本已更新，草稿已保留。请核对新证据后再提交。';return;}
      busy=true;
      const baseline=controls.content.value;
      const testedScope=controls.scope.value;
      try {
        const result=await request(`/${item.id}/${action}`,{method:'POST',body:JSON.stringify({version:getVersion(),content:JSON.parse(baseline),scope:controls.scope.value,note:note(),...extra})});
        if(!isCurrent())return;
        if(action==='evaluate') {
          state.evaluation=result;state.evaluatedText=baseline;notice.textContent='模型评测已记录；执行器、语义与完整回放结果分别展示。';
        } else if(action==='replay') {
          state.validation=result;state.validatedText=baseline;state.validatedScope=testedScope;
          notice.textContent=result.report.status==='passed'?'隔离模拟回放通过；不代表真实模型测试通过。':'回放未通过，不能批准执行规则。';
          if(result.report.historical?.status==='unavailable')notice.textContent+=' 历史快照不完整，无法完整回放问题轮。';
        } else {
          state.baseline=baseline;state.baselineScope=testedScope;state.draftId=result.id;state.sourceVersion=result.base_version;
          state.restoreScope=result.scope;state.validation=null;
          state.conditionsConfirmedText=null;confirmed.checked=false;
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
      loadEditor();
      if(state.restoreScope)controls.scope.value=state.restoreScope;
      state.validation=null;override.hidden=true;notice.textContent='草案已填入编辑器；仍需回放并批准。';capture();window.ECE329I18n?.refresh();
    };
    const override=button('确认用草案替换当前 JSON',()=>apply(true));override.hidden=true;
    detail.append(node('h4','变更字段'),changed,node('h4','修改前'),before,node('h4','修订草案（可编辑 JSON）'),draft,node('h4','修改后'),after,source,
                  button('将草案填入 JSON',()=>apply()),override);
    const versions=node('select');versions.setAttribute('aria-label','Historical rule version');
    const available=[...new Set((item.reviews||[]).flatMap(r=>[r.version-1,r.version]))].sort((a,b)=>a-b);
    for(const version of available){const option=node('option',`v${version}`);option.value=String(version);versions.append(option);}
    config.append(button('从当前 JSON 载入结构化设置',loadEditor),button('根据结构化设置生成草案',()=>call('configure',{
      conditions:{pending_types:readEditor().pendingTypes,min_repeat_count:Number(repeat.value)},
      reply_style:style.value,unmapped_conditions:unmapped.value.split('\n').map(s=>s.trim()).filter(Boolean)})),
      node('p','结束候选、重新检查阶段、合规推进、校对回复为必要动作；关闭任意已回答待办及补答遗漏问题暂需代码维护。'));
    panel.append(button('根据审阅意见生成修订草案',()=>call('draft')),config,detail,
                 button('隔离回放当前 JSON',()=>call('replay')),versions,
                 button('从历史版本生成恢复草案',()=>call('restore',{restore_version:Number(versions.value)})),notice,report,
                 summary,conditionPreview,confirmation,
                 node('p','真实模型评测会产生 API 费用：每个来源版本最多 3 次，每次最多 1 个请求、8192 输出 token；不自动重试。'),
                 button('运行真实模型语义评测及隔离流程回放',()=>call('evaluate',{confirm_model_evaluation:true})),
                 node('p','恢复规则不会撤销已经发生的设计修改；规则修改后必须重新验证。'));
    draft.addEventListener('input',()=>{state.text=draft.value;draw();capture();});
    detail.addEventListener('toggle',()=>{state.open=detail.open;capture();});
    confirmed.addEventListener('change',()=>{state.conditionsConfirmedText=confirmed.checked?controls.content.value:null;capture();});
    for(const control of [controls.content,controls.scope])control.addEventListener('input',()=>{state.validation=null;state.conditionsConfirmedText=null;confirmed.checked=false;capture();draw();});
    draw();host.append(panel);
    authorViews.set(panel,draw);
    return {capture:()=>({...state,text:draft.value,open:detail.open,editor:readEditor()}),
      approval:()=>({...(state.validation&&state.validatedText===controls.content.value&&state.validatedScope===controls.scope.value?{validation_id:state.validation.id}:{}),
        ...(confirmed.checked&&state.conditionsConfirmedText===controls.content.value&&state.validatedText===controls.content.value?{confirmed_conditions:state.validation?.report.effective_conditions}:{}),
        ...(state.draftId&&state.sourceVersion===getVersion()?{draft_id:state.draftId}:{})})};
  }
  window.ECE329RuleAuthoring={mount,execution};
  window.addEventListener('ece329:language-changed',()=>{
    for(const host of document.querySelectorAll('.experience-execution'))if(views.has(host))execution(host,views.get(host));
    for(const panel of document.querySelectorAll('.rule-authoring'))authorViews.get(panel)?.();
  });
})();
