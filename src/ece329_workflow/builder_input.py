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
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .builder_requirements import (
    LAB_ID_PATTERN,
    builder_requirement_values,
    is_resolved_design_value,
    validate_builder_requirements,
)
from .builder_defaults import (
    IMPLEMENTATION_DEFAULTS_FIELD,
    measurement_disables_probe,
    measurement_is_qualitative_only,
)
from .builder_portability import (
    PACK_NAME, ROOT_DISCOVERY, SOURCE_BOUNDARY, UNITY_PROJECT,
    embed_supplied_references, validate_portable_content, validate_embedded_reference_links,
)
from .models import DesignSession, InteractionState, Stage
from .knowledge_base import KNOWLEDGE
from .reporting import (
    _formula_expression_for_report,
    _pdf_safe_formula_text,
    effective_emvr_stage_payload,
    effective_experiment_brief,
    validate_emvr_report_completeness,
)


_LATIN_RUN = re.compile(
    r"[A-Za-z0-9_./:+()=\-*'|^<>\[\]]+(?:\s+[A-Za-z0-9_./:+()=\-*'|^<>\[\]]+)*"
)
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
    if isinstance(value, list):
        return "；".join(
            part for item in value if (part := _text(item, depth=depth + 1))
        )
    if isinstance(value, dict):
        # Export is lossless: a display/name/value key must not hide siblings,
        # numeric zero, false, or nested implementation details.
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
        object_action = _text(item.get("student_interaction")) or "仅观察，不直接操作"
        rows.append(
            {
                "object_id": f"OBJ_{index:02d}",
                "display_name": name,
                "object_type": _text(item.get("category")) or "unresolved",
                "role": _text(item.get("purpose")) or "unresolved",
                "initial_state": _text(item.get("physics_or_data_state")) or "unresolved",
                "desktop_interaction": f"{object_action}；对应桌面方式见本文 interaction_modes.mouse_to_vr_mapping",
                "xr_interaction": object_action,
                "visible_feedback": _text(item.get("visual_feedback")) or "unresolved",
                "required": _text(item.get("required")) or "unresolved",
                "status": "confirmed-from-design-session",
            }
        )
    return rows


def _builder_formula_contracts(
    experiment_brief: dict[str, Any],
    theory: dict[str, Any],
) -> list[dict[str, str]]:
    """Return exact, font-safe equations with their implementation conditions."""

    formula_by_id = {
        str(item.get("id") or ""): item
        for item in KNOWLEDGE.formulas
        if isinstance(item, dict)
    }
    primary_ids = [
        str(item) for item in experiment_brief.get("primary_formula_ids", []) if str(item)
    ]
    supporting_ids = [
        str(item) for item in experiment_brief.get("supporting_formula_ids", []) if str(item)
    ]
    contracts: list[dict[str, str]] = []
    for role, formula_ids in (("primary", primary_ids), ("supporting", supporting_ids)):
        for formula_id in formula_ids:
            formula = formula_by_id.get(formula_id)
            if not formula:
                raise ValueError(f"Builder selected formula reference is unknown: {formula_id}")
            contracts.append(
                {
                    "formula_id": formula_id,
                    "role": role,
                    "name": _text(formula.get("name")),
                    "expression": _formula_expression_for_report(
                        formula.get("expression")
                    ),
                    "conditions": _text(formula.get("conditions")),
                }
            )
    if contracts:
        return contracts

    # Legacy completed sessions may not retain formula IDs. Preserve their
    # confirmed equations, but still normalize glyphs for the Builder PDF.
    for index, formula in enumerate(theory.get("core_equations", []), start=1):
        if isinstance(formula, dict):
            expression = _first_value(
                formula.get("expression"), formula.get("equation"), formula.get("display")
            )
            name = _first_value(formula.get("name"), f"legacy_formula_{index}")
            conditions = _first_value(formula.get("conditions"), experiment_brief.get("boundary_conditions"), theory.get("assumptions")) or _UNRESOLVED
        else:
            expression = _text(formula)
            name = f"legacy_formula_{index}"
            conditions = _first_value(experiment_brief.get("boundary_conditions"), theory.get("assumptions")) or _UNRESOLVED
        if expression:
            contracts.append(
                {
                    "formula_id": f"legacy_formula_{index}",
                    "role": "primary",
                    "name": name,
                    "expression": _formula_expression_for_report(expression),
                    "conditions": conditions,
                }
            )
    return contracts


def _selected_formula_adjustable_inputs(
    formula_flow: dict[str, Any],
    experiment_brief: dict[str, Any],
    parameter_contract: str,
) -> list[dict[str, Any]]:
    """Expose only the inputs actually selected as experiment variables."""

    selection = formula_flow.get("formula_selection", {})
    selection = selection if isinstance(selection, dict) else {}
    selected_profile_ids = {
        str(item)
        for item in [
            *selection.get("primary_profile_ids", []),
            *selection.get("supporting_profile_ids", []),
        ]
        if str(item)
    }
    variations: list[dict[str, Any]] = []
    for profile in KNOWLEDGE.public_formula_design_profiles():
        if str(profile.get("profile_id") or "") not in selected_profile_ids:
            continue
        for item in profile.get("supported_variations", []):
            if not isinstance(item, dict) or not item.get("quantity"):
                continue
            variations.append(item)

    def match_variation(quantity: str) -> dict[str, Any] | None:
        tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,}", quantity)
            if len(token) >= 2
        }
        ranked = []
        for variation in variations:
            candidate = _text(variation.get("quantity"))
            lowered = candidate.casefold()
            score = sum(token in lowered or lowered in token for token in tokens)
            if quantity.casefold() in lowered or lowered in quantity.casefold():
                score += 3
            ranked.append((score, variation))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return ranked[0][1] if ranked and ranked[0][0] > 0 else None

    rows: list[dict[str, Any]] = []
    for raw in experiment_brief.get("changed_quantities", []):
        quantity = _text(raw)
        if not quantity:
            continue
        variation = match_variation(quantity)
        symbols = [
            _formula_expression_for_report(value)
            for value in (variation or {}).get("symbols", [])
            if _text(value)
        ]
        units = [
            _formula_expression_for_report(value)
            for value in (variation or {}).get("units", [])
            if _text(value)
        ]
        discrete = bool(re.search(r"(?:符号|配置|类型|极性|模式|材料)", quantity))
        mixed = discrete and bool(re.search(r"距离|位置|电荷量|频率|幅值|distance|position", quantity, re.I))
        rows.append(
            {
                "quantity": quantity,
                "symbols": symbols or ["defined in the confirmed formula contract"],
                "units": (["mixed controls: each quantity uses its own unit in physics.input_parameter_contract; never assign one categorical unit to the whole group"]
                          if mixed else units or (["categorical; numeric unit not applicable"] if discrete else ["see physics.input_parameter_contract in this PDF"])),
                "control_role": "adjustable experiment independent variable",
                "control_specification": parameter_contract,
            }
        )
    return rows


def _student_task_contracts(
    steps: list[str],
    measurement_contract: str,
) -> list[dict[str, str]]:
    """Turn each confirmed procedure line into an implementable Unity transition."""
    from .procedure_contract import has_positive_action

    rows: list[dict[str, str]] = []
    previous_state = "READY"
    for index, step in enumerate(steps, start=1):
        # A snapshot noun is not a Capture command: Restore, clearing and
        # inspecting saved evidence must never create fresh evidence.
        capture_requested = has_positive_action(step,
            r"(?<![A-Za-z])Capture(?![A-Za-z])|(?:保存|记录)(?!的|过)[^。；;,，]{0,24}(?:快照|结果|读数|基准)|等待[^。；;]{0,35}记录")
        restore_requested = has_positive_action(step, r"(?<![A-Za-z])Restore(?![A-Za-z])|恢复(?:最近|已保存|所选|指定|第\S+张)?快照")
        # "每轮比较前恢复基准" and "比较情形" name context, not a
        # request to enter comparison before any snapshots exist.
        compare_requested = has_positive_action(step,
            r"(?:并排|(?:比较|对照)(?!前|条件|配置|组|情形|方案|案例|实验|过程|逻辑|目的))",
        )
        if has_positive_action(step, r"(?:加载|恢复|建立)[^。；;]*基准"):
            action = "load the confirmed baseline preset"
            response = "Restore baseline parameters and refresh once; preserve saved snapshots and current step"
            evidence = "Parameters match the baseline in this PDF; earlier comparison evidence remains available"
            exit_state = "VALID"
        elif has_positive_action(step, r"(?:Reset|重置|恢复.*初始)"):
            action = "activate Reset or load the confirmed baseline preset"
            response = "ResetController restores all confirmed defaults; model and UI refresh once"
            evidence = "Parameters and Status match the Initial/Reset contract"
            exit_state = "VALID"
        elif restore_requested:
            action = "activate Restore for the named saved snapshot"
            response = "Common SnapshotStore restores all saved inputs and object states; recalculate once; preserve snapshots and never Capture implicitly"
            evidence = "Restored parameters and readouts match the selected snapshot; no additional snapshot ID exists"
            exit_state = "VALID"
        elif has_positive_action(step, r"(?:调节|改变|拖动|移动|切换|选择|设置|设为)"):
            action = "change exactly the named independent-variable control"
            response = "ParameterController validates the value, then triggers one model and visualization refresh"
            evidence = f"Status shows the accepted value and Results follows: {measurement_contract}"
            exit_state = "VALID"
        elif re.search(r"(?:解释|分析|判断|总结|回答)", step):
            action = "submit the stated interpretation using the displayed evidence"
            response = "LabFlowController checks that required comparisons exist and marks the task complete"
            evidence = "Completion status cites the snapshot IDs and the confirmed acceptance criterion"
            exit_state = "COMPLETE"
            if compare_requested:
                action = "compare the named saved snapshots; then " + action
                response = "Require at least two valid compatible snapshots before comparison and interpretation; " + response
        elif compare_requested:
            action = "select the saved cases named in this step and start comparison"
            response = "Require at least two valid compatible snapshots; then lock a shared view/scale and present the cases together"
            evidence = "At least two valid compatible snapshots show their IDs, parameters, results, and shared visual encoding"
            exit_state = "COMPARING"
        elif capture_requested:
            action = "press Capture after the current state becomes VALID"
            response = "Domain adapter wraps Common object-state JSON with parameters, outputs, validity, comparison label, and screenshot ID"
            evidence = "Results shows a new unique snapshot ID with the current parameter set"
            exit_state = "CAPTURED"
        else:
            action = "perform the named observation and wait for the calculation-complete status"
            response = "MeasurementController refreshes the confirmed readout or qualitative comparison"
            evidence = f"Results visibly reports: {measurement_contract}"
            exit_state = "VALID"
        # A single confirmed line can contain several actions. Retain its
        # full instruction and append capture/comparison after a parameter or
        # baseline action instead of losing the latter half to the first match.
        if exit_state in {"VALID", "READY"} and capture_requested:
            action += "; then explicitly Capture the valid result"
            response += "; Common SnapshotStore saves one snapshot after calculation succeeds"
            evidence += "; Results lists its unique snapshot ID"
            exit_state = "CAPTURED"
        if exit_state in {"VALID", "CAPTURED"} and compare_requested:
            action += "; compare the named saved snapshots"
            response += "; comparison is enabled only with at least two valid compatible snapshots; otherwise retain the current valid state (CAPTURED only if a snapshot exists); use the same scale and view"
            evidence += "; at least two valid compatible snapshot IDs are visibly compared"
            exit_state = "COMPARING"
        if exit_state == "COMPLETE" and index < len(steps):
            exit_state = "VALID"
            response = "Record this step's interpretation; continue to the next required step"
        if restore_requested:
            # Preserve earlier comparisons/interpretation in mixed steps;
            # the final Restore must not be replaced by automatic Capture.
            action += "; perform every stated comparison/interpretation and Restore in the written order"
            response += "; after Restore recompute once from the saved input state without adding a snapshot"
            evidence += "; restored readouts match the saved inputs"
            if not capture_requested:
                exit_state = "VALID"
        if has_positive_action(step, r"(?<![A-Za-z])Back(?![A-Za-z])|返回(?:开始页|Start)"):
            action += "; finish the preceding stated checks, then Back to Start"
            response += "; cancel pending work and leave the Lab without starting another computation"
            evidence += "; Start is visible and no stale result is committed"
            exit_state = "START"
        rows.append(
            {
                "step_id": f"S{index}",
                "goal": step,
                "entry_state": previous_state,
                "expected_action": f"{step}；Unity操作映射：{action}",
                "unity_response": response,
                "observable_evidence": evidence,
                "success_criteria": f"Only advance when {evidence}; satisfy the complete stated step, then advance the Common runner once; exit_state below describes successful completion, never a failed action",
                "exit_state": exit_state,
                "status": "confirmed-from-design-session",
            }
        )
        previous_state = exit_state
    return rows


def _contract_parts(value: str, *, limit: int = 900) -> list[str]:
    parts: list[str] = []
    for line in str(value or "").splitlines() or [str(value or "")]:
        text = line.strip()
        while text:
            parts.append(text[:limit])
            text = text[limit:]
    return parts


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
            "comparison_cases": _as_list(research.get("comparison_cases")),
            "boundary_conditions": _as_list(value_limits.get("limitations")),
        }
    method_by_id = {
        str(item.get("method_id") or ""): item
        for item in formula_flow.get("experiment_methods", [])
        if isinstance(item, dict)
    }
    if isinstance(authoritative_formula_brief, dict) and authoritative_formula_brief:
        missing_methods = set(experiment_brief.get("selected_experiment_method_ids", [])) - set(method_by_id)
        if missing_methods:
            raise ValueError("Builder selected method references are unknown: " + ", ".join(sorted(missing_methods)))
    selected_methods = [
        {
            "method_id": method_id,
            "title": _text(method_by_id[method_id].get("title")),
            "pattern_ids": list(method_by_id[method_id].get("pattern_ids", [])),
            "description": _formula_expression_for_report(
                _text(method_by_id[method_id].get("description"))
            ),
            "process_summary": _formula_expression_for_report(
                _text(method_by_id[method_id].get("process_summary"))
            ),
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
    parameter_contract = _formula_expression_for_report(
        builder_values["parameter_specifications"]
    )
    constants_and_media = _formula_expression_for_report(
        builder_values["model_constants_and_media"]
    )
    measurement_contract = _formula_expression_for_report(
        builder_values["measurement_specifications"]
    )
    implementation_defaults = builder_values[IMPLEMENTATION_DEFAULTS_FIELD]
    initial_reset_state = _formula_expression_for_report(
        builder_values["initial_reset_state"]
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
    expected_results = _as_list(builder_values["expected_results"])
    objects = _object_rows(
        setup.get("object_inventory"), builder_values["desktop_interaction_plan"]
    )
    if measurement_disables_probe(measurement_contract):
        objects = [
            item
            for item in objects
            if not re.search(r"(?:探针|空间观察与测量工具)", item["display_name"])
        ]
    first_action = steps[0] if steps else _UNRESOLVED
    builder_root = PACK_NAME
    unity_project_path = UNITY_PROJECT
    formula_contracts = _builder_formula_contracts(experiment_brief, theory)
    formula_adjustable_inputs = _selected_formula_adjustable_inputs(
        formula_flow, experiment_brief, parameter_contract
    )
    student_tasks = _student_task_contracts(steps, measurement_contract)
    visualization_layer = (
        "定性空间比较：固定视角、种子数、色标和快照布局；不创建数值纵轴或理论曲线。"
        if measurement_is_qualitative_only(measurement_contract)
        else setup.get("visualization_layer")
    )
    implementation_rows = [
        _field(
            f"implementation_defaults.approved_contract_part_{index:02d}",
            part,
            status="approved-before-export",
        )
        for index, part in enumerate(_contract_parts(implementation_defaults), start=1)
    ]
    implementation_rows.append(
        _field(
            "implementation_defaults.revision_rule",
            (
                "Any later change to the experiment variables, measurements, procedure, UI, or Unity mapping "
                "invalidates this approval and requires a regenerated proposal before export."
            ),
            status="workflow-invariant",
        )
    )
    primary_formulas = [
        f"{item['name']}: {item['expression']}"
        for item in formula_contracts
        if item["role"] == "primary"
    ]
    supporting_formulas = [
        f"{item['name']}: {item['expression']}"
        for item in formula_contracts
        if item["role"] == "supporting"
    ]
    comparison_cases = list(experiment_brief.get("comparison_cases", []))
    if not comparison_cases:
        comparison_cases = _as_list(research.get("comparison_cases"))
    if not comparison_cases:
        comparison_cases = [
            "按已确认的公式自变量契约逐项执行比较：" + parameter_contract
        ]

    payload = {
        "document": {
            "title": "EMVR Builder Pack — Gate 1 Requirements Input",
            "purpose": (
                "作为 EMVR Builder Pack 阶段 1（Brief confirmed）的原创新实验输入。"
                "本实验使用 integrated-development。"
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
            _field("experiment_origin", "original-new-experiment", status="confirmed-from-design-session"),
            _field("workflow_mode", "integrated-development", status="confirmed-from-design-session"),
            _field("status", "draft", status="builder-template-reference"),
        ],
        "execution_context": [
            _field("execution.builder_pack_root", builder_root, status="builder-runtime-check"),
            _field("execution.unity_project_relative", unity_project_path, status="pack-relative-path"),
            _field(
                "execution.local_root_discovery",
                ROOT_DISCOVERY,
                status="builder-processing-instruction",
            ),
            _field(
                "execution.initialization_command",
                (
                    "python Tools/labflow/labflow.py new "
                    f"--lab-id {lab_id} --title \"{title}\" --domain \"ECE329 electromagnetics\" "
                    f"--scene \"Assets/Scenes/{lab_id}.unity\" --mode integrated-development"
                ),
                status="builder-processing-instruction",
            ),
        ],
        "source_material": [
            _field("source_material.handbook", "this Gate 1 input PDF"),
            _field(
                "source_material.additional_notes",
                f"ECE329 EMVR design session {session.design_id}",
            ),
            _field(
                "source_material.course_scope",
                "ECE329：所用公式、适用条件、固定输入和实验约束见本文 Physics；必要补充内容见本文内嵌参考。课程出处是文献标识，不是包外文件读取要求。",
                status="confirmed-from-course-scope",
            ),
            _field(
                "source_material.builder_treatment",
                SOURCE_BOUNDARY,
                status="builder-processing-instruction",
            ),
        ],
        "formula_driven_experiment": {
            "topic": _text(experiment_brief.get("topic")),
            "summary": _text(experiment_brief.get("summary")),
            "primary_formulas": primary_formulas,
            "supporting_formulas": supporting_formulas
            or ["none (no supporting formula is required for this design)"],
            "formula_contracts": formula_contracts,
            "selected_formula_adjustable_inputs": formula_adjustable_inputs,
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
            "comparison_cases": comparison_cases,
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
        "student_tasks": student_tasks,
        "physics": {
            "mechanism": _text(theory.get("physical_mechanism")) or _UNRESOLVED,
            "formulas": formula_contracts,
            "formula_support_map": _as_list(theory.get("formula_support_map")),
            "units": _as_list(parameter_contract),
            "simulation_inputs": _as_list(theory.get("simulation_inputs")),
            "parameter_ranges": _as_list(parameter_contract),
            "formula_input_policy": (
                "Every selected-formula input designated as an experiment independent variable must be adjustable in desktop and VR modes. "
                "Constants, media properties, and controls must use the confirmed fixed-input contract."
            ),
            "input_parameter_contract": parameter_contract,
            "constants_and_media": constants_and_media,
            "numerical_model": builder_values["numerical_model_specifications"],
            "assumptions": (
                _as_list(theory.get("assumptions"))
                + _as_list(value_limits.get("limitations"))
            ) or [item["conditions"] for item in formula_contracts],
            "expected_results": expected_results,
        },
        "objects": objects,
        "interaction_modes": [
            _field("interaction_modes.desktop_mouse", "required"),
            _field("interaction_modes.xr_device_simulator", "required", status="builder-policy-reference"),
            _field("interaction_modes.real_vr", "required", status="builder-policy-reference"),
            _field("interaction_modes.xr_actions", setup.get("interactions")),
            _field("interaction_modes.measurement_interface", measurement_contract),
            _field("interaction_modes.mouse_to_vr_mapping", builder_values["desktop_interaction_plan"]),
        ],
        "visualization": [
            _field(
                "visualization.requirements",
                visualization.get("student_visualization_requirements") or measurement_contract,
                status="confirmed-from-design-session",
            ),
            _field("visualization.trend_annotation", visualization.get("trend_annotation")),
            _field("visualization.update_event", visualization.get("unity_update_event")),
            _field("visualization.layer", visualization_layer),
            _field("visualization.metric_and_probe_definitions", measurement_contract),
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
                "Game View must keep instruction, experiment, parameters, status, and result regions visible.",
                status="builder-policy-reference",
            ),
        ],
        "presets": [
            _field("presets.parameter_values", parameter_contract),
            _field(
                "presets.reference_condition",
                variables.get("reference_condition"),
                status="confirmed-from-design-session",
            ),
            _field("presets.initial_and_reset_state", initial_reset_state),
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
                ["instruction", "experiment", "parameters", "status", "result"],
                status="builder-policy-reference",
            ),
        ],
        "implementation_defaults": implementation_rows,
        "initial_and_action_states": [
            _field(
                "initial_and_action_states.authored_initial_state",
                initial_reset_state,
                status="confirmed-from-design-session",
            ),
            _field(
                "initial_and_action_states.reset_state",
                initial_reset_state,
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
                measurement_contract,
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
            _field("current_editor_state.builder_pack_root", builder_root, status="builder-runtime-check"),
            _field("current_editor_state.unity_project_relative", unity_project_path, status="pack-relative-path"),
            _field("current_editor_state.unity_version", "Read the confirmed host UnityProject/ProjectSettings/ProjectVersion.txt; use its locked Editor version", status="builder-runtime-check"),
            _field("current_editor_state.unity_open", "Builder must read the active editor state at Gate 1", status="builder-runtime-check"),
            _field("current_editor_state.compiling", "Builder must read the active editor state at Gate 1", status="builder-runtime-check"),
            _field("current_editor_state.play_mode", "Builder must read the active editor state at Gate 1", status="builder-runtime-check"),
            _field("workflow_limits.run_batchmode", "false", status="builder-policy-reference"),
            _field("workflow_limits.hand_edit_scene_yaml", "false", status="builder-policy-reference"),
            _field("workflow_limits.no_visible_progress_minutes", "5", status="builder-policy-reference"),
            _field("workflow_limits.unity_wait_limit_minutes", "10", status="builder-policy-reference"),
        ],
        "handoff_notes": [
            "这是原创新实验，Builder 必须使用 integrated-development，不得改成 blind-rebuild。",
            ROOT_DISCOVERY,
            "Builder 必须先把本 PDF 映射为 LabSpecs/<lab_id>/brief.yaml，再由用户确认 Gate 1。",
            "设计输入的确认仅覆盖其字段标明的范围；待确认建议不可伪装为既有决定，Gate 审批需由实际 Builder 工作流单独记录。",
            "本 PDF 仅描述实验设计，不授权创建 Unity 场景、代码或批准任何 Gate。",
        ],
    }
    payload, embedded = embed_supplied_references(
        payload, session.design_context.get("builder_reference_material", [])
    )
    payload["embedded_reference_material"] = [
        _field("embedded_references.formula_basis", formula_contracts,
               status="copied-course-formula-content",
               note="已复制所选课程公式及适用条件；数值输入见本文 physics，不读取外部课件。"),
        *embedded,
    ]
    payload["value_semantics"] = [
        _field("value.required", "必需值缺失会阻止导出；0和false是有效值，不得当作空白。", status="export-invariant"),
        _field("value.runtime", "Unity版本与打开/编译/Play Mode状态由本机检查；PDF不伪造运行时数值。", status="builder-runtime-check"),
        _field("value.numeric_morphology_axis", "not-applicable：本实验采用定性空间比较，没有数值形态纵轴或理论曲线。"
               if measurement_is_qualitative_only(measurement_contract) else "见本文 measurement_specifications 的指标、算法和单位。",
               status="not-applicable" if measurement_is_qualitative_only(measurement_contract) else "confirmed-from-design-session"),
        _field("value.spatial_probe", "not-applicable：本实验不使用空间探针，无需填写探针坐标或局部场读数。"
               if measurement_disables_probe(measurement_contract) else measurement_contract,
               status="not-applicable" if measurement_disables_probe(measurement_contract) else "confirmed-from-design-session"),
    ]
    validate_builder_gate1_input(payload)
    return payload


def validate_builder_gate1_input(payload: dict[str, Any]) -> None:
    """Reject incomplete or contract-incompatible Builder handoffs."""

    validate_portable_content(payload)
    required_sections = {
        "embedded_reference_material",
        "value_semantics",
        "document",
        "identity",
        "execution_context",
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
        "implementation_defaults",
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
    reference_ids = [str(row.get("key", "")).removeprefix("embedded_references.")
                     for row in payload["embedded_reference_material"]
                     if isinstance(row, dict) and str(row.get("key", "")).startswith("embedded_references.REF_")]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("Builder embedded reference IDs must be unique")
    validate_embedded_reference_links(payload, set(reference_ids))
    formula_design = payload.get("formula_driven_experiment", {})
    if not isinstance(formula_design, dict):
        raise ValueError("Builder Gate 1 formula-driven experiment must be an object")
    required_formula_fields = (
        "topic",
        "summary",
        "primary_formulas",
        "formula_contracts",
        "selected_formula_adjustable_inputs",
        "composition_strategy",
        "selected_methods",
        "selected_pattern_ids",
        "objects",
        "operations",
        "changed_quantities",
        "observed_quantities",
        "comparison_cases",
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
    formula_contracts = formula_design.get("formula_contracts", [])
    if any(
        not isinstance(item, dict)
        or any(
            item.get(field) in (None, "", [], {})
            for field in ("formula_id", "role", "name", "expression", "conditions")
        )
        for item in formula_contracts
    ):
        raise ValueError("Builder Gate 1 formula contracts are incomplete")
    formula_ids = [item["formula_id"] for item in formula_contracts]
    if len(formula_ids) != len(set(formula_ids)):
        raise ValueError("Builder formula references must be unique across primary and supporting roles")
    if payload.get("physics", {}).get("formulas") != formula_contracts:
        raise ValueError("Builder physics and selected formula contracts are disconnected")
    if any(
        re.search(r"[₀₁₂₃ᵢ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]", str(item.get("expression") or ""))
        for item in formula_contracts
        if isinstance(item, dict)
    ):
        raise ValueError("Builder Gate 1 formula expressions contain unsafe PDF glyphs")
    adjustable_inputs = formula_design.get("selected_formula_adjustable_inputs", [])
    if any(
        not isinstance(item, dict)
        or not item.get("quantity")
        or not item.get("symbols")
        or not item.get("units")
        or not item.get("control_specification")
        or not str(item.get("control_role") or "").startswith("adjustable")
        for item in adjustable_inputs
    ):
        raise ValueError(
            "Builder Gate 1 selected-formula inputs must declare an adjustable control role"
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
    task_ids = [task.get("step_id") for task in payload["student_tasks"] if isinstance(task, dict)]
    if task_ids != [f"S{index}" for index in range(1, len(payload["student_tasks"]) + 1)]:
        raise ValueError("Builder student step IDs must be unique and ordered S1 through Sn")
    required_task_fields = {
        "step_id",
        "goal",
        "entry_state",
        "expected_action",
        "unity_response",
        "observable_evidence",
        "success_criteria",
        "exit_state",
        "status",
    }
    if any(
        not isinstance(task, dict)
        or any(task.get(field) in (None, "", [], {}) for field in required_task_fields)
        or task.get("expected_action") == "observe_or_interact"
        for task in payload.get("student_tasks", [])
    ):
        raise ValueError("Builder Gate 1 student tasks lack implementable state transitions")
    physics = payload.get("physics", {})
    if not isinstance(physics, dict) or any(
        physics.get(field) in (None, "", [], {})
        for field in (
            "mechanism",
            "formulas",
            "formula_support_map",
            "simulation_inputs",
            "parameter_ranges",
            "formula_input_policy",
            "input_parameter_contract",
            "constants_and_media",
            "numerical_model",
            "expected_results",
        )
    ):
        raise ValueError("Builder Gate 1 physics contract is incomplete")
    unresolved_paths: list[str] = []

    def scan(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key != "note" and item in (None, "", [], {}):
                    unresolved_paths.append(f"{path}.{key}")
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
    workflow_mode = next(
        (
            str(item.get("value") or "")
            for item in identity
            if isinstance(item, dict) and item.get("key") == "workflow_mode"
        ),
        "",
    )
    if workflow_mode != "integrated-development":
        raise ValueError("Original EMVR experiments must use integrated-development")
    builder_root = row_value(
        "execution_context", "execution.builder_pack_root"
    )
    unity_root = row_value(
        "execution_context", "execution.unity_project_relative"
    )
    if builder_root != PACK_NAME or unity_root != UNITY_PROJECT:
        raise ValueError("Builder Gate 1 must locate the local Pack and use relative UnityProject")
    object_ids = [
        str(item.get("object_id") or "")
        for item in payload.get("objects", [])
        if isinstance(item, dict)
    ]
    if not object_ids or len(object_ids) != len(set(object_ids)):
        raise ValueError("Builder Gate 1 object IDs must be present and unique")
    for task in payload["student_tasks"]:
        referenced_objects = set(re.findall(r"(?<![A-Za-z0-9_])OBJ_\d+(?![A-Za-z0-9_])", _text(task)))
        if referenced_objects - set(object_ids):
            raise ValueError(f"Builder student step {task['step_id']} references an unknown object ID")
    required_object_fields = {
        "object_id",
        "display_name",
        "object_type",
        "role",
        "initial_state",
        "desktop_interaction",
        "xr_interaction",
        "visible_feedback",
        "required",
        "status",
    }
    if any(
        not isinstance(item, dict)
        or any(item.get(field) in (None, "", [], {}, "unresolved") for field in required_object_fields)
        for item in payload.get("objects", [])
    ):
        raise ValueError("Builder Gate 1 object inventory contains blank implementation fields")
    measurement_definition = row_value(
        "visualization", "visualization.metric_and_probe_definitions"
    )
    if measurement_disables_probe(measurement_definition) and any(
        re.search(r"(?:探针|空间观察与测量工具)", str(item.get("display_name") or ""))
        for item in payload.get("objects", [])
        if isinstance(item, dict)
    ):
        raise ValueError("Builder Gate 1 disables the probe but still requires a probe object")

    table_sections = {
        section
        for section in required_sections
        if isinstance(payload.get(section), list)
        and section not in {"learning_goals", "student_tasks", "objects", "handoff_notes"}
    }
    blank_rows = [
        f"{section}[{index}]"
        for section in table_sections
        for index, row in enumerate(payload.get(section, []))
        if not isinstance(row, dict)
        or any(row.get(field) in (None, "", [], {}) for field in ("key", "value", "status"))
    ]
    if blank_rows:
        raise ValueError(
            "Builder Gate 1 contains blank PDF table rows: " + ", ".join(blank_rows[:8])
        )

    required_contract_rows = {
        "execution_context": (
            "execution.builder_pack_root",
            "execution.unity_project_relative",
            "execution.local_root_discovery",
            "execution.initialization_command",
        ),
        "interaction_modes": (
            "interaction_modes.desktop_mouse",
            "interaction_modes.xr_actions",
            "interaction_modes.mouse_to_vr_mapping",
        ),
        "visualization": (
            "visualization.requirements",
            "visualization.update_event",
            "visualization.layer",
            "visualization.metric_and_probe_definitions",
        ),
        "environment": ("environment.room_placement_and_adaptation",),
        "initial_and_action_states": (
            "initial_and_action_states.authored_initial_state",
            "initial_and_action_states.reset_state",
            "initial_and_action_states.hidden_templates_or_loaders",
            "initial_and_action_states.first_required_action",
            "initial_and_action_states.expected_visible_after_action",
        ),
        "implementation_defaults": (
            "implementation_defaults.approved_contract_part_01",
            "implementation_defaults.revision_rule",
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
    text = _pdf_safe_formula_text(value)
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
    return render_builder_gate1_payload_pdf(data)


def render_builder_gate1_payload_pdf(data: dict[str, Any]) -> bytes:
    validate_builder_gate1_input(data)
    return render_builder_review_pdf(data["document"], _builder_sections(data), data["handoff_notes"])


def render_builder_review_pdf(
    metadata: dict[str, str], sections: list[tuple[str, list[dict[str, str]]]], notes: list[str]
) -> bytes:
    """Shared renderer for validated exports and explicitly labelled document reviews."""
    validate_portable_content([metadata, sections, notes])
    for heading, rows in sections:
        if not rows or any(not _text(row.get(key)) for row in rows for key in ("key", "value", "status")):
            raise ValueError(f"Blank PDF field in {heading}")
    data = {"document": metadata}
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
        subject="EMVR integrated-development Builder Pack Gate 1 input",
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
        keepWithNext=True,
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
            # Split the VALUE into measured paragraph fragments, then repeat
            # the field ID/status on every fragment. Never create unlabeled
            # continuation cells or truncate text to satisfy a page budget.
            pending = [p(value)]
            fragments = []
            while pending:
                paragraph = pending.pop(0)
                if paragraph.wrap(92 * mm - 8, 10000)[1] <= 150:
                    fragments.append(paragraph)
                else:
                    split = paragraph.split(92 * mm - 8, 150)
                    if len(split) < 2:
                        raise ValueError(f"Cannot paginate Builder field {row['key']}")
                    fragments.append(split[0])
                    pending[0:0] = split[1:]
            for index, fragment in enumerate(fragments, 1):
                suffix = f" [part {index}/{len(fragments)}]" if len(fragments) > 1 else ""
                table_rows.append([p(row["key"] + suffix, key_style), fragment, p(row["status"], key_style)])
        table = Table(
            table_rows, colWidths=[48 * mm, 92 * mm, 40 * mm],
            repeatRows=1, splitInRow=0,
        )
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
            p("长值按 part 编号续写，每段重复字段名与状态。not-applicable须附理由；builder-runtime-check须在本机核对；proposal-needs-confirmation表示建议尚未确认。", small_style),
        ]
    )

    for heading, rows in sections:
        story.append(p(heading, heading_style))
        story.append(field_table(rows or [_field(f"{heading}.content", _UNRESOLVED)]))

    story.append(KeepTogether([p("Handoff instructions", heading_style), *[p(f"• {note}") for note in notes]]))

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#738696"))
        canvas.drawString(15 * mm, 8 * mm, "ECE329 Lab Studio | Builder Gate 1 input")
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _builder_sections(data: dict[str, Any]) -> list[tuple[str, list[dict[str, str]]]]:
    sections = [
        ("1. Lab identity", data["identity"]),
        ("2. Integrated-development execution context", data["execution_context"]),
        ("3. Source material and traceability", data["source_material"]),
        (
            "4. Formula-driven experiment brief",
            [
                _field(
                    f"formula_driven_experiment.{key}",
                    value,
                )
                for key, value in data["formula_driven_experiment"].items()
                if key != "status"
            ],
        ),
        ("5. Research definition", data["design_definition"]),
        (
            "6. Learning goals",
            [_field(f"learning_goals[{i}]", goal) for i, goal in enumerate(data["learning_goals"], 1)],
        ),
        (
            "7. Student tasks",
            [
                _field(
                    f"student_tasks[{task['step_id']}]",
                    f"goal={task['goal']}；entry_state={task['entry_state']}；"
                    f"expected_action={task['expected_action']}；unity_response={task['unity_response']}；"
                    f"observable_evidence={task['observable_evidence']}；"
                    f"success_criteria={task['success_criteria']}；exit_state={task['exit_state']}",
                    status=task["status"],
                )
                for task in data["student_tasks"]
            ],
        ),
        (
            "8. Physics",
            [
                _field("physics.mechanism", data["physics"]["mechanism"]),
                _field("physics.formulas", data["physics"]["formulas"]),
                _field("physics.formula_support_map", data["physics"]["formula_support_map"]),
                _field("physics.units", data["physics"]["units"]),
                _field("physics.simulation_inputs", data["physics"]["simulation_inputs"]),
                _field("physics.parameter_ranges", data["physics"]["parameter_ranges"]),
                _field("physics.formula_input_policy", data["physics"]["formula_input_policy"]),
                _field("physics.input_parameter_contract", data["physics"]["input_parameter_contract"]),
                _field("physics.constants_and_media", data["physics"]["constants_and_media"]),
                _field("physics.numerical_model", data["physics"]["numerical_model"]),
                _field("physics.assumptions", data["physics"]["assumptions"]),
                _field("physics.expected_results", data["physics"]["expected_results"]),
            ],
        ),
        (
            "9. Object inventory",
            [
                _field(
                    f"objects[{obj['object_id']}]",
                    "；".join(f"{key}={value}" for key, value in obj.items() if key not in {"object_id", "status"}),
                    status=obj["status"],
                )
                for obj in data["objects"]
            ],
        ),
        ("10. Presets", data["presets"]),
        ("11. Interaction modes", data["interaction_modes"]),
        ("12. Visualization and measurement definitions", data["visualization"]),
        ("13. Environment and Game View", data["environment"]),
        ("14. Reuse requirements", data["reuse_requirements"]),
        ("15. Scene", data["scene"]),
        ("16. Approved UI, flow, and Unity defaults", data["implementation_defaults"]),
        ("17. Initial and post-action states", data["initial_and_action_states"]),
        ("18. Acceptance and evidence", data["acceptance_and_evidence"]),
        ("19. Builder runtime constraints", data["builder_runtime_constraints"]),
    ]
    sections.extend([
        ("20. Embedded source material", data["embedded_reference_material"]),
        ("21. Value completeness and applicability", data["value_semantics"]),
    ])
    return sections
