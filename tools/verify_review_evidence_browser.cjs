"use strict";
// Browser interaction checks use deterministic API evidence; no model calls.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''))throw Error('Local fixture URL required');
const output=path.resolve(__dirname,'../.test-tmp/review-evidence-browser');fs.mkdirSync(output,{recursive:true});
const action={type:'CONFIRM_STAGE_OR_MODIFY',action_id:'action_9_old',status:'PENDING',created_at_revision:9,
  question:'请确认实验设计边界。',proposal:{items:[{value:'电磁波传播速度与波长'}]},answer_fields:['research_object','observations'],
  allowed_intents:['ACCEPT_PREVIOUS_PROPOSAL'],editable_field_bindings:[{canonical_field:'observations',visible_labels:['观察量']}]};
const state={revision:9,stage:'IDEA_BRAINSTORMING',completed_stages:[],pending_excerpt:JSON.stringify(action),confirmation_excerpt:JSON.stringify({topic_lock:{locked:false}})};
const after={...state,revision:10,pending_excerpt:JSON.stringify({...action,action_id:'action_10_new',created_at_revision:10}),completion_error:'请先结合当前VR实验回答这一问。'};
const record={id:'a'.repeat(32),version:1,status:'candidate',extra_field:{preserve:true},reviews:[{note:'原始审阅记录'}],
  content:{summary:'确认后的流程推进',trigger:'用户确认当前方案后',recommendation:'检查实际状态并推进下一任务',verification:'核对前后记录'},
  evidence:{message:'说了继续后没有推进。<img src=x onerror="window.evidenceAttack=1">',category:'answered_pending',scope:'global',
    evidence:{evidence_schema_version:2,evidence_migration:{version:1,source:'saved_history',recovered_fields:['event_chain'],limitations:['historical_state_unavailable']},event_chain:[
      {position:'before',revision:9,stage:'IDEA_BRAINSTORMING',user:'我想研究磁场',assistant:'请确认实验设计边界。\n电磁波传播速度与波长',recorded_fields:{assistant:true}},
      {position:'reported',revision:10,stage:'IDEA_BRAINSTORMING',mode:'EMVR_DIRECT',user:'继续。',assistant:'',recorded_fields:{assistant:true},warnings:['观察位置需要明确'],student_task:'请选择观察位置',
       resolved_intent:{intent:'ACCEPT_PREVIOUS_PROPOSAL',advance_requested:true},state_before:state,state_after:after,truncated_fields:['warnings']}],
      current_state:{...after,revision:11,completion_error:null}},
    extraction_analysis:{diagnosis:{expected_behavior:'继续处理下一项',facts:[{evidence_ref:'turn:10',observation:'回复摘录为空'}],hypotheses:['可能没有处理推进信号'],applicability:'明确确认之后',exceptions:'用户要求修改',unknowns:['未进行真实回放']},validation_status:'not_replayed'},
    analysis_attempts:[{attempt:3,provider:'openai',model:'gpt-5.4-mini',phase:'check',status:'failed',code:'model_connection_error'},
      {attempt:4,provider:'openai',model:'gpt-5.4-mini',phase:'validate_draft',status:'failed',diagnostic:{version:1,source:'local_validation',code:'schema_validation_failed',reason:'max_length',field_path:'candidate.trigger',limit:140,actual_length:168}},
      {attempt:5,provider:'deepseek',model:'deepseek-flash',status:'completed'}],
    attachments:[{role:'problem',data_url:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4N8AAAAASUVORK5CYII='}]} };
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  try {
    await context.addInitScript(()=>localStorage.setItem('ece329-interface-language','zh'));
    const page=await context.newPage(),errors=[],translated=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/v1/feedback/experiences?*',route=>route.fulfill({json:{experiences:[record]}}));
    await page.route('**/v1/localization',route=>{const body=route.request().postDataJSON();translated.push(...body.texts);return route.fulfill({json:{language:body.language,translations:body.texts}});});
    await page.goto(base+'/feedback-review.html');
    await page.locator('#reviewToken').fill('test-maintainer');await page.locator('#reviewFilter').selectOption('candidate');
    await page.locator('#reviewLogin button[type=submit]').click();
    const viewer=page.locator('.review-evidence');await viewer.waitFor();
    const readable=viewer.locator(':scope > details').nth(0),raw=viewer.locator(':scope > details').nth(1);
    assert.equal(await readable.evaluate(el=>el.open),true);assert.equal(await raw.evaluate(el=>el.open),false);
    await page.waitForFunction(()=>document.querySelector('.review-evidence img')?.complete);
    assert.equal(await viewer.locator('img').evaluate(img=>img.naturalWidth>0),true);
    assert.match(await readable.innerText(),/该轮回复正文为空/);assert.match(await readable.innerText(),/以下记录已截断/);
    assert.match(await readable.innerText(),/模型连接失败/);
    const diagnostic=viewer.locator('.feedback-attempt').nth(1);
    assert.match(await diagnostic.innerText(),/触发条件：字段超过长度限制.*168 \/ 140 字符/);
    assert.match(await diagnostic.innerText(),/检查建议/);
    assert.equal(await diagnostic.locator('details').evaluate(el=>el.open),false);
    await diagnostic.locator('summary button').click();
    assert.match(await diagnostic.innerText(),/candidate.trigger/);
    await diagnostic.locator('summary button').click();
    const event=viewer.locator('.evidence-event-summary'),technical=viewer.locator('[data-evidence-detail=technical]'),submission=viewer.locator('[data-evidence-detail=submission]');
    assert.equal(await technical.evaluate(el=>el.open),false);assert.equal(await submission.evaluate(el=>el.open),false);
    const eventText=await event.innerText();
    assert.match(eventText,/9 → 10/);assert.match(eventText,/待办标识已更换/);
    assert.doesNotMatch(eventText,/action_9_old|action_10_new|请确认实验设计边界。|电磁波传播速度与波长|editable_field_bindings/);
    assert.equal((await readable.innerText()).split('请确认实验设计边界。').length-1,1);
    await submission.locator(':scope > summary button').click();
    assert.match(await submission.innerText(),/提交时状态与问题轮结束状态一致/);
    assert.doesNotMatch(await submission.innerText(),/电磁波传播速度与波长/);
    await submission.locator(':scope > summary button').click();
    assert.equal(await page.evaluate(()=>window.evidenceAttack),undefined);
    await page.locator('#note-'+record.id).fill('审阅草稿保留');
    await readable.locator(':scope > summary button').click();assert.equal(await readable.evaluate(el=>el.open),false);
    await raw.locator('summary button').click();assert.equal(await raw.evaluate(el=>el.open),true);
    await page.evaluate(()=>{window.copiedEvidence=null;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.copiedEvidence=value}});});
    await raw.getByRole('button',{name:'复制 JSON',exact:true}).click();assert.deepEqual(JSON.parse(await page.evaluate(()=>window.copiedEvidence)),record);
    const wait=page.waitForEvent('download');await raw.getByRole('button',{name:'下载 JSON',exact:true}).click();
    const download=await wait;await download.saveAs(path.join(output,'evidence.json'));assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output,'evidence.json'),'utf8')),record);
    await page.locator('#reviewLogin button[type=submit]').click();await page.waitForTimeout(150);
    assert.equal(await page.locator('#note-'+record.id).inputValue(),'审阅草稿保留');
    await viewer.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'evidence-zh.png')});
    await technical.locator(':scope > summary button').click();
    assert.match(await technical.innerText(),/action_9_old/);assert.match(await technical.innerText(),/action_10_new/);
    await technical.locator(':scope > summary button').click();
    await diagnostic.locator('summary button').click();
    await page.locator('#languageToggle').click();await page.waitForFunction(()=>document.querySelector('.review-evidence').textContent.includes('Conversation and state'));
    assert.equal(await diagnostic.locator('details').evaluate(el=>el.open),true);
    await diagnostic.locator('summary button').click();
    assert.match(await readable.innerText(),/This turn has an empty reply body/);assert.match(await readable.innerText(),/继续。/);
    assert.match(await readable.innerText(),/Model connection failed/);
    assert.match(await diagnostic.innerText(),/Trigger: Field exceeds the length limit.*168 \/ 140 characters/);
    assert.match(await diagnostic.innerText(),/Check:.*shorten/);
    assert.match(await event.innerText(),/pending-action ID changed/);
    assert.equal(await page.locator('#note-'+record.id).inputValue(),'审阅草稿保留');
    await page.setViewportSize({width:390,height:844});await viewer.evaluate(el=>el.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(output,'evidence-en-mobile.png')});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
    assert.ok(!translated.some(text=>text.includes('继续。')||text.includes('可能没有处理推进信号')));
    assert.deepEqual(errors,[]);
    console.log('PASS: independent folds, draft refresh/language retention, literal evidence, JSON copy/download, image preview and narrow layout');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
