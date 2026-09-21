"use strict";
// Real review page; API evidence and translation responses are local test doubles.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const base=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''))throw Error('Local fixture URL required');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  try {
    for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT']) {
      const context=await browser.newContext({viewport:{width:1100,height:900}}),page=await context.newPage();
      await context.addInitScript(()=>localStorage.setItem('ece329-interface-language','zh'));
      const record={id:'f'.repeat(32),version:1,status:'candidate',reviews:[],
        content:{summary:'经验摘要需推进流程',trigger:'确认前一方案后',recommendation:'询问具体缺项',verification:'核对阶段变化'},
        evidence:{scope:'global',message:'反馈原文保持不变',evidence:{mode,evidence_schema_version:2,event_chain:[
          {position:'reported',ref:'turn:2',revision:2,mode,stage:'IDEA_BRAINSTORMING',user:'用户原始确认',assistant:'助手原始回复',recorded_fields:{user:true,assistant:true}}]},
          extraction_analysis:{diagnosis:{expected_behavior:'根据当前阶段继续询问缺项',facts:[{evidence_ref:'turn:2',observation:'观察到没有推进流程'}],
            hypotheses:['可能没有处理推进请求'],applicability:'前一方案已确认',exceptions:'用户要求修改时',unknowns:['具体原因尚未核实']},
            model_check:{evidence_supported:true,issues:'仍需人工核对'},validation_status:'not_replayed'}}};
      let calls=0,failed=false;const inputs=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/v1/feedback/experiences?*',r=>r.fulfill({json:{experiences:[record]}}));
      await page.route('**/v1/localization',r=>{
        calls++;const body=r.request().postDataJSON();inputs.push(...body.texts);
        return failed?r.fulfill({status:503,json:{error:'test failure'}}):r.fulfill({json:{language:body.language,translations:body.texts.map(text=>body.language==='zh'?'中文译文 '+text:'English analysis '+text.replace(/[\u3400-\u9fff]/g,''))}});
      });
      await page.goto(base+'/feedback-review.html');await page.locator('#reviewToken').fill('test-only');
      await page.locator('#reviewFilter').selectOption('candidate');await page.locator('#reviewLogin button[type=submit]').click();
      const editor=page.locator('#content-'+record.id),note=page.locator('#note-'+record.id);
      await editor.waitFor();await note.fill('我的审阅草稿');
      const original=await editor.inputValue();
      const waitEnglish=()=>page.waitForFunction(()=>{
        const nodes=[...document.querySelectorAll('[data-i18n-translate]')];
        return nodes.length>10&&nodes.every(n=>n.textContent.startsWith('English analysis'));
      });
      await page.locator('#languageToggle').click();await waitEnglish();
      assert.match(await page.locator('.evidence-analysis').innerText(),/turn:2/);
      assert.doesNotMatch(await page.locator('.evidence-analysis').innerText(),/[\u3400-\u9fff]/);
      assert.match(await page.locator('.evidence-turn-reported').innerText(),/用户原始确认|助手原始回复/);
      assert.equal(await editor.inputValue(),original);assert.equal(await note.inputValue(),'我的审阅草稿');
      assert.ok(inputs.includes('观察到没有推进流程'));
      assert.ok(!inputs.some(t=>['用户原始确认','助手原始回复','反馈原文保持不变','turn:2'].includes(t)));
      const oldCalls=calls;
      await page.locator('#languageToggle').click();await page.waitForFunction(()=>document.querySelector('.evidence-analysis').textContent.includes('根据当前阶段继续询问缺项'));
      await page.locator('#languageToggle').click();await waitEnglish();await page.waitForTimeout(200);
      assert.equal(calls,oldCalls,'A language round trip must reuse translations');
      failed=true;record.evidence.extraction_analysis.diagnosis.expected_behavior='新分析正文需要翻译';
      await page.locator('#reviewLogin button[type=submit]').click();
      await page.locator('#translationStatus').filter({hasText:'Some content could not be translated.'}).waitFor();
      assert.doesNotMatch(await page.locator('.evidence-analysis').innerText(),/[\u3400-\u9fff]/);
      assert.match(await page.locator('.evidence-analysis').innerText(),/Translation unavailable/);
      assert.match(await page.locator('.evidence-turn-reported').innerText(),/用户原始确认|助手原始回复/);
      await page.locator('#translationStatusClose').click();
      await page.evaluate(()=>window.ECE329I18n.refresh());
      assert.equal(await page.locator('#translationStatus').isVisible(),false);
      const failedCalls=calls;await page.waitForTimeout(250);assert.equal(calls,failedCalls,'Failure must not cause a retry loop');
      failed=false;await page.locator('#translationRetry').click();await waitEnglish();
      assert.equal(await note.inputValue(),'我的审阅草稿');assert.equal(await editor.inputValue(),original);
      await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copied=text}}));
      const raw=page.locator('.review-evidence > details').nth(1);await raw.locator('summary button').click();
      await raw.getByRole('button',{name:'Copy JSON',exact:true}).click();
      assert.deepEqual(JSON.parse(await page.evaluate(()=>window.copied)),record);
      record.evidence.extraction_analysis.diagnosis.expected_behavior='Ask for missing information.';
      await page.locator('#reviewLogin button[type=submit]').click();
      await page.locator('.evidence-analysis').getByText('Ask for missing information.',{exact:true}).waitFor();
      failed=true;
      await page.locator('#languageToggle').click();
      await page.locator('.evidence-analysis').getByText('翻译暂不可用，请重试。',{exact:true}).waitFor();
      assert.equal(await page.locator('.evidence-analysis').getByText('Ask for missing information.',{exact:true}).count(),0);
      const failedChineseCalls=calls;await page.waitForTimeout(250);assert.equal(calls,failedChineseCalls);
      await page.locator('#languageToggle').click();
      await page.locator('.evidence-analysis').getByText('Ask for missing information.',{exact:true}).waitFor();
      failed=false;
      await page.locator('#languageToggle').click();
      await page.locator('.evidence-analysis').getByText('中文译文 Ask for missing information.',{exact:true}).waitFor();
      const chineseCalls=calls;await page.waitForTimeout(250);assert.equal(calls,chineseCalls);
      await page.locator('#languageToggle').click();
      await page.locator('.evidence-analysis').getByText('Ask for missing information.',{exact:true}).waitFor();
      assert.equal(await note.inputValue(),'我的审阅草稿');
      assert.deepEqual(errors,[]);await context.close();
    }
    console.log('PASS: Guided and EMVR analysis prose translates; source evidence/JSON/drafts remain intact; cache and bounded explicit retry verified');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
