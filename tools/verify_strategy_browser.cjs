"use strict";
// Local fixture only: tests.feedback_smoke_server --models.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')) throw new Error('Local fixture URL required');
const output=path.resolve(__dirname,'../.test-tmp/strategy-browser');fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ECE329_SMOKE_BROWSER?{executablePath:process.env.ECE329_SMOKE_BROWSER}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  async function send(create=false){
    if (await page.locator('#modelPopover').isVisible()) await page.locator('#closeModelPopover').click();
    await page.locator('#chatInput').fill('电场的叠加如何计算？');
    const pending=page.waitForResponse(r=>r.request().method()==='POST'&&(create?r.url()===`${base}/v1/designs`:r.url().endsWith('/turns')));
    await page.locator('#sendButton').click();const response=await pending;
    assert.ok(response.ok(),await response.text());await page.waitForFunction(()=>!document.getElementById('sendButton').disabled);
    return {body:await response.json(),request:response.request().postDataJSON()};
  }
  try{
    await page.goto(base);await page.waitForFunction(()=>!document.getElementById('modelStrategy').disabled);
    assert.equal(await page.locator('#modelStrategy').inputValue(),'recommended');
    const first=await send(true);assert.equal(first.request.model,undefined);assert.equal(first.request.model_config.strategy,'recommended');
    await page.locator('#currentModelButton').click();
    await page.locator('#modelStrategy').selectOption('quality');
    const saving=page.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().endsWith('/model-config'));
    await page.locator('#saveModelStrategy').click();assert.ok((await saving).ok());
    await page.waitForFunction(()=>!document.getElementById('modelStrategy').disabled);
    const second=await send();assert.equal(second.body.selected_model,'gpt-5.6-sol');
    assert.equal(second.body.current_stage,first.body.current_stage);
    await page.reload();await page.waitForFunction(()=>document.getElementById('modelStrategy').value==='quality'&&!document.getElementById('modelStrategy').disabled);
    await page.locator('#currentModelButton').click();
    await page.locator('#modelStrategy').selectOption('custom');
    await page.locator('#stageRoutingDetails summary').click();
    assert.equal(await page.locator('#stageRoutingRows select').count(),13);
    await page.locator(`[data-stage="${second.body.current_stage}"]`).selectOption('fast');
    await page.locator('#experienceEnabled').uncheck();
    const third=await send();assert.equal(third.body.selected_model,'gpt-5.4-mini');
    assert.equal(third.request.model_config.experience_enabled,false);
    await page.getByRole('button',{name:'反馈这条回答 / Report Problem',exact:true}).last().click();
    await page.locator('#feedbackScope').selectOption('session');
    await page.locator('#feedbackMessage').fill('这条回答的步骤还不够明确，希望给出可操作的说明。');
    const savingFeedback=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/feedback'));
    await page.locator('#feedbackSubmit').click();const submitted=await savingFeedback;assert.ok(submitted.ok());
    const feedback=await submitted.json();assert.equal(submitted.request().postDataJSON().telemetry_id,third.body.telemetry_id);
    assert.equal(feedback.scope,'session');await page.locator('#feedbackClose').click();
    await page.locator('#currentModelButton').click();
    const download=page.waitForEvent('download');await page.locator('#exportTelemetry').click();
    const file=await download;const target=path.join(output,'telemetry.json');await file.saveAs(target);
    const data=JSON.parse(fs.readFileSync(target,'utf8'));assert.ok(data.records.length>=3);
    assert.ok(data.records.some(row=>row.user_feedback.some(ticket=>ticket.id===feedback.id)));
    assert.ok(data.records.some(row=>row.model_config.experience_enabled===false));
    assert.ok(data.records.every(row=>row.input_tokens===null)); // Fixture supplies no usage.
    await page.locator('#stageRoutingDetails summary').click();
    await page.screenshot({path:path.join(output,'desktop.png')});
    await page.setViewportSize({width:390,height:844});await page.locator('#modelStrategy').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'mobile.png')});
    const bounds=await page.locator('#modelStrategy').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:'recommended/quality/custom, stage overrides, owner PATCH, reload, experience OFF, output-bound scoped feedback, telemetry export',output}));
  }catch(error){await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});throw error;}
  finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
