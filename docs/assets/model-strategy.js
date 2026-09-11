"use strict";
(() => {
  const el = Object.fromEntries(['modelStrategy', 'experienceEnabled', 'adaptiveRoutingEnabled', 'saveModelStrategy', 'exportTelemetry',
    'strategyStatus', 'stageRoutingDetails', 'defaultModelProfile', 'clearModelOverride', 'stageRoutingRows']
    .map(id => [id, document.getElementById(id)]));
  let saving = false;
  const copy = value => structuredClone(value);
  function profiles(select, selected, inherited = false) {
    select.replaceChildren();
    const choices = [...(inherited ? [['', '沿用默认能力']] : []), ...Object.entries(routingCatalog.registry)
      .map(([id, item]) => [id, `${id} · ${modelCatalog.models.find(m => m.id === item.model)?.label || item.model} · ${item.reasoning}`])];
    for (const [value, label] of choices) {
      const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option);
    }
    select.value = selected;
  }
  function changed() { saveState(); renderModelSelection(); }
  window.renderModelStrategy = () => {
    const disabled = !routingCatalog?.enabled || dom.sendButton.disabled || saving;
    for (const name of ['modelStrategy', 'experienceEnabled', 'adaptiveRoutingEnabled', 'saveModelStrategy', 'defaultModelProfile', 'clearModelOverride']) el[name].disabled = disabled;
    el.exportTelemetry.disabled = !state.designId || state.sessionKind !== 'api' || dom.sendButton.disabled;
    if (!routingCatalog?.enabled || !state.modelConfig) { el.stageRoutingDetails.hidden = true; window.updateModelPopover?.(); return; }
    el.modelStrategy.value = state.modelConfig.strategy;
    el.experienceEnabled.checked = state.modelConfig.experience_enabled;
    el.adaptiveRoutingEnabled.checked = Boolean(state.modelConfig.adaptive_enabled);
    el.stageRoutingDetails.hidden = state.modelConfig.strategy !== 'custom';
    profiles(el.defaultModelProfile, state.modelConfig.profile);
    el.stageRoutingRows.replaceChildren();
    for (const [stage, title] of STAGES) {
      const label = document.createElement('label'); label.textContent = title;
      const select = document.createElement('select'); select.dataset.stage = stage; select.disabled = disabled;
      profiles(select, state.modelConfig.stage_overrides[stage] || '', true);
      select.addEventListener('change', () => {
        if (select.value) state.modelConfig.stage_overrides[stage] = select.value;
        else delete state.modelConfig.stage_overrides[stage];
        state.modelConfig.model_override = null; changed();
      });
      label.append(select); el.stageRoutingRows.append(label);
    }
    el.strategyStatus.textContent = state.pendingRequest ? '有待重试请求：重试保留原策略，新消息使用当前设置。'
      : state.modelConfig.model_override ? '当前固定使用所选模型。选择“使用能力路由”可恢复分阶段设置。'
      : '从下一条消息生效；直接选择回复模型将切换为 Custom。本地校验始终启用。';
    window.updateModelPopover?.();
  };
  el.modelStrategy.addEventListener('change', () => {
    if (!routingCatalog?.enabled) return;
    state.modelConfig = {...copy(state.modelConfig || routingCatalog.defaults), strategy: el.modelStrategy.value, model_override: null};
    if (state.modelConfig.strategy !== 'custom') state.modelConfig.stage_overrides = {};
    changed();
  });
  el.defaultModelProfile.addEventListener('change', () => { state.modelConfig.profile = el.defaultModelProfile.value; state.modelConfig.model_override = null; changed(); });
  el.clearModelOverride.addEventListener('click', () => { state.modelConfig.model_override = null; changed(); });
  el.experienceEnabled.addEventListener('change', () => { state.modelConfig.experience_enabled = el.experienceEnabled.checked; changed(); });
  el.adaptiveRoutingEnabled.addEventListener('change', () => { state.modelConfig.adaptive_enabled = el.adaptiveRoutingEnabled.checked; changed(); });
  el.saveModelStrategy.addEventListener('click', async () => {
    if (saving || !routingCatalog?.enabled) return;
    if (!state.designId) { saveState(); el.strategyStatus.textContent = '已保存在本机，开始设计时提交。'; return; }
    const generation = designGeneration, config = copy(state.modelConfig), id = state.designId;
    saving = true; window.renderModelStrategy();
    try {
      const result = await authorizedDesignApiRequest(`/v1/designs/${encodeURIComponent(id)}/model-config`, {
        method: 'PATCH', body: JSON.stringify({config, version: state.modelConfigVersion || 0})});
      if (generation !== designGeneration || id !== state.designId) return;
      state.modelConfigVersion = result.version; saveState();
      el.strategyStatus.textContent = '策略已保存，设计阶段未推进。';
    } catch (error) {
      if (generation === designGeneration) el.strategyStatus.textContent = `保存失败：${error.message}。请刷新设计后重试。`;
    } finally {
      saving = false;
      if (generation === designGeneration) { const status = el.strategyStatus.textContent; window.renderModelStrategy(); el.strategyStatus.textContent = status; }
    }
  });
  el.exportTelemetry.addEventListener('click', async () => {
    if (!state.designId || state.sessionKind !== 'api') return;
    const id = state.designId, generation = designGeneration, records = [];
    el.exportTelemetry.disabled = true;
    try {
      let offset = 0, page = 0;
      do {
        const result = await authorizedDesignApiRequest(`/v1/designs/${encodeURIComponent(id)}/telemetry?offset=${offset}`, {method: 'GET'});
        if (generation !== designGeneration) return;
        records.push(...result.records); offset = result.next_offset; page++;
      } while (offset !== null && page < 100);
      const url = URL.createObjectURL(new Blob([JSON.stringify({design_id: id, records, next_offset: offset}, null, 2)], {type:'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = `${id}-telemetry.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      el.strategyStatus.textContent = `已导出 ${records.length} 条记录${offset !== null ? '；更多记录可按文件内 next_offset 继续读取' : ''}。`;
    } catch (error) { if (generation === designGeneration) el.strategyStatus.textContent = `导出失败：${error.message}`; }
    finally { if (generation === designGeneration) el.exportTelemetry.disabled = false; }
  });
  window.renderModelStrategy();
})();
