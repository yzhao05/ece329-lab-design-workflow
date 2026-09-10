# emvr34 项目复核与磁场 Builder 交付

本次审阅覆盖近期主题识别与公式恢复改动，以及意图动作、阶段状态、知识库、Builder 校验、两种模式的回归、PDF 导出、前端语法与 CI 配置。附件仅作为故障证据；没有操作 BuilderPack 的 Gate，也没有执行 Git 提交、推送或部署。

## 实际发现并修复的问题

1. **候选重放**：延后执行的旧主题动作可能压过“换一组”。现在仅在明确继续原选择时恢复延后动作；新请求和新按钮优先。候选用尽后，重复解析出的相同主题保留排除集合；有实质细化时才重新匹配。直接改为另一个明确课内主题也支持服务故障恢复。
2. **领域校验旁路**：模型虚构的 `explicit_formula_ids` 曾能让静电公式越过磁场过滤。跨领域候选现在需要课程关联或主题中的明确公式依据；全否定和多个领域的粗略主题不会任意选择一个领域。选定档案也不能从未选卡片中夹带公式。
3. **引用静默丢失**：找不到已选公式或方法时，原导出逻辑会跳过项目或套用 legacy 内容。现在明确报错；Physics 和所选公式表必须一致，主要/辅助角色间公式 ID 不得重复。旧式交付的兼容只适用于没有权威公式 Brief 的真实旧会话。
4. **步骤未写入主流程**：通用字段写入将步骤列表压成一句，并仅出现在“补充要求”；默认七步继续进入 Builder。现在列表保留换行边界并同步到权威步骤存储，PDF 与批准的实现流程使用相同步骤。清空和替换也会使旧批准失效。
5. **无曲线误判为无数值**：曲线开关与测量类型已分开。“不生成曲线”的磁场实验仍保留 B 分量、大小、单位及数值快照；明确仅定性的实验才标记定量指标不适用。
6. **动作错误引发循环/证据污染**：“快照”这个名词不再自动触发 Capture；Restore 恢复并重算一次，Reset 清空不自动捕获，Back 取消工作并回 Start。“设为值 -> Capture -> 比较”保留各动作。所有步骤仍要求完成原始完整指令才推进一次。
7. **物理说明缺项与矛盾**：移除报告默认段落中未经支持的“距离缩短必然扩大低密度区/增强连接密度”等趋势。新增右手 SI 坐标与 Unity 显示变换、所选磁场公式的计算角色、必要边界和独立基准要求。

## PDF 是否足以开始 Unity 构建

新增 `physics_blueprint.py` 针对磁场源、安培环流、长螺线管、电流面、磁矢势、法拉第感应、电感、RL 和洛伦兹力给出实现要求，只纳入当前所选公式。它不会代填未确认的几何、材料、采样或容差，也不会把仅给出旋度/泊松方程的设计冒充完整求解方案。

`builder-ui-flow-v5` 默认契约增加物理坐标/显示变换与公式实现要求。版本更新使旧版默认实现批准失效，需在设计中审阅更新后的完整契约；不能在最终 PDF 中悄悄追加未审阅的默认行为。

磁场合成案例明确：长螺线管中心采样、唯一可调的有向电流、真空模型磁导率、匝数密度、可视几何、右手绕行方向、H/B 直接计算、零场处理、精度基准、五步流程、桌面/XR、快照与 Reset。它通过实际公式选择状态机、生成器、最终完成检查和正式 Builder PDF 导出器。字段由测试提供，不模拟自然语言模型；不是对用户历史方案的批准。

交付已经包含从本机定位包根、包内依赖检查、合同映射、Unity 程序集/Editor 入口、公共组件连接、物理求值到步骤/验收的构建依据。所有本地读取依据仍限制在 `EMVR_Blind_BuilderPack`；外部依据必须内嵌必要正文，不能留成包外读取任务。Unity 使用左手场景坐标的事实参考 [Unity 官方旋转与方向文档](https://docs.unity.cn/Components/QuaternionAndEulerRotationsInUnity.html)；PDF 已包含实际所需的坐标换算说明，不要求 Builder 联网读取该网页。

**验证范围**：完整磁场案例具备开始实现所需的设计输入，但没有运行 Unity Editor、C# 编译、XR 或真实在线模型，不能据此宣称任意实验都已成功构建。新实验仍需确认特有源几何、方程边界和数值契约，并由 Builder 在本机验证实际 Common API 和锁定依赖。关键词结构校验不能证明任意自然语言物理契约在科学上完整。

## 本地复现

最终验证：在带 PDF 依赖的 Python 3.12 环境中，全量 **801 项测试及 205 个子测试通过，无跳过**；包括两种模式的问题优先处理、磁场解析故障恢复、候选耗尽、真实 PDF 内容回读和长字段分页。知识库引用、文档本地链接、Python 编译、前端 JavaScript 语法及 `git diff --check` 均通过。21 页磁场 PDF 已逐页渲染检查，无空白正文页、越界路径或静电公式残留。

在项目根目录的 CMD 中：

```bat
set PYTHONPATH=src
python -m pytest -q
python -m compileall -q src tests tools
node --check docs\assets\app.js
python -m tools.export_emvr34_regression
```

合成 PDF 输出到 `output/pdf/emvr34-magnetic-builder-regression.pdf`；中间 JSON 在 `build/emvr34-regression/`。这些目录被 Git 忽略，不上传用户文档。PDF 测试依赖 `pdfplumber`，标准安装为 `python -m pip install -e ".[test]"`。

## 用 CMD 推送

本次已核对分支 `main`，远端 `origin` 为 `https://github.com/yzhao05/ece329-lab-design-workflow.git`。从 CMD 执行：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git diff --check
git diff
git add README.md src tests tools docs
git diff --cached --stat
git commit -m "Fix EMVR topic recovery and magnetic Builder handoff"
git pull --rebase origin main
git push origin main
```

每条命令成功后再执行下一条。首次认证按 Git Credential Manager 的提示登录。若 rebase 冲突，解决冲突文件后 `git add 文件名`，再 `git rebase --continue`，成功后才 push；不使用 force push。推送后查看 GitHub Actions，使用中的后端也必须更新并重启到新版本；仅刷新旧部署网页不能加载这些后端修复。
