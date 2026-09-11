"use strict";
// Use only tests.feedback_smoke_server --models, never a live deployment.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || "")) throw new Error("Local model fixture URL required");
const output = path.resolve(__dirname, "../.test-tmp/model-browser");
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ECE329_SMOKE_BROWSER ? { executablePath: process.env.ECE329_SMOKE_BROWSER } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  async function send(model, create = false) {
    await page.locator("#currentModelButton").click();
    await page.locator("#modelSelect").selectOption(model);
    await page.locator("#closeModelPopover").click();
    await page.locator("#chatInput").fill("电场的叠加如何计算？");
    const pending = page.waitForResponse(response => response.request().method() === "POST" &&
      (create ? response.url() === `${base}/v1/designs` : response.url().endsWith("/turns")));
    await page.locator("#sendButton").click();
    const response = await pending;
    assert.equal(response.request().postDataJSON().model, model);
    assert.ok(response.ok(), await response.text());
    const body = await response.json();
    assert.equal(body.selected_model, model);
    await page.waitForFunction(() => !document.getElementById("sendButton").disabled);
    assert.equal(await page.locator("#modelSelect").inputValue(), model);
    return body;
  }
  try {
    await page.goto(base);
    await page.waitForFunction(() => !document.getElementById("modelSelect").disabled);
    const catalogue = await (await context.request.get(`${base}/v1/models`)).json();
    const deepseek = catalogue.models.filter(m => m.provider === 'deepseek');
    const newModels = ['gpt-5.5', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', ...deepseek.map(m => m.id)];
    assert.deepEqual(await page.locator("#modelSelect option").allTextContents(), catalogue.models.map(m => m.label));
    assert.equal(await page.locator("#modelSelect").inputValue(), "gpt-5.4-mini");
    const created = await send("gpt-5.4", true);
    await send("gpt-5.4-nano");
    await page.reload();
    await page.waitForFunction(() => !document.getElementById("modelSelect").disabled && document.getElementById("modelSelect").value === "gpt-5.4-nano");
    await send("gpt-5.4-mini");
    for (const model of newModels) await send(model);
    const requests = await (await context.request.get(`${base}/__smoke/model-requests`)).json();
    assert.ok(requests.some(r => r.model === "gpt-5.4"));
    assert.ok(requests.some(r => r.model === "gpt-5.4-nano"));
    assert.ok(requests.some(r => r.model === "gpt-5.4-mini"));
    for (const model of newModels) assert.ok(requests.some(r => r.model === (catalogue.models.find(m => m.id === model)?.api_model || model)));
    if (deepseek.length) {
      assert.equal(deepseek.length, 4);
      assert.ok(requests.some(r => r.provider === 'deepseek' && r.thinking === 'disabled'));
      assert.ok(requests.some(r => r.provider === 'deepseek' && r.reasoning_effort === 'high'));
    }
    await page.screenshot({ path: path.join(output, "desktop.png"), animations: 'disabled' });
    // Exercise the same visible selector inside an EMVR session as well.
    const emvr = await (await context.request.post(`${base}/v1/designs`, { data: { idea: "电场的叠加如何计算？", interaction_state: "EMVR_DIRECT", model: "gpt-5.4-mini" } })).json();
    assert.ok(emvr.design_id);
    await page.evaluate(design => { invalidateDesignRequests(); applyDesignSnapshot(design); saveState(); render(); }, emvr);
    assert.equal(await page.locator("#modelSelect").inputValue(), "gpt-5.4-mini");
    await send("gpt-5.4");
    for (const model of newModels) await send(model);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#sendButton").scrollIntoViewIfNeeded();
    await page.locator("#currentModelButton").click();
    await page.screenshot({ path: path.join(output, "mobile.png"), animations: 'disabled' });
    const bounds = await page.locator("#modelSelect").boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, models: [...new Set(requests.map(r => r.model))], presets:deepseek.map(m => m.id), checks: 'real create/turn payloads, provider adapters, refresh restoration, both modes, mobile layout', screenshots: output }));
  } catch (error) {
    await page.screenshot({ path: path.join(output, "failure.png") }).catch(() => {});
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
