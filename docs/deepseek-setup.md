# DeepSeek 接入与后端配置

两个 mode 共用现有模型选择、阶段路由、反馈经验和评测链路。当前加入两个官方模型、四个界面选项：

| 界面名称 | 本项目选择 ID | 实际 DeepSeek API 模型 | 行为 |
| --- | --- | --- | --- |
| DeepSeek Flash（recommend） | `deepseek-flash` | `deepseek-flash` | 推理强度跟随阶段能力配置 |
| DeepSeek Flash（快速预设） | `deepseek-flash:fast` | `deepseek-flash` | 关闭 thinking，适合简单补答与低延迟对照 |
| DeepSeek Flash（深度思考预设） | `deepseek-flash:reasoning` | `deepseek-flash` | 固定 high，供复杂问题对照 |
| DeepSeek V4 Pro | `deepseek-v4-pro` | `deepseek-v4-pro` | 保留 Pro 模型作为另一种选择，强度跟随能力配置 |

`recommend` 是本项目推荐标记，不是效果评测结论。带冒号的 ID 是本项目预设，发送到官方 API 前转换为真实模型名，不冒充独立模型。模型选择依据[官方当前调用说明](https://api-docs.deepseek.com/)：旧 `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` 已映射至 Flash，不作为另外两个模型重复展示；旧 `deepseek-chat/deepseek-reasoner` 也不加入新白名单。实际权限和未来可用性仍以部署账号及官方公告为准。

## 已有 OpenAI 后端，增加 DeepSeek

在实际 `.env.production` 或服务环境变量中设置，保留现有 OpenAI 密钥：

```dotenv
ECE329_GENERATOR=auto
OPENAI_MODEL=gpt-5.4-mini
DEEPSEEK_API_KEY=替换为你的DeepSeek密钥
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT_SECONDS=90
DEEPSEEK_MAX_OUTPUT_TOKENS=8192
ECE329_ALLOWED_MODELS=gpt-5.4-mini,gpt-5.4,gpt-5.4-nano,gpt-5.5,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,deepseek-flash,deepseek-flash:fast,deepseek-flash:reasoning,deepseek-v4-pro
```

`auto` 在有 OpenAI 密钥时保留 OpenAI 默认模型；只有 DeepSeek 密钥时使用 `DEEPSEEK_MODEL`。同时有两套密钥时可在对话中切换，后台按选择的模型使用对应密钥。显式 `ECE329_GENERATOR=openai` 也支持附加 DeepSeek 选项，但仍要求 OpenAI 密钥。

只增加密钥而保留旧的七模型白名单，DeepSeek 不会显示；应同步扩展白名单，或删除该变量以使用按已配置密钥生成的默认列表。没有对应密钥的供应商选项会被过滤，不向前端发送无凭据的选项。默认模型必须在过滤后的白名单内。

## 只用 DeepSeek

```dotenv
ECE329_GENERATOR=deepseek
DEEPSEEK_API_KEY=替换为你的DeepSeek密钥
DEEPSEEK_MODEL=deepseek-flash
ECE329_ALLOWED_MODELS=deepseek-flash,deepseek-flash:fast,deepseek-flash:reasoning,deepseek-v4-pro
```

其余 DeepSeek 参数可省略，使用上面的默认值。显式 deepseek 模式缺少密钥时会报告配置错误，不静默改成本地规则模式。`ECE329_GENERATOR=rule` 仍可主动禁用所有在线模型。

## 环境变量含义

| 变量 | 默认值 / 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 必需，独立的 DeepSeek 密钥，仅存在后端 |
| `DEEPSEEK_MODEL` | `deepseek-flash`；DeepSeek 为默认供应商时使用，也决定后台反馈提炼的基础模型 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com`；允许 HTTPS 根地址或 `/v1`，不填 `/chat/completions` 后缀 |
| `DEEPSEEK_TIMEOUT_SECONDS` | `90`；单次 DeepSeek HTTP 请求超时 |
| `DEEPSEEK_MAX_OUTPUT_TOKENS` | `8192`；包含思考与输出 token，作为现有各阶段 token 预算的下限，应用上限 384000 |
| `ECE329_ALLOWED_MODELS` | 两供应商共用的管理员白名单，包含本项目预设 ID；最多 12 项 |

现有 `OPENAI_REASONING_EFFORT` 仍是基础生成器的通用默认强度，阶段路由可覆盖它；DeepSeek 将 medium/xhigh 映射为 high，快速预设固定 none、深度预设固定 high，依据[官方 thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)。`OPENAI_*MAX_OUTPUT_TOKENS` 等既有阶段预算仍保留，DeepSeek 使用两者较大值，避免思考消耗完小预算导致 JSON 被截断。后台反馈提炼跟随后端基础模型，不跟随某位学生临时选择的模型。

若已经配置 `ECE329_MODEL_REGISTRY`，其中模型也须在当前白名单内。希望三个能力均使用 DeepSeek，可设置：

```dotenv
ECE329_MODEL_REGISTRY={"fast":{"model":"deepseek-flash:fast","reasoning":"none"},"balanced":{"model":"deepseek-flash","reasoning":"low"},"reasoning":{"model":"deepseek-v4-pro","reasoning":"high"}}
```

没有覆盖注册表时，基础供应商为 DeepSeek 的 reasoning 能力优先使用允许的 V4 Pro；基础供应商为 OpenAI 时保留原策略。手动固定具体模型或预设优先，自动升级规则不越过用户的明确选择。

## 协议、校验与上下文

DeepSeek 接入使用官方 Chat Completions 接口，将内部 Responses 请求转换成 `messages`、`thinking`、`response_format=json_object`。选择这一共同协议覆盖 Flash 和 Pro；[DeepSeek 的 Responses 兼容说明](https://api-docs.deepseek.com/guides/responses_api/)也明确指出远端会话 ID 不受支持。

DeepSeek 的 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)不等于远端严格 Schema 保证。适配器将原 JSON Schema 加入提示，在生成器解析入口用 `jsonschema` 做完整校验，再执行原课程与阶段约束。缺字段、类型错误、空回答、截断输出都进入原有有限修复机制；不会新增无界自动重试。思考原文不放进用户回答、经验或评测记录。

DeepSeek 每次携带现有本地历史，禁止传入远端 response ID。模型切换保留本地设计和历史，清除旧供应商响应链。评测保留选中预设 ID，同时单独记录实际模型 ID、供应商、映射后的推理强度与 token usage；Schema 校验失败也保留服务商已返回的用量。

## 更新与检查

这次增加了 `jsonschema` 运行依赖。代码更新后，在仓库根目录 CMD 使用后端自己的 Python 环境执行：

```bat
python -m pip install -e ".[production]"
```

Docker 部署需要重新构建镜像。随后重启后端并发布前端更新；模型列表可点击“刷新列表”。环境文件必须由现有启动方式加载，单纯修改 `.env.example` 不会改变运行服务。密钥不填入网页、`docs/assets/config.js` 或 Git。

验证 `GET /v1/models` 返回 DeepSeek 选项，再分别选快速预设、深度预设和 Pro 发送消息。使用基础 Chat API 需要足够余额；真实账号调用还应核对返回错误与是否发生本地回退。涉及长时间思考时，可按需要调整前端 `REQUEST_TIMEOUT_MS` 与服务器请求超时，网络重试保持原请求编号和模型配置。

自动回归覆盖两种 mode、四个选项、密钥/URL 隔离、独立部署、历史恢复、严格 Schema、有限输出修复、用量、反馈提炼。浏览器脚本用真实适配器与受控 HTTP 替身验证完整前后端链路，不消耗真实 API 额度，也不证明账号权限或实际教学效果。
