from __future__ import annotations

from copy import deepcopy
from typing import Any

from .design_state import design_state_snapshot
from .dialogue_acts import stage_design_state_snapshot
from .models import DesignSession, InteractionState, Stage


STATE_ACT_TYPES = frozenset(
    {
        "ANSWER_PENDING_QUESTION",
        "MODIFY_DESIGN_FIELD",
        "MODIFY_STAGE_FIELD",
        "MODIFY_COMPARISON",
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
    "limitations": "设计局限",
    "unity_objects": "VR实验对象",
    "interactions": "VR交互",
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
        if act_type in STATE_ACT_TYPES:
            phase = "COMMIT_DESIGN"
        elif act_type in SERVICE_ACT_TYPES:
            phase = "RESPOND_TO_REQUEST"
        elif act_type == "CONTROL":
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
    return {**canonical, **stage_fields}


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
        }:
            target = str(task.get("target") or "")
            if target in FIELD_LABELS and target not in fields:
                fields.append(target)
        elif task.get("type") == "MODIFY_COMPARISON":
            if "baseline_comparisons" not in fields:
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
        if phase == "COMMIT_DESIGN":
            if target in changed_fields:
                task["status"] = "APPLIED"
            elif target in unchanged_fields:
                task["status"] = "NO_CHANGE"
            elif task.get("type") == "NEW_TOPIC":
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
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


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
        if interaction_state is InteractionState.EMVR_DIRECT:
            if old_value:
                lines.append(
                    f"已同步修订设计中的{label}：由“{old_value}”调整为“{new_value}”。"
                )
            else:
                lines.append(f"已同步修订设计中的{label}：“{new_value}”。")
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
    return {
        "for_stage": current.value,
        "confirmed": confirmed,
        "missing": [field for field in fields if field not in confirmed],
    }
