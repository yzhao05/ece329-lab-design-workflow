"use strict";

// Evidence is rendered locally and verbatim. Never send it to display translation.
(() => {
  const views = new WeakMap();
  const own = (value, key) => value != null && Object.prototype.hasOwnProperty.call(value, key);
  const obj = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const list = value => Array.isArray(value) ? value : [];
  const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const t = (zh, en) => window.ECE329I18n?.language === 'en' ? en : zh;
  const missing = () => t('未记录', 'Not recorded');
  const node = (tag, text = '') => { const n = document.createElement(tag); n.textContent = text; return n; };
  const labels = {
    answered_pending:['已回答仍被追问','Repeated question'], cross_stage_edit:['跨阶段修改','Cross-stage edit'],
    meta_question:['流程外问题','Workflow question'], missed_requests:['遗漏请求','Missed requests'],
    artifact_mismatch:['报告与设计不一致','Report/design mismatch'], final_review:['最终体验反馈','Final review'], other:['其他','Other'],
    IDEA_BRAINSTORMING:['实验想法完善','Experiment idea development'], COURSE_MAPPING:['课程映射','Course mapping'],
    LEARNING_OBJECTIVES:['学习目标','Learning objectives'], RESEARCH_QUESTION:['研究问题','Research question'],
    THEORETICAL_FRAMEWORK:['理论框架','Theoretical framework'], HYPOTHESIS:['假设','Hypothesis'],
    CONCEPTUAL_EXPERIMENT_DESIGN:['概念实验结构','Conceptual structure'], VARIABLES_AND_CONDITIONS:['变量与条件','Variables and conditions'],
    PROCEDURE:['实验流程','Procedure'], DATA_VISUALIZATION:['数据可视化','Data visualization'],
    EXPECTED_RESULTS:['预期结果','Expected results'], DESIGN_EVALUATION:['设计评价','Design evaluation'], SYNTHESIS:['总结','Summary'],
    ACCEPT_PREVIOUS_PROPOSAL:['接受前一方案','Accept previous proposal'], ADVANCE_STAGE:['请求推进阶段','Request stage advancement'],
    ANSWER_CURRENT_QUESTION:['回答当前问题','Answer current question'], MODIFY_PREVIOUS_PROPOSAL:['修改前一方案','Modify previous proposal'],
    REJECT_PREVIOUS_PROPOSAL:['拒绝前一方案','Reject previous proposal'], UNCLEAR:['意图不明确','Unclear intent'],
    REQUEST_CURRENT_DESIGN_SUMMARY:['请求当前设计总结','Request current design summary'], ASK_COURSE_QUESTION:['询问课程问题','Ask course question'],
    CONFIRM_STAGE_OR_MODIFY:['等待确认当前阶段或修改','Awaiting stage confirmation or revision'],
    ANSWER_IDEA_FACET:['等待回答想法完善问题','Awaiting an idea-development answer'],
    ANSWER_STAGE_QUESTION:['等待回答当前阶段问题','Awaiting a stage answer'], ANSWER_EMVR_STAGE_QUESTION:['等待回答 EMVR 阶段问题','Awaiting an EMVR stage answer'],
    stage:['阶段','Stage'], revision:['设计版本','Design revision'], intent:['意图','Intent'], confidence:['置信度','Confidence'],
    source:['来源','Source'], type:['类型','Type'], question:['待回答问题','Pending question'], subject:['事项','Subject'],
    answer_fields:['待回答字段','Answer fields'], missing_fields:['缺失字段','Missing fields'], detail:['说明','Detail'],
    message:['信息','Message'], code:['代码','Code'], reason:['理由','Reason'], action:['动作','Action'],
    before:['前一轮','Previous turn'], reported:['问题轮','Reported turn'], after:['后一轮','Following turn'],
    failed:['失败','Failed'], completed:['提炼完成','Extraction completed'], running:['进行中','Running'], queued:['等待分析','Queued'],
    draft:['提炼环节','Drafting'], check:['检查环节','Checking'], analysis:['分析环节','Analysis'],
    model_output_invalid:['模型输出不符合要求','Invalid model output'], connection_error:['连接失败','Connection failed'],
    timeout:['请求超时','Request timed out'], internal_error:['内部错误','Internal error'],
    output_limit:['输出额度耗尽，结果被截断','Output limit reached; result truncated'], invalid_json:['结果不是有效 JSON','Invalid JSON result'],
    schema_validation:['经验字段未通过校验','Experience fields failed validation'], evidence_reference:['引用了不存在的证据','Invalid evidence reference'],
    incomplete_response:['模型响应未完成','Incomplete model response'], empty_or_invalid_output:['没有完整有效的结构化输出','No complete valid structured output'],
    openai:['OpenAI','OpenAI'], deepseek:['DeepSeek','DeepSeek'], unconfigured:['未配置','Not configured'],
    GUIDED_DESIGN:['Guided 模式','Guided mode'], EMVR_DIRECT:['EMVR 模式','EMVR mode'],
    COURSE_MAPPING_AND_DIRECTION:['课程映射','Course mapping'], CONCEPTUAL_OR_VR_SETUP:['概念实验结构','Conceptual structure'],
    CONCEPTUAL_PROCEDURE:['概念实验流程','Conceptual procedure'], EXPECTED_DATA_VISUALIZATION:['预期数据可视化','Expected data visualization'],
    RESULT_INTERPRETATION:['可能结果及解释','Results and interpretation'], DESIGN_VALUE_AND_LIMITATIONS:['设计价值与局限','Value and limitations'],
    STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:['总结 PDF','Summary PDF'], CONFIRM_OR_MODIFY:['等待确认或修改方案','Awaiting proposal confirmation or revision'],
    observation:['观察内容','Observation'], evidence_ref:['证据引用','Evidence reference'],
    pending_action:['待办事项','Pending action'], completed_stages:['已完成阶段','Completed stages'],
    confirmation:['确认状态摘录','Confirmation excerpt'], completion_error:['完成检查返回值','Completion-check result'],
    evidence_supported:['证据支持检查','Evidence-support check'], positive_case_passes:['正例检查','Positive-case check'],
    negative_case_passes:['反例检查','Negative-case check'], issues:['检查说明','Check notes'],
    model_service_error:['模型服务错误','Model service error'], model_configuration_error:['模型未配置或不可用','Model not configured or unavailable'],
    model_connection_error:['模型连接失败','Model connection failed'], model_timeout:['模型请求超时','Model request timed out'],
    model_rate_limited:['模型请求受限','Model rate limit reached'], model_upstream_error:['模型服务端错误','Model provider server error'],
    model_request_rejected:['模型请求被拒绝','Model request rejected'],
    REQUEST_MORE_EXAMPLES:['请求更多例子','Request more examples'], PROVIDE_FEEDBACK:['提供反馈','Provide feedback'],
    REQUEST_DESIGN_REVIEW:['请求检查设计','Request design review'], COMPARE_DESIGN_OPTIONS:['比较设计方案','Compare design options'],
    MANAGE_DESIGN_VERSION:['管理设计版本','Manage design versions'], RETURN_TO_PREVIOUS_POINT:['返回之前事项','Return to a previous point'],
    NEW_TOPIC:['提出新主题','Introduce a new topic'], SET_INTERACTION_STATE:['切换模式','Switch interaction mode'],
  };
  const name = value => value == null || value === '' ? missing() : own(labels,value) ? t(...labels[value]) : String(value);
  function valueText(value) {
    if (value == null) return missing();
    if (value === '') return t('已记录为空', 'Recorded as empty');
    if (typeof value === 'boolean') return value ? t('是','Yes') : t('否','No');
    return String(value);
  }
  function valueNode(value) {
    if (Array.isArray(value)) {
      if (!value.length) return node('p',t('记录为空列表','Recorded as an empty list'));
      const ul=node('ul'); for (const item of value) { const li=node('li');li.append(valueNode(item));ul.append(li); } return ul;
    }
    if (value && typeof value === 'object') {
      const dl=node('dl'); for (const [key,item] of Object.entries(value)) {dl.append(node('dt',name(key)));const dd=node('dd');dd.append(valueNode(['stage','type','intent','status','phase'].includes(key) && typeof item==='string' ? name(item) : item));dl.append(dd);} return dl;
    }
    return node('p',valueText(value));
  }
  function field(host, zh, en, value) { const block=node('div');block.className='evidence-field';block.append(node('h5',t(zh,en)),valueNode(value));host.append(block); }
  function section(host, zh, en) { const s=node('section');s.className='evidence-section';s.append(node('h4',t(zh,en)));host.append(s);return s; }
  function recordList(host, value) {
    const rows=list(value), valid=rows.filter(isRecord);
    if ((value != null && !Array.isArray(value)) || valid.length!==rows.length) {
      host.append(node('p',t('部分记录格式异常，无法转为说明；原始数据保留在 JSON 中。','Some records have an unsupported format and cannot be summarized; their original data is preserved in JSON.')));
    }
    return valid;
  }
  function fold(host, title, open, onToggle) {
    const details=node('details');details.open=open;
    const summary=node('summary'), button=node('button');button.type='button';button.className='ghost-button evidence-toggle';
    summary.append(node('span',title),button);const body=node('div');body.className='evidence-body';details.append(summary,body);
    const sync=()=>{button.textContent=details.open?t('收起','Collapse'):t('展开','Expand');button.setAttribute('aria-expanded',String(details.open));onToggle(details.open);};
    button.addEventListener('click',event=>{event.preventDefault();details.open=!details.open;sync();});
    details.addEventListener('toggle',sync);sync();host.append(details);return body;
  }
  function recorded(row, key) { return own(row.recorded_fields,key) ? row.recorded_fields[key] === true : own(row,key) && row[key] != null; }
  function bodyText(row) {
    if (!recorded(row,'assistant')) return t('回复正文未记录','Reply body not recorded');
    if (row.assistant === '' && !own(row.recorded_fields,'assistant')) return t('旧记录正文摘录为空；原始正文是否为空未记录','Legacy reply excerpt is empty; whether the original body was empty is not recorded');
    if (row.assistant === '') return t('该轮回复正文为空','This turn has an empty reply body');
    return valueText(row.assistant);
  }
  function renderTurn(host, row, position) {
    const turn=node('article');turn.className='evidence-turn'+(position==='reported'?' evidence-turn-reported':'');
    turn.append(node('h5',`${name(position)} · ${t('版本','Revision')} ${valueText(row.revision)} · ${name(row.stage)} · ${name(row.mode)}`));
    const user=node('div');user.className='evidence-bubble evidence-user';user.append(node('strong',t('用户','User')),node('p',recorded(row,'user')?valueText(row.user):missing()));
    const agent=node('div');agent.className='evidence-bubble evidence-agent';agent.append(node('strong','ECE329 Agent'),node('p',bodyText(row)));
    field(agent,'提示','Warnings',recorded(row,'warnings')?row.warnings:undefined);
    field(agent,'假设／补充说明','Assumptions / notes',recorded(row,'assumptions')?row.assumptions:undefined);
    field(agent,'下一步任务','Next task',recorded(row,'student_task')?row.student_task:undefined);
    turn.append(user,agent);
    if (list(row.truncated_fields).length) turn.append(node('p',t('以下记录已截断：','Truncated fields: ')+row.truncated_fields.map(name).join(', ')));
    host.append(turn);
  }
  function pending(state) {
    if (own(state,'pending_action')) return state.pending_action;
    if (!own(state,'pending_excerpt')) return undefined;
    try {return JSON.parse(state.pending_excerpt);} catch {return undefined;}
  }
  function stateDescription(state) {
    if (!state) return undefined;
    const result={};
    for(const key of ['revision','stage','completed_stages','completion_error']) if(own(state,key))result[key]=state[key];
    if(own(result,'stage'))result.stage=name(result.stage);
    if(Array.isArray(result.completed_stages))result.completed_stages=result.completed_stages.map(name);
    result.pending_action=pending(state);
    if(state.confirmation_excerpt) {
      try {result.confirmation=JSON.parse(state.confirmation_excerpt);}
      catch {result.confirmation=t('确认状态摘录不完整；请核对原始 JSON','Confirmation excerpt is incomplete; inspect the raw JSON');}
    }
    return result;
  }
  function renderState(host, snapshot, target) {
    const s=section(host,'当时的流程状态','Workflow state at the reported turn');
    const before=obj(target?.state_before),after=obj(target?.state_after),p=pending(before),q=pending(after);
    field(s,'当时在做什么','What was pending',p===null?t('记录显示没有待办','No pending action in the record'):p?name(p.type):undefined);
    if (p) field(s,'当时的具体问题','Recorded pending question',p.question);
    field(s,'用户说了什么','What the user said',target && recorded(target,'user')?target.user:undefined);
    const intent=obj(target?.resolved_intent);
    field(s,'系统如何理解（记录值）','System interpretation (recorded)',intent.intent?name(intent.intent):undefined);
    if (Object.keys(intent).length) field(s,'意图解析明细','Intent details',intent);
    let change;
    if(before.stage && after.stage) change=before.stage===after.stage?t('阶段未变化','Stage unchanged'):`${name(before.stage)} → ${name(after.stage)}`;
    field(s,'实际阶段变化','Actual stage change',change);
    field(s,'回复正文状态','Reply body status',target ? recorded(target,'assistant') && target.assistant !== '' ? t('已记录非空回复正文，见上方对话','A non-empty reply body is recorded; see the conversation above') : bodyText(target) : undefined);
    field(s,'处理后的待办','Pending action after the turn',q===null?t('记录显示没有待办','No pending action in the record'):q);
    if(p && q && JSON.stringify(p)===JSON.stringify(q)) s.append(node('p',t('前后记录中的待办相同。','The recorded pending action is unchanged.')));
    field(s,'阻塞信息／完成检查返回值','Blocking information / completion-check result',after.completion_error);
    s.append(node('p',t('仅展示记录的检查信息；未记录具体缺项时，不推测缺项数量或原因。','Only recorded check results are shown. Missing fields or causes are not inferred.')));
    field(s,'问题发生前的状态','State before the reported turn',stateDescription(target?.state_before));
    field(s,'问题发生后的状态','State after the reported turn',stateDescription(target?.state_after));
    if(before.pending_excerpt_truncated || after.pending_excerpt_truncated) s.append(node('p',t('待办状态摘录已截断。','Pending-state excerpt was truncated.')));
    const current=section(s,'反馈提交时的状态（不是问题发生时）','State at feedback submission (not at the reported turn)');
    field(current,'当前状态快照','Submission snapshot',stateDescription(snapshot.current_state));
    if(snapshot.field_excerpt_truncated) current.append(node('p',t('提交时设计字段摘录已截断。','Design field excerpt at submission was truncated.')));
  }
  function readable(host, record) {
    const payload=obj(record.evidence),snapshot=obj(payload.evidence || payload),analysis=obj(payload.extraction_analysis),diagnosis=obj(analysis.diagnosis);
    if(snapshot.evidence_migration) {
      host.append(node('p',t('历史证据已自动升级。仅补充可核对的历史记录；经验内容和人工审阅保持原样，未重新调用模型。','Historical evidence was upgraded automatically using verifiable saved history. Experience content and human reviews were preserved; no model was called.')));
      if(list(snapshot.evidence_migration.conflicting_fields_preserved).length)host.append(node('p',t('部分历史字段不一致，已保留原证据；请核对原始 JSON 中的迁移记录。','Some historical fields conflict. Original evidence was preserved; inspect the migration record in the raw JSON.')));
    }
    const feedback=section(host,'用户反馈','User feedback');
    field(feedback,'原始反馈','Original feedback',payload.message ?? record.message);
    field(feedback,'问题类型','Issue category',name(payload.category ?? record.category));
    field(feedback,'用户明确填写的期望行为','Expected behavior explicitly recorded from the user',payload.expected_behavior);
    field(feedback,'Agent 归纳的期望行为（需核对原文）','Agent interpretation of expected behavior (verify against the original)',diagnosis.expected_behavior);
    const chat=section(host,'问题前后对话','Conversation around the reported turn');
    const chain=recordList(chat,snapshot.event_chain),target=chain.find(row=>row.position==='reported') || (isRecord(snapshot.reported_turn)?snapshot.reported_turn:null);
    for(const position of ['before','reported','after']) {
      const row=chain.find(item=>item.position===position) || (position==='reported'?target:null);
      if(row)renderTurn(chat,row,position);else field(chat,...(labels[position]),undefined);
    }
    if(!target && list(snapshot.recent_turns).length) {
      const recent=section(chat,'提交时的最近对话（无法定位为问题轮）','Recent turns at submission (not identified as the reported turn)');
      for(const row of recordList(recent,snapshot.recent_turns))renderTurn(recent,row,t('最近对话','Recent turn'));
    }
    if(snapshot.evidence_schema_version!==2 || snapshot.evidence_migration) chat.append(node('p',t('旧记录未能核对的截断信息和历史状态仍为未知。没有原始记录佐证的空摘录，不能证明原始回复为空。','Unverified legacy truncation and historical states remain unknown. An empty excerpt without source evidence does not prove the original reply was empty.')));
    if(snapshot.history_limitations) chat.append(node('p',t('历史状态缺失时显示“未记录”。摘录可能不完整，缺少相邻对话不能证明成功或失败。','Missing historical states are shown as not recorded. Excerpts may be incomplete; missing neighboring turns do not prove success or failure.')));
    renderState(host,snapshot,target);
    const results=section(host,'经验层的分析结果','Experience-layer analysis');
    results.append(node('p',t('以下是 Agent 的分析，不是独立确认的原始事实。','The following is Agent analysis, not independently verified source facts.')));
    for(const [key,zh,en] of [['facts','归纳的事实（Agent）','Summarized facts (Agent)'],['hypotheses','原因假设','Cause hypotheses'],['applicability','适用条件','Applicability'],['exceptions','例外','Exceptions'],['unknowns','待核实事项','Unverified items']])field(results,zh,en,diagnosis[key]);
    const candidate=payload.extraction_candidate || record.content;
    if(candidate)for(const [key,zh,en] of [['summary','经验摘要','Experience summary'],['trigger','经验触发条件','Experience trigger'],['recommendation','处理建议','Recommendation'],['verification','建议的验证方法（不是已完成验证）','Proposed verification (not completed validation)']])field(results,zh,en,candidate[key]);
    field(results,'模型检查（不是回放）','Model checks (not replay)',analysis.model_check);
    results.append(node('p',analysis.validation_status==='not_replayed'?t('尚未进行回放验证','No replay validation has been performed'):analysis.validation_status?t('回放验证状态请核对原始记录','Check the original record for replay-validation status'):t('未记录已完成的回放验证','No completed replay validation is recorded')));
    if(analysis.validation_status && analysis.validation_status!=='not_replayed')field(results,'验证状态原值','Recorded validation status',analysis.validation_status);
    const attempts=section(host,'经验提炼记录','Experience extraction history');
    attempts.append(node('p',t('这是反馈提交后的提炼过程，不是原对话故障的发生过程。','These attempts occurred after feedback submission; they are not the original conversation failure timeline.')));
    const rows=recordList(attempts,payload.analysis_attempts);
    if(!rows.length)attempts.append(node('p',missing()));
    const ul=node('ul');for(const row of rows) {
      const li=node('li',`${t('第','Attempt ')} ${valueText(row.attempt)}${t(' 次','')}: ${name(row.provider)} / ${valueText(row.model)} · ${name(row.phase)} · ${name(row.status)}`);
      if(row.code || row.reason) li.append(node('p',[row.code,row.reason].filter(Boolean).map(name).join(' · ')));
      if(row.http_status)li.append(node('p',`HTTP ${valueText(row.http_status)}`));ul.append(li);
    }attempts.append(ul);
  }
  function render(host) {
    const state=views.get(host);if(!state)return;
    host.replaceChildren();
    const read=fold(host,t('对话与状态说明','Conversation and state'),state.readOpen,open=>state.readOpen=open);
    readable(read,state.record);
    const raw=fold(host,t('原始 JSON 记录','Raw JSON record'),state.rawOpen,open=>state.rawOpen=open);
    const images=[];
    const compact=JSON.stringify(state.record,function(key,value){
      if(key==='data_url' && typeof value==='string' && /^data:image\/(?:jpeg|png|webp|gif|bmp);base64,/.test(value)) {
        images.push({url:value,role:this.role});return t('[图片见预览；复制和下载保留完整数据]','[See image preview; copy/download retains full data]');
      }return value;
    },2);
    const actions=node('div');actions.className='experience-actions';const status=node('p');status.setAttribute('role','status');
    const copy=node('button',t('复制 JSON','Copy JSON')),download=node('button',t('下载 JSON','Download JSON'));
    for(const button of [copy,download]){button.type='button';button.className='ghost-button';}
    copy.addEventListener('click',async()=>{
      try {await navigator.clipboard.writeText(JSON.stringify(state.record,null,2));status.textContent=t('已复制','Copied');}
      catch {status.textContent=t('无法复制，请使用下载 JSON。','Copy unavailable. Use Download JSON.');}
    });
    download.addEventListener('click',()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(state.record,null,2)],{type:'application/json;charset=utf-8'}));
      const a=node('a');a.href=url;a.download=`ece329-evidence-${String(state.record.id || 'record').replace(/[^\w-]/g,'_')}.json`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    actions.append(copy,download);raw.append(actions,status,node('pre',compact));
    if(images.length){const gallery=node('div');gallery.className='feedback-image-gallery';for(const [i,image] of images.entries()){const figure=node('figure'),img=node('img');img.src=image.url;img.alt=image.role==='problem'?t('问题对话截图','Reported-turn screenshot'):image.role==='before'?t('前文截图','Previous-context screenshot'):image.role==='after'?t('后文截图','Following-context screenshot'):t('证据图片 ','Evidence image ')+(i+1);figure.append(img,node('figcaption',img.alt));gallery.append(figure);}read.append(gallery);}
  }
  window.ECE329ReviewEvidence={mount(host,record){host.className='review-evidence';host.setAttribute('data-i18n-ignore','');views.set(host,{record,readOpen:true,rawOpen:false});render(host);}};
  window.addEventListener('ece329:language-changed',()=>{for(const host of document.querySelectorAll('.review-evidence'))render(host);});
})();
