from __future__ import annotations

from typing import Any, Protocol

from .knowledge_base import KNOWLEDGE
from .models import DesignSession, InteractionState, Stage, StepOutput


class StageGenerator(Protocol):
    def generate(self, session: DesignSession, user_message: str) -> StepOutput: ...


def _idea(session: DesignSession, user_message: str) -> str:
    idea_context = session.design_context.get("idea", {})
    if isinstance(idea_context, dict):
        for key in ("current_summary", "main_direction"):
            value = idea_context.get(key)
            if value:
                return str(value)
    control_messages = {"继续", "进入下一阶段", "确认本阶段并进入下一阶段"}
    if (
        session.current_stage is Stage.IDEA_BRAINSTORMING
        and user_message.strip()
        and user_message.strip() not in control_messages
    ):
        return user_message.strip()
    if isinstance(idea_context, dict) and idea_context.get("original"):
        return str(idea_context["original"])
    for item in reversed(session.history):
        if item.get("user_message"):
            return str(item["user_message"])
    return "尚未明确的ECE329实验想法"


def _topic_options(text: str) -> list[dict[str, Any]]:
    return KNOWLEDGE.brainstorm_options(text)


def _course_topics(text: str) -> list[str]:
    matches = KNOWLEDGE.concept_references(text)
    if matches:
        return [item["title"] for item in matches]
    return [item["direction"] for item in KNOWLEDGE.broad_entry_points()]


def _course_references(text: str) -> list[dict[str, Any]]:
    return KNOWLEDGE.concept_references(text)


def _formula_references(text: str) -> list[dict[str, Any]]:
    return KNOWLEDGE.formula_references(text)


def _visualization(idea: str, emvr: bool) -> dict[str, Any]:
    return {
        "type": "interactive_line_chart",
        "title": "ECE329理论预测参考窗口",
        "x_axis": {"label": "主要自变量", "unit": "由当前设计定义"},
        "y_axis": {"label": "主要因变量", "unit": "由当前设计定义"},
        "series": [
            {
                "id": "theory",
                "label": "理论预测",
                "points": [],
                "source": KNOWLEDGE.source_reference,
                "formula_candidates": _formula_references(idea),
            }
        ],
        "controls": [
            {"type": "slider", "binds_to": "independent_variable"},
            {"type": "button", "action": "reset_to_reference"},
        ],
        "annotations": ["需要由阶段5的理论关系生成数值或曲线"],
        "unity_binding": (
            {
                "suggested_surface": "world-space data panel",
                "updates_from": "simulation parameter controller",
            }
            if emvr
            else None
        ),
        "idea_reference": idea,
        "data_type": "theoretical_prediction",
        "measured": False,
        "disclaimer": "该窗口表示理论预测，不是实际测量数据。",
    }


class RuleBasedStageGenerator:
    """A safe fallback generator.

    Production deployments can replace it with an LLM adapter using the prompt
    packet from ``prompts.build_prompt_packet``. Stage transitions remain in the
    engine so a model cannot skip stages.
    """

    @staticmethod
    def runtime_info() -> dict[str, Any]:
        return {
            "provider": "rule_based",
            "model": None,
            "fallback_enabled": False,
        }

    def generate(self, session: DesignSession, user_message: str) -> StepOutput:
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            return self._generate_emvr(session, user_message)
        return self._generate_guided(session, user_message)

    def _generate_guided(self, session: DesignSession, user_message: str) -> StepOutput:
        stage = session.current_stage
        idea = _idea(session, user_message)
        options = _topic_options(idea)

        if stage is Stage.IDEA_BRAINSTORMING:
            return StepOutput(
                assistant_message="我们先停留在想法探索，不急着写研究问题。下面是围绕你当前兴趣可以继续发展的三个角度。",
                stage_payload={
                    "brainstorm_activity": "DIVERGE_OR_COMPARE",
                    "current_idea_summary": idea,
                    "alternative_ideas": options,
                    "course_source": KNOWLEDGE.source_reference,
                    "ready_for_next_stage": False,
                },
                student_task="这三个方向中，哪一个最接近你真正想让学生探索的现象？也可以修改其中一个。",
            )
        if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
            topics = _course_topics(idea)
            return StepOutput(
                assistant_message="当前只判断这个想法与哪一类ECE329内容联系最紧密。",
                stage_payload={
                    "candidate_course_directions": topics,
                    "course_references": _course_references(idea),
                    "idea_reference": idea,
                },
                student_task="你希望把哪一个课程方向作为实验的主要理论核心？",
            )
        if stage is Stage.LEARNING_OBJECTIVES:
            return StepOutput(
                assistant_message="先确定学习目标的重点类型，不同时写完整目标列表。",
                stage_payload={"objective_types": ["概念理解", "定量计算", "结果解释"]},
                student_task="你最希望学生通过这个实验获得哪一种能力？",
            )
        if stage is Stage.RESEARCH_QUESTION:
            return StepOutput(
                assistant_message="研究问题需要先确定一个主要变化因素。",
                stage_payload={"candidate_independent_variables": [item["direction"] for item in options]},
                student_task="你希望实验主要改变哪一个因素？",
            )
        if stage is Stage.THEORETICAL_FRAMEWORK:
            formulas = _formula_references(idea)
            return StepOutput(
                assistant_message="理论框架先从一个核心课程关系开始。",
                stage_payload={
                    "candidate_topics": _course_topics(idea),
                    "lecture_formula_candidates": formulas,
                    "formula_source_required": True,
                },
                student_task="你认为哪一个ECE329公式或边界关系最直接连接自变量和观察量？",
            )
        if stage is Stage.HYPOTHESIS:
            return StepOutput(
                assistant_message="请先依据已经选定的理论关系判断趋势，不需要写完整实验结论。",
                stage_payload={"trend_choices": ["随自变量增大而增大", "随自变量增大而减小", "可能非单调"]},
                student_task="当主要自变量增大时，你预计因变量怎样变化，理由是什么？",
            )
        if stage is Stage.CONCEPTUAL_OR_VR_SETUP:
            return StepOutput(
                assistant_message="概念结构先确定实验中负责产生电磁现象的部分。",
                stage_payload={"module_focus": "excitation_or_source"},
                student_task="这个设计需要由什么对象或条件产生目标电磁场？",
            )
        if stage is Stage.VARIABLES_AND_CONDITIONS:
            return StepOutput(
                assistant_message="变量设计从自变量开始，本轮不同时填写其他变量。",
                stage_payload={"variable_type": "independent_variable"},
                student_task="请写出你准备主动改变的一个量，并说明它的合理范围。",
            )
        if stage is Stage.CONCEPTUAL_PROCEDURE:
            return StepOutput(
                assistant_message="流程设计先建立比较所需的基准条件。",
                stage_payload={"procedure_unit": "reference_condition"},
                student_task="在改变主要变量前，你会先建立什么基准状态？",
            )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            visualization = _visualization(idea, emvr=False)
            return StepOutput(
                assistant_message="这是预期数据窗口的结构草图；它只表示理论预测，不是实测数据。",
                stage_payload={"observation_focus": "relationship_shape"},
                student_task="根据你的假设，你认为理论曲线最可能呈现什么形状？",
                visualization=visualization,
            )
        if stage is Stage.RESULT_INTERPRETATION:
            return StepOutput(
                assistant_message="本轮只考虑一种与预测不一致的情况。",
                stage_payload={"result_case": "no_clear_change"},
                student_task="如果因变量没有随自变量明显变化，你认为最值得先检查哪一个理论假设？",
            )
        if stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
            return StepOutput(
                assistant_message="先从理论局限这一个角度反思设计。",
                stage_payload={"review_dimension": "model_limitation"},
                student_task="你的设计依赖的哪个理想化假设最可能限制结论？",
            )
        return StepOutput(
            assistant_message="最后由你自己完成总结；我会逐部分检查，不会替你生成整份方案。",
            stage_payload={
                "current_summary_section": "实验想法与设计动机",
                "final_proposal_generated": False,
            },
            student_task="请先用两到三句话总结实验想研究什么，以及为什么值得研究。",
        )

    def _generate_emvr(self, session: DesignSession, user_message: str) -> StepOutput:
        stage = session.current_stage
        idea = _idea(session, user_message)
        topics = _course_topics(idea)

        if stage is Stage.IDEA_BRAINSTORMING:
            return StepOutput(
                assistant_message="已将用户的初步想法整理为适合继续发展为Unity VR模拟实验的设计起点。",
                stage_payload={
                    "original_idea": idea,
                    "normalized_idea": f"围绕“{idea}”设计ECE329交互式模拟实验",
                    "target_phenomenon": topics[0],
                    "possible_vr_interactions": [item["focus"] for item in _topic_options(idea)],
                    "design_scope": "概念设计与Unity VR模拟规划，不包含真实实验实施",
                    "course_references": _course_references(idea),
                },
                assumptions=["暂以用户提供的想法为设计边界，后续阶段再补充参数和理论模型。"],
            )
        if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
            return StepOutput(
                assistant_message="已选择兼顾ECE329相关性、理论可解释性和VR交互价值的实验方向。",
                stage_payload={
                    "primary_topic": topics[0],
                    "secondary_topics": topics[1:],
                    "selected_direction": _topic_options(idea)[0],
                    "course_references": _course_references(idea),
                    "vr_suitability": "参数可调、结果可计算、现象可空间化展示",
                    "selection_reason": "优先保留用户原始意图，并选择能够形成明确输入—输出反馈的方向。",
                },
            )
        if stage is Stage.LEARNING_OBJECTIVES:
            return StepOutput(
                assistant_message="已将课程学习与VR操作组织为一致的学习目标。",
                stage_payload={
                    "conceptual_objective": f"解释{topics[0]}中的核心物理机制",
                    "calculation_objective": "使用ECE329关系式预测参数变化造成的响应",
                    "analysis_objective": "比较参数设置、理论输出和空间可视化",
                    "vr_interaction_objective": "通过有物理意义的操作改变模型输入",
                    "observation_objective": "从数值和空间表现中解释趋势",
                },
            )
        if stage is Stage.RESEARCH_QUESTION:
            return StepOutput(
                assistant_message="已形成一个可在Unity中调整参数并观察理论输出的研究问题。",
                stage_payload={
                    "main_research_question": "在其余条件固定时，主要电磁参数的变化如何影响目标场量或传播响应？",
                    "adjustable_quantity_in_vr": "由阶段8确定的主要自变量",
                    "observable_quantity_in_vr": "理论数值、曲线和空间场表现",
                    "question_boundary": topics[0],
                },
                assumptions=["具体变量名称将在变量阶段依据用户设计固定。"],
            )
        if stage is Stage.THEORETICAL_FRAMEWORK:
            formulas = _formula_references(idea)
            return StepOutput(
                assistant_message="已把物理计算与教学可视化分开，避免把动画误认为精确数值模拟。",
                stage_payload={
                    "physical_mechanism": topics[0],
                    "core_equations": formulas,
                    "simulation_inputs": ["主要自变量", "控制参数", "基准条件"],
                    "calculated_outputs": ["目标场量或无量纲响应", "理论趋势曲线"],
                    "visual_only_elements": ["方向箭头", "波前或场线动画", "颜色强度映射"],
                    "model_type": "课程层面的解析模型或预计算数据",
                },
                warnings=["视觉动画必须标明是计算映射还是教学示意。"],
            )
        if stage is Stage.HYPOTHESIS:
            return StepOutput(
                assistant_message="已将理论假设映射为用户调整参数后可立即观察的VR反馈。",
                stage_payload={
                    "research_hypothesis": "改变主要自变量将按照ECE329理论关系引起可预测响应。",
                    "null_hypothesis": "在设计范围内，主要自变量变化不会造成可分辨响应。",
                    "expected_trend": "由阶段5确定的理论关系生成，不使用伪造实测数据。",
                    "limiting_cases": ["基准条件", "参数下限", "参数上限或模型失效边界"],
                    "vr_feedback_for_trend": ["数值更新", "曲线更新", "空间视觉编码更新"],
                },
            )
        if stage is Stage.CONCEPTUAL_OR_VR_SETUP:
            return StepOutput(
                assistant_message="已在保留用户现有场景条件的前提下，完善Unity VR模拟实验的对象、交互、物理计算和反馈设计。",
                stage_payload={
                    "user_original_design": idea,
                    "existing_context": "保留用户已有场景设定；工作流不新增或改写VR场景设计。",
                    "user_role": "通过有物理意义的交互调整参数、观察结果并进行比较",
                    "core_learning_task": f"探索参数变化与{topics[0]}响应之间的关系",
                    "unity_objects": [
                        "XR Origin与控制器（若项目已有则复用）",
                        "电磁源或激励对象",
                        "实验对象或介质对象",
                        "虚拟探测器",
                        "参数控制面板",
                        "数据与理论反馈面板",
                        "场或波的可视化对象",
                        "记录与重置组件",
                    ],
                    "interactions": [
                        {"user_action": "调整滑块或旋钮", "physical_meaning": "改变主要模型参数", "system_response": "重新计算并更新数值、曲线和视觉编码"},
                        {"user_action": "移动虚拟探测器", "physical_meaning": "改变观察位置", "system_response": "显示当前位置对应的理论场量"},
                        {"user_action": "记录当前设置", "physical_meaning": "保存一个比较条件", "system_response": "向数据面板添加理论预测记录"},
                    ],
                    "physics_layer": {
                        "user_inputs": ["主要自变量", "可选控制参数"],
                        "calculated_outputs": ["目标理论量", "派生比较量"],
                        "model_type": "解析公式或预计算数据",
                        "real_time_updates": ["数值", "曲线", "场表现"],
                        "parameter_limits": ["限制在理论模型适用范围内"],
                        "invalid_conditions": ["参数超界时停止计算并解释原因"],
                    },
                    "visualization_layer": [
                        {"visual_element": "箭头或曲线", "physical_quantity": "矢量场方向或传播方向", "calculated_or_illustrative": "必须在实现时明确"},
                        {"visual_element": "颜色或透明度", "physical_quantity": "归一化强度或衰减", "calculated_or_illustrative": "由理论输出映射"},
                    ],
                    "measurement_interface": ["当前参数及单位", "理论输出", "比较曲线", "模型假设提示", "记录与重置状态"],
                    "internal_experiment_states": ["INTRO", "BASELINE", "PARAMETER_ADJUSTMENT", "OBSERVATION", "DATA_RECORDING", "COMPARISON", "REFLECTION", "COMPLETE"],
                    "design_improvements": ["确保每个交互都有物理意义", "分开数值计算与教学动画", "保留参数基准和重置闭环"],
                },
                warnings=["本阶段不定义VR场景，也不包含可访问性与舒适性设计。"],
            )
        if stage is Stage.VARIABLES_AND_CONDITIONS:
            return StepOutput(
                assistant_message="已把实验变量映射到Unity控制、显示和模型约束。",
                stage_payload={
                    "independent_variable": {"name": "主要电磁参数", "unity_control": "有单位和范围的滑块或旋钮", "range": "限制在理论适用范围"},
                    "dependent_variable": {"name": "目标场量或传播响应", "vr_representation": "数值、曲线和空间编码"},
                    "controlled_variables": ["源条件", "几何条件", "材料或边界中未被选为自变量的参数"],
                    "reference_condition": {"purpose": "建立比较基线", "unity_action": "Reset/Reference preset"},
                    "confounding_factors": ["视觉缩放与真实单位混淆", "多个参数同时变化", "超出模型范围"],
                },
            )
        if stage is Stage.CONCEPTUAL_PROCEDURE:
            return StepOutput(
                assistant_message="已将实验逻辑整理为单一、可重复的VR学习闭环。",
                stage_payload={
                    "procedure_type": "conceptual_vr_flow",
                    "procedure_steps": ["读取目标", "建立基准", "只改变一个参数", "观察空间现象", "读取理论数值", "记录条件", "比较不同设置", "解释趋势"],
                    "comparison_logic": "每次只改变主要自变量，其余条件保持锁定。",
                    "derived_quantities": ["由阶段5理论关系定义的派生量"],
                },
            )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            return StepOutput(
                assistant_message="已生成理论预测窗口规范，并给出与Unity参数控制器联动的接口。",
                stage_payload={"trend_annotation": "由理论模型计算后标注", "unity_update_event": "OnSimulationParameterChanged"},
                visualization=_visualization(idea, emvr=True),
            )
        if stage is Stage.RESULT_INTERPRETATION:
            return StepOutput(
                assistant_message="已为不同结果情形设计物理解释和教学反馈。",
                stage_payload={
                    "if_prediction_supported": "提示理论关系与当前参数条件的一致性。",
                    "if_opposite_trend": "检查符号、边界条件、变量映射和可视化方向。",
                    "if_no_clear_change": "检查参数范围、归一化尺度和模型灵敏度。",
                    "inconclusive_conditions": ["参数超出模型范围", "多个输入同时变化", "显示尺度掩盖变化"],
                    "alternative_explanations": ["理想化假设不适用", "视觉编码与物理量映射错误"],
                },
            )
        if stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
            return StepOutput(
                assistant_message="已从课程、模型、教学和VR附加价值四个方面评价设计。",
                stage_payload={
                    "conceptual_feasibility": {"rating": "待参数确定后复核", "reasoning": "使用课程解析模型时原则上可实现"},
                    "limitations": ["理想化边界条件", "忽略部分损耗或边缘效应", "视觉缩放不等于真实尺度", "VR结果来自模型而非实测"],
                    "teaching_value": {"rating": "high_if_aligned", "learning_contribution": "让不可见场量、空间分布和参数关系可观察"},
                    "innovation": {"rating": "context_dependent", "innovative_elements": ["空间探测", "实时参数—理论反馈", "多条件叠加比较"]},
                    "vr_added_value": {"rating": "high_if_spatial", "reasoning": "只有空间观察和交互对理解有贡献时才值得使用VR"},
                    "recommended_improvements": ["删除无物理意义的交互", "优先保留一个清晰的参数—响应闭环"],
                },
            )
        return StepOutput(
            assistant_message="已汇总为面向Unity VR模拟实验的最终设计结构。",
            stage_payload={
                "proposal_status": "complete",
                "proposal_sections": [
                    "实验标题与课程联系", "学习目标", "研究问题", "理论框架", "假设与趋势",
                    "用户任务与Unity对象", "交互及物理意义", "变量和参数控制", "物理计算层",
                    "电磁现象可视化", "测量与反馈界面", "模拟实验流程", "预期数据窗口",
                    "结果解释", "教学价值与模型局限", "VR附加价值与扩展",
                ],
                "source_stage_outputs": list(session.stage_outputs.keys()),
                "final_design": {"idea": idea, "course_topic": topics[0], "stage_outputs": session.stage_outputs},
                "builder_pack_handoff": {
                    "purpose": "供EMVR_Blind_BuilderPack的Brief与Design阶段人工审阅，不自动启动或批准任何Gate。",
                    "lab_identity": {"title": "待用户确认", "lab_id": "待按Builder Pack规则确定", "domain": topics[0]},
                    "learning_goals": "来自阶段3",
                    "core_student_flow": "来自阶段9",
                    "physics_and_presets": "来自阶段5、6和8",
                    "objects_and_feedback": "来自阶段7",
                    "desktop_xr_interaction_meaning": "来自阶段7的交互—物理意义映射",
                    "initial_and_post_action_states": "来自阶段9的基准状态和第一次参数调整",
                    "unresolved_builder_inputs": [
                        "Builder Pack要求的房间、XR Prefab和场景复用决策",
                        "真实Unity API签名及Common复用审计",
                        "验收证据与Unity测试结果",
                    ],
                },
                "course_knowledge_source": KNOWLEDGE.source_reference,
            },
        )
