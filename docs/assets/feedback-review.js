"use strict";

(() => {
  const el = Object.fromEntries(["Login", "Token", "Filter", "Logout", "Status", "Cards", "Previous", "Next"]
    .map(name => [name, document.getElementById(`review${name}`)]));
  const base = String(window.ECE329_CONFIG?.API_BASE_URL || "").trim().replace(/\/$/, "");
  window.requestDisplayTranslation = async (texts, language) => {
    if (!base || !el.Token.value.trim()) throw new Error('Enter a maintainer token to translate review evidence.');
    const controller = new AbortController();
    const token = el.Token.value.trim(), pieces = [], output = texts.map(()=>[]);
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
    try {
      if(pieces.length>96) throw new Error('Review display is too large');
      for(let start=0;start<pieces.length;) {
        if(el.Token.value.trim()!==token) throw new Error('Review credentials changed');
        const batch=[];let size=0;
        while(start<pieces.length && batch.length<24 && size+pieces[start].text.length<=12000) {
          size+=pieces[start].text.length;batch.push(pieces[start++]);
        }
        const response = await fetch(`${base}/v1/localization`,{method:'POST',signal:controller.signal,
          headers:{'Content-Type':'application/json','X-ECE329-Feedback-Admin-Token':token},
          body:JSON.stringify({texts:batch.map(p=>p.text),language})});
        if (!response.ok) throw new Error('Translation unavailable');
        const result=await response.json();
        if(result.translations?.length!==batch.length) throw new Error('Incomplete translation');
        batch.forEach((piece,i)=>output[piece.index].push(result.translations[i]));
      }
      return {language,translations:output.map(parts=>parts.join('\n'))};
    } finally {clearTimeout(timeout);}
  };
  window.ECE329I18n?.refresh();
  let offset = 0;
  let generation = 0;
  let loading = false;
  let reviewing = false;
  const controllers = new Set();
  function invalidate() {
    generation++;
    for (const controller of controllers) controller.abort();
    controllers.clear();
    el.Cards.replaceChildren();
    el.Previous.disabled = true;
    el.Next.disabled = true;
  }
  async function request(path, options = {}) {
    if (!base) throw new Error("请先在工作台配置文件中设置课程服务地址。");
    if (!el.Token.value.trim()) throw new Error("请填写维护者令牌。");
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(`${base}/v1/feedback/experiences${path}`, { ...options, signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-ECE329-Feedback-Admin-Token": el.Token.value.trim() } });
      const body = await response.json();
      if (!response.ok) throw new Error(response.status === 409 ? "记录已变化或与已有经验重复，请重新加载核对。"
        : response.status === 401 ? "维护者令牌无效，或后端尚未配置审阅权限。" : body.detail || body.error || `HTTP ${response.status}`);
      return body;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  const node = (tag, text) => { const item = document.createElement(tag); item.textContent = text; return item; };
  function render(items, version) {
    el.Cards.replaceChildren();
    for (const item of items) {
      const card = node("article", "");
      card.className = "experience-card";
      card.append(node("h2", item.content.summary), node("p", `${item.id} · ${item.status} · 版本 ${item.version}`));
      const scope = node('select', '');
      for (const [value, label] of [['session','本次设计'],['project','本课程项目'],['global','通用经验']]) {
        const option = node('option', label); option.value = value; scope.append(option);
      }
      scope.value = item.evidence.scope || 'global';
      scope.id = `scope-${item.id}`;
      const scopeLabel = node('label', '审阅后的适用范围'); scopeLabel.htmlFor = scope.id;
      scope.disabled = !['candidate','disabled'].includes(item.status);
      card.append(scopeLabel, scope);
      const evidence = node("details", "");
      evidence.append(node("summary", "查看反馈原文、设计证据及审阅记录"), node("pre", JSON.stringify({ evidence: item.evidence, reviews: item.reviews }, null, 2)));
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
      const noteLabel = node("label", "3. 对经验层 Agent 总结的经验的处理意见（至少 5 个字符）");
      noteLabel.htmlFor = note.id;
      note.placeholder = "例如：修改后采用；归因应改为未承接确认，仅用于上一轮明确邀请确认继续的情形。未进行真实回放。";
      const field = node('select', '');
      field.id = `correction-field-${item.id}`;
      for (const [value, title] of [['summary','经验摘要'],['trigger','适用条件'],['recommendation','处理建议'],['verification','验证方法']]) {
        const option = node('option', title); option.value = value; field.append(option);
      }
      field.value = 'summary';
      const fieldLabel = node('label', '人工审阅：选择需要修正的字段'); fieldLabel.htmlFor = field.id;
      const original = node('textarea', '');
      original.id = `original-${item.id}`; original.rows = 3; original.readOnly = true;
      original.value = item.content.summary;
      const originalLabel = node('label', '1. Agent 原不恰当内容（原文；无需修改时保留原文）'); originalLabel.htmlFor = original.id;
      const corrected = node('textarea', '');
      corrected.id = `corrected-${item.id}`; corrected.rows = 4; corrected.maxLength = 600;
      const correctedLabel = node('label', '2. 人工修改后的正确内容（无需修改时复制原文）'); correctedLabel.htmlFor = corrected.id;
      const drafts = {};
      let previousField = field.value;
      field.addEventListener('change', () => {
        let content;
        if (!editor.disabled) {
          try {
            content = JSON.parse(editor.value);
            if (!content || typeof content !== 'object' || Array.isArray(content)) throw new Error();
            if (corrected.value.trim()) content[previousField] = corrected.value.trim();
          } catch {
            field.value = previousField;
            el.Status.textContent = '经验 JSON 格式有误，请先修正；已填写的修正内容仍保留。';
            editor.focus(); return;
          }
          editor.value = JSON.stringify(content, null, 2);
        }
        drafts[previousField] = corrected.value; previousField = field.value;
        original.value = item.content[field.value];
        corrected.value = content && content[field.value] !== item.content[field.value]
          ? content[field.value] : drafts[field.value] || '';
        corrected.maxLength = {summary:600,trigger:140,recommendation:260,verification:120}[field.value];
      });
      card.append(evidence, node('p', '模型正反例检查不等于真实回放；检查依据见展开的设计证据。'), label, editor,
        fieldLabel, field, originalLabel, original, correctedLabel, corrected,
        node('p', '切换字段会将已填写的修正同步到 JSON，启用时一并保存。摘要用于概述，实际指导设计的是适用条件、处理建议和验证方法，请同步核对。已启用修订供后续提炼参考，停用后停止引用。'), noteLabel, note,
        node('p', '说明这条经验应如何修改、保留、限制适用范围或不予采用，以及经验总结需要改进的地方。实际处理结果由下方操作按钮决定。'));
      const actions = node("div", "");
      actions.className = "experience-actions";
      const choices = item.status === "candidate" ? [["approve", "启用经验"], ["reject", "不采用"]]
        : item.status === "active" ? [["disable", "停用经验"]] : item.status === "disabled" ? [["approve", "重新启用"]] : [];
      if (item.status !== 'deleted') choices.push(['delete','删除经验']);
      editor.disabled = !["candidate", "disabled"].includes(item.status);
      note.disabled = !choices.length;
      field.disabled = corrected.disabled = !choices.length;
      for (const [decision, title] of choices) {
        const button = node("button", title);
        button.type = "button";
        button.className = "ghost-button";
        button.addEventListener("click", async () => {
          if (version !== generation || reviewing || loading) return;
          if (!corrected.value.trim()) { el.Status.textContent = "请先填写人工修改后的正确内容；无需修改时复制原文。"; corrected.focus(); return; }
          if (note.value.trim().length < 5) { el.Status.textContent = "请先填写至少 5 个字符的经验处理意见。"; note.focus(); return; }
          reviewing = true;
          const buttons = [...el.Cards.querySelectorAll("button")];
          buttons.forEach(control => { control.disabled = true; });
          try {
            const content = decision === "approve" ? JSON.parse(editor.value) : undefined;
            const result = await request(`/${encodeURIComponent(item.id)}/review`, { method: "POST",
              body: JSON.stringify({ decision, version: item.version,
                note: {field:field.value, original:original.value, corrected:corrected.value.trim(), opinion:note.value.trim()},
                ...(decision === 'approve' ? {scope: scope.value} : {}), ...(content ? { content } : {}) }) });
            if (version !== generation) return;
            el.Status.textContent = `已保存：${result.status}，版本 ${result.version}。`;
            await load(false);
          } catch (error) { if (version === generation) el.Status.textContent = `审阅未确认成功：${error.message} 请重新加载记录核对状态。`; }
          finally { reviewing = false; if (version === generation) buttons.forEach(control => { control.disabled = false; }); }
        });
        actions.append(button);
      }
      card.append(actions);
      el.Cards.append(card);
    }
    if (!items.length) el.Cards.append(node("p", "该状态下暂无经验。"));
  }
  async function load(showStatus = true) {
    const version = ++generation;
    loading = true;
    el.Previous.disabled = true;
    el.Next.disabled = true;
    if (showStatus) el.Status.textContent = "正在读取……";
    try {
      const result = await request(`?status=${encodeURIComponent(el.Filter.value)}&offset=${offset}`);
      if (version !== generation) return;
      render(result.experiences, version);
      el.Previous.disabled = offset === 0;
      el.Next.disabled = result.experiences.length < 50;
      if (showStatus) el.Status.textContent = `已加载 ${result.experiences.length} 条经验，第 ${offset / 50 + 1} 页。`;
    } catch (error) { if (version === generation) { el.Cards.replaceChildren(); el.Status.textContent = `读取失败：${error.message}`; } }
    finally { if (version === generation) loading = false; }
  }
  el.Login.addEventListener("submit", event => { event.preventDefault(); if (reviewing) return; offset = 0; load(); });
  el.Filter.addEventListener("change", () => { invalidate(); loading = false; offset = 0; el.Status.textContent = "点击“加载记录”查看所选状态。"; });
  el.Token.addEventListener("input", () => { invalidate(); loading = false; });
  el.Logout.addEventListener("click", () => { invalidate(); loading = false; el.Token.value = ""; el.Status.textContent = "令牌已清除。"; });
  el.Previous.addEventListener("click", () => { if (!loading && !reviewing) { offset = Math.max(0, offset - 50); load(); } });
  el.Next.addEventListener("click", () => { if (!loading && !reviewing) { offset += 50; load(); } });
})();
