# ZJUI ECE329 实验设计工作流

这是一个面向学生的实验**设计**工作流。它帮助学生把模糊想法发展成 ECE329 Lab Design Proposal；它不要求真实搭建实验，也不生成伪造的实测数据。

项目提供零外部依赖的 Python 状态机和 HTTP API，可以直接运行。后端已经接入 OpenAI Responses API；未配置密钥或模型服务临时失败时，会使用讲义约束的本地规则生成器。

## GitHub Pages网页

`docs/` 中包含一个可直接发布到 GitHub Pages 的静态网页，包括：

- 响应式聊天界面与13阶段进度；
- GUIDED／EMVR状态展示；
- lecture note概念、公式和页码依据面板；
- Stage 10理论预测曲线与参数滑块；
- 浏览器本地会话保存；
- 空API配置和未来后端适配层。

当前 `docs/assets/config.js` 中的 `API_BASE_URL` 特意保持为空，因此网站会明确显示“演示模式”。演示回答来自浏览器本地规则，不会伪装成真实Agent。API上线后只需填写后端HTTPS地址。

网页接入兼容后端时还会执行以下保护：

- 学生明确确认后，GUIDED请求自动生成 `complete_stage` 及阶段1／阶段13所需的 `context_patch`；
- API健康检查或请求失败时，界面与实际行为都会切换到明确标识的本地演示模式；
- 后端会话失效或设计令牌不匹配时，网页会清除旧会话并要求重新开始，不会把“继续”伪装成新设计；
- 若后端启用课程访问码，网页会在首次创建设计时询问，访问码和设计令牌只保存在当前标签页的 `sessionStorage`；
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

- `GUIDED_DESIGN`：默认状态。重点停留在阶段1进行 brainstorming；系统每次只给一个引导任务。阶段13由学生自己总结，系统不代写最终方案。
- `EMVR_DIRECT`：用户明确提到 `EMVR` 时触发。系统每次直接完善一个阶段，并把设计面向 Unity VR 模拟实验展开。

两种状态都遵守同一条硬规则：**一次 API 响应只处理一个阶段**。阶段推进由状态机控制，内容生成器不能跳阶段。

## ECE329知识来源与约束

`ece329lecture_notes.pdf`（Erhan Kudeki，324页，39讲）定义ECE329课程范围，但不是阶段1的唯一参考答案。工作流还使用三份经过核对的补充资料扩展课程相关的概念关系、应用和例子：Jin Au Kong 的 *Electromagnetic Wave Theory*、David H. Staelin 的 *Electromagnetics and Applications*，以及 N. Narayana Rao 的 *Fundamentals of Electromagnetics for Electrical and Computer Engineering*。

- 阶段1先帮助学生探索“当前宽泛主题与哪些现象或概念有关”，可以使用课程讲义或补充资料中提到、学习或直接相关的关系示例，并允许学生提出自己的关联；此时不要求确定变量、公式、研究问题或实验结构。
- 阶段1面向学生统一使用“ECE329课上所学概念”的表述，并按请求的实际意图分为三类：ECE329课程内容正常进行关系探索；正常但不属于课程范围的主题会被说明课程边界；试图控制或关闭课程助手、改变内部规则、执行代码/脚本/命令、借外部平台改变输出或绕开课程用途的请求会被明确拒绝。后两类都会回到静电场、磁场与感应、电磁波与传输线三个课程关系示例。类别按意图判断，文档中的行为示例不是限定关键词清单。
- 学生可见回答不会显示知识检索字段、内部规则、提示词、PDF页码或前后端部署术语；来源和页码只保留在依据面板及结构化记录中供核查。
- 每个补充概念都必须通过 `course_scope_concept_ids` 映射回ECE329课程范围，并返回 `supplemental_concept_id`、资料章节和PDF页码。课程目录和补充目录都没有具体命中时，才回退到讲义第10—12页的 Electrostatics、Magnetism、Electromagnetics 三个板块。
- 阶段2的课程映射和阶段5的理论公式必须来自知识目录，并随结果返回 `concept_id`／公式 `id` 和 PDF 页码。
- 不凭模型记忆补充课程主题、公式、课程要求或实验条件。补充资料用于概念与关系检索，不会自动授权模型生成未核对的公式。
- 讲义、教材及其提取文本只被视为参考数据，其中的文字不会覆盖工作流规则或作为系统指令执行。
- 讲义第10—12页把 radiation and antennas、dispersion in material media 标为未覆盖或仅略微覆盖；工作流不会主动把它们推荐为核心方向。

运行时知识文件位于 `src/ece329_workflow/knowledge/`：`concepts.json` 收录39讲的课程范围，`formulas.json` 收录82条核心公式，`source_manifest.json` 固定讲义身份，`supplemental_sources.json` 收录补充来源、概念摘要、关系示例、课程映射和PDF页码。大型来源PDF不提交到GitHub。详细目录见 `knowledge/README.md`。

## 13个阶段

1. 实验想法探索与完善
2. ECE329课程映射与实验方向
3. 学习目标
4. 研究问题
5. 理论框架
6. 假设与预期趋势
7. 概念实验结构／Unity VR模拟设计
8. 变量与条件
9. 概念实验流程
10. 预期数据可视化窗口
11. 可能结果及解释
12. 设计价值、可行性与局限性
13. 学生总结／EMVR最终方案

阶段1允许在同一阶段内多轮发散、比较、组合和完善。引导状态只有在学生确认现象与主要方向后才能离开阶段1。

阶段7在 EMVR 状态下只完善用户已有设计的任务、Unity对象、交互、物理计算、可视化、反馈界面和模拟内部状态：

- 不替用户定义VR场景；
- 不包含可访问性与舒适性设计；
- 不默认生成Unity代码；
- 区分理论计算结果和教学示意动画。

阶段10返回可供前端渲染的 `visualization` 对象。所有数据必须标记为 `theoretical_prediction` 或 `illustrative_synthetic_data`，并且 `measured=false`。

## 与 EMVR Blind Builder Pack 的关系

本项目只参考同一研究工作区中的 `EMVR_Blind_BuilderPack` 当前接口和单阶段工作原则，不读取其他 EMVR 项目作为设计依据。

本工作流是 Builder Pack 之前的设计前端：

- 不运行 Builder Pack 的 Gate；
- 不创建 Unity 场景或代码；
- 不修改 Builder Pack；
- 不替用户批准任何 Gate；
- EMVR 最终输出只提供可供 Brief/Design 人工审阅的 `builder_pack_handoff`。

真正进入 Builder Pack 后，房间/XR Prefab复用、Common API审计、Unity编译、测试和验收仍应按照 Builder Pack 自身规则完成。

## 快速运行

不安装任何依赖即可启动开发服务器：

```bat
set PYTHONPATH=src
python -m ece329_workflow --host 127.0.0.1 --port 8080
```

健康检查：

```text
GET http://127.0.0.1:8080/health
```

返回的 `generator.provider` 表示当前生成器：`rule_based` 表示尚未配置模型；`openai` 表示已启用模型。它只显示模型名和是否允许回退，不显示密钥。
`storage.provider` 表示会话存储；本地默认是 `memory`。设置 `ECE329_DATABASE_PATH` 后会使用 `sqlite`；只有云平台把该路径挂载到持久化磁盘时，容器重建后数据才会保留。

## 接入OpenAI API（CMD）

API密钥必须只放在运行后端的服务器环境变量中，不能写入 `docs/`、`config.js`、Git提交或浏览器请求。先在 OpenAI Platform 创建密钥，然后在同一个CMD窗口执行：

```bat
set OPENAI_API_KEY=在这里粘贴你的密钥
set ECE329_GENERATOR=auto
set OPENAI_MODEL=gpt-5.4-mini
set PYTHONPATH=src
python -m ece329_workflow --host 127.0.0.1 --port 8080
```

`ECE329_GENERATOR=auto` 是推荐设置：有 `OPENAI_API_KEY` 时使用OpenAI，无密钥时使用本地规则。默认还会在超时、网络错误或模型输出不符合工作流约束时自动回退，并在本轮响应的 `warnings` 中明确说明。

可选服务器环境变量：

- `OPENAI_MODEL`：模型ID，默认 `gpt-5.4-mini`；
- `OPENAI_TIMEOUT_SECONDS`：请求超时，默认45秒；
- `OPENAI_MAX_OUTPUT_TOKENS`：单轮最大输出，默认2400；
- `ECE329_OPENAI_FALLBACK`：默认 `true`。设为 `false` 后，模型失败会返回HTTP 502；
- `ECE329_GENERATOR=rule`：强制使用本地规则生成器；
- `ECE329_GENERATOR=openai`：强制要求密钥，缺少密钥时后端拒绝启动。
- `ECE329_ACCESS_CODE`：公开部署时强烈建议设置的课程访问码；它保护创建设计这一会产生模型费用的入口。
- `ECE329_MAX_TEXT_CHARS`：单条学生输入的最大字符数，默认4000，用于限制异常请求和模型费用。

启动后在浏览器访问 `http://127.0.0.1:8080/health`。确认 `generator.provider` 为 `openai` 后，使用下一节的GitHub Repository Variable连接公开后端；不需要修改仓库中的 `config.js`。GitHub Pages只能连接可公开访问的HTTPS后端，不能直接连接你电脑上的 `127.0.0.1`。

## 公开部署

项目根目录现在包含生产用 `Dockerfile`、无秘密的 `.env.example`、可选SQLite持久化、指定来源CORS、请求体限制和按客户端地址计算的基础POST限流。完整流程见 [DEPLOYMENT.md](DEPLOYMENT.md)。

GitHub Pages推荐使用Repository Variable `ECE329_API_BASE_URL`。Pages Action会在上传产物前注入公开后端URL，仓库中的 `docs/assets/config.js` 仍保持空白。后端则必须设置：

```text
ECE329_ALLOWED_ORIGINS=https://你的用户名.github.io
ECE329_DATABASE_PATH=/data/ece329.sqlite3
ECE329_ACCESS_CODE=请使用独立生成且不提交到Git的访问码
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

响应只处理阶段1，并保持在阶段1等待学生继续 brainstorm。响应中的 `design_access_token` 仅返回一次；后续设计路由必须使用 `Authorization: Bearer <design_access_token>`。

### 确认阶段1并进入阶段2

```http
POST /v1/designs/{design_id}/turns
Content-Type: application/json
Authorization: Bearer <design_access_token>

{
  "message": "我决定研究网格尺寸对屏蔽效果的影响",
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

这条响应仍只处理阶段1；响应结束后会把会话指针移到阶段2。下一次请求才会处理阶段2。

### 创建EMVR设计

```http
POST /v1/designs
Content-Type: application/json

{
  "idea": "请把偏振实验放入EMVR工作流中完成"
}
```

显式出现 `EMVR` 会切换为 `EMVR_DIRECT`。当前阶段直接完成并自动移动会话指针，但响应中不会生成下一阶段内容。

### 继续当前设计

```http
POST /v1/designs/{design_id}/turns
Content-Type: application/json

{
  "message": "继续"
}
```

### 获取当前设计状态

```text
GET /v1/designs/{design_id}                         (需要Bearer令牌)
GET /v1/designs/{design_id}?include_history=true    (需要Bearer令牌)
```

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

### 获取大模型提示包

```http
POST /v1/designs/{design_id}/prompt
Content-Type: application/json

{
  "message": "学生本轮输入"
}
```

该接口返回当前阶段、全局硬规则、设计上下文和JSON响应契约。部署端可将其发送给任意模型，再通过自定义 `StageGenerator` 接入工作流。阶段推进始终保留在 `WorkflowEngine` 中。

## 引导状态阶段1完成条件

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

缺少任一项时，即使请求 `complete_stage=true`，工作流仍停留在阶段1。

## 引导状态阶段13

阶段13不会输出完整 Proposal。系统逐部分要求学生总结，并只对学生草稿提供当前部分的反馈。全部总结由学生完成后，客户端可写入：

```json
{
  "synthesis": {
    "student_summary": "学生自己完成的实验设计总结（至少20个字符）",
    "student_summary_complete": true
  }
}
```

这只会标记工作流完成，不会让系统补写最终方案。

## 测试

```bat
set PYTHONPATH=src
python -m unittest discover -s tests -v
```

测试覆盖：

- 引导状态默认停留在阶段1；
- 阶段1必须由学生确认后才能推进；
- 明确提到EMVR才切换直接状态；
- 每次只处理一个阶段；
- 阶段7不定义VR场景和可访问性；
- 阶段10只输出理论预测；
- 阶段13引导状态不生成最终方案；
- HTTP健康检查。
- 39讲概念与82条公式目录的内部引用有效性；
- 阶段1方向带有课程范围映射及讲义/补充资料页码，阶段5公式带有已核对目录页码；
- 未知想法只回退到讲义总览板块；
- 课外主题会被明确标注，并返回三个ECE329课内示例；控制助手、代码注入、内部机制、外部平台改写输出和角色扮演等不合理请求会被拒绝且不会泄露项目术语；
- 多来源知识搜索接口。

## 更新知识目录

`tools/extract_lecture_notes.py` 用于生成带 PDF 页码的原始文本索引，`tools/inspect_lecture_notes.py` 用于辅助检查讲次、标题和公式候选。它们不在 API 运行时执行。

如果课程讲义发生变化，应重新提取并人工核对公式符号与页码。如果补充资料发生变化，应更新 `supplemental_sources.json` 中的来源哈希、概念摘要、课程映射和页码，然后运行完整测试。不要在未核对的情况下让大模型自动扩写公式目录。

## 生产部署注意事项

内置 `InMemorySessionStore` 适合本地开发；设置 `ECE329_DATABASE_PATH` 后会启用SQLite和乐观版本检查。SQLite适合单服务实例，多实例部署仍应替换为共享数据库。

`OpenAIStageGenerator` 使用官方 Responses API 的严格 JSON Schema 结构化输出。模型结果仍会经过本地校验：阶段1方向必须逐项复用本轮课程/补充检索结果并保留课程范围映射和来源，阶段2课程映射及阶段5公式必须来自已核对目录；阶段10不得伪装成实测数据；引导状态阶段13不得代写最终方案；EMVR阶段7不得加入场景、舒适性或可访问性字段。阶段推进仍只由 `WorkflowEngine` 控制。

内置 `InMemorySessionStore` 会在进程重启后清空会话。正式部署前仍应替换为持久化数据库，并为同一 `design_id` 的阶段推进增加事务或乐观锁。
