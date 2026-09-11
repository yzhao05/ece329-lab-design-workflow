"use strict";
// Local fixture: tests.feedback_smoke_server --deepseek. No real API calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) throw new Error('Local fixture URL required');
const output = path.resolve(__dirname, '../.test-tmp/composer-browser');
fs.mkdirSync(output, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.ECE329_SMOKE_BROWSER ? {executablePath:process.env.ECE329_SMOKE_BROWSER} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];
  page.on('pageerror', e => errors.push(e.message));
  const admin={'X-ECE329-Feedback-Admin-Token':'smoke-maintainer'};
  async function send(model) {
    await page.locator('#modelSelect').selectOption(model);
    await page.locator('#chatInput').fill('电场的叠加如何计算？');
    const response=page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/turns'));
    await page.locator('#sendButton').click();
    const result=await response;
    assert.ok(result.ok(),await result.text());
    const body=await result.json();
    assert.equal(body.selected_model,model);
    await page.waitForFunction(() => !document.getElementById('sendButton').disabled);
    return body;
  }
  try {
    await page.goto(base);
    await page.waitForFunction(() => !document.getElementById('modelSelect').disabled);
    for (const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const created=await page.request.post(`${base}/v1/designs`,{data:{idea:'电场的叠加如何计算？',interaction_state:mode}});
      assert.equal(created.status(),201);
      const design=await created.json(), id=design.design_id;
      const auth={Authorization:`Bearer ${design.design_access_token}`};
      await page.evaluate(d => {invalidateDesignRequests(); applyDesignSnapshot(d); saveState(); render();},design);
      const before=await send('gpt-5.5');
      await page.getByRole('button',{name:'反馈这条回答 / Report Problem',exact:true}).last().click();
      await page.locator('#feedbackScope').selectOption('session');
      await page.locator('#feedbackCategory').selectOption('answered_pending');
      await page.locator('#feedbackMessage').fill('已经回答了，仍在重复询问，请先回应当前问题。');
      await page.locator('#feedbackSubmit').click();
      await page.locator('#feedbackStatus').filter({hasText:/^提交成功：/}).waitFor();
      await page.locator('#feedbackClose').click();
      let item;
      for (let attempt=0; attempt<30 && !item; attempt++) {
        const rows=await (await page.request.get(`${base}/v1/feedback/experiences?status=candidate`,{headers:admin})).json();
        item=rows.experiences.find(r => r.evidence.scope_design_id===id);
        if (!item) await page.waitForTimeout(100);
      }
      assert.ok(item,'Feedback is extracted into a scoped candidate');
      assert.equal(item.evidence.telemetry_id,before.telemetry_id);
      const review=await page.request.post(`${base}/v1/feedback/experiences/${item.id}/review`,{headers:admin,
        data:{decision:'approve',version:1,note:'本机回归已验证适用于本设计',content:{...item.content,keywords:['叠加']}}});
      assert.ok(review.ok(),await review.text());
      const learned=await send('deepseek-flash:fast');
      let traces=(await (await page.request.get(`${base}/v1/designs/${id}/telemetry`,{headers:auth})).json()).records;
      const trace=traces.find(r => r.id===learned.telemetry_id);
      assert.ok(trace.calls.length>=2);
      assert.ok(trace.calls.every(r => r.provider==='deepseek' && r.experience_rule_ids.includes(`EXP-${item.id}`)));
      assert.equal(traces.find(r => r.id===before.telemetry_id).user_feedback.length,1);
      const disabled=await page.request.post(`${base}/v1/feedback/experiences/${item.id}/review`,{headers:admin,
        data:{decision:'disable',version:2,note:'停用测试，验证后续调用不再引用'}});
      assert.ok(disabled.ok());
      const withdrawn=await send('gpt-5.4-mini');
      traces=(await (await page.request.get(`${base}/v1/designs/${id}/telemetry`,{headers:auth})).json()).records;
      assert.ok(traces.find(r => r.id===withdrawn.telemetry_id).calls.every(r => !r.experience_rule_ids.includes(`EXP-${item.id}`)));
      assert.equal(learned.current_stage,before.current_stage);
      const widths=[];
      for (const width of [1440,1024,768,390,320]) {
        await page.setViewportSize({width,height:1100});
        const geometry=await page.evaluate(() => {
          const input=document.getElementById('chatInput'), css=getComputedStyle(input);
          const left=input.getBoundingClientRect().left+parseFloat(css.paddingLeft);
          const rect=selector => {const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,width:r.width};};
          return {left,label:rect('[for="modelSelect"]'),help:rect('#modelHelp'),select:rect('#modelSelect'),
            refresh:rect('#refreshModels'),form:rect('#chatForm'),inset:parseFloat(css.paddingRight),scroll:document.documentElement.scrollWidth};
        });
        assert.ok(Math.abs(geometry.label.left-geometry.left)<1,'Label aligns with textarea text');
        assert.ok(Math.abs(geometry.help.left-geometry.left)<1,'Help aligns with textarea text');
        assert.ok(geometry.scroll<=width,'No page overflow');
        if (width<=480) {
          assert.ok(Math.abs(geometry.select.left-geometry.left)<1);
          assert.ok(Math.abs(geometry.select.right-(geometry.form.right-geometry.inset-1))<1);
        } else {
          assert.ok(Math.abs(geometry.refresh.left-geometry.select.right-12)<1,'Select fills available row width');
        }
        widths.push({viewport:width,select:geometry.select.width});
        if ([1440,390].includes(width)) await page.locator('#chatForm').screenshot({path:path.join(output,`${mode}-${width}.png`)});
      }
      console.log(JSON.stringify({mode,passed:true,widths,checks:'model switching, output feedback, extraction, approval, real prompt injection and withdrawal, responsive text alignment'}));
    }
    assert.deepEqual(errors,[]);
  } catch(e) {await page.screenshot({path:path.join(output,'failure.png')}); throw e;}
  finally {await browser.close();}
})().catch(e => {console.error(e);process.exitCode=1;});
