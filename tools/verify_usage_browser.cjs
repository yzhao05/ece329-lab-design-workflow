"use strict";
// Only tests.feedback_smoke_server --models --usage; no real model API calls.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const base=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')) throw new Error('Local fixture URL required');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ECE329_SMOKE_BROWSER?{executablePath:process.env.ECE329_SMOKE_BROWSER}:{})});
  const out=path.resolve(__dirname,'../.test-tmp/usage-browser');fs.mkdirSync(out,{recursive:true});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:950}}),page=await context.newPage(),errors=[];
    context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);const ids=[];
    for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const response=await context.request.post(`${base}/v1/designs`,{data:{idea:'电场的叠加如何计算？',interaction_state:mode}});
      assert.equal(response.status(),201);const design=await response.json();ids.push(design.design_id);
      assert.equal(typeof design.timing.reply_ms,'number');
      await page.evaluate(design=>{invalidateDesignRequests();state=initialState();applyResponse({...design,_runtime_source:'api'},'电场的叠加如何计算？');render();},design);
      await page.locator('.reply-time').last().waitFor();
      assert.match(await page.locator('.reply-time').last().innerText(),/用时/);
      assert.match(await page.locator('#designActiveTime').innerText(),/活跃总时长/);
      const timing=await page.locator('#designActiveTime').innerText();
      await page.waitForTimeout(1100);
      assert.equal(await page.locator('#designActiveTime').innerText(),timing);
      await page.screenshot({path:path.join(out,`${mode}-desktop.png`)});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await page.screenshot({path:path.join(out,`${mode}-mobile.png`)});
      await page.setViewportSize({width:1440,height:950});
      const submitted=await context.request.post(`${base}/v1/designs/${design.design_id}/feedback`,{
        headers:{Authorization:`Bearer ${design.design_access_token}`},data:{request_id:`usage-${mode}`,message:'已经回答，仍重复询问。',category:'answered_pending'}});
      assert.equal(submitted.status(),201);
    }
    assert.equal((await context.request.get(`${base}/v1/feedback/usage`)).status(),401);
    const review=await context.newPage();await review.goto(`${base}/feedback-review.html`);
    await review.locator('#reviewToken').fill('smoke-maintainer');
    await review.locator('#reviewUsage').click();
    await review.locator('.experience-card').filter({hasText:ids[0]}).waitFor();
    assert.ok(await review.locator('.experience-card').count()>=2);
    assert.match(await review.locator('#reviewCards').innerText(),/输入 token：[1-9]/);
    assert.match(await review.locator('#reviewCards').innerText(),/估算费用（USD）：\$0\./);
    assert.match(await review.locator('#reviewCards').innerText(),/当前停留阶段：/);
    await review.locator('.usage-breakdown > summary').first().click();
    assert.match(await review.locator('.usage-breakdown').first().innerText(),/API: openai.*gpt-5.4-mini/);
    assert.match(await review.locator('.usage-breakdown').first().innerText(),/经验提炼 Agent/);
    assert.match(await review.locator('.usage-breakdown').first().innerText(),/API 用时/);
    await review.screenshot({path:path.join(out,'maintainer-usage.png')});
    await review.locator('#languageToggle').click();
    await review.locator('#reviewUsage').filter({hasText:'Design usage records'}).waitFor();
    assert.match(await review.locator('.usage-summary').first().innerText(),/Input tokens/);
    await review.locator('#reviewFilter').selectOption('candidate');await review.locator('#reviewLogin button[type=submit]').click();
    await review.locator('.experience-card').first().waitFor();
    assert.match(await review.locator('.usage-summary').first().innerText(),/Input tokens: 2000/);
    await review.locator('#reviewLogout').click();assert.equal(await review.locator('.experience-card').count(),0);
    await page.reload();await page.locator('.reply-time').last().waitFor();
    assert.match(await page.locator('.reply-time').last().innerText(),/^Time /);
    await page.locator('#languageToggle').click();
    assert.match(await page.locator('.reply-time').last().innerText(),/用时/);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:'both modes; reply timing; idle exclusion; reload; admin usage/cost; mobile; English; logout',screenshots:out}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
