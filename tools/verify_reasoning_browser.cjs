"use strict";
// Local deterministic service: verify preferences and actual adapter parameters.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')) throw Error('Local fixture URL required');
const output=path.resolve(__dirname,'../.test-tmp/reasoning-browser'); fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(base);
    await page.waitForFunction(()=>!document.getElementById('modelSelect').disabled);
    for (const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const created=await page.request.post(`${base}/v1/designs`,{data:{idea:'电场的叠加如何计算？',interaction_state:mode}});
      assert.equal(created.status(),201);
      const design=await created.json();
      await page.evaluate(d=>{invalidateDesignRequests();applyDesignSnapshot(d);saveState();render();},design);
      await page.locator('#currentModelButton').click();
      for (const model of ['gpt-5.4-mini','gpt-5.6-sol','deepseek-flash:fast']) {
        await page.locator('#modelSelect').selectOption(model);
        await page.locator('#reasoningSelect').selectOption('low');
        const low=await page.locator('#modelBudgetHelp').innerText();
        const effort=model==='gpt-5.4-mini'?'high':'max';
        await page.locator('#reasoningSelect').selectOption(effort);
        assert.notEqual(await page.locator('#modelBudgetHelp').innerText(),low);
        assert.match(await page.locator('#currentModelName').innerText(),new RegExp(effort,'i'));
        await page.locator('#saveModelStrategy').click();
        await page.waitForFunction(()=>!document.getElementById('saveModelStrategy').disabled);
        await page.locator('#closeModelPopover').click();
        await page.locator('#chatInput').fill('电场的叠加如何计算？');
        const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/turns'));
        await page.locator('#sendButton').click();
        const reply=await pending;
        assert.equal(reply.status(),200,await reply.text());
        assert.equal(reply.request().postDataJSON().model_config.reasoning_overrides[model],effort);
        await page.waitForFunction(()=>!document.getElementById('sendButton').disabled);
        const stateInfo=await page.evaluate(()=>({id:state.designId,token:state.designAccessToken}));
        const telemetry=await page.request.get(`${base}/v1/designs/${stateInfo.id}/telemetry`,{headers:{Authorization:`Bearer ${design.design_access_token}`}});
        assert.equal(telemetry.status(),200);
        const records=(await telemetry.json()).records;
        const turn=records.find(r=>r.turn_id===reply.request().postDataJSON().turn_id);
        assert.ok(turn.calls.length>=2);
        assert.ok(turn.calls.every(c=>c.reasoning===effort && c.max_output_tokens>0));
        assert.ok(turn.output_budget_charged<=turn.output_budget_limit);
        await page.reload(); await page.waitForFunction(()=>!document.getElementById('modelSelect').disabled);
        await page.locator('#currentModelButton').click();
        assert.equal(await page.locator('#reasoningSelect').inputValue(),effort);
      }
      // Each model retains its own preference after switching away and back.
      await page.locator('#modelSelect').selectOption('gpt-5.4-mini');
      assert.equal(await page.locator('#reasoningSelect').inputValue(),'high');
      assert.equal(await page.locator('#reasoningSelect option[value=max]').count(),0);
      await page.locator('#languageToggle').click();
      await page.waitForFunction(()=>!document.body.innerText.includes('Translating…'));
      await page.locator('#currentModelButton').click();
      assert.match(await page.locator('#modelBudgetHelp').innerText(),/Output limit per turn/);
      for (const width of [1440,390,320]) {
        await page.setViewportSize({width,height:1000});
        await page.locator('#reasoningSelect').scrollIntoViewIfNeeded();
        const model=await page.locator('#modelSelect').boundingBox();
        const effort=await page.locator('#reasoningSelect').boundingBox();
        assert.ok(effort.y>=model.y+model.height,JSON.stringify({model,effort}));
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:path.join(output,`${mode}-${width}.png`)});
      }
      await page.setViewportSize({width:1440,height:1000});
      await page.locator('#closeModelPopover').click();
      await page.locator('#languageToggle').click();
      await page.route('**/turns',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'model_budget_exceeded',retryable:false})}));
      await page.locator('#chatInput').fill('电場预算停止验证');
      await page.locator('#sendButton').click();
      await page.waitForFunction(()=>!document.getElementById('sendButton').disabled);
      assert.equal(await page.evaluate(()=>state.pendingRequest),null);
      assert.deepEqual(await page.evaluate(()=>state.quickActions),[]);
      assert.equal(await page.locator('#chatInput').inputValue(),'电場预算停止验证');
      await page.unroute('**/turns');
      console.log(JSON.stringify({mode,passed:true,checks:'model/effort budgets, adapter telemetry, saved preferences, bilingual mobile layout'}));
    }
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
