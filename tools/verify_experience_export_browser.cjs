// Browser-only fixture: every request is intercepted; no live service or credentials.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
  const context=await browser.newContext({acceptDownloads:true});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let item,serial=0,downloads=0;
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!=='https://export.test')return route.abort();
    if(url.pathname==='/assets/config.js')return route.fulfill({contentType:'application/javascript',body:'window.ECE329_CONFIG={API_BASE_URL:"https://export.test"};'});
    if(url.pathname==='/v1/feedback/experiences')return route.fulfill({json:{experiences:[item]}});
    if(url.pathname==='/v1/localization')return route.fulfill({json:{translations:route.request().postDataJSON().texts.map(()=> 'Translated display only')}});
    const file=path.resolve(__dirname,'../docs','.'+url.pathname);
    const root=path.resolve(__dirname,'../docs')+path.sep;
    if(!file.startsWith(root)||!fs.existsSync(file))return route.abort();
    return route.fulfill({path:file});
  });
  try {
    await page.goto('https://export.test/feedback-review.html');
    await page.locator('#reviewToken').fill('export-fixture-token');
    for(const mode of ['GUIDED_DESIGN','EMVR_DIRECT'])for(const status of ['candidate','active','stopped']){
      item={id:(++serial).toString(16).padStart(32,'0'),version:3,status,
        content:{summary:'原始经验',trigger:'原始条件',recommendation:'原始建议',verification:'原始验证',modes:[mode],stages:['IDEA_BRAINSTORMING'],keywords:[]},
        evidence:{scope:'global',mode,message:'原始用户反馈',evidence:{reported_turn:{user:'保留设计并继续',assistant:''}}},reviews:[{version:2,note:'原始审阅意见'}]};
      await page.locator('#reviewFilter').selectOption(status);
      await page.locator('#reviewLogin button[type=submit]').click();
      await page.locator(`#content-${item.id}`).waitFor();
      await page.locator(`#note-${item.id}`).fill('Do not save this draft');
      for(const language of ['en','zh']){
        await page.locator('#languageToggle').click();
        const label=language==='en'?'Export experience and context (JSON)':'导出经验与上下文（JSON）';
        const pending=page.waitForEvent('download');
        await page.getByRole('button',{name:label,exact:true}).click();
        const download=await pending;
        assert.equal(download.suggestedFilename(),`ece329-experience-${item.id}-v3.json`);
        const chunks=[];for await(const chunk of await download.createReadStream())chunks.push(chunk);
        assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString('utf8')),item);
        assert.equal(await page.locator(`#note-${item.id}`).inputValue(),'Do not save this draft');downloads++;
      }
    }
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,downloads,modes:2,statuses:3,languages:2,narrowLayout:true}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
