"use strict";

(() => {
  const el = Object.fromEntries(["Dialog", "Close", "Button", "Form", "Category", "Message", "Submit", "Status", "Refresh", "History", "Scope", "Target", "Switch", "SwitchPanel", "RetryTicket", "RetryModel", "SwitchHint", "SwitchRun"]
    .map(name => [name, document.getElementById(`feedback${name}`)]));
  const labels = { queued: "已保存，等待分析", running: "后台分析中", candidate: "已提炼经验，等待审阅",
    active: "经验已启用", rejected: "经审阅未采用", disabled: "经验已停用", duplicate: "分析完成：同类经验已存在",
    no_learning: "分析完成，未提炼新经验", failed: "反馈已保存，分析未完成", deleted: '经验已删除，保留审阅记录' };
  let client = null;
  let generation = -1;
  let timer = null;
  let polls = 0;
  let refreshing = false;
  let refreshPending = false;
  let reportTarget = {};
  let openingTarget = null;
  let savedTickets = [];
  const current = bound => client === bound && generation === designGeneration && state.designId === bound?.designId;
  const status = text => { el.Status.textContent = text; };
  const stopPolling = () => { clearTimeout(timer); timer = null; };
  function controls(disabled) {
    for (const name of ["Message", "Category", "Submit", "Scope"]) el[name].disabled = disabled;
  }
  function saveDraft() {
    if (client && current(client)) client.edit(el.Message.value, el.Category.value, {...reportTarget, scope: el.Scope.value || 'global'});
  }
  function updateSwitcher(bound, preferOther = false) {
    if (!current(bound)) return;
    const previousTicket=el.RetryTicket.value, previousModel=el.RetryModel.value;
    const tickets=savedTickets.filter(ticket=>ticket.can_retry);
    el.RetryTicket.replaceChildren();
    for (const ticket of tickets) {
      const option=document.createElement('option');option.value=ticket.id;
      option.textContent=`${ticket.message.slice(0,45)} · ${ticket.id.slice(0,8)} · ${ticket.attempts}/${ticket.max_attempts ?? 3}`;
      el.RetryTicket.append(option);
    }
    const ticket=previousTicket ? tickets.find(item=>item.id===previousTicket) : tickets[0];
    if(previousTicket && !ticket) {
      const option=document.createElement('option');option.value=previousTicket;option.disabled=true;
      option.textContent='所选记录当前不可重试，请重新选择。';el.RetryTicket.append(option);
    }
    el.RetryTicket.value=ticket?.id || previousTicket || '';
    const models=bound.analysisOptions?.models || [];
    const previousProvider=ticket?.last_analysis?.provider || models[0]?.provider;
    const alternatives=models.filter(model=>model.provider!==previousProvider);
    el.RetryModel.replaceChildren();
    for (const model of alternatives) {
      const option=document.createElement('option');option.value=model.id;
      option.textContent=`${model.provider === 'deepseek' ? 'DeepSeek' : 'OpenAI'} · ${model.label}`;
      el.RetryModel.append(option);
    }
    el.RetryModel.value=(!preferOther && alternatives.find(model=>model.id===previousModel)?.id) || alternatives[0]?.id || '';
    el.SwitchHint.textContent=bound.analysisOptions === undefined ? '正在读取……'
      : bound.analysisOptions === null ? '当前后端尚不支持切换分析 API，请更新后端并刷新状态。'
      : previousTicket && !ticket ? '所选记录当前不可重试，请重新选择。'
      : !ticket ? '没有可重试的失败记录；运行中或已达上限的记录不能重试。'
      : !alternatives.length ? '没有其他已配置的分析 API；请维护者检查备用 API 密钥及允许模型列表。'
      : '点击下方按钮后开始分析；打开此面板不会调用模型。';
    el.RetryTicket.disabled=el.RetryModel.disabled=Boolean(bound.retryBusy);
    el.SwitchRun.disabled=Boolean(bound.retryBusy) || !ticket || !alternatives.length;
  }
  async function retryTicket(bound, id, model) {
    if (!current(bound) || bound.retryBusy) return;
    bound.retryBusy=true;
    for (const button of el.History.querySelectorAll('button')) button.disabled=true;
    updateSwitcher(bound);
    try {
      await bound.retry(id,model);
      if (current(bound)) {
        status('已提交重试；请查看记录中的分析状态。');polls=0;await refresh();
      }
    } catch(error) {
      if(current(bound)) status(`重试失败：${error.message}`);
    } finally {
      bound.retryBusy=false;
      if(current(bound)) {
        for (const button of el.History.querySelectorAll('button')) button.disabled=false;
        updateSwitcher(bound);
      }
    }
  }
  function renderTickets(tickets, bound) {
    savedTickets=tickets;
    el.History.replaceChildren();
    if (!tickets.length) {
      const item = document.createElement("li");
      item.textContent = "尚无反馈记录。";
      el.History.append(item);
    }
    for (const ticket of tickets) {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = labels[ticket.status] || ticket.status;
      const body = document.createElement("p");
      body.textContent = ticket.message;
      const detail = document.createElement("small");
      detail.textContent = `记录 ${ticket.id} · 设计版本 ${ticket.revision} · 分析 ${ticket.attempts}/${ticket.max_attempts ?? 3} 次${ticket.durable ? "" : " · 内存模式，重启后丢失"}`;
      item.append(title, body, detail);
      if (ticket.error) {
        const error = document.createElement("p");
        error.textContent = ticket.error;
        item.append(error);
      }
      if (ticket.can_retry) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "ghost-button";
        retry.textContent = "重试分析";
        retry.disabled = Boolean(bound.retryBusy);
        retry.addEventListener("click", async () => {
          await retryTicket(bound,ticket.id);
        });
        item.append(retry);
      }
      el.History.append(item);
    }
    updateSwitcher(bound);
  }
  async function refresh() {
    const bound = client;
    if (!el.Dialog.open || !current(bound)) return;
    if (refreshing) { refreshPending = true; return; }
    refreshPending = false;
    stopPolling();
    refreshing = true;
    try {
      const tickets = await bound.list();
      if (!current(bound) || !el.Dialog.open) return;
      renderTickets(tickets, bound);
      if (tickets.some(ticket => ["queued", "running"].includes(ticket.status)) && polls++ < 60) {
        timer = setTimeout(refresh, 5000);
      }
    } catch (error) {
      if (current(bound) && el.Dialog.open) status(`读取反馈记录失败：${error.message}。可点击“刷新状态”重试。`);
    } finally {
      refreshing = false;
      if ((refreshPending || client !== bound) && el.Dialog.open && current(client)) {
        stopPolling();
        timer = setTimeout(refresh, 0);
      }
    }
  }
  el.Button.addEventListener("click", () => {
    el.Dialog.showModal();
    el.SwitchPanel.hidden=true;
    el.Switch.setAttribute('aria-expanded','false');
    el.Switch.disabled=true;
    if (!apiBase() || state.sessionKind !== "api" || !state.designId) {
      controls(true);
      el.Refresh.disabled = true;
      el.History.replaceChildren();
      status("请先连接课程服务并开始一份设计，再提交反馈。本地示例无法保存到后端。");
      return;
    }
    if (!current(client)) {
      savedTickets=[];
      el.RetryTicket.value=el.RetryModel.value='';
      generation = designGeneration;
      client = new window.FeedbackClient({ designId: state.designId, request: authorizedDesignApiRequest,
        storage: { getItem: key => sessionStorage.getItem(key), setItem: (key, value) => sessionStorage.setItem(key, value), removeItem: key => sessionStorage.removeItem(key) },
        storageKey: `ece329-feedback:${apiBase()}:${state.designId}` });
    }
    el.Message.value = client.draft?.message || "";
    el.Category.value = client.draft?.category || "other";
    el.Scope.value = client.draft?.scope || 'global';
    reportTarget = openingTarget || Object.fromEntries(['stage','revision','telemetry_id'].filter(key => client.draft?.[key] != null).map(key => [key, client.draft[key]]));
    openingTarget = null;
    el.Target.textContent = reportTarget.stage ? `反馈目标：${reportTarget.stage} · 版本 ${reportTarget.revision ?? '当前'}` : '反馈目标：当前设计';
    controls(client.busy);
    el.Refresh.disabled = false;
    el.Switch.disabled = false;
    status(client.busy ? "正在提交，请稍候。" : "反馈会附带当前设计的相关内容和最近四轮对话，便于定位问题。");
    polls = 0;
    refresh();
  });
  el.Form.addEventListener("submit", async event => {
    event.preventDefault();
    const bound = client;
    if (!current(bound) || bound.busy) return;
    saveDraft();
    if (!bound.draft?.message.trim()) { status("请先描述问题。"); return; }
    controls(true);
    status("正在保存反馈……");
    try {
      const receipt = await bound.submit();
      if (!current(bound)) return;
      el.Message.value = "";
      reportTarget = {};
      el.Target.textContent = '反馈目标：当前设计';
      status(`提交成功：反馈已保存，处理状态见下方记录。${receipt.durable ? "" : "当前后端使用内存存储，重启后记录会丢失。"}`);
      polls = 0;
      await refresh();
    } catch (error) {
      if (current(bound)) status(`尚未确认提交成功：${error.message}。内容已保留，可直接重试，不会重复创建记录。`);
    } finally { if (current(bound)) controls(false); }
  });
  el.Message.addEventListener("input", saveDraft);
  el.Category.addEventListener("change", saveDraft);
  el.Scope.addEventListener('change', saveDraft);
  el.Refresh.addEventListener("click", () => { polls = 0; refresh(); });
  el.Switch.addEventListener('click',()=>{
    if(!current(client)) return;
    el.SwitchPanel.hidden=!el.SwitchPanel.hidden;
    el.Switch.setAttribute('aria-expanded',String(!el.SwitchPanel.hidden));
    updateSwitcher(client);
  });
  el.RetryTicket.addEventListener('change',()=>updateSwitcher(client,true));
  el.SwitchRun.addEventListener('click',async()=>{
    if(el.SwitchRun.disabled || !savedTickets.some(ticket=>ticket.id===el.RetryTicket.value && ticket.can_retry)) return;
    await retryTicket(client,el.RetryTicket.value,el.RetryModel.value);
  });
  el.Close.addEventListener("click", () => el.Dialog.close());
  el.Dialog.addEventListener("close", stopPolling);
  window.addEventListener("ece329:design-changed", () => {
    stopPolling();
    el.Dialog.close();
    client = null;
    savedTickets = [];
    reportTarget = {};
    openingTarget = null;
  });
  window.addEventListener('ece329:report-output', event => {
    if (event.detail.designId !== state.designId || client?.busy) return;
    openingTarget = Object.fromEntries(['stage','revision','telemetry_id'].filter(key => event.detail[key] != null).map(key => [key, event.detail[key]]));
    el.Button.click();
  });
})();
