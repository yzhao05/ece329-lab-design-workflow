"use strict";
// Real DOM stress test with a local fake translation function; no API calls.
const {chromium}=require('playwright');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.setContent('<title>Cache regression</title><button id="languageToggle" data-i18n-ignore></button><button id="translationRetry" hidden></button><main></main>');
    await page.evaluate(()=>{
      window.translatedCounts={};window.translationCalls=0;
      window.requestDisplayTranslation=async texts=>{
        window.translationCalls++;
        return {translations:texts.map(t=>{
          window.translatedCounts[t]=(window.translatedCounts[t]||0)+1;
          return 'English '+t.replace(/[\u3400-\u9fff]/g,'');
        })};
      };
      const root=document.querySelector('main');
      for(let i=0;i<2050;i++) {
        const el=document.createElement('span');el.textContent=`独立文本 ${i}`;
        if(i<10)el.title=`独立标题 ${i}`;
        root.append(el);
      }
    });
    await page.addScriptTag({path:path.resolve(__dirname,'../docs/assets/i18n.js')});
    await page.evaluate(()=>window.ECE329I18n.setLanguage('en'));
    await page.waitForFunction(()=>[...document.querySelectorAll('main span')].every(n=>n.textContent.startsWith('English ')),null,{timeout:60000});
    const calls=await page.evaluate(()=>window.translationCalls);
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(()=>window.translationCalls),calls);
    assert.equal(await page.evaluate(()=>Math.max(...Object.values(window.translatedCounts))),1);
    await page.evaluate(()=>window.ECE329I18n.setLanguage('zh'));
    await page.waitForFunction(()=>document.querySelector('main span').textContent==='独立文本 0');
    await page.evaluate(()=>window.ECE329I18n.setLanguage('en'));
    await page.waitForFunction(()=>document.querySelector('main span').textContent.startsWith('English '));
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(()=>window.translationCalls),calls,'Language round trip must reuse live-node translations');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,texts:2050,attributes:10,calls,checks:'LRU eviction, no repeated translation, reversible language'}));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
