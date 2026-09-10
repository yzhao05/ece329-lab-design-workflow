"""Synthetic magnetic design through formula selection and production exporters.

No historical student approval is implied; no live model or Unity is used.
Run with PYTHONPATH=src: python -m tools.export_emvr34_regression
"""
from pathlib import Path
import json

from ece329_workflow.builder_defaults import build_implementation_defaults, format_implementation_defaults, record_implementation_defaults_approval
from ece329_workflow.builder_input import build_builder_gate1_input
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import UserIntent, resolved_intent
from ece329_workflow.emvr_formula_flow import handle_emvr_formula_turn, recover_topic_analysis_from_knowledge
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage


TOPIC = "长螺线管内部磁场：改变有向电流，比较中心B的大小和方向"
NUMERICAL = (
    "右手SI坐标，轴向为+z；直接代数计算H=(0,0,n*I) A/m、B=mu*H T，无需迭代和时间积分。"
    "只在物理中心(0,0,0) m采样；不生成场线或空间网格，不计算端部与外场。"
    "中心读数保留Bx、By、Bz与|B|；I=0时输出零矢量并隐藏方向箭头，不归一化零场。"
    "可视线圈长1.0 m、半径0.02 m，属于长螺线管近似；显示尺寸不能进入公式。"
    "采样边界限定中心位置；NaN/Infinity、缺失输入或位置越界时INVALID并停止本次求值。"
    "每次电流变化后取消旧revision，只求值一次并同步刷新读数和箭头，Reset同样只重算一次。"
    "误差容差采用绝对1e-9 T；独立基准：采用本实验mu=1.25663706212e-6 H/m和n=1000 1/m，"
    "I=1 A时Bz=0.00125663706212 T；I=-1 A时符号反转；I=0 A时B=0。"
)
VALUES = {
    "lab_title": "Long Solenoid Magnetic Field - Synthetic Regression",
    "lab_id": "ece329_solenoid_regression",
    "desktop_interaction_plan": "桌面鼠标操作电流滑块和Capture、Restore、Reset、Back；XR射线操作相同滑块和按钮，调用同一参数处理器。线圈仅观察，不拖动。",
    "room_spatial_requirements": "学生位于实验台前，线圈中心在台面上方，右侧参数左侧读数，留出1 m操作空间；房间采用批准预制体和均匀照明。",
    "hidden_object_lifecycle": "无",
    "initial_reset_state": "Initial电流1 A，中心磁场箭头显示，读数按公式刷新，快照为空；Reset恢复1 A、中心采样位置并清除全部快照；Restore恢复最近Capture中的电流并重算一次且保留所有快照。",
    "parameter_specifications": "唯一自变量为有向电流I，通过桌面和XR滑块可调，最小-2 A、最大2 A、默认1 A、步长0.1 A；比较组准确为-1 A、0 A、1 A、2 A。",
    "model_constants_and_media": "固定真空模型mu=1.25663706212e-6 H/m，mu_r=1无量纲；固定n=1000 1/m，N=1000匝，length=1.0 m、radius=0.02 m；正电流从+z端观察沿逆时针绕行，中心场指向物理+z；固定中心采样点与显示尺度以隔离电流影响。",
    "measurement_specifications": "中心读数定义B=mu*n*I*(0,0,1)，显示Bx、By、Bz与|B|，单位T；固定在(0,0,0) m采样，电流变化后刷新。空间探针不适用，因为仅研究长螺线管中心；不生成定量曲线。方向箭头只显示中心磁场方向，长度固定，不编码场强。",
    "numerical_model_specifications": NUMERICAL,
    "independent_variable": "有向线圈电流I，单位A",
    "observations": "中心Bz和|B|读数以及方向箭头",
    "controlled_conditions": "真空磁导率、单位长度匝数、线圈几何、中心采样位置和显示尺度固定",
    "research_hypothesis": "在长螺线管内部近似中，固定mu和n时Bz与有向电流I成正比；电流反向则中心磁场反向。",
    "expected_trend": "I从1 A变到2 A时Bz翻倍；-1 A时方向反转；0 A时中心场为零。",
    "limiting_cases": "I=-2 A、0 A、2 A；端部与外部不在模型适用范围。",
    "physical_mechanism": "均匀长螺线管内部近似H=nI，线性真空介质B=muH，变化电流改变中心场的带符号轴向分量。",
    "simulation_inputs": "I来自滑块，mu、n和中心采样位置来自固定输入契约。",
    "calculated_outputs": "H矢量A/m、B矢量T及其大小和有效性。",
    "physics_layer": "纯模型在右手SI坐标直接求中心H和B，不求有限线圈外部场。",
    "visualization_layer": "固定线圈、中心方向箭头、只读B分量和大小及快照比较；无场线、无自由探针。",
    "measurement_interface": "只读中心磁场分量和大小，使用T单位；Capture保存实际电流与计算结果。",
    "unity_objects": "固定长螺线管、中心磁场箭头、电流滑块、结果面板和快照控制按钮",
    "interactions": "调电流、Capture、Restore、Reset和Back；桌面与XR等效",
    "reference_condition": "I=1 A，Bz=0.00125663706212 T，其余输入固定。",
    "procedure_steps": [
        "Start加载Initial，确认I=1 A及中心Bz基准值，Capture保存基准快照。",
        "把电流设为2 A，保持其余输入固定，Capture保存中心B读数，比较是否为基准的两倍。",
        "把电流设为-1 A，Capture记录Bz与方向反转，再设为0 A并Capture记录零场和箭头隐藏。",
        "对照四个快照解释线性与方向关系，执行Restore后核对最近快照电流及读数一致。",
        "执行Reset，核对1 A基准恢复且快照清空；Back回到Start。",
    ],
    "comparison_logic": "仅改变I，比较1 A、2 A、-1 A、0 A的中心Bz、|B|和方向，其他参数相同。",
    "visualization_plan": "中心方向箭头与T单位读数实时刷新，四个快照并列比较；无数值曲线。",
    "trend_annotation": "固定mu和n时Bz随I线性变化，I=0为零场，反向电流使箭头反向。",
    "unity_update_event": "滑块事件 -> 校验输入 -> 单次直接求值 -> 刷新读数与箭头；Capture才写快照。",
    "expected_results": "1 A与2 A的Bz分别为0.00125663706212 T与0.00251327412424 T；-1 A为-0.00125663706212 T；0 A为0 T。",
    "acceptance_criteria": "完成1 A、2 A、-1 A、0 A四组快照，Bz与独立基准差不超过1e-9 T且方向正确；Restore后读数一致，Reset回1 A并清空记录。",
    "report_questions": "为何固定mu、n时电流翻倍使B翻倍？负电流的方向怎样确定？为什么中心近似不能预测端部外场？",
    "result_interpretation": "由H=nI与B=muH解释中心磁场的大小、零值与方向；对比证据来自理论计算快照。",
    "if_prediction_supported": "四组基准满足容差和方向关系时，支持长螺线管内部线性近似。",
    "if_opposite_trend": "检查电流符号、绕行方向、坐标变换和是否把显示坐标代入了物理模型。",
    "if_no_clear_change": "检查滑块是否更新I、revision是否一致及Results是否绑定当前B。",
    "design_rationale": "单一电流变量和四组比较能隔离中心磁场的线性与方向关系。",
    "design_value": "将有向电流、公式计算与中心磁场方向对应起来。",
    "limitations": "稳恒、线性真空与长螺线管中心近似，不包含端部、外场、磁滞或感应瞬态。",
    "conceptual_feasibility": "直接代数求值和五步操作在桌面与XR均可实现。",
    "teaching_value": "理解B=mu*n*I的大小、单位、方向及适用边界。",
    "vr_added_value": "从线圈端部观察绕行方向与中心磁场方向的空间对应。",
    "student_summary": "合成回归案例：仅验证设计交付链与PDF；不是用户批准的方案，也未在Unity运行。",
}


def formula_action(kind, content):
    return {"intent": UserIntent.ANSWER_CURRENT_QUESTION.value, "source": "SYNTHETIC_REGRESSION",
            "semantic_updates": {"emvr_formula_actions": [{"type": kind, "content": content}]}}


def magnetic_session():
    session = DesignSession(design_id="emvr34_synthetic_regression", interaction_state=InteractionState.EMVR_DIRECT)
    analysis = recover_topic_analysis_from_knowledge(TOPIC)
    analysis.update(mentioned_objects=["固定长螺线管"], changed_quantities=["有向线圈电流I"],
                    observed_quantities=["中心Bz和|B|以及方向"], specificity="SPECIFIC")
    handle_emvr_formula_turn(session, TOPIC, formula_action("SET_EMVR_TOPIC", analysis))
    output, _ = handle_emvr_formula_turn(session, "选定长螺线管及本构关系", formula_action("SELECT_EMVR_FORMULAS", {
        "primary_profile_ids": ["FD13_CURRENT_SHEET_SOLENOID"], "primary_formula_ids": ["long_solenoid_field"],
        "supporting_formula_ids": ["linear_magnetic_medium"], "objects": ["固定长螺线管"],
        "changed_quantities": ["有向线圈电流I"], "observed_quantities": ["中心Bz和|B|以及方向"],
        "operations": VALUES["procedure_steps"], "comparison_cases": ["1 A", "2 A", "-1 A", "0 A"],
        "boundary_conditions": [VALUES["limitations"]],
    }))
    output, _ = handle_emvr_formula_turn(session, "组合", resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), selected_option_id="emvr-composition:combined")
    method = output.stage_payload["experiment_methods"][0]
    handle_emvr_formula_turn(session, "采用该方法", resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), selected_option_id=method["option_id"])
    _, locked = handle_emvr_formula_turn(session, "确认这份方向", resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), complete_stage=True)
    assert locked
    apply_stage_field_updates(session, [{"field": field, "operation": "REPLACE", "value": value} for field, value in VALUES.items()],
                              stage=Stage.DESIGN_VALUE_AND_LIMITATIONS, provenance="SYNTHETIC_REGRESSION")
    contract = format_implementation_defaults(build_implementation_defaults(session))
    apply_stage_field_updates(session, [{"field": "implementation_defaults", "operation": "REPLACE", "value": contract}],
                              stage=Stage.DESIGN_VALUE_AND_LIMITATIONS, provenance="SYNTHETIC_REGRESSION")
    record_implementation_defaults_approval(session, source="SYNTHETIC_REGRESSION")
    generator = RuleBasedStageGenerator()
    for index, stage in enumerate(Stage):
        session.current_stage_index = index
        session.stage_outputs[stage.value] = generator.generate(session, "合成回归字段已提供").to_dict()
    return session


def completed_magnetic_engine():
    session = magnetic_session()
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    result = engine.process_turn(session.design_id, {"message": "保留这部分并继续", "complete_stage": True})
    if result["workflow_status"] != "complete":
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))
    return engine, engine.store.get(session.design_id)


def main():
    engine, session = completed_magnetic_engine()
    payload = build_builder_gate1_input(session)
    out = Path("output/pdf")
    out.mkdir(parents=True, exist_ok=True)
    (out / "emvr34-magnetic-builder-regression.pdf").write_bytes(engine.render_builder_input_pdf(session.design_id))
    scratch = Path("build/emvr34-regression")
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "builder-input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Synthetic magnetic Builder PDF exported; no live model or Unity execution.")


if __name__ == "__main__":
    main()
