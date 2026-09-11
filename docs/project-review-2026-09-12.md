# 模型选择、反馈恢复与 Builder 交付复核

本页记录此前审阅；后续 DeepSeek、双模式并发及 v9 初始化复核见[本轮补充审阅](project-review-2026-09-12-final.md)。

本次检查覆盖前端请求、API、会话及模型恢复、反馈队列/经验审阅、两个 mode 的阶段推进、引用校验与 PDF 导出。重点检查最近的模型选择和反馈线路；保留既有未提交修改。

## 发现与修复

| 触发条件 | 原问题 | 修复及回归 |
| --- | --- | --- |
| 已选在线模型的会话遇到规则模式部署 | 后端持续拒绝旧模型，页面也无法清除选择 | 未显式指定模型时允许继续；成功处理时清除远端链及失效选择，保留历史/实验。两个 mode 测试通过，显式在线请求仍拒绝。 |
| 远端响应链失效，或结构化回复需要修复 | 新请求移除了 response ID，却没有补回原先省略的本地历史 | 两条恢复路径统一携带本地历史，保留已有有限重试，避免重复追问已答内容。 |
| 提交反馈时旧列表请求尚未返回 | 保存成功后刷新被忽略，旧空列表覆盖记录且停止轮询 | 合并一次待执行刷新，旧请求完成后读取新列表；关闭对话框仍停止轮询。 |
| 反馈后台持续遇到数据库异常 | 每两秒无限循环 | 连续三次存储异常退出，保留队列和租约；修复存储后由后续反馈请求或服务重启恢复。 |
| 较长实施说明中的路径正好跨过第 900 字符 | 合法路径被拆成 `/Scenes/...` 等片段，边界检查误判包外引用，最终交付反复失败 | 保留语义条目完整，由 PDF 段落测量负责分页并重复字段 ID；五组不同长度回归保留路径，真实包外引用仍拒绝。电场完整流程重新通过。 |
| 原创新实验按实施说明初始化 | 盲重建复制步骤与主包说明冲突，工具默认 mode 又是 blind-rebuild | 明确新实验留在主包，显式传 `--mode integrated-development`；只有用户明确要求既有 Lab 盲重建才适用复制说明。 |
| 快照比较或恢复失败 | “兼容”缺少可执行判断，部分恢复后的状态不明确 | 增加版本、受控条件、单位及对象校验；允许自变量不同，恢复失败不部分覆盖，提交失败回滚，回填只重算一次。 |

同时修正服务连接失败时错误声称已切换本地示例的提示，更新前端资源版本号。模型白名单、请求幂等、跨设计迟到回执和经验人工审阅边界继续保留。

## Builder PDF 能否作为从零构建输入

在已有完整本机 `EMVR_Blind_BuilderPack` 的前提下，当前 PDF 提供创建新实验所需的设计和实施说明：宿主初始化、五份合同映射、脚本/程序集与 Editor 入口、场景生成、Common 引用、房间和照明、物理模型及数值边界、桌面/XR 动作、快照生命周期、证据与验收。

本次读取核对了包内 15 个关键文件，包括锁定 Editor、依赖清单、房间及照明配方、Prefab、Common 实际接口和初始化工具；本地 `file:` 依赖均在包内。PDF 只保留相对路径和本机定位规则。新增初始化说明依据实际工具参数，避免靠默认模式猜测。

实施契约升级到 `builder-ui-flow-v8`。已有批准不会自动覆盖新增要求；旧会话需要审阅一次更新后的实施方案。回归确认重新批准后有效，单纯追加元问题历史不会再次使批准失效。

实际生成并检查了 23 页 Builder PDF 和 16 页学生 PDF。检查包括全部页面渲染、字段值、路径边界、公式/方法/步骤/对象引用。合成样例没有空 value、绝对路径或越界文字；运行时核对项保留相应状态，不伪造本机执行结果。六个圆环轴线独立数值基准通过，源离散分别采用 0.01 m 和 0.005 m。

这些是合成测试报告。未调用真实付费模型，也未在 Unity Editor/XR 中编译运行，不能把设计交付完整性当作 Unity 实机验收。PDF 依赖包内已有的宿主、获准资产与接口；不会自动成为可运行 Unity 工程，也不代表实际用户批准任何 Builder Gate。

## 验证与复现

- 后端全量：1066 passed，210 subtests passed。
- 前端：14 项通过，包含反馈并发刷新和模型选择/请求冻结。
- 浏览器：两个 mode 的三个模型切换、刷新恢复、真实 SQLite 反馈回执、维护者权限、启用/停用及移动布局通过；模型使用测试替身。
- 本地 HTML 资源和 README/docs 文件链接检查未发现断联。

在已安装项目及测试依赖的 CMD 中执行：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
set PYTHONPATH=src
python -m pytest -q
node --test tests/frontend/feedback.test.cjs
node --test tests/frontend/model-selection.test.cjs
python -m tools.export_project_review
```

导出位置为 `output/pdf/review-20260912-builder.pdf` 与 `output/pdf/review-20260912-student.pdf`，结构化输入和数值结果在 `build/review-20260912/`。这些是被 Git 忽略的生成目录；复现脚本会上传。

## 用 CMD 推送当前修改

已核对当前分支为 `main`，`origin` 指向 `https://github.com/yzhao05/ece329-lab-design-workflow.git`。下列操作提交并推送工作流代码、前端、配置示例、文档和测试，包括此前模型选择和反馈线路的新文件：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git diff --check
git add .env.example .github README.md pyproject.toml src tests tools docs
git diff --cached --stat
git commit -m "Fix workflow recovery and complete Unity Builder handoff"
git pull --rebase origin main
git push origin main
```

如 rebase 出现冲突，解决对应文件后 `git add 文件路径`，再 `git rebase --continue`；完成后再 push，不使用强制推送。本次审阅没有代为 commit 或 push。后端上线仍需部署新代码并重启服务，保留持久数据库及既有模型白名单、密钥和维护者令牌配置；GitHub push 本身不等于后端更新。
