# 反馈 API 切换与项目审阅（2026-09-19）

本轮对项目运行全量回归、文件引用和语法检查，重点审阅新增的反馈分析 API 切换、独立分析额度、错误分类，以及前后端重试线路。未提交、推送或部署到生产。

## 本轮发现并修复

| 触发条件 | 原问题 | 修复 |
| --- | --- | --- |
| 面板选中反馈 A，刷新时 A 已被处理，但 B 仍可重试 | 选项自动跳到 B，下一次点击可能分析错误记录 | 保留 A 的不可用选项，禁用提交并要求重新选择；切换设计才清除旧选择 |
| 显式模型已排队，重启后默认模型或允许列表排序变化 | worker 把每服务商的代表模型当成完整可用列表，误拒仍可用的原选择 | 分开计算完整可用列表与自动切换代表；显式选择按完整列表重新校验，保留用户选择 |
| 主模型被移出允许列表，或其 transport 不可用 | 原实现仍可能展示、选择该主模型 | 仅展示和调用仍允许且已配置的模型；自动路线每服务商仍最多一个模型 |
| 所有分析模型均不可用 | 空路线可能被当成没有可提炼经验 | 记录配置失败并结束本次任务，不调用模型、不误记 no_learning、不自动空转 |

修改见 [反馈后台](../src/ece329_workflow/experience.py)、[反馈面板](assets/feedback-ui.js)。新增回归见 [后台测试](../tests/test_feedback_manual_retry.py)、[前端测试](../tests/frontend/feedback.test.cjs) 和 [浏览器验证](../tools/verify_feedback_switch_browser.cjs)。更新了前端资源版本号，避免继续读取旧缓存。

## 同类问题与循环边界

- 初次分析、普通重试、显式 API 重试、自动备用调用和租约恢复共用十次总额度；不会通过切换 API 重置计数。
- 显式指定模型只调用该模型；被禁用后记录失败，不擅自替换。普通重试才采用默认的有限备用路线。
- 每次尝试最多生成草案、检查草案两次调用。输出无效停止，不递归修复；检查不通过记为 no_learning，不能通过换模型绕过审阅。
- 普通连接或 HTTP 故障最多尝试每个已配置服务商的一个模型。无工作时退出；十次耗尽、旧 worker 迟到、持续存储故障均有终止或写入保护。
- 前端双击合并、跨设计迟到响应隔离，运行中的记录不可重复排队；自动刷新最多连续 60 次，关闭窗口停止。
- 反馈分析独立于设计 turn，两个 mode 共用，不改变对话模型、设计版本或阶段。

这些修复覆盖选择失效、后台配置漂移和重试终止这一类问题。不能据此保证任意线上模型的经验归纳质量，也不能用本地测试确认历史生产记录的具体失败原因。

## 验证

- 完成修复后的全量回归：1332 passed，210 subtests passed，175.39 秒；包含状态机、防循环、模型路由、知识库、反馈审阅、安全、持久化及 Builder/PDF 的既有回归。

- 前端 Node：41 项通过，覆盖反馈、维护者审阅、模型选择及配置恢复。
- 本机 Edge：两个 mode 均完成“OpenAI 输出无效 → 用户选择 DeepSeek → 生成候选或识别重复”，次数为 2/10，设计版本不变；确认已完成记录保留原选择且禁止再提交；手机宽度无横向溢出，中英文提示通过。
- 106 个 Python 文件语法、9 个知识 JSON、10 个 JavaScript 文件语法检查通过。
- 82 个本地 HTML/Markdown 引用、249 个 Python 相对导入检查通过；git diff --check 通过。

浏览器测试使用本机 HTTP、SQLite 和确定性的模型替身，未调用真实付费 API，未访问生产反馈数据库。本轮未运行 Docker 构建。

## 部署和 CMD 推送

无需新增必填变量。上一轮新增的可选反馈配置有默认值，可按需要在后端设置：

```dotenv
ECE329_FEEDBACK_REASONING_EFFORT=low
ECE329_FEEDBACK_MAX_OUTPUT_TOKENS=8192
ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS=4096
```

切换服务商仍要求配置对应 API 密钥，且 ECE329_ALLOWED_MODELS 包含实际可用模型。前后端应部署同一提交并重启后端；保留持久化数据库，无需清空或重复提交反馈。故障分类与配置说明见[反馈层说明](feedback-layer.md)。

已确认当前分支为 main、origin 指向本项目 GitHub 仓库。CMD 执行：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git add src tests docs tools .env.example
git diff --cached --stat
git commit -m "Fix feedback API selection and bounded retry recovery"
git pull --rebase origin main
git push origin main
```

上述暂存范围不包含无关的根目录未跟踪文件。若 rebase 提示冲突，先解决冲突并完成 rebase，再执行 push，不使用强制推送。推送后检查 GitHub Actions；GitHub Pages 发布前端，后端需另行部署对应提交。
