"""Reproducible review of the supplied emvr33 PDF/screenshot, not a Gate approval.

Facts were checked against all 20 source pages. Only the explicitly supplied PDF
is read; no Builder workspace, previous lab, or source path inside a document is
opened. P1-P3 retain the screenshot's proposal status. P4 is a new numerical
precision proposal, since an RK4 step size is not itself an error tolerance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from ece329_workflow.builder_input import _field, render_builder_review_pdf
from ece329_workflow.builder_portability import PACK_NAME, ROOT_DISCOVERY, SOURCE_BOUNDARY
from ece329_workflow.unity_blueprint import construction_defaults, CONSTRUCTION_LABELS

SOURCE_SHA256 = "478f757416edb50ea20a8adf81c7ebe5e1d145000ef4fe8e8430298c62b5ac5f"
LAB_ID = "ece329_electric_field_0"
FACT = "confirmed-from-source-document"
PROPOSAL = "proposal-needs-confirmation"


def review_sections():
    def row(key, value, status=FACT, note=""):
        if status == FACT and re.search(r"P[1-4]", str(value)):
            status = "source-with-proposal-references"
            note = note or "涉及P1-P4的具体补充仍以相应建议的确认状态为准。"
        return _field(key, value, status=status, note=note)

    params = "distance_m：滑块和受约束拖动；最小0.5 m，最大5.0 m，默认2.0 m，步长0.1 m。charge_configuration：按钮，选项same_sign/opposite_sign，默认same_sign；属于类别，无数值单位。两端使用同一输入校验。"
    science = "根据库仑公式计算各源电场并作矢量叠加，再由合场方向积分场线。同号观察源间弯曲、稀疏与对称性，异号观察从正到负的方向与连接。场线空白不等于整片零场；不把空白面积随距离减小必然扩大作为验收条件，也不把有限种子的线密度直接当场强。"
    initial = "首次进入Lab后，两电荷位于原点两侧、距离2.0 m、同号，显示参数、场线和方向箭头；没有空间探针和数值形态曲线，快照为空。模型初始化后为VALID，但S1仍等待真实用户操作。精确正负号和轴向采用P1/P2建议时须记录确认。"
    measurement = "距离读数d=|r_A-r_B|，单位m，源位置更新时刷新；电荷配置按Q_A、Q_B符号分类，单位不适用。空间形态用统一视角、种子、色标下的连接、弯曲与对称性作定性描述。理论计算结果标记theoretical_prediction，measured=false；不创建空间探针、局部场强读数、数值形态纵轴或理论曲线。"
    steps = [
        ("核对参数并操作一次", "核对距离范围/步长/默认值及极性选项，改变一次距离或极性。", "控件与最新有效场线一致；初始化不自动完成S1。"),
        ("记录基准", "加载2.0 m同号基准，等待VALID后显式Capture。", "产生一个新快照ID；一张快照为CAPTURED；加载基准保留已有记录见P2。"),
        ("只改变一个自变量", "固定另一条件，调节距离或切换极性。", "常量、种子、视角与色标固定；仅最新revision提交结果。"),
        ("保存变化结果", "有效结果显示后显式Capture，填写定性观察。", "快照含实际参数、配置、有效性、标签和截图ID；拖动本身不自动保存。"),
        ("覆盖六种情形", "按P1的三种距离各记录同号和异号。", "六个不同参数组合均有有效快照；重复同一组合不能补齐缺项。"),
        ("公平并排比较", "选择至少两张同距离、不同极性的兼容快照，统一视图比较。", "显示快照ID、参数及差异；当前改值不改写保存结果；条件不足时保持CAPTURED。"),
        ("解释与提交", "用公式解释至少一种差异，引用快照并说明模型/显示限制。", "六种情形和一组对照齐全；按P3评价科学解释，最终正确性由评价者确认。"),
    ]
    objects = [
        ("点电荷 A", "物理源", "保存位置、带符号电荷量C和两源间距m；初态2m同号，绝对值1e-9 C。", "桌面选中拖动/XR射线或抓取；按P2仅改变距离。", "位置、正负标签、距离和场线同步刷新。"),
        ("点电荷 B", "物理源", "与A共同定义合场；绝对值1e-9 C；P1指定切换符号。", "与A相同；极性切换走同一动作处理器。", "标签和场线与接受的配置一致。"),
        ("XR Origin与左右控制器", "交互基础", "批准的完整XR Setup和Simulator；不参与电磁计算。", "提供视角、射线选择、抓取和UI输入；桌面等效操作见交互表。", "选中/抓取/按钮反馈可读。"),
        ("参数与状态控制面板", "UI", "保存范围、默认、步长、配置和状态；固定输入只读。", "桌面鼠标与XR UI操作同一距离控件和极性按钮。", "显示接受值、单位、范围和有效性。"),
        ("理论计算组件", "计算与数据", "读取完整SI参数快照，计算E_A+E_B，带revision/有效性。", "无直接学生操作；由已接受输入触发。", "输出数值/错误原因，经场线和Status显示；不抢射线。"),
        ("测量与观察控制器", "观察与测量", "读取距离、配置和定性比较；无空间探针。", "通过已定义观察/记录控件；无可移动探针。", "显示距离m、类别与定性描述；没有虚构形态分数。"),
        ("场量可视化系统", "教学可视化", "只读取理论结果；20种子/电荷，固定色标与方向规则。", "桌面与XR切换观察/比较视图；不修改物理输入。", "3D场线、箭头、图例与截断/无效提示。"),
        ("结果记录与比较面板", "实验控制与反馈", "初始快照集合为空；保存参数、结果与比较顺序。", "桌面点击/XR按钮执行Capture、Restore、Compare、Reset。", "显示唯一快照ID、六情形覆盖和至少一组公平对照。"),
    ]
    formulas = [
        {"formula_id": "coulomb_point_charge", "expression": "E_i(r) = Q_i*(r-r_i)/(4*pi*epsilon_0*|r-r_i|^3)", "condition": "静止理想点电荷、真空、采样位置不在源排除区内。"},
        {"formula_id": "electric_field_superposition", "expression": "E_total(r) = E_A(r) + E_B(r)", "condition": "均匀线性介质，本实验固定真空。"},
    ]
    sections = [
        ("1. 实验概览与使用范围", [
            row("identity", {"lab_id": LAB_ID, "title": "点电荷场中的库仑定律和矢量叠加", "domain": "ECE329 electromagnetics", "mode": "integrated-development", "origin": "original-new-experiment", "schema_version": "1.0.0"}),
            row("summary", "两点电荷、三维RK4场线、三种距离乘两种极性共六种情形；7个步骤、8类对象、桌面/XR等效操作、快照恢复、英语界面与Common/批准房间复用。"),
            row("approval_scope", "本文件是对原PDF与截图的整理修订。原文明确事实与待确认建议分别标注；不复述或继承其他任务的Gate审批，不表示Unity实现已完成。", "review-document"),
            row("course_priority", "以用户已确认的课内研究问题、公式、变量和约束为主；先解决用户当前要求与缺项，再推进设计流程。", "workflow-rule"),
        ]),
        ("2. 本机定位与包内边界", [
            row("execution.builder_pack_root", PACK_NAME, "builder-runtime-check"),
            row("execution.unity_project_relative", "UnityProject", "pack-relative-path"),
            row("execution.local_root_discovery", ROOT_DISCOVERY, "builder-runtime-check"),
            row("source_material.boundary", SOURCE_BOUNDARY, "export-invariant"),
            row("source_material.reference_manifest", f"LabSpecs/{LAB_ID}/source-references.json；base=builder_pack_root；access=read_only；persisted_absolute_source_paths=false", "pack-relative-path"),
            row("scene.output_scene", f"Assets/Scenes/{LAB_ID}.unity；相对包内UnityProject。", "pack-relative-path"),
        ]),
        ("3. 截图建议与缺项补全", [
            row("P1.polarity_and_presets", "A固定+1 nC；同号时B=+1 nC，异号时B=-1 nC。Near=0.5 m、Middle=2.0 m、Far=5.0 m。六情形为每种距离各自的same_sign与opposite_sign。", PROPOSAL, "截图建议；原PDF只给绝对电荷量、相对符号、距离端点和默认值，未给精确对应。"),
            row("P2.drag_and_baseline", "两源沿固定X轴关于固定中点对称：r_A=(-d/2,0,0)、r_B=(d/2,0,0) m；拖动投影到该轴，仅改变d，按0.1m吸附并限制0.5-5m，另一源同步。超范围停在最近边界并显示提示；非法非有限输入拒绝。基准加载保留快照与步骤；Reset清空本轮记录并回S1。", PROPOSAL, "截图中的拖动/记录建议；精确坐标与边界反馈是本次可执行化补充。"),
            row("P3.scientific_acceptance", science, PROPOSAL, "截图的科学判据修正；删除原文中把空白扩大当固定结论的冲突说法。"),
            row("P4.numerical_precision", "建议|E|<1e-9 V/m时停止归一化；用同一种子和弧长采样、将RK4步长减半到0.025 m复算，位置偏差容差0.01 m；超差标记精度不足。每源20个确定性均匀球面种子，种子球半径0.06 m（排除半径的1.2倍）；过滤落入另一源排除区或显示域外的种子。正源顺E、负源逆E跟踪，箭头始终沿E；所有比较固定此规则。", PROPOSAL, "本次新增建议：原PDF只有步长与种子数量，没有轨迹容差、近零阈值和具体种子位置。不得视为原用户已确认的数值。"),
        ]),
        ("4. 研究问题与学习目标", [
            row("research_question", "距离与同号/异号配置如何影响两点电荷合场方向、弯曲、连接和空间分布？用库仑场的矢量叠加解释配置差异。"),
            row("independent_variables", "distance_m与charge_configuration；电荷绝对值、介质、种子规则、色标、比较视角和尺度固定。"),
            row("observed_quantity", "三维合场线与方向箭头的定性形态。"),
            row("hypothesis_and_expected_results", science, "corrected-scientific-interpretation"),
            row("learning_goals", ["解释库仑场与矢量叠加。", "由位置与极性判断合场方向、大小变化和零场点。", "比较六种配置并解释至少一种差异。", "用桌面/XR完成等效调参、记录和恢复。", "联系对称性、弯曲/连接与模型、视觉编码限制。"]),
        ]),
        ("5. 公式、单位与固定输入（内容已内嵌）", [
            row("physics.formulas", formulas, "copied-course-formula-content"),
            row("physics.symbols_and_units", "r、r_i与d：位置/距离，m；Q_i：带符号电荷量，C；epsilon_0：真空介电常数，F/m；E_i、E_total：电场矢量，N/C（等价V/m）。d=|r_A-r_B|。极性与定性形态属于类别，无数值单位。"),
            row("physics.constants", {"epsilon_0_F_per_m": "8.854e-12", "charge_magnitudes_C": ["1e-9", "1e-9"], "medium": "vacuum", "exclusion_radius_m": 0.05, "seeds_per_charge": 20}),
            row("physics.scope", "静电场、理想点源、均匀线性真空；不模拟材料边界、导体表面重分布、辐射或传播瞬态。排除半径是数值保护，不是电荷物理尺寸；场线密度/颜色是视觉示意，不是实测值。"),
        ]),
        ("6. 参数、预设与数值计算", [
            row("physics.parameter_contract", params),
            row("presets.case_matrix", [{"distance_label": label, "distance_m": distance, "configurations": ["same_sign", "opposite_sign"]} for label, distance in (("Near", 0.5), ("Middle", 2.0), ("Far", 5.0))], PROPOSAL, "精确预设值源自P1。"),
            row("numerical_model", "在SI坐标中直接计算库仑场并叠加；三维RK4沿合场方向积分，dr/ds=+/-E_total/|E_total|，箭头始终沿E_total。包围盒中心(0,0,0)m、尺寸10x10x10m，即各轴[-5,5]m；步长0.05m；每线最多500步；每电荷20个种子，固定生成规则。"),
            row("numerical_termination", "进入任一0.05m排除区、离开包围盒、达到500步、零场无法归一化时终止。NaN/Infinity或请求总预算耗尽为INVALID；标明截断原因。每线500步优先于通用2048步保护；40条轨迹乘500步乘RK4的4次求值，全场预算80000次场求值；半步验证单独计入验证预算。每帧4096采样，帧预算用尽让出后继续，不重启请求。近零阈值和轨迹精度采用P4前须确认。"),
            row("formula_validation_tolerance", "独立解析基准的默认容差：abs(actual-expected) <= max(1e-9个输出SI单位, 1e-5*abs(expected))。这是公式数值输出的检查，不能替代RK4轨迹误差检查。"),
        ]),
        ("7. 七个学生步骤", [row(f"student_tasks.S{i}", {"goal": goal, "action": action, "success_criteria": success}) for i, (goal, action, success) in enumerate(steps, 1)]),
        ("8. 八类对象及职责", [row(f"objects.OBJ_{i:02d}", {"display_name": name, "role": role, "state": state, "interaction": action, "visible_feedback": feedback, "required": True}) for i, (name, role, state, action, feedback) in enumerate(objects, 1)]),
        ("9. 桌面与XR等效操作", [
            row("interaction.mapping", [
                "选择：鼠标左键点击 / XR射线选择；同一目标和选中反馈。",
                "调距离：滑块或受约束鼠标拖动 / 同一XR UI或抓取适配；同一d和场线结果，约束见P2。",
                "切极性：点击按钮 / XR射线扳机按钮；同一配置和符号反馈。",
                "Capture、Restore、Compare、Baseline、Reset：两端操作同名控件，状态变化与证据一致。",
                "观察缩放：滚轮 / 批准的手柄轴或UI适配；只改变观察，保持SI量和标准比较相机不变。",
            ]),
            row("interaction.pipeline", "ValidateInput -> UpdateExperimentState -> RecalculateModel -> RefreshVisualization -> RefreshReadouts -> EnableCapture。参数变动递增revision并取消旧任务；拖动/回填不自动Capture。事件名表示职责，须按包内真实API接线。"),
        ]),
        ("10. 显示、测量和快照", [
            row("visualization.measurement", measurement),
            row("snapshot.fields", "lab_id、design_revision、step_id、snapshot_id、UTC时间、完整SI参数、极性配置、OBJ状态及动态成员、输出/单位/有效性、比较标签、截图ID。"),
            row("snapshot.lifecycle", "Capture仅接受最新完整有效结果；同一event_id重放不重复记录，新显式点击创建新ID。Restore恢复最近快照并重算一次，不新增快照，无快照时禁用。Compare至少两张有效兼容快照，固定视角、尺度、种子和色标；P2区分Baseline与Reset。"),
        ]),
        ("11. 房间、UI与复用边界", [
            row("environment.room", "房间源为 ApprovedAssets/EMVRRoom/Prefabs/Room_Big_Part_01.prefab，须按附录A的GUID闭包导入到Unity宿主后实例化；保留房间完整结构，不复制演示场景，不生成基础几何房间代替。"),
            row("environment.placement", "学生位于房间中央，两电荷在前方约1-2m的操作观察区；参数在右、结果在左，四周至少2m活动空间。柔和均匀照明、简洁背景、清晰符号标签；UI不遮挡场线与射线目标。"),
            row("scene.ui", "语言English；Instruction、Experiment、Parameters、Status、Results五区。世界空间UI正面-Transform.forward朝向实际Play Mode摄像机，保存前检查正面与可读性。"),
            row("reuse.common", "必须复用Common身份/状态、快照/Reset、步骤、遥测、帮助、Status、桌面指针、XR输入/反馈、Start/Lab/Back和场景校验职责；按实际能力复用拖动组件。审查真实API和程序集，不猜方法签名，不改写公共源码和知识库。"),
        ]),
        ("12. 初态、恢复和流程状态", [
            row("initial", initial),
            row("hidden_objects", "not-applicable：教学实验没有隐藏启动模板或待加载电荷；无需填写触发数值。", "not-applicable"),
            row("states", "START、READY、DIRTY、CALCULATING、VALID、INVALID、CAPTURED、COMPARING、COMPLETE。Start加载Initial并计算；改值进入DIRTY再计算；单张快照为CAPTURED；两张兼容快照才能COMPARING。步骤编号与计算状态分别维护。"),
            row("reset_and_back", "Reset取消旧计算，恢复Initial对象、控件和观察视图，清空本轮记录/选择并回S1，只重算一次。Back取消并返回Start；再次Start无旧任务、快照或重复监听回流。基准加载保留记录和步骤见P2。"),
            row("error", "INVALID显示具体原因与恢复动作；不显示过期结果，禁用Capture，不自动重试；修正输入后按最新revision计算。"),
        ]),
        ("13. 验收与学生报告", [
            row("acceptance.core_flow", "六种情形均有真实快照；至少一组同距离异极性比较；完成Initial、操作、Capture、改值、Restore、Reset与Back/Start闭环。步骤完成需领域证据；记录齐全不能替代科学解释正确。"),
            row("acceptance.science", science, "corrected-scientific-interpretation"),
            row("acceptance.evidence", "Game View初态、首次操作后、完成态；清空后的Console；六情形快照与比较对；桌面/XR动作轨迹；公式、快照/恢复和生命周期测试。XR Simulator与真机分开记录，未实际运行不得标通过。"),
            row("report_questions", ["同号在远、中、近的方向、弯曲、对称性和稀疏形态如何变化？", "异号的正到负连接、路径和源间分布如何变化？", "同距离异配置差别如何由E_A+E_B解释？哪些观察受种子、视角或显示边界影响？"]),
        ]),
        ("14. Value完整性审计与现场检查", [
            row("audit.blank_cells", "原PDF的Value列未发现真正空白；部分Field ID/Status为空来自跨页续行。新版长值每段重复字段名和状态，不删尾部内容。", "audit-result"),
            row("audit.missing_design_values", "原文确实缺少精确极性与近中远对应、拖动自由度和基准加载记录规则：P1/P2补成明确建议。0.05m是积分步长，不是误差容差；P4补充数值方案。", "audit-result"),
            row("audit.not_applicable", "空间探针坐标、局部读数、数值形态纵轴/理论曲线与隐藏对象触发不适用；类别无数值单位。不得为填满表格创建额外功能。", "not-applicable"),
            row("current_editor_state.unity_version", "读取包内 UnityProject/ProjectSettings/ProjectVersion.txt 及锁定依赖；不继承其他电脑的编辑器版本。", "builder-runtime-check"),
            row("current_editor_state.flags", "unity_open、compiling、play_mode须在本机现场观察；无观测值时记录尚未检查，不伪造false或true。", "builder-runtime-check"),
            row("workflow_limits", {"run_batchmode": False, "hand_edit_scene_yaml": False, "no_visible_progress_minutes": 5, "unity_wait_limit_minutes": 10}, "builder-policy-reference"),
        ]),
        ("15. 内嵌来源与修订依据", [
            row("embedded_sources.source_pdf", f"用户提供的emvr33 Builder Gate1输入，20页。SHA256={SOURCE_SHA256}。必要实验事实已复制整理到本文，原附件不是Builder继续实现的文件依赖。", "source-provenance"),
            row("embedded_sources.screenshot", "截图说明：两源RK4、六情形、7步骤、8对象、桌面/XR、快照、英语UI和Common/房间复用；提出P1极性/预设、P2拖动/记录、P3科学判据。本文件完整列出其具体内容，截图也不作为外部读取依赖。", "copied-source-content"),
            row("embedded_sources.course_basis", "ECE329 Lecture 2，所选库仑场及静电叠加关系（课程公式目录对应pp.22-24）。公式、符号、单位和适用条件已内嵌第5节；没有要求读取课件目录、工作流补充库或其他任务。", "copied-course-formula-content"),
        ]),
    ]
    # Copy the experiment-independent construction contract into the PDF, rather
    # than referring the consumer to this workflow's Python source or guide.
    defaults = construction_defaults(LAB_ID)
    sections.append(("附录A. 内嵌实现职责与验证要求", [
        row(f"construction.{key}", f"{label}：{defaults[key]}", "embedded-implementation-contract")
        for key, label in CONSTRUCTION_LABELS
    ]))
    return sections


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/pdf/emvr33-builder-gate1-reviewed.pdf"))
    args = parser.parse_args()
    if hashlib.sha256(args.source_pdf.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("This review is scoped to the inspected emvr33 source PDF")
    metadata = {
        "title": "EMVR Builder Gate 1｜emvr33 修订审阅版",
        "purpose": "可移植、自包含的Builder需求包；保留原实验范围，修正冲突，列清缺失值与建议确认状态。",
        "source_design_id": "design_8e9802847723",
        "target_gate": "Gate 1需求审阅；不是Gate批准记录",
        "template_reference": "LabSpecs/templates/lab-brief.template.yaml (schema 1.0.0)",
    }
    sections = review_sections()
    content = render_builder_review_pdf(metadata, sections, [
        "Builder仅在本机有效Pack内映射本文到当前Lab的Brief。本文不批准任何Gate。",
        "P1-P3保留截图建议状态，P4是新增数值精度建议；实际采用前应由当前工作流记录确认或替代值。",
        "需要新增包外依据时，先由设计侧复制必要内容并内嵌更新PDF；不得扩大Builder读取边界。",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    args.output.with_suffix(".json").write_text(json.dumps({"document": metadata, "sections": sections}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Reviewed PDF: {len(content)} bytes; no workflow or Unity gate was executed.")


if __name__ == "__main__":
    main()
