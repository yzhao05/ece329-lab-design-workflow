"use strict";
// Run only against tests.feedback_smoke_server --feedback-switch.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const base=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) throw new Error('Local fixture URL required');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ECE329_SMOKE_BROWSER?{executablePath:process.env.ECE329_SMOKE_BROWSER}:{})});
  try {
    const context=await browser.newContext({viewport:{width:1100,height:900}});
    const page=await context.newPage();const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const out=path.resolve(__dirname,'../.test-tmp/feedback-switch-browser');fs.mkdirSync(out,{recursive:true});
    await page.goto(base);
    for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const response=await context.request.post(`${base}/v1/designs`,{data:{idea:'我想研究磁场实验',interaction_state:mode}});
      assert.equal(response.status(),201);const design=await response.json();
      await page.evaluate(design=>{invalidateDesignRequests();applyDesignSnapshot(design);saveState();render();},design);
      await page.locator('#feedbackButton').click();
      await page.locator('#feedbackMessage').fill(`${mode}：继续下一步没有推进当前流程`);
      await page.locator('#feedbackSubmit').click();
      await page.locator('#feedbackHistory').filter({hasText:'model_output_invalid'}).waitFor();
      const before=await (await context.request.get(`${base}/__smoke/feedback-requests`)).json();
      await page.locator('#feedbackSwitch').click();
      await page.locator('#feedbackSwitchPanel').waitFor({state:'visible'});
      assert.equal(await page.locator('#feedbackRetryModel').inputValue(),'deepseek-flash');
      const selectedTicket=await page.locator('#feedbackRetryTicket').inputValue();
      assert.equal(await page.locator('#feedbackSwitchRun').isEnabled(),true);
      assert.deepEqual(await (await context.request.get(`${base}/__smoke/feedback-requests`)).json(),before);
      await page.setViewportSize({width:390,height:844});
      await page.locator('#feedbackSwitchPanel').scrollIntoViewIfNeeded();
      assert.equal(await page.locator('#feedbackDialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
      await page.screenshot({path:path.join(out,`${mode}.png`)});
      const retryRequest=page.waitForRequest(r=>r.url().endsWith('/retry') && r.method()==='POST');
      await page.locator('#feedbackSwitchRun').click();
      assert.equal((await retryRequest).postDataJSON().model,'deepseek-flash');
      await page.locator('#feedbackHistory').filter({hasText:/等待审阅|同类经验/}).waitFor();
      assert.match(await page.locator('#feedbackHistory').innerText(),/分析 2\/10 次/);
      assert.equal(await page.locator('#feedbackRetryTicket').inputValue(),selectedTicket);
      assert.equal(await page.locator('#feedbackSwitchRun').isDisabled(),true);
      assert.match(await page.locator('#feedbackSwitchHint').innerText(),/请重新选择/);
      const saved=await (await context.request.get(`${base}/v1/designs/${design.design_id}`,{headers:{Authorization:`Bearer ${design.design_access_token}`}})).json();
      assert.equal(saved.revision,design.revision);
      const after=await (await context.request.get(`${base}/__smoke/feedback-requests`)).json();
      assert.deepEqual(after.slice(before.length).map(r=>r.provider),['deepseek','deepseek']);
      await page.locator('#feedbackClose').click();
      await page.setViewportSize({width:1100,height:900});
    }
    await page.locator('#languageToggle').click();
    await page.locator('#feedbackButton').click();
    await page.locator('#feedbackSwitch').filter({hasText:'Change analysis API'}).waitFor();
    await page.locator('#feedbackSwitch').click();
    await page.locator('#feedbackSwitchRun').filter({hasText:'Retry with selected API'}).waitFor();
    assert.equal(await page.locator('#feedbackSwitchRun').isDisabled(),true);
    assert.match(await page.locator('#feedbackSwitchHint').innerText(),/Please select another record/);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:'both modes, explicit DeepSeek retry, 2/10 attempts, no design advancement, mobile and English UI',screenshots:out}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
