from __future__ import annotations

import html
import re
from copy import deepcopy
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    KeepTogether,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import DesignSession, InteractionState, Stage, WorkflowStatus
from .stages import stage_title
from .design_quality import evaluate_design_quality, public_quality_review
from .dialogue_acts import stage_design_state_snapshot
from .emvr_design import EMVR_THEORY_RELATIONS, merge_emvr_structured_requirements
from .emvr_formula_flow import EMVR_DETAIL_DESIGN, formula_support_map_for_selection
from .knowledge_base import KNOWLEDGE
from .builder_requirements import (
    is_resolved_design_value,
    validate_builder_requirements,
)


_FIELD_LABELS = {
    "original_idea": "原始想法",
    "normalized_idea": "设计起点",
    "target_phenomenon": "目标现象",
    "possible_vr_interactions": "可用交互",
    "formula_direction_summary": "公式驱动的实验方向",
    "primary_formulas": "主要公式",
    "supporting_formulas": "辅助公式",
    "formula_composition_strategy": "公式组织方式",
    "selected_experiment_methods": "采用的实验方法",
    "selected_experiment_patterns": "覆盖的实验设计形式",
    "model_boundary_conditions": "公式适用边界",
    "primary_topic": "主要课程主题",
    "secondary_topics": "相关课程主题",
    "selected_direction": "设计方向",
    "course_relationship": "课程关系",
    "selection_reason": "采用理由",
    "vr_suitability": "适合VR的原因",
    "lab_title": "实验名称",
    "lab_id": "Builder实验ID",
    "conceptual_objective": "概念目标",
    "calculation_objective": "计算目标",
    "analysis_objective": "分析目标",
    "vr_interaction_objective": "交互目标",
    "observation_objective": "观察目标",
    "main_research_question": "研究问题",
    "adjustable_quantity_in_vr": "VR中可调内容",
    "observable_quantity_in_vr": "VR中可观察内容",
    "comparison_cases": "比较情形",
    "physical_mechanism": "物理机制",
    "core_equations": "核心公式",
    "formula_support_map": "理论关系与研究内容的对应",
    "theory_selection_status": "理论关系筛选状态",
    "simulation_inputs": "计算输入",
    "calculated_outputs": "计算输出",
    "visual_only_elements": "教学可视化",
    "research_hypothesis": "研究假设",
    "expected_trend": "预期趋势",
    "limiting_cases": "边界情形",
    "user_role": "学生操作",
    "core_learning_task": "核心任务",
    "unity_objects": "Unity对象",
    "object_inventory": "实验物体清单",
    "object_name": "物体名称",
    "category": "类别",
    "student_interaction": "学生交互",
    "physics_or_data_state": "物理或数据状态",
    "visual_feedback": "可见反馈",
    "required": "是否必需",
    "interactions": "交互与反馈",
    "physics_layer": "物理计算层",
    "visualization_layer": "可视化层",
    "measurement_interface": "数据显示",
    "desktop_interaction_plan": "桌面鼠标操作与VR映射",
    "room_spatial_requirements": "房间空间与相对摆放",
    "hidden_object_lifecycle": "初始隐藏与触发后状态",
    "student_constraints": "学生明确的设计约束",
    "independent_variable": "自变量",
    "student_variable_definition": "学生定义的变量与观察方式",
    "dependent_variable": "观察量",
    "controlled_variables": "控制条件",
    "reference_condition": "基准条件",
    "parameter_specifications": "参数范围、单位与步长",
    "procedure_steps": "实验流程",
    "student_required_steps": "学生要求保留的流程",
    "comparison_logic": "比较逻辑",
    "trend_annotation": "趋势标注",
    "student_visualization_requirements": "学生要求的显示内容",
    "unity_update_event": "Unity更新触发",
    "if_prediction_supported": "符合预期时",
    "if_opposite_trend": "趋势相反时",
    "if_no_clear_change": "变化不明显时",
    "student_result_interpretation": "学生提出的结果解释",
    "expected_results": "Lab特有预期结果",
    "acceptance_criteria": "Lab特有通过条件",
    "report_questions": "实验报告问题",
    "limitations": "设计局限",
    "conceptual_feasibility": "概念可行性",
    "teaching_value": "教学价值",
    "innovation": "设计特点",
    "vr_added_value": "VR附加价值",
    "recommended_improvements": "完善建议",
    "student_value_and_limit_notes": "学生提出的价值与局限",
    "builder_pack_handoff": "后续交接信息",
    "proposal_sections": "报告包含内容",
    "user_action": "学生操作",
    "physical_meaning": "物理意义",
    "system_response": "系统反馈",
    "user_inputs": "学生输入",
    "model_type": "计算方式",
    "real_time_updates": "实时更新",
    "update_policy": "更新原则",
    "parameter_limits": "参数范围",
    "invalid_conditions": "无效条件处理",
    "visual_element": "视觉元素",
    "physical_quantity": "对应物理量",
    "calculated_or_illustrative": "显示性质",
    "purpose": "用途",
    "unity_action": "Unity操作",
    "rating": "评价",
    "reasoning": "理由",
    "learning_contribution": "学习作用",
    "innovative_elements": "设计特点",
}

_REPORT_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.IDEA_BRAINSTORMING: (
        "original_idea",
        "normalized_idea",
        "target_phenomenon",
        "possible_vr_interactions",
        "formula_direction_summary",
        "primary_formulas",
        "supporting_formulas",
        "formula_composition_strategy",
        "selected_experiment_methods",
        "selected_experiment_patterns",
        "model_boundary_conditions",
    ),
    Stage.COURSE_MAPPING_AND_DIRECTION: (
        "lab_title",
        "lab_id",
        "primary_topic",
        "secondary_topics",
        "selected_direction",
        "course_relationship",
        "selection_reason",
        "vr_suitability",
    ),
    Stage.LEARNING_OBJECTIVES: (
        "conceptual_objective",
        "calculation_objective",
        "analysis_objective",
        "vr_interaction_objective",
        "observation_objective",
    ),
    Stage.RESEARCH_QUESTION: (
        "main_research_question",
        "adjustable_quantity_in_vr",
        "observable_quantity_in_vr",
        "comparison_cases",
    ),
    Stage.THEORETICAL_FRAMEWORK: (
        "physical_mechanism",
        "core_equations",
        "formula_support_map",
        "theory_selection_status",
        "simulation_inputs",
        "calculated_outputs",
        "visual_only_elements",
    ),
    Stage.HYPOTHESIS: (
        "research_hypothesis",
        "expected_trend",
        "limiting_cases",
    ),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "desktop_interaction_plan",
        "room_spatial_requirements",
        "hidden_object_lifecycle",
        "student_constraints",
        "user_role",
        "core_learning_task",
        "unity_objects",
        "object_inventory",
        "interactions",
        "physics_layer",
        "visualization_layer",
        "measurement_interface",
    ),
    Stage.VARIABLES_AND_CONDITIONS: (
        "parameter_specifications",
        "student_variable_definition",
        "independent_variable",
        "dependent_variable",
        "controlled_variables",
        "reference_condition",
    ),
    Stage.CONCEPTUAL_PROCEDURE: (
        "student_required_steps",
        "procedure_steps",
        "comparison_logic",
    ),
    Stage.EXPECTED_DATA_VISUALIZATION: (
        "student_visualization_requirements",
        "trend_annotation",
        "unity_update_event",
    ),
    Stage.RESULT_INTERPRETATION: (
        "expected_results",
        "acceptance_criteria",
        "report_questions",
        "student_result_interpretation",
        "if_prediction_supported",
        "if_opposite_trend",
        "if_no_clear_change",
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: (
        "limitations",
        "conceptual_feasibility",
        "teaching_value",
        "innovation",
        "vr_added_value",
        "recommended_improvements",
        "student_value_and_limit_notes",
    ),
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: ("proposal_sections",),
}

_LATIN_RUN = re.compile(r"[A-Za-z0-9_./:+()=\-]+(?:\s+[A-Za-z0-9_./:+()=\-]+)*")


def _paragraph_text(value: Any) -> str:
    text = str(value or "")
    parts: list[str] = []
    cursor = 0
    for match in _LATIN_RUN.finditer(text):
        parts.append(html.escape(text[cursor : match.start()]))
        parts.append(f'<font name="Helvetica">{html.escape(match.group(0))}</font>')
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts).replace("\n", "<br/>")


def _plain(value: Any, *, depth: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return {
            "high_if_aligned": "高（与学习目标一致时）",
            "high_if_spatial": "高（空间观察确有必要时）",
            "context_dependent": "取决于具体设计",
            "NEEDS_ATTENTION": "仍需完善",
            "PASS": "通过",
            "READY": "已具备条件",
        }.get(text, text)
    if depth >= 2:
        return ""
    if isinstance(value, list):
        parts = [_plain(item, depth=depth + 1) for item in value]
        return "；".join(item for item in parts if item)
    if isinstance(value, dict):
        preferred = value.get("name") or value.get("title") or value.get("focus")
        if preferred:
            detail = value.get("reasoning") or value.get("physical_meaning")
            return f"{_plain(preferred)}（{_plain(detail)}）" if detail else _plain(preferred)
        parts = []
        for key, item in value.items():
            if key in {"concept_id", "formula_id", "source_pages", "pdf_pages"}:
                continue
            rendered = _plain(item, depth=depth + 1)
            if rendered:
                parts.append(f"{_FIELD_LABELS.get(str(key), str(key))}：{rendered}")
        return "；".join(parts)
    return str(value).strip()


def effective_experiment_brief(session: DesignSession) -> dict[str, Any]:
    """Return the latest structured EMVR brief used by every final artifact.

    The formula onboarding owns the initial brief, while later stages may
    legitimately refine its object, operation, variable, observation or model
    boundary fields.  Final exports must therefore overlay the latest
    canonical field state instead of reading the Stage 1 snapshot verbatim.
    """

    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    stored = emvr.get("authoritative_experiment_brief", {})
    brief = deepcopy(stored) if isinstance(stored, dict) else {}
    requirements = merge_emvr_structured_requirements(emvr)

    def values(field: str) -> list[str]:
        value = requirements.get(field)
        if isinstance(value, list):
            return list(
                dict.fromkeys(str(item).strip() for item in value if str(item).strip())
            )
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        prior = brief.get(field, [])
        return (
            list(dict.fromkeys(str(item).strip() for item in prior if str(item).strip()))
            if isinstance(prior, list)
            else []
        )

    topic = str(
        requirements.get("direction_summary")
        or requirements.get("research_summary")
        or brief.get("topic")
        or ""
    ).strip()
    research_object = str(requirements.get("research_object") or "").strip()
    if topic:
        brief["topic"] = topic
    if research_object:
        stored_objects = values("objects")
        # Formula onboarding projects its structured object list into the
        # legacy scalar research_object using this exact separator.  Reading
        # that compatibility projection back as one object would silently
        # collapse the Builder inventory ("source、probe、surface" becomes a
        # single object). Preserve the authoritative list unless the student
        # later supplied a genuinely different object description.
        projected_objects = "、".join(stored_objects)
        brief["objects"] = (
            stored_objects
            if stored_objects and research_object == projected_objects
            else [research_object]
        )
    else:
        brief["objects"] = values("objects")
    for target, source in (
        ("operations", "required_behaviors"),
        ("changed_quantities", "changed_quantities"),
        ("observed_quantities", "observed_quantities"),
        ("comparison_cases", "comparison_cases"),
        ("boundary_conditions", "object_constraints"),
    ):
        latest = values(source)
        if latest:
            brief[target] = latest
        else:
            brief[target] = values(target)
    summary = str(requirements.get("experiment_brief") or "").strip()
    if summary:
        brief["summary"] = summary
    return brief


def effective_emvr_stage_payload(
    session: DesignSession,
    stage: Stage,
) -> dict[str, Any]:
    """Project latest canonical EMVR state onto a stage-shaped report view."""

    stored = session.stage_outputs.get(stage.value, {})
    payload = stored.get("stage_payload", {}) if isinstance(stored, dict) else {}
    payload = deepcopy(payload) if isinstance(payload, dict) else {}
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    requirements = merge_emvr_structured_requirements(emvr)
    stage_state = stage_design_state_snapshot(session)
    brief = effective_experiment_brief(session)

    def set_if(field: str, value: Any) -> None:
        if value not in (None, "", [], {}):
            payload[field] = deepcopy(value)

    if stage is Stage.IDEA_BRAINSTORMING:
        summary = str(brief.get("summary") or emvr.get("brief") or "").strip()
        set_if("normalized_idea", summary)
        set_if("formula_direction_summary", summary)
        set_if("target_phenomenon", brief.get("observed_quantities"))
        set_if("possible_vr_interactions", brief.get("operations"))
        formula_ids = list(brief.get("primary_formula_ids", []))
        supporting_ids = list(brief.get("supporting_formula_ids", []))
        formulas = {
            str(item.get("id") or ""): item
            for item in KNOWLEDGE.formulas
            if isinstance(item, dict)
        }
        set_if(
            "primary_formulas",
            [
                "：".join(
                    part
                    for part in (
                        str(formulas[item].get("name") or "").strip(),
                        str(formulas[item].get("expression") or "").strip(),
                    )
                    if part
                )
                for item in formula_ids
                if item in formulas
            ],
        )
        set_if(
            "supporting_formulas",
            [
                "：".join(
                    part
                    for part in (
                        str(formulas[item].get("name") or "").strip(),
                        str(formulas[item].get("expression") or "").strip(),
                    )
                    if part
                )
                for item in supporting_ids
                if item in formulas
            ],
        )
        composition_labels = {
            "SINGLE": "围绕一组公式形成完整实验",
            "COMBINED": "多组公式共同解释一个完整实验",
            "SEPARATE_THEN_COMBINE": "先分别设计小实验，再组合为连续任务",
        }
        set_if(
            "formula_composition_strategy",
            composition_labels.get(str(brief.get("formula_composition_strategy") or "")),
        )
        flow = emvr.get("formula_flow", {})
        flow = flow if isinstance(flow, dict) else {}
        methods = {
            str(item.get("method_id") or ""): item
            for item in flow.get("experiment_methods", [])
            if isinstance(item, dict)
        }
        method_titles = [
            str(methods[item].get("title") or "").strip()
            for item in brief.get("selected_experiment_method_ids", [])
            if item in methods and str(methods[item].get("title") or "").strip()
        ]
        patterns = {
            str(item.get("pattern_id") or ""): str(item.get("title_zh") or "").strip()
            for item in KNOWLEDGE.experiment_design_patterns
            if isinstance(item, dict)
        }
        pattern_titles = [
            patterns[item]
            for item in brief.get("selected_experiment_pattern_ids", [])
            if item in patterns and patterns[item]
        ]
        set_if("selected_experiment_methods", method_titles)
        set_if("selected_experiment_patterns", pattern_titles)
        set_if("model_boundary_conditions", brief.get("boundary_conditions"))
    elif stage is Stage.COURSE_MAPPING_AND_DIRECTION:
        set_if("lab_title", requirements.get("lab_title") or stage_state.get("lab_title"))
        set_if("lab_id", requirements.get("lab_id") or stage_state.get("lab_id"))
        set_if("selected_direction", requirements.get("experiment_brief"))
        set_if("course_relationship", requirements.get("course_relationship"))
    elif stage is Stage.LEARNING_OBJECTIVES:
        for field in (
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        ):
            set_if(field, requirements.get(field))
    elif stage is Stage.RESEARCH_QUESTION:
        set_if(
            "main_research_question",
            requirements.get("research_question") or stage_state.get("research_question"),
        )
        set_if("adjustable_quantity_in_vr", requirements.get("changed_quantities"))
        set_if("observable_quantity_in_vr", requirements.get("observed_quantities"))
        set_if("comparison_cases", requirements.get("comparison_cases"))
    elif stage is Stage.THEORETICAL_FRAMEWORK:
        selected_ids = list(
            dict.fromkeys(
                [
                    *emvr.get("selected_primary_formula_ids", []),
                    *emvr.get("selected_supporting_formula_ids", []),
                ]
            )
        )
        formula_by_id = {
            str(item.get("id") or ""): item
            for item in KNOWLEDGE.formulas
            if isinstance(item, dict)
        }
        set_if(
            "core_equations",
            [deepcopy(formula_by_id[item]) for item in selected_ids if item in formula_by_id],
        )
        set_if("formula_support_map", formula_support_map_for_selection(session))
    elif stage is Stage.HYPOTHESIS:
        hypothesis = requirements.get("hypothesis") or stage_state.get("hypothesis")
        expected_trend = (
            requirements.get("expected_phenomenon")
            or stage_state.get("expected_phenomenon")
        )
        set_if("research_hypothesis", hypothesis)
        # A hypothesis is the proposed explanation; an expected trend is the
        # observable consequence.  If the student has not independently
        # revised the latter, keep the stage generator's concrete trend rather
        # than copying the hypothesis into both final-report rows.
        set_if("expected_trend", expected_trend)
    elif stage is Stage.CONCEPTUAL_OR_VR_SETUP:
        for field in (
            "desktop_interaction_plan",
            "room_spatial_requirements",
            "hidden_object_lifecycle",
        ):
            set_if(field, requirements.get(field) or stage_state.get(field))
    elif stage is Stage.VARIABLES_AND_CONDITIONS:
        changed = requirements.get("changed_quantities") or stage_state.get("independent_variable")
        observed = requirements.get("observed_quantities") or stage_state.get("observations")
        controls = stage_state.get("controlled_conditions")
        if changed:
            set_if("independent_variable", {"name": _plain(changed)})
        if observed:
            set_if("dependent_variable", {"name": _plain(observed)})
        set_if("controlled_variables", controls)
        set_if(
            "parameter_specifications",
            requirements.get("parameter_specifications")
            or stage_state.get("parameter_specifications"),
        )
    elif stage is Stage.CONCEPTUAL_PROCEDURE:
        latest_steps = requirements.get("procedure_steps") or stage_state.get(
            "procedure_steps"
        )
        latest_steps = latest_steps if isinstance(latest_steps, list) else []
        if len(latest_steps) >= 5:
            set_if("procedure_steps", latest_steps)
        elif latest_steps:
            # A concise student description is valuable context but is not a
            # replacement for the already materialized, ordered Builder flow.
            set_if("student_required_steps", latest_steps)
    elif stage is Stage.EXPECTED_DATA_VISUALIZATION:
        set_if(
            "student_visualization_requirements",
            requirements.get("visualization_requirements")
            or stage_state.get("visualization_plan"),
        )
    elif stage is Stage.RESULT_INTERPRETATION:
        for field in ("expected_results", "acceptance_criteria", "report_questions"):
            set_if(field, requirements.get(field) or stage_state.get(field))
        set_if("student_result_interpretation", stage_state.get("result_interpretation"))
    elif stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
        set_if("limitations", requirements.get("limitations") or stage_state.get("limitations"))
    return payload


def stage_report_section(
    stage: Stage,
    payload: dict[str, Any],
    *,
    visualization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    stage_one_brief = (
        _plain(payload.get("normalized_idea"))
        if stage is Stage.IDEA_BRAINSTORMING
        else ""
    )
    for field in _REPORT_FIELDS.get(stage, ()):
        if field not in payload:
            continue
        if (
            stage is Stage.IDEA_BRAINSTORMING
            and field == "original_idea"
            and _plain(payload.get(field)) == stage_one_brief
        ):
            # The raw idea and authoritative brief are often initially
            # identical. Show it once as the design starting point instead of
            # implying that two independently completed fields exist.
            continue
        if (
            stage is Stage.IDEA_BRAINSTORMING
            and field == "target_phenomenon"
            and _plain(payload.get(field)) == stage_one_brief
        ):
            # A repeated brief is not evidence that the observable phenomenon
            # has been specified.
            continue
        if field == "unity_objects" and isinstance(payload.get("object_inventory"), list):
            continue
        if field == "object_inventory" and isinstance(payload.get(field), list):
            for index, obj in enumerate(payload[field], start=1):
                if not isinstance(obj, dict):
                    continue
                name = str(obj.get("object_name") or f"未命名物体 {index}").strip()
                details = []
                for key in (
                    "category",
                    "purpose",
                    "student_interaction",
                    "physics_or_data_state",
                    "visual_feedback",
                    "required",
                ):
                    rendered = _plain(obj.get(key))
                    if rendered:
                        details.append(f"{_FIELD_LABELS[key]}：{rendered}")
                items.append(
                    {
                        "label": f"物体 {index}｜{name}",
                        "value": "；".join(details),
                    }
                )
            continue
        if field == "formula_support_map" and isinstance(payload.get(field), list):
            links = []
            for link in payload[field]:
                if not isinstance(link, dict):
                    continue
                relation_id = str(link.get("relation_id") or "").strip()
                relation = str(
                    link.get("relation")
                    or EMVR_THEORY_RELATIONS.get(relation_id, {}).get("label")
                    or ""
                ).strip()
                supports = str(link.get("supports_design_content") or "").strip()
                if relation and supports:
                    links.append(f"{relation}用于解释：{supports}")
            if links:
                items.append(
                    {
                        "label": _FIELD_LABELS[field],
                        "value": "；".join(dict.fromkeys(links)),
                    }
                )
            continue
        if field == "theory_selection_status":
            status = str(payload.get(field) or "").strip()
            friendly_status = {
                "selected_for_current_research": "已按当前研究问题筛选",
                "course_context_fallback": "已按当前实验的课程关系筛选",
                "needs_semantic_theory_confirmation": "尚需确认与研究问题直接相关的理论关系",
            }.get(status, "")
            if friendly_status:
                items.append(
                    {"label": _FIELD_LABELS[field], "value": friendly_status}
                )
            continue
        value = _plain(payload.get(field))
        if value:
            items.append({"label": _FIELD_LABELS.get(field, field), "value": value})
    if stage is Stage.EXPECTED_DATA_VISUALIZATION and isinstance(visualization, dict):
        x_axis = _plain(visualization.get("x_axis"))
        y_axis = _plain(visualization.get("y_axis"))
        if x_axis:
            items.append({"label": "横轴", "value": x_axis})
        if y_axis:
            items.append({"label": "纵轴", "value": y_axis})
        items.append({"label": "数据性质", "value": "理论预测，不是实测数据"})
    return {
        "stage_id": stage.value,
        "title": stage_title(stage, InteractionState.EMVR_DIRECT),
        "items": items,
    }


def build_emvr_task_report(session: DesignSession) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    for stage in Stage:
        stored = session.stage_outputs.get(stage.value)
        if not isinstance(stored, dict):
            continue
        payload = effective_emvr_stage_payload(session, stage)
        section = stage_report_section(
            stage,
            payload,
            visualization=(
                stored.get("visualization")
                if isinstance(stored.get("visualization"), dict)
                else None
            ),
        )
        if section["items"]:
            sections.append(section)
    idea = session.design_context.get("emvr_design", {})
    effective_brief = effective_experiment_brief(session)
    brief = str(
        effective_brief.get("summary")
        or effective_brief.get("topic")
        or ""
    ).strip()
    if not brief:
        brief = idea.get("brief", "") if isinstance(idea, dict) else ""
    if not brief:
        original = session.design_context.get("idea", {})
        brief = original.get("current_summary") or original.get("original", "") \
            if isinstance(original, dict) else ""
    # The task report is rebuilt after every EMVR turn.  Running a final
    # completeness review while the design is still in progress would persist
    # future-stage omissions into the next prompt and could steer the
    # conversation backwards.  Only the completed workflow receives the full
    # final review.
    quality = evaluate_design_quality(
        session,
        final_review=(session.status is WorkflowStatus.COMPLETE),
    )
    quality_public = public_quality_review(quality, max_issues=8)
    causal = quality_public.get("causal_chain", {})
    causal = causal if isinstance(causal, dict) else {}
    causal_text = "；".join(
        part
        for part in (
            f"改变：{_plain(causal.get('cause'))}" if _plain(causal.get("cause")) else "",
            f"观察：{_plain(causal.get('response'))}" if _plain(causal.get("response")) else "",
            f"依据：{_plain(causal.get('mechanism'))}" if _plain(causal.get("mechanism")) else "",
            f"比较：{_plain(causal.get('comparison'))}" if _plain(causal.get("comparison")) else "",
            f"能否回答研究问题：{_plain(causal.get('answerability'))}" if _plain(causal.get("answerability")) else "",
        )
        if part
    )
    feasibility = quality_public.get("feasibility", {})
    feasibility = feasibility if isinstance(feasibility, dict) else {}
    feasibility_labels = (
        ("independent_variable_can_change", "自变量可调整"),
        ("observation_can_be_recorded", "观察量可记录"),
        ("comparison_is_defined", "基准或比较条件已定义"),
        ("controls_are_defined", "控制条件已定义"),
        ("procedure_can_test_hypothesis", "流程能够检验假设"),
        ("course_link_is_defined", "课程关系已明确"),
    )
    feasibility_text = "；".join(
        f"{label}：{'是' if feasibility.get(field) else '否'}"
        for field, label in feasibility_labels
    )
    quality_items: list[dict[str, str]] = [
        {
            "label": "因果链",
            "value": causal_text,
        },
        {
            "label": "概念可行性",
            "value": feasibility_text,
        },
    ]
    for index, issue in enumerate(quality_public.get("issues", []), start=1):
        if isinstance(issue, dict):
            quality_items.append(
                {
                    "label": f"审阅提醒 {index}",
                    "value": "；".join(
                        part
                        for part in (
                            _plain(issue.get("finding")),
                            _plain(issue.get("suggestion")),
                        )
                        if part
                    ),
                }
            )
    if quality_public.get("boundary_cases"):
        boundary_text = "；".join(
            "：".join(
                part
                for part in (
                    _plain(item.get("case")),
                    _plain(item.get("relevance")),
                )
                if part
            )
            for item in quality_public.get("boundary_cases", [])
            if isinstance(item, dict)
        )
        quality_items.append(
            {
                "label": "边界情形",
                "value": boundary_text,
            }
        )
    if quality_public.get("traceability"):
        traceability_text = "；".join(
            " — ".join(
                part
                for part in (
                    f"{_plain(item.get('design_field_label'))}：{_plain(item.get('course_item'))}",
                    _plain(item.get("purpose")),
                )
                if part
            )
            for item in quality_public.get("traceability", [])
            if isinstance(item, dict)
            and _plain(item.get("design_field_label"))
            and _plain(item.get("course_item"))
        )
        quality_items.append(
            {
                "label": "课程关系与设计来源",
                "value": traceability_text,
            }
        )
    if session.status is WorkflowStatus.COMPLETE:
        sections.append(
            {
                "stage_id": "FINAL_QUALITY_REVIEW",
                "title": "最终设计质量检查",
                "items": [item for item in quality_items if item["value"]],
            }
        )
    return {
        "title": "ECE329 EMVR 模拟实验设计报告",
        "design_id": session.design_id,
        "status": (
            "complete" if session.status is WorkflowStatus.COMPLETE else "in_progress"
        ),
        "idea": str(brief).strip(),
        "sections": sections,
        "completed_stage_count": len(session.completed_stages),
        "quality_review": quality_public,
    }


def render_emvr_report_pdf(session: DesignSession) -> bytes:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        raise ValueError("PDF summary is only available for an EMVR design")
    validate_emvr_report_completeness(session)
    report = build_emvr_task_report(session)
    if not report["sections"]:
        raise ValueError("The EMVR design does not have report content yet")

    font_name = "STSong-Light"
    try:
        pdfmetrics.getFont(font_name)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=report["title"],
        author="ECE329 Lab Studio",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ChineseTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=20,
        leading=28,
        textColor=colors.HexColor("#0B2942"),
        alignment=TA_CENTER,
        spaceAfter=10 * mm,
    )
    heading_style = ParagraphStyle(
        "ChineseHeading",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#0D7E78"),
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
    )
    body_style = ParagraphStyle(
        "ChineseBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#17334A"),
    )
    small_style = ParagraphStyle(
        "ChineseSmall",
        parent=body_style,
        fontSize=8,
        leading=12,
        textColor=colors.HexColor("#587084"),
    )

    def paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
        return Paragraph(_paragraph_text(value), style)

    story: list[Any] = [paragraph(report["title"], title_style)]
    summary_data = [
        [paragraph("设计编号", body_style), paragraph(report["design_id"], body_style)],
        [paragraph("报告状态", body_style), paragraph("已完成" if report["status"] == "complete" else "完善中", body_style)],
        [paragraph("实验想法", body_style), paragraph(report["idea"] or "尚未填写", body_style)],
    ]
    summary = Table(summary_data, colWidths=[28 * mm, 130 * mm], hAlign="LEFT")
    summary.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8F5F3")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8CCC9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([summary, Spacer(1, 5 * mm)])

    for index, section in enumerate(report["sections"], start=1):
        section_flowables: list[Any] = [
            paragraph(f"{index}. {section['title']}", heading_style)
        ]
        rows = []
        for item in section["items"]:
            rows.append(
                [
                    paragraph(str(item["label"]), body_style),
                    paragraph(str(item["value"]), body_style),
                ]
            )
        if rows:
            table = Table(rows, colWidths=[34 * mm, 124 * mm], hAlign="LEFT", repeatRows=0)
            table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font_name),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6E0DF")),
                        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0D7E78")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            section_flowables.append(table)
        story.append(KeepTogether(section_flowables))

    story.extend(
        [
            Spacer(1, 7 * mm),
            paragraph(
                "说明：本报告记录的是课程实验设计与Unity VR模拟规划，不代表已经完成Unity实现、真实测量或验收。",
                small_style,
            ),
        ]
    )

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#738696"))
        canvas.drawCentredString(A4[0] / 2 - 10 * mm, 9 * mm, "ECE329 Lab Studio")
        canvas.setFont(font_name, 8)
        canvas.drawString(A4[0] / 2 + 15 * mm, 9 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def emvr_stage_completeness_issues(
    session: DesignSession,
    stage: Stage,
) -> list[dict[str, str]]:
    """List omissions owned by one EMVR stage before it may advance.

    The final PDF validator used to discover some early-stage omissions only
    after the workflow reached its last step.  At that point the student no
    longer had a contextual question to answer, which could produce a retry
    loop.  These checks keep every Builder-facing requirement with the stage
    that creates it.
    """

    payload = effective_emvr_stage_payload(session, stage)
    issues: list[dict[str, str]] = []

    def require(field: str, label: str, question: str) -> None:
        if not is_resolved_design_value(payload.get(field)):
            issues.append({"field": field, "label": label, "question": question})

    if stage is Stage.IDEA_BRAINSTORMING:
        emvr = session.design_context.get("emvr_design", {})
        emvr = emvr if isinstance(emvr, dict) else {}
        formula_flow = emvr.get("formula_flow")
        if isinstance(formula_flow, dict):
            brief = effective_experiment_brief(session)
            if (
                formula_flow.get("phase") != EMVR_DETAIL_DESIGN
                or formula_flow.get("direction_locked") is not True
            ):
                issues.append({
                    "field": "experiment_brief",
                    "label": "已锁定的公式驱动实验方向",
                    "question": "请先确认公式、实验方法和最终实验方向，再继续细化设计。",
                })
            for field, label, question in (
                ("topic", "实验主题", "请明确这个实验最终研究的物理主题。"),
                ("primary_formula_ids", "主要公式", "请确认真正用于计算或解释本实验的主要公式。"),
                ("selected_experiment_method_ids", "实验方法", "请确认采用哪一种公式驱动的实验方法。"),
                ("objects", "研究对象", "请明确学生要操作或比较的物理对象。"),
                ("operations", "核心操作", "请明确学生在实验中的核心操作。"),
                ("changed_quantities", "变化量", "请明确实验主动改变的物理量。"),
                ("observed_quantities", "观察量", "请明确实验需要观察或记录的物理响应。"),
                ("boundary_conditions", "公式适用边界", "请明确所选公式成立所需的边界或近似条件。"),
            ):
                if not is_resolved_design_value(brief.get(field)):
                    issues.append({"field": field, "label": label, "question": question})
        return issues

    if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
        require("lab_title", "实验名称", "请为实验确定一个简洁名称。")
        require("lab_id", "Builder实验ID", "请确定符合格式要求的 Builder 实验ID。")
        require("selected_direction", "设计方向", "请确认本实验最终采用的设计方向。")
        require("course_relationship", "课程关系", "请说明相关 ECE329 概念具体支持实验中的哪部分。")
    elif stage is Stage.LEARNING_OBJECTIVES:
        for field, label in (
            ("conceptual_objective", "概念目标"),
            ("calculation_objective", "计算目标"),
            ("analysis_objective", "分析目标"),
            ("vr_interaction_objective", "交互目标"),
        ):
            require(field, label, f"请补充能够由本实验检验的{label}。")
    elif stage is Stage.RESEARCH_QUESTION:
        require("main_research_question", "研究问题", "请明确改变什么、观察什么，以及比较关系。")
        require("adjustable_quantity_in_vr", "VR中可调内容", "请明确学生在VR中实际改变的物理量。")
        require("observable_quantity_in_vr", "VR中可观察内容", "请明确用于回答研究问题的观察量。")
    elif stage is Stage.THEORETICAL_FRAMEWORK:
        require("physical_mechanism", "物理机制", "请说明所选公式如何把变化量连接到观察量。")
        require("core_equations", "理论关系", "请确认真正参与当前实验计算或解释的公式。")
        require("formula_support_map", "公式与设计内容的对应", "请说明每条公式具体支持哪个变化量、观察量或边界条件。")
        require("simulation_inputs", "理论计算输入", "请明确公式计算所需的输入量。")
        require("calculated_outputs", "理论计算输出", "请明确公式实际计算并显示的输出量。")
    elif stage is Stage.HYPOTHESIS:
        require("research_hypothesis", "研究假设", "请给出与研究问题对应的方向性假设。")
        require("expected_trend", "预期趋势", "请说明主要参数变化时预期出现的可观察趋势。")
        require("limiting_cases", "边界情形", "请至少说明基准、极限或模型失效情形。")
    elif stage is Stage.CONCEPTUAL_OR_VR_SETUP:
        for field, label, question in (
            ("desktop_interaction_plan", "桌面鼠标操作与VR映射", "请说明鼠标怎样操作哪个对象，并写清对应的VR操作。"),
            ("room_spatial_requirements", "房间空间与相对摆放", "请说明站位、对象、面板和观察空间的相对安排。"),
            ("hidden_object_lifecycle", "初始隐藏与触发后状态", "请说明隐藏对象、触发方式和出现后状态；没有则明确写“无”。"),
            ("interactions", "交互与反馈", "请明确每项学生操作及其可见反馈。"),
            ("physics_layer", "物理计算层", "请明确交互输入、理论输出和更新规则。"),
            ("visualization_layer", "可视化层", "请明确各视觉元素对应的物理量。"),
            ("measurement_interface", "数据显示", "请明确学生能够读取和记录哪些结果。"),
        ):
            require(field, label, question)
        inventory = payload.get("object_inventory")
        required_object_fields = {
            "object_name", "category", "purpose", "student_interaction",
            "physics_or_data_state", "visual_feedback", "required",
        }
        if not isinstance(inventory, list) or len(inventory) < 5 or any(
            not isinstance(item, dict)
            or any(item.get(field) in (None, "", [], {}) for field in required_object_fields)
            for item in (inventory if isinstance(inventory, list) else [])
        ):
            issues.append({
                "field": "object_inventory",
                "label": "完整Unity物体清单",
                "question": "请补全至少五类必要对象，并为每个对象说明用途、交互、状态和可见反馈。",
            })
    elif stage is Stage.VARIABLES_AND_CONDITIONS:
        for field, label, question in (
            ("independent_variable", "自变量", "请明确主要自变量及其Unity控制方式。"),
            ("dependent_variable", "观察量", "请明确与研究问题对应的观察量。"),
            ("controlled_variables", "控制条件", "请明确公平比较时保持不变的条件。"),
            ("reference_condition", "基准条件", "请明确用于比较和重置的基准状态。"),
            ("parameter_specifications", "参数范围、单位与步长", "请给出所有主要自变量的范围、单位和步长或离散选项。"),
        ):
            require(field, label, question)
    elif stage is Stage.CONCEPTUAL_PROCEDURE:
        steps = payload.get("procedure_steps")
        if not isinstance(steps, list) or len(steps) < 5 or any(not _plain(item) for item in steps):
            issues.append({
                "field": "procedure_steps",
                "label": "完整实验流程",
                "question": "请补全从基准、操作、观察、记录、比较到解释的有序实验流程。",
            })
        require("comparison_logic", "比较逻辑", "请说明各次比较如何只改变目标条件并保持其余条件一致。")
    elif stage is Stage.EXPECTED_DATA_VISUALIZATION:
        require("trend_annotation", "趋势标注", "请说明理论趋势在界面上如何标注。")
        require("unity_update_event", "Unity更新触发", "请说明哪项操作会触发理论结果与可视化刷新。")
        stored = session.stage_outputs.get(stage.value, {})
        visual = stored.get("visualization") if isinstance(stored, dict) else None
        if not _plain(payload.get("student_visualization_requirements")) and not isinstance(visual, dict):
            issues.append({
                "field": "visualization_requirements",
                "label": "显示内容",
                "question": "请明确需要保留的数值、曲线或空间可视化。",
            })
    elif stage is Stage.RESULT_INTERPRETATION:
        for field, label, question in (
            ("expected_results", "Lab特有预期结果", "请说明各主要比较情形下应出现的具体结果。"),
            ("acceptance_criteria", "Lab特有通过条件", "请明确学生必须完成的操作和可观察通过标准。"),
            ("report_questions", "实验报告问题", "请给出直接检验研究问题与理论解释的报告问题。"),
            ("if_prediction_supported", "符合预期时的解释", "请说明结果符合预期时能够支持什么结论。"),
            ("if_opposite_trend", "趋势相反时的解释", "请说明趋势相反时需要检查什么。"),
            ("if_no_clear_change", "变化不明显时的解释", "请说明变化不明显时如何区分物理结果与显示问题。"),
        ):
            require(field, label, question)
    elif stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
        for field, label, question in (
            ("limitations", "设计局限", "请明确模型假设、适用边界和可视化不能代表的内容。"),
            ("conceptual_feasibility", "概念可行性", "请核对变量能否独立改变、结果能否观察并回答研究问题。"),
            ("teaching_value", "教学价值", "请说明这套实验如何支持已确认的学习目标。"),
            ("vr_added_value", "VR附加价值", "请说明空间观察或交互相较平面展示增加了什么学习价值。"),
        ):
            require(field, label, question)
    elif stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:
        require("proposal_sections", "最终报告结构", "请完成最终报告结构汇总。")
    return issues


def validate_emvr_stage_completeness(session: DesignSession, stage: Stage) -> None:
    issues = emvr_stage_completeness_issues(session, stage)
    if issues:
        labels = "、".join(dict.fromkeys(item["label"] for item in issues))
        raise ValueError(f"本阶段仍需明确：{labels}")


def validate_emvr_report_completeness(session: DesignSession) -> None:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        raise ValueError("EMVR report validation requires an EMVR design")
    # The student-facing report renders the same Lab-specific requirements
    # collected for Builder Gate 1 (desktop/VR controls, room placement,
    # hidden-object lifecycle, parameter units, expected results, acceptance
    # criteria, and report questions).  Validate them here as well so a legacy
    # completed session cannot expose a superficially complete PDF with empty
    # requirement rows.
    validate_builder_requirements(session)
    quality_review = evaluate_design_quality(session, final_review=True)

    missing_sections = [
        issue["label"]
        for stage in Stage
        for issue in emvr_stage_completeness_issues(session, stage)
    ]

    if missing_sections:
        raise ValueError(
            "EMVR报告仍缺少：" + "、".join(dict.fromkeys(missing_sections))
        )
    major_quality_issues = [
        issue
        for issue in quality_review.get("issues", [])
        if isinstance(issue, dict)
        and str(issue.get("severity") or "").upper() == "MAJOR"
    ]
    if major_quality_issues:
        findings = [
            _plain(issue.get("finding"))
            for issue in major_quality_issues
            if _plain(issue.get("finding"))
        ]
        raise ValueError(
            "EMVR报告仍存在需要先解决的设计一致性问题："
            + "；".join(dict.fromkeys(findings))
        )
