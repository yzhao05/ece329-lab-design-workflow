"use strict";
// Optional local integration check. Requires Playwright and a running
// tests/feedback_smoke_server.py fixture; never point this at a real deployment.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || "")) throw new Error("A local smoke fixture URL is required");
const output = path.resolve(__dirname, "../.test-tmp/feedback-browser");
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.ECE329_SMOKE_BROWSER ? { executablePath: process.env.ECE329_SMOKE_BROWSER } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  context.on("page", page => page.on("pageerror", error => errors.push(error.message)));
  try {
    const page = await context.newPage();
    await page.goto(base);
    await page.locator("#feedbackButton").click();
    assert.equal(await page.locator("#feedbackClose").evaluate(el => getComputedStyle(el).color), "rgb(20, 45, 66)");
    await page.locator("#feedbackStatus").filter({ hasText: "请先连接课程服务" }).waitFor();
    assert.equal(await page.locator("#feedbackSubmit").isDisabled(), true);
    await page.locator("#feedbackClose").click();
    const ids = [];
    for (const mode of ["GUIDED_DESIGN", "EMVR_DIRECT"]) {
      const response = await context.request.post(`${base}/v1/designs`, { data: { idea: "我想研究磁场实验", interaction_state: mode } });
      assert.equal(response.status(), 201);
      const design = await response.json();
      ids.push(design.design_id);
      await page.evaluate(design => { invalidateDesignRequests(); applyDesignSnapshot(design); saveState(); render(); }, design);
      await page.locator("#feedbackButton").click();
      await page.locator("#feedbackCategory").selectOption("answered_pending");
      await page.locator("#feedbackMessage").fill(`${mode}：我已经回答过了，为什么重复询问？`);
      await page.locator("#feedbackSubmit").click();
      await page.locator("#feedbackStatus").filter({ hasText: /^提交成功：/ }).waitFor();
      await page.locator("#feedbackRefresh").click();
      await page.locator("#feedbackHistory").filter({ hasText: mode === "GUIDED_DESIGN" ? "等待审阅" : "同类经验" }).waitFor();
      const records = await (await context.request.get(`${base}/v1/designs/${design.design_id}/feedback`, { headers: { Authorization: `Bearer ${design.design_access_token}` } })).json();
      assert.equal(records.feedback.length, 1);
      assert.equal(records.feedback[0].durable, true);
      const unchanged = await (await context.request.get(`${base}/v1/designs/${design.design_id}`, { headers: { Authorization: `Bearer ${design.design_access_token}` } })).json();
      assert.equal(unchanged.revision, design.revision);
      await page.screenshot({ path: path.join(output, `${mode}.png`) });
      await page.locator("#feedbackClose").click();
    }
    const admin = await context.newPage();
    await admin.goto(`${base}/feedback-review.html`);
    await admin.locator("#reviewToken").fill("wrong-token");
    await admin.locator("#reviewLogin button[type=submit]").click();
    await admin.locator("#reviewStatus").filter({ hasText: "令牌无效" }).waitFor();
    await admin.locator("#reviewToken").fill("smoke-maintainer");
    await admin.locator("#reviewLogin button[type=submit]").click();
    const candidates = await (await context.request.get(`${base}/v1/feedback/experiences?status=candidate`, { headers: { "X-ECE329-Feedback-Admin-Token": "smoke-maintainer" } })).json();
    const candidate = candidates.experiences.find(item => item.evidence.scope_design_id === ids[0]);
    assert.ok(candidate, "Find this test's candidate, even when other scoped experiences exist");
    const card = admin.locator(".experience-card").filter({ has: admin.locator(`[id="scope-${candidate.id}"]`) });
    await card.waitFor();
    await card.locator("textarea[id^=note-]").fill("已对照证据并运行重复问题回放，检查适用范围。");
    await card.getByRole("button", { name: "启用经验", exact: true }).click();
    await admin.locator("#reviewStatus").filter({ hasText: "已保存：active" }).waitFor();
    await admin.locator("#reviewFilter").selectOption("active");
    await admin.locator("#reviewLogin button[type=submit]").click();
    await card.getByRole("button", { name: "停用经验", exact: true }).waitFor();
    await admin.screenshot({ path: path.join(output, "review-active.png") });
    const active = await (await context.request.get(`${base}/v1/feedback/experiences?status=active`, { headers: { "X-ECE329-Feedback-Admin-Token": "smoke-maintainer" } })).json();
    assert.ok(active.experiences.some(item => item.id === candidate.id));
    await card.locator("textarea[id^=note-]").fill("停用回归：确认后续检索撤回这条经验。");
    await card.getByRole("button", { name: "停用经验", exact: true }).click();
    await admin.locator("#reviewStatus").filter({ hasText: "已保存：disabled" }).waitFor();
    await admin.locator("#reviewLogout").click();
    assert.equal(await admin.locator("#reviewToken").inputValue(), "");
    assert.equal(await admin.evaluate(() => Object.keys(localStorage).some(key => localStorage[key].includes("smoke-maintainer"))), false);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#feedbackButton").click();
    await page.screenshot({ path: path.join(output, "mobile.png") });
    const bounds = await page.locator("#feedbackDialog").boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, designs: ids, checks: "both modes, real SQLite receipts, no stage advancement, admin denial/approval/disable, no stored admin token, mobile dialog", screenshots: output }));
  } catch (error) {
    for (const [index, page] of context.pages().entries()) {
      await page.screenshot({ path: path.join(output, `failure-${index}.png`) }).catch(() => {});
      console.error(await page.locator("#feedbackStatus, #reviewStatus").allTextContents());
    }
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
