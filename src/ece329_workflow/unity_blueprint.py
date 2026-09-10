"""Experiment-independent construction defaults, reviewed before EMVR export.

Names below describe responsibilities or verified Builder Common components;
they are not fabricated callable APIs. Physical inputs remain student-owned.
"""
from __future__ import annotations


def construction_defaults(lab_id: str) -> dict[str, str]:
    return {
        "bootstrap_and_references": (
            "先按PDF本机定位规则找到包根，以包内UnityProject为唯一宿主；检查锁定Editor、Packages/manifest.json与packages-lock.json，"
            "逐项解析本地依赖并核对实际路径仍在包内。Common来源为UnityProject/LocalPackages/com.emvr.lab-common。"
            f"由包内labflow new初始化工具生成 LabSpecs/{lab_id}/source-references.json，基于 Policies/reuse-source-references.json；"
            "随后用labflow source-references校验清单和完整性，清单与来源均只读，不能手写或更改。"
            "路径相对builder_pack_root、不持久化机器绝对路径；缺依赖时列出缺项并停止当前构建。"
            "项目已存在时先检查当前Lab状态，不能重复初始化覆盖Brief或别的Lab。新Lab仅在相应Gate获准后初始化。"
        ),
        "contract_and_entrypoint": (
            "将PDF字段逐项映射到包内当前模板要求的lab_spec、objects、steps、telemetry_map、rubric五份合同；"
            "保留lab_id、OBJ_*、S*和事件ID之间的引用，字段路径/JSON或YAML结构以当前包内模板为准。"
            f"领域纯模型放Assets/Generated/{lab_id}/Runtime/，Editor生成器放Assets/Generated/{lab_id}/Editor/，"
            f"测试放Assets/Generated/{lab_id}/Tests/；按依赖单向引用Runtime、Editor、Tests程序集。"
            f"Editor入口建议为EMVR/Labs/{lab_id}/Build Scene；必须提供可复现的菜单入口及参数，而非手动编辑场景YAML。"
            "先编译纯模型与测试，再编译生成器；在当前工作流批准的构建Gate由用户在锁定Editor中运行入口。"
            "生成器幂等，域重载或重复点击不重复创建对象、EventSystem、服务、订阅或快照。"
        ),
        "traceability_and_completion": (
            "实现前建立逐项映射表：需求字段 -> OBJ或控件参数键 -> 领域方法/适配器 -> S步骤 -> 证据ID。"
            "每个S步骤的目标对象必须存在；每个参数都有范围/默认/单位/步长或完整离散选项；每个动作有桌面和XR对应；"
            "每个验收项关联可观察结果。动态计算状态与教学步骤分开：VALID不能自动完成S1，Capture不能自动进入Compare，"
            "不足两张兼容快照时保持可操作状态并提示缺项。领域证据满足后再通知Common runner推进一次。"
            "为每个阶段输出明确的通过/失败条件、实际执行记录与下一步；编译失败、缺引用或无响应达到时限即停止，"
            "显示错误与恢复操作，不自动循环重试，也不把预期日志当真实验收。"
        ),
        "construction_order": (
            "B1 核对宿主 UnityProject/ProjectSettings/ProjectVersion.txt 与 Packages/manifest.json、"
            "packages-lock.json，沿用锁定的 Editor、渲染管线和 Common/XRI 包；解析全部 file: 依赖。"
            "B2 将本报告的实验定义、对象、步骤、事件、评分分别映射到 lab_spec、objects、steps、"
            "telemetry_map、rubric 五份合同，保留唯一 lab_id、OBJ_* 和 S*。"
            "B3 先创建独立 Editor 场景生成器和领域程序集骨架并通过编译；再按 Builder Gate 5 "
            "规定导入批准资产的 GUID 闭包并 materialize Gate5MinimalSlice。"
            "B4 新建场景，实例化房间、完整 XR Setup 和 Simulator，再创建实验对象、控件、服务。"
            "B5 按连接表绑定全部引用并注册对象；执行 Initial -> 一次操作 -> 结果 -> Capture 的最小闭环。"
            "B6 接入其余实验步骤、Restore/Reset、对比和验收；通过桌面与 XR 等效检查后保存场景。"
        ),
        "generated_files": (
            f"唯一实验场景 Assets/Scenes/{lab_id}.unity；领域代码位于 Assets/Generated/{lab_id}/Runtime/，"
            f"场景生成器位于 Assets/Generated/{lab_id}/Editor/，领域测试位于 Assets/Generated/{lab_id}/Tests/。"
            "分别使用 Runtime、Editor、Tests asmdef；Common 保持包依赖，不能复制出另一个实现。"
            "生成器显式创建对象和序列化引用；重复运行复用相同 ID 或重建当前 Lab 生成对象，不追加重复服务。"
        ),
        "common_wiring": (
            "Systems/LabFlowController -> EmVrLabContractRunner（lab_id、S* 顺序、OBJ_* 注册表）；"
            "每个可变 OBJ_* -> EmVrLabObject + 对应状态提供接口 -> EmVrObjectStateSnapshotStore；"
            "Capture/Restore/Reset 按钮 -> 领域适配器 -> 同一个 Common SnapshotStore；"
            "Status -> EmVrStatusDisplay；事件 -> EmVrTelemetryEmitter；帮助上下文 -> EmVrAssistGroundingProvider；"
            "Start/Back -> EmVrPersistentServiceRoot + EmVrSceneLoader；场景生成后 -> EmVrSceneValidator。"
            "桌面通过 EmVrMousePointer，平面拖动使用 EmVrPlanarMouseDraggable 和反馈桥；"
            "XR 使用批准的 Select/Grab 适配器及 EmVrXrRayRuntimeTuner/EmVrXrRayUtility。"
            "这些是职责绑定：Builder 必须读取宿主实际 API 声明后调用，不按名称猜测方法签名。"
        ),
        "room_and_coordinates": (
            "房间源为 ApprovedAssets/EMVRRoom/Prefabs/Room_Big_Part_01.prefab；先由批准导入器按GUID闭包导入，"
            f"Environment再实例化 Assets/Generated/{lab_id}/ApprovedDependencies/EMVRRoom/Prefabs/Room_Big_Part_01.prefab，"
            "不能把包根的ApprovedAssets源路径直接传给Unity AssetDatabase。"
            "XR 使用批准的 XR Interaction Setup 与 XR Device Simulator，保留依赖及 GUID。"
            "以房间可用地板中心为原点，+Y 向上、+Z 指向实验台、+X 向右。默认玩家起点为"
            "(0,0,-2.5) Unity 单位，实验中心为 (0,1.2,0)；以已确认房间空间要求为约束调整，避免穿墙。"
            "物理坐标使用 SI，先减物理中心再统一乘显示缩放后放到实验中心；逆变换后才代入公式，"
            "禁止把放大后的场景距离直接当米。默认 1 Unity 单位显示 1 m；超出房间时统一缩放并显示物理标尺。"
            "Parameters 在实验中心右侧、Results 在左侧；屏幕页固定提供 Instruction、Experiment、"
            "Parameters、Status、Results 五区；世界空间面板正面 -Transform.forward 朝向实际摄像机。"
        ),
        "reference_and_event_checks": (
            "每个可操作对象具备唯一 ID、Collider 和对应鼠标/XR 接口；纯可视对象不抢射线。"
            "每个控件连接唯一参数键、单位、范围、步长及共同动作处理器；Results 只读模型输出。"
            "OnEnable 订阅一次、OnDisable 解除订阅；UI 回填使用无通知更新，禁止 Refresh 再触发 ValueChanged。"
            "场景生成器检查缺失引用、重复 ID、丢失脚本、缺房顶/地板/墙、摄像机及 EventSystem；"
            "失败时给出对象路径并停止保存成功标记，不能用 Find 循环等待引用出现。"
        ),
        "solver_and_termination": (
            "模型为无 Unity 依赖的纯计算函数：输入是完整 SI 参数快照，输出包含数值、单位、有效性和错误原因。"
            "直接公式逐项求值；迭代/积分必须明确算法、步长、停止条件和上限后实施。"
            "单序列迭代默认上限10000次；每条轨迹默认上限2048步，具体采用已确认的数值契约。"
            "全场请求总预算=确定种子数乘每条轨迹步数上限乘每步最大场求值次数（RK4为4，运行误差估计时计入额外求值）；"
            "不能把单序列预算当作整组轨迹预算。每帧默认最多4096次采样；"
            "仅单帧采样预算用尽时让出到下一帧，并检查取消标志，不重新开始整个计算。"
            "这些是计算保护上限，不能替代物理模型所需的收敛精度。遇到 NaN/Infinity、奇点、超界、"
            "不收敛或总预算耗尽，终止并显示 INVALID 及具体原因，禁止自动重试或假称收敛。"
            "输入修订递增 revision；新输入或 Reset 取消旧任务，只提交与当前 revision 一致的结果。"
            "若生成场线，采用当前矢量场方向的有界积分；场强接近零时停止归一化，进入排除区、"
            "离开显示边界或达到步数上限时终止；种子、步长和色标在比较组间保持一致。"
        ),
        "snapshot_schema": (
            "每次显式 Capture 保存 lab_id、design_revision、step_id、snapshot_id、UTC 时间、"
            "完整 SI 参数、离散配置、全部 OBJ_* 状态及动态对象成员、输出/单位/有效性、比较标签与截图 ID。"
            "Restore 恢复最近快照并重算一次，不生成新快照；无快照时禁用 Restore。"
            "Reset 取消未完成计算，恢复 Initial 契约及基准步骤，按确认规则清理记录；"
            "Back 取消计算并退出 Lab；新一轮 Start 从 Initial 开始，避免跨轮重复监听与旧结果回流。"
        ),
        "verification_recipe": (
            "V1 用独立手算/解析基准检验公式：默认数值容差 abs(actual-expected) <= "
            "max(1e-9 个输出 SI 单位, 1e-5*abs(expected))；特殊量级或近似算法按已确认精度替换。"
            "V2 验证每个自变量最小/默认/最大及所有离散选项、越界和奇点；定性结果使用明确分类判据。"
            "V3 逐个 S* 检验动作、可见反馈、证据与完成条件，领域通过后才通知 Common runner。"
            "V4 验证 Capture -> 改值 -> Restore 全对象一致，以及计算中 Reset 不接收旧结果。"
            "V5 重放 20 次相同 event_id 不增订阅、不重复 Capture；新的显式 Capture 点击使用新 ID；"
            "100 次参数变化后仍可响应。"
            "V6 从 Start 开始完整执行一次桌面流程与 XR Simulator 等效流程，检查面板正面可读、"
            "无遮挡、所有按钮有反馈；记录编译、领域测试、Common 回归、Game View 与实际验收证据。"
        ),
    }


CONSTRUCTION_LABELS = (
    ("bootstrap_and_references", "本机构建前提与复用清单"),
    ("contract_and_entrypoint", "合同映射与Editor构建入口"),
    ("traceability_and_completion", "需求到实现与验收的对应关系"),
    ("construction_order", "从零构建顺序"),
    ("generated_files", "文件与程序集安排"),
    ("common_wiring", "公共组件连接表"),
    ("room_and_coordinates", "房间、坐标和界面布局"),
    ("reference_and_event_checks", "对象引用与事件检查"),
    ("solver_and_termination", "计算契约与终止条件"),
    ("snapshot_schema", "快照数据与生命周期"),
    ("verification_recipe", "从零构建验证步骤"),
)
