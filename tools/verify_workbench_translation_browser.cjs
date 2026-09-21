"use strict";
// Real workbench DOM with local API fixtures; no paid model requests.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const base=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''))throw Error('Local fixture URL required');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  try {
    for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const context=await browser.newContext({viewport:{width:390,height:900}}),page=await context.newPage();
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      let fail=true,calls=0;
      await page.route('**/v1/localization',async route=>{
        calls++;const body=route.request().postDataJSON();
        assert.ok(body.texts.join('').length<=6000);
        await new Promise(resolve=>setTimeout(resolve,80));
        await route.fulfill(fail && calls===1
          ? {status:502,json:{error:'model_output_invalid',translation_reason:'output_truncated',detail:'PRIVATE PROVIDER CONTENT'}}
          : {json:{translations:body.texts.map(t=>'Translated '+t.replace(/[\u3400-\u9fff]/g,''))}});
      });
      await page.goto(base);
      await page.waitForFunction(()=>!document.getElementById('modelSelect').disabled);
      const response=await page.request.post(base+'/v1/designs',{data:{idea:'电场叠加',interaction_state:mode}});
      assert.equal(response.status(),201);
      const design=await response.json();
      await page.evaluate(d=>{
        invalidateDesignRequests();applyDesignSnapshot(d);render();
        if(state.mode==='EMVR_DIRECT') {
          state.unityLayout={design_id:state.designId,revision:d.revision || 0,nodes:[],relations:[],
            summary:[{label:'实验对象',items:['介质块作为传播环境','平面 TEM 波作为研究对象','可移动探针作为测量方式']},
              {label:'可用交互',items:['固定其他条件，连续改变一个公式变量并记录响应']},
              {label:'可观察',items:['电场和磁场波形、方向与相位','本征阻抗及E/H幅值比']}],
            notes:['波动方程形式要求均匀、无源、非导电且μ、ε为常数。']};
          renderUnityLayout();
        }
        addMessage('user','问题原文保持可见 123');
        addMessage('assistant','回答原文保持可见 456');
        addMessage('assistant','长对话需要分批翻译。'.repeat(900));
      },design);
      const before=await page.evaluate(()=>JSON.stringify(state));
      await page.locator('#languageToggle').click();
      await page.locator('#translationRetry').waitFor();
      await page.waitForFunction(()=>!document.querySelector('main').textContent.includes('Translating…'));
      assert.doesNotMatch(await page.locator('main').innerText(),/[\u3400-\u9fff]/);
      assert.match(await page.locator('#translationStatus').innerText(),/output was truncated/);
      assert.doesNotMatch(await page.locator('body').innerText(),/PRIVATE PROVIDER CONTENT/);
      assert.ok(calls>1,'A rejected batch must not block subsequent text batches');
      await page.locator('#translationStatusClose').click();
      await page.evaluate(()=>window.ECE329I18n.refresh());
      assert.equal(await page.locator('#translationStatus').isVisible(),false);
      assert.equal(await page.locator('#translationRetry').isVisible(),true);
      const failures=calls;await page.waitForTimeout(250);assert.equal(calls,failures,'No automatic retry loop');
      assert.equal(await page.evaluate(()=>JSON.stringify(state)),before,'Translation never changes the design');
      fail=false;await page.locator('#translationRetry').click();
      await page.waitForFunction(()=>document.querySelector('#messageList').textContent.includes('Translated  456'));
      await page.waitForFunction(()=>document.getElementById('translationStatus').hidden);
      assert.doesNotMatch(await page.locator('main').innerText(),/[\u3400-\u9fff]|Translation unavailable|Translating…/);
      if(mode==='EMVR_DIRECT')assert.match(await page.locator('[data-unity-summary]').innerText(),/Translated .*TEM/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      const completed=calls;
      await page.locator('#languageToggle').click();
      await page.getByText('回答原文保持可见 456',{exact:true}).waitFor();
      await page.locator('#languageToggle').click();
      await page.waitForFunction(()=>document.querySelector('#messageList').textContent.includes('Translated  456'));
      await page.waitForTimeout(250);assert.equal(calls,completed,'Cache prevents repeated requests');
      assert.equal(await page.evaluate(()=>JSON.stringify(state)),before);
      assert.deepEqual(errors,[]);await context.close();
    }
    console.log('PASS: both modes use English display, isolate rejected batches, dismiss notices, retry explicitly, cache translations, preserve source state and fit narrow screens');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
