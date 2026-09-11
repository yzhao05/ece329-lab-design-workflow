# 对话中的模型选择

两个 mode 都通过输入框右下角的当前模型名称展开绿色设置浮窗，包含策略、模型和思考强度。新设计默认 Recommended，根据当前阶段选择能力；Custom 可逐阶段覆盖，也可固定具体模型。每个模型可以保存独立的思考强度，切换时动态显示单次与整轮输出限额。选择或保存策略本身不会发送消息或推进阶段。预算算法与后端变量见 [思考强度与动态预算](reasoning-budgets.md)，完整路由配置见 [模型路由与经验层](routing-experience-implementation.md)。模型列表包含：

除下列 OpenAI 模型外，配置 DeepSeek 密钥后还支持 Flash、Flash 快速预设、Flash 深度思考预设和 V4 Pro。真实模型名、四个选项的区别和后端变量见 [DeepSeek 接入说明](deepseek-setup.md)。

- `GPT 5.4-mini（recommend）` → `gpt-5.4-mini`
- `GPT 5.4` → `gpt-5.4`
- `GPT 5.4-nano` → `gpt-5.4-nano`
- `GPT 5.5` → `gpt-5.5`
- `GPT 5.6 Sol` → `gpt-5.6-sol`
- `GPT 5.6 Terra` → `gpt-5.6-terra`
- `GPT 5.6 Luna` → `gpt-5.6-luna`

标识依据 OpenAI 官方模型文档：[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)、[GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)、[GPT-5.4 nano](https://developers.openai.com/api/docs/models/gpt-5.4-nano)。`recommend` 是本项目的默认推荐标记。实际是否有权调用取决于部署使用的 API 项目；下拉框展示的是管理员允许使用的模型，不会每轮请求 OpenAI 模型目录。

新增标识及结构化输出能力已核对官方文档：[GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)、[GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)、[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)。其中 `gpt-5.6` 是 Sol 的别名，默认列表使用明确的 `gpt-5.6-sol`，避免显示两个指向同一模型的选项。

## 后端需要的配置

在后端环境变量或现有 `.env.production` 中设置：

```dotenv
ECE329_GENERATOR=openai
OPENAI_MODEL=gpt-5.4-mini
ECE329_ALLOWED_MODELS=gpt-5.4-mini,gpt-5.4,gpt-5.4-nano,gpt-5.5,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna
# OPENAI_API_KEY 沿用现有后端密钥，不放进前端。
```

`OPENAI_MODEL` 是 OpenAI 为基础供应商时的默认模型，也继续作为后台反馈经验提炼模型；DeepSeek 为基础供应商时对应 `DEEPSEEK_MODEL`。`ECE329_ALLOWED_MODELS` 控制用户可选清单，必须包含基础模型。未设置白名单时，按配置的供应商密钥开放七个 OpenAI 选项和四个 DeepSeek 选项；自定义 OpenAI 模型使用其 API ID 作为界面名称。现有 token 设置作为动态预算的基线，整轮上限始终优先于 DeepSeek 的预算下限。超时仍使用各供应商现有设置。OpenAI 使用 Responses/严格 JSON Schema；DeepSeek 使用 Chat JSON Output 加本地完整 Schema 校验。默认 OpenAI reasoning 能力优先用白名单中的 `gpt-5.6-sol`；可通过 `ECE329_MODEL_REGISTRY` 改写。

已有部署若显式设置了旧的三个模型白名单，更新代码不会覆盖管理员配置。请将后端该环境变量更新为上述清单并重启服务，然后点击前端模型列表旁的刷新按钮；仅修改 `.env.example` 不会改变生产环境。

更新配置后重启后端，再发布前端静态文件。前端不需要维护另一份模型列表，也不需要填写模型密钥。`ECE329_GENERATOR=rule` 或自动模式未配置在线模型时，后端返回不可选状态，页面明确显示本地规则模式。

## 接口和实现

- `GET /v1/models`：返回 `enabled`、`default_model`、模型 `id/label/recommended` 清单及 `routing` 注册表，无密钥信息。
- `POST /v1/designs`：在既有 `{idea, interaction_state?}` 中支持可选 `model`、`model_config`。
- `POST /v1/designs/{id}/turns`：同样支持 `model`、`model_config`，仍需设计 Bearer 令牌。
- 创建、消息及恢复响应带 `selected_model`、`model_config` 和配置版本。`selected_model` 表示最近实际使用的模型；配置表示后续调用策略。已有固定模型的旧会话继续保留选择，新设计使用 Recommended。

后端在创建/执行前校验白名单，不接受客户端任意模型 ID。配置保存在会话 `model_context` 中，随现有 SQLite 会话持久化；不进入实验规范字段。每次意图解析/回复生成按当时的内部阶段创建独立 generator；同一轮若跨阶段，可切换到新阶段的能力。手动固定模型优先于阶段模型选择。不同用户并发选择不会修改共享默认模型。

切换时清除旧 `openai_previous_response_id`，重新以现有设计、待办和本地对话上下文发起请求；不跨模型沿用远端响应链。随后同模型、同经验版本的轮次可继续使用其新响应链。旧消息保留当时选用的模型记录；切换不会追溯改写它们。默认保留原有有限重试及可配置的本地规则回退。只有显式勾选“失败时有限升级模型”才启用自适应升级，且不会覆盖手动固定模型或当前阶段的明确覆盖。

远端响应链失效或输出修复需要重新请求时，同样补回本地对话历史；链恢复最多重试一次，输出修复沿用既有有限次数。后端改为规则模式后，未显式传模型的旧会话可以继续，成功处理时清除已失效的在线选择及远端链，历史和实验内容保留；仍显式要求在线模型的请求会被拒绝。在线部署移除白名单模型时仍要求用户重新选择。

模型和策略均纳入新请求的幂等指纹：同一 `turn_id` 或创建请求编号不能换配置重放。前端将提交时配置固定在 `pendingRequest`；刷新页面或网络重试仍使用该值。用户改变选择后，快捷重试沿用原配置，新发送消息使用新选择。历史请求未带这些字段的指纹保持兼容。后台管理员移除模型后，页面提示重新选择，不自动替换已选模型。

## 检查

```bat
set PYTHONPATH=src
python -m pytest -q tests/test_model_selection.py
node --test tests/frontend/model-selection.test.cjs
```

测试覆盖两个 mode 的实际模型请求、意图恢复重试、切换/同模型响应链、并发隔离、白名单拒绝、幂等、SQLite 重启及前端请求冻结。受控 transport 测试不等于实际 API 账号权限验证；部署后应为每个开放模型发送一条真实消息并检查错误或回退提示。
