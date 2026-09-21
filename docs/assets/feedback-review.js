"use strict";

(() => {
  const el = Object.fromEntries(["Login", "Token", "Filter", "Logout", "Status", "Cards", "Previous", "Next", "Usage"]
    .map(name => [name, document.getElementById(`review${name}`)]));
  const base = String(window.ECE329_CONFIG?.API_BASE_URL || "").trim().replace(/\/$/, "");
  const translationControllers = new Set();
  function cancelTranslations() {
    for(const controller of translationControllers)controller.abort();
    translationControllers.clear();
  }
  window.addEventListener('ece329:language-changed',cancelTranslations);
  const passwordError = () => Object.assign(new Error('密码错误'), {code:'invalid_password'});
  function maintainerToken() {
    const raw = el.Token.value;
    // Keep malformed or oversized credentials out of HTTP headers. Never truncate them.
    if (raw.length > 1024 || !/^[\x20-\x7e]+$/.test(raw) || !raw.trim()) throw passwordError();
    return raw.trim();
  }
  function showFailure(target, error, prefix, suffix = '') {
    if (error.code === 'invalid_password') {
      target.textContent = '';
      el.Status.textContent = '';
      if (passwordAlertGeneration !== generation) {
        passwordAlertGeneration = generation;
        window.alert(window.ECE329I18n?.language === 'en' ? 'Incorrect password' : '密码错误');
      }
      el.Token.focus();
    } else target.textContent = `${prefix}${error.message}${suffix}`;
  }
  window.requestDisplayTranslation = async (texts, language) => {
    if (!base || !el.Token.value.trim()) throw new Error('Enter a maintainer token to translate review evidence.');
    const controller = new AbortController();
    const token = maintainerToken(), version = generation, pieces = [], output = texts.map(()=>[]);
    texts.forEach((text,index)=>{
      for (let start=0;start<text.length;) {
        let end=Math.min(start+4000,text.length);
        if (end<text.length) {
          while(end>start && /[^\s\u3400-\u9fff]/.test(text[end-1]) && /[^\s\u3400-\u9fff]/.test(text[end])) end--;
          if(end===start) throw new Error('An indivisible display token is too long');
        }
        if(text.slice(start,end).trim()) pieces.push({index,text:text.slice(start,end)});
        start=end;
      }
    });
    const timeout = setTimeout(()=>controller.abort(),90000);
    translationControllers.add(controller);
    try {
      if(pieces.length>96) throw new Error('Review display is too large');
      for(let start=0;start<pieces.length;) {
        if(controller.signal.aborted || version!==generation || el.Token.value.trim()!==token) throw new Error('Review context changed');
        const batch=[];let size=0;
        while(start<pieces.length && batch.length<24 && size+pieces[start].text.length<=12000) {
          size+=pieces[start].text.length;batch.push(pieces[start++]);
        }
        const response = await fetch(`${base}/v1/localization`,{method:'POST',signal:controller.signal,
          headers:{'Content-Type':'application/json','X-ECE329-Feedback-Admin-Token':token},
          body:JSON.stringify({texts:batch.map(p=>p.text),language})});
        if (!response.ok) throw new Error('Translation unavailable');
        const result=await response.json();
        if(controller.signal.aborted || version!==generation || el.Token.value.trim()!==token) throw new Error('Review context changed');
        if(!Array.isArray(result.translations) || result.translations.length!==batch.length
            || result.translations.some(text=>typeof text!=='string'||!text.trim())) throw new Error('Incomplete translation');
        batch.forEach((piece,i)=>output[piece.index].push(result.translations[i]));
      }
      return {language,translations:output.map(parts=>parts.join('\n'))};
    } finally {clearTimeout(timeout);translationControllers.delete(controller);}
  };
  window.ECE329I18n?.refresh();
  let offset = 0;
  let generation = 0;
  let passwordAlertGeneration = -1;
  let loading = false;
  let reviewing = false;
  let usageView = false;
  const controllers = new Set();
  const drafts = new Map();
  const editors = new Map();
  function captureDrafts() { for (const [id, read] of editors) {const value=read();if(value)drafts.set(id,value);else drafts.delete(id);} }
  function clearCards() {
    captureDrafts(); editors.clear(); el.Cards.replaceChildren();
    cancelTranslations();
    window.ECE329I18n?.reset?.();
  }
  function appendEvidence(host, value) {
    window.ECE329ReviewEvidence.mount(host, value);
  }
  function invalidate() {
    generation++;
    for (const controller of controllers) controller.abort();
    controllers.clear();
    clearCards();
    el.Previous.disabled = true;
    el.Next.disabled = true;
  }
  async function request(path, options = {}, resource = 'experiences') {
    if (!base) throw new Error("请先在工作台配置文件中设置课程服务地址。");
    const token = maintainerToken();
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      let response;
      try {
        response = await fetch(`${base}/v1/feedback/${resource}${path}`, { ...options, signal: controller.signal,
          headers: { "Content-Type": "application/json", "X-ECE329-Feedback-Admin-Token": token } });
      } catch (error) {
        throw new Error(error.name === 'AbortError' ? '请求超时，请重试。' : '无法连接课程服务，请稍后重试。');
      }
      if ([401, 403, 431].includes(response.status)) throw passwordError();
      const body = await response.json();
      if (!response.ok) throw new Error(response.status === 409 ? "记录已变化或与已有经验重复，请重新加载核对。"
        : response.status === 404 && resource === 'tickets' && path.startsWith('?') ? "后端尚不支持全部反馈列表，请更新后端后重试。"
        : body.detail || body.error || `HTTP ${response.status}`);
      return body;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  const node = (tag, text) => { const item = document.createElement(tag); item.textContent = text; return item; };
  const sourceNode = (tag,text) => {const item=node(tag,text);item.setAttribute("data-i18n-ignore","");return item;};
  function usageSummary(value) {
    const item=node('p','');item.className='usage-summary';
    window.ECE329Usage?.summary(item,value);return item;
  }
  function usageBreakdown(usage) {
    const panel=node('details','');panel.className='usage-breakdown';
    panel.append(node('summary','按阶段、Agent 和 API 模型查看'));
    panel.append(node('p','阶段与模型用时按实际 API 调用计量；其余本地处理单列，避免重复计算。经验分析归入反馈目标阶段，翻译归入请求时所在阶段。'));
    for(const stage of usage?.stages || []) {
      const title=node('h4','');window.ECE329Usage?.identity(title,stage);
      panel.append(title,usageSummary(stage.usage));
      for(const row of (usage.breakdown || []).filter(row=>row.stage===stage.stage)) {
        const name=node('p','');name.className='usage-model';window.ECE329Usage?.identity(name,row);
        panel.append(name,usageSummary(row.usage));
      }
    }
    if(!usage?.stages?.length) panel.append(node('p','尚无阶段明细。'));
    return panel;
  }
  function renderUsage(result) {
    clearCards();
    el.Cards.append(node('p','费用是按后端配置单价计算的 USD 估算，实际扣费以服务商账单为准。活跃时长不含用户等待及排队时间；合计包括对话、经验提炼和设计关联翻译。'));
    for(const item of result.designs) {
      const card=node('article','');card.className='experience-card';
      card.append(node('h2',item.design_id),node('p',`${new Date(item.created*1000).toISOString()} · ${item.mode}`));
      const stop=node('p','');window.ECE329Usage?.stopState(stop,item);card.append(stop);
      if(!item.complete) card.append(node('p','此设计开始时尚未启用完整计量，仅展示已记录部分。'));
      card.append(node('h3','合计'),usageSummary(item.usage),node('h3','设计对话'),usageSummary(item.usage.dialogue),
        node('h3','经验提炼'),usageSummary(item.usage.feedback),node('h3','设计关联翻译'),usageSummary(item.usage.translation));
      card.append(usageBreakdown(item.usage));
      el.Cards.append(card);
    }
    if(!result.designs.length) el.Cards.append(node('p','暂无设计用量记录。'));
    if(!result.durable) el.Cards.append(node('p','当前后端使用内存存储，重启后记录会丢失。'));
  }
  const experienceLabels = {candidate:'待审阅', active:'已启用', stopped:'已停止', rejected:'已停止', disabled:'已停止', deleted:'已停止'};
  const experienceStatus = value => ['rejected','disabled','deleted'].includes(value) ? 'stopped' : value;
  const ticketLabels = {queued:'等待分析',running:'分析中',candidate:'分析完成，已生成经验',
    active:'分析完成，已生成经验',rejected:'分析完成，已生成经验',disabled:'分析完成，已生成经验',deleted:'分析完成，已生成经验',
    failed:'分析失败',no_learning:'未提炼出可审阅经验',duplicate:'与已有经验重复'};
  function renderTickets(items, version) {
    clearCards();
    for (const item of items) {
      const card = node('article',''); card.className='experience-card';
      card.append(node('h2',ticketLabels[item.status] || item.status), sourceNode('p',item.message),
        node('p',`${item.id} · ${item.design_id} · ${item.attempts}/${item.max_attempts ?? 3}`));
      if (item.error && !item.last_analysis?.diagnostic) card.append(node('p',item.error));
      card.append(usageSummary(item.usage));
      const detailBody = node('div','');
      let fetching=false, loaded=false;
      const loadEvidence = async()=>{
        if (fetching || loaded || version!==generation) return;
        fetching=true; detailBody.textContent='正在读取……';
        try {
          const detail=await request(`/${encodeURIComponent(item.id)}`,{},'tickets');
          if(version!==generation) return;
          detailBody.replaceChildren();
          appendEvidence(detailBody,detail);
          loaded=true;
        } catch(error) {if(version===generation) showFailure(detailBody,error,'读取失败：');}
        finally {fetching=false;}
      };
      card.append(detailBody);
      card.append(usageBreakdown(item.usage));
      if (item.experience) {
        card.append(node('p', `关联经验：${experienceLabels[item.experience.status] || item.experience.status}`));
        const open=node('button','查看关联经验'); open.type='button';open.className='ghost-button';
        open.addEventListener('click',()=>{
          if(version!==generation || reviewing || loading) return;
          el.Filter.value=experienceStatus(item.experience.status);offset=0;load(true,item.experience.id);
        });card.append(open);
      }
      if(item.can_retry) {
        const retry=node('button','重试分析');retry.type='button';retry.className='ghost-button';
        retry.addEventListener('click',async()=>{
          if(version!==generation || reviewing || loading) return;
          reviewing=true;retry.disabled=true;
          try {
            await request(`/${encodeURIComponent(item.id)}/retry`,{method:'POST',body:'{}'},'tickets');
            if(version===generation) await load();
          }catch(error){if(version===generation) showFailure(el.Status,error,'重试失败：');}
          finally{reviewing=false;retry.disabled=false;}
        });card.append(retry);
      }
      el.Cards.append(card);
      void loadEvidence();
    }
    if(!items.length) el.Cards.append(node('p','此筛选下没有反馈记录；可选择“全部反馈”查看其他处理状态。'));
  }
  function render(items, version) {
    clearCards();
    for (const item of items) {
      const card = node("article", "");
      card.className = "experience-card";
      const summary=node('h2',item.content.summary);summary.setAttribute('data-i18n-translate','');
      card.append(summary, node("p", `${item.id} · ${experienceLabels[item.status] || item.status} · 版本 ${item.version}`));
      card.append(usageSummary(item.usage));
      const scope = node('select', '');
      for (const [value, label] of [['session','本次设计'],['project','本课程项目'],['global','通用经验']]) {
        const option = node('option', label); option.value = value; scope.append(option);
      }
      scope.value = item.evidence.scope || 'global';
      scope.id = `scope-${item.id}`;
      const scopeLabel = node('label', '审阅后的适用范围'); scopeLabel.htmlFor = scope.id;
      scope.disabled = !['candidate','stopped'].includes(experienceStatus(item.status));
      card.append(scopeLabel, scope);
      const evidence = node("div", "");
      appendEvidence(evidence,item);
      const editor = node("textarea", "");
      editor.rows = 12;
      editor.value = JSON.stringify(item.content, null, 2);
      editor.id = `content-${item.id}`;
      const label = node("label", "经验内容（可编辑 JSON；modes、stages、keywords 控制检索范围）");
      label.htmlFor = editor.id;
      const note = node("textarea", "");
      note.rows = 3;
      note.maxLength = 2000;
      note.id = `note-${item.id}`;
      const noteLabel = node("label", "3. 对经验层 Agent 总结的经验的处理意见");
      noteLabel.htmlFor = note.id;
      note.placeholder = "如果没问题，请填写：总结没问题，批准加入经验层。也可用自己的话表达，如“没问题，继续”或“批准”。";
      const original = node('textarea', '');
      original.id = `original-${item.id}`; original.rows = 3; original.maxLength = 2000;
      const originalLabel = node('label', '1. Agent 原不恰当内容（请手动填写，可涉及多个字段；无需修改时留空）'); originalLabel.htmlFor = original.id;
      const corrected = node('textarea', '');
      corrected.id = `corrected-${item.id}`; corrected.rows = 4; corrected.maxLength = 2000;
      const correctedLabel = node('label', '2. 人工修改后的正确内容（无需修改时留空）'); correctedLabel.htmlFor = corrected.id;
      card.append(evidence, node('p', '模型正反例检查不等于真实回放；检查依据见展开的设计证据。'), label, editor,
        originalLabel, original, correctedLabel, corrected,
        node('p', '请在上方“经验内容”JSON 中手动同步修改相关字段；下方审阅记录不会自动改写 JSON。请同时核对 summary、trigger、recommendation 和 verification。启用时保存 JSON 和审阅记录，实际 JSON 修订供后续提炼参考，停止后不再引用。'), noteLabel, note,
        node('p', '说明这条经验应如何修改、保留或限制适用范围。停止时请说明原因，例如“总结不准确”“暂时停用”“被新经验替代”。实际处理结果由下方操作按钮决定。'));
      const saved = drafts.get(item.id);
      const controls = {content:editor,original,corrected,note,scope};
      const initial=JSON.stringify(Object.fromEntries(Object.entries(controls).map(([key,control])=>[key,control.value])));
      if (saved) for (const [key,control] of Object.entries(controls)) control.value=saved[key];
      let reviewVersion=saved?.version ?? item.version;
      const readDraft=()=>{const values=Object.fromEntries(Object.entries(controls).map(([key,control])=>[key,control.value]));return saved || JSON.stringify(values)!==initial ? {version:reviewVersion,...values} : null;};
      if(reviewVersion!==item.version) {
        const warning=node('p','记录版本已更新，草稿已保留。请核对新证据后再提交。');
        const acknowledge=node('button','已核对新版本，继续使用草稿');acknowledge.type='button';acknowledge.className='ghost-button';
        acknowledge.addEventListener('click',()=>{reviewVersion=item.version;drafts.set(item.id,readDraft());warning.textContent='';acknowledge.disabled=true;});
        card.append(warning,acknowledge);
      }
      editors.set(item.id,readDraft);
      for (const control of Object.values(controls)) for (const event of ['input','change']) control.addEventListener(event,captureDrafts);
      const actions = node("div", "");
      actions.className = "experience-actions";
      const currentStatus = experienceStatus(item.status);
      if (currentStatus === "active") note.placeholder = "停止时请填写具体原因，例如：总结不准确、暂时停用、被新经验替代。";
      const choices = currentStatus === "candidate" ? [["approve", "启用经验"], ["stop", "停止经验"]]
        : currentStatus === "active" ? [["stop", "停止经验"]] : currentStatus === "stopped" ? [["approve", "重新启用"]] : [];
      editor.disabled = !["candidate", "stopped"].includes(currentStatus);
      note.disabled = !choices.length;
      original.disabled = corrected.disabled = !choices.length;
      for (const [decision, title] of choices) {
        const button = node("button", title);
        button.type = "button";
        button.className = "ghost-button";
        button.addEventListener("click", async () => {
          if (version !== generation || reviewing || loading) return;
          if(reviewVersion!==item.version) {el.Status.textContent='记录版本已更新，草稿已保留。请核对新证据后再提交。';return;}
          if (Boolean(original.value.trim()) !== Boolean(corrected.value.trim())) { el.Status.textContent = "请成对填写原不当内容和正确内容；无需修改时两项均留空。"; (original.value.trim() ? corrected : original).focus(); return; }
          if (!note.value.trim()) { el.Status.textContent = decision === "stop" ? "请填写停止原因。" : "请填写处理意见；无需修改时可填写：总结没问题，批准加入经验层。"; note.focus(); return; }
          let content;
          if (decision === "approve") {
            try {
              content = JSON.parse(editor.value);
              if (!content || typeof content !== "object" || Array.isArray(content)) throw new Error();
            } catch {
              el.Status.textContent = "经验 JSON 格式有误，请先修正；已填写的修正内容仍保留。";
              editor.focus(); return;
            }
          }
          reviewing = true;
          const buttons = [...el.Cards.querySelectorAll("button")];
          buttons.forEach(control => { control.disabled = true; });
          try {
            const result = await request(`/${encodeURIComponent(item.id)}/review`, { method: "POST",
              body: JSON.stringify({ decision, version: reviewVersion,
                note: {original:original.value.trim(), corrected:corrected.value.trim(), opinion:note.value.trim()},
                ...(decision === 'approve' ? {scope: scope.value} : {}), ...(content ? { content } : {}) }) });
            if (version !== generation) return;
            el.Status.textContent = `已保存：${experienceLabels[result.status] || result.status}，版本 ${result.version}。`;
            editors.delete(item.id); drafts.delete(item.id);
            await load(false);
          } catch (error) { if (version === generation) showFailure(el.Status,error,'审阅未确认成功：',' 请重新加载记录核对状态。'); }
          finally { reviewing = false; if (version === generation) buttons.forEach(control => { control.disabled = false; }); }
        });
        actions.append(button);
      }
      card.append(actions);
      card.append(usageBreakdown(item.usage));
      el.Cards.append(card);
    }
    if (!items.length) el.Cards.append(node("p", "该状态下暂无经验，不代表没有收到反馈。请切换到“全部反馈”查看分析状态。"));
  }
  async function load(showStatus = true, experienceId = null) {
    captureDrafts();
    const version = ++generation;
    loading = true;
    el.Previous.disabled = true;
    el.Next.disabled = true;
    if (showStatus) el.Status.textContent = "正在读取……";
    try {
      if(usageView) {
        const result=await request(`?offset=${offset}`,{},'usage');
        if(version!==generation) return;
        renderUsage(result);el.Previous.disabled=offset===0;el.Next.disabled=offset+result.designs.length>=result.total;
        el.Status.textContent=`${offset+1}–${offset+result.designs.length} / ${result.total}`;
        return;
      }
      if(el.Filter.value.startsWith('feedback:')) {
        const filter=el.Filter.value.slice('feedback:'.length);
        const result=await request(`?offset=${offset}${filter==='all'?'':`&status=${encodeURIComponent(filter)}`}`,{},'tickets');
        if(version!==generation) return;
        renderTickets(result.feedback,version);
        el.Previous.disabled=offset===0;el.Next.disabled=offset+result.feedback.length>=result.filtered_total;
        const counts=Object.entries(result.counts).map(([status,count])=>`${ticketLabels[status]||status}: ${count}`).join(' · ');
        el.Status.textContent=`反馈总数：${result.total}；当前筛选：${result.filtered_total}；本页：${result.feedback.length}。 ${counts}`;
        if(!result.durable) el.Cards.append(node('p','当前后端使用内存存储，重启后记录会丢失。'));
        if(!result.total) el.Cards.append(node('p','当前服务没有反馈记录。若已提交，请核对前后端服务地址和持久化存储；不要重复提交。'));
        return;
      }
      const result = await request(experienceId ? `?experience_id=${encodeURIComponent(experienceId)}` : `?status=${encodeURIComponent(el.Filter.value)}&offset=${offset}`);
      if (version !== generation) return;
      render(result.experiences, version);
      el.Previous.disabled = offset === 0;
      el.Next.disabled = Boolean(experienceId) || result.experiences.length < 50;
      if (showStatus) el.Status.textContent = `已加载 ${result.experiences.length} 条经验，第 ${offset / 50 + 1} 页。`;
    } catch (error) { if (version === generation) { clearCards(); showFailure(el.Status,error,'读取失败：'); } }
    finally { if (version === generation) loading = false; }
  }
  el.Login.addEventListener("submit", event => { event.preventDefault(); if (reviewing) return; usageView=false;offset = 0; load(); });
  el.Usage.addEventListener('click',()=>{if(reviewing || loading) return;usageView=true;offset=0;load();});
  el.Filter.addEventListener("change", () => { invalidate(); usageView=false;loading = false; offset = 0; el.Status.textContent = "点击“加载记录”查看所选状态。"; });
  el.Token.addEventListener("input", () => { invalidate(); drafts.clear(); loading = false; });
  el.Logout.addEventListener("click", () => { invalidate(); drafts.clear(); loading = false; el.Token.value = ""; el.Status.textContent = "令牌已清除。"; });
  el.Previous.addEventListener("click", () => { if (!loading && !reviewing) { offset = Math.max(0, offset - 50); load(); } });
  el.Next.addEventListener("click", () => { if (!loading && !reviewing) { offset += 50; load(); } });
})();
