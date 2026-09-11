# 思考强度、预算与双语链路审阅（2026-09-12）

本次检查前后端调用链、模型设置、累计预算、自动升级、翻译、反馈、状态恢复、仓库引用及项目回归。重点修复以下可重复出现的问题，而非针对单条会话增加例外。

| 问题 | 原因与修复 | 回归证据 |
| --- | --- | --- |
| 长页面重复翻译 | 共用缓存仅保留 2000 条，淘汰仍在页面上的译文后，DOM 扫描会不断重新排队。现让存活节点保留已完成的语言副本，不依赖共用缓存长期留存。 | 2050 段文本、10 个标题属性，86 批完成，每段只请求一次；切换语言不再次请求。 |
| 自动升级忽略目标模型偏好 | 修复重试另走一条路由路径，未应用目标模型保存的思考强度。现统一完成路由、强度和预算计算，并同步当前强度与路由状态。 | 目标模型 none/max 均实际进入调用；升级前后继续共享同一整轮预算。 |
| 正常回复被异常用量字段破坏 | 可选 usage 或其子字段不是对象时，遥测代码抛异常。现保留回复，将不可信用量记为未知并保守扣除预算。 | null、布尔、数字、字符串、数组与异常子字段回归通过。 |
| 翻译副本出现引用或数值错误 | 原检查主要覆盖对象/步骤 ID，未充分检查路径、代码、公式片段与数值。现校验这些字面值及数量，数值包括极性与科学计数法；错误译文不能进入缓存或导出。 | 路径改名、代码名称变化、公式指数变化、数值变更和电荷极性反转均被拒绝；合法重排通过。 |

代码位于 [翻译界面](assets/i18n.js)、[模型路由](../src/ece329_workflow/model_routing.py)、[用量记录](../src/ece329_workflow/telemetry.py)、[翻译验证](../src/ece329_workflow/localization.py)。页面的翻译脚本版本也已更新，避免继续加载旧缓存。

## 验证结果

- 全量 Python 回归：1206 passed，210 subtests passed。
- 最后补充符号校验后，翻译与执行专项：32 passed。
- 前端单元测试：27 passed；语法、编译和差异格式检查通过。
- 检查 294 处仓库相对链接和 Python 模块引用，未发现断链。
- GUIDED_DESIGN 与 EMVR_DIRECT 浏览器回归均通过：中英文切换、历史显示、模型/强度设置、预算停止、偏好恢复、手机布局，以及反馈提交→提炼→审阅→后续提示使用→停用撤回。

测试使用受控模型替身与本地 API，不消耗真实模型额度。它们验证调用、状态和防循环边界，不代表所有自然语言表达都已穷尽，也不替代真实模型的科学内容审阅。

## 部署与 CMD 推送

本次没有新增必填环境变量，保留 [预算配置](reasoning-budgets.md) 中的设置。同步部署前后端并重启后端。

已核对当前分支为 `main`，`origin` 为 `https://github.com/yzhao05/ece329-lab-design-workflow.git`。本次未执行提交或推送。在 CMD 中依次执行：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git add .env.example .github README.md docs src tests tools
git diff --cached --stat
git commit -m "Fix model routing, translation loops and reference validation"
git pull --rebase origin main
git push origin main
```

上面的添加命令包含本轮和此前尚未提交的工作流改动，避开仓库根目录现有的异常命名未跟踪文件；该文件保持原样。若 rebase 报冲突，解决冲突文件后执行 `git add` 和 `git rebase --continue`，完成后再推送。
