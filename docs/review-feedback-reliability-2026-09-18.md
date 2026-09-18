# 反馈可靠性与项目回归审阅（2026-09-18）

本轮检查项目引用、前后端接口、状态机相关回归和部署入口，重点复核反馈收件箱、跨服务商分析与十次尝试上限。保留此前未提交修改，没有执行 commit、push 或生产部署。

## 发现并修复

| 触发条件 | 问题 | 修复与验证 |
| --- | --- | --- |
| API 连接被重置、远端断开，或读取响应时数据传输中断 | 底层 ConnectionResetError、RemoteDisconnected、IncompleteRead 等可能绕过连接错误分类，反馈分析记录内部错误而未尝试备用服务商 | 共享 HTTP transport 将 OSError/HTTPException 归为 ModelConnectionError；通过实际 transport 入口模拟连接及读取阶段的故障，验证备用服务商成功且总计数为 2 |
| HTTP 503 等错误的响应正文读取也失败 | 原始 HTTP 状态被正文异常遮蔽，诊断与切换行为不完整 | 正文读取失败仍保留 HTTP 状态，关闭响应资源；无效 UTF-8 归入输出错误，不误报内部错误 |
| 多个 WSGI 进程同时读取收件箱和保存反馈 | 各查询独立读取，计数、当前页及关联经验可能来自不同时刻 | 在同一只读事务中读取；用两个独立 SQLite Store 和 WAL 写入复现交错，确认本页与下次刷新各自保持一致 |

实现见 [HTTP transport](../src/ece329_workflow/openai_generator.py) 和 [反馈队列与收件箱](../src/ece329_workflow/experience.py)；新增故障与并发回归见 [测试](../tests/test_feedback_transport_recovery.py)。

## 重试及状态边界

- 首次分析、人工重试、服务商切换和租约回收共用每条反馈十次上限。第十次失败后不再调用备用 API，也不能再次排队。
- 一次任务只尝试主服务商及另一已配置服务商的一个允许模型，不在多个模型间反复轮换。未配置或不在允许列表内的服务商不会被调用。
- 输出解析失败可由人显式重试；证据检查不通过保持 no_learning，不通过换模型寻找同意答案。模型检查不冒充真实回放，候选仍须人工审阅。
- 删除任务或租约被其他 worker 取得后，旧 worker 不能写回诊断、预留备用次数或覆盖结果。耗尽次数的异常排队任务终止为 failed，后台连续存储故障仍有退出上限。
- 维护者双击重试只发出一个请求；清除令牌后，迟到响应不重新展示记录。反馈及重试不调用设计 turn，不推进阶段，两个 mode 共用这条线路。

这些验证覆盖连接故障分类、并发读取、有限重试及迟到结果这一类问题，不能代替对任意线上模型输出的质量审阅。

## 验证结果

- 全量回归：1297 passed，210 subtests passed，513.97 秒。该运行在开始审阅时收集用例，包含状态机、模型路由、知识库、反馈、Builder/PDF 和既有防循环回归。
- 完成代码修复后专项重跑：262 passed，2 subtests passed；包含新增的 11 个 transport/并发用例及反馈、OpenAI/DeepSeek、审阅、安全和持久化测试。
- 前端 Node：35 项通过；所有 docs/assets JavaScript 语法检查通过。
- 浏览器：两个 mode 的 SQLite 反馈提交、十次额度显示、全部反馈列表、审阅启用/停用、未授权访问拒绝、移动端反馈窗口通过。
- 105 个 Python 文件语法及 9 个知识 JSON 文件解析通过。本地 HTML/Markdown 文件引用及 Python 相对导入无断联；CI 和 Pages 继续运行后端及前端测试。
- git diff --check 通过。Docker 入口和打包文件进行了静态检查，本轮未执行 Docker 构建。

浏览器和故障测试使用本机服务与模型替身，未调用真实付费 API，也未访问生产反馈数据库。具体历史反馈的上游错误需在部署后重试，根据新增诊断确认。

## 部署与 CMD 推送

当前分支 main，origin 为本项目 GitHub 仓库。以下命令包含新增测试和环境变量模板，避开无关的根目录未跟踪文件：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git add src tests docs tools .env.example
git diff --cached --stat
git commit -m "Harden feedback inbox and bounded provider failover"
git pull --rebase origin main
git push origin main
```

推送后查看 GitHub Actions。Pages 工作流只发布前端；后端还需在其托管服务部署对应提交并重启。保留现有持久化数据库，无需清库或重复提交反馈，也无新增必填环境变量。两个服务商的密钥和允许模型配置决定是否可跨服务商切换，详见[反馈层说明](feedback-layer.md)。
