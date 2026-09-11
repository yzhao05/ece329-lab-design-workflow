"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto").webcrypto;
class Element {
  constructor() { this.value = ""; this.disabled = false; this.children = []; this.handlers = {}; this.dataset = {}; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  querySelector() { return new Element(); }
}
function harness() {
  const elements = new Map();
  const storage = new Map();
  const adapter = { getItem: key => storage.get(key) || null, setItem: (key, val) => storage.set(key, val), removeItem: key => storage.delete(key) };
  const context = { window: { ECE329_CONFIG: { API_BASE_URL: "https://api.test" }, addEventListener() {} },
    document: { querySelector: selector => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); },
      getElementById: id => { const key = '#' + id; if (!elements.has(key)) elements.set(key, new Element()); return elements.get(key); }, createElement: () => new Element() },
    localStorage: adapter, sessionStorage: adapter, crypto, console, setTimeout, clearTimeout, structuredClone };
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "../../docs/assets/app.js"), "utf8").replace("void initializePage();", "");
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  run(`modelCatalog = {enabled:true, default_model:'gpt-5.4-mini', models:[
    {id:'gpt-5.4-mini',label:'GPT 5.4-mini（recommend）'}, {id:'gpt-5.4',label:'GPT 5.4'}, {id:'gpt-5.4-nano',label:'GPT 5.4-nano'},
    {id:'gpt-5.5',label:'GPT 5.5'}, {id:'gpt-5.6-sol',label:'GPT 5.6 Sol'},
    {id:'gpt-5.6-terra',label:'GPT 5.6 Terra'}, {id:'gpt-5.6-luna',label:'GPT 5.6 Luna'}]};`);
  return { context, run, elements, storage };
}

test("catalogue populates exact recommended label and selection affects turn body", () => {
  const h = harness();
  h.run("state.selectedModel = 'gpt-5.4-mini'; renderModelSelection();");
  const select = h.elements.get("#modelSelect");
  assert.equal(select.children[0].textContent, "GPT 5.4-mini（recommend）");
  assert.equal(select.children.length, 7);
  for (const model of ['gpt-5.4', 'gpt-5.5', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna']) {
    select.value = model;
    select.handlers.change();
    assert.equal(h.run("buildTurnRequest('test').model"), model);
    assert.equal(JSON.parse([...h.storage.values()][0]).selectedModel, model);
  }
});

test("timeout replay freezes original model even after a new selection", () => {
  const h = harness();
  h.run("state.selectedModel='gpt-5.4'; state.pendingRequest={turnId:'retry-0001',model:'gpt-5.4-mini'};");
  assert.equal(h.run("buildTurnRequest('same').model"), "gpt-5.4-mini");
  assert.equal(h.run("buildTurnRequest('same').turn_id"), "retry-0001");
  h.run("renderModelSelection()");
  assert.match(h.elements.get("#modelHelp").textContent, /快捷重试仍使用原模型/);
  h.run("state.pendingRequest={turnId:'legacy-0001'}");
  assert.equal(h.run("Object.hasOwn(buildTurnRequest('legacy'), 'model')"), false);
});

test("create request includes the submitted model", async () => {
  const h = harness();
  const bodies = [];
  h.context.recordBody = body => bodies.push(JSON.parse(body));
  h.run(`state.selectedModel='gpt-5.4-nano'; state.pendingRequest={turnId:'create-0001',model:'gpt-5.4-nano'};
    apiRequest=async (url, options)=>{recordBody(options.body); return {design_id:'new'};};`);
  await h.run("createApiDesign('idea')");
  assert.deepEqual(bodies, [{ idea: "idea", model: "gpt-5.4-nano" }]);
});

test("successful original-model replay does not erase the user's next model choice", () => {
  const h = harness();
  h.run("state.selectedModel='gpt-5.4'; state.pendingRequest={turnId:'retry-0001',model:'gpt-5.4-mini'}; applyModelReply({selected_model:'gpt-5.4-mini'});");
  assert.equal(h.run("state.selectedModel"), "gpt-5.4");
  h.run("state.pendingRequest=null;");
  assert.equal(h.run("buildTurnRequest('new message').model"), "gpt-5.4");
});

test("busy state and server-disabled catalogue disable selection; removed model stays explicit", () => {
  const h = harness();
  h.run("state.selectedModel='removed-model'; renderModelSelection();");
  assert.match(h.elements.get("#modelSelect").children.at(-1).textContent, /已停用/);
  assert.equal(h.elements.get("#modelSelect").disabled, false);
  h.run("setBusy(true)");
  assert.equal(h.elements.get("#modelSelect").disabled, true);
  h.run("setBusy(false); modelCatalog={enabled:false,models:[],default_model:null}; renderModelSelection();");
  assert.equal(h.elements.get("#modelSelect").disabled, true);
  assert.equal(h.run("modelForRequest()"), null);
});

test("late catalogue response after reset cannot overwrite a different design choice", async () => {
  const h = harness();
  let finish;
  h.context.waitForCatalogue = () => new Promise(resolve => { finish = resolve; });
  h.run("apiRequest=waitForCatalogue; state.selectedModel='gpt-5.4';");
  const loading = h.run("loadModelCatalog()");
  h.run("designGeneration++; state.selectedModel='gpt-5.4-nano';");
  finish({ enabled: true, models: [{ id: "gpt-5.4-mini", label: "mini" }], default_model: "gpt-5.4-mini" });
  await loading;
  assert.equal(h.run("state.selectedModel"), "gpt-5.4-nano");
});

function enableRouting(h) {
  h.run(`routingCatalog={enabled:true,defaults:{strategy:'recommended',profile:'balanced',stage_overrides:{},model_override:null,experience_enabled:true},
    registry:{fast:{model:'gpt-5.4-mini',reasoning:'low'},balanced:{model:'gpt-5.4-mini',reasoning:'medium'},reasoning:{model:'gpt-5.6-sol',reasoning:'high'}}};
    state.modelConfig=structuredClone(routingCatalog.defaults); state.selectedModel='gpt-5.4-mini';`);
}

test('language is frozen for retries and updated for new messages', () => {
  const h=harness();
  h.context.window.ECE329I18n={language:'en'};
  assert.equal(h.run("buildTurnRequest('hello').language"),'en');
  h.run("state.pendingRequest={turnId:'language-retry',language:'en'};");
  h.context.window.ECE329I18n.language='zh';
  assert.equal(h.run("buildTurnRequest('hello').language"),'en');
  h.run('state.pendingRequest=null');
  assert.equal(h.run("buildTurnRequest('hello').language"),'zh');
});

test('display translation batches short texts and never submits a design turn', async () => {
  const h=harness();const calls=[];
  h.context.translateFixture=(url,options)=>{calls.push([url,JSON.parse(options.body)]);return {translations:JSON.parse(options.body).texts.map(t=>'English '+t)};};
  h.run('apiRequest=translateFixture');
  const result=await h.run("window.requestDisplayTranslation(['first','second','third','fourth'],'en')");
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'/v1/localization');
  assert.equal(result.translations.length,4);
});

for (const mode of ['EMVR_DIRECT', 'GUIDED_DESIGN']) {
  test(`${mode}: conflict refresh rebases frozen retry onto current server settings`, async () => {
    const h = harness(); enableRouting(h);
    h.run(`state.mode='${mode}'; state.designId='conflict-owner'; render=()=>{};
      state.pendingRequest={turnId:'conflict-retry',message:'already answered',model:null,
        modelConfig:structuredClone(state.modelConfig),needsRefresh:true};
      authorizedDesignApiRequest=async()=>({design_id:state.designId,interaction_state:state.mode,
        model_config:{...state.modelConfig,strategy:'custom',model_override:'gpt-5.5',experience_enabled:false},
        model_config_version:4,selected_model:'gpt-5.5'});`);
    assert.equal(await h.run('refreshConflictedRequest()'), true);
    assert.equal(h.run("buildTurnRequest('already answered').model"), 'gpt-5.5');
    assert.equal(h.run("buildTurnRequest('already answered').model_config.experience_enabled"), false);
    assert.equal(h.run('state.pendingRequest.needsRefresh'), false);
    assert.equal(h.run('state.modelConfigVersion'), 4);
  });
}

test('failed conflict refresh stays pending and never submits stale settings', async () => {
  const h = harness(); enableRouting(h);
  h.run(`state.designId='conflict-owner'; state.pendingRequest={modelConfig:structuredClone(state.modelConfig),needsRefresh:true};
    authorizedDesignApiRequest=async()=>{throw new Error('offline')}; showToast=()=>{};
    dom.chatForm.requestSubmit=()=>{throw new Error('must not submit')};`);
  await h.run('retryPendingRequest()');
  assert.equal(h.run('state.pendingRequest.needsRefresh'), true);
  assert.equal(h.run('dom.sendButton.disabled'), false);
  h.run("dom.chatInput.value='a new answer';");
  await h.run('handleSubmit({preventDefault(){}})');
  assert.equal(h.run('state.pendingRequest.needsRefresh'), true);
  assert.equal(h.run('dom.chatInput.value'), 'a new answer');
});

test('late conflict refresh cannot replace a different design retry', async () => {
  const h = harness(); enableRouting(h);
  let finish;
  h.context.waitForDesign = () => new Promise(resolve => { finish = resolve; });
  h.run("state.designId='first'; state.pendingRequest={needsRefresh:true}; authorizedDesignApiRequest=waitForDesign;");
  const result = h.run('refreshConflictedRequest()');
  h.run("designGeneration++; state.designId='second'; state.pendingRequest={turnId:'second-turn'};");
  finish({design_id:'first'});
  assert.equal(await result, false);
  assert.equal(h.run('state.pendingRequest.turnId'), 'second-turn');
});

test('DeepSeek presets expose their mode and freeze the selected preset in requests', () => {
  const h=harness(); enableRouting(h);
  h.run("modelCatalog.models.push({id:'deepseek-flash:fast',label:'DeepSeek Flash（快速预设）',provider:'deepseek',api_model:'deepseek-flash',preset_reasoning:'none'}); state.selectedModel='deepseek-flash:fast'; state.modelConfig.strategy='custom'; state.modelConfig.model_override=state.selectedModel; renderModelSelection();");
  assert.match(h.elements.get('#modelHelp').textContent, /关闭深度思考/);
  assert.equal(h.run("buildTurnRequest('hello').model"),'deepseek-flash:fast');
  assert.equal(h.run("buildTurnRequest('hello').model_config.model_override"),'deepseek-flash:fast');
});

test('reply updates server config version while preserving next-message preferences', () => {
  const h=harness(); enableRouting(h);
  h.run("state.pendingRequest={modelConfig:structuredClone(state.modelConfig)}; state.modelConfig.strategy='quality'; applyRoutingReply({model_config:{strategy:'recommended'},model_config_version:7});");
  assert.equal(h.run('state.modelConfig.strategy'),'quality');
  assert.equal(h.run('state.modelConfigVersion'),7);
});

test('failed catalogue refresh disables stale routing controls', async () => {
  const h=harness(); enableRouting(h);
  h.run("apiRequest=async()=>{throw new Error('offline')}");
  await h.run('loadModelCatalog()');
  assert.equal(h.run('routingCatalog'),null);
  assert.equal(h.run('modelConfigForRequest()'),null);
});

test('catalogue removes obsolete effort preferences without rewriting submitted retries', async () => {
  const h=harness(); enableRouting(h);
  h.run(`state.modelConfig.reasoning_overrides={'gpt-5.4-mini':'high','gpt-5.5':'max','removed-model':'low'};
    state.pendingRequest={turnId:'effort-retry-001',modelConfig:structuredClone(state.modelConfig)};
    apiRequest=async()=>({...modelCatalog,routing:{...routingCatalog,execution:{models:{
      'gpt-5.4-mini':{reasoning_efforts:['low','high']},'gpt-5.5':{reasoning_efforts:['low','high']}}}}});`);
  await h.run('loadModelCatalog()');
  assert.equal(h.run('JSON.stringify(state.modelConfig.reasoning_overrides)'),'{"gpt-5.4-mini":"high"}');
  assert.equal(h.run("buildTurnRequest('retry').model_config.reasoning_overrides['removed-model']"),'low');
});

test('automatic strategy transmits configuration without overriding its model; retry freezes it', () => {
  const h=harness(); enableRouting(h);
  assert.equal(h.run("buildTurnRequest('hello').model"),undefined);
  assert.equal(h.run("buildTurnRequest('hello').model_config.strategy"),'recommended');
  h.run("state.pendingRequest={turnId:'frozen-config-001',model:null,modelConfig:structuredClone(state.modelConfig)}; state.modelConfig.strategy='quality';");
  assert.equal(h.run("buildTurnRequest('hello').model_config.strategy"),'recommended');
  h.run("applyRoutingReply({model_config:{strategy:'recommended'},model_config_version:1})");
  assert.equal(h.run('state.modelConfig.strategy'),'quality');
});

test('strategy controls expose 13 stages and persist owner config without a design turn', async () => {
  const h=harness(); enableRouting(h);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../docs/assets/model-strategy.js'),'utf8'),h.context);
  const select=h.elements.get('#modelStrategy'); select.value='custom'; select.handlers.change();
  assert.equal(h.elements.get('#stageRoutingRows').children.length,13);
  assert.equal(h.elements.get('#stageRoutingDetails').hidden,false);
  const stageSelect=h.elements.get('#stageRoutingRows').children[4].children[0];
  stageSelect.value='reasoning'; stageSelect.handlers.change();
  assert.equal(h.run("state.modelConfig.stage_overrides.THEORETICAL_FRAMEWORK"),'reasoning');
  h.run("state.designId='config-owner'; state.sessionKind='api';");
  const calls=[]; h.context.capture=(url,options)=>{calls.push([url,options]);return {version:1};};
  h.run('authorizedDesignApiRequest=capture');
  await h.elements.get('#saveModelStrategy').handlers.click();
  assert.equal(calls[0][0],'/v1/designs/config-owner/model-config');
  assert.equal(calls[0][1].method,'PATCH');
  assert.equal(h.elements.get('#modelStrategy').disabled,false);
  assert.equal(h.run('state.modelConfigVersion'),1);
});
