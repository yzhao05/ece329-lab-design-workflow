"use strict";
// Local transport fixtures only; this verifies wiring, not translation quality.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) throw new Error('Local fixture URL required');
const output=path.resolve(__dirname,'../.test-tmp/language-browser');fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  async function settled(){
    await page.waitForFunction(()=>!document.body.innerText.includes('Translating…'),{},{timeout:60000});
    assert.equal(await page.locator('#translationRetry').isVisible(),false);
  }
  try {
    await page.goto(base);
    await page.waitForFunction(()=>!document.getElementById('modelSelect').disabled);
    assert.equal(await page.locator('#modelPopover').isVisible(),false);
    assert.ok((await page.locator('#chatForm').boundingBox()).height<180);
    await page.locator('#languageToggle').click();
    await settled();
    assert.equal(await page.locator('html').getAttribute('lang'),'en');
    assert.equal(await page.locator('#feedbackButton').innerText(),'Report a problem');
    assert.match(await page.locator('#chatInput').getAttribute('placeholder'),/Describe/);
    await page.locator('#currentModelButton').click();
    await settled();
    assert.ok(await page.locator('#modelSelect').isVisible());
    assert.ok(await page.locator('#modelHelp').isVisible());
    assert.equal(await page.locator('#currentModelButton').getAttribute('aria-expanded'),'true');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#modelPopover').isVisible(),false);
    for (const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const result=await page.request.post(`${base}/v1/designs`,{data:{idea:'电场的叠加如何计算？',interaction_state:mode,language:'en'}});
      assert.equal(result.status(),201);
      const design=await result.json();
      await page.evaluate(d=>{invalidateDesignRequests();applyDesignSnapshot(d);saveState();render();},design);
      await page.locator('#currentModelButton').click();
      await page.locator('#modelSelect').selectOption('deepseek-flash:fast');
      await page.locator('#closeModelPopover').click();
      await page.locator('#chatInput').fill('电场的叠加如何计算？');
      const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/turns'));
      await page.locator('#sendButton').click();
      const reply=await response;assert.equal(reply.status(),200,await reply.text());
      assert.equal(reply.request().postDataJSON().language,'en');
      assert.equal((await reply.json()).selected_model,'deepseek-flash:fast');
      await page.waitForFunction(()=>!document.getElementById('sendButton').disabled);
      await settled();
      const original=await page.evaluate(()=>JSON.stringify(state.messages));
      for (const width of [1440,768,390,320]) {
        await page.setViewportSize({width,height:1000});
        assert.equal(await page.locator('#modelPopover').isVisible(),false);
        assert.ok((await page.locator('#chatForm').boundingBox()).height<190);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.locator('#currentModelButton').click();await settled();
        const box=await page.locator('#modelPopover').boundingBox();
        assert.ok(box.x>=0&&box.x+box.width<=width+1&&box.y>=0,JSON.stringify(box));
        await page.screenshot({path:path.join(output,`${mode}-${width}-open.png`)});
        await page.locator('#closeModelPopover').click();
      }
      await page.setViewportSize({width:1440,height:1000});
      await page.locator('#feedbackButton').click();await settled();
      assert.equal(await page.locator('#feedbackTitle').innerText(),'Report a problem');
      assert.match(await page.locator('#feedbackMessage').getAttribute('placeholder'),/For example/);
      await page.locator('#feedbackClose').click();
      const untranslated=await page.evaluate(()=>{
        const nodes=[],walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;
        while(n=walker.nextNode()) if(/[\u3400-\u9fff]/.test(n.nodeValue)&&n.parentElement?.getClientRects().length
          &&!n.parentElement.closest('[data-i18n-ignore],script,style,textarea')) nodes.push(n.nodeValue);
        return nodes;
      });
      assert.deepEqual(untranslated,[]);
      await page.screenshot({path:path.join(output,`${mode}-closed.png`)});
      await page.locator('#languageToggle').click();
      assert.equal(await page.locator('#feedbackButton').innerText(),'反馈问题');
      assert.equal(await page.evaluate(()=>JSON.stringify(state.messages)),original);
      await page.locator('#languageToggle').click();await settled();
      await page.reload();await page.waitForFunction(()=>!document.getElementById('modelSelect').disabled);await settled();
      assert.equal(await page.locator('html').getAttribute('lang'),'en');
      console.log(JSON.stringify({mode,passed:true,checks:'compact popover, viewport bounds, locale persistence, history, selected-model translation and English replies'}));
    }
    let failures=0;
    await page.route('**/v1/localization',route=>{failures++;return route.fulfill({status:503,contentType:'application/json',body:'{"error":"test_outage"}'});});
    await page.evaluate(()=>addMessage('assistant','用于检查翻译失败是否无限重试的新文本XYZ。'));
    await page.locator('#translationRetry').waitFor({state:'visible'});
    const stoppedAt=failures;
    await page.waitForTimeout(700);
    assert.equal(failures,stoppedAt,'No automatic retry loop after a translation error');
    await page.unroute('**/v1/localization');
    await page.locator('#translationRetry').click();await settled();
    let release;
    const held=new Promise(resolve=>release=resolve);
    await page.route('**/v1/localization',async route=>{await held;await route.continue();});
    const waiting=page.waitForRequest(r=>r.url().endsWith('/v1/localization'));
    await page.evaluate(()=>addMessage('assistant','用于检查迟到翻译的新文本ABC。'));
    await waiting;
    await page.locator('#languageToggle').click();
    release();await page.waitForTimeout(500);
    assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
    assert.equal(await page.locator('.message-bubble').last().innerText(),'用于检查迟到翻译的新文本ABC。');
    await page.unroute('**/v1/localization');
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
