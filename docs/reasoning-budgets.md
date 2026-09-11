# 思考强度与动态输出预算

两个 mode 共用模型浮窗中的“思考强度”。可选择跟随策略，或为每个模型保存独立强度；模型之间切换会恢复各自选择。设置从下一条新消息生效，超时请求的幂等重试继续使用提交时配置。显式强度不被自动升级覆盖，也不会触发阶段推进。切回“跟随模型策略”会删除此模型的手动覆盖。

## 支持的选项

| 模型 | 可选强度 |
| --- | --- |
| GPT 5.4 / mini / nano、GPT 5.5 | none、low、medium、high、xhigh |
| GPT 5.6 Sol / Terra / Luna | none、low、medium、high、xhigh、max |
| DeepSeek Flash（含快速/深度预设）、V4 Pro | none、low、high、max |

选项按官方文档核对：[GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)、[GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)、[GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)、[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)、[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。DeepSeek 的 none 关闭思考；其他选项发送对应 `reasoning_effort`。预设只提供默认强度，用户手动设置优先。

## 限额的含义

这是本工作流的**输出 token 预算（含思考）**，不是账户额度、输入上下文长度、人民币/美元费用或回复字数。界面显示单次模型调用上限、整轮累计上限和调用次数上限。

模型基线为：nano/Luna 4000，mini/Terra 5000，其他 OpenAI 8000，DeepSeek Flash 8192，V4 Pro 12000。实际基线取模型基线与既有阶段 token 设置的较大值；DeepSeek 还考虑 `DEEPSEEK_MAX_OUTPUT_TOKENS`。none/low/medium/high/xhigh/max 分别乘以 1/1.5/2/4/6/8，再按服务端与模型上限截断。整轮上限为单次上限的 6 倍，并受服务端整轮硬上限约束。自定义、未列入内置目录的模型使用保守的单次 16000 上限；管理员应先验证其 API 能力。

默认配置示例：

| 选择 | 单次输出上限 | 整轮输出上限 |
| --- | ---: | ---: |
| GPT 5.4-mini · low | 7500 | 45000 |
| GPT 5.4-mini · high | 20000 | 120000 |
| GPT 5.5 · high | 32000 | 192000 |
| GPT 5.6 Sol · max | 64000 | 256000 |

正式轮次的意图解析、回复、跨阶段调用、修复和有限升级共享同一个预算。整轮开始时冻结累计上限；中途跨阶段或升级不会重置它。每次调用先预留可用额度，有真实 usage 时按真实输出扣除；usage 缺失或网络结果不明时保守保留预留额度。评测记录分别保存实际 usage 与预算扣除，不将估算值冒充实测消耗。

显式启用自动升级、且未固定模型或强度时，服务器可能根据近期验证失败选择更强模型；界面注明基础预算，实际路由及预算以评测记录为准。手动选择模型或此模型的强度后不适用这项自动升级。

达到输出或调用次数上限后返回 `409 model_budget_exceeded`，标明 `retryable: false`，不进入模型修复循环。前端移除原请求的快捷重试，保留输入供用户调整设置后发送新一轮。语言翻译与后台反馈分析为独立请求，不计入此对话轮次的预算；其成本依然存在。

## 后端环境变量与部署

**没有新增必填变量。** 默认值已写入代码；若希望明确管理上限，在后端部署平台或 `.env.production` 添加：

```dotenv
ECE329_MAX_OUTPUT_TOKENS_PER_CALL=64000
ECE329_MAX_OUTPUT_TOKENS_PER_TURN=256000
ECE329_MAX_MODEL_CALLS_PER_TURN=12
```

这些变量是硬上限，页面不能提高它们。可降低用于控制成本；上限过小可能导致结构化输出被截断。原有 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、模型白名单及路由配置继续有效。`OPENAI_REASONING_EFFORT` 仅提供后端默认强度，用户选择保存在会话配置中，不需要每次改环境变量。`max` 不能作为不支持它的模型的默认值。

较高思考强度可能增加耗时，供应商请求超时仍由 `OPENAI_TIMEOUT_SECONDS` / `DEEPSEEK_TIMEOUT_SECONDS` 控制；如调整超时，还需协调反向代理及前端 `REQUEST_TIMEOUT_MS`，不要把输出预算理解为时间保证。

同步部署后端 Python 与前端 `docs/`，重启后端，刷新页面。只修改 `.env.example` 不会修改线上配置。

## 接口与验证

`GET /v1/config` 和 `/v1/models` 的路由信息新增 `execution.models`，返回各模型允许的强度及计算后的预算。客户端只负责展示，不提交任意 token 上限。

模型配置新增 `reasoning_overrides`，例如：

```json
{"strategy":"custom","model_override":"gpt-5.6-sol","reasoning_overrides":{"gpt-5.6-sol":"max","deepseek-flash":"low"}}
```

该字段走原有创建、消息、所有者鉴权的配置 PATCH、版本冲突与幂等线路。未携带该字段的旧会话默认使用空映射。

`tests/test_reasoning_budgets.py` 覆盖全部内置模型与强度的真实适配器参数、双模式、缓存重放、无效组合、累计上限、缺失 usage 和停止调用。`tools/verify_reasoning_browser.cjs` 验证浏览器选项、即时预算、持久化、遥测及中英文窄屏排版。自动化使用模型替身，不消耗真实 API 额度，也不代替实际账户权限与生成质量测试。
