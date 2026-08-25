from __future__ import annotations

import html
import re
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


_FIELD_LABELS = {
    "original_idea": "原始想法",
    "normalized_idea": "设计起点",
    "target_phenomenon": "目标现象",
    "possible_vr_interactions": "可用交互",
    "primary_topic": "主要课程主题",
    "secondary_topics": "相关课程主题",
    "selected_direction": "设计方向",
    "student_revisions": "学生修改",
    "selection_reason": "采用理由",
    "vr_suitability": "适合VR的原因",
    "conceptual_objective": "概念目标",
    "calculation_objective": "计算目标",
    "analysis_objective": "分析目标",
    "vr_interaction_objective": "交互目标",
    "observation_objective": "观察目标",
    "main_research_question": "研究问题",
    "adjustable_quantity_in_vr": "VR中可调内容",
    "observable_quantity_in_vr": "VR中可观察内容",
    "physical_mechanism": "物理机制",
    "core_equations": "理论关系",
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
    "student_constraints": "学生明确的设计约束",
    "independent_variable": "自变量",
    "student_variable_definition": "学生定义的变量与观察方式",
    "dependent_variable": "观察量",
    "controlled_variables": "控制条件",
    "reference_condition": "基准条件",
    "procedure_steps": "实验流程",
    "student_required_steps": "学生要求保留的流程",
    "comparison_logic": "比较逻辑",
    "trend_annotation": "趋势标注",
    "student_visualization_requirements": "学生要求的显示内容",
    "unity_update_event": "Unity更新触发",
    "if_prediction_supported": "符合预期时",
    "if_opposite_trend": "趋势相反时",
    "if_no_clear_change": "变化不明显时",
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
    ),
    Stage.COURSE_MAPPING_AND_DIRECTION: (
        "primary_topic",
        "secondary_topics",
        "selected_direction",
        "selection_reason",
        "vr_suitability",
        "student_revisions",
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
        return str(value).strip()
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


def stage_report_section(
    stage: Stage,
    payload: dict[str, Any],
    *,
    visualization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    for field in _REPORT_FIELDS.get(stage, ()):
        if field not in payload:
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
        payload = stored.get("stage_payload", {})
        if not isinstance(payload, dict):
            payload = {}
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
    brief = idea.get("brief", "") if isinstance(idea, dict) else ""
    if not brief:
        original = session.design_context.get("idea", {})
        brief = original.get("current_summary") or original.get("original", "") \
            if isinstance(original, dict) else ""
    return {
        "title": "ECE329 EMVR 模拟实验设计报告",
        "design_id": session.design_id,
        "status": (
            "complete" if session.status is WorkflowStatus.COMPLETE else "in_progress"
        ),
        "idea": str(brief).strip(),
        "sections": sections,
        "completed_stage_count": len(session.completed_stages),
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


def validate_emvr_report_completeness(session: DesignSession) -> None:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        raise ValueError("EMVR report validation requires an EMVR design")

    def payload(stage: Stage) -> dict[str, Any]:
        stored = session.stage_outputs.get(stage.value, {})
        value = stored.get("stage_payload", {}) if isinstance(stored, dict) else {}
        return value if isinstance(value, dict) else {}

    objectives = payload(Stage.LEARNING_OBJECTIVES)
    required_objectives = (
        "conceptual_objective",
        "calculation_objective",
        "analysis_objective",
        "vr_interaction_objective",
    )
    if any(not _plain(objectives.get(key)) for key in required_objectives):
        raise ValueError("EMVR report is missing one or more experiment objectives")

    setup = payload(Stage.CONCEPTUAL_OR_VR_SETUP)
    inventory = setup.get("object_inventory")
    required_object_fields = {
        "object_name",
        "category",
        "purpose",
        "student_interaction",
        "physics_or_data_state",
        "visual_feedback",
        "required",
    }
    if not isinstance(inventory, list) or len(inventory) < 5:
        raise ValueError("EMVR report requires a complete experiment object inventory")
    if any(
        not isinstance(item, dict)
        or any(key not in item or item.get(key) in (None, "") for key in required_object_fields)
        for item in inventory
    ):
        raise ValueError("Each EMVR experiment object must include its complete design role")

    procedure = payload(Stage.CONCEPTUAL_PROCEDURE).get("procedure_steps")
    if (
        not isinstance(procedure, list)
        or len(procedure) < 5
        or any(not isinstance(step, str) or not step.strip() for step in procedure)
    ):
        raise ValueError("EMVR report requires a complete experiment procedure")
