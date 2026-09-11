"use strict";

// Shared submission state is independent of rendering; uncertain requests retain
// their idempotency key, including after a browser refresh.
window.FeedbackClient = class FeedbackClient {
  constructor({ designId, request, storage, storageKey, newId = () => crypto.randomUUID() }) {
    Object.assign(this, { designId, request, storage, storageKey, newId });
    this.busy = false;
    this.draft = null;
    try {
      const saved = JSON.parse(storage.getItem(storageKey));
      if (saved && typeof saved.message === "string" && typeof saved.category === "string"
          && typeof saved.request_id === "string") this.draft = saved;
    } catch (_) { /* Storage may be disabled; this tab still retains the draft. */ }
  }

  edit(message, category, context = {}) {
    if (this.busy) return;
    const extra = Object.fromEntries(['scope', 'stage', 'revision', 'telemetry_id'].filter(key => context[key] != null).map(key => [key, context[key]]));
    const previous = this.draft ? Object.fromEntries(Object.entries(this.draft).filter(([key]) => !['message', 'category', 'request_id'].includes(key))) : {};
    if (this.draft?.message === message && this.draft?.category === category && JSON.stringify(previous) === JSON.stringify(extra)) return;
    this.draft = { message, category, request_id: this.newId(), ...extra };
    this.persist();
  }

  persist() {
    try {
      if (this.draft) this.storage.setItem(this.storageKey, JSON.stringify(this.draft));
      else this.storage.removeItem(this.storageKey);
    } catch (_) { /* Submission itself must work without browser storage. */ }
  }

  path(suffix = "") {
    return `/v1/designs/${encodeURIComponent(this.designId)}/feedback${suffix}`;
  }

  async submit() {
    if (this.busy || !this.draft?.message.trim()) return null;
    this.busy = true;
    try {
      const receipt = await this.request(this.path(), { method: "POST", body: JSON.stringify(this.draft) });
      if (!receipt?.id || receipt.design_id !== this.designId || !receipt.status) {
        throw new Error("未收到有效的反馈回执，请用原内容重试。");
      }
      this.draft = null;
      this.persist();
      return receipt;
    } finally {
      this.busy = false;
    }
  }

  async list() {
    const result = await this.request(this.path(), { method: "GET" });
    if (!Array.isArray(result.feedback)) throw new Error("反馈记录响应无效");
    return result.feedback;
  }

  retry(id) {
    return this.request(this.path(`/${encodeURIComponent(id)}/retry`), { method: "POST", body: "{}" });
  }
};
