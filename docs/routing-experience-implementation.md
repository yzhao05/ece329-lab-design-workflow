# 模型路由、反馈经验与评测实施说明

2026-09-12。保留原 Web/API、状态机、Structured Outputs 和本地校验。界面仍分七个阶段组；后端实际有 13 个 `Stage`，Custom 按这 13 个执行阶段配置，避免把原有状态错误压缩为七个。校验器始终启用，不作为可替换模型。

## 五项方案的落地位置

| 方案 | 已实现行为 | 主要代码 |
| --- | --- | --- |
| 反馈经验 | 逐回答反馈、结构化提炼、会话/项目/全局候选、审核/停用/软删除、同范围去重、版本审计、Top 3 检索 | `experience.py`、`feedback.py`、`feedback-client.js`、`feedback-ui.js`、`feedback-review.js` |
| 统一配置 | fast/balanced/reasoning 注册表、13 阶段默认能力映射、模型白名单与配置校验 | `model_routing.py`、`model_selection.py` |
| 模型路由 | Recommended/Fast/High Quality/Custom、整体能力、逐阶段覆盖、固定模型、可选有限升级 | `model_routing.py`、`engine.py`、`model-strategy.js` |
| API | 沿用 `/v1` 与设计 Bearer 鉴权，增加会话配置、相关经验、评测读取；配置版本冲突保护 | `api.py`、`security.py` |
| 评测 | 每轮和每次调用的模型/推理/经验 ID、usage、延迟、失败、重试、反馈关联，网页 JSON 与只读汇总导出 | `telemetry.py`、`experience.py`、`export_workflow_telemetry.py` |

Python 文件位于 `src/ece329_workflow/`，前端脚本位于 `docs/assets/`，导出脚本位于 `tools/`。反馈经验属于提示与检索改进，不训练模型参数，不替代代码修复。

## 使用方式与优先级

新设计默认 Recommended，根据当前内部阶段请求能力。Fast 全程 fast；High Quality 全程 reasoning；Custom 可选整体能力并逐阶段覆盖。直接从回复模型下拉框选择具体模型会切到 Custom，并固定模型；点击“使用能力路由”清除固定模型。固定模型优先于阶段模型选择；推理强度默认来自相应能力，用户可在浮窗中为每个模型单独覆盖，具体限额与部署见 [思考强度与动态预算](reasoning-budgets.md)。

选择从下一条新消息生效，也可点击“保存策略”单独保存到后端。保存配置不修改实验版本、不推进阶段，有独立配置版本检查。待重试请求冻结原始模型和完整配置，不能用相同请求编号替换参数。旧会话已固定模型时保留旧选择。在线服务切到规则模式时沿用原兼容处理；规则模式不显示可用在线路由。

后台调用每次按当前阶段重新 resolve，同一轮跨阶段也会重新选择。每个调用使用独立 generator，避免多会话模型串用；实际模型改变会清除远端 response chain，保留本地设计与对话。反馈经验版本/集合改变也会清链，防止停用规则继续留在模型上下文。

## 后端部署

在实际使用的环境文件或服务环境中配置后重启后端；`.env.example` 只是模板。已有 SQLite 自动增加所需表和索引，已有设计和反馈记录保留。前端需同步发布 `docs/` 更新。

```dotenv
ECE329_GENERATOR=openai
OPENAI_MODEL=gpt-5.4-mini
ECE329_ALLOWED_MODELS=gpt-5.4-mini,gpt-5.4,gpt-5.4-nano,gpt-5.5,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna
ECE329_PROJECT_ID=ece329
# OPENAI_API_KEY、ECE329_DATABASE_PATH、ECE329_FEEDBACK_ADMIN_TOKEN 沿用部署配置。
ECE329_MODEL_REGISTRY={"fast":{"model":"gpt-5.4-mini","reasoning":"low"},"balanced":{"model":"gpt-5.4","reasoning":"medium"},"reasoning":{"model":"gpt-5.6-sol","reasoning":"high"}}
ECE329_STAGE_POLICY={"THEORETICAL_FRAMEWORK":"reasoning","HYPOTHESIS":"reasoning"}
```

注册表非空时必须完整包含三种能力，各模型必须在白名单内。阶段策略允许只覆盖部分枚举名；完整默认映射可通过 `GET /v1/config` 查询。默认 fast 使用基础模型/low，balanced 使用基础模型/现有推理强度；reasoning 优先用允许的 `gpt-5.6-sol`，否则用基础模型/high。示例使用不同模型是管理员可选配置，不要求每个部署全部开放。

新增 DeepSeek 后，基础供应商为 DeepSeek 时 reasoning 优先使用其白名单中的 V4 Pro。注册表可混用两家已配置的供应商；DeepSeek 预设、实际推理强度映射及密钥配置见 [DeepSeek 接入说明](deepseek-setup.md)。

`ECE329_PROJECT_ID` 是服务端可信项目分区。修改它不会迁移旧项目经验；没有账户体系时不声称已实现个人用户库。全局经验跨项目共享，仍须维护者审核。维护者令牌不得放进静态配置或学生浏览器存储。

## API

| 接口 | 权限与行为 |
| --- | --- |
| `GET /v1/models`、`GET /v1/config` | 公开白名单与路由信息，无密钥 |
| `GET /v1/designs/{id}/model-config` | 设计 Bearer，返回 `{config, version}` |
| `PATCH /v1/designs/{id}/model-config` | 设计 Bearer，提交 `{config, version}`；版本冲突 409，不推进阶段 |
| `GET /v1/designs/{id}/experiences/relevant?q=...` | 设计 Bearer，当前 mode/阶段/主题与范围检索，关闭经验时为空 |
| `GET /v1/designs/{id}/telemetry?offset=0` | 设计 Bearer，每页最多 100 条，返回 `records/next_offset` |
| `POST /v1/designs/{id}/feedback` | 设计 Bearer，反馈及可选目标版本/阶段/telemetry ID/范围 |
| `GET /v1/feedback/experiences`、`POST /v1/feedback/experiences/{id}/review` | 独立维护者令牌，查看及审核/编辑/停用/软删除 |

原创建与 turn 接口支持可选 `model_config`，结构如下：

```json
{"strategy":"custom","profile":"balanced","stage_overrides":{"HYPOTHESIS":"reasoning"},"model_override":null,"experience_enabled":true,"adaptive_enabled":false}
```

`strategy` 可为 `recommended/fast/quality/custom`。阶段覆盖和具体模型覆盖只允许在 Custom 使用。传入旧 `model` 字段仍受白名单保护，并转为 Custom 固定模型。配置及反馈提交均不允许更改本地校验器。

## 有限自适应路由

默认关闭，用户主动勾选“失败时有限升级模型”后启用：

- 从该设计最近 100 条评测中取当前阶段最近 5 次生成事件；至少 2 次失败，下一次将能力提高一级。
- 当前生成校验失败后，最多增加一次 reasoning 能力生成尝试。原单次生成仍保留最多一次输出修复；再次失败返回现有错误/干预路径，不自动持续重试。
- 手动固定模型、为当前模型指定思考强度、当前阶段明确覆盖及已用 reasoning 能力的调用均不再升级。记录升级原因与实际每次调用模型，便于复核。
- 启用该选项时生成失败先走上述有限升级；耗尽后不会再套一层自动本地回退循环。默认关闭时保留原生成器回退策略。

后台反馈分析另有最多 3 次人工重试上限；无待处理任务时 worker 退出。前端轮询和导出分页也有限额。选择强模型可能增加调用费用，该选项不默认开启。

## 评测记录与导出

前端“导出评测记录”导出当前设计 JSON；后台可在仓库根目录 CMD 使用只读导出：

```bat
set PYTHONPATH=src
python -m tools.export_workflow_telemetry --database "实际数据库.sqlite" --output "build\workflow-telemetry.json"
```

可加 `--design-id "实际设计ID"`。输出含原始 records 和按 mode/初始阶段/策略/经验开关/自适应开关分组的 summary。每条回答携带 telemetry ID；其反馈加入对应评测记录，旧回答仍可反馈，无法关联旧评测时保留反馈本身。报告早期回答时，后台会从服务器历史单独提取目标轮原文；匹配不到则明确为空，不把当前快照伪装成历史证据。

评测记录保存实际发送的模型、推理强度和提示中实际出现的经验 ID；记录 provider usage、延迟、有限重试和异常类型，不保存原始提示、访问令牌或服务商错误正文。没有 usage 的调用写 `null`，汇总单列未知用量，不当成零费用；规则模式零模型调用的 token 合计为零。阶段 `validator_pass` 表示模型结构/本地生成约束检查，不代表物理或教学质量被人工认可；无模型调用或网络故障无法判定时为 `null`，不作为历史校验失败触发升级。整体阶段完成失败另见 `completion_error`。后台反馈提炼不计入这些设计阶段调用的用量。

可通过新建配对设计进行 Experience ON/OFF、固定模型/强弱能力、固定路由/自适应路由对照。应固定输入、课程条件、模型版本与评审标准；旧会话的历史回答已受旧经验影响，切换开关不会抹除这些历史。当前提供记录和分组工具，尚未代替用户运行真实模型对照实验，也未内置随价格变化的美元估算。

## 验证

`tests/test_routing_and_telemetry.py` 覆盖策略优先级、鉴权/版本冲突、不推进阶段、跨阶段模型、作用域隔离、持久化、反馈关联、历史目标、未知 usage、有限升级与经验撤回。原模型/反馈/状态机/PDF 回归继续运行。前端 Node 测试覆盖请求冻结和反馈重试；三个 `tools/verify_*_browser.cjs` 用本机服务、真实 SQLite 与受控模型替身验证界面链路。替身联调不代表生产 API 账号权限或真实教学效果验证。
