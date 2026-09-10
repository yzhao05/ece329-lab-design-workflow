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
from .emvr_design import (
    EMVR_THEORY_RELATIONS,
    clean_emvr_field_text,
    merge_emvr_structured_requirements,
)
from .emvr_formula_flow import EMVR_DETAIL_DESIGN, formula_support_map_for_selection
from .knowledge_base import KNOWLEDGE
from .builder_requirements import (
    builder_requirement_values,
    is_resolved_design_value,
    validate_builder_requirements,
)
from .builder_defaults import (
    IMPLEMENTATION_DEFAULTS_FIELD,
    measurement_disables_probe,
    measurement_is_qualitative_only,
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
    "course_references": "课程讲义依据",
    "selection_reason": "采用理由",
    "vr_suitability": "适合VR的原因",
    "lab_title": "实验名称",
    "lab_id": "Builder实验ID",
    "builder_workspace_absolute_path": "Builder宿主项目绝对路径",
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
    "initial_reset_state": "Initial与Reset确定状态",
    "student_constraints": "学生明确的设计约束",
    "independent_variable": "自变量",
    "student_variable_definition": "学生定义的变量与观察方式",
    "dependent_variable": "观察量",
    "controlled_variables": "保持不变的控制条件",
    "reference_condition": "用于比较的基准状态",
    "parameter_specifications": "公式自变量可调契约",
    "model_constants_and_media": "模型常量、介质与固定输入",
    "procedure_steps": "实验流程",
    "student_required_steps": "学生要求保留的流程",
    "comparison_logic": "比较逻辑",
    "trend_annotation": "趋势标注",
    "student_visualization_requirements": "学生要求的显示内容",
    "unity_update_event": "Unity更新触发",
    "series_encoding": "系列区分与面板联动",
    "measurement_specifications": "指标与空间测量定义",
    "numerical_model_specifications": "数值算法与边界契约",
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
        "builder_workspace_absolute_path",
        "primary_topic",
        "secondary_topics",
        "selected_direction",
        "course_relationship",
        "course_references",
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
        "comparison_cases",
        "controlled_variables",
        "reference_condition",
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
        "initial_reset_state",
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
        "model_constants_and_media",
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
        "measurement_specifications",
        "numerical_model_specifications",
        "trend_annotation",
        "series_encoding",
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

# A report payload starts from the previously stored stage artifact.  Explicit
# EMVR clears therefore need a reverse projection; otherwise an old visible row
# survives even though the canonical field was removed.
_EMVR_CLEAR_REPORT_BINDINGS: dict[Stage, dict[str, tuple[str, ...]]] = {
    Stage.IDEA_BRAINSTORMING: {
        "experiment_brief": ("normalized_idea", "formula_direction_summary"),
        "observed_quantities": ("target_phenomenon",),
        "required_behaviors": ("possible_vr_interactions",),
        "object_constraints": ("model_boundary_conditions",),
    },
    Stage.COURSE_MAPPING_AND_DIRECTION: {
        "experiment_brief": ("selected_direction",),
        "course_relationship": ("course_relationship",),
        "lab_title": ("lab_title",),
        "lab_id": ("lab_id",),
        "builder_workspace_absolute_path": ("builder_workspace_absolute_path",),
    },
    Stage.LEARNING_OBJECTIVES: {
        field: (field,)
        for field in (
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        )
    },
    Stage.RESEARCH_QUESTION: {
        "research_question": ("main_research_question",),
        "changed_quantities": ("adjustable_quantity_in_vr",),
        "observed_quantities": ("observable_quantity_in_vr",),
        "comparison_cases": ("comparison_cases",),
    },
    Stage.THEORETICAL_FRAMEWORK: {
        field: (field,)
        for field in (
            "physical_mechanism",
            "simulation_inputs",
            "calculated_outputs",
            "reference_condition",
        )
    },
    Stage.HYPOTHESIS: {
        "hypothesis": ("research_hypothesis",),
        "research_hypothesis": ("research_hypothesis",),
        "expected_trend": ("expected_trend",),
        "limiting_cases": ("limiting_cases",),
    },
    Stage.CONCEPTUAL_OR_VR_SETUP: {
        "required_behaviors": ("interactions",),
        "desktop_interaction_plan": ("desktop_interaction_plan",),
        "room_spatial_requirements": ("room_spatial_requirements",),
        "hidden_object_lifecycle": ("hidden_object_lifecycle",),
        "initial_reset_state": ("initial_reset_state",),
        "physics_layer": ("physics_layer",),
        "visualization_layer": ("visualization_layer",),
        "measurement_interface": ("measurement_interface",),
    },
    Stage.VARIABLES_AND_CONDITIONS: {
        "changed_quantities": ("independent_variable",),
        "independent_variable": ("independent_variable",),
        "observed_quantities": ("dependent_variable",),
        "observations": ("dependent_variable",),
        "controlled_conditions": ("controlled_variables",),
        "parameter_specifications": ("parameter_specifications",),
        "model_constants_and_media": ("model_constants_and_media",),
        "reference_condition": ("reference_condition",),
    },
    Stage.CONCEPTUAL_PROCEDURE: {
        "procedure_steps": ("procedure_steps",),
        "comparison_logic": ("comparison_logic",),
    },
    Stage.EXPECTED_DATA_VISUALIZATION: {
        "visualization_requirements": ("student_visualization_requirements",),
        "visualization_plan": ("student_visualization_requirements",),
        "measurement_specifications": ("measurement_specifications",),
        "numerical_model_specifications": ("numerical_model_specifications",),
        "trend_annotation": ("trend_annotation",),
        "unity_update_event": ("unity_update_event",),
    },
    Stage.RESULT_INTERPRETATION: {
        "expected_results": ("expected_results",),
        "acceptance_criteria": ("acceptance_criteria",),
        "report_questions": ("report_questions",),
        "result_interpretation": ("student_result_interpretation",),
        "if_prediction_supported": ("if_prediction_supported",),
        "if_opposite_trend": ("if_opposite_trend",),
        "if_no_clear_change": ("if_no_clear_change",),
    },
    Stage.DESIGN_VALUE_AND_LIMITATIONS: {
        "limitations": ("limitations",),
        "conceptual_feasibility": ("conceptual_feasibility",),
        "teaching_value": ("teaching_value",),
        "vr_added_value": ("vr_added_value",),
    },
}

# These fields describe a previous workflow response, not the experiment.  A
# stored stage payload may contain them because the engine decorates each turn
# before persistence.  Carrying them into a later report projection can embed
# an obsolete report inside the new report and make removed values appear to
# survive through that nested snapshot.
_TRANSIENT_REPORT_PAYLOAD_FIELDS = frozenset(
    {
        "emvr_report_section",
        "builder_handoff_status",
        "quality_review",
        "design_version",
        "design_state",
        "stage_design_state",
        "pending_action",
        "decision_options",
        "awaiting_user_design_input",
        "builder_requirement_field",
        "default_implementation_contract",
    }
)

_LATIN_RUN = re.compile(
    r"[A-Za-z0-9_./:+()=\-*'|^<>]+(?:\s+[A-Za-z0-9_./:+()=\-*'|^<>]+)*"
)


def _formal_report_text(value: Any) -> str:
    """Use formal third-person language in the EMVR report artifact."""

    text = str(value or "").strip()
    if not text:
        return ""
    for source, target in (
        ("你的", "学生的"),
        ("你所", "学生所"),
        ("你定义的", "学生定义的"),
        ("你定义", "学生定义"),
        ("你提出的", "学生提出的"),
        ("你提出", "学生提出"),
        ("你", "学生"),
    ):
        text = text.replace(source, target)
    return _pdf_safe_formula_text(text)


_FORMULA_CHARACTER_REPLACEMENTS = {
    "εrε₀": "epsilon_r * epsilon_0",
    "εᵣε₀": "epsilon_r * epsilon_0",
    "εr": "epsilon_r",
    "εᵣ": "epsilon_r",
    "₀": "_0",
    "₁": "_1",
    "₂": "_2",
    "₃": "_3",
    "ᵢ": "_i",
    "⁰": "^0",
    "¹": "^1",
    "²": "^2",
    "³": "^3",
    "π": "*pi*",
    "ε": "epsilon",
    "ρ": "rho",
    "λ": "lambda",
    "μ": "mu",
    "σ": "sigma",
    "Σ": "sum",
    "∇": "nabla",
    "∂": "d",
    "×": " x ",
    "·": " dot ",
    "≫": ">>",
    "≪": "<<",
    "≈": "~=",
    "−": "-",
    "′": "'",
}


def _pdf_safe_formula_text(value: Any) -> str:
    """Replace formula glyphs that CID fonts render unreliably."""

    text = str(value or "")
    superscript_translation = str.maketrans(
        "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻",
        "0123456789+-",
    )
    text = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+",
        lambda match: (
            f"^{match.group(0).translate(superscript_translation)}"
            if len(match.group(0)) == 1 and match.group(0).isdigit()
            else f"^({match.group(0).translate(superscript_translation)})"
        ),
        text,
    )
    for source, target in _FORMULA_CHARACTER_REPLACEMENTS.items():
        text = text.replace(source, target)
    return text


def _formula_expression_for_report(value: Any) -> str:
    """Render formula indices and operators without CID-font glyph loss."""

    text = _pdf_safe_formula_text(value).strip()
    text = re.sub(r"\s*\*\s*", "*", text).strip("*")
    text = re.sub(r"(?<=[A-Za-z0-9_)])\s+(?=[|(])", "*", text)
    return re.sub(r"\s+", " ", text).strip()


def _unity_project_absolute_path(builder_root: str) -> str:
    """Resolve the Unity child path without discarding the confirmed host root."""

    root = str(builder_root or "").rstrip("\\/")
    separator = "\\" if re.match(r"^(?:[A-Za-z]:|\\\\)", root) else "/"
    return f"{root}{separator}UnityProject" if root else ""


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


def _experiment_brief_overview(brief: dict[str, Any]) -> str:
    """Render one readable overview from structured fields, not edit history."""

    parts: list[str] = []
    for label, field in (
        ("主题", "topic"),
        ("研究对象", "objects"),
        ("核心操作", "operations"),
        ("变化条件", "changed_quantities"),
        ("观察内容", "observed_quantities"),
        ("比较情形", "comparison_cases"),
    ):
        value = _plain(brief.get(field))
        if value:
            parts.append(f"{label}：{value}")
    return "；".join(parts)


_NUMBERED_REPORT_FIELDS = {
    "procedure_steps",
    "expected_results",
    "acceptance_criteria",
    "report_questions",
    "student_visualization_requirements",
}


def _strip_report_list_marker(value: str) -> str:
    return re.sub(r"^\s*(?:\d+[.、）)]|[-•])\s*", "", value).strip()


def _coalesce_numbered_report_items(field: str, items: list[str]) -> list[str]:
    """Restore semantic items that older model output split into fragments."""

    cleaned = [_strip_report_list_marker(item) for item in items if item.strip()]
    if field == "expected_results":
        result: list[str] = []
        context = ""
        for item in cleaned:
            compact = item.rstrip("：:").strip()
            if re.search(r"(?:比较情景|比较情形|预期结果)$", compact) and len(compact) < 30:
                continue
            if compact in {"同种电荷", "异种电荷", "同号电荷", "异号电荷"}:
                context = compact
                continue
            if compact in {"支持研究假设的现象", "支持假设的现象", "判定依据"}:
                context = "判定依据"
                continue
            if context in {"同种电荷", "异种电荷", "同号电荷", "异号电荷"} and re.match(
                r"^(?:远|中|近)距离[：:]", item
            ):
                result.append(f"{context} - {item}")
            elif context == "判定依据":
                result.append(f"判定依据 - {item}")
            else:
                result.append(item)
        return result
    if field == "acceptance_criteria":
        groups: list[str] = []
        current = ""
        for item in cleaned:
            starts_group = bool(re.match(r"^(?:完成条件|通过条件|验收条件)[：:]", item))
            if starts_group and current:
                groups.append(current)
                current = item
            elif current:
                current = f"{current} {item}"
            else:
                current = item
        if current:
            groups.append(current)
        return groups
    if field == "report_questions":
        questions: list[str] = []
        for item in cleaned:
            continuation = bool(
                re.match(r"^(?:特别是|这些差异|上述差异|其中|该差异)", item)
            )
            if continuation and questions:
                questions[-1] = f"{questions[-1]} {item}"
            else:
                questions.append(item)
        return questions
    return cleaned


def _readable_report_value(field: str, raw: Any) -> str:
    """Preserve long student content while presenting it as scannable items."""

    raw_is_list = isinstance(raw, list)
    values = raw if raw_is_list else [raw]
    items: list[str] = []
    for value in values:
        text = _plain(value).strip()
        if not text:
            continue
        if field in _NUMBERED_REPORT_FIELDS and not raw_is_list:
            # A model or legacy session may store several questions/results in
            # one scalar even when the text is shorter than the long-text
            # threshold. Split on actual sentence endings and visible list
            # numbers so every requested item remains readable.
            expanded = re.sub(r"\s+(?=\d+[.、）)]\s*)", "\n", text)
            sentences = [
                item.strip()
                for item in re.split(r"\n+|(?<=[。！？?；;])\s*", expanded)
                if item.strip()
            ]
            items.extend(sentences if len(sentences) > 1 else [text])
        elif field in _NUMBERED_REPORT_FIELDS:
            # Structured list entries are already semantic units. Splitting a
            # procedure step again at an internal semicolon produced orphaned
            # rows such as “电荷类型（同种/异种）” in the final PDF.
            items.append(text)
        elif len(text) >= 180:
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[。！？?])\s*", text)
                if item.strip()
            ]
            items.extend(sentences if len(sentences) > 1 else [text])
        else:
            items.append(text)
    if field in _NUMBERED_REPORT_FIELDS:
        items = _coalesce_numbered_report_items(field, items)
    if field in _NUMBERED_REPORT_FIELDS and len(items) > 1:
        return "\n".join(
            f"{index}. {item}" for index, item in enumerate(items, start=1)
        )
    return _formal_report_text("；".join(items))


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
    explicitly_cleared = {
        str(field)
        for field in emvr.get("explicitly_cleared_fields", [])
        if str(field)
    } if isinstance(emvr.get("explicitly_cleared_fields", []), list) else set()
    raw_stage_state = session.design_context.get("stage_design_state", {})
    raw_stage_state = raw_stage_state if isinstance(raw_stage_state, dict) else {}
    stage_explicitly_cleared = raw_stage_state.get("explicitly_cleared_fields", [])
    if isinstance(stage_explicitly_cleared, list):
        explicitly_cleared.update(
            str(field) for field in stage_explicitly_cleared if str(field)
        )

    def values(field: str) -> list[str]:
        # A field-level CLEAR is newer and more authoritative than the
        # formula-onboarding brief.  Falling back to that brief here used to
        # resurrect deleted objects, operations, variables and observations in
        # the task report and Builder inventory.
        if field in explicitly_cleared:
            return []
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

    topic_candidates = (
        requirements.get("direction_summary")
        if "direction_summary" not in explicitly_cleared
        else None,
        requirements.get("research_summary")
        if "research_summary" not in explicitly_cleared
        else None,
        brief.get("topic")
        if not {"direction_summary", "research_summary"} <= explicitly_cleared
        else None,
    )
    topic = clean_emvr_field_text(
        "direction_summary",
        str(next((item for item in topic_candidates if item), "")).strip(),
    )
    research_object = (
        str(requirements.get("research_object") or "").strip()
        if "research_object" not in explicitly_cleared
        else ""
    )
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
    elif "research_object" in explicitly_cleared:
        brief["objects"] = []
    else:
        brief["objects"] = values("objects")
    generic_objects = {
        "研究对象",
        "实验对象",
        "物理对象",
        "公式研究对象",
        "核心研究对象",
        "与已确认公式对应的场源、材料或边界对象",
        "与已确认公式对应的场源、材料或边界对象、可移动测量探针",
        "source object",
        "research object",
    }
    current_objects = [
        str(item).strip()
        for item in brief.get("objects", [])
        if str(item).strip()
    ]
    if (
        "research_object" not in explicitly_cleared
        and (
            not current_objects
            or all(item.lower() in generic_objects for item in current_objects)
        )
    ):
        flow = emvr.get("formula_flow", {})
        flow = flow if isinstance(flow, dict) else {}
        topic_analysis = flow.get("topic_analysis", {})
        topic_analysis = topic_analysis if isinstance(topic_analysis, dict) else {}
        mentioned = [
            str(item).strip()
            for item in topic_analysis.get("mentioned_objects", [])
            if str(item).strip()
            and str(item).strip().lower() not in generic_objects
        ]
        formula_ids = {
            str(item).strip()
            for key in ("primary_formula_ids", "supporting_formula_ids")
            for item in brief.get(key, [])
            if str(item).strip()
        }
        if mentioned:
            brief["objects"] = list(dict.fromkeys(mentioned))
        elif "coulomb_point_charge" in formula_ids:
            brief["objects"] = ["两个点电荷"]
    for target, source in (
        ("operations", "required_behaviors"),
        ("changed_quantities", "changed_quantities"),
        ("observed_quantities", "observed_quantities"),
        ("comparison_cases", "comparison_cases"),
        ("boundary_conditions", "object_constraints"),
    ):
        latest = values(source)
        if source in explicitly_cleared:
            brief[target] = []
        elif latest:
            brief[target] = latest
        else:
            brief[target] = values(target)
    summary = (
        str(requirements.get("experiment_brief") or "").strip()
        if "experiment_brief" not in explicitly_cleared
        else ""
    )
    if "experiment_brief" in explicitly_cleared:
        brief["summary"] = ""
    elif summary:
        brief["summary"] = summary
    # Rebuild the display summary from the latest structured causal fields.
    # This repairs reports created before those fields were separated, whose
    # saved summary may contain every objective and revision instruction in one
    # unreadable sentence.
    structured_summary = _experiment_brief_overview(brief)
    summary_is_stitched = bool(
        len(str(brief.get("summary") or "")) > 240
        or re.search(
            r"(?:学习目标|概念目标|交互目标|需要明确|应当|更具体)",
            str(brief.get("summary") or ""),
        )
    )
    if structured_summary and (not brief.get("summary") or summary_is_stitched):
        brief["summary"] = structured_summary
    return brief


def _emvr_report_context(
    session: DesignSession,
    requirements: dict[str, Any],
) -> dict[str, Any]:
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    flow = emvr.get("formula_flow", {})
    selection = flow.get("formula_selection", {}) if isinstance(flow, dict) else {}
    formula_ids = {
        str(item)
        for item in [
            *emvr.get("selected_primary_formula_ids", []),
            *emvr.get("selected_supporting_formula_ids", []),
            *(selection.get("primary_formula_ids", []) if isinstance(selection, dict) else []),
            *(selection.get("supporting_formula_ids", []) if isinstance(selection, dict) else []),
        ]
        if str(item)
    }
    changed = _plain(requirements.get("changed_quantities")) or "已确认的自变量"
    observed = _plain(requirements.get("observed_quantities")) or "已确认的观察量"
    comparisons = _plain(requirements.get("comparison_cases"))
    specifications = _plain(requirements.get("parameter_specifications"))
    return {
        "formula_ids": formula_ids,
        "point_charge": "coulomb_point_charge" in formula_ids,
        "changed": changed,
        "observed": observed,
        "comparisons": comparisons,
        "specifications": specifications,
    }


def _point_charge_limit_cases(context: dict[str, Any]) -> list[str]:
    specifications = str(context.get("specifications") or "").strip()
    limits = []
    if specifications:
        limits.append(f"参数端点：{specifications}")
    limits.extend(
        [
            "点电荷近似：电荷源可视尺寸必须远小于两电荷间距及采样点到电荷的距离",
            "奇点处理：采样点进入电荷源排除半径时不计算无限大场强，并显示超界提示",
            "静电边界：只比较静止电荷的稳态场；拖动过程中的辐射与瞬态传播不属于本模型",
        ]
    )
    return limits


def _specific_emvr_limitations(context: dict[str, Any]) -> list[str]:
    if context.get("point_charge"):
        return [
            "两个电荷按理想点电荷处理，电荷源半径相对间距和观察距离不可忽略时，库仑点电荷公式不再适用",
            "介质按均匀、线性、各向同性且介电常数固定处理，不模拟非均匀介质、极化边界或导体表面电荷重分布",
            "拖动结束后的场线来自静电场重算，不表示移动电荷产生的辐射或电磁波传播过程",
            "场线数量、粗细和颜色是固定采样密度下的视觉编码，不是粒子轨迹，也不是场强的直接实测值",
            "电荷源附近设置排除半径以避开 1/r^2 奇点；超出参数范围时停止更新并显示模型边界提示",
        ]
    changed = str(context.get("changed") or "自变量")
    observed = str(context.get("observed") or "观察量")
    return [
        f"模型仅在报告列出的参数范围和边界条件内描述{changed}与{observed}的关系",
        "可视化由课程理论模型计算，不代表真实测量或高精度工程仿真",
        "颜色、缩放和动画只承担结果编码，不能替代理论量的数值、单位与适用条件",
    ]


def effective_emvr_stage_payload(
    session: DesignSession,
    stage: Stage,
) -> dict[str, Any]:
    """Project latest canonical EMVR state onto a stage-shaped report view."""

    stored = session.stage_outputs.get(stage.value, {})
    payload = stored.get("stage_payload", {}) if isinstance(stored, dict) else {}
    payload = deepcopy(payload) if isinstance(payload, dict) else {}
    for transient_field in _TRANSIENT_REPORT_PAYLOAD_FIELDS:
        payload.pop(transient_field, None)
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    requirements = merge_emvr_structured_requirements(emvr)
    explicitly_cleared = {
        str(field)
        for field in emvr.get("explicitly_cleared_fields", [])
        if str(field)
    } if isinstance(emvr.get("explicitly_cleared_fields", []), list) else set()
    raw_stage_state = session.design_context.get("stage_design_state", {})
    raw_stage_state = raw_stage_state if isinstance(raw_stage_state, dict) else {}
    stage_explicitly_cleared = raw_stage_state.get("explicitly_cleared_fields", [])
    if isinstance(stage_explicitly_cleared, list):
        explicitly_cleared.update(
            str(field) for field in stage_explicitly_cleared if str(field)
        )
    clear_bindings = _EMVR_CLEAR_REPORT_BINDINGS.get(stage, {})
    for canonical_field in explicitly_cleared:
        for report_field in clear_bindings.get(canonical_field, ()):
            payload.pop(report_field, None)
    stage_state = stage_design_state_snapshot(session)
    brief = effective_experiment_brief(session)
    context = _emvr_report_context(session, requirements)

    def set_if(field: str, value: Any) -> None:
        if value not in (None, "", [], {}):
            payload[field] = deepcopy(value)

    def set_bound(field: str, canonical_field: str, value: Any) -> None:
        """Project a canonical field without reviving explicitly cleared data."""

        if canonical_field in explicitly_cleared:
            payload.pop(field, None)
            return
        set_if(field, value)

    if stage is Stage.IDEA_BRAINSTORMING:
        summary = _experiment_brief_overview(brief)
        topic = str(brief.get("topic") or "").strip()
        set_bound("normalized_idea", "experiment_brief", topic or summary)
        set_bound("formula_direction_summary", "experiment_brief", summary)
        set_bound(
            "target_phenomenon",
            "observed_quantities",
            brief.get("observed_quantities"),
        )
        set_bound(
            "possible_vr_interactions",
            "required_behaviors",
            requirements.get("desktop_interaction_plan") or brief.get("operations"),
        )
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
                        _formula_expression_for_report(
                            formulas[item].get("expression")
                        ),
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
                        _formula_expression_for_report(
                            formulas[item].get("expression")
                        ),
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
        set_bound(
            "model_boundary_conditions",
            "object_constraints",
            brief.get("boundary_conditions"),
        )
    elif stage is Stage.COURSE_MAPPING_AND_DIRECTION:
        set_bound(
            "lab_title",
            "lab_title",
            stage_state.get("lab_title") or requirements.get("lab_title"),
        )
        set_bound(
            "lab_id",
            "lab_id",
            stage_state.get("lab_id") or requirements.get("lab_id"),
        )
        set_bound(
            "builder_workspace_absolute_path",
            "builder_workspace_absolute_path",
            stage_state.get("builder_workspace_absolute_path")
            or requirements.get("builder_workspace_absolute_path"),
        )
        set_bound(
            "selected_direction",
            "experiment_brief",
            _experiment_brief_overview(brief),
        )
        set_bound(
            "course_relationship",
            "course_relationship",
            requirements.get("course_relationship"),
        )
        formula_records = {
            str(item.get("id") or ""): item
            for item in KNOWLEDGE.formulas
            if isinstance(item, dict)
        }
        concept_ids = [
            str(concept_id)
            for formula_id in sorted(context["formula_ids"])
            for concept_id in formula_records.get(formula_id, {}).get("concept_ids", [])
            if str(concept_id)
        ]
        formula_course_references = KNOWLEDGE.concept_references_for_ids(
            list(dict.fromkeys(concept_ids))
        )
        if formula_course_references:
            payload["course_references"] = formula_course_references
        changed = str(context["changed"])
        observed = str(context["observed"])
        if context["point_charge"]:
            payload["primary_topic"] = "Static electric fields - Coulomb's and Gauss's laws"
            payload["secondary_topics"] = ["Coulomb field", "Electric-field superposition"]
            if (
                "course_relationship" not in explicitly_cleared
                and not _plain(payload.get("course_relationship"))
            ):
                payload["course_relationship"] = (
                    "ECE329 Lecture 2 的库仑点电荷场给出单个场源贡献，矢量叠加原理将两个"
                    "场源贡献合成为 E_total；合场的空间方向直接决定场线弯曲、连接与中间低密度区。"
                )
            payload["selection_reason"] = (
                f"以{changed}作为模型输入，分别计算两个点电荷的库仑场并进行矢量叠加，"
                f"再用{observed}检验同号与异号配置的空间差异。"
            )
            payload["vr_suitability"] = (
                "点电荷可直接抓取和定位，间距可连续读取，电荷符号可离散切换；"
                "每次状态变化均能触发合场、场线和对比曲线同步重算。"
            )
    elif stage is Stage.LEARNING_OBJECTIVES:
        for field in (
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        ):
            set_bound(field, field, requirements.get(field))
        if context["point_charge"]:
            changed = str(context["changed"])
            observed = str(context["observed"])
            replacements = {
                "conceptual_objective": (
                    "解释库仑点电荷场与矢量叠加如何共同决定同号和异号电荷的场线拓扑，"
                    "并说明距离变化为何增强或减弱中间区域的场线弯曲与连接特征。"
                ),
                "calculation_objective": (
                    "使用 E(r)=Q(r-r_0)/(4*pi*epsilon_0*|r-r_0|^3) 计算单个点电荷场，"
                    "并按 E_total=sum_i E_i 得到空间采样点的合场矢量。"
                ),
                "analysis_objective": (
                    f"在相同距离下比较同号与异号配置的{observed}，并在同一电荷配置下分析"
                    f"{changed}变化造成的场线弯曲、连接或空白区差异。"
                ),
                "vr_interaction_objective": (
                    f"通过抓取点电荷改变{changed}，通过控制面板切换电荷符号配置，"
                    "并在每次操作后读取距离、合场状态与对比快照。"
                ),
                "observation_objective": (
                    f"依据{observed}以及同一色标下的合场方向和强度，判断观察结果是否"
                    "符合库仑定律与矢量叠加的预测。"
                ),
            }
            for field, replacement in replacements.items():
                if field in explicitly_cleared:
                    continue
                current = _plain(payload.get(field))
                if not current or any(
                    marker in current
                    for marker in (
                        "当前研究问题",
                        "学生定义",
                        "预期关系",
                        "Vector fields",
                        "核心物理机制",
                    )
                ):
                    payload[field] = replacement
    elif stage is Stage.RESEARCH_QUESTION:
        set_bound(
            "main_research_question",
            "research_question",
            requirements.get("research_question") or stage_state.get("research_question"),
        )
        set_bound(
            "adjustable_quantity_in_vr",
            "changed_quantities",
            requirements.get("changed_quantities"),
        )
        set_bound(
            "observable_quantity_in_vr",
            "observed_quantities",
            requirements.get("observed_quantities"),
        )
        set_bound(
            "comparison_cases",
            "comparison_cases",
            requirements.get("comparison_cases"),
        )
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
        support_map = formula_support_map_for_selection(session)
        set_if("formula_support_map", support_map)
        generated_mechanism = payload.get("physical_mechanism")
        mechanism = stage_state.get("physical_mechanism")
        if _plain(mechanism).replace("。", "") in {
            "不确定", "暂时不确定", "我不确定", "不知道", "暂时不知道", "我不知道", "不清楚",
        }:
            # Older EMVR sessions could persist a request for help as if it
            # were the student's physical explanation.  Rebuild that row from
            # the already selected formula-to-design links instead.
            payload.pop("physical_mechanism", None)
            mechanism = "；".join(
                f"{_plain(item.get('relation'))}用于解释{_plain(item.get('supports_design_content'))}"
                for item in support_map
                if isinstance(item, dict)
                and _plain(item.get("relation"))
                and _plain(item.get("supports_design_content"))
            )
            if not mechanism and _plain(generated_mechanism).replace("。", "") not in {
                "不确定", "暂时不确定", "我不确定", "不知道", "暂时不知道", "我不知道", "不清楚",
            }:
                mechanism = generated_mechanism
        set_if("physical_mechanism", mechanism)
        for field in (
            "simulation_inputs",
            "comparison_cases",
            "controlled_variables",
            "reference_condition",
            "calculated_outputs",
        ):
            set_if(field, stage_state.get(field))
        if context["point_charge"]:
            payload["visual_only_elements"] = [
                "合场方向箭头：方向直接取自每个采样点的 E_total 矢量",
                "电场线：从正电荷出发并终止于负电荷或显示边界，使用固定种子数量以保证比较公平",
                "场强颜色映射：按统一色标显示 |E_total| 的归一化区间，所有距离和电荷配置共用同一量程",
            ]
    elif stage is Stage.HYPOTHESIS:
        hypothesis = (
            stage_state.get("research_hypothesis")
            or requirements.get("hypothesis")
            or stage_state.get("hypothesis")
        )
        expected_trend = (
            stage_state.get("expected_trend")
            or requirements.get("expected_phenomenon")
            or stage_state.get("expected_phenomenon")
        )
        set_if("research_hypothesis", hypothesis)
        # A hypothesis is the proposed explanation; an expected trend is the
        # observable consequence.  If the student has not independently
        # revised the latter, keep the stage generator's concrete trend rather
        # than copying the hypothesis into both final-report rows.
        set_if("expected_trend", expected_trend)
        set_if("limiting_cases", stage_state.get("limiting_cases"))
        if (
            "expected_trend" not in explicitly_cleared
            and "具体方向以已确认假设为准" in _plain(payload.get("expected_trend"))
        ):
            payload["expected_trend"] = (
                "距离由远到近时，同号电荷中间的场线向外弯曲并形成扩大的低密度区；"
                "异号电荷的场线由正电荷连接至负电荷，连接密度随距离缩短而增加。"
                if context["point_charge"]
                else (
                    f"当{context['changed']}变化时，{context['observed']}应出现可与基准状态"
                    "逐项比较的方向性变化。"
                )
            )
        if context["point_charge"] and "limiting_cases" not in explicitly_cleared and (
            not payload.get("limiting_cases")
            or _plain(payload.get("limiting_cases"))
            in {"基准条件；参数下限；参数上限或模型失效边界", "基准条件;参数下限;参数上限或模型失效边界"}
        ):
            payload["limiting_cases"] = _point_charge_limit_cases(context)
    elif stage is Stage.CONCEPTUAL_OR_VR_SETUP:
        for field in (
            "desktop_interaction_plan",
            "room_spatial_requirements",
            "hidden_object_lifecycle",
            "initial_reset_state",
        ):
            set_bound(field, field, stage_state.get(field) or requirements.get(field))
        for field in ("physics_layer", "visualization_layer", "measurement_interface"):
            set_if(field, stage_state.get(field))
        set_bound(
            "interactions",
            "required_behaviors",
            stage_state.get("interactions")
            or payload.get("interactions")
            or requirements.get("required_behaviors"),
        )
        measurement_contract = (
            stage_state.get("measurement_specifications")
            or requirements.get("measurement_specifications")
        )
        if context["point_charge"]:
            physics_cleared = "physics_layer" in explicitly_cleared
            visualization_cleared = "visualization_layer" in explicitly_cleared
            measurement_interface_cleared = (
                "measurement_interface" in explicitly_cleared
            )
            if not physics_cleared:
                payload.setdefault(
                    "physics_layer",
                    {
                        "user_inputs": "两点电荷世界坐标、电荷量大小、电荷符号配置与由坐标计算的间距 d",
                        "calculated_outputs": "规则采样点上的 E_A、E_B、E_total=E_A+E_B、|E_total| 及由合场方向积分得到的场线",
                        "model_type": "库仑点电荷场逐点计算与矢量叠加；场线仅由当前合场数值积分生成",
                        "real_time_updates": "点电荷释放或符号切换后重算场网格、场线、方向箭头、统一色标和对比曲线",
                        "parameter_limits": context["specifications"] or "使用变量章节确认的距离范围、单位和离散电荷配置",
                        "invalid_conditions": "采样点进入电荷排除半径或间距超界时停止该点计算，保留上一有效状态并显示原因",
                    },
                )
            if not visualization_cleared:
                payload.setdefault(
                    "visualization_layer",
                    [
                        {
                            "visual_element": "电场线与箭头",
                            "physical_quantity": "E_total 的局部方向及同号排斥、异号连接的拓扑形态",
                            "calculated_or_illustrative": "由同一组库仑场采样值实时生成；场线种子数量固定",
                        },
                        {
                            "visual_element": "统一颜色标尺",
                            "physical_quantity": "|E_total| 的归一化强度区间",
                            "calculated_or_illustrative": "同号/异号及远/中/近距离共用同一量程，避免自动缩放造成假差异",
                        },
                    ],
                )
            if (
                measurement_is_qualitative_only(measurement_contract)
                and not visualization_cleared
            ):
                payload["visualization_layer"] = [
                    {
                        "visual_element": "统一视角的电场线与方向箭头",
                        "physical_quantity": "E_total 的方向和空间拓扑",
                        "calculated_or_illustrative": (
                            "由同一组库仑场采样值生成；固定场线种子数、色标和视角，"
                            "只按已确认类别作定性比较"
                        ),
                    },
                    {
                        "visual_element": "定性快照比较面板",
                        "physical_quantity": "连接、弯曲、发散或低密度区等已确认分类",
                        "calculated_or_illustrative": "不创建数值纵轴、形态指标或理论曲线",
                    },
                ]
                physics_layer = payload.get("physics_layer")
                if isinstance(physics_layer, dict):
                    physics_layer["real_time_updates"] = (
                        "点电荷释放或符号切换后重算场网格、场线、方向箭头、统一色标和结果面板"
                    )
            if (
                measurement_disables_probe(measurement_contract)
                and not measurement_interface_cleared
            ):
                payload["measurement_interface"] = [
                    "当前自变量、比较配置、快照编号与模型有效性状态",
                    "不创建或显示空间探针及点场强读数；面板明确标注该测量不适用",
                    "按已确认的定性判据比较统一视图中的空间形态",
                ]
            elif (
                not measurement_interface_cleared
                and not _plain(payload.get("measurement_interface"))
            ):
                payload["measurement_interface"] = [
                    "当前距离 d（米）与电荷配置（同号/异号）",
                    "选定采样点的 E_x、E_y、E_z 与 |E_total|",
                    "远、中、近距离快照及同一距离下的同号/异号并排比较",
                    "场线形态指标、预期状态、模型有效性与重置状态",
                ]
    elif stage is Stage.VARIABLES_AND_CONDITIONS:
        changed = requirements.get("changed_quantities") or stage_state.get("independent_variable")
        observed = requirements.get("observed_quantities") or stage_state.get("observations")
        controls = stage_state.get("controlled_conditions")
        if changed and "changed_quantities" not in explicitly_cleared:
            set_if("independent_variable", {"name": _plain(changed)})
        elif "changed_quantities" in explicitly_cleared:
            payload.pop("independent_variable", None)
        if observed and "observed_quantities" not in explicitly_cleared:
            set_if("dependent_variable", {"name": _plain(observed)})
        elif "observed_quantities" in explicitly_cleared:
            payload.pop("dependent_variable", None)
        set_if("controlled_variables", controls)
        set_bound(
            "parameter_specifications",
            "parameter_specifications",
            stage_state.get("parameter_specifications")
            or requirements.get("parameter_specifications"),
        )
        set_bound(
            "model_constants_and_media",
            "model_constants_and_media",
            stage_state.get("model_constants_and_media")
            or requirements.get("model_constants_and_media"),
        )
        set_if("reference_condition", stage_state.get("reference_condition"))
    elif stage is Stage.CONCEPTUAL_PROCEDURE:
        latest_steps = requirements.get("procedure_steps")
        latest_steps = latest_steps if isinstance(latest_steps, list) else []
        if latest_steps:
            set_bound("procedure_steps", "procedure_steps", latest_steps)
        student_steps = stage_state.get("procedure_steps")
        if student_steps:
            # Legacy stage-state revisions are retained as explicit student
            # requirements. New EMVR edits use the typed requirements list,
            # so a complete replacement remains available to Builder output.
            set_if("student_required_steps", student_steps)
        set_if("comparison_logic", stage_state.get("comparison_logic"))
    elif stage is Stage.EXPECTED_DATA_VISUALIZATION:
        set_bound(
            "student_visualization_requirements",
            "visualization_requirements",
            stage_state.get("visualization_plan")
            or requirements.get("visualization_requirements"),
        )
        set_if("trend_annotation", stage_state.get("trend_annotation"))
        set_bound(
            "measurement_specifications",
            "measurement_specifications",
            stage_state.get("measurement_specifications")
            or requirements.get("measurement_specifications"),
        )
        set_bound(
            "numerical_model_specifications",
            "numerical_model_specifications",
            stage_state.get("numerical_model_specifications"),
        )
        set_if("unity_update_event", stage_state.get("unity_update_event"))
        if context["point_charge"]:
            measurement_contract = (
                stage_state.get("measurement_specifications")
                or requirements.get("measurement_specifications")
            )
            qualitative_only = measurement_is_qualitative_only(measurement_contract)
            if qualitative_only:
                qualitative_annotation = _plain(stage_state.get("trend_annotation"))
                if "trend_annotation" not in explicitly_cleared:
                    payload["trend_annotation"] = (
                        qualitative_annotation
                        if qualitative_annotation
                        and re.search(
                            r"(?:定性|不生成|不设置|无)(?:[^。；]{0,20})(?:纵轴|曲线|指标)",
                            qualitative_annotation,
                        )
                        else (
                            "该观察量只作定性分类，不设置数值纵轴或理论曲线；所有快照使用相同视角、"
                            "场线种子数和色标，并按已确认判据比较。"
                        )
                    )
                payload["series_encoding"] = (
                    "不生成曲线；Results 面板按比较配置列出快照编号、定性类别和判定依据。"
                )
            else:
                if "trend_annotation" not in explicitly_cleared:
                    payload.setdefault(
                        "trend_annotation",
                        "横轴按距离 d（米）递增；同号与异号配置使用固定且可辨识的两种颜色。"
                        "每个采样点标注已定义的数值观察量，并保持纵轴与色标范围不变。",
                    )
                payload.setdefault(
                    "series_encoding",
                    "同号/异号两条理论曲线使用固定图例；面板同步显示当前距离、电荷配置、"
                    "数值观察量、预期状态以及当前快照编号。",
                )
            if "unity_update_event" not in explicitly_cleared:
                payload.setdefault(
                    "unity_update_event",
                    "OnChargeMoved 或 OnChargeTypeChanged -> RecalculateField -> RefreshFieldLinesAndResults",
                )
    elif stage is Stage.RESULT_INTERPRETATION:
        for field in ("expected_results", "acceptance_criteria", "report_questions"):
            set_bound(field, field, stage_state.get(field) or requirements.get(field))
        set_if("student_result_interpretation", stage_state.get("result_interpretation"))
        for field in (
            "if_prediction_supported",
            "if_opposite_trend",
            "if_no_clear_change",
        ):
            set_if(field, stage_state.get(field))
        hypothesis = _plain(requirements.get("hypothesis"))
        observed = str(context["observed"])
        if "if_prediction_supported" not in explicitly_cleared:
            payload.setdefault(
                "if_prediction_supported",
                (
                    f"若各比较情形下的{observed}与 Lab 特有预期结果一致，"
                    f"则当前结果支持研究假设“{hypothesis}”。"
                    if hypothesis
                    else f"若各比较情形下的{observed}与 Lab 特有预期结果一致，则支持已确认的理论机制。"
                ),
            )
        if "if_opposite_trend" not in explicitly_cleared:
            payload.setdefault(
                "if_opposite_trend",
                (
                    "依次核对电荷正负号、两点间距的坐标换算、E_A 与 E_B 的矢量相加方向、"
                    "场线积分方向以及同号/异号颜色图例；在同一参数快照下重新计算后再比较。"
                    if context["point_charge"] else
                    "依次核对输入单位、公式符号、边界条件、数值算法与可视化映射；在同一参数快照下重算后比较。"
                ),
            )
        if "if_no_clear_change" not in explicitly_cleared:
            payload.setdefault(
                "if_no_clear_change",
                (
                    "固定场线种子数量、观察尺度和颜色量程，确认距离覆盖近/中/远三个区间；"
                    "若 |E_total| 已变化而场线形态不明显，则归为显示灵敏度问题，不作为物理趋势缺失。"
                    if context["point_charge"] else
                    "核对参数是否覆盖已确认比较范围、输出采样和显示量程；区分模型输出未变化与显示灵敏度不足。"
                ),
            )
    elif stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
        set_bound(
            "limitations",
            "limitations",
            stage_state.get("limitations") or requirements.get("limitations"),
        )
        for field in ("conceptual_feasibility", "teaching_value", "vr_added_value"):
            set_if(field, stage_state.get(field))
        if (
            "limitations" not in explicitly_cleared
            and not _plain(payload.get("limitations"))
        ):
            payload["limitations"] = _specific_emvr_limitations(context)
        if context["point_charge"]:
            measurement_contract = (
                stage_state.get("measurement_specifications")
                or requirements.get("measurement_specifications")
            )
            qualitative_only = measurement_is_qualitative_only(measurement_contract)
            if "conceptual_feasibility" not in explicitly_cleared and not payload.get("conceptual_feasibility"):
                payload["conceptual_feasibility"] = {
                    "rating": "可行",
                    "reasoning": (
                        "距离可在已确认范围内独立调节，电荷符号配置可离散切换；每种状态均可由"
                        "库仑点电荷场与矢量叠加重算，并通过场线拓扑、方向箭头和快照比较验收。"
                    ),
                }
            if "teaching_value" not in explicitly_cleared and not payload.get("teaching_value"):
                payload["teaching_value"] = {
                    "rating": "高",
                    "learning_contribution": (
                        "把同号电荷中间低密度区、异号电荷连接场线以及距离缩短后的形态增强"
                        "与 E_total=E_A+E_B 的矢量叠加逐项对应。"
                    ),
                }
            payload["innovation"] = {
                "rating": "由具体功能定义",
                "innovative_elements": (
                    [
                        "同一距离下同号/异号场线并排比较",
                        "抓取结束后同步更新三维场线与定性分类结果",
                        "保存远/中/近快照并保持统一场线种子密度、颜色量程和观察视角",
                    ]
                    if qualitative_only
                    else [
                        "同一距离下同号/异号场线并排比较",
                        "抓取结束后同步更新三维场线与已定义数值指标曲线",
                        "保存远/中/近快照并保持统一场线种子密度和颜色量程",
                    ]
                ),
            }
            if "vr_added_value" not in explicitly_cleared and not payload.get("vr_added_value"):
                payload["vr_added_value"] = {
                    "rating": "高",
                    "reasoning": (
                        "可从不同视角检查三维合场的方向连续性和场线连接关系，并通过直接抓取"
                        "建立点电荷位置、间距数值与空间场重排之间的即时因果联系。"
                    ),
                }
            payload["recommended_improvements"] = [
                "在场景中固定显示距离标尺、统一色标及同号/异号图例",
                "为点电荷设置排除半径和参数越界提示，避免奇点附近的误读",
                (
                    "记录每次比较的电荷配置、距离、定性类别、判定依据与场快照，保证结果可追溯"
                    if qualitative_only
                    else "记录每次比较的电荷配置、距离、已定义数值指标与场快照，保证结果可追溯"
                ),
            ]
    return payload


def stage_report_section(
    stage: Stage,
    payload: dict[str, Any],
    *,
    visualization: dict[str, Any] | None = None,
    include_field_ids: bool = False,
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
                        details.append(
                            f"{_FIELD_LABELS[key]}：{_formal_report_text(rendered)}"
                        )
                item = {
                    "label": f"物体 {index}｜{name}",
                    "value": "\n".join(details),
                }
                if include_field_ids:
                    item["field"] = field
                items.append(item)
            continue
        if field == "core_equations" and isinstance(payload.get(field), list):
            equations = []
            for formula in payload[field]:
                if not isinstance(formula, dict):
                    rendered = _formula_expression_for_report(formula)
                else:
                    name = str(formula.get("name") or formula.get("title") or "").strip()
                    expression = _formula_expression_for_report(
                        formula.get("expression")
                    )
                    rendered = "：".join(part for part in (name, expression) if part)
                if rendered:
                    equations.append(rendered)
            if equations:
                item = {
                    "label": _FIELD_LABELS[field],
                    "value": "\n".join(
                        f"{index}. {equation}"
                        for index, equation in enumerate(equations, start=1)
                    ),
                }
                if include_field_ids:
                    item["field"] = field
                items.append(item)
            continue
        if field == "course_references" and isinstance(payload.get(field), list):
            references = []
            for reference in payload[field]:
                if not isinstance(reference, dict):
                    continue
                lecture = reference.get("lecture")
                title = str(reference.get("title") or "").strip()
                pages = reference.get("pages", [])
                page_text = "-".join(str(page) for page in pages) if isinstance(pages, list) else ""
                concepts = reference.get("concepts", [])
                concept_text = "、".join(
                    str(concept).strip()
                    for concept in concepts
                    if str(concept).strip()
                ) if isinstance(concepts, list) else ""
                parts = [
                    f"Lecture {lecture}" if lecture not in (None, "") else "",
                    title,
                    f"讲义页码 {page_text}" if page_text else "",
                    f"关联概念：{concept_text}" if concept_text else "",
                ]
                rendered = "｜".join(part for part in parts if part)
                if rendered:
                    references.append(rendered)
            if references:
                item = {
                    "label": _FIELD_LABELS[field],
                    "value": "\n".join(
                        f"{index}. {reference}"
                        for index, reference in enumerate(references, start=1)
                    ),
                }
                if include_field_ids:
                    item["field"] = field
                items.append(item)
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
                    links.append(
                        f"{_formal_report_text(relation)}用于解释："
                        f"{_formal_report_text(supports)}"
                    )
            if links:
                item = {
                    "label": _FIELD_LABELS[field],
                    "value": "；".join(dict.fromkeys(links)),
                }
                if include_field_ids:
                    item["field"] = field
                items.append(item)
            continue
        if field == "theory_selection_status":
            status = str(payload.get(field) or "").strip()
            friendly_status = {
                "selected_for_current_research": "已按当前研究问题筛选",
                "course_context_fallback": "已按当前实验的课程关系筛选",
                "needs_semantic_theory_confirmation": "尚需确认与研究问题直接相关的理论关系",
            }.get(status, "")
            if friendly_status:
                item = {
                    "label": _FIELD_LABELS[field],
                    "value": friendly_status,
                }
                if include_field_ids:
                    item["field"] = field
                items.append(item)
            continue
        value = _readable_report_value(field, payload.get(field))
        if field in {
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
            "main_research_question",
        }:
            canonical_field = (
                "research_question"
                if field == "main_research_question"
                else field
            )
            value = clean_emvr_field_text(canonical_field, value)
        value = _formal_report_text(value)
        if value:
            item = {"label": _FIELD_LABELS.get(field, field), "value": value}
            if include_field_ids:
                item["field"] = field
            items.append(item)
    if stage is Stage.EXPECTED_DATA_VISUALIZATION and isinstance(visualization, dict):
        x_axis = _plain(visualization.get("x_axis"))
        y_axis = _plain(visualization.get("y_axis"))
        if x_axis:
            item = {"label": "横轴", "value": _formal_report_text(x_axis)}
            if include_field_ids:
                item["field"] = "visualization_x_axis"
            items.append(item)
        if y_axis:
            item = {"label": "纵轴", "value": _formal_report_text(y_axis)}
            if include_field_ids:
                item["field"] = "visualization_y_axis"
            items.append(item)
        item = {"label": "数据性质", "value": "理论预测，不是实测数据"}
        if include_field_ids:
            item["field"] = "visualization_data_status"
        items.append(item)
    return {
        "stage_id": stage.value,
        "title": stage_title(stage, InteractionState.EMVR_DIRECT),
        "items": items,
    }


def _effective_emvr_visualization(
    session: DesignSession,
    stored: dict[str, Any],
) -> dict[str, Any] | None:
    visualization = stored.get("visualization")
    visualization = deepcopy(visualization) if isinstance(visualization, dict) else {}
    requirements = merge_emvr_structured_requirements(
        session.design_context.get("emvr_design", {})
    )
    stage_state = stage_design_state_snapshot(session)
    measurement_contract = (
        stage_state.get("measurement_specifications")
        or requirements.get("measurement_specifications")
    )
    if measurement_is_qualitative_only(measurement_contract):
        return {
            "display_mode": "qualitative_snapshot_comparison",
            "data_status": "定性分类；不生成数值纵轴或理论曲线",
        }
    plan = _plain(requirements.get("visualization_requirements"))
    changed = [
        part.strip()
        for item in requirements.get("changed_quantities", [])
        for part in re.split(r"[；;]", str(item))
        if part.strip()
    ] if isinstance(requirements.get("changed_quantities"), list) else []
    observed = [
        part.strip()
        for item in requirements.get("observed_quantities", [])
        for part in re.split(r"[；;]", str(item))
        if part.strip()
    ] if isinstance(requirements.get("observed_quantities"), list) else []
    x_match = re.search(r"横轴(?:改为|设为|为|[:：])\s*([^，。；;]+)", plan)
    y_match = re.search(r"纵轴(?:改为|设为|为|[:：])\s*([^，。；;]+)", plan)
    x_label = (x_match.group(1).strip() if x_match else "") or (
        changed[0] if changed else ""
    )
    y_label = (y_match.group(1).strip() if y_match else "") or (
        observed[0] if observed else ""
    )
    specifications = requirements.get("parameter_specifications", [])
    specifications = specifications if isinstance(specifications, list) else []
    matching_spec = next(
        (str(item) for item in specifications if x_label and x_label in str(item)),
        "",
    )
    unit_match = re.search(
        r"(?:单位\s*)?(毫米|厘米|千米|米|纳秒|微秒|毫秒|秒|赫兹|伏特|安培|库仑|欧姆|特斯拉|韦伯|弧度|度|mm|cm|km|m|ns|ms|GHz|MHz|kHz|Hz|rad|Wb|s|V|A|C|Ω|T)",
        matching_spec,
        flags=re.IGNORECASE,
    )
    if x_label:
        visualization["x_axis"] = {
            "label": x_label,
            "unit": unit_match.group(1) if unit_match else "见参数规格",
        }
    if y_label:
        visualization["y_axis"] = {
            "label": y_label,
            "unit": "定性形态指标" if "形态" in y_label else "见观察量定义",
        }
    return visualization or None


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
            visualization=_effective_emvr_visualization(session, stored),
        )
        if section["items"]:
            sections.append(section)
    idea = session.design_context.get("emvr_design", {})
    effective_brief = effective_experiment_brief(session)
    brief = str(effective_brief.get("summary") or "").strip()
    if not brief:
        brief = _experiment_brief_overview(effective_brief)
    if not brief:
        brief = idea.get("brief", "") if isinstance(idea, dict) else ""
    if not brief:
        original = session.design_context.get("idea", {})
        brief = original.get("current_summary") or original.get("original", "") \
            if isinstance(original, dict) else ""
    # Avoid repeating the same long stitched brief in several historical stage
    # rows. Short values may legitimately recur, but a long exact duplicate is
    # report noise and obscures the fields the student actually specified.
    seen_long_values = {
        re.sub(r"\s+", "", str(brief))
    } if len(str(brief).strip()) >= 80 else set()
    for section in sections:
        unique_items: list[dict[str, str]] = []
        for item in section.get("items", []):
            normalized = re.sub(r"\s+", "", str(item.get("value") or ""))
            is_inventory_object = str(item.get("label") or "").startswith("物体 ")
            if (
                not is_inventory_object
                and len(normalized) >= 80
                and normalized in seen_long_values
            ):
                continue
            if not is_inventory_object and len(normalized) >= 80:
                seen_long_values.add(normalized)
            unique_items.append(item)
        section["items"] = unique_items
    sections = [section for section in sections if section.get("items")]
    for section in sections:
        for item in section.get("items", []):
            item["value"] = _formal_report_text(item.get("value"))
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
    hypothesis_payload = effective_emvr_stage_payload(session, Stage.HYPOTHESIS)
    report_limits = hypothesis_payload.get("limiting_cases")
    if report_limits not in (None, "", [], {}):
        boundary_text = _readable_report_value("limiting_cases", report_limits)
    elif quality_public.get("boundary_cases"):
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
    else:
        boundary_text = ""
    if boundary_text:
        quality_items.append(
            {
                "label": "边界情形",
                "value": boundary_text,
            }
        )
    if quality_public.get("traceability"):
        traceability_rows = []
        for item in quality_public.get("traceability", []):
            if not isinstance(item, dict):
                continue
            label = _plain(item.get("design_field_label"))
            purpose = _plain(item.get("purpose"))
            source = ""
            source_match = re.search(r"最近更新来源[：:]([^。]+)", purpose)
            if source_match:
                source = source_match.group(1).strip()
            if label:
                traceability_rows.append(
                    f"{label}（{source or '已确认内容'}）"
                )
        traceability_text = "；".join(dict.fromkeys(traceability_rows[:12]))
        quality_items.append(
            {
                "label": "设计内容来源",
                "value": traceability_text,
            }
        )
    if session.status is WorkflowStatus.COMPLETE:
        builder_values = builder_requirement_values(session)
        builder_root = builder_values.get("builder_workspace_absolute_path", "")
        implementation_items = []
        for index, line in enumerate(
            builder_values.get(IMPLEMENTATION_DEFAULTS_FIELD, "").splitlines(),
            start=1,
        ):
            text = line.strip()
            if not text:
                continue
            label, separator, value = text.partition("：")
            implementation_items.append(
                {
                    "label": (
                        f"默认实现 {label.strip()}"
                        if separator
                        else f"默认实现条目 {index}"
                    ),
                    "value": _formal_report_text(value.strip() if separator else text),
                }
            )
        sections.append(
            {
                "stage_id": "BUILDER_IMPLEMENTATION_CONTRACT",
                "title": "Builder实现契约",
                "items": [
                    {
                        "label": "实验开发性质",
                        "value": "原创新实验；不是盲重建、复刻或对照评测。",
                    },
                    {
                        "label": "Builder工作流模式",
                        "value": "integrated-development",
                    },
                    {
                        "label": "唯一绝对实现根目录",
                        "value": builder_root,
                    },
                    {
                        "label": "Unity宿主项目绝对路径",
                        "value": _unity_project_absolute_path(builder_root),
                    },
                    {
                        "label": "路径规则",
                        "value": (
                            "原创新实验与盲重建采用相同的绝对路径交接要求：最终PDF必须给出唯一、已确认的"
                            "实现根目录。原创新实验直接在该宿主集成根目录运行 integrated-development，"
                            "不得创建或切换到 RebuildWorkspaces 盲重建子副本，也不得再次请求另一宿主路径。"
                        ),
                    },
                    {
                        "label": "公式输入调节规则",
                        "value": (
                            "所选公式中被定义为实验自变量的输入必须全部提供桌面与VR调节控件；"
                            "常量、介质和控制量必须在下列固定输入契约中逐项定值，不得由Builder猜测。"
                        ),
                    },
                    {
                        "label": "公式自变量可调契约",
                        "value": _formal_report_text(
                            builder_values.get("parameter_specifications", "")
                        ),
                    },
                    {
                        "label": "模型常量、介质与固定输入",
                        "value": _formal_report_text(
                            builder_values.get("model_constants_and_media", "")
                        ),
                    },
                    {
                        "label": "指标与空间测量定义",
                        "value": _formal_report_text(
                            builder_values.get("measurement_specifications", "")
                        ),
                    },
                    {
                        "label": "Initial与Reset确定状态",
                        "value": _formal_report_text(
                            builder_values.get("initial_reset_state", "")
                        ),
                    },
                    *implementation_items,
                ],
            }
        )
        sections.append(
            {
                "stage_id": "FINAL_QUALITY_REVIEW",
                "title": "最终设计质量检查",
                "items": [
                    {**item, "value": _formal_report_text(item["value"])}
                    for item in quality_items
                    if item["value"]
                ],
            }
        )
    return {
        "title": "ECE329 EMVR 模拟实验设计报告",
        "design_id": session.design_id,
        "status": (
            "complete" if session.status is WorkflowStatus.COMPLETE else "in_progress"
        ),
        "idea": _formal_report_text(brief),
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
            table = Table(
                rows, colWidths=[34 * mm, 124 * mm], hAlign="LEFT",
                repeatRows=0, splitInRow=1,
            )
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
        require(
            "builder_workspace_absolute_path",
            "Builder宿主项目绝对路径",
            "请给出承载 UnityProject、LabSpecs 和 Tools 的宿主集成根目录绝对路径。",
        )
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
            ("initial_reset_state", "Initial与Reset确定状态", "请给出首次打开与Reset后的对象配置、参数默认值、探针和数据状态。"),
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
            ("model_constants_and_media", "模型常量、介质与固定输入", "请给出公式常量、介质和控制量的准确数值、单位与固定/可调角色。"),
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
        require(
            "measurement_specifications",
            "指标与空间测量定义",
            "请给出每个指标与探针读数的计算方法、单位、采样位置和刷新条件。",
        )
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
