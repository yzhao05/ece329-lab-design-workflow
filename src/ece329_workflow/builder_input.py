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

from .builder_requirements import (
    LAB_ID_PATTERN,
    builder_requirement_values,
    is_resolved_design_value,
    validate_builder_requirements,
)
from .models import DesignSession, InteractionState, Stage
from .reporting import (
    effective_emvr_stage_payload,
    effective_experiment_brief,
    validate_emvr_report_completeness,
)


_LATIN_RUN = re.compile(r"[A-Za-z0-9_./:+()=\-\[\]]+(?:\s+[A-Za-z0-9_./:+()=\-\[\]]+)*")
_UNRESOLVED = "unresolved — 由 EMVR Builder Gate 1 与用户确认"


def _stage_payload(session: DesignSession, stage: Stage) -> dict[str, Any]:
    return effective_emvr_stage_payload(session, stage)


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


def _object_rows(inventory: Any, desktop_plan: str) -> list[dict[str, str]]:
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
                "desktop_interaction": desktop_plan,
                "xr_interaction": _text(item.get("student_interaction")) or "unresolved",
                "visible_feedback": _text(item.get("visual_feedback")) or "unresolved",
                "required": _text(item.get("required")) or "unresolved",
                "status": "confirmed-from-design-session",
            }
        )
    return rows


def build_builder_gate1_input(session: DesignSession) -> dict[str, Any]:
    """Map a completed EMVR design to the Builder Pack Gate 1 intake contract.

    Builder-owned runtime checks remain policy references, while every
    user-owned experiment-design field must already be confirmed in EMVR.
    """
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        raise ValueError("Builder Gate 1 input is only available for an EMVR design")
    validate_emvr_report_completeness(session)
    validate_builder_requirements(session)
    builder_values = builder_requirement_values(session)

    idea = _stage_payload(session, Stage.IDEA_BRAINSTORMING)
    objectives = _stage_payload(session, Stage.LEARNING_OBJECTIVES)
    research = _stage_payload(session, Stage.RESEARCH_QUESTION)
    theory = _stage_payload(session, Stage.THEORETICAL_FRAMEWORK)
    hypothesis = _stage_payload(session, Stage.HYPOTHESIS)
    setup = _stage_payload(session, Stage.CONCEPTUAL_OR_VR_SETUP)
    variables = _stage_payload(session, Stage.VARIABLES_AND_CONDITIONS)
    procedure = _stage_payload(session, Stage.CONCEPTUAL_PROCEDURE)
    visualization = _stage_payload(session, Stage.EXPECTED_DATA_VISUALIZATION)
    value_limits = _stage_payload(session, Stage.DESIGN_VALUE_AND_LIMITATIONS)
    experiment_brief = effective_experiment_brief(session)
    emvr_design = session.design_context.get("emvr_design", {})
    emvr_design = emvr_design if isinstance(emvr_design, dict) else {}
    formula_flow = emvr_design.get("formula_flow", {})
    formula_flow = formula_flow if isinstance(formula_flow, dict) else {}
    authoritative_formula_brief = emvr_design.get("authoritative_experiment_brief")
    if not isinstance(authoritative_formula_brief, dict) or not authoritative_formula_brief:
        # Completed sessions created before formula-first onboarding remain
        # exportable.  Their already-confirmed stage artifacts are projected
        # into the same Builder contract and explicitly marked as legacy;
        # current sessions still use the authoritative formula brief.
        inventory = setup.get("object_inventory", [])
        legacy_objects = [
            str(item.get("object_name") or "").strip()
            for item in inventory
            if isinstance(item, dict) and str(item.get("object_name") or "").strip()
        ] if isinstance(inventory, list) else []
        experiment_brief = {
            "topic": _first_value(
                idea.get("normalized_idea"),
                research.get("main_research_question"),
            ),
            "summary": _first_value(
                idea.get("normalized_idea"),
                research.get("main_research_question"),
            ),
            "primary_formula_ids": [],
            "supporting_formula_ids": [],
            "formula_composition_strategy": "LEGACY_CONFIRMED_STAGE_FLOW",
            "selected_experiment_method_ids": ["legacy_confirmed_procedure"],
            "selected_experiment_pattern_ids": ["LEGACY_CONFIRMED_STAGE_FLOW"],
            "objects": legacy_objects,
            "operations": _as_list(procedure.get("procedure_steps")),
            "changed_quantities": _as_list(variables.get("independent_variable")),
            "observed_quantities": _as_list(variables.get("dependent_variable")),
            "boundary_conditions": _as_list(value_limits.get("limitations")),
        }
    method_by_id = {
        str(item.get("method_id") or ""): item
        for item in formula_flow.get("experiment_methods", [])
        if isinstance(item, dict)
    }
    selected_methods = [
        {
            "method_id": method_id,
            "title": _text(method_by_id[method_id].get("title")),
            "pattern_ids": list(method_by_id[method_id].get("pattern_ids", [])),
            "description": _text(method_by_id[method_id].get("description")),
            "process_summary": _text(method_by_id[method_id].get("process_summary")),
        }
        for method_id in experiment_brief.get("selected_experiment_method_ids", [])
        if method_id in method_by_id
    ]
    if not selected_methods and experiment_brief.get("selected_experiment_method_ids"):
        selected_methods = [
            {
                "method_id": "legacy_confirmed_procedure",
                "title": "已确认的完整实验流程",
                "pattern_ids": ["LEGACY_CONFIRMED_STAGE_FLOW"],
                "description": "由升级前已经确认的阶段设计投影而来。",
                "process_summary": _text(procedure.get("procedure_steps")),
            }
        ]

    title = builder_values["lab_title"]
    lab_id = builder_values["lab_id"]
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
    expected_results = _as_list(builder_values["expected_results"])
    objects = _object_rows(
        setup.get("object_inventory"), builder_values["desktop_interaction_plan"]
    )
    first_action = steps[0] if steps else _UNRESOLVED

    payload = {
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
                lab_id,
            ),
            _field("title", title),
            _field("domain", "ECE329 electromagnetics", status="confirmed-from-course-scope"),
            _field("workflow_mode", "blind-rebuild", status="confirmed-from-design-session"),
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
                "Treat this PDF as confirmed user input for Builder Gate 1; do not reinterpret the design intent.",
                status="builder-processing-instruction",
            ),
        ],
        "formula_driven_experiment": {
            "topic": _text(experiment_brief.get("topic")),
            "summary": _text(experiment_brief.get("summary")),
            "primary_formulas": _as_list(
                idea.get("primary_formulas") or theory.get("core_equations")
            ),
            "supporting_formulas": _as_list(idea.get("supporting_formulas"))
            or ["none (no supporting formula is required for this design)"],
            "composition_strategy": _text(
                experiment_brief.get("formula_composition_strategy")
            ),
            "selected_methods": selected_methods,
            "selected_pattern_ids": list(
                experiment_brief.get("selected_experiment_pattern_ids", [])
            ),
            "objects": list(experiment_brief.get("objects", [])),
            "operations": list(experiment_brief.get("operations", [])),
            "changed_quantities": list(
                experiment_brief.get("changed_quantities", [])
            ),
            "observed_quantities": list(
                experiment_brief.get("observed_quantities", [])
            ),
            "boundary_conditions": list(
                experiment_brief.get("boundary_conditions", [])
            ),
            "status": "confirmed-from-design-session",
        },
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
                "status": "confirmed-from-design-session",
            }
            for index, step in enumerate(steps, start=1)
        ],
        "physics": {
            "mechanism": _text(theory.get("physical_mechanism")) or _UNRESOLVED,
            "formulas": _as_list(theory.get("core_equations")),
            "formula_support_map": _as_list(theory.get("formula_support_map")),
            "units": _as_list(builder_values["parameter_specifications"]),
            "simulation_inputs": _as_list(theory.get("simulation_inputs")),
            "parameter_ranges": _as_list(builder_values["parameter_specifications"]),
            "assumptions": (
                _as_list(theory.get("assumptions"))
                + _as_list(value_limits.get("limitations"))
            ) or ["Use the confirmed ECE329 model assumptions in the design report."],
            "expected_results": expected_results,
        },
        "objects": objects,
        "interaction_modes": [
            _field("interaction_modes.desktop_mouse", "required"),
            _field("interaction_modes.xr_device_simulator", "required", status="builder-policy-reference"),
            _field("interaction_modes.real_vr", "required", status="builder-policy-reference"),
            _field("interaction_modes.xr_actions", setup.get("interactions")),
            _field("interaction_modes.measurement_interface", setup.get("measurement_interface")),
            _field("interaction_modes.mouse_to_vr_mapping", builder_values["desktop_interaction_plan"]),
        ],
        "visualization": [
            _field(
                "visualization.requirements",
                _first_value(
                    visualization.get("student_visualization_requirements"),
                    visualization.get("trend_annotation"),
                    "Display the confirmed theoretical response with units and a spatial encoding.",
                ),
                status="confirmed-from-design-session",
            ),
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
            _field("environment.room_placement_and_adaptation", builder_values["room_spatial_requirements"]),
            _field("environment.visual_style_reference", builder_values["room_spatial_requirements"]),
            _field("environment.lighting_requirement", builder_values["room_spatial_requirements"]),
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
                status="confirmed-from-design-session",
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
                f"Assets/Scenes/{lab_id}.unity",
                status="confirmed-from-design-session",
            ),
            _field("scene.creation_mode", "new_scene", status="builder-policy-reference"),
            _field(
                "scene.approved_asset_sources",
                "ApprovedAssets/EMVRRoom/Prefabs/Room_Big_Part_01.prefab",
                status="builder-policy-reference",
            ),
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
                status="confirmed-from-design-session",
            ),
            _field("initial_and_action_states.hidden_templates_or_loaders", builder_values["hidden_object_lifecycle"]),
            _field(
                "initial_and_action_states.first_required_action",
                first_action,
                status="confirmed-from-design-session",
            ),
            _field(
                "initial_and_action_states.expected_visible_after_action",
                _first_value(
                    visualization.get("student_visualization_requirements"),
                    hypothesis.get("expected_trend"),
                ),
                status="confirmed-from-design-session",
            ),
        ],
        "acceptance_and_evidence": [
            _field(
                "acceptance.core_flow",
                steps,
                status="confirmed-from-design-session",
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
            _field("acceptance.pass_criteria", builder_values["acceptance_criteria"]),
            _field("acceptance.report_questions", builder_values["report_questions"]),
        ],
        "builder_runtime_constraints": [
            _field("current_editor_state.unity_version", "2022.3.62f3c1", status="builder-template-reference"),
            _field("current_editor_state.unity_open", "Builder must read the active editor state at Gate 1", status="builder-runtime-check"),
            _field("current_editor_state.compiling", "Builder must read the active editor state at Gate 1", status="builder-runtime-check"),
            _field("current_editor_state.play_mode", "Builder must read the active editor state at Gate 1", status="builder-runtime-check"),
            _field("workflow_limits.run_batchmode", "false", status="builder-policy-reference"),
            _field("workflow_limits.hand_edit_scene_yaml", "false", status="builder-policy-reference"),
            _field("workflow_limits.no_visible_progress_minutes", "5", status="builder-policy-reference"),
            _field("workflow_limits.unity_wait_limit_minutes", "10", status="builder-policy-reference"),
        ],
        "handoff_notes": [
            "Builder 必须先把本 PDF 映射为 LabSpecs/<lab_id>/brief.yaml，再由用户确认 Gate 1。",
            "本文件中的用户设计输入已在EMVR工作流前置确认；Builder只需执行实现期检查。",
            "本 PDF 仅描述实验设计，不授权创建 Unity 场景、代码或批准任何 Gate。",
        ],
    }
    validate_builder_gate1_input(payload)
    return payload


def validate_builder_gate1_input(payload: dict[str, Any]) -> None:
    """Reject incomplete or contract-incompatible Builder handoffs."""

    required_sections = {
        "document",
        "identity",
        "source_material",
        "formula_driven_experiment",
        "design_definition",
        "learning_goals",
        "student_tasks",
        "physics",
        "objects",
        "interaction_modes",
        "visualization",
        "environment",
        "presets",
        "reuse_requirements",
        "scene",
        "initial_and_action_states",
        "acceptance_and_evidence",
        "builder_runtime_constraints",
        "handoff_notes",
    }
    missing_sections = sorted(required_sections - set(payload))
    if missing_sections:
        raise ValueError(
            "Builder Gate 1 input is missing sections: " + ", ".join(missing_sections)
        )
    empty_sections = sorted(
        section for section in required_sections if payload.get(section) in (None, "", [], {})
    )
    if empty_sections:
        raise ValueError(
            "Builder Gate 1 input contains empty sections: " + ", ".join(empty_sections)
        )
    formula_design = payload.get("formula_driven_experiment", {})
    if not isinstance(formula_design, dict):
        raise ValueError("Builder Gate 1 formula-driven experiment must be an object")
    required_formula_fields = (
        "topic",
        "summary",
        "primary_formulas",
        "composition_strategy",
        "selected_methods",
        "selected_pattern_ids",
        "objects",
        "operations",
        "changed_quantities",
        "observed_quantities",
        "boundary_conditions",
    )
    missing_formula_fields = [
        field
        for field in required_formula_fields
        if formula_design.get(field) in (None, "", [], {})
    ]
    if missing_formula_fields:
        raise ValueError(
            "Builder Gate 1 formula-driven experiment is incomplete: "
            + ", ".join(missing_formula_fields)
        )
    selected_methods = formula_design.get("selected_methods", [])
    required_method_fields = {
        "method_id",
        "title",
        "pattern_ids",
        "description",
        "process_summary",
    }
    if any(
        not isinstance(item, dict)
        or any(item.get(field) in (None, "", [], {}) for field in required_method_fields)
        for item in selected_methods
    ):
        raise ValueError(
            "Builder Gate 1 selected experiment methods are missing their process contract"
        )
    selected_pattern_ids = {
        str(item)
        for item in formula_design.get("selected_pattern_ids", [])
        if str(item).strip()
    }
    method_pattern_ids = {
        str(pattern_id)
        for method in selected_methods
        for pattern_id in method.get("pattern_ids", [])
        if str(pattern_id).strip()
    }
    if selected_pattern_ids != method_pattern_ids:
        raise ValueError(
            "Builder Gate 1 method and experiment-pattern selections are disconnected"
        )

    def row_value(section: str, key: str) -> str:
        rows = payload.get(section, [])
        return next(
            (
                _text(item.get("value"))
                for item in rows
                if isinstance(item, dict) and item.get("key") == key
            ),
            "",
        ) if isinstance(rows, list) else ""

    required_design_values = {
        "research_question": row_value("design_definition", "research_question"),
        "independent_variable": row_value("design_definition", "independent_variable"),
        "dependent_variable": row_value("design_definition", "dependent_variable"),
        "controlled_variables": row_value("design_definition", "controlled_variables"),
        "research_hypothesis": row_value("design_definition", "research_hypothesis"),
    }
    missing_design_values = [
        field for field, value in required_design_values.items() if not value
    ]
    if missing_design_values:
        raise ValueError(
            "Builder Gate 1 research definition is incomplete: "
            + ", ".join(missing_design_values)
        )
    if len(payload.get("learning_goals", [])) < 4:
        raise ValueError("Builder Gate 1 requires the four EMVR learning goals")
    if len(payload.get("student_tasks", [])) < 5:
        raise ValueError("Builder Gate 1 requires a complete ordered student flow")
    physics = payload.get("physics", {})
    if not isinstance(physics, dict) or any(
        physics.get(field) in (None, "", [], {})
        for field in (
            "mechanism",
            "formulas",
            "formula_support_map",
            "simulation_inputs",
            "parameter_ranges",
            "expected_results",
        )
    ):
        raise ValueError("Builder Gate 1 physics contract is incomplete")
    unresolved_paths: list[str] = []

    def scan(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                scan(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                scan(item, f"{path}[{index}]")
        elif value not in (None, "") and not is_resolved_design_value(value):
            unresolved_paths.append(path)

    scan(payload, "")
    if unresolved_paths:
        raise ValueError(
            "Builder Gate 1 input still contains unresolved design content: "
            + ", ".join(unresolved_paths[:8])
        )
    identity = payload.get("identity", [])
    lab_id = next(
        (
            str(item.get("value") or "")
            for item in identity
            if isinstance(item, dict) and item.get("key") == "lab_id"
        ),
        "",
    )
    if LAB_ID_PATTERN.fullmatch(lab_id) is None:
        raise ValueError("Builder Gate 1 input contains an invalid lab_id")
    object_ids = [
        str(item.get("object_id") or "")
        for item in payload.get("objects", [])
        if isinstance(item, dict)
    ]
    if not object_ids or len(object_ids) != len(set(object_ids)):
        raise ValueError("Builder Gate 1 object IDs must be present and unique")

    required_contract_rows = {
        "interaction_modes": (
            "interaction_modes.desktop_mouse",
            "interaction_modes.xr_actions",
            "interaction_modes.mouse_to_vr_mapping",
        ),
        "visualization": (
            "visualization.requirements",
            "visualization.update_event",
            "visualization.layer",
        ),
        "environment": ("environment.room_placement_and_adaptation",),
        "initial_and_action_states": (
            "initial_and_action_states.authored_initial_state",
            "initial_and_action_states.hidden_templates_or_loaders",
            "initial_and_action_states.first_required_action",
            "initial_and_action_states.expected_visible_after_action",
        ),
        "acceptance_and_evidence": (
            "acceptance.core_flow",
            "acceptance.result_interpretation",
            "acceptance.pass_criteria",
            "acceptance.report_questions",
        ),
    }
    missing_contract_rows = [
        f"{section}.{key}"
        for section, keys in required_contract_rows.items()
        for key in keys
        if not row_value(section, key)
    ]
    if missing_contract_rows:
        raise ValueError(
            "Builder Gate 1 operational contract is incomplete: "
            + ", ".join(missing_contract_rows)
        )


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
                "状态说明：confirmed-from-design-session 表示已由用户在 EMVR 设计过程中确认；"
                "builder-policy-reference 表示来自 Builder Pack 的固定约束；"
                "builder-runtime-check 表示由 Builder 在实际 Unity 工作区中核对。",
                small_style,
            ),
        ]
    )

    sections = [
        ("1. Lab identity", data["identity"]),
        ("2. Source material and traceability", data["source_material"]),
        (
            "3. Formula-driven experiment brief",
            [
                _field(
                    f"formula_driven_experiment.{key}",
                    value,
                )
                for key, value in data["formula_driven_experiment"].items()
                if key != "status"
            ],
        ),
        ("4. Research definition", data["design_definition"]),
        (
            "5. Learning goals",
            [_field(f"learning_goals[{i}]", goal) for i, goal in enumerate(data["learning_goals"], 1)],
        ),
        (
            "6. Student tasks",
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
            "7. Physics",
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
            "8. Object inventory",
            [
                _field(
                    f"objects[{obj['object_id']}]",
                    "；".join(f"{key}={value}" for key, value in obj.items() if key not in {"object_id", "status"}),
                    status=obj["status"],
                )
                for obj in data["objects"]
            ],
        ),
        ("9. Presets", data["presets"]),
        ("10. Interaction modes", data["interaction_modes"]),
        ("11. Visualization", data["visualization"]),
        ("12. Environment and Game View", data["environment"]),
        ("13. Reuse requirements", data["reuse_requirements"]),
        ("14. Scene", data["scene"]),
        ("15. Initial and post-action states", data["initial_and_action_states"]),
        ("16. Acceptance and evidence", data["acceptance_and_evidence"]),
        ("17. Builder runtime constraints", data["builder_runtime_constraints"]),
    ]
    for heading, rows in sections:
        story.append(p(heading, heading_style))
        story.append(field_table(rows or [_field(f"{heading}.content", _UNRESOLVED)]))

    story.append(p("18. Handoff instructions", heading_style))
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
