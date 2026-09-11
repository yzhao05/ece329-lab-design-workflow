"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../..");
function makeClient(request, storage = new Map()) {
  const context = { window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "docs/assets/feedback-client.js"), "utf8"), context);
  let sequence = 0;
  const adapter = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  return new context.window.FeedbackClient({ designId: "design-a", request, storage: adapter, storageKey: "draft-a", newId: () => `request-${++sequence}` });
}

test("network retry after page refresh reuses exact body and clears only on receipt", async () => {
  const storage = new Map();
  const bodies = [];
  const broken = makeClient(async (url, options) => { bodies.push(options.body); throw new Error("timeout"); }, storage);
  broken.edit("已回答但重复询问", "answered_pending");
  await assert.rejects(broken.submit(), /timeout/);
  assert.equal(broken.busy, false);
  assert.equal(storage.size, 1);
  const restored = makeClient(async (url, options) => {
    assert.equal(url, "/v1/designs/design-a/feedback");
    bodies.push(options.body);
    return { id: "ticket-1", design_id: "design-a", status: "queued" };
  }, storage);
  const receipt = await restored.submit();
  assert.equal(receipt.id, "ticket-1");
  assert.equal(bodies[0], bodies[1]);
  assert.equal(storage.size, 0);
});

test("output scope and target survive reload; editing the target changes the idempotency key", async () => {
  const storage = new Map();
  const client = makeClient(async () => { throw new Error('timeout'); }, storage);
  const target = {scope:'session',stage:'HYPOTHESIS',revision:2,telemetry_id:'a'.repeat(32)};
  client.edit('步骤不明确', 'other', {...target, ignored:'untrusted'});
  const body = JSON.stringify(client.draft);
  client.edit('步骤不明确', 'other', target);
  assert.equal(JSON.stringify(client.draft), body);
  assert.equal(client.draft.ignored, undefined);
  await assert.rejects(client.submit(), /timeout/);
  const restored = makeClient(async (_, options) => {
    assert.equal(options.body, body);
    return {id:'ticket-target',design_id:'design-a',status:'queued'};
  }, storage);
  await restored.submit();
  const original = client.draft.request_id;
  client.edit('步骤不明确', 'other', {...target,scope:'project'});
  assert.notEqual(client.draft.request_id, original);
});

test("double click makes one call; editing after failure creates a fresh request", async () => {
  let finish;
  let calls = 0;
  const client = makeClient(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  client.edit("问题一", "other");
  const original = client.draft.request_id;
  const first = client.submit();
  assert.equal(await client.submit(), null);
  client.edit("正在提交时的编辑", "other");
  assert.equal(client.draft.message, "问题一");
  finish({ id: "ticket-1", design_id: "design-a", status: "queued" });
  await first;
  assert.equal(calls, 1);
  client.edit("问题二", "other");
  assert.notEqual(client.draft.request_id, original);
});

test("invalid or cross-design receipt preserves feedback; empty text never posts", async () => {
  const client = makeClient(async () => ({ id: "ticket-1", design_id: "design-b", status: "queued" }));
  client.edit("   ", "other");
  assert.equal(await client.submit(), null);
  client.edit("未写入实验参数", "artifact_mismatch");
  await assert.rejects(client.submit(), /回执/);
  assert.equal(client.draft.message, "未写入实验参数");
});

test("list and retry use feedback routes without posting a design turn", async () => {
  const calls = [];
  const client = makeClient(async (url, options) => { calls.push([url, options.method]); return { feedback: [{ id: "ticket-1", status: "failed" }] }; });
  assert.equal((await client.list())[0].status, "failed");
  await client.retry("ticket-1");
  assert.deepEqual(calls, [["/v1/designs/design-a/feedback", "GET"], ["/v1/designs/design-a/feedback/ticket-1/retry", "POST"]]);
});

test("browser storage failure does not prevent server submission", async () => {
  const storage = { get() { throw new Error("blocked"); }, set() { throw new Error("blocked"); }, delete() { throw new Error("blocked"); } };
  const client = makeClient(async () => ({ id: "ticket", design_id: "design-a", status: "candidate" }), storage);
  client.edit("无法回应元问题", "meta_question");
  assert.equal((await client.submit()).status, "candidate");
});

// Minimal DOM drives actual event handlers, including delayed requests across
// design switches. Browser smoke tests separately verify rendering and HTTP.
class Element {
  constructor() { this.listeners = {}; this.children = []; this.value = ""; this.disabled = false; this.open = false; this.textContent = ""; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  async fire(name) { await Promise.all((this.listeners[name] || []).map(handler => handler({ preventDefault() {} }))); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  showModal() { this.open = true; }
  close() { this.open = false; this.fire("close"); }
  focus() {}
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function uiHarness() {
  const els = {};
  const win = new Element();
  const storage = new Map();
  const timers = new Map();
  let timerId = 0;
  let resolveSubmit;
  const calls = [];
  const context = { window: win, document: { getElementById: id => els[id] ||= new Element(), createElement: tag => Object.assign(new Element(), { tag }) },
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    crypto: { randomUUID: () => "request-12345678" }, state: { designId: "a", sessionKind: "api" }, designGeneration: 0,
    apiBase: () => "https://api.example.test", setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id),
    authorizedDesignApiRequest: async (url, options) => {
      calls.push([url, options]);
      if (options.method === "POST") return new Promise(resolve => { resolveSubmit = resolve; });
      return { feedback: [{ id: "record", design_id: context.state.designId, message: "<img src=x onerror=attack()>", status: "queued", revision: 1, attempts: 0, durable: true }] };
    },
  };
  vm.createContext(context);
  for (const name of ["feedback-client", "feedback-ui"]) vm.runInContext(fs.readFileSync(path.join(root, `docs/assets/${name}.js`), "utf8"), context);
  return { context, els, win, timers, calls, finish: value => resolveSubmit(value) };
}

test("feedback dialog uses text nodes, stops polling on close, and blocks demo submissions", async () => {
  const h = uiHarness();
  await h.els.feedbackButton.fire("click");
  await flush();
  assert.equal(h.els.feedbackHistory.children[0].children[1].textContent, "<img src=x onerror=attack()>");
  assert.equal(h.timers.size, 1);
  await h.els.feedbackClose.fire("click");
  assert.equal(h.timers.size, 0);
  h.context.state = { designId: null, sessionKind: "demo" };
  h.context.designGeneration++;
  await h.win.fire("ece329:design-changed");
  await h.els.feedbackButton.fire("click");
  assert.equal(h.els.feedbackSubmit.disabled, true);
  assert.match(h.els.feedbackStatus.textContent, /本地示例/);
  assert.equal(h.calls.length, 1);
});

test("late feedback receipt cannot clear a new design's draft or display false success", async () => {
  const h = uiHarness();
  await h.els.feedbackButton.fire("click");
  await flush();
  h.els.feedbackMessage.value = "旧设计反馈";
  const sending = h.els.feedbackForm.fire("submit");
  await flush();
  h.context.state = { designId: "b", sessionKind: "api" };
  h.context.designGeneration++;
  await h.win.fire("ece329:design-changed");
  await h.els.feedbackButton.fire("click");
  h.els.feedbackMessage.value = "新设计反馈";
  await h.els.feedbackMessage.fire("input");
  h.finish({ id: "old-ticket", design_id: "a", status: "queued", durable: true });
  await sending;
  assert.equal(h.els.feedbackMessage.value, "新设计反馈");
  assert.doesNotMatch(h.els.feedbackStatus.textContent, /提交成功/);
});

test("submission during a stale list request schedules one fresh read instead of losing the receipt", async () => {
  const h = uiHarness();
  let finishList;
  let reads = 0;
  const request = h.context.authorizedDesignApiRequest;
  h.context.authorizedDesignApiRequest = (url, options) => {
    if (options.method === 'GET' && ++reads === 1) return new Promise(resolve => { finishList = resolve; });
    return request(url, options);
  };
  await h.els.feedbackButton.fire('click');
  h.els.feedbackMessage.value = '提交时列表仍在加载';
  const sending = h.els.feedbackForm.fire('submit');
  await flush();
  h.finish({ id: 'new-ticket', design_id: 'a', status: 'queued', durable: true });
  await sending;
  finishList({ feedback: [] });
  await flush();
  assert.equal(h.timers.size, 1);
  const [id, followup] = [...h.timers][0];
  h.timers.delete(id);
  await followup();
  assert.equal(reads, 2);
  assert.equal(h.els.feedbackHistory.children[0].children[0].textContent, '已保存，等待分析');
  assert.equal(h.timers.size, 1);
  await h.els.feedbackClose.fire('click');
  assert.equal(h.timers.size, 0);
});
