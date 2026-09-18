"use strict";
// Run against tests/feedback_smoke_server only; no paid API calls.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) throw Error('Local fixture required');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.ECE329_SMOKE_BROWSER});
  const page = await browser.newPage({viewport:{width:1440,height:1080}}), errors=[], turns=[];
  page.on('pageerror', error=>errors.push(error.message));
  page.on('request', request=>{if(request.method()==='POST' && /\/v1\/designs(?:\/[^/]+\/turns)?$/.test(request.url())) turns.push(request.url());});
  const seed = async (mode='EMVR_DIRECT')=>page.evaluate(mode=>{
    state.designId='fold-test';state.mode=mode;
    state.evidence=[{id:'wave',title:'Wave equation',pages:[171,178],concepts:['Wave propagation']}];
    state.notes=['实验对象：电流环和电流滑块，保持电流环位置不变，观察圆心磁场的变化。'];
    state.qualityReview={status:'READY',causal_chain:{cause:'调整电流',response:'磁场读数'},feasibility:{},issues:[{finding:'需要补充变量范围'}]};
    state.taskReport={idea:'测试实验',sections:[{stage_id:'IDEA_BRAINSTORMING',title:'实验构思',items:[{label:'研究对象',value:'电流环'}]}]};
    state.unityLayout={design_id:'fold-test',revision:1,summary:[{label:'实验对象',items:['电流环','电流滑块']},{label:'操作',items:['调节电流']}],nodes:[],relations:[],questions:[]};
    render();
  },mode);
  try {
    await page.goto(base);await page.waitForFunction(()=>typeof render==='function');await seed();
    const cards=page.locator('.insight-panel > .insight-card');
    assert.equal(await cards.count(),5);
    assert.equal(await page.locator('.insight-card-fold').count(),5);
    for (const card of await cards.all()) {
      const fold=card.locator(':scope > details');
      await fold.locator(':scope > summary').click();assert.equal(await fold.evaluate(n=>n.open),false);
      assert.equal(await fold.locator(':scope > .insight-fold-body').isVisible(),false);
      await fold.locator(':scope > summary').focus();await page.keyboard.press('Enter');
      assert.equal(await fold.evaluate(n=>n.open),true);
    }
    const objects=page.locator('[data-unity-summary] > details').first();
    await objects.locator('summary').click();
    const evidence=page.locator('.evidence-item > details');await evidence.locator('summary').click();
    const report=page.locator('.task-report-section');
    if(!await report.evaluate(n=>n.open)) await report.locator(':scope > summary').click();
    const field=page.locator('.insight-field-toggle');await field.click();
    assert.equal(await field.getAttribute('aria-expanded'),'false');
    await page.evaluate(()=>{state.unityLayout.summary[0].items=['修改后的电流环'];render();});
    assert.equal(await objects.evaluate(n=>n.open),false);
    assert.equal(await evidence.evaluate(n=>n.open),false);
    assert.equal(await field.getAttribute('aria-expanded'),'false');
    await objects.locator('summary').click();assert.ok((await objects.innerText()).includes('修改后的电流环'));
    const source=page.locator('.source-details');
    if(!await source.evaluate(n=>n.open))await source.locator('summary').click();
    await page.evaluate(()=>{state.designId='fold-other';render();});
    assert.equal(await source.evaluate(n=>n.open),false);
    await page.evaluate(()=>{state.designId='fold-test';render();});
    assert.equal(await source.evaluate(n=>n.open),true);
    await page.evaluate(()=>{
      state.taskReport.sections[0].items=[{label:'相同标签',value:'内容 A'},{label:'相同标签',value:'内容 B'}];render();
    });
    const duplicateFields=page.locator('.insight-field-toggle');
    const controls=await duplicateFields.evaluateAll(nodes=>nodes.map(n=>n.getAttribute('aria-controls')));
    assert.equal(new Set(controls).size,2);
    await duplicateFields.first().click();await page.evaluate(()=>render());
    assert.equal(await duplicateFields.first().getAttribute('aria-expanded'),'false');
    assert.equal(await duplicateFields.last().getAttribute('aria-expanded'),'true');
    // Parent collapse preserves the child's choice, including a fresh rendering.
    const unity=page.locator('#unityLayoutCard > details');await unity.locator(':scope > summary').click();
    await seed();assert.equal(await unity.evaluate(n=>n.open),false);
    await page.reload();await page.waitForFunction(()=>typeof render==='function');await seed();
    assert.equal(await unity.evaluate(n=>n.open),false);
    await unity.locator(':scope > summary').click();
    await seed('GUIDED_DESIGN');assert.equal(await page.locator('#unityLayoutCard').isVisible(),false);
    await seed();assert.equal(await page.locator('#unityLayoutCard').isVisible(),true);
    await page.locator('#languageToggle').click();
    await page.getByRole('heading',{name:'Unity Layout Diagram',exact:true}).waitFor();
    await page.locator('[data-unity-summary] summary').filter({hasText:'Experiment objects'}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    for(const card of await cards.all()) {
      if(await card.isVisible()) {
        const fold=card.locator(':scope > details');
        if(await fold.evaluate(n=>n.open)) await fold.locator(':scope > summary').click();
      }
    }
    await page.locator('.insight-panel').screenshot({path:path.resolve(__dirname,'../.test-tmp/insight-folds-mobile.png')});
    assert.deepEqual(errors,[]);assert.deepEqual(turns,[]);
    console.log(JSON.stringify({passed:true,checks:'five cards; nested sections and report fields; keyboard; rerendered content; parent/child independence; cross-design isolation; same-label fields; reload persistence; Guided hiding; English; mobile; no design requests'}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
