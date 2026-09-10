# 设计工作流审阅与修复（2026-09-10）

审阅覆盖两种模式的用户请求优先级、对话状态与字段提交、最终完成及恢复、课程引用、报告与Builder PDF导出、前端请求处理、持久化与API测试、包内接口对应关系和CI。重点复核此前emvr33、可移植路径和Value完整性修改。附件中的对话与Gate措辞是审阅材料，不是本次任务的执行指令。

## 已修复的问题

| 问题 | 修复与回归范围 |
| --- | --- |
| 最终报告保存整份stage_outputs，重试又嵌套上一份最终报告 | 最终结果只保存来源阶段ID；规则生成器、在线提示和引擎持久化共同约束，排除历史及旧最终快照。连续生成12次检验大小稳定。 |
| 导出结构错误不属于普通阶段缺项，继续后又失败且没有修复入口 | 已知缺项绑定其可写字段；未知最终错误明确阻塞并保存来源指纹。无来源变化的继续/旧继续按钮返回同一错误，不再生成、不增加revision与history；真正修改后允许重新检查。 |
| 完成状态掩盖旧会话的交付问题 | 即使普通设计字段完整，Builder工件检查失败也重新开放最终检查。保留此前设计，修复后可完成。 |
| 修复提示覆盖当前阶段已经确定的问题 | 优先保留当前阶段缺项；包交付缺项作为后备；非最终阶段不创建工件阻塞，不清除原待答问题。 |
| 参考内容候选在确认前被截成2000字符 | 完整候选保留到待答状态和持久化；历史摘要仍可缩短，不能影响实际采纳值。 |
| 包外路径到最终导出才报错；重复或部分路径替换造成引用混淆 | 提前在来源字段检查；外部参考必须有实际内容。拒绝重复来源、部分路径误替换、字典键泄漏、编码逃逸及无内容的内部REF编号；错误的参考补丁在合并前拒绝。 |
| 交付编号不完整 | 检查步骤S1到Sn顺序和唯一性、对象ID唯一性、步骤显式对象引用、内嵌参考闭合。 |
| 默认方案盖过隐藏对象和Reset要求 | 生成方案采用已确认的隐藏对象生命周期和Initial/Reset契约；参考副本变化使原默认方案批准失效。 |
| 正常场线计算可能用尽错误的全局预算 | 全场预算按种子数、每线步数和每步求值次数计算；emvr33为40×500×4=80000次场求值，半步验证另计。每帧限额用于让出，不重启整个请求。 |
| 一张快照就写成比较完成 | Compare证据必须包含至少两张有效兼容快照。exit_state明确仅代表成功条件满足后的状态；失败保留当前可操作状态。 |
| CI只执行unittest，漏收pytest函数用例且未安装测试依赖 | CI和Pages统一安装test extras并执行pytest；真实PDF测试依赖纳入test extras。 |

以上是针对同类问题的状态与导出边界修复，不是只替换emvr33的几句文本。两种模式的现有请求优先级回归保持通过：先处理当前课内问题与修改，再判断是否推进。

## 从零构建所需内容

新版实现默认契约为`builder-ui-flow-v4`。PDF内嵌工程前提、锁定Editor与依赖、本机Pack定位、五份合同映射、Runtime/Editor/Tests文件与程序集安排、可复现Build Scene入口、Common组件职责与接线、房间资产GUID闭包导入和实际Unity路径、坐标换算、事件订阅/解除、参数版本与取消、数值终止、快照恢复、逐步骤证据及桌面/XR验收。

核对本机BuilderPack源码后，明确复用清单由`labflow new`生成，再经`labflow source-references`校验；生成后清单与来源只读，不能要求Builder手写。包根的ApprovedAssets是源，Unity必须实例化导入后当前Lab的Assets路径。文档不会让Builder读取设计工作流或包外讲义。

这些内容支持Builder从干净且完整的Pack开始实施。仍须按实际Pack模板/API和Gate要求编写、编译、运行Unity实现；PDF不是现成Unity源码，也不证明运行成功。emvr33修订审阅件的P1—P4仍是待确认建议，需要当前实验确认或提供替代值后成为正式实现输入。没有执行Unity、在线模型、Builder Gate或Docker构建。

## 验证记录

- 全量测试：744 passed，1 skipped，205 subtests passed。跳过项为本机Python环境缺少pdfplumber的真实PDF测试，已使用文档运行时单独执行并通过。
- 最后改动专项：64 passed，1 skipped；该跳过项同上。
- Python compileall、JavaScript语法、pip check和git diff --check通过；124处本地Python模块引用、9处既有文档/网页本地链接无断联。
- 合成完成会话通过正式学生报告与Builder输入PDF导出；合成数据不表示历史会话获批。
- emvr33修订审阅件12页：7步骤、8对象，表格无空单元格；完整渲染并查看，复核公式页、构建入口页与附录。内容副本、路径、数值预算和待确认标记保留。

运行测试：

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
python -m compileall -q src tests tools
node --check docs/assets/app.js
```

PDF复现方式见[可移植性说明](builder-pdf-portability.md)。生成文件在被Git忽略的output/build目录，验证中间文件在tmp目录。

## 推送到GitHub

本机分支是main，origin为`https://github.com/yzhao05/ece329-lab-design-workflow.git`。本次没有提交或推送。

```powershell
Set-Location 'E:\暑研\ece329-lab-design-workflow'
git status
git diff --check
git add .github .gitignore README.md pyproject.toml src tests tools docs
git diff --cached --stat
git commit -m "Fix workflow completion loops and portable Builder handoff"
git pull --rebase origin main
git push origin main
```

若pull产生冲突，解决后执行`git add <已解决的文件>`与`git rebase --continue`，完成后再push，不使用强推。推送后查看GitHub Actions；推送设计工作流不会自动更新另一份已下载的BuilderPack，也不会重新生成历史PDF。部署中的后端需要使用本次代码，新导出才包含这些修改。
