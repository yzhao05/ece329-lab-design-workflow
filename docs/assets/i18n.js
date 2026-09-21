"use strict";
(() => {
  const KEY = 'ece329-interface-language';
  let language = 'zh';
  try { language = localStorage.getItem(KEY) === 'en' ? 'en' : 'zh'; } catch {}
  const dictionary = new Map(Object.entries(window.ECE329_LOCALES || {}));
  const pageTitle = document.title;
  for (const [zh, en] of [...dictionary]) {
    const a = zh.split('\n\n'), b = en.split('\n\n');
    if (a.length === b.length) a.forEach((part, i) => dictionary.set(part, b[i]));
  }
  const ordered = [...dictionary].sort((a,b) => b[0].length-a[0].length);
  const originals = new WeakMap(), attributes = new WeakMap();
  const cache = new Map(), pending = new Set(), failed = new Set();
  let scheduled = false, busy = false, blocked = false, paused = false, dismissed = false, generation = 0;
  const cjk = /[\u3400-\u9fff]/;
  const retry = document.getElementById('translationRetry');
  const toggle = document.getElementById('languageToggle');
  let failureCode = '';
  const status = document.createElement('div');
  status.id = 'translationStatus';
  status.setAttribute('data-i18n-ignore', '');
  status.hidden = true;
  status.className = 'translation-status';
  const statusCopy = document.createElement('span'), closeStatus = document.createElement('button');
  statusCopy.setAttribute('role', 'status');
  closeStatus.id = 'translationStatusClose';
  closeStatus.type = 'button';
  closeStatus.textContent = '×';
  closeStatus.addEventListener('click', () => { dismissed = true; status.hidden = true; });
  status.append(statusCopy, closeStatus);
  const main = document.querySelector('main');
  if (main) main.before(status); else if (retry) retry.after(status);
  const failures = {
    output_truncated: ['翻译输出被截断，请检查输出上限，或换模型后重试。', 'Translation output was truncated. Check the output limit or select another model and retry.'],
    invalid_structure: ['译文格式不完整，请重试或更换模型。', 'The translation format was incomplete. Retry or select another model.'],
    untranslated_text: ['模型未完成全文翻译，请重试或更换模型。', 'The model did not translate all text. Retry or select another model.'],
    changed_reference: ['译文改变了数值或引用，已拒绝使用，请重试。', 'Translation changed a number or reference and was rejected. Please retry.'],
    model_timeout: ['翻译请求超时，请稍后重试。', 'The translation request timed out. Please retry later.'],
    client_timeout: ['浏览器等待翻译超时，请稍后重试。', 'The browser timed out waiting for translation. Please retry later.'],
    model_rate_limited: ['翻译服务限流，请稍后重试。', 'The translation service is rate limited. Please retry later.'],
    model_configuration_error: ['翻译服务配置有误，请维护者检查模型和 API 配置。', 'Translation is not configured correctly. Ask the maintainer to check the model and API configuration.'],
    model_request_rejected: ['服务商拒绝了翻译请求，请维护者检查模型名称、请求参数和 API 鉴权。', 'The provider rejected translation. Ask the maintainer to check the model, request parameters and API authentication.'],
    model_connection_error: ['后端无法连接模型服务，请维护者检查 API 地址和网络。', 'The backend cannot reach the model. Ask the maintainer to check the API address and network.'],
    network_error: ['无法连接翻译服务，请检查网络。', 'Cannot reach the translation service. Check your connection.'],
  };
  const key = text => `${language}\0${text}`;
  function local(text) {
    if (language === 'zh') return text;
    if (dictionary.has(text)) return dictionary.get(text);
    let result = text.replace(/阶段\s*(\d+)/g, 'Stage $1').replace(/（recommend）/g, ' (recommended)')
      .replace(/（快速预设）/g, ' (fast)').replace(/（深度思考预设）/g, ' (thinking)');
    for (const [from,to] of ordered) if (result.includes(from)) result = result.split(from).join(to);
    return cjk.test(result) ? null : result;
  }
  function translate(text, forceChinese = false) {
    if (!text.trim()) return text;
    const exact = local(text.trim());
    if (exact !== null && !forceChinese) return text.replace(text.trim(), exact);
    const id = key(text);
    if (cache.has(id)) return cache.get(id);
    if (!failed.has(text)) pending.add(text);
    if (!busy && !paused && !failed.has(text)) setTimeout(flush, 30);
    // Keep the conversation readable while waiting, and on failure. Never
    // replace source evidence with a repeated error or cache it as a translation.
    return text;
  }
  function skip(element) {
    if (!element || element.closest('script,style,textarea')) return true;
    // A prose leaf may opt into display translation inside a protected evidence
    // region. Sibling evidence, IDs, JSON and editor contents remain literal.
    const boundary = element.closest('[data-i18n-ignore],[data-i18n-translate]');
    return Boolean(boundary && !boundary.hasAttribute('data-i18n-translate'));
  }
  function renderRecord(record, forceChinese = false, englishOnly = false) {
    // Live nodes retain their completed translation even after the shared LRU
    // evicts it. Otherwise a page with >2000 strings requeues itself forever.
    record.translations ||= {};
    if (Object.hasOwn(record.translations, language)) return record.translations[language];
    const result = translate(record.source, forceChinese);
    if (cache.has(key(record.source)) || (!forceChinese && local(record.source.trim()) !== null)) {
      record.translations[language] = result;
    }
    if (englishOnly && language === 'en' && cjk.test(result)) {
      return paused || failed.has(record.source) ? 'Translation unavailable. Use Retry translation.' : 'Translating…';
    }
    if (forceChinese && !cache.has(key(record.source))) {
      return paused || failed.has(record.source) ? '翻译暂不可用，请重试。' : '正在翻译…';
    }
    return result;
  }
  function scan() {
    scheduled = false;
    document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
    const translatedTitle = translate(pageTitle);
    if (document.title !== translatedTitle) document.title = translatedTitle;
    if (toggle) {
      const label = language === 'en' ? '中文' : 'English', title = language === 'en' ? '切换到中文' : 'Switch to English';
      if (toggle.textContent !== label) toggle.textContent = label;
      if (toggle.getAttribute('aria-label') !== title) toggle.setAttribute('aria-label', title);
    }
    if (retry) {
      retry.hidden = !blocked;
      const label = language === 'en' ? 'Retry translation' : '重试翻译';
      if (retry.textContent !== label) retry.textContent = label;
    }
    status.hidden = dismissed || (!blocked && !busy && !pending.size);
    const closeLabel = language === 'en' ? 'Dismiss translation notice' : '关闭翻译提示';
    if (closeStatus.getAttribute('aria-label') !== closeLabel) closeStatus.setAttribute('aria-label', closeLabel);
    const detail = failures[failureCode] || ['翻译暂不可用，请重试；若持续失败，请检查后端翻译日志。', 'Translation is unavailable. Retry; if it persists, check the backend translation logs.'];
    const statusText = blocked
      ? (language === 'en' ? 'Some content could not be translated. ' : '部分内容尚未完成翻译。') + detail[language === 'en' ? 1 : 0]
      : (language === 'en' ? 'Translating display text…' : '正在翻译显示内容…');
    if (statusCopy.textContent !== statusText) statusCopy.textContent = statusText;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (skip(node.parentElement) || node.parentElement === retry) continue;
      let record = originals.get(node);
      if (!record || node.nodeValue !== record.rendered) record = {source: node.nodeValue};
      // English replies received while English was selected can also be read
      // in Chinese. User input controls and original stored messages stay intact.
      const prose = Boolean(node.parentElement.closest('[data-i18n-translate]'));
      const force = language === 'zh' && !cjk.test(record.source)
        && (prose ? /[a-z]/i.test(record.source) : /[a-z]{3} [a-z]{3}/i.test(record.source)
          && Boolean(node.parentElement.closest('.message-bubble')) && record.source.split(/\s+/).length > 8);
      record.rendered = renderRecord(record, force, Boolean(node.parentElement.closest('[data-i18n-english-only],[data-i18n-translate]')));
      originals.set(node, record);
      if (node.nodeValue !== record.rendered) node.nodeValue = record.rendered;
    }
    for (const element of document.querySelectorAll('[placeholder],[title],[aria-label],optgroup[label]')) {
      if (element.closest('script,style,[data-i18n-ignore]')) continue;
      const records = attributes.get(element) || {};
      for (const attr of ['placeholder','title','aria-label','label']) {
        if (!element.hasAttribute(attr)) continue;
        const value = element.getAttribute(attr);
        let record = records[attr];
        if (!record || value !== record.rendered) record = {source:value};
        record.rendered = renderRecord(record, false, Boolean(element.closest('[data-i18n-english-only]')));
        records[attr] = record;
        if (value !== record.rendered) element.setAttribute(attr,record.rendered);
      }
      attributes.set(element, records);
    }
  }
  function schedule() { if (!scheduled) { scheduled = true; queueMicrotask(scan); } }
  async function flush() {
    if (busy || paused || !pending.size) return;
    if (!window.requestDisplayTranslation) return; // App wires transport after loading.
    busy = true;
    schedule();
    const epoch = generation, target = language;
    const batch = []; let length = 0;
    for (const text of pending) {
      if (batch.length >= 24 || batch.length && length + text.length > 6000) break;
      batch.push(text); length += text.length;
    }
    try {
      if (!batch.length) throw new Error('Display text exceeds translation limit');
      const result = await window.requestDisplayTranslation(batch, target);
      if (epoch !== generation) return;
      if (!Array.isArray(result.translations) || result.translations.length !== batch.length
          || result.translations.some(t => typeof t !== 'string' || !t.trim() || target === 'en' && cjk.test(t))) throw new Error('Incomplete translation');
      batch.forEach((text,i) => { cache.set(`${target}\0${text}`,result.translations[i]); pending.delete(text); });
      while (cache.size > 2000) cache.delete(cache.keys().next().value);
    } catch (error) {
      if (epoch === generation) {
        blocked = true;
        failureCode = error?.details?.translation_reason || error?.code || (error?.name === 'AbortError' ? 'client_timeout' : '');
        if (failureCode === 'model_output_invalid') failureCode = 'invalid_structure';
        if (failureCode === 'rate_limit_exceeded') failureCode = 'model_rate_limited';
        batch.forEach(text => { failed.add(text); pending.delete(text); });
        // A rejected text batch must not block unrelated sidebar or chat text.
        // Transport failures pause the queue; validation failures skip this batch.
        paused = !['invalid_structure','changed_reference','untranslated_text','output_truncated'].includes(failureCode);
      }
    } finally {
      busy = false;
      schedule();
      window.dispatchEvent(new Event('ece329:translations-ready'));
      if (!paused && pending.size) setTimeout(flush, 30);
    }
  }
  function setLanguage(value) {
    if (!['zh','en'].includes(value)) return;
    language = value; generation++; blocked = false; paused = false; dismissed = false; pending.clear(); failed.clear();
    try { localStorage.setItem(KEY,language); } catch {}
    schedule();
    window.dispatchEvent(new Event('ece329:language-changed'));
  }
  // Drop queued/stale work when the owner replaces a review record or credentials.
  // Keep successful text translations cached; they never modify source records.
  function reset() { generation++; pending.clear(); failed.clear(); blocked=false; paused=false; dismissed=false; schedule(); }
  toggle?.addEventListener('click', () => setLanguage(language === 'en' ? 'zh' : 'en'));
  function retryTranslation() { failed.clear(); blocked=false; paused=false; dismissed=false; schedule(); setTimeout(flush,0); }
  retry?.addEventListener('click', retryTranslation);
  window.ECE329I18n = {get language() {return language;}, text: translate, setLanguage, refresh:schedule, reset,
    retry: retryTranslation};
  new MutationObserver(schedule).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['placeholder','title','aria-label','label']});
  window.addEventListener('ece329:design-changed', reset);
  schedule();
})();
