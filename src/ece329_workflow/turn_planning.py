from __future__ import annotations

from copy import deepcopy
from typing import Any

from .design_state import design_state_snapshot
from .dialogue_acts import stage_design_state_snapshot
from .emvr_design import merge_emvr_structured_requirements
from .models import DesignSession, InteractionState, Stage


STATE_ACT_TYPES = frozenset(
    {
        "ANSWER_PENDING_QUESTION",
        "MODIFY_DESIGN_FIELD",
        "MODIFY_STAGE_FIELD",
        "MODIFY_EMVR_FIELD",
        "MODIFY_COMPARISON",
        "NEW_TOPIC_CONTENT",
        "NEW_TOPIC",
    }
)
SERVICE_ACT_TYPES = frozenset(
    {
        "ASK_COURSE_QUESTION",
        "REQUEST_REFERENCE",
        "REQUEST_SUMMARY",
        "CORRECT_ASSISTANT",
        "REQUEST_QUALITY_REVIEW",
        "COMPARE_OPTIONS",
        "VERSION_CONTROL",
    }
)

FIELD_LABELS = {
    "research_object": "研究对象",
    "course_relationship": "课程关系",
    "learning_objective": "学习目标",
    "learning_objectives": "学习目标",
    "conceptual_objective": "概念目标",
    "calculation_objective": "计算目标",
    "analysis_objective": "分析目标",
    "vr_interaction_objective": "交互目标",
    "observation_objective": "观察目标",
    "research_question": "研究问题",
    "theoretical_framework": "理论依据",
    "hypothesis": "假设",
    "expected_phenomenon": "预期现象",
    "conceptual_structure": "实验结构",
    "baseline_comparisons": "比较条件",
    "independent_variable": "主动改变量",
    "observations": "观察内容",
    "controlled_conditions": "控制条件",
    "procedure_steps": "实验流程",
    "visualization_plan": "显示方式",
    "result_interpretation": "结果解释",
    "design_rationale": "设计依据",
    "design_value": "设计价值",
    "limitations": "设计局限",
    "unity_objects": "VR实验对象",
    "interactions": "VR交互",
    "experiment_brief": "完整实验方向",
}


def build_turn_task_plan(
    acts: Any,
    unresolved_items: Any = None,
) -> dict[str, Any]:
    """Build an internal execution plan without collapsing a mixed turn.

    Source order is retained for traceability, while ``execution_phase`` gives
    the state machine a stable order: commit design changes, answer requests,
    then perform navigation.  An invalid fragment never blocks other tasks.
    """

    tasks: list[dict[str, Any]] = []
    for source_order, act in enumerate(acts if isinstance(acts, list) else []):
        if not isinstance(act, dict):
            continue
        act_type = str(act.get("type") or "")
        target = str(act.get("target") or "")
        correction_content = act.get("content")
        correction_has_updates = bool(
            act_type == "CORRECT_ASSISTANT"
            and isinstance(correction_content, dict)
            and any(
                isinstance(correction_content.get(key), list)
                and correction_content.get(key)
                for key in (
                    "design_updates",
                    "stage_field_updates",
                    "comparison_updates",
                )
            )
        )
        if act_type in STATE_ACT_TYPES or correction_has_updates:
            phase = "COMMIT_DESIGN"
        elif act_type in SERVICE_ACT_TYPES:
            phase = "RESPOND_TO_REQUEST"
        elif act_type in {"CONTROL", "REQUEST_NEW_TOPIC"}:
            phase = "NAVIGATE"
        else:
            phase = "CLARIFY"
        tasks.append(
            {
                "task_id": str(act.get("act_id") or f"turn_task_{source_order}"),
                "source_order": source_order,
                "execution_phase": phase,
                "type": act_type,
                "target": target,
                "operation": str(act.get("operation") or ""),
                "content": deepcopy(act.get("content")),
                "status": "READY" if phase != "CLARIFY" else "NEEDS_CLARIFICATION",
            }
        )
    unresolved = unresolved_items if isinstance(unresolved_items, list) else []
    for offset, item in enumerate(unresolved, start=len(tasks)):
        content = (
            str(item.get("content") or "").strip()
            if isinstance(item, dict)
            else str(item).strip()
        )
        if not content:
            continue
        tasks.append(
            {
                "task_id": f"unresolved_{offset}",
                "source_order": offset,
                "execution_phase": "CLARIFY",
                "type": "UNRESOLVED",
                "target": "",
                "operation": "",
                "content": content,
                "status": "NEEDS_CLARIFICATION",
            }
        )
    execution_order = {
        "COMMIT_DESIGN": 0,
        "RESPOND_TO_REQUEST": 1,
        "NAVIGATE": 2,
        "CLARIFY": 3,
    }
    ordered_ids = [
        task["task_id"]
        for task in sorted(
            tasks,
            key=lambda item: (
                execution_order.get(str(item["execution_phase"]), 9),
                int(item["source_order"]),
            ),
        )
    ]
    return {
        "tasks": tasks,
        "execution_order": ordered_ids,
        "has_partial_work": bool(tasks),
        "has_unresolved_work": any(
            task["status"] == "NEEDS_CLARIFICATION" for task in tasks
        ),
    }


def workflow_design_snapshot(session: DesignSession) -> dict[str, Any]:
    """Capture all editable design fields used for before/after comparison."""

    canonical = design_state_snapshot(session)
    stage_fields = stage_design_state_snapshot(session)
    emvr_fields = (
        merge_emvr_structured_requirements(
            session.design_context.get("emvr_design", {})
        )
        if session.interaction_state is InteractionState.EMVR_DIRECT
        else {}
    )
    # EMVR stores Builder-facing names (for example ``changed_quantities``),
    # while quality review and stage hand-off use the mode-neutral design
    # names.  Keep one authoritative projection here so every downstream
    # consumer sees the same committed meaning instead of mistaking a saved
    # EMVR value for a missing guided field.
    emvr_projection: dict[str, Any] = {}
    if emvr_fields:
        objective_values = [
            str(emvr_fields.get(field) or "").strip()
            for field in (
                "conceptual_objective",
                "calculation_objective",
                "analysis_objective",
                "vr_interaction_objective",
                "observation_objective",
            )
            if str(emvr_fields.get(field) or "").strip()
        ]
        if not objective_values:
            raw_objectives = emvr_fields.get("learning_objectives", [])
            objective_values = (
                [str(item).strip() for item in raw_objectives if str(item).strip()]
                if isinstance(raw_objectives, list)
                else []
            )
        theory_links = emvr_fields.get("theory_links", [])
        theory_summary = [
            str(item.get("supports_design_content") or "").strip()
            for item in theory_links
            if isinstance(item, dict)
            and str(item.get("supports_design_content") or "").strip()
        ] if isinstance(theory_links, list) else []
        aliases = {
            "learning_objective": objective_values,
            "independent_variable": emvr_fields.get("changed_quantities"),
            "observations": emvr_fields.get("observed_quantities"),
            "theoretical_framework": theory_summary,
            "conceptual_structure": emvr_fields.get("object_constraints"),
            "interactions": emvr_fields.get("required_behaviors"),
            "visualization_plan": emvr_fields.get("visualization_requirements"),
            "design_value": emvr_fields.get("design_values"),
        }
        emvr_projection = {
            field: deepcopy(value)
            for field, value in aliases.items()
            if value not in (None, "", [], {})
        }
    return {
        **canonical,
        **stage_fields,
        **emvr_fields,
        **emvr_projection,
    }


def requested_fields_from_plan(plan: Any) -> list[str]:
    if not isinstance(plan, dict):
        return []
    fields: list[str] = []
    for task in plan.get("tasks", []):
        if not isinstance(task, dict):
            continue
        if task.get("type") in {
            "ANSWER_PENDING_QUESTION",
            "MODIFY_DESIGN_FIELD",
            "MODIFY_STAGE_FIELD",
            "MODIFY_EMVR_FIELD",
        }:
            target = str(task.get("target") or "")
            if target in FIELD_LABELS and target not in fields:
                fields.append(target)
        elif task.get("type") == "MODIFY_COMPARISON":
            if "baseline_comparisons" not in fields:
                fields.append("baseline_comparisons")
        elif task.get("type") == "CORRECT_ASSISTANT":
            content = task.get("content")
            if not isinstance(content, dict):
                continue
            for update_key in ("design_updates", "stage_field_updates"):
                for update in (
                    content.get(update_key, [])
                    if isinstance(content.get(update_key), list)
                    else []
                ):
                    if not isinstance(update, dict):
                        continue
                    field = str(update.get("field") or "")
                    if field in FIELD_LABELS and field not in fields:
                        fields.append(field)
            if (
                isinstance(content.get("comparison_updates"), list)
                and content["comparison_updates"]
                and "baseline_comparisons" not in fields
            ):
                fields.append("baseline_comparisons")
    return fields


def compute_design_diff(
    before: dict[str, Any],
    after: dict[str, Any],
    task_plan: Any,
) -> dict[str, Any]:
    """Compare committed state and distinguish a real edit from a no-op."""

    requested_fields = requested_fields_from_plan(task_plan)
    if isinstance(task_plan, dict) and any(
        isinstance(task, dict) and task.get("type") == "VERSION_CONTROL"
        for task in task_plan.get("tasks", [])
    ):
        requested_fields = list(FIELD_LABELS)
    changes: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for field in requested_fields:
        old_value = deepcopy(before.get(field))
        new_value = deepcopy(after.get(field))
        if old_value == new_value:
            unchanged.append(field)
            continue
        changes.append(
            {
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "before": old_value,
                "after": new_value,
            }
        )
    return {
        "changes": changes,
        "unchanged_requested_fields": unchanged,
        "changed_fields": [item["field"] for item in changes],
        "has_changes": bool(changes),
    }


def finalize_turn_task_plan(
    plan: Any,
    diff: Any,
    *,
    response_generated: bool,
    transition_requested: bool,
    transition_completed: bool,
) -> dict[str, Any]:
    """Attach per-task outcomes after state commit and response planning."""

    finalized = deepcopy(plan) if isinstance(plan, dict) else {"tasks": []}
    changed_fields = set(
        diff.get("changed_fields", []) if isinstance(diff, dict) else []
    )
    unchanged_fields = set(
        diff.get("unchanged_requested_fields", [])
        if isinstance(diff, dict)
        else []
    )
    for task in finalized.get("tasks", []):
        if not isinstance(task, dict):
            continue
        phase = str(task.get("execution_phase") or "")
        target = str(task.get("target") or "")
        if task.get("type") == "MODIFY_COMPARISON":
            target = "baseline_comparisons"
        correction_targets: list[str] = []
        if task.get("type") == "CORRECT_ASSISTANT":
            correction_targets = requested_fields_from_plan({"tasks": [task]})
        if phase == "COMMIT_DESIGN":
            if correction_targets and changed_fields.intersection(correction_targets):
                task["status"] = "APPLIED"
            elif correction_targets and unchanged_fields.intersection(
                correction_targets
            ):
                task["status"] = "NO_CHANGE"
            elif target in changed_fields:
                task["status"] = "APPLIED"
            elif target in unchanged_fields:
                task["status"] = "NO_CHANGE"
            elif task.get("type") in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"}:
                task["status"] = "APPLIED"
            else:
                task["status"] = "PRESERVED"
        elif phase == "RESPOND_TO_REQUEST":
            task["status"] = "COMPLETED" if response_generated else "READY"
        elif phase == "NAVIGATE":
            if not transition_requested:
                task["status"] = "COMPLETED"
            else:
                task["status"] = (
                    "COMPLETED" if transition_completed else "BLOCKED"
                )
    finalized["completed_task_count"] = sum(
        task.get("status") in {"APPLIED", "NO_CHANGE", "PRESERVED", "COMPLETED"}
        for task in finalized.get("tasks", [])
        if isinstance(task, dict)
    )
    finalized["remaining_task_count"] = sum(
        task.get("status") in {"READY", "NEEDS_CLARIFICATION", "BLOCKED"}
        for task in finalized.get("tasks", [])
        if isinstance(task, dict)
    )
    return finalized


def _compact(value: Any, limit: int = 110) -> str:
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                cases = item.get("cases") or item.get("recommended_cases") or []
                if isinstance(cases, list):
                    parts.extend(str(case).strip() for case in cases if str(case).strip())
            elif str(item).strip():
                parts.append(str(item).strip())
        text = "、".join(dict.fromkeys(parts))
    else:
        text = str(value).strip() if value is not None else ""
    if len(text) <= limit:
        return text
    prefix = text[:limit]
    boundary = max(prefix.rfind(mark) for mark in "。！？；.!?;")
    if boundary >= max(24, limit // 3):
        return prefix[: boundary + 1]
    return f"{prefix[: limit - 1].rstrip('，、：；,;:-—')}…"


def student_change_notice(
    diff: Any,
    interaction_state: InteractionState,
) -> str:
    """Render a concise user-facing delta, never an internal-state report."""

    if not isinstance(diff, dict):
        return ""
    changes = diff.get("changes", [])
    unchanged = diff.get("unchanged_requested_fields", [])
    lines: list[str] = []
    for change in changes[:3] if isinstance(changes, list) else []:
        if not isinstance(change, dict):
            continue
        label = str(change.get("label") or "这项设计")
        old_value = _compact(change.get("before"))
        new_value = _compact(change.get("after"))
        old_is_long = len(str(change.get("before") or "")) > 110
        new_is_long = len(str(change.get("after") or "")) > 110
        if interaction_state is InteractionState.EMVR_DIRECT:
            if old_is_long or new_is_long:
                lines.append(f"已按本轮完整说明更新设计中的{label}，其他栏目保持不变。")
            elif old_value:
                lines.append(
                    f"已同步修订设计中的{label}：由“{old_value}”调整为“{new_value}”。"
                )
            else:
                lines.append(f"已同步修订设计中的{label}：“{new_value}”。")
        elif old_is_long or new_is_long:
            lines.append(f"我已经把你刚才完整说明的{label}接进设计了，其他内容没有被覆盖。")
        elif old_value:
            lines.append(f"我已按你的想法把{label}从“{old_value}”调整为“{new_value}”。")
        else:
            lines.append(f"我把你补充的{label}“{new_value}”接到现有想法里了。")
    if not lines and isinstance(unchanged, list) and unchanged:
        labels = "、".join(
            FIELD_LABELS.get(str(field), str(field)) for field in unchanged[:3]
        )
        if interaction_state is InteractionState.EMVR_DIRECT:
            lines.append(f"这次表述与现有{labels}一致，因此相关设计保持不变。")
        else:
            lines.append(f"你这次补充的内容与现有{labels}一致，这部分保持不变。")
    return "".join(lines)


def build_stage_context_summary(
    session: DesignSession,
    stage: Stage | None = None,
) -> dict[str, Any]:
    """Build the complete hand-off summary consumed by every later stage."""

    current = stage or session.current_stage
    snapshot = workflow_design_snapshot(session)
    fields = (
        "research_object",
        "course_relationship",
        "learning_objective",
        "research_question",
        "baseline_comparisons",
        "independent_variable",
        "observations",
        "controlled_conditions",
        "hypothesis",
        "expected_phenomenon",
        "procedure_steps",
        "visualization_plan",
    )
    confirmed = {
        field: deepcopy(snapshot.get(field))
        for field in fields
        if snapshot.get(field) not in (None, "", [], {})
    }
    comparisons = confirmed.get("baseline_comparisons")
    if isinstance(comparisons, list):
        accepted = [
            deepcopy(item)
            for item in comparisons
            if isinstance(item, dict)
            and str(item.get("adoption_status") or "PENDING").upper()
            in {"ACCEPTED", "MODIFIED"}
        ]
        if accepted:
            confirmed["baseline_comparisons"] = accepted
        else:
            confirmed.pop("baseline_comparisons", None)
    return {
        "for_stage": current.value,
        "confirmed": confirmed,
        "missing": [field for field in fields if field not in confirmed],
    }
