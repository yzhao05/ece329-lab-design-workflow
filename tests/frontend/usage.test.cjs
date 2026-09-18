"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
test('usage formatting distinguishes unknown usage, partial cost and bilingual active durations',()=>{
  const handlers={}, nodes=[];
  const context={window:{ECE329I18n:{language:'zh'},addEventListener:(key,fn)=>handlers[key]=fn},
    document:{querySelectorAll:selector=>nodes.filter(node=>node.dataset[{'[data-duration]':'duration','[data-usage]':'usage','[data-usage-identity]':'usageIdentity','[data-usage-stop]':'usageStop'}[selector]])}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../docs/assets/usage-ui.js'),'utf8'),context);
  const ui=context.window.ECE329Usage;
  const make=()=>{const el={dataset:{},setAttribute(){}};nodes.push(el);return el;};
  const total=make(),reply=make(),stats=make();
  const identity=make(),stop=make();
  ui.identity(identity,{agent:'experience_checker',provider:'deepseek',model_id:'deepseek-v4-pro',reported_models:['deepseek-v4-pro']});
  ui.stopState(stop,{current_state:{stage:'CONCEPTUAL_PROCEDURE',status:'active'},usage:{last_dialogue:{handled_stage:'VARIABLES_AND_CONDITIONS',status:'completed'}}});
  assert.match(identity.textContent,/经验检查 Agent.*deepseek/);assert.match(stop.textContent,/当前停留阶段：实验流程/);
  ui.timing(total,7200000,true);ui.timing(reply,null);
  ui.summary(stats,{run_count:1,call_count:2,input_tokens:null,known_input_tokens:100,output_tokens:5,estimated_cost_usd:null,known_cost_usd:0.001,active_ms:1999});
  assert.match(total.textContent,/2小时/);assert.match(reply.textContent,/未记录/);
  assert.match(stats.textContent,/不完整，已知 100/);assert.match(stats.textContent,/0.00100000/);
  context.window.ECE329I18n.language='en';handlers['ece329:language-changed']();
  assert.match(total.textContent,/Active time 2h/);assert.match(reply.textContent,/Not recorded/);
  assert.match(stats.textContent,/Incomplete; known 100/);assert.doesNotMatch(stats.textContent,/[\u3400-\u9fff]/);
  assert.match(identity.textContent,/Experience checker/);assert.match(stop.textContent,/Current stage: Procedure/);
});
