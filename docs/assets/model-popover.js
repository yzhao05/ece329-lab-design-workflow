"use strict";
(() => {
  const panel = document.getElementById('modelPopover');
  const trigger = document.getElementById('currentModelButton');
  const name = document.getElementById('currentModelName');
  const close = document.getElementById('closeModelPopover');
  const effortSelect = document.getElementById('reasoningSelect');
  const budgetHelp = document.getElementById('modelBudgetHelp');
  const effortLabels = {none:'关闭（None）',low:'低（Low）',medium:'中（Medium）',high:'高（High）',xhigh:'很高（XHigh）',max:'最高（Max）'};
  let activeModel = null;
  function position() {
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(520, window.innerWidth - 24);
    panel.style.right = `${Math.min(Math.max(12, window.innerWidth - rect.right), Math.max(12, window.innerWidth - width - 12))}px`;
    const above = rect.top - 24, below = window.innerHeight - rect.bottom - 24;
    const openBelow = above < 360 && below > above;
    panel.style.top = openBelow ? `${rect.bottom + 10}px` : 'auto';
    panel.style.bottom = openBelow ? 'auto' : `${Math.max(12, window.innerHeight - rect.top + 10)}px`;
    panel.style.maxHeight = `${Math.max(120, openBelow ? below : above)}px`;
  }
  window.updateModelPopover = () => {
    const config = state.modelConfig;
    const stage = STAGES[Math.min(state.stageIndex,STAGES.length-1)]?.[0];
    const profile = config?.strategy === 'quality' ? 'reasoning' : config?.strategy === 'fast' ? 'fast'
      : config?.strategy === 'custom' ? config.stage_overrides?.[stage] || config.profile
        : routingCatalog?.stage_policy?.[stage];
    const id = config?.model_override || routingCatalog?.registry?.[profile]?.model || state.selectedModel || modelCatalog.default_model;
    activeModel = id;
    const entry = modelCatalog.models.find(item => item.id === id);
    const label = entry?.label || '模型设置';
    const capability = routingCatalog?.execution?.models?.[id];
    let inherited = entry?.preset_reasoning || routingCatalog?.registry?.[profile]?.reasoning || 'medium';
    if (entry?.provider === 'deepseek' && ['medium','xhigh'].includes(inherited)) inherited = 'high';
    if (capability && !capability.reasoning_efforts.includes(inherited)) inherited = capability.reasoning_efforts.at(-1);
    const effort = config?.reasoning_overrides?.[id] || inherited;
    name.textContent = `${label.replace(/（recommend）|\(recommend\)/g, '').trim()}${capability ? ` · ${effortLabels[effort] || effort}` : ''}`;
    if (entry) dom.modelSelect.value = id;
    effortSelect.replaceChildren();
    for (const value of ['', ...(capability?.reasoning_efforts || [])]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value ? effortLabels[value] || value : `跟随模型策略${capability ? ` · ${effortLabels[inherited] || inherited}` : ''}`;
      effortSelect.append(option);
    }
    effortSelect.value = config?.reasoning_overrides?.[id] || '';
    effortSelect.disabled = !capability || !config || dom.modelSelect.disabled || document.getElementById('saveModelStrategy').disabled;
    const budget = capability?.budgets?.[effort];
    budgetHelp.textContent = budget
      ? `单次输出上限：${budget.max_output_tokens.toLocaleString('en-US')} tokens；整轮输出上限：${budget.turn_output_tokens.toLocaleString('en-US')} tokens；最多调用：${budget.max_model_calls} 次。包含思考与回复，解析及修复共用整轮预算；不含输入、翻译和后台反馈分析。`
      : '连接支持思考强度设置的后端后显示限额。';
    if (budget && config?.adaptive_enabled && !config.model_override && !config.reasoning_overrides?.[id]) {
      budgetHelp.textContent += ' 自动升级启用时，此处为基础预算；实际升级预算记录在评测中。';
    }
    trigger.disabled = dom.sendButton.disabled;
    position();
  };
  effortSelect.addEventListener('change', () => {
    if (!activeModel || effortSelect.disabled) return;
    const overrides = {...(state.modelConfig.reasoning_overrides || {})};
    if (effortSelect.value) overrides[activeModel] = effortSelect.value;
    else delete overrides[activeModel];
    state.modelConfig = {...state.modelConfig, reasoning_overrides: overrides};
    saveState(); renderModelSelection();
  });
  panel.addEventListener('beforetoggle', event => {
    if (event.newState === 'open') position();
  });
  panel.addEventListener('toggle', event => {
    trigger.setAttribute('aria-expanded', String(event.newState === 'open'));
    if (event.newState === 'open') close.focus({preventScroll:true});
  });
  close.addEventListener('click', () => { panel.hidePopover(); trigger.focus(); });
  window.addEventListener('resize', position);
  window.addEventListener('ece329:design-changed', () => { if (panel.matches(':popover-open')) panel.hidePopover(); });
  window.updateModelPopover();
})();
