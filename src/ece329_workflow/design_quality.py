from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .design_state import design_state_snapshot, ensure_design_state
from .dialogue_acts import stage_design_state_snapshot
from .models import DesignSession, InteractionState, Stage
from .turn_planning import workflow_design_snapshot


QUALITY_CATEGORIES = frozenset(
    {
        "CONSISTENCY",
        "CAUSALITY",
        "FEASIBILITY",
        "BOUNDARY_CASE",
        "COURSE_ALIGNMENT",
        "TRACEABILITY",
        "COMPLETENESS",
    }
)
QUALITY_STATUSES = frozenset({"PASS", "NEEDS_ATTENTION", "BLOCKED", "UNKNOWN"})
QUALITY_SEVERITIES = frozenset({"INFO", "MINOR", "MAJOR"})

_FIELD_LABELS = {
    "research_object": "研究对象",
    "course_relationship": "课程关系",
    "learning_objective": "学习目标",
    "research_question": "研究问题",
    "theoretical_framework": "理论依据",
    "hypothesis": "假设",
    "expected_phenomenon": "预期现象",
    "conceptual_structure": "实验结构",
    "baseline_comparisons": "比较条件",
    "independent_variable": "自变量",
    "observations": "观察量",
    "controlled_conditions": "控制条件",
    "procedure_steps": "实验流程",
    "visualization_plan": "可视化方式",
    "result_interpretation": "结果解释",
    "design_rationale": "设计依据",
    "design_value": "设计价值",
    "limitations": "局限与边界",
    "unity_objects": "Unity对象",
    "interactions": "VR交互",
    "student_summary": "学生总结",
}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(item for item in (_text(child) for child in value) if item)
    if isinstance(value, dict):
        return "；".join(item for item in (_text(child) for child in value.values()) if item)
    return str(value).strip() if value is not None else ""


def _comparison_summary(value: Any) -> str:
    """Return only student-facing comparison cases, never storage metadata."""

    if not isinstance(value, list):
        return ""
    visible: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        cases = item.get("cases")
        if not isinstance(cases, list) or not cases:
            cases = item.get("recommended_cases")
        case_keys = item.get("case_semantic_keys", {})
        case_keys = case_keys if isinstance(case_keys, dict) else {}
        if isinstance(cases, list):
            candidates = cases
        else:
            title = _text(item.get("title"))
            candidates = [title] if title else []
        for candidate in candidates:
            label = _text(candidate)
            if not label:
                continue
            identity = _text(case_keys.get(label)) or "".join(label.split()).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            visible.append(label)
    return "；".join(visible)


def _issue_id(category: str, fields: list[str], finding: str) -> str:
    material = json.dumps(
        [category, sorted(fields), "".join(finding.split()).casefold()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "quality_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _normalize_issue(raw: Any, *, source: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    category = str(raw.get("category") or "CONSISTENCY").upper()
    status = str(raw.get("status") or "NEEDS_ATTENTION").upper()
    severity = str(raw.get("severity") or "MINOR").upper()
    finding = _text(raw.get("finding"))[:1000]
    suggestion = _text(raw.get("suggestion"))[:1000]
    question = _text(raw.get("student_question"))[:500]
    fields = raw.get("fields", [])
    fields = list(
        dict.fromkeys(
            str(field).strip()
            for field in fields
            if isinstance(field, str) and str(field).strip() in _FIELD_LABELS
        )
    )[:8] if isinstance(fields, list) else []
    if not finding:
        return None
    if category not in QUALITY_CATEGORIES:
        category = "CONSISTENCY"
    if status not in QUALITY_STATUSES:
        status = "NEEDS_ATTENTION"
    if severity not in QUALITY_SEVERITIES:
        severity = "MINOR"
    return {
        "issue_id": str(raw.get("issue_id") or "").strip()[:100]
        or _issue_id(category, fields, finding),
        "category": category,
        "status": status,
        "severity": severity,
        "fields": fields,
        "finding": finding,
        "suggestion": suggestion,
        "student_question": question,
        "source": source,
    }


def normalize_quality_assessment(raw: Any) -> dict[str, Any]:
    """Validate model-authored quality reasoning without executing state changes."""

    if not isinstance(raw, dict):
        return {}
    issues = [
        issue
        for item in raw.get("issues", []) if isinstance(raw.get("issues"), list)
        for issue in [_normalize_issue(item, source="SEMANTIC_REVIEW")]
        if issue is not None
    ][:12]
    causal = raw.get("causal_chain", {})
    causal = causal if isinstance(causal, dict) else {}
    normalized_causal = {
        "cause": _text(causal.get("cause"))[:500],
        "response": _text(causal.get("response"))[:500],
        "mechanism": _text(causal.get("mechanism"))[:800],
        "comparison": _text(causal.get("comparison"))[:500],
        "answerability": _text(causal.get("answerability"))[:800],
        "status": (
            str(causal.get("status") or "UNKNOWN").upper()
            if str(causal.get("status") or "UNKNOWN").upper() in QUALITY_STATUSES
            else "UNKNOWN"
        ),
    }
    boundary_cases = []
    for item in raw.get("boundary_cases", []) if isinstance(raw.get("boundary_cases"), list) else []:
        if not isinstance(item, dict):
            continue
        case = _text(item.get("case"))[:500]
        relevance = _text(item.get("relevance"))[:700]
        if case and relevance:
            boundary_cases.append({"case": case, "relevance": relevance})
    traceability = []
    for item in raw.get("traceability", []) if isinstance(raw.get("traceability"), list) else []:
        if not isinstance(item, dict):
            continue
        design_field = str(item.get("design_field") or "").strip()
        course_item = _text(item.get("course_item"))[:500]
        purpose = _text(item.get("purpose"))[:800]
        source_type = str(item.get("source_type") or "COURSE").upper()
        if design_field in _FIELD_LABELS and course_item and purpose:
            traceability.append(
                {
                    "design_field": design_field,
                    "design_field_label": _FIELD_LABELS[design_field],
                    "course_item": course_item,
                    "purpose": purpose,
                    "source_type": (
                        source_type
                        if source_type in {"COURSE", "STUDENT", "AGENT_SUGGESTION", "EXTENSION"}
                        else "COURSE"
                    ),
                }
            )
    option_comparison = []
    for item in raw.get("option_comparison", []) if isinstance(raw.get("option_comparison"), list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))[:300]
        if not name:
            continue
        option_comparison.append(
            {
                "name": name,
                "observability": _text(item.get("observability"))[:500],
                "course_alignment": _text(item.get("course_alignment"))[:500],
                "controllability": _text(item.get("controllability"))[:500],
                "vr_suitability": _text(item.get("vr_suitability"))[:500],
                "discrimination": _text(item.get("discrimination"))[:500],
                "extra_assumptions": _text(item.get("extra_assumptions"))[:500],
                "recommendation": _text(item.get("recommendation"))[:700],
            }
        )
    return {
        "issues": issues,
        "causal_chain": normalized_causal,
        "boundary_cases": boundary_cases[:8],
        "traceability": traceability[:16],
        "option_comparison": option_comparison[:6],
    }


def _local_issue(
    category: str,
    severity: str,
    fields: list[str],
    finding: str,
    suggestion: str,
    question: str,
) -> dict[str, Any]:
    return _normalize_issue(
        {
            "category": category,
            "severity": severity,
            "status": "NEEDS_ATTENTION",
            "fields": fields,
            "finding": finding,
            "suggestion": suggestion,
            "student_question": question,
        },
        source="STRUCTURAL_REVIEW",
    ) or {}


def _required_fields_for_stage(stage: Stage, *, final_review: bool) -> tuple[str, ...]:
    if final_review:
        return (
            "research_object",
            "course_relationship",
            "learning_objective",
            "research_question",
            "independent_variable",
            "observations",
            "controlled_conditions",
            "hypothesis",
            "procedure_steps",
            "visualization_plan",
            "result_interpretation",
            "limitations",
        )
    stage_requirements = {
        Stage.VARIABLES_AND_CONDITIONS: ("research_question", "independent_variable", "observations"),
        Stage.CONCEPTUAL_PROCEDURE: ("independent_variable", "observations", "controlled_conditions"),
        Stage.EXPECTED_DATA_VISUALIZATION: ("observations", "procedure_steps", "visualization_plan"),
        Stage.RESULT_INTERPRETATION: ("research_question", "hypothesis", "result_interpretation"),
        Stage.DESIGN_VALUE_AND_LIMITATIONS: ("learning_objective", "limitations"),
        Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: (
            "research_question",
            "learning_objective",
            "independent_variable",
            "observations",
            "procedure_steps",
        ),
    }
    return stage_requirements.get(stage, ())


def _emvr_stage_output_projection(session: DesignSession) -> dict[str, Any]:
    """Project confirmed EMVR report artifacts into the quality vocabulary.

    Student-authored field state remains authoritative.  Generated stage
    artifacts are nevertheless confirmed design content after their stage is
    accepted, and the final quality panel must not call those fields missing
    merely because they use Builder-facing names.
    """

    def payload(stage: Stage) -> dict[str, Any]:
        stored = session.stage_outputs.get(stage.value, {})
        value = stored.get("stage_payload", {}) if isinstance(stored, dict) else {}
        return value if isinstance(value, dict) else {}

    objectives = payload(Stage.LEARNING_OBJECTIVES)
    variables = payload(Stage.VARIABLES_AND_CONDITIONS)
    result = payload(Stage.RESULT_INTERPRETATION)
    visualization = payload(Stage.EXPECTED_DATA_VISUALIZATION)
    stored_visualization = session.stage_outputs.get(
        Stage.EXPECTED_DATA_VISUALIZATION.value, {}
    )
    visual_object = (
        stored_visualization.get("visualization")
        if isinstance(stored_visualization, dict)
        else None
    )
    return {
        "course_relationship": payload(Stage.COURSE_MAPPING_AND_DIRECTION).get(
            "course_relationship"
        ),
        "learning_objective": [
            objectives.get(field)
            for field in (
                "conceptual_objective",
                "calculation_objective",
                "analysis_objective",
                "vr_interaction_objective",
                "observation_objective",
            )
            if objectives.get(field)
        ],
        "research_question": payload(Stage.RESEARCH_QUESTION).get(
            "main_research_question"
        ),
        "theoretical_framework": payload(Stage.THEORETICAL_FRAMEWORK).get(
            "physical_mechanism"
        ),
        "hypothesis": payload(Stage.HYPOTHESIS).get("research_hypothesis"),
        "expected_phenomenon": payload(Stage.HYPOTHESIS).get("expected_trend"),
        "independent_variable": variables.get("independent_variable"),
        "observations": variables.get("dependent_variable"),
        "controlled_conditions": variables.get("controlled_variables"),
        "procedure_steps": payload(Stage.CONCEPTUAL_PROCEDURE).get(
            "procedure_steps"
        ),
        "visualization_plan": (
            visualization.get("student_visualization_requirements")
            or visualization.get("trend_annotation")
            or visual_object
        ),
        "result_interpretation": [
            result.get(field)
            for field in (
                "if_prediction_supported",
                "if_opposite_trend",
                "if_no_clear_change",
            )
            if result.get(field)
        ],
        "limitations": payload(Stage.DESIGN_VALUE_AND_LIMITATIONS).get(
            "limitations"
        ),
    }


def _provenance_trace(session: DesignSession) -> list[dict[str, Any]]:
    state = ensure_design_state(session)
    design_values = design_state_snapshot(session)
    stage_values = stage_design_state_snapshot(session)
    provenance = state.get("field_provenance", {})
    provenance = provenance if isinstance(provenance, dict) else {}
    stage_state = session.design_context.get("stage_design_state", {})
    stage_state = stage_state if isinstance(stage_state, dict) else {}
    stage_provenance = stage_state.get("field_provenance", {})
    stage_provenance = (
        stage_provenance if isinstance(stage_provenance, dict) else {}
    )

    source_labels = {
        "STUDENT": "学生回答",
        "STUDENT_TURN": "学生回答",
        "STUDENT_CONFIRMED": "学生确认",
        "STUDENT_SEMANTIC": "学生回答",
        "AGENT_SUGGESTION": "课程助手建议",
        "AGENT_SELF_CORRECTION": "根据纠错更新",
        "VERSION_CONTROL": "从设计版本恢复",
    }
    trace: list[dict[str, Any]] = []
    for field in _FIELD_LABELS:
        value = _text(stage_values.get(field) or design_values.get(field))
        records = stage_provenance.get(field) or provenance.get(field)
        if not value or not isinstance(records, list) or not records:
            continue
        latest = records[-1] if isinstance(records[-1], dict) else {}
        raw_source = str(latest.get("source") or "").upper()
        source_label = source_labels.get(raw_source, "已确认内容")
        trace.append(
            {
                "design_field": field,
                "design_field_label": _FIELD_LABELS[field],
                "course_item": value[:500],
                "purpose": f"已写入当前设计；最近更新来源：{source_label}。",
                "source_type": raw_source or "CONFIRMED",
                "revision": int(latest.get("revision") or 0),
            }
        )
    trace.sort(key=lambda item: int(item.get("revision") or 0), reverse=True)
    for item in trace:
        item.pop("revision", None)
    return trace


def evaluate_design_quality(
    session: DesignSession,
    semantic_assessment: Any = None,
    *,
    final_review: bool = False,
) -> dict[str, Any]:
    """Combine deterministic completeness checks with model semantic review."""

    design = design_state_snapshot(session)
    # In EMVR mode the canonical causal chain is projected from Builder-facing
    # fields by ``workflow_design_snapshot``.  Guided mode receives the same
    # values it used previously.  This prevents a saved EMVR variable or
    # observation from being reported as missing at a later stage.
    snapshot = workflow_design_snapshot(session)
    if session.interaction_state is InteractionState.EMVR_DIRECT:
        stage_projection = _emvr_stage_output_projection(session)
        snapshot = {
            **snapshot,
            **{
                field: deepcopy(value)
                for field, value in stage_projection.items()
                if value not in (None, "", [], {})
                and snapshot.get(field) in (None, "", [], {})
            },
        }
    semantic = normalize_quality_assessment(semantic_assessment)
    issues: list[dict[str, Any]] = []

    missing = [
        field
        for field in _required_fields_for_stage(session.current_stage, final_review=final_review)
        if not _text(snapshot.get(field))
    ]
    if missing:
        labels = "、".join(_FIELD_LABELS[field] for field in missing[:4])
        issues.append(
            _local_issue(
                "COMPLETENESS",
                "MAJOR" if final_review else "MINOR",
                missing,
                f"当前设计还没有明确{labels}。",
                "先补足最直接影响研究问题的一项，不需要一次填写整张清单。",
                f"我们先补{_FIELD_LABELS[missing[0]]}：你希望它怎样服务于当前研究问题？",
            )
        )

    question = _text(snapshot.get("research_question"))
    independent = _text(snapshot.get("independent_variable"))
    observations = _text(snapshot.get("observations"))
    mechanism = _text(snapshot.get("theoretical_framework")) or _text(
        snapshot.get("course_relationship")
    )
    procedure = _text(snapshot.get("procedure_steps"))
    visualization = _text(snapshot.get("visualization_plan"))
    controls = _text(snapshot.get("controlled_conditions"))
    comparisons = ensure_design_state(session).get("baseline_comparisons", [])
    comparison_text = _comparison_summary(comparisons)

    if question and (not independent or not observations):
        absent = "自变量" if not independent else "观察量"
        field = "independent_variable" if not independent else "observations"
        issues.append(
            _local_issue(
                "CAUSALITY",
                "MAJOR" if final_review else "MINOR",
                ["research_question", field],
                f"研究问题已经形成，但“改变什么→观察什么”的链条还缺少{absent}。",
                "把研究问题拆成一个主动改变的因素和一个可以显示或记录的响应。",
                f"在这个问题里，你准备把什么作为{absent}？",
            )
        )
    if independent and observations and not mechanism:
        issues.append(
            _local_issue(
                "CAUSALITY",
                "MINOR",
                ["independent_variable", "observations", "theoretical_framework"],
                "改变因素和观察响应已经明确，但两者之间的ECE329物理联系还没有说明。",
                "补上一条能解释趋势的课程关系，并说明它支持哪个观察量。",
                "哪一条ECE329物理关系最能解释这个响应随自变量变化的原因？",
            )
        )
    if independent and observations and not controls and (
        session.current_stage in {
            Stage.CONCEPTUAL_PROCEDURE,
            Stage.EXPECTED_DATA_VISUALIZATION,
            Stage.RESULT_INTERPRETATION,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        }
        or final_review
    ):
        issues.append(
            _local_issue(
                "FEASIBILITY",
                "MAJOR" if final_review else "MINOR",
                ["independent_variable", "controlled_conditions"],
                "已经有自变量和观察量，但公平比较所需的控制条件尚未明确。",
                "至少固定会同时影响观察量的主要条件，并保持记录方式一致。",
                "为了只比较这个自变量，你认为最需要固定哪一个条件？",
            )
        )
    if procedure and observations and not visualization and (
        session.current_stage in {
            Stage.RESULT_INTERPRETATION,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        }
        or final_review
    ):
        issues.append(
            _local_issue(
                "FEASIBILITY",
                "MINOR",
                ["observations", "visualization_plan"],
                "流程会产生观察结果，但还没有说明怎样显示或记录它。",
                "为每个核心观察量指定一种可比较的图、场分布或数值记录。",
                "你希望用什么显示方式让不同条件下的结果可以直接比较？",
            )
        )
    if question and not comparison_text and not controls and final_review:
        issues.append(
            _local_issue(
                "FEASIBILITY",
                "MINOR",
                ["research_question", "baseline_comparisons"],
                "研究问题已经提出，但缺少用于判断变化的基准或比较条件。",
                "加入一个基准状态和至少一个变化后的状态。",
                "哪个状态最适合作为这项研究的比较基准？",
            )
        )

    issues.extend(semantic.get("issues", []))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    for issue in issues:
        if not issue:
            continue
        identity = (
            str(issue.get("category")),
            tuple(sorted(str(field) for field in issue.get("fields", []))),
            "".join(str(issue.get("finding") or "").split()).casefold(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(issue)

    severity_rank = {"MAJOR": 0, "MINOR": 1, "INFO": 2}
    unique.sort(key=lambda item: severity_rank.get(str(item.get("severity")), 9))
    causal = semantic.get("causal_chain", {})
    if not any(_text(causal.get(key)) for key in ("cause", "response", "mechanism")):
        causal = {
            "cause": independent,
            "response": observations,
            "mechanism": mechanism,
            "comparison": comparison_text,
            "answerability": (
                "流程与显示方式均已明确，可以据此回答研究问题。"
                if procedure and visualization
                else "仍需用实验流程和显示方式说明怎样形成可比较证据。"
            ),
            "status": (
                "PASS"
                if independent and observations and mechanism and procedure
                else "NEEDS_ATTENTION"
            ),
        }
    else:
        # Canonical state is the source of truth for the visible chain.  The
        # semantic review may explain answerability, but must not echo storage
        # IDs, adoption flags or stale pre-edit values into the student panel.
        causal = deepcopy(causal)
        if independent:
            causal["cause"] = independent
        if observations:
            causal["response"] = observations
        if mechanism:
            causal["mechanism"] = mechanism
        if comparison_text:
            causal["comparison"] = comparison_text

    boundary_cases = semantic.get("boundary_cases", [])
    if not boundary_cases and independent:
        boundary_cases = [
            {
                "case": f"检查{independent}的基准值与课程模型仍然适用的极端取值",
                "relevance": "用于区分真实趋势、近似失效和显示不明显三种情况。",
            }
        ]
    local_traceability = _provenance_trace(session)
    traceability = [*local_traceability, *semantic.get("traceability", [])]
    trace_seen: set[tuple[str, str, str]] = set()
    traced_fields: set[str] = set()
    unique_traceability: list[dict[str, Any]] = []
    for item in traceability:
        if not isinstance(item, dict):
            continue
        design_field = str(item.get("design_field") or "")
        # A locally committed value is stronger evidence than another model
        # paraphrase of the same field. Show one clear row per field.
        if design_field and design_field in traced_fields:
            continue
        identity = (
            design_field,
            str(item.get("course_item")),
            str(item.get("purpose")),
        )
        if identity in trace_seen:
            continue
        trace_seen.add(identity)
        if design_field:
            traced_fields.add(design_field)
        unique_traceability.append(item)
    traceability = unique_traceability
    review = {
        "reviewed_design_revision": int(design.get("revision") or 0),
        "reviewed_stage": session.current_stage.value,
        "final_review": final_review,
        "status": (
            "READY"
            if not unique
            else "NEEDS_ATTENTION"
        ),
        "issues": unique[:16],
        "priority_issue": deepcopy(unique[0]) if unique else None,
        "causal_chain": causal,
        "feasibility": {
            "independent_variable_can_change": bool(independent),
            "observation_can_be_recorded": bool(observations and (visualization or procedure)),
            "comparison_is_defined": bool(comparison_text or controls),
            "controls_are_defined": bool(controls),
            "procedure_can_test_hypothesis": bool(procedure and observations),
            "course_link_is_defined": bool(
                _text(snapshot.get("course_relationship")) or mechanism
            ),
        },
        "boundary_cases": boundary_cases[:8],
        "traceability": traceability[:24],
        "option_comparison": semantic.get("option_comparison", [])[:6],
    }
    session.design_context["quality_review"] = deepcopy(review)
    return review


def public_quality_review(review: Any, *, max_issues: int = 1) -> dict[str, Any]:
    if not isinstance(review, dict):
        return {}
    public = deepcopy(review)
    public["issues"] = [
        issue
        for issue in public.get("issues", [])[:max_issues]
        if isinstance(issue, dict)
    ]
    public["priority_issue"] = deepcopy(public["issues"][0]) if public["issues"] else None
    return public


def format_quality_review(
    review: Any,
    interaction_state: InteractionState,
    *,
    final_review: bool = False,
) -> str:
    if not isinstance(review, dict):
        return ""
    issues = review.get("issues", [])
    issues = [item for item in issues if isinstance(item, dict)]
    if not issues:
        return (
            "提交前检查通过：研究问题、变量、观察方式与流程之间已经能够形成完整证据链。"
            if final_review
            else "目前这部分与前面的研究问题能够衔接，可以继续完善。"
        )
    visible = issues[:2] if final_review else issues[:1]
    if final_review:
        review_target = (
            "生成设计报告前"
            if interaction_state is InteractionState.EMVR_DIRECT
            else "写总结前"
        )
        lead = (
            f"{review_target}，我检查到两个值得确认的地方："
            if len(visible) > 1
            else f"{review_target}，我发现一个值得确认的地方："
        )
    else:
        lead = "这里有一个会直接影响实验结论的地方："
    lines = [lead]
    for index, issue in enumerate(visible, start=1):
        prefix = f"{index}. " if len(visible) > 1 else ""
        lines.append(
            f"{prefix}{issue.get('finding')}"
            + (f" 建议：{issue.get('suggestion')}" if issue.get("suggestion") else "")
        )
    if interaction_state is InteractionState.EMVR_DIRECT:
        lines.append("这些提醒只用于审阅Unity VR实验设计，不代表已经实现或验证模拟结果。")
    return "\n".join(lines)


def format_option_comparison(review: Any, interaction_state: InteractionState) -> str:
    if not isinstance(review, dict):
        return ""
    options = review.get("option_comparison", [])
    options = [item for item in options if isinstance(item, dict)]
    if not options:
        return (
            "我还缺少两个方案各自的研究对象、主动改变量和观察量，暂时不能公平比较。"
            "请先补充其中一个方案最核心的‘改变什么、观察什么’。"
        )
    lead = (
        "下面按课程关联、可观察性、变量控制和VR实现价值比较这几个方案："
        if interaction_state is InteractionState.EMVR_DIRECT
        else "我们先不急着选，可以从课程联系、是否容易观察和变量是否好控制来比较："
    )
    lines = [lead]
    for item in options[:4]:
        details = [
            f"观察：{item.get('observability')}" if item.get("observability") else "",
            f"课程联系：{item.get('course_alignment')}" if item.get("course_alignment") else "",
            f"控制：{item.get('controllability')}" if item.get("controllability") else "",
            f"VR适配：{item.get('vr_suitability')}" if item.get("vr_suitability") else "",
            f"区分度：{item.get('discrimination')}" if item.get("discrimination") else "",
            f"额外假设：{item.get('extra_assumptions')}" if item.get("extra_assumptions") else "",
        ]
        lines.append(f"- {item.get('name')}：" + "；".join(part for part in details if part))
        if item.get("recommendation"):
            lines.append(f"  建议：{item.get('recommendation')}")
    lines.append("你可以根据最重视的标准作决定，我不会替你静默更换研究方向。")
    return "\n".join(lines)
