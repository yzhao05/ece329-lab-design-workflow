# ZJUI ECE329 实验设计工作流

这是一个面向学生的实验**设计**工作流。它帮助学生把模糊想法发展成 ECE329 Lab Design Proposal；它不要求真实搭建实验，也不生成伪造的实测数据。

项目提供 Python 状态机和 HTTP API；PDF报告使用 ReportLab 在后端即时生成。后端已经接入 OpenAI Responses API；未配置密钥或模型服务临时失败时，会使用讲义约束的本地规则生成器。本地回退只执行结构化按钮事件与保守内容承接，不会重新启用自然语言关键词猜测，因此完整的语义推进需要可用的Agent API。

## GitHub Pages网页

`docs/` 中包含一个可直接发布到 GitHub Pages 的静态网页，包括：

- 响应式聊天界面与7个大阶段进度；
- GUIDED／EMVR状态展示；
- lecture note概念、公式和页码依据面板；
- Stage 10理论预测曲线与参数滑块；
- EMVR阶段草稿、累计任务报告，以及完成后的学生版设计报告 PDF 与 Builder Gate 1 输入 PDF 下载；
- 结构化显示设计一致性、因果链、概念可行性、课程追溯与版本差异；
- GUIDED完成后导出学生自己撰写的设计总结；
- 浏览器本地会话保存；
- 空API配置和未来后端适配层。

当前 `docs/assets/config.js` 中的 `API_BASE_URL` 特意保持为空，因此网站会明确显示“演示模式”。演示回答来自浏览器本地规则，不会伪装成真实Agent。API上线后只需填写后端HTTPS地址。

网页接入兼容后端时还会执行以下保护：

- 学生明确确认后，GUIDED请求自动生成 `complete_stage` 及想法探索／学生总结所需的 `context_patch`；
- 只有 `API_BASE_URL` 为空时才使用明确标识的本地演示模式；API已配置但健康检查或请求失败时会保留真实会话并提示重试，不会悄悄改用本地回答；
- 后端会话失效或设计令牌不匹配时，网页会清除旧会话并要求重新开始，不会把“继续”伪装成新设计；
- 若后端启用课程访问码，网页会在首次创建设计时询问；课程访问码和短期设计令牌只保存在当前标签页的 `sessionStorage`。网页另存一个可轮换、仅用于该设计恢复的恢复凭证，关闭页面后用它换取新短期令牌；每次恢复都会使旧令牌和旧恢复凭证失效；
- 每次创建或继续设计都会携带稳定的 `Idempotency-Key`／`turn_id`。网络超时后的同轮重试返回原响应，不会再次调用模型、重复保存或推进两次；
- Stage 10若收到API返回的 `series.points`，会按返回坐标轴和数据点绘图，不再用本地示意曲线代替；没有数据点时仍明确显示为示意预览。

本地预览（CMD）：

```bat
python -m http.server 4173 --directory docs
```

打开 `http://127.0.0.1:4173`。GitHub发布步骤和API接入边界见 `docs/README.md`。

仓库已经提供 `.github/workflows/pages.yml`。推送到GitHub后，在 **Settings → Pages → Source** 选择 **GitHub Actions**，之后修改 `docs/` 会自动触发发布。

不要把OpenAI密钥或其他秘密写入 `docs/`。GitHub Pages中的所有前端文件都可被访问，模型调用必须经过独立后端。

## 核心行为

工作流有两个交互状态：

- `GUIDED_DESIGN`：默认状态。第1大阶段采用“了解具体想法 → 一次广度拓展 → 学生描述兴趣点 → 基于描述深度拓展 → 形成实验大纲雏形 → 动态补齐缺口 → 学生确认”的对话节奏。只有广度拓展会显示多个课程方向；学生选定后改用开放式描述，不连续出选择题。大纲形成后，系统每轮重新检查七项必要内容，只引导当前最关键的缺口；学生一条回复可以同时明确多项。第7大阶段由学生自己总结，系统不代写最终方案。
- `EMVR_DIRECT`：由网页/API的结构化模式事件指定，或在安全检查通过后，只要输入中包含 `EMVR` 标记就切换。系统默认学生已经有一个模糊实验想法，不进入“没有思路—展示三幅图景”的分支。每个关键阶段先结合此前确认的目标、对象、变量和流程给出一份专业、可修改的阶段草稿，再请学生核对物理内容、Unity映射或展示要求；学生确认后由状态机进入下一阶段。右侧任务报告同步累计已经整理的内容。除这个明确标记外，其余自然语言意图仍由上下文语义解析器判断。

两种状态都遵守同一条硬规则：**一次 API 响应只处理一个大阶段，并且最多给学生一个当前任务**。阶段推进由状态机控制，内容生成器不能跳步。为兼容现有API和已保存会话，后端仍保留13个内部步骤标识；网页把前7个内部标识合并显示为“实验想法完善”的七项完整性维度，不显示固定序号。

### 上下文意图与阶段推进

对话推进采用“本轮任务编排 + 结构化待办 + 字段级状态提交 + 设计差异检查 + 确定性状态机”，不靠不断扩充同义词正则：

- 每轮回答后，后端在学生不可见的会话状态中保存 `pending_action`，记录上一轮正在等待确认、修改或回答的对象；
- 下一轮把上一问题、`pending_action`、已经确认的 `carried_context` 和学生新消息一起交给独立的结构化意图判断；
- 语义判断先把一句话拆成多个 `dialogue_acts`，分别表示回答当前问题、修改设计、课程提问、索取参考、纠正理解或推进；内部任务编排器先提交清楚的设计修改，再完成解释／参考请求，最后执行阶段控制；
- 一句话中某一片段不明确时，其他可执行任务仍会提交，回复只追问未解析的片段；课程问题、纠错和“给我参考”等会话内容不会写入实验设计；
- 研究方向和研究问题确认后建立统一主题锁定。后续补充默认属于当前实验，只有语义解析明确确认学生放弃当前方向时才允许重置；
- 引导模式和EMVR模式的开放问题共享同一套回答状态：语义服务第一次未能绑定明显回答时会暂存该回答，学生说明“上一条就是回答”后可直接恢复，不要求重写；
- 对阶段草稿的补充会依据待办归为“修改当前草稿”并与原内容合并；只有结构化语义明确标记学生要放弃并替换研究方向时，才会归档旧方向并重新开始；
- 两种模式都会按阶段保存学生的实质回答与修改；“补充且其他不变”采用合并操作。学生可以增加资料目录未预设的对照情形，但新增内容必须能逐字追溯到学生本轮输入，模型不能自行扩充；
- 基础对照的每个物理情形在语义判断中使用稳定身份；缩写、完整说法和重新命名会更新同一情形，不会被当成三个新情形重复追加；
- 研究问题、学习目标、假设、预期现象、流程环节和后续设计项也保存稳定语义标识；语义相同的改写不会重复追加，明确的替换仍只更新目标字段；
- 每轮提交后比较修改前后的统一设计状态。学生只看到实际变化，例如“将观察量由……调整为……”；如果内容没有变化，会明确说明当前设计保持不变，不会重复整份方案；
- 每次进入新阶段都会生成完整的阶段间摘要，包含研究对象、课程关系、学习目标、研究问题、比较条件、变量、观察量、假设、流程和显示方式，已经明确的内容不会再次作为空白问题提出；
- EMVR修改使用逐字段操作保存研究方向、研究问题、变化量、观察量、假设、流程等内容。一轮修改多项时分别落到各自字段，抽象改写只替换被点名的字段，未点名内容保持不变；
- 意图统一为回答当前问题、接受／修改／拒绝上一建议、推进阶段、索取更多例子、只读查看当前设计、返回、换题、切换交互状态和不明确；模型只返回结构化语义，不直接修改阶段编号或交互状态；
- 状态机验证意图与置信度后才更新决定或推进。低置信度只提出一个简短澄清问题，不重复整段阶段入口；
- 新阶段会继承已经确认的研究方向、比较情形、变量、观察量和控制条件，并据此生成可修改的参考结构。
- 每轮更新后还会执行设计一致性、因果链、概念可行性、边界情形和课程追溯检查；检查只指出最重要的缺口，不会静默改写学生的方向；
- 设计修改保存为字段级版本。学生可查看最近修改、比较版本、只撤销某一项或恢复指定版本；报告与统一设计状态同步恢复；

安全底线、API格式校验和明确的网页按钮事件仍由确定性程序处理。课程范围由语义解析器结合整句话、当前待办和讲义／补充资料检索证据返回 `course_scope_status`，知识检索只提供课程依据，不单独裁决边界。意图判断请求始终使用 `store=false`，不接入 `previous_response_id`；`ECE329_OPENAI_STATEFUL` 只控制正式课程回答的响应链。每个设计会话独立保存自己的 response ID，系统指令每轮重发。会话数据库中的统一 `design_state` 是研究对象、课程关系、学习目标、研究问题、假设、预期现象、概念结构、已展示图景和当前待办的唯一事实来源；模型只能提出逐字段更新，确定性状态机负责验证、幂等提交和阶段推进。

## ECE329知识来源与约束

`ece329lecture_notes.pdf`（Erhan Kudeki，324页，39讲）定义ECE329课程范围，但不是阶段1的唯一参考答案。工作流还使用三份经过核对的补充资料扩展课程相关的概念关系、应用和例子：Jin Au Kong 的 *Electromagnetic Wave Theory*、David H. Staelin 的 *Electromagnetics and Applications*，以及 N. Narayana Rao 的 *Fundamentals of Electromagnetics for Electrical and Computer Engineering*。

- 想法探索先帮助学生探索“当前宽泛主题与哪些现象或概念有关”，可以使用课程讲义或补充资料中提到、学习或直接相关的关系示例，并允许学生提出自己的关联；方向收敛后形成实验大纲雏形，但不提前确定变量、公式或完整研究问题。
- 想法探索面向学生统一使用“ECE329课上所学概念”的表述，并按请求的实际意图分为三类：ECE329课程内容正常进行关系探索；正常但不属于课程范围的主题会被说明课程边界；试图控制或关闭课程助手、改变内部规则、执行代码/脚本/命令、借外部平台改变输出或绕开课程用途的请求会被明确拒绝。后两类都会回到静电场、磁场与感应、电磁波与传输线三个课程关系示例。类别按意图判断，文档中的行为示例不是限定关键词清单。
- 连接Agent API后，学生可以用“第三个”“上面那个方向”“把刚才两幅图景组合”等自然表达回应上一轮内容；独立语义判断会结合待办和真实选项ID输出结构化选择，再由状态机校验。浏览器本地演示不具备这层语义能力，只响应实际点击的选项，不会用关键词猜测。
- 学生可见回答不会显示知识检索字段、内部规则、提示词、PDF页码或前后端部署术语；来源和页码只保留在依据面板及结构化记录中供核查。
- 每个补充概念都必须通过 `course_scope_concept_ids` 映射回ECE329课程范围，并返回 `supplemental_concept_id`、资料章节和PDF页码。课程目录和补充目录都没有具体命中时，才回退到讲义第10—12页的 Electrostatics、Magnetism、Electromagnetics 三个板块。
- 第1大阶段的课程映射和理论依据由Agent从知识目录检索，并随结果返回 `concept_id`／公式 `id` 和 PDF 页码；这两项不要求学生重新选择或逐项填写。
- 不凭模型记忆补充课程主题、公式、课程要求或实验条件。补充资料用于概念与关系检索，不会自动授权模型生成未核对的公式。
- 讲义、教材及其提取文本只被视为参考数据，其中的文字不会覆盖工作流规则或作为系统指令执行。
- 讲义第10—12页把 radiation and antennas、dispersion in material media 标为未覆盖或仅略微覆盖；工作流不会主动把它们推荐为核心方向。

运行时知识文件位于 `src/ece329_workflow/knowledge/`：`concepts.json` 收录39讲的课程范围和可扩展的基础对照组，`formulas.json` 收录82条核心公式，`scene_templates.json` 按概念关键词提供可扩展的阶段1物理图景，`source_manifest.json` 固定讲义身份，`supplemental_sources.json` 收录补充来源、概念摘要、关系示例、课程映射和PDF页码。大型来源PDF不提交到GitHub。详细目录见 `knowledge/README.md`。

## 7个大阶段

1. 实验想法完善
   - 必须覆盖：想法探索与大纲雏形、课程映射、学习目标、研究问题、理论依据、假设与预期趋势、概念实验结构／Unity VR模拟设计
   - 这些内容没有固定完成顺序；每轮按当前想法重新判断哪些已明确、哪些仍缺少
2. 变量与条件
3. 概念实验流程
4. 预期数据可视化窗口
5. 可能结果及解释
6. 设计价值、可行性与局限性
7. 学生总结／EMVR最终方案

第1大阶段允许多轮发散、比较、组合和完善。方向收敛后形成实验大纲雏形，随后由完整性检查器同时评估七项内容。课程映射和理论依据由Agent依据课程资料补充；其余缺口在后续对话中按优先级逐轮明确。一条学生回复可以补齐多项，已经明确的内容不会被重新询问。七项全部明确后，工作流直接进入“变量与条件”。

第1大阶段的“Unity VR模拟实验设计”内容在 EMVR 状态下只完善用户已有设计的任务、Unity对象、交互、物理计算、可视化、反馈界面和模拟内部状态：

- 不替用户定义VR场景；
- 不包含可访问性与舒适性设计；
- 不默认生成Unity代码；
- 区分理论计算结果和教学示意动画。
- 延续引导模式的 `pending_action + carried_context + 语义解析器 + 状态机` 上下文机制，保留学生已经确认的学习目标、研究问题、假设、对象和交互，不要求学生从头复述；
- 学生对阶段草稿的回答和修改会按阶段写入 `emvr_stage_inputs`，作为后续生成和规则回退的权威依据；修改草稿时必须让对应字段发生可见变化，不能重新输出未修改的旧草稿；
- 研究问题、变量、假设和对象约束直接使用学生已经定义的具体内容，不向学生显示“由阶段N确定”一类内部占位符；
- 语义解析器先把实验整理为变化量、观察量、比较情形、对象约束和物理关系类型，公式层只依据已保存的关系类型选择公式，并逐条记录它支持哪项研究内容；原始文字和同讲次命中都不能直接触发公式；
- 最终 PDF 必须包含研究问题、四类学习目标、与当前研究相连的理论关系及对应说明、假设、变量与控制条件、完整实验物体枚举、学生交互及物理状态、理论可视化、概念实验流程、结果解释和局限；缺项时不会生成一份看似完整的PDF。报告只记录设计，不代表已经生成或实现 Unity 实验。

第4大阶段返回可供前端渲染的 `visualization` 对象。所有数据必须标记为 `theoretical_prediction` 或 `illustrative_synthetic_data`，并且 `measured=false`。

## 与 EMVR Blind Builder Pack 的关系

本项目只参考同一研究工作区中的 `EMVR_Blind_BuilderPack` 当前接口和单阶段工作原则，不读取其他 EMVR 项目作为设计依据。

本工作流是 Builder Pack 之前的设计前端：

- 不运行 Builder Pack 的 Gate；
- 不创建 Unity 场景或代码；
- 不修改 Builder Pack；
- 不替用户批准任何 Gate；
- EMVR 最终输出同时提供学生版设计报告与 Builder Gate 1 输入 PDF。后者按
  `LabSpecs/templates/lab-brief.template.yaml` 的字段组织，保留
  `confirmed-from-design-session`、`inferred-needs-confirmation` 和 `unresolved`
  状态，供 Builder 阶段 1 转写并与用户确认；它不替用户批准 Gate，也不直接修改 Builder Pack。

真正进入 Builder Pack 后，房间/XR Prefab复用、Common API审计、Unity编译、测试和验收仍应按照 Builder Pack 自身规则完成。

## 快速运行

不安装任何依赖即可启动开发服务器：

```bat
set PYTHONPATH=src
python -m ece329_workflow --host 127.0.0.1 --port 8080
```

存活与就绪检查：

```text
GET http://127.0.0.1:8080/health
GET http://127.0.0.1:8080/ready
```

返回的 `generator.provider` 表示当前生成器：`rule_based` 表示尚未配置模型；`openai` 表示已启用模型。它只显示模型名和是否允许回退，不显示密钥。OpenAI模式下还会显示 `api_successes`、`api_failures`、`output_rejections`、`repair_successes` 和 `fallback_calls`；若发生回退，`last_fallback_reason` 会用不含学生内容的类别说明最近原因。这样可以区分网络/API失败与模型回答未通过工作流校验。
`storage.provider` 表示会话存储；本地默认是 `memory`。设置 `ECE329_DATABASE_PATH` 后会使用 `sqlite`；只有云平台把该路径挂载到持久化磁盘时，容器重建后数据才会保留。
网页使用 `/ready` 检查会话存储是否可读写；只有该检查通过才显示课程服务已连接。

## 接入OpenAI API（CMD）

API密钥必须只放在运行后端的服务器环境变量中，不能写入 `docs/`、`config.js`、Git提交或浏览器请求。先在 OpenAI Platform 创建密钥，然后在同一个CMD窗口执行：

```bat
set OPENAI_API_KEY=在这里粘贴你的密钥
set ECE329_GENERATOR=auto
set OPENAI_MODEL=gpt-5.4-mini
set OPENAI_REASONING_EFFORT=medium
set OPENAI_INTENT_MAX_OUTPUT_TOKENS=1400
set ECE329_OPENAI_STATEFUL=true
set PYTHONPATH=src
python -m ece329_workflow --host 127.0.0.1 --port 8080
```

`ECE329_GENERATOR=auto` 是推荐设置：有 `OPENAI_API_KEY` 时使用OpenAI，无密钥时使用本地规则。模型回答第一次未通过结构或课程约束时会自动重试修正一次；仍不合格，或遇到超时、网络错误时，才回退到课程内置引导。回退不会清空学生此前的实验方向、细化关系或阶段进度。

可选服务器环境变量：

- `OPENAI_MODEL`：模型ID，默认 `gpt-5.4-mini`；
- `OPENAI_REASONING_EFFORT`：所有OpenAI语义判断与正式回复的推理强度，默认 `medium`；
- `OPENAI_INTENT_MAX_OUTPUT_TOKENS`：语义意图JSON调用的总输出预算（包含推理token），默认1400；
- `OPENAI_TIMEOUT_SECONDS`：请求超时，默认60秒，为 `medium` 推理预留更多时间；
- `OPENAI_MAX_OUTPUT_TOKENS`：单轮最大输出，默认2400；
- `OPENAI_STAGE_ONE_MAX_OUTPUT_TOKENS`：引导模式阶段1的最大输出，默认3200，用于生成多幅有细节、可组合的课程内物理图景；
- `OPENAI_FINAL_MAX_OUTPUT_TOKENS`：EMVR最终设计包的最大输出，默认5000；
- `ECE329_OPENAI_FALLBACK`：默认 `true`。设为 `false` 后，模型失败会返回HTTP 502；
- `ECE329_OPENAI_STATEFUL`：代码在未配置时仍采用隐私优先的 `false`；学生网站的 Render 部署建议明确设置为 `true`。正式课程回答会保存并续接该设计会话自己的 `previous_response_id`，请求使用 `store=true`；意图分类始终无状态，系统指令仍会在每轮重新发送，本地 `design_state` 仍是最终事实来源；
- `ECE329_GENERATOR=rule`：强制使用本地规则生成器；
- `ECE329_GENERATOR=openai`：强制要求密钥，缺少密钥时后端拒绝启动。
- `ECE329_ACCESS_CODE`：公开部署时强烈建议设置的课程访问码；它保护创建设计这一会产生模型费用的入口。
- `ECE329_MAX_TEXT_CHARS`：单条学生输入的最大字符数，默认4000，用于限制异常请求和模型费用。
- `ECE329_SESSION_TTL_DAYS`：不活跃设计的保留天数，默认30；
- `ECE329_ENABLE_PROMPT_DEBUG`：默认 `false`。公开部署不要开启完整提示包调试接口。

启动后分别访问 `http://127.0.0.1:8080/health` 和 `http://127.0.0.1:8080/ready`。确认 `generator.provider` 为 `openai` 且 `storage.read_write_check` 为 `ok` 后，使用下一节的GitHub Repository Variable连接公开后端；不需要修改仓库中的 `config.js`。GitHub Pages只能连接可公开访问的HTTPS后端，不能直接连接你电脑上的 `127.0.0.1`。

## 公开部署

项目根目录现在包含生产用 `Dockerfile`、无秘密的 `.env.example`、可选SQLite持久化、指定来源CORS、请求体限制和按客户端地址计算的基础POST限流。完整流程见 [DEPLOYMENT.md](DEPLOYMENT.md)。

GitHub Pages推荐使用Repository Variable `ECE329_API_BASE_URL`。Pages Action会在上传产物前注入公开后端URL，仓库中的 `docs/assets/config.js` 仍保持空白。后端则必须设置：

```text
ECE329_ALLOWED_ORIGINS=https://你的用户名.github.io
ECE329_DATABASE_PATH=/data/ece329.sqlite3
ECE329_ACCESS_CODE=请使用独立生成且不提交到Git的访问码
ECE329_SESSION_TTL_DAYS=30
ECE329_ENABLE_PROMPT_DEBUG=false
```

其中 `/data` 必须是云平台提供的持久化磁盘。课程访问码和每个设计的随机令牌提供小规模课程使用所需的基础保护；公开大规模使用前仍应接入学校登录、API网关或验证码，并使用共享限流。

## API

### 创建一个引导式设计

```http
POST /v1/designs
Content-Type: application/json
X-ECE329-Access-Code: 课程访问码（后端启用时）

{
  "idea": "我想研究金属网对无线信号的影响"
}
```

响应进入第1大阶段并等待学生继续 brainstorm。方向收敛并形成大纲雏形后，响应会在 `stage_payload.idea_development_status` 中返回七项内容的已明确项、缺失项和当前优先缺口。响应中的 `design_access_token` 仅返回一次；后续设计路由必须使用 `Authorization: Bearer <design_access_token>`。

### 完成想法完整性检查并进入变量与条件

```http
POST /v1/designs/{design_id}/turns
Content-Type: application/json
Authorization: Bearer <design_access_token>

{
  "message": "确认想法完善并进入变量与条件",
  "complete_stage": true,
  "context_patch": {
    "idea": {
      "phenomenon": "电磁屏蔽",
      "main_direction": "网格尺寸对屏蔽效果的影响",
      "student_confirmed": true
    }
  }
}
```

只有大纲雏形、课程映射、学习目标、研究问题、理论依据、假设与预期趋势、概念实验结构全部明确时，这条纯推进请求才会把会话移动到“变量与条件”。响应中的 `handled_stage` 与 `current_stage` 都是 `VARIABLES_AND_CONDITIONS`，`transitioned_from_stage` 记录 `IDEA_BRAINSTORMING`；仍有缺口时工作流继续停留在第1大阶段并返回当前优先补充内容。

### 创建EMVR设计

```http
POST /v1/designs
Content-Type: application/json

{
  "idea": "请把偏振实验放入EMVR工作流中完成"
}
```

输入中包含 `EMVR` 标记时会切换为 `EMVR_DIRECT`。系统根据学生的模糊想法和已经确认的内容提供专业、可修改的阶段草稿，不展示三幅通用图景；学生可以逐项修订物理内容、Unity对象、交互或显示要求，确认前不会移动会话指针。EMVR 流程会在相应阶段前置确认 Builder Gate 1 必需的实验名称与ID、桌面鼠标/VR操作映射、房间空间与相对摆放、隐藏对象生命周期、参数范围与单位、Lab特有预期结果、通过条件和报告问题。它不会在下载前再列出若干未明确项，也不会替用户静默补齐。每次响应还会返回 `task_report` 和 `builder_handoff_status`；只有设计报告与全部 Builder 输入均通过校验后，才返回 `report_ready=true`、`report_url`、`builder_input_ready=true` 和 `builder_input_url`。两个下载地址都受设计令牌保护，分别对应学生版设计报告和 Builder Pack Gate 1 输入 PDF。

### 继续当前设计

```http
POST /v1/designs/{design_id}/turns
Content-Type: application/json
Authorization: Bearer <design_access_token>
Idempotency-Key: 由客户端为本轮生成的唯一ID

{
  "message": "保留这部分并继续",
  "complete_stage": true,
  "turn_id": "与Idempotency-Key相同"
}
```

`turn_id` 在每个设计会话内保存；相同ID和相同请求返回第一次的结果，相同ID配不同内容会返回冲突，防止客户端误复用。

网页关闭后可使用创建时返回的 `design_resume_token` 调用：

```http
POST /v1/designs/{design_id}/resume
Content-Type: application/json

{
  "resume_token": "该设计的恢复凭证"
}
```

响应会返回新的 `design_access_token` 和新的 `design_resume_token`，旧凭证立即失效。恢复凭证等同于该设计的恢复权限，不应写进仓库、日志或发给其他人。

### 获取当前设计状态

```text
GET /v1/designs/{design_id}                         (需要Bearer令牌)
GET /v1/designs/{design_id}?include_history=true    (需要Bearer令牌)
DELETE /v1/designs/{design_id}                      (需要Bearer令牌)
GET /v1/designs/{design_id}/report.pdf              (EMVR完成后可用，需要Bearer令牌)
GET /v1/designs/{design_id}/builder-gate1-input.pdf (EMVR完成后可用，需要Bearer令牌)
GET /v1/designs/{design_id}/guided-summary.txt      (GUIDED完成后可用，需要Bearer令牌)
```

## 真实模型全流程评测

普通单元测试不会消耗API额度。需要在发布前用真实模型回放 GUIDED 和 EMVR 全流程时，在已设置 `OPENAI_API_KEY` 的 CMD 中运行：

```bat
set PYTHONPATH=src
python tools\run_live_dialogue_eval.py --mode both
```

脚本会实际调用模型，并把完整对话写到 `.test-tmp\live-dialogue-eval.json`；退出码为0表示所选流程均到达完成状态。该文件不应提交，其中可能包含测试对话内容。建议在模型、提示词或状态结构发生较大变化时人工运行，而不是放进每次GitHub Actions。

### 获取阶段目录

```text
GET /v1/stages
```

### 查询课程与补充知识

```text
GET /v1/knowledge/source
GET /v1/knowledge/concepts
GET /v1/knowledge/supplemental-concepts
GET /v1/knowledge/formulas
GET /v1/knowledge/search?q=偏振
```

搜索响应会同时返回课程范围概念、补充概念、公式、阶段1候选方向及来源页码。创建设计和处理每轮的响应保留 `knowledge_source`，并新增 `knowledge_sources` 记录全部启用来源。

### 私有调试：获取大模型提示包

```http
POST /v1/designs/{design_id}/prompt
Content-Type: application/json
Authorization: Bearer <design_access_token>
X-ECE329-Debug-Token: <单独的调试令牌>

{
  "message": "学生本轮输入"
}
```

该接口会返回内部工作流约束，因此默认不存在（HTTP 404）。只有在私有调试环境同时设置 `ECE329_ENABLE_PROMPT_DEBUG=true` 和强随机值 `ECE329_PROMPT_DEBUG_TOKEN` 后才启用；公开部署必须保持关闭。生产模型接入由后端内部的 `StageGenerator` 完成，网页不需要也不应读取提示包。

## 引导状态“实验想法完善”完成条件

前端或模型编排层需要写入：

```json
{
  "idea": {
    "phenomenon": "已明确的现象",
    "main_direction": "学生选择的主要方向",
    "student_confirmed": true
  }
}
```

除上述学生确认字段外，后端还要求 `experiment_outline_seed` 已形成，并且 `idea_development.complete=true`。缺少任一项时，即使请求 `complete_stage=true`，工作流仍停留在“实验想法完善”，继续补当前缺口。

## 引导状态第7大阶段（内部步骤13）

第7大阶段不会输出完整 Proposal。系统逐部分要求学生总结，并只对学生草稿提供当前部分的反馈。全部总结由学生完成后，客户端可写入：

```json
{
  "synthesis": {
    "student_summary": "学生自己完成的实验设计总结（至少20个字符）",
    "student_summary_sections": [
      "学生第一次写下的总结部分（至少10个字符）",
      "学生第二次写下的总结部分（至少10个字符）"
    ],
    "student_summary_complete": true
  }
}
```

至少需要一段由学生自己写下、能够串联研究对象或问题、主要比较或观察以及课程关系的总结；系统不会补写最终方案，也不会要求学生再做一次形式化确认。

## 测试

```bat
set PYTHONPATH=src
python -m unittest discover -s tests -v
```

上述测试不会调用真实模型。更新提示词、课程边界或模型版本后，可在已经设置
`OPENAI_API_KEY` 的 CMD 中显式运行付费的阶段1模型回归：

```bat
set PYTHONPATH=src
python tools\run_live_stage_one_evals.py --confirm-cost
```

该命令使用与本地回归相同的课内、课外、不合理请求、序号跟进、英文边界和
否定EMVR样例，但强制关闭规则回退；任一模型分类不符合预期时返回非零退出码。

测试覆盖：

- 引导状态默认停留在第1大阶段；
- 大纲雏形形成后动态检查七项必要内容，不按固定小点顺序推进；
- 同一条学生回复可以补齐多项，全部明确并经学生确认后才进入变量与条件；
- 明确提到EMVR才切换直接状态；
- EMVR各关键阶段先询问学生，再根据回答生成可修改草稿；不会因学生没有完整方案而跳入三图景分支；
- EMVR上下文保留已确认的目标、研究问题、假设、Unity对象、交互和流程；
- EMVR最终PDF必须包含四类学习目标、完整物体枚举和概念实验流程，并明确不代表Unity实现已经完成；
- 每次只处理一个大阶段，并且最多给学生一个当前任务；
- 第1大阶段的Unity VR设计内容不定义VR场景和可访问性；
- 第4大阶段只输出理论预测；
- 第7大阶段引导状态不生成最终方案；
- HTTP健康检查。
- 39讲概念与82条公式目录的内部引用有效性；
- 想法方向带有课程范围映射及讲义/补充资料页码，理论依据中的公式带有已核对目录页码；
- 未知想法只回退到讲义总览板块；
- 课外主题会被明确标注，并返回三个ECE329课内示例；控制助手、代码注入、内部机制、外部平台改写输出和角色扮演等不合理请求会被拒绝且不会泄露项目术语；
- 多来源知识搜索接口。

## 更新知识目录

`tools/extract_lecture_notes.py` 用于生成带 PDF 页码的原始文本索引，`tools/inspect_lecture_notes.py` 用于辅助检查讲次、标题和公式候选。它们不在 API 运行时执行。

如果课程讲义发生变化，应重新提取并人工核对公式符号与页码。如果补充资料发生变化，应更新 `supplemental_sources.json` 中的来源哈希、概念摘要、课程映射和页码，然后运行完整测试。不要在未核对的情况下让大模型自动扩写公式目录。

## 生产部署注意事项

内置 `InMemorySessionStore` 适合本地开发；设置 `ECE329_DATABASE_PATH` 后会启用SQLite和乐观版本检查。SQLite适合单服务实例，多实例部署仍应替换为共享数据库。

`OpenAIStageGenerator` 使用官方 Responses API 的严格 JSON Schema 结构化输出。模型结果仍会经过本地校验：想法探索必须逐项复用本轮课程/补充检索结果并保留课程范围映射和来源，课程映射及理论依据中的公式必须来自已核对目录；预期数据可视化不得伪装成实测数据；引导状态的学生总结不得代写最终方案；EMVR概念结构不得加入场景、舒适性或可访问性字段。阶段推进仍只由 `WorkflowEngine` 控制。

阶段1会在后端持久保存 `topic_anchor`、`current_focus`、`focus_history`、已展示图景签名和当前待明确内容。网页按钮携带稳定 `option_id`，由确定性程序处理；学生键入的序号、指代、补充、拒绝、换例子或换题要求则统一交给结合上一问、待办和完整设计状态的语义解析器，不用关键词表猜测。明显的代码执行、提示注入和用途劫持只由安全规则拒绝。在线模型暂时不可用时，离线回退仅保守地把有实质内容的长回复绑定到当前开放问题，不推断“保留、继续、修改”等上下文决定。学生选定方向后，`alternative_ideas` 会变为空数组；后续请求参考只围绕已锁定方向展开，不会再次播放三幅图景。

内置 `InMemorySessionStore` 会在进程重启后清空会话。生产配置使用 `ECE329_DATABASE_PATH` 启用SQLite，并已对同一 `design_id` 串行处理、使用乐观版本检查和持久化 `turn_id` 响应缓存；这适合Render上的单实例服务。若以后扩展到多个后端实例，应改用共享数据库和跨实例锁。创建接口的 `Idempotency-Key` 缓存是单进程缓存，主要覆盖浏览器超时重试；设计创建成功后的每轮 `turn_id` 幂等记录则随设计会话持久化。
