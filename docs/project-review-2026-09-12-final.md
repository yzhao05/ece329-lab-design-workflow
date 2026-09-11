# 双模式、模型反馈线路与 Unity 交付复核

本次审阅覆盖前端、API、会话存储、模型路由/DeepSeek、反馈经验、阶段推进、Builder 引用校验、PDF 和部署配置，重点检查最近的修改。保留此前全部未提交工作，没有替用户 commit 或 push。

## 本轮修复

| 问题与触发条件 | 修复 | 验证范围 |
| --- | --- | --- |
| 一个工作进程生成回复时，另一个保存模型或经验开关；配置更新不增加实验 revision，旧会话仍能覆盖新配置 | 保存时原子比较已加载的完整持久化快照及 revision；冲突返回 409。快照令牌不进入公开 JSON，不增加数据库字段 | 内存存储、两个独立 SQLite Store、两个 mode；冲突后显式重试保留新模型和经验开关 |
| 前端收到冲突后，虽然刷新设计，却仍使用冻结的旧模型配置重试；同步失败也声称成功 | 冲突重试改用同步后的设置，保留用户另外选定的下一条设置；同步失败保留待办，快捷重试和普通发送都先同步，不提交旧配置 | 两个 mode、网络失败、跨设计迟到结果；普通网络超时仍保持原请求幂等 |
| 经验检索只扫描最新 500 条，旧的相关规则被较新的无关规则挤掉 | 先按作用域、mode、stage 过滤，流式计算相关性，仅保留最高三条，不在相关性排序前截断候选集 | 分别加入 501 条 mode、stage 或关键词不匹配的新经验，两个 mode 均仍找到旧规则 |
| DeepSeek 返回 NaN/Infinity/数值溢出，或 usage 是错误类型 | 在结构化输出校验中拒绝非有限数值，进入已有有限修复路径；错误的可选 usage 记为未知，不丢弃有效内容 | 非标准常量、1e999、缺失/列表/字符串/数字 usage；既有单次修复与遥测回归 |
| Builder 知道要接线，但没有明确初始化先后关系 | 增加 Awake/OnEnable/Start 分工、唯一幂等初始化入口、就绪门控、10 秒默认等待上限、取消与解除订阅、重复启动和域重载验收 | 实施契约升级到 v9，v8 批准失效；审阅新契约后恢复有效，元问题不重新作废批准 |
| 先前 CMD 提交示例遗漏 pyproject.toml | 将依赖清单加入提交命令，避免只上传 DeepSeek 适配器却遗漏 jsonschema 依赖 | 核对生产 Docker 安装路径及 CI 安装项目依赖的步骤 |

修复针对并发状态覆盖、相关规则检索遗漏、异常响应解析和初始化等待这几类触发条件。既有“已答问题不重问、先处理课内要求和元问题、跨阶段修改、阶段完成门控、有限后台重试”的回归继续保留；不能据此承诺任意模型输出都绝无错误。

## PDF 能否从头构建 Unity 实验

在本机具有完整 `EMVR_Blind_BuilderPack` 的前提下，当前 Builder PDF 可作为 Builder 编写新实验代码、生成场景和执行验收的输入。它提供包根定位、锁定宿主、初始化工具和模式、五份合同、对象/步骤 ID、参数和公式、数值算法、资产与 Common 连接、桌面/XR 输入、快照/Reset/Restore、工程生成顺序、生命周期及证据要求。

新增初始化章节解决“接线齐全却因组件初始化顺序或重复启动而失效”的缺口。`InitializeOnce` 明确标为待实现的领域入口，不冒充 Common API。既有批准需要审阅 v9 一次；不会把新增要求悄悄写进已批准 PDF。

本轮只读核对包内宿主版本文件、依赖清单及实际 Runner/SnapshotStore 接口；宿主当前锁定 `2022.3.62f3c1`，本地 `file:` 依赖存在并仍在包内。PDF 继续让 Builder 读取其本机锁定版本，未写入本机绝对路径。外部材料仍需内嵌后才能成为交付内容，包外引用仍受原边界校验约束。

通过当前生产导出函数生成了四份合成 QA 文件：

| 样例 | Builder | 学生报告 | 生成位置（仓库相对路径） |
| --- | --- | --- | --- |
| 圆环磁场 | 24 页 | 17 页 | `output/pdf/review-20260912-builder.pdf`、`output/pdf/review-20260912-student.pdf` |
| 双点电荷 | 23 页 | 16 页 | `output/pdf/electric-review/emvr-builder-smoke.pdf`、`output/pdf/electric-review/emvr-design-smoke.pdf` |

80 页均用 Poppler 渲染并检查版面，另检查页面文字边界、Builder 字段 value、路径和新增初始化内容。未发现空 value、绝对路径或越页文字。电场样例 35 轮完成；磁场的 6 个独立轴线数值基准通过。公式、方法、对象、步骤及越界引用由导出验证器和相关回归检查。

这些是合成测试，不是用户真实批准记录。PDF 是实施与验收规格，需要 Builder 实际写 C#、生成场景并运行检查；并非可直接导入即运行的 Unity 工程。本次没有调用真实付费模型，没有在 Unity Editor 或 XR 设备中编译运行，因此不宣称 Unity 实机验收完成。

## 回归与复现

后端全量：1141 passed，210 subtests passed，耗时 414.38 秒。包括此前阶段恢复、用户请求优先、跨阶段修改、Builder 边界和本轮新增回归。

- 前端 Node：24 项通过，包含冲突同步、冻结请求、反馈并发刷新与迟到回执。
- 浏览器：两个 mode 在 320、390、768、1024、1440 像素宽度下通过对齐和自适应检查；GPT/DeepSeek 切换、实际 SQLite 反馈回执、后台提炼、维护者批准、提示注入及停用撤回通过。模型为测试替身。
- 本地 HTML/Markdown 资源链接：45 个通过；Python 编译检查通过。

使用已安装 Python 和 Node 的 CMD 复现：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
python -m pip install -e ".[test]"
python -m pytest -q
node --test tests/frontend/feedback.test.cjs
node --test tests/frontend/model-selection.test.cjs
python -m tools.export_project_review
python -m tools.export_emvr_smoke_pdfs --output-dir output/pdf/electric-review
```

生成的 PDF、数据库和测试图片均位于 Git 忽略目录；提交的是可复现脚本和测试。

## 用 CMD push 到 GitHub

已核对分支 `main`，远端 `origin` 为 `https://github.com/yzhao05/ece329-lab-design-workflow.git`。

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git diff --check
git add .env.example .github README.md pyproject.toml src tests tools docs
git diff --cached --stat
git commit -m "Fix model conflicts, feedback retrieval and Unity builder lifecycle"
git pull --rebase origin main
git push origin main
```

逐条执行，上一条失败先处理。如果 rebase 冲突，解决文件后执行 `git add 文件路径`、`git rebase --continue`，结束后再 push；不要强制推送。提交命令包含此前模型选择、DeepSeek、反馈功能以及本轮修复的全部代码和依赖清单。

GitHub push 不等于后端自动更新：后端需重新部署并安装新依赖、重启服务，保留持久数据库和原有 API 密钥/维护者令牌。前端资源版本已更新，发布后加载新页面即可获得冲突恢复逻辑。本轮没有增加必填环境变量，也没有改变数据库表结构。
