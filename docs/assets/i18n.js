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
  const cache = new Map(), pending = new Set();
  let scheduled = false, busy = false, blocked = false, generation = 0;
  const cjk = /[\u3400-\u9fff]/;
  const retry = document.getElementById('translationRetry');
  const toggle = document.getElementById('languageToggle');
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
    pending.add(text);
    if (!busy && !blocked) setTimeout(flush, 30);
    return language === 'en' ? (blocked ? 'Translation unavailable. Use Retry translation.' : 'Translating…')
      : (blocked ? '翻译暂不可用，请重试。' : '正在翻译…');
  }
  function skip(element) {
    return !element || element.closest('script,style,textarea,[data-i18n-ignore]');
  }
  function renderRecord(record, forceChinese = false) {
    // Live nodes retain their completed translation even after the shared LRU
    // evicts it. Otherwise a page with >2000 strings requeues itself forever.
    record.translations ||= {};
    if (Object.hasOwn(record.translations, language)) return record.translations[language];
    const result = translate(record.source, forceChinese);
    if (cache.has(key(record.source)) || (!forceChinese && local(record.source.trim()) !== null)) {
      record.translations[language] = result;
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
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (skip(node.parentElement) || node.parentElement === retry) continue;
      let record = originals.get(node);
      if (!record || node.nodeValue !== record.rendered) record = {source: node.nodeValue};
      // English replies received while English was selected can also be read
      // in Chinese. User input controls and original stored messages stay intact.
      const force = language === 'zh' && !cjk.test(record.source) && /[a-z]{3} [a-z]{3}/i.test(record.source)
        && Boolean(node.parentElement.closest('.message-bubble')) && record.source.split(/\s+/).length > 8;
      record.rendered = renderRecord(record, force);
      originals.set(node, record);
      if (node.nodeValue !== record.rendered) node.nodeValue = record.rendered;
    }
    for (const element of document.querySelectorAll('[placeholder],[title],[aria-label]')) {
      if (element.closest('script,style,[data-i18n-ignore]')) continue;
      const records = attributes.get(element) || {};
      for (const attr of ['placeholder','title','aria-label']) {
        if (!element.hasAttribute(attr)) continue;
        const value = element.getAttribute(attr);
        let record = records[attr];
        if (!record || value !== record.rendered) record = {source:value};
        record.rendered = renderRecord(record);
        records[attr] = record;
        if (value !== record.rendered) element.setAttribute(attr,record.rendered);
      }
      attributes.set(element, records);
    }
  }
  function schedule() { if (!scheduled) { scheduled = true; queueMicrotask(scan); } }
  async function flush() {
    if (busy || blocked || !pending.size) return;
    if (!window.requestDisplayTranslation) return; // App wires transport after loading.
    busy = true;
    const epoch = generation, target = language;
    const batch = []; let length = 0;
    for (const text of pending) {
      if (batch.length >= 24 || batch.length && length + text.length > 12000) break;
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
    } catch {
      if (epoch === generation) blocked = true;
    } finally {
      busy = false;
      schedule();
      window.dispatchEvent(new Event('ece329:translations-ready'));
      if (!blocked && pending.size) setTimeout(flush, 30);
    }
  }
  function setLanguage(value) {
    if (!['zh','en'].includes(value)) return;
    language = value; generation++; blocked = false; pending.clear();
    try { localStorage.setItem(KEY,language); } catch {}
    schedule();
    window.dispatchEvent(new Event('ece329:language-changed'));
  }
  toggle?.addEventListener('click', () => setLanguage(language === 'en' ? 'zh' : 'en'));
  retry?.addEventListener('click', () => { blocked = false; schedule(); setTimeout(flush,0); });
  window.ECE329I18n = {get language() {return language;}, text: translate, setLanguage, refresh:schedule,
    retry() {blocked=false; schedule(); setTimeout(flush,0);}};
  new MutationObserver(schedule).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['placeholder','title','aria-label']});
  window.addEventListener('ece329:design-changed', () => { generation++; pending.clear(); blocked=false; schedule(); });
  schedule();
})();
