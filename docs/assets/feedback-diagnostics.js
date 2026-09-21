"use strict";
// Fixed local labels only. Diagnostics never use the display-translation API.
(() => {
  const views=new WeakMap(),t=(zh,en)=>window.ECE329I18n?.language==='en'?en:zh;
  const node=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const phases={prepare:['准备分析','Prepare analysis'],request_draft:['请求草案','Request draft'],parse_draft:['解析草案','Parse draft'],
    validate_draft:['校验草案','Validate draft'],validate_evidence:['校验证据引用','Validate evidence references'],
    request_check:['请求检查','Request check'],parse_check:['解析检查结果','Parse check result'],validate_check:['校验检查结果','Validate check result'],
    evaluate_check:['评估检查结果','Evaluate check result'],persist_result:['保存结果','Save result'],completed:['提炼完成','Extraction completed'],
    draft:['提炼环节','Drafting'],check:['检查环节','Checking'],analysis:['分析环节','Analysis']};
  const reasons={
    max_length:['字段超过长度限制','Field exceeds the length limit','检查所示字段，缩短内容至上限；修改提炼提示约束。','Check the indicated field and shorten it to the limit; review extraction prompt constraints.'],
    min_length:['字段内容为空或过短','Field is empty or too short','检查所示字段是否返回有效内容及对应 JSON Schema。','Check the field content and its JSON Schema.'],
    required:['缺少必填字段','Required field is missing','检查所示字段、模型结构化输出及后端 Schema 是否一致。','Check the field and ensure model output matches the backend schema.'],
    type:['字段类型不符','Wrong field type','检查字段应为文本、列表或布尔值，核对模型输出 Schema。','Check the expected text, array or boolean type and output schema.'],
    enum:['字段值不在允许范围','Value is outside the allowed set','检查模式、阶段、类别等枚举值。','Check allowed mode, stage and category values.'],
    max_items:['列表条目超过上限','Too many list entries','减少列表条目，核对对应字段的数量限制。','Reduce list entries and check the field limit.'],
    min_items:['列表条目不足','Too few list entries','检查必填列表是否为空。','Check whether required lists are empty.'],
    additional_properties:['返回了额外字段','Unexpected extra fields','检查模型输出和后端 Schema，移除不允许的字段。','Check the output schema and remove unsupported fields.'],
    timeout:['模型请求超时','Model request timed out','检查服务商连接及 OPENAI_TIMEOUT_SECONDS / DEEPSEEK_TIMEOUT_SECONDS；按需调整超时或减少输入。','Check provider connectivity and OPENAI_TIMEOUT_SECONDS / DEEPSEEK_TIMEOUT_SECONDS; adjust timeouts or reduce input.'],
    connection:['无法连接模型服务','Cannot connect to the model service','检查网络、服务商地址及服务状态；可手动尝试另一服务商。','Check networking, provider URL and service status; optionally try another provider.'],
    rate_limited:['请求受到限流','Request was rate limited','检查请求频率与并发量，稍后手动重试；限流不等于余额不足。','Check request rate and concurrency, then retry later; rate limiting does not establish insufficient balance.'],
    quota_exhausted:['服务商明确返回额度不足','Provider explicitly reported insufficient quota','检查对应服务商账户余额、项目额度和计费设置。','Check the provider account balance, project quota and billing settings.'],
    input_limit:['输入超过模型上下文限制','Input exceeds the model context limit','减少输入或使用支持更大上下文的模型；若服务支持可配置输入上限，检查该设置。提高输出上限不能解决输入超限。','Reduce input or use a larger-context model; check configurable input limits if supported. Increasing output limits does not fix excessive input.'],
    output_limit:['输出达到上限，被截断','Output was truncated at its limit','检查当前环节的 ECE329_FEEDBACK_MAX_OUTPUT_TOKENS 或 ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS；适当提高上限、减少输出要求或降低思考强度。','Check ECE329_FEEDBACK_MAX_OUTPUT_TOKENS or ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS for this phase; increase the cap, request less output or lower reasoning effort.'],
    empty_output:['模型未返回有效正文','Model returned no usable body','检查返回状态、思考强度及输出额度；不能仅凭空正文认定额度不足。','Check response status, reasoning effort and output budget; an empty body alone does not establish quota exhaustion.'],
    invalid_json:['模型正文不是有效 JSON','Model body is not valid JSON','检查模型的 JSON 输出支持和结构化输出约束。','Check model JSON support and structured-output constraints.'],
    invalid_response_json:['API 响应不是有效 JSON','API response is not valid JSON','检查 API 地址、网关响应及服务商状态。','Check the API URL, gateway response and provider status.'],
    invalid_response_shape:['API 响应结构不符','Unexpected API response structure','检查服务商适配器与 API 响应格式。','Check the provider adapter and API response format.'],
    evidence_reference:['引用了不存在的证据','Referenced evidence does not exist','核对诊断中的证据引用与本次反馈保存的 event_chain / reported_turn。','Compare evidence references with this feedback record’s event_chain / reported_turn.'],
    incomplete_response:['模型响应未完成','Model response did not complete','检查响应状态、结束原因及服务商日志。','Check response status, finish reason and provider logs.'],
    refusal:['模型拒绝输出','Model declined to respond','核对反馈内容与模型使用限制，不反复自动重试。','Review feedback content and model restrictions; do not retry automatically in a loop.'],
    authentication:['API 鉴权失败','API authentication failed','检查对应服务商 API Key 和 API 地址。','Check the provider API key and API URL.'],
    permission:['API 权限被拒绝','API permission denied','检查模型访问权限和项目权限。','Check model and project access permissions.'],
    payment_required:['API 返回支付要求','API returned payment required','检查服务商账单；具体余额原因以服务商返回为准。','Check provider billing; do not infer a specific balance issue without provider evidence.'],
    provider_unavailable:['模型服务端错误','Provider server error','检查服务商状态，稍后重试或切换服务商。','Check provider status; retry later or switch providers.'],
    configuration:['模型配置不可用','Model configuration unavailable','检查 API Key、ECE329_FEEDBACK_MODEL 和 ECE329_ALLOWED_MODELS。','Check API keys, ECE329_FEEDBACK_MODEL and ECE329_ALLOWED_MODELS.'],
    unexpected_exception:['后端处理异常','Backend processing exception','使用反馈 ID 核对后端日志和发布版本，检查代码处理逻辑。','Use the feedback ID to inspect backend logs, release version and processing code.'],
    unknown:['原因未明确','Cause not determined','查看调试详情及关联日志；不要据此推断余额或模型问题。','Inspect debug details and correlated logs; do not infer a billing or model cause.'],
  };
  const results={candidate:['已生成候选经验','Candidate experience created'],duplicate:['同类经验已存在','Equivalent experience already exists'],
    check_not_passed:['检查未通过，未启用经验','Check not passed; experience not enabled'],insufficient_evidence:['未提炼可用经验／证据不足','No usable experience / insufficient evidence']};
  const sources={local_validation:['本地校验器','Local validator'],local_parser:['本地响应解析','Local response parser'],provider_http:['服务商 HTTP 响应','Provider HTTP response'],transport:['网络传输层','Network transport'],configuration:['配置检查','Configuration check'],backend:['后端程序','Backend'],analysis_result:['分析结果','Analysis outcome']};
  function render(host) {
    const state=views.get(host),row=state.row,d=row.diagnostic,legacy=!d||d.version!==1;
    const phase=row.status==='completed'&&row.phase!=='persist_result'?'completed':row.phase;
    const phaseText=phases[phase]?t(...phases[phase]):t('分析环节未记录','Analysis phase not recorded');
    const provider={openai:'OpenAI',deepseek:'DeepSeek'}[row.provider]||row.provider||t('未记录','Not recorded');
    host.replaceChildren();
    host.append(node('p',`${t('第 ','Attempt ')}${row.attempt??t('未记录','Not recorded')}${t(' 次','')} · ${provider} / ${row.model??t('未记录','Not recorded')} · ${phaseText}${row.status==='failed'?t('失败',' failed'):''}`));
    if(legacy) {
      const legacyCodes={model_connection_error:['模型连接失败','Model connection failed'],model_output_invalid:['模型输出不符合要求','Invalid model output'],model_timeout:['模型请求超时','Model request timed out'],model_rate_limited:['模型请求受限','Model rate limit reached'],internal_error:['后端处理异常','Backend processing exception']};
      if(legacyCodes[row.code])host.append(node('p',t(...legacyCodes[row.code])));
      host.append(node('p',row.status==='failed'?t('历史记录未保存具体失败原因。','This historical record did not store a specific failure cause.'):t('历史记录未保存详细诊断。','This historical record did not store detailed diagnostics.')));
      if(row.status==='failed')host.append(node('p',t('检查建议：核对原始 JSON 和后端日志；如需新的诊断，可在剩余次数内手动重试。','Check: inspect raw JSON and backend logs; manually retry within the remaining budget for new diagnostics.')));
    }else if(d.source==='analysis_result')host.append(node('p',results[d.code]?t(...results[d.code]):t('分析已完成','Analysis completed')));
    else if(row.phase==='persist_result')host.append(node('p',t('结果保存尚未确认；请核对后端日志。','Result persistence is not confirmed; check backend logs.')));
    else {
      const words=reasons[d.reason]||reasons.unknown;
      const length=Number.isFinite(d.actual_length)&&Number.isFinite(d.limit)?` (${d.actual_length} / ${d.limit}${['max_length','min_length'].includes(d.reason)?t(' 字符',' characters'):''})`:'';
      const fields={'candidate.trigger':['触发条件','Trigger'],'candidate.summary':['经验摘要','Summary'],'candidate.recommendation':['处理建议','Recommendation'],'candidate.verification':['验证方法','Verification']};
      host.append(node('p',(fields[d.field_path]?t(...fields[d.field_path])+t('：',': '):'')+t(words[0],words[1])+length));
      let advice=t(words[2],words[3]);
      if(d.reason==='output_limit' && ['check','request_check','parse_check','validate_check'].includes(row.phase)) {
        advice=t('检查 ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS（建议 8192）及 ECE329_FEEDBACK_CHECK_REASONING_EFFORT（建议 none）；更新配置并重启后端后手动重试。检查环节只需简短 JSON 结果，提高草案上限不会改变检查上限。',
          'Check ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS (suggested: 8192) and ECE329_FEEDBACK_CHECK_REASONING_EFFORT (suggested: none). Restart the backend after updating settings, then retry manually. The checker only needs concise JSON; changing the draft cap does not change the check cap.');
      }
      host.append(node('p',t('检查建议：','Check: ')+advice));
    }
    if(row.input_evidence_truncated)host.append(node('p',t('输入证据摘录已截断：核对原始记录和后端摘录上限，补充较短的关键上下文。此标记不等于模型上下文超限。','Input evidence excerpts were truncated: check original records and backend excerpt limits, and supply shorter key context. This does not establish a model context-limit error.')));
    const details=node('details');details.open=state.fold.open;const summary=node('summary'),button=node('button');button.type='button';button.className='ghost-button';
    const sync=()=>{state.fold.open=details.open;button.textContent=details.open?t('收起','Collapse'):t('展开','Expand');button.setAttribute('aria-expanded',String(details.open));};
    button.addEventListener('click',event=>{event.preventDefault();details.open=!details.open;sync();});details.addEventListener('toggle',sync);sync();
    summary.append(node('span',t('调试详情','Debug details')),button);details.append(summary);
    const values={...row,diagnostic:legacy?null:d};
    details.append(node('p',t('判断来源：','Diagnostic source: ')+(sources[d?.source]?t(...sources[d.source]):t('未记录','Not recorded'))));
    details.append(node('pre',JSON.stringify(values,null,2)));host.append(details);
  }
  window.ECE329Diagnostics={mount(host,row,fold={open:false}){host.className='feedback-attempt';host.setAttribute('data-i18n-ignore','');views.set(host,{row,fold});render(host);}};
  window.addEventListener('ece329:language-changed',()=>{for(const host of document.querySelectorAll('.feedback-attempt'))if(views.has(host))render(host);});
})();
