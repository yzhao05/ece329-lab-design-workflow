"use strict";
// Local fixture only. Python-generated cases use unity_layout_snapshot, never a paid API.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.argv[2];if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''))throw Error('Local fixture required');
const cases=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../.test-tmp/unity-layout-cases.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ECE329_SMOKE_BROWSER});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.goto(base);await page.waitForFunction(()=>typeof connectionState!=='undefined'&&connectionState!=='checking');
  const card=page.locator('#unityLayoutCard');assert.equal(await card.isVisible(),false);
  assert.equal(await page.locator('#theoryChart,#chartParameter').count(),0);
  const apply=async(layout,mode='EMVR_DIRECT')=>page.evaluate(({layout,mode})=>{
   applyDesignSnapshot({design_id:layout?.design_id||'layout-test',interaction_state:mode,current_stage:'IDEA_BRAINSTORMING',unity_layout:layout});
  },{layout,mode});
  await apply(null);assert.equal(await card.isVisible(),true);assert.equal(await card.locator('svg').count(),0);
  await apply(cases[0]);assert.equal(await card.locator('svg').count(),2);
  assert.ok((await card.innerText()).includes('右侧'));assert.equal(await card.locator('.unity-flow').count(),1);
  await card.screenshot({path:path.resolve(__dirname,'../.test-tmp/unity-layout-desktop.png')});
  await apply(cases[1]);assert.equal(await card.getAttribute('data-revision'),'4');
  assert.ok(!(await card.innerText()).includes('右侧'));
  await apply(cases[0]);assert.equal(await card.getAttribute('data-revision'),'4');
  await apply(cases[2]);assert.equal(await card.locator('svg').count(),0);
  assert.equal(await card.locator('.unity-flow').count(),0);
  await page.locator('#chatInput').fill('保留我的草稿');await card.getByRole('button',{name:'补充位置说明'}).click();
  assert.equal(await page.locator('#chatInput').inputValue(),'保留我的草稿');
  await page.locator('#chatInput').fill('');await card.getByRole('button',{name:'补充位置说明'}).click();
  assert.ok((await page.locator('#chatInput').inputValue()).includes('实验对象'));
  await apply(cases[2],'GUIDED_DESIGN');assert.equal(await card.isVisible(),false);
  // A different design can start with its own revision; old local data is not reused.
  await apply({...cases[0],design_id:'layout-other'});
  await page.setViewportSize({width:390,height:844});await card.scrollIntoViewIfNeeded();
  await page.evaluate(()=>document.activeElement?.blur());
  const bounds=await card.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=391);
  await card.screenshot({path:path.resolve(__dirname,'../.test-tmp/unity-layout-mobile.png')});
  await page.locator('#languageToggle').click();await card.getByRole('heading',{name:'Unity Layout Diagram',exact:true}).waitFor();
  await card.getByText('Method diagram, not an actual simulation; positions and dimensions are not to scale',{exact:true}).waitFor();
  const note='已有位置说明未全部绘出，请以文字为准；无需重复确认已说明的位置。';
  await apply({...cases[0],design_id:'layout-other',revision:9,relations:[],flows:[],notes:[note]});
  await card.getByText('Some existing position details could not be drawn. Refer to the written details; established positions do not need to be confirmed again.',{exact:true}).waitFor();
  assert.equal(await card.locator('svg,button').count(),0);
  await apply({...cases[0],design_id:'layout-other',revision:10,nodes:[null]});
  assert.equal(await card.locator('svg,.unity-flow,button').count(),0);
  assert.equal(await card.getAttribute('data-revision'),null);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,checks:'Guided hidden; EMVR partial diagram; changed/cleared/stale snapshots; cleared arrows; preserved draft; mobile layout; English title/disclaimer/parse notes; malformed payload isolation'}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
