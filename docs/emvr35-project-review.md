# emvr35 项目复核（2026-09-11）

本轮复核设计工作流的状态推进、恢复与元问题入口、知识引用、Builder 输入校验、PDF 导出、前端语法及回归测试，重点检查前两轮尚未提交的改动。保留已有修改，没有改动 EMVR_Blind_BuilderPack、部署服务或推送 Git。

## 复现与修复

新增回归先复现了九个失败案例，修复后进一步补充数值与导出检查，共十三个案例。

| 问题 | 修复后的行为 |
| --- | --- |
| “我有点乱，电流方向反过来试试”等混合消息被元问题入口整条消费 | 两种模式共用的入口逐句检查；有实质修改的消息交回原解析路径，避免仅回复进度而丢弃修改。仍不宣称离线规则能理解任意自然语言。 |
| “不要恢复之前的实验流程”反而恢复历史 | 恢复必须包含肯定操作；否定请求保持当前流程和待办。 |
| 换实验后恢复了前一个实验的步骤 | 新主题记录历史起点；兼容旧记录中的 NEW_TOPIC 边界，历史查询和恢复限于当前实验。 |
| “不要Reset或Back”“不要Restore”“Restore已禁用”被 PDF 映射成执行按钮 | 操作提取共用局部否定检测；后续分句的肯定 Capture 仍保留；比较后解释按原顺序输出。 |
| Common 接口职责不完整，可能重复推进或恢复已清除的快照 | 写入实际接口签名、领域适配层的检查职责、一次性步骤推进、快照封装、Reset 清理和过期事件隔离。 |
| 仅限制 RK4 外层步数，源积分或精度复算仍可能长时间阻塞 | 默认方案要求源贡献也计入工作预算，分帧保存进度，检查取消、总预算和复算次数；闭合轨迹与截断有明确区分。 |
| PDF 章节跳号、末尾说明零散跨页 | 章节连续编号，交接说明整体分页。 |

此前的单步查看保护、阶段 7 补全、默认方案重新批准和最终导出恢复测试继续通过。新增构建默认项进入 `generated_contract` 批准指纹，旧批准不能直接覆盖新增内容，需要重新审阅。

## 从零构建所需内容

新增 `tools/export_emvr35_review.py`，经真实工作流状态和正式 Builder 导出生成闭合圆环磁场的合成审阅件。它只使用毕奥–萨伐尔公式，包含：

- 本机 BuilderPack 根目录定位、从项目版本文件读取 Unity 与依赖、场景和对象搭建、桌面/XR 共用操作。
- 电流范围、默认值、步长、单位、圆环方向与几何、磁导率、中心采样、固定种子的具体坐标公式。
- 源线段积分、RK4 步长、排除区、零场、闭合判据、边界与最大步数、收敛容差和有限复算。
- 七步实验操作及状态变化，Capture、比较、Restore、Reset、Back 的职责和验收。
- Common runner 与 snapshot store 的实际接口，以及领域层必须自行实现的物理就绪检查、记录元数据、取消和重复事件防护。

实际核对了本机包中的 `EmVrLabContractRunner.cs` 与 `EmVrObjectStateSnapshotStore.cs`。PDF 引用使用完整包内相对路径；包外知识需要的说明以内嵌内容交接，不要求 Builder 越过目录边界读取维护仓库。

圆环案例已具备从零开始实施所需的设计输入和搭建说明。PDF 是构建契约，Builder 仍需编写领域计算与适配代码、遵守自身 Gate 并进行 Unity 编译和桌面/XR 验收；本次没有执行 Unity。其他任意实验仍需通过各自参数和算法完整性检查，不能从这一案例推断全部模型已可构建。

复现审阅件（安装项目依赖后，在仓库根目录运行）：

```bat
set PYTHONPATH=src
python -m tools.export_emvr35_review
```

输出为 `output/pdf/emvr35-biot-savart-builder-review.pdf`，中间数据在 `build/emvr35-review/`。这些生成目录被 Git 忽略；推送代码后可重新生成。合成会话中的确认仅用于测试，不代表真实用户已批准该实验。

## 验证结果

- 完整套件：**871 项测试、205 项子测试通过，无跳过**，使用带 PDF 依赖的 Python 3.12 环境。
- 知识库引用校验、正式 Builder 输入校验、Python 编译、前端 JavaScript 语法和 `git diff --check` 通过。
- 独立 CPU 计算比较圆环轴上解析解与 512 段毕奥–萨伐尔积分；中心及两个轴上位置满足误差要求，1024 段误差减小。没有执行完整 RK4 轨迹收敛实验。
- 最终 PDF 共 **22 页**，全部页面渲染并检查，未见文字裁切或乱码；检查了包内路径、公式、步骤、数值和新增接口内容。
- 未调用真实在线模型，未运行 Docker 构建、Unity 编译或 XR 设备测试。

全量回归与部署不会互相替代：提交到 GitHub 后，还需要更新并重启实际后端，新行为才会用于线上会话。

## 在 CMD 推送

已核对仓库分支为 `main`，`origin` 为 `https://github.com/yzhao05/ece329-lab-design-workflow.git`。先查看改动，再逐条执行，每条成功后执行下一条：

```bat
cd /d "E:\暑研\ece329-lab-design-workflow"
git status
git diff --check
git diff
git add README.md src tests tools docs
git diff --cached --stat
git commit -m "Fix workflow recovery and Unity Builder handoff"
git pull --rebase origin main
git push origin main
```

`git add` 会包含此前尚未提交的工作流修复，提交前核对暂存列表。若 rebase 遇到冲突，解决冲突后暂存对应文件并运行 `git rebase --continue`，成功后再 push。
