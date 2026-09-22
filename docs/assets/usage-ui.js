"use strict";
(() => {
  const english=()=>window.ECE329I18n?.language==='en';
  const stages={IDEA_BRAINSTORMING:['想法探索','Idea exploration'],COURSE_MAPPING_AND_DIRECTION:['课程映射','Course mapping'],LEARNING_OBJECTIVES:['学习目标','Learning objectives'],RESEARCH_QUESTION:['研究问题','Research question'],THEORETICAL_FRAMEWORK:['理论框架','Theoretical framework'],HYPOTHESIS:['假设','Hypothesis'],CONCEPTUAL_OR_VR_SETUP:['实验结构','Experiment setup'],VARIABLES_AND_CONDITIONS:['变量与条件','Variables and conditions'],CONCEPTUAL_PROCEDURE:['实验流程','Procedure'],EXPECTED_DATA_VISUALIZATION:['数据可视化','Visualization'],RESULT_INTERPRETATION:['结果解释','Interpretation'],DESIGN_VALUE_AND_LIMITATIONS:['价值与局限','Value and limitations'],STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:['最终总结','Final synthesis'],UNKNOWN:['阶段未记录','Stage not recorded'],UNATTRIBUTED_PROCESSING:['未分配的本地处理','Unattributed local processing']};
  const agents={design_agent:['设计 Agent','Design agent'],intent_resolver:['意图解析 Agent','Intent resolver'],experience_extractor:['经验提炼 Agent','Experience extractor'],experience_checker:['经验检查 Agent','Experience checker'],experience_semantic_evaluator:['经验语义评测','Experience semantic evaluation'],translator:['翻译 Agent','Translator']};
  const stageName=stage=>stages[stage]?.[english()?1:0] || stage || (english()?'Not recorded':'未记录');
  function identity(el, value) {
    el.setAttribute('data-i18n-ignore','');el.dataset.usageIdentity=JSON.stringify(value);
    if(value.agent) {
      const role=agents[value.agent]?.[english()?1:0] || value.agent;
      const reported=value.reported_models?.length ? ` · ${english()?'Returned model':'返回模型'}: ${value.reported_models.join(', ')}`:'';
      el.textContent=`${role} · API: ${value.provider} · ${english()?'Model':'模型'}: ${value.model_id}${reported}`;
    } else el.textContent=stageName(value.stage);
  }
  function stopState(el, item) {
    el.setAttribute('data-i18n-ignore','');el.dataset.usageStop=JSON.stringify(item);
    const current=item.current_state, last=item.usage?.last_dialogue;
    const status=current?.status==='complete'?(english()?'Complete':'已完成'):current?.status==='active'?(english()?'In progress':'未完成'):(english()?'Unknown':'未知');
    el.textContent=english()?`Current stage: ${stageName(current?.stage)} · ${status}`:`当前停留阶段：${stageName(current?.stage)} · ${status}`;
    if(last) el.textContent+=english()?` · Last handled: ${stageName(last.handled_stage)} · Last operation: ${last.status}`:` · 最近处理阶段：${stageName(last.handled_stage)} · 最近处理结果：${last.status==='failed'?'失败':last.status==='completed'?'完成':last.status}`;
  }
  function duration(ms) {
    if(typeof ms!=='number' || !Number.isFinite(ms) || ms<0) return english()?'Not recorded':'未记录';
    const seconds=Math.floor(ms/1000), minutes=Math.floor(seconds/60), hours=Math.floor(minutes/60);
    if(!seconds) return english()?'<1s':'不足1秒';
    return [hours?(english()?`${hours}h`:`${hours}小时`):'',minutes%60?(english()?`${minutes%60}m`:`${minutes%60}分`):'',english()?`${seconds%60}s`:`${seconds%60}秒`].filter(Boolean).join(' ');
  }
  function timing(el, ms, total=false) {
    el.setAttribute('data-i18n-ignore','');
    el.dataset.duration=JSON.stringify({ms,total});
    el.textContent=`${total?(english()?'Active time':'活跃总时长'):(english()?'Time':'用时')} ${duration(ms)}`;
    el.title=english()?'Backend processing time; excludes user idle time and queueing.':'后端实际处理用时，不含用户等待及排队时间。';
  }
  function summary(el, usage) {
    el.setAttribute('data-i18n-ignore','');
    el.dataset.usage=JSON.stringify(usage || null);
    if(!usage || !usage.run_count) {el.textContent=english()?'Usage not recorded':'尚无用量记录';return;}
    const count=(key)=>usage[key] == null ? `${english()?'Incomplete; known':'不完整，已知'} ${usage['known_'+key] ?? 0}`:String(usage[key]);
    const money=usage.estimated_cost_usd == null
      ? `${english()?'Incomplete; known estimate':'不完整，已知估算'} $${Number(usage.known_cost_usd||0).toFixed(8)}`
      : `$${Number(usage.estimated_cost_usd).toFixed(8)}`;
    const timeLabel=usage.time_basis==='api'?(english()?'API time':'API 用时'):usage.time_basis==='local'?(english()?'Local processing':'本地处理用时'):(english()?'Active time':'活跃时长');
    el.textContent=english()
      ? `Input tokens: ${count('input_tokens')} · Output tokens: ${count('output_tokens')} · Estimated cost (USD): ${money} · ${timeLabel}: ${duration(usage.active_ms)} · API calls: ${usage.call_count}`
      : `输入 token：${count('input_tokens')} · 输出 token：${count('output_tokens')} · 估算费用（USD）：${money} · ${timeLabel}：${duration(usage.active_ms)} · API 调用：${usage.call_count}`;
  }
  window.ECE329Usage={duration,timing,summary,identity,stopState};
  window.addEventListener('ece329:language-changed',()=>{
    document.querySelectorAll('[data-duration]').forEach(el=>{const data=JSON.parse(el.dataset.duration);timing(el,data.ms,data.total);});
    document.querySelectorAll('[data-usage]').forEach(el=>summary(el,JSON.parse(el.dataset.usage)));
    document.querySelectorAll('[data-usage-identity]').forEach(el=>identity(el,JSON.parse(el.dataset.usageIdentity)));
    document.querySelectorAll('[data-usage-stop]').forEach(el=>stopState(el,JSON.parse(el.dataset.usageStop)));
  });
})();
