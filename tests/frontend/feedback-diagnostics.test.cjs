"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.textContent='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(k,v){this[k]=v;}
  addEventListener(k,v){this.listeners[k]=v;}
  fire(k){this.listeners[k]?.({preventDefault(){}});}
  get text(){return this.textContent+this.children.map(n=>n.text).join(' ');}
  find(tag){return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.find(tag)]);}
}
function harness(row){
  const host=new Element('div'),listeners={};
  const context={document:{createElement:tag=>new Element(tag),querySelectorAll:()=>[host]},
    window:{ECE329I18n:{language:'zh'},addEventListener:(k,v)=>listeners[k]=v}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../docs/assets/feedback-diagnostics.js'),'utf8'),context);
  context.window.ECE329Diagnostics.mount(host,row);
  return {host,en(){context.window.ECE329I18n.language='en';listeners['ece329:language-changed']();}};
}
const failure=(reason,more={})=>({attempt:4,provider:'openai',model:'gpt-5.4-mini',phase:'validate_draft',status:'failed',diagnostic:{version:1,source:'local_validation',code:'schema_validation_failed',reason,...more}});
test('precise validation message is bilingual; debug details stay collapsed until opened',()=>{
  const row=failure('max_length',{field_path:'candidate.trigger',limit:140,actual_length:168}),h=harness(row);
  assert.match(h.host.text,/第 4 次.*OpenAI.*校验草案失败/);
  assert.match(h.host.text,/触发条件：字段超过长度限制 \(168 \/ 140 字符\)/);
  assert.match(h.host.text,/检查建议/);
  assert.equal(h.host.find('details')[0].open,false);
  h.host.find('button')[0].fire('click');h.en();
  assert.equal(h.host.find('details')[0].open,true);
  assert.match(h.host.text,/Trigger: Field exceeds the length limit \(168 \/ 140 characters\)/);
  assert.match(h.host.text,/Check:.*shorten/);
  assert.deepEqual(JSON.parse(h.host.find('pre')[0].textContent),row);
});
test('input limits, output truncation and rate limits give distinct checks',()=>{
  const input=harness(failure('input_limit')),output=harness(failure('output_limit')),rate=harness(failure('rate_limited'));
  input.en();output.en();rate.en();
  assert.match(input.host.text,/Reduce input or use a larger-context model/);
  assert.match(output.host.text,/ECE329_FEEDBACK_MAX_OUTPUT_TOKENS/);
  assert.match(rate.host.text,/Check request rate and concurrency/);
  assert.doesNotMatch(rate.host.text,/explicitly reported insufficient quota/);
});
test('legacy failures have no invented explanation; completed attempts have a clear phase',()=>{
  const old=harness({attempt:1,status:'failed',code:'model_output_invalid'});
  assert.match(old.host.text,/历史记录未保存具体失败原因/);old.en();
  assert.match(old.host.text,/historical record did not store a specific failure cause/);
  const success=harness({attempt:2,status:'completed'});
  assert.match(success.host.children[0].text,/提炼完成/);
  assert.doesNotMatch(success.host.children[0].text,/环节未记录/);
});
for(const result of ['candidate','duplicate','insufficient_evidence','check_not_passed'])test(`analysis outcome ${result} is not a system failure`,()=>{
  const h=harness({attempt:1,phase:'completed',status:'completed',diagnostic:{version:1,source:'analysis_result',code:result}});
  assert.doesNotMatch(h.host.children.slice(0,2).map(n=>n.text).join(' '),/失败|原因未明确/);
  assert.match(h.host.children[0].text,/提炼完成/);
});
test('backend exceptions remain backend errors and untrusted strings remain text',()=>{
  const row=failure('unexpected_exception');row.model='<img onerror=attack()>';row.diagnostic.source='backend';
  const h=harness(row);h.en();assert.match(h.host.text,/Backend processing exception/);
  assert.equal(h.host.find('img').length,0);assert.match(h.host.children[0].text,/<img onerror=attack\(\)>/);
});
