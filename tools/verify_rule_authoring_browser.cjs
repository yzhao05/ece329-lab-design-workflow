// Local fixture only; never use real credentials or a deployed design.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''))throw Error('Local fixture URL required');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  try {
    const created=await context.request.post(`${base}/v1/designs`,{data:{idea:'研究电流环磁场',interaction_state:'EMVR_DIRECT'}});
    assert.equal(created.status(),201);const design=await created.json();
    const submitted=await context.request.post(`${base}/v1/designs/${design.design_id}/feedback`,{
      headers:{Authorization:`Bearer ${design.design_access_token}`},
      data:{message:'不要修改保留当前设计并继续，却仍询问旧修改',category:'cross_stage_edit',request_id:'browser-rule-feedback-1'}});
    assert.equal(submitted.status(),201);
    const headers={'X-ECE329-Feedback-Admin-Token':'smoke-maintainer'};
    let item;
    for(let i=0;i<30&&!item;i++){
      const body=await (await context.request.get(`${base}/v1/feedback/experiences?status=candidate`,{headers})).json();
      item=body.experiences?.find(x=>x.evidence.scope_design_id===design.design_id);
      if(!item)await page.waitForTimeout(200);
    }
    assert.ok(item);
    await page.goto(`${base}/feedback-review.html`);
    await page.locator('#reviewToken').fill('smoke-maintainer');await page.locator('#reviewFilter').selectOption('candidate');
    await page.locator('#reviewLogin button[type=submit]').click();
    const editor=page.locator(`#content-${item.id}`),note=page.locator(`#note-${item.id}`);
    await editor.waitFor();await note.fill('明确拒绝旧修改时，保留当前设计并继续；必须检查缺项。');
    await page.getByRole('button',{name:'根据审阅意见生成修订草案',exact:true}).click();
    await page.getByText('已生成草案；当前生效版本未改变。',{exact:true}).waitFor();
    const current=JSON.parse(await editor.inputValue());current.summary='人工并发编辑';await editor.fill(JSON.stringify(current));
    await page.getByRole('button',{name:'将草案填入 JSON',exact:true}).click();
    assert.equal(JSON.parse(await editor.inputValue()).summary,'人工并发编辑');
    await page.getByRole('button',{name:'确认用草案替换当前 JSON',exact:true}).click();
    assert.ok(JSON.parse(await editor.inputValue()).execution);
    await page.getByText('结构化条件与受控动作',{exact:true}).click();
    await page.getByText('缺项询问是必要行为，不能关闭。旧规则未列出此动作时，也会依据阶段检查提出具体问题。',{exact:true}).waitFor();
    assert.equal(await page.locator('.rule-authoring input[type=checkbox]').count(),1);
    const pendingTypes=page.locator('.rule-authoring select[multiple]');
    await pendingTypes.selectOption(['CONFIRM_STAGE_OR_MODIFY','ANSWER_STAGE_QUESTION']);
    await page.getByRole('button',{name:'根据结构化设置生成草案',exact:true}).click();
    await page.getByText('已生成草案；当前生效版本未改变。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'将草案填入 JSON',exact:true}).click();
    assert.deepEqual(JSON.parse(await editor.inputValue()).execution.conditions.pending_types,['CONFIRM_STAGE_OR_MODIFY','ANSWER_STAGE_QUESTION']);
    const saved=await editor.inputValue();
    await page.locator('#reviewLogin button[type=submit]').click();await editor.waitFor();
    assert.equal(await editor.inputValue(),saved);assert.ok((await note.inputValue()).includes('必须检查缺项'));
    assert.deepEqual(await pendingTypes.evaluate(el=>Array.from(el.selectedOptions,o=>o.value)),['CONFIRM_STAGE_OR_MODIFY','ANSWER_STAGE_QUESTION']);
    await page.locator('#languageToggle').click();
    await page.getByRole('button',{name:'Replay current JSON in isolation',exact:true}).waitFor();
    await page.getByRole('button',{name:'Run live semantic evaluation and isolated workflow replay',exact:true}).waitFor();
    assert.ok((await page.locator('.rule-authoring').textContent()).includes('Asking for missing information is required and cannot be disabled.'));
    assert.equal(await editor.inputValue(),saved);
    await page.locator('#languageToggle').click();
    await page.getByRole('button',{name:'运行真实模型语义评测及隔离流程回放',exact:true}).click();
    await page.getByText('模型评测已记录；执行器、语义与完整回放结果分别展示。',{exact:true}).waitFor({timeout:120000});
    assert.ok((await page.locator('.rule-authoring').innerText()).includes('语义评测: 通过'));
    await page.getByRole('button',{name:'隔离回放当前 JSON',exact:true}).click();
    await page.getByText(/隔离模拟回放通过/).waitFor({timeout:120000});
    await page.getByText('我已核对实际执行条件；没有被遗漏的文字限制',{exact:true}).locator('..').locator('input').check();
    await page.getByRole('button',{name:'启用经验',exact:true}).click();
    await page.locator('#reviewStatus').filter({hasText:'已保存'}).waitFor();
    const approved=await (await context.request.get(`${base}/v1/feedback/experiences?experience_id=${item.id}`,{headers})).json();
    assert.equal(approved.experiences[0].status,'active');assert.ok(approved.experiences[0].reviews[0].content.source.validation_id);
    await page.locator('#reviewFilter').selectOption('active');await page.locator('#reviewLogin button[type=submit]').click();
    await editor.waitFor();await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    const out=path.resolve(__dirname,'../.test-tmp/rule-authoring-browser');fs.mkdirSync(out,{recursive:true});
    await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,experience:item.id,checks:'draft conflict, explicit apply, refresh, bilingual labels, exact-version replay/approval, narrow layout'}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
