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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import DesignSession, InteractionState, Stage
from .reporting import validate_emvr_report_completeness


_LATIN_RUN = re.compile(r"[A-Za-z0-9_./:+()=\-\[\]]+(?:\s+[A-Za-z0-9_./:+()=\-\[\]]+)*")
_UNRESOLVED = "unresolved — 由 EMVR Builder Gate 1 与用户确认"


def _stage_payload(session: DesignSession, stage: Stage) -> dict[str, Any]:
    stored = session.stage_outputs.get(stage.value, {})
    payload = stored.get("stage_payload", {}) if isinstance(stored, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _text(value: Any, *, depth: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if depth >= 3:
        return ""
    if isinstance(value, list):
        return "；".join(
            part for item in value if (part := _text(item, depth=depth + 1))
        )
    if isinstance(value, dict):
        preferred_keys = (
            "display",
            "equation",
            "name",
            "title",
            "object_name",
            "focus",
            "value",
        )
        preferred = next(
            (_text(value.get(key), depth=depth + 1) for key in preferred_keys if value.get(key)),
            "",
        )
        details: list[str] = []
        for key in (
            "id",
            "physical_meaning",
            "reasoning",
            "supports",
            "units",
            "pages",
        ):
            rendered = _text(value.get(key), depth=depth + 1)
            if rendered and rendered != preferred:
                details.append(f"{key}={rendered}")
        if preferred:
            return f"{preferred} ({'; '.join(details)})" if details else preferred
        parts = []
        for key, item in value.items():
            rendered = _text(item, depth=depth + 1)
            if rendered:
                parts.append(f"{key}={rendered}")
        return "；".join(parts)
    return str(value).strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [rendered for item in value if (rendered := _text(item))]
    rendered = _text(value)
    return [rendered] if rendered else []


def _safe_lab_id(design_id: str) -> str:
    suffix = re.sub(r"[^a-z0-9]", "", design_id.lower())[:16] or "design"
    return f"ece329_{suffix}"


def _first_value(*values: Any) -> str:
    return next((rendered for value in values if (rendered := _text(value))), "")


def _field(
    key: str,
    value: Any,
    *,
    status: str = "confirmed-from-design-session",
    note: str = "",
) -> dict[str, str]:
    rendered = _text(value) or _UNRESOLVED
    if rendered == _UNRESOLVED:
        status = "unresolved"
    return {"key": key, "value": rendered, "status": status, "note": note}


def _object_rows(inventory: Any) -> list[dict[str, str]]:
    if not isinstance(inventory, list):
        return []
    rows: list[dict[str, str]] = []
    for index, item in enumerate(inventory, start=1):
        if not isinstance(item, dict):
            continue
        name = _first_value(item.get("object_name"), f"object_{index}")
        rows.append(
            {
                "object_id": f"OBJ_{index:02d}",
                "display_name": name,
                "object_type": _text(item.get("category")) or "unresolved",
                "role": _text(item.get("purpose")) or "unresolved",
                "initial_state": _text(item.get("physics_or_data_state")) or "unresolved",
                "desktop_interaction": _UNRESOLVED,
                "xr_interaction": _text(item.get("student_interaction")) or "unresolved",
                "visible_feedback": _text(item.get("visual_feedback")) or "unresolved",
                "required": _text(item.get("required")) or "unresolved",
                "status": "mixed-needs-confirmation",
            }
        )
    return rows


def build_builder_gate1_input(session: DesignSession) -> dict[str, Any]:
    """Map a completed EMVR design to the Builder Pack Gate 1 intake contract.

    The result deliberately remains an intake document. Builder-owned scene,
    room, evidence, and acceptance decisions are marked as policy references or
    unresolved instead of being invented by the design workflow.
    """
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        raise ValueError("Builder Gate 1 input is only available for an EMVR design")
    validate_emvr_report_completeness(session)

    idea = _stage_payload(session, Stage.IDEA_BRAINSTORMING)
    mapping = _stage_payload(session, Stage.COURSE_MAPPING_AND_DIRECTION)
    objectives = _stage_payload(session, Stage.LEARNING_OBJECTIVES)
    research = _stage_payload(session, Stage.RESEARCH_QUESTION)
    theory = _stage_payload(session, Stage.THEORETICAL_FRAMEWORK)
    hypothesis = _stage_payload(session, Stage.HYPOTHESIS)
    setup = _stage_payload(session, Stage.CONCEPTUAL_OR_VR_SETUP)
    variables = _stage_payload(session, Stage.VARIABLES_AND_CONDITIONS)
    procedure = _stage_payload(session, Stage.CONCEPTUAL_PROCEDURE)
    visualization = _stage_payload(session, Stage.EXPECTED_DATA_VISUALIZATION)
    interpretation = _stage_payload(session, Stage.RESULT_INTERPRETATION)
    value_limits = _stage_payload(session, Stage.DESIGN_VALUE_AND_LIMITATIONS)

    design_context_idea = session.design_context.get("idea", {})
    if not isinstance(design_context_idea, dict):
        design_context_idea = {}
    title = _first_value(
        mapping.get("selected_direction"),
        idea.get("normalized_idea"),
        design_context_idea.get("current_summary"),
        design_context_idea.get("original"),
        "ECE329 EMVR Lab",
    )
    learning_goals = [
        goal
        for key in (
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        )
        if (goal := _text(objectives.get(key)))
    ]
    steps = _as_list(procedure.get("procedure_steps"))
    expected_results = [
        item
        for item in (
            _text(hypothesis.get("research_hypothesis")),
            _text(hypothesis.get("expected_trend")),
            _text(interpretation.get("if_prediction_supported")),
            _text(interpretation.get("if_opposite_trend")),
            _text(interpretation.get("if_no_clear_change")),
        )
        if item
    ]
    objects = _object_rows(setup.get("object_inventory"))
    first_action = steps[0] if steps else _UNRESOLVED

    return {
        "document": {
            "title": "EMVR Builder Pack — Gate 1 Requirements Input",
            "purpose": (
                "作为 EMVR Blind Builder Pack 阶段 1（Brief confirmed）的用户输入。"
                "本文件不表示 Gate 已获批准，也不表示 Unity 实现已经完成。"
            ),
            "source_design_id": session.design_id,
            "target_gate": "Gate 1 — Brief confirmed",
            "template_reference": "LabSpecs/templates/lab-brief.template.yaml (schema 1.0.0)",
        },
        "identity": [
            _field("schema_version", "1.0.0", status="builder-template-reference"),
            _field(
                "lab_id",
                _safe_lab_id(session.design_id),
                status="inferred-needs-confirmation",
                note="可直接采用，也可在 Gate 1 改为符合命名规则的项目 ID。",
            ),
            _field("title", title),
            _field("domain", "ECE329 electromagnetics", status="confirmed-from-course-scope"),
            _field("workflow_mode", "new lab design", status="inferred-needs-confirmation"),
            _field("status", "draft", status="builder-template-reference"),
        ],
        "source_material": [
            _field("source_material.handbook", "this Gate 1 input PDF"),
            _field(
                "source_material.additional_notes",
                f"ECE329 EMVR design session {session.design_id}",
            ),
            _field(
                "source_material.course_scope",
                "ECE329 lecture notes and the workflow's verified supplemental references",
                status="confirmed-from-course-scope",
            ),
            _field(
                "source_material.builder_treatment",
                "Treat this PDF as user-provided input; preserve every unresolved marker for Gate 1 confirmation.",
                status="builder-processing-instruction",
            ),
        ],
        "design_definition": [
            _field("research_question", research.get("main_research_question")),
            _field("target_phenomenon", idea.get("target_phenomenon")),
            _field("independent_variable", variables.get("independent_variable")),
            _field("dependent_variable", variables.get("dependent_variable")),
            _field("controlled_variables", variables.get("controlled_variables")),
            _field("reference_condition", variables.get("reference_condition")),
            _field("research_hypothesis", hypothesis.get("research_hypothesis")),
            _field("expected_trend", hypothesis.get("expected_trend")),
            _field("limiting_cases", hypothesis.get("limiting_cases")),
        ],
        "learning_goals": learning_goals,
        "student_tasks": [
            {
                "step_id": f"S{index}",
                "goal": step,
                "expected_action": "observe_or_interact",
                "success_criteria": (
                    "完成该步骤，并在设计指定的数值、曲线或空间可视化中保留可比较结果。"
                ),
                "status": "inferred-needs-confirmation",
            }
            for index, step in enumerate(steps, start=1)
        ],
        "physics": {
            "mechanism": _text(theory.get("physical_mechanism")) or _UNRESOLVED,
            "formulas": _as_list(theory.get("core_equations")),
            "formula_support_map": _as_list(theory.get("formula_support_map")),
            "units": _as_list(theory.get("units")) or [_UNRESOLVED],
            "simulation_inputs": _as_list(theory.get("simulation_inputs")),
            "parameter_ranges": _as_list(theory.get("parameter_limits")) or [_UNRESOLVED],
            "assumptions": (
                _as_list(theory.get("assumptions"))
                + _as_list(value_limits.get("limitations"))
            ) or [_UNRESOLVED],
            "expected_results": expected_results,
        },
        "objects": objects,
        "interaction_modes": [
            _field("interaction_modes.desktop_mouse", _UNRESOLVED),
            _field("interaction_modes.xr_device_simulator", "required", status="builder-policy-reference"),
            _field("interaction_modes.real_vr", "required", status="builder-policy-reference"),
            _field("interaction_modes.xr_actions", setup.get("interactions")),
            _field("interaction_modes.measurement_interface", setup.get("measurement_interface")),
            _field("interaction_modes.mouse_to_vr_mapping", _UNRESOLVED),
        ],
        "visualization": [
            _field("visualization.requirements", visualization.get("student_visualization_requirements")),
            _field("visualization.trend_annotation", visualization.get("trend_annotation")),
            _field("visualization.update_event", visualization.get("unity_update_event")),
            _field("visualization.layer", setup.get("visualization_layer")),
            _field("visualization.data_status", "theoretical_prediction; measured=false"),
        ],
        "environment": [
            _field("environment.room_shell_strategy", "mandatory_approved_prefab", status="builder-policy-reference"),
            _field(
                "environment.room_shell_source",
                "ApprovedAssets/EMVRRoom/Prefabs/Room_Big_Part_01.prefab",
                status="builder-policy-reference",
            ),
            _field("environment.room_placement_and_adaptation", _UNRESOLVED),
            _field("environment.visual_style_reference", _UNRESOLVED),
            _field("environment.lighting_requirement", _UNRESOLVED),
            _field(
                "environment.camera_and_ui_safe_area",
                "Game View must keep instruction, experiment, status, and result regions visible.",
                status="builder-policy-reference",
            ),
        ],
        "presets": [
            _field(
                "presets.reference_condition",
                variables.get("reference_condition"),
                status="inferred-needs-confirmation",
            )
        ],
        "reuse_requirements": [
            _field("reuse_requirements.mandatory_common_baseline", "true", status="builder-policy-reference"),
            _field("reuse_requirements.capability_triggered_common_reuse", "true", status="builder-policy-reference"),
            _field("reuse_requirements.reuse_audit_required_in_design", "true", status="builder-policy-reference"),
        ],
        "scene": [
            _field(
                "scene.output_scene",
                f"Assets/Scenes/{_safe_lab_id(session.design_id)}.unity",
                status="inferred-needs-confirmation",
            ),
            _field("scene.creation_mode", "new_scene", status="builder-policy-reference"),
            _field("scene.approved_asset_sources", _UNRESOLVED),
            _field(
                "scene.camera_style",
                "generated from approved XR/Common components",
                status="builder-policy-reference",
            ),
            _field("scene.language", "English", status="builder-template-reference"),
            _field(
                "scene.required_visible_regions",
                ["instruction", "experiment", "status", "result"],
                status="builder-policy-reference",
            ),
        ],
        "initial_and_action_states": [
            _field(
                "initial_and_action_states.authored_initial_state",
                variables.get("reference_condition"),
                status="inferred-needs-confirmation",
            ),
            _field("initial_and_action_states.hidden_templates_or_loaders", _UNRESOLVED),
            _field(
                "initial_and_action_states.first_required_action",
                first_action,
                status="inferred-needs-confirmation",
            ),
            _field(
                "initial_and_action_states.expected_visible_after_action",
                _first_value(
                    visualization.get("student_visualization_requirements"),
                    hypothesis.get("expected_trend"),
                ),
                status="inferred-needs-confirmation",
            ),
        ],
        "acceptance_and_evidence": [
            _field(
                "acceptance.core_flow",
                steps,
                status="inferred-needs-confirmation",
            ),
            _field(
                "acceptance.required_evidence",
                ["Game View initial state", "Game View completed state", "Console after Clear"],
                status="builder-policy-reference",
            ),
            _field(
                "acceptance.result_interpretation",
                expected_results,
                status="confirmed-from-design-session",
            ),
            _field("acceptance.report_questions", _UNRESOLVED),
        ],
        "builder_runtime_constraints": [
            _field("current_editor_state.unity_version", "2022.3.62f3c1", status="builder-template-reference"),
            _field("current_editor_state.unity_open", _UNRESOLVED),
            _field("current_editor_state.compiling", _UNRESOLVED),
            _field("current_editor_state.play_mode", _UNRESOLVED),
            _field("workflow_limits.run_batchmode", "false", status="builder-policy-reference"),
            _field("workflow_limits.hand_edit_scene_yaml", "false", status="builder-policy-reference"),
            _field("workflow_limits.no_visible_progress_minutes", "5", status="builder-policy-reference"),
            _field("workflow_limits.unity_wait_limit_minutes", "10", status="builder-policy-reference"),
        ],
        "handoff_notes": [
            "Builder 必须先把本 PDF 映射为 LabSpecs/<lab_id>/brief.yaml，再由用户确认 Gate 1。",
            "不得用课程常识补齐 unresolved 项；应在 Gate 1 进行局部确认。",
            "本 PDF 仅描述实验设计，不授权创建 Unity 场景、代码或批准任何 Gate。",
        ],
    }


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


def render_builder_gate1_input_pdf(session: DesignSession) -> bytes:
    data = build_builder_gate1_input(session)
    font_name = "STSong-Light"
    try:
        pdfmetrics.getFont(font_name)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=data["document"]["title"],
        author="ECE329 Lab Studio",
        subject="EMVR Blind Builder Pack Gate 1 input",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BuilderTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=25,
        textColor=colors.HexColor("#0B2942"),
        alignment=TA_CENTER,
        spaceAfter=6 * mm,
    )
    heading_style = ParagraphStyle(
        "BuilderHeading",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=12,
        leading=17,
        textColor=colors.HexColor("#0D7E78"),
        spaceBefore=5 * mm,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "BuilderBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8.4,
        leading=12.5,
        textColor=colors.HexColor("#17334A"),
    )
    key_style = ParagraphStyle(
        "BuilderKey",
        parent=body_style,
        fontName="Helvetica",
        fontSize=7.4,
        leading=10.5,
        textColor=colors.HexColor("#0D655F"),
    )
    small_style = ParagraphStyle(
        "BuilderSmall",
        parent=body_style,
        fontSize=7.3,
        leading=10.5,
        textColor=colors.HexColor("#5B7183"),
    )

    def p(value: Any, style: ParagraphStyle = body_style) -> Paragraph:
        return Paragraph(_paragraph_text(value), style)

    def field_table(rows: list[dict[str, str]]) -> Table:
        table_rows = [[p("Field ID", key_style), p("Value"), p("Status", key_style)]]
        for row in rows:
            value = row["value"]
            if row.get("note"):
                value = f"{value}\n说明：{row['note']}"
            table_rows.append(
                [p(row["key"], key_style), p(value), p(row["status"], key_style)]
            )
        table = Table(table_rows, colWidths=[48 * mm, 92 * mm, 40 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDF1EE")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B7CBC8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    story: list[Any] = [p(data["document"]["title"], title_style)]
    story.append(
        field_table(
            [
                _field("document.purpose", data["document"]["purpose"], status="processing-instruction"),
                _field("document.source_design_id", data["document"]["source_design_id"]),
                _field("document.target_gate", data["document"]["target_gate"], status="builder-template-reference"),
                _field("document.template_reference", data["document"]["template_reference"], status="builder-template-reference"),
            ]
        )
    )
    story.extend(
        [
            Spacer(1, 3 * mm),
            p(
                "状态说明：confirmed-from-design-session 表示已写入最终 EMVR 设计；"
                "inferred-needs-confirmation 表示为了衔接 Builder 而生成的候选值；"
                "unresolved 必须在 Gate 1 询问用户，不能自行补齐。",
                small_style,
            ),
        ]
    )

    sections = [
        ("1. Lab identity", data["identity"]),
        ("2. Source material and traceability", data["source_material"]),
        ("3. Research definition", data["design_definition"]),
        (
            "4. Learning goals",
            [_field(f"learning_goals[{i}]", goal) for i, goal in enumerate(data["learning_goals"], 1)],
        ),
        (
            "5. Student tasks",
            [
                _field(
                    f"student_tasks[{task['step_id']}]",
                    f"goal={task['goal']}；expected_action={task['expected_action']}；"
                    f"success_criteria={task['success_criteria']}",
                    status=task["status"],
                )
                for task in data["student_tasks"]
            ],
        ),
        (
            "6. Physics",
            [
                _field("physics.mechanism", data["physics"]["mechanism"]),
                _field("physics.formulas", data["physics"]["formulas"]),
                _field("physics.formula_support_map", data["physics"]["formula_support_map"]),
                _field("physics.units", data["physics"]["units"]),
                _field("physics.simulation_inputs", data["physics"]["simulation_inputs"]),
                _field("physics.parameter_ranges", data["physics"]["parameter_ranges"]),
                _field("physics.assumptions", data["physics"]["assumptions"]),
                _field("physics.expected_results", data["physics"]["expected_results"]),
            ],
        ),
        (
            "7. Object inventory",
            [
                _field(
                    f"objects[{obj['object_id']}]",
                    "；".join(f"{key}={value}" for key, value in obj.items() if key not in {"object_id", "status"}),
                    status=obj["status"],
                )
                for obj in data["objects"]
            ],
        ),
        ("8. Presets", data["presets"]),
        ("9. Interaction modes", data["interaction_modes"]),
        ("10. Visualization", data["visualization"]),
        ("11. Environment and Game View", data["environment"]),
        ("12. Reuse requirements", data["reuse_requirements"]),
        ("13. Scene", data["scene"]),
        ("14. Initial and post-action states", data["initial_and_action_states"]),
        ("15. Acceptance and evidence", data["acceptance_and_evidence"]),
        ("16. Builder runtime constraints", data["builder_runtime_constraints"]),
    ]
    for heading, rows in sections:
        story.append(p(heading, heading_style))
        story.append(field_table(rows or [_field(f"{heading}.content", _UNRESOLVED)]))

    story.append(p("17. Handoff instructions", heading_style))
    for note in data["handoff_notes"]:
        story.append(p(f"• {note}"))

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#738696"))
        canvas.drawString(15 * mm, 8 * mm, "ECE329 Lab Studio | Builder Gate 1 input")
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
