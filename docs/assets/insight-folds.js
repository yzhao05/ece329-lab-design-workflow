"use strict";
(() => {
  const storageKey = 'ece329-insight-folds-v1';
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(storageKey) || '[]'); } catch {}
  const states = new Map(Array.isArray(saved) ? saved.filter(row => Array.isArray(row)
    && typeof row[0] === 'string' && typeof row[1] === 'boolean').slice(-300) : []);
  const bindings = new WeakMap();
  let fieldId = 0;
  function remember(key, open) {
    states.delete(key); states.set(key, open);
    while (states.size > 300) states.delete(states.keys().next().value);
    try { sessionStorage.setItem(storageKey, JSON.stringify([...states])); } catch {}
  }
  function hash(value) {
    let result = 2166136261;
    for (const char of value) result = Math.imul(result ^ char.codePointAt(0), 16777619);
    return (result >>> 0).toString(36);
  }
  function bind(details, key) {
    const previous = bindings.get(details);
    if (previous?.key === key) return;
    const binding = {key, defaultOpen:previous?.defaultOpen ?? details.open};
    bindings.set(details, binding);
    details.dataset.foldKey = key;
    details.classList.add('insight-fold');
    details.open = states.get(key) ?? binding.defaultOpen;
    if (previous) return;
    // Native keyboard activation also dispatches click. Programmatic toggle
    // events from rendering must not save defaults as explicit preferences.
    details.querySelector(':scope > summary')?.addEventListener('click', event => {
      if (!event.defaultPrevented && details.isConnected) remember(bindings.get(details).key, !details.open);
    });
  }
  function wrap(host, heading, contents, key, outer = false) {
    if (!heading || !contents.length) return;
    const details = document.createElement('details'); details.open = true;
    details.className = outer ? 'insight-card-fold' : 'insight-section-fold';
    const summary = document.createElement('summary');
    host.insertBefore(details, heading);
    summary.append(heading); details.append(summary);
    const body = document.createElement('div'); body.className = 'insight-fold-body';
    body.append(...contents); details.append(body);
    bind(details, key);
    return details;
  }
  function titled(host, selector, key) {
    if (!host || host.querySelector(':scope > .insight-fold')) return;
    const title = host.querySelector(selector);
    if (title) wrap(host, title, [...host.children].filter(n => n !== title), key);
  }
  function previewRows(host, selector, prefix) {
    host?.querySelectorAll(selector).forEach(row => {
      if (row.querySelector(':scope > .insight-fold')) return;
      const value = row.textContent.trim(); if (!value) return;
      const heading = document.createElement('span');
      heading.textContent = value.length > 32 ? value.slice(0, 32) + '…' : value;
      const body = document.createElement('div'); body.append(...row.childNodes);
      row.append(heading, body); wrap(row, heading, [body], prefix + hash(value));
    });
  }
  function reportFields(panel, scope) {
    const occurrences = new Map();
    panel.querySelectorAll('.task-report-section dl > div').forEach(row => {
      const heading = row.querySelector('dt'), value = row.querySelector('dd');
      if (!heading || !value) return;
      row.dataset.foldFieldKey ||= hash(heading.textContent);
      const baseKey = scope + ':field:' + row.closest('details').dataset.sectionKey + ':' + row.dataset.foldFieldKey;
      const occurrence = occurrences.get(baseKey) || 0;
      occurrences.set(baseKey, occurrence + 1);
      if (heading.querySelector('button')) return;
      const key = baseKey + ':' + occurrence;
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'insight-field-toggle'; button.textContent = heading.textContent;
      const id = 'fold-value-' + (++fieldId); value.id = id;
      button.setAttribute('aria-controls', id);
      function show(open) { value.hidden = !open; button.setAttribute('aria-expanded', String(open)); }
      show(states.get(key) ?? true);
      button.addEventListener('click', () => { const open = value.hidden; show(open); remember(key, open); });
      heading.replaceChildren(button);
    });
  }
  function enhance(panel, scope = 'local') {
    if (!panel) return;
    panel.querySelectorAll(':scope > .insight-card').forEach(card => {
      const id = card.id || (card.classList.contains('source-card') ? 'evidence' : 'notes');
      if (!card.querySelector(':scope > .insight-card-fold')) {
        const heading = card.querySelector(':scope > .card-heading');
        wrap(card, heading, [...card.children].filter(n => n !== heading), 'card:' + id, true);
      }
    });
    panel.querySelectorAll('.evidence-item').forEach((item, i) =>
      titled(item, ':scope > strong', scope + ':evidence:' + hash(item.dataset.itemKey || String(i))));
    panel.querySelectorAll('[data-unity-summary] > h3').forEach(heading => {
      const next = heading.nextElementSibling;
      if (next?.tagName === 'UL') wrap(heading.parentElement, heading, [next], scope + ':unity:' + hash(heading.textContent));
    });
    const drawing = panel.querySelector('[data-unity-drawing]');
    if (drawing?.childElementCount && !drawing.querySelector(':scope > .insight-fold')) {
      const contents = [...drawing.children], title = document.createElement('h3');
      title.textContent = '组件俯视图'; drawing.prepend(title);
      wrap(drawing, title, contents, scope + ':unity:drawing');
    }
    titled(panel.querySelector('[data-unity-questions]'), ':scope > h3', scope + ':unity:questions');
    for (const id of ['qualityCausalChain', 'qualityFeasibility'])
      titled(panel.querySelector('#' + id), ':scope > strong', scope + ':' + id);
    panel.querySelectorAll('.quality-option-item').forEach((item, i) =>
      titled(item, ':scope > strong', scope + ':option:' + i));
    previewRows(panel.querySelector('#designNotes'), ':scope > li', scope + ':note:');
    previewRows(panel.querySelector('#qualityIssueList'), ':scope > li', scope + ':issue:');
    reportFields(panel, scope);
    panel.querySelectorAll('details[data-native-fold],details:not(.insight-fold)').forEach((details, i) => {
      details.dataset.nativeFold ||= details.dataset.sectionKey || details.id || details.className || String(i);
      bind(details, scope + ':existing:' + details.dataset.nativeFold);
    });
  }
  window.ECE329InsightFolds = {enhance};
})();
