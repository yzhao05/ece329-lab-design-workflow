from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .design_state import DESIGN_TEXT_FIELDS, ensure_design_state, sync_design_state_to_legacy
from .dialogue_acts import STAGE_ACT_FIELDS
from .models import DesignSession


_REPORT_FIELD_BINDINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "research_object": (
        "IDEA_BRAINSTORMING",
        ("normalized_idea", "target_phenomenon"),
    ),
    "course_relationship": (
        "COURSE_MAPPING_AND_DIRECTION",
        ("primary_topic", "secondary_topics", "selected_direction"),
    ),
    "learning_objective": (
        "LEARNING_OBJECTIVES",
        (
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        ),
    ),
    "research_question": ("RESEARCH_QUESTION", ("main_research_question",)),
    "theoretical_framework": (
        "THEORETICAL_FRAMEWORK",
        ("physical_mechanism", "core_equations", "formula_support_map"),
    ),
    "hypothesis": ("HYPOTHESIS", ("research_hypothesis",)),
    "expected_phenomenon": ("HYPOTHESIS", ("expected_trend",)),
    "conceptual_structure": (
        "CONCEPTUAL_OR_VR_SETUP",
        ("user_role", "core_learning_task", "physics_layer", "visualization_layer"),
    ),
    "independent_variable": ("VARIABLES_AND_CONDITIONS", ("independent_variable",)),
    "observations": ("VARIABLES_AND_CONDITIONS", ("dependent_variable",)),
    "controlled_conditions": ("VARIABLES_AND_CONDITIONS", ("controlled_variables",)),
    "procedure_steps": ("CONCEPTUAL_PROCEDURE", ("procedure_steps",)),
    "visualization_plan": (
        "EXPECTED_DATA_VISUALIZATION",
        ("student_visualization_requirements", "trend_annotation"),
    ),
    "result_interpretation": (
        "RESULT_INTERPRETATION",
        ("if_prediction_supported", "if_opposite_trend", "if_no_clear_change"),
    ),
    "limitations": ("DESIGN_VALUE_AND_LIMITATIONS", ("limitations",)),
    "unity_objects": ("CONCEPTUAL_OR_VR_SETUP", ("unity_objects", "object_inventory")),
    "interactions": ("CONCEPTUAL_OR_VR_SETUP", ("interactions",)),
}


VERSION_ACTIONS = frozenset({"VIEW_RECENT", "UNDO_LAST", "RESTORE", "COMPARE"})

_FIELD_LABELS = {
    "research_object": "研究对象",
    "course_relationship": "课程关系",
    "learning_objective": "学习目标",
    "research_question": "研究问题",
    "theoretical_framework": "理论依据",
    "hypothesis": "研究假设",
    "expected_phenomenon": "预期现象",
    "conceptual_structure": "实验结构",
    "baseline_comparisons": "比较条件",
    "independent_variable": "自变量",
    "observations": "观察量",
    "controlled_conditions": "控制条件",
    "procedure_steps": "实验流程",
    "visualization_plan": "可视化方式",
    "result_interpretation": "结果解释",
    "limitations": "局限与边界",
    "unity_objects": "Unity对象",
    "interactions": "VR交互",
}


def _snapshot(session: DesignSession) -> dict[str, Any]:
    return {
        "design_state": deepcopy(ensure_design_state(session)),
        "stage_design_state": deepcopy(
            session.design_context.get("stage_design_state", {})
            if isinstance(session.design_context.get("stage_design_state"), dict)
            else {}
        ),
        "idea": deepcopy(
            session.design_context.get("idea", {})
            if isinstance(session.design_context.get("idea"), dict)
            else {}
        ),
        "stage_outputs": deepcopy(session.stage_outputs),
    }


def _public_values(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    design = snapshot.get("design_state", {})
    stage = snapshot.get("stage_design_state", {})
    design = design if isinstance(design, dict) else {}
    stage = stage if isinstance(stage, dict) else {}
    return {
        **{field: deepcopy(design.get(field, "")) for field in DESIGN_TEXT_FIELDS},
        "baseline_comparisons": deepcopy(design.get("baseline_comparisons", [])),
        **{field: deepcopy(stage.get(field, "")) for field in STAGE_ACT_FIELDS},
    }


def _versions(session: DesignSession) -> list[dict[str, Any]]:
    versions = session.model_context.setdefault("design_versions", [])
    if not isinstance(versions, list):
        versions = []
        session.model_context["design_versions"] = versions
    return versions


def ensure_initial_version(session: DesignSession) -> list[dict[str, Any]]:
    versions = _versions(session)
    if versions:
        return versions
    versions.append(
        {
            "version_id": "v0001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_revision": int(session.revision),
            "reason": "实验设计起点",
            "source": "SYSTEM_BASELINE",
            "changed_fields": [],
            "snapshot": _snapshot(session),
        }
    )
    return versions


def record_design_version(
    session: DesignSession,
    *,
    changed_fields: list[str],
    reason: str,
    source: str = "STUDENT_TURN",
) -> dict[str, Any] | None:
    versions = ensure_initial_version(session)
    current = _snapshot(session)
    if versions and _public_values(versions[-1].get("snapshot")) == _public_values(current):
        return None
    next_number = max(
        [
            int(str(item.get("version_id") or "v0")[1:])
            for item in versions
            if str(item.get("version_id") or "").startswith("v")
            and str(item.get("version_id") or "")[1:].isdigit()
        ]
        or [0]
    ) + 1
    version = {
        "version_id": f"v{next_number:04d}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_revision": int(session.revision),
        "reason": reason.strip()[:500],
        "source": source,
        "changed_fields": list(dict.fromkeys(changed_fields)),
        "snapshot": current,
    }
    versions.append(version)
    del versions[:-40]
    return deepcopy(version)


def normalize_version_request(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").upper()
    if action not in VERSION_ACTIONS:
        return None
    fields = raw.get("fields", [])
    valid_fields = {*DESIGN_TEXT_FIELDS, "baseline_comparisons", *STAGE_ACT_FIELDS}
    return {
        "action": action,
        "version_id": str(raw.get("version_id") or "").strip()[:40] or None,
        "other_version_id": str(raw.get("other_version_id") or "").strip()[:40] or None,
        "fields": list(
            dict.fromkeys(
                str(field).strip()
                for field in fields
                if isinstance(field, str) and str(field).strip() in valid_fields
            )
        ) if isinstance(fields, list) else [],
    }


def _find_version(versions: list[dict[str, Any]], version_id: str | None) -> dict[str, Any] | None:
    if not version_id:
        return None
    return next(
        (item for item in versions if str(item.get("version_id")) == version_id),
        None,
    )


def _diff_values(left: Any, right: Any, fields: list[str] | None = None) -> list[dict[str, Any]]:
    left_values = _public_values(left)
    right_values = _public_values(right)
    selected = fields or list(dict.fromkeys([*left_values, *right_values]))
    return [
        {
            "field": field,
            "label": _FIELD_LABELS.get(field, field),
            "before": deepcopy(left_values.get(field)),
            "after": deepcopy(right_values.get(field)),
        }
        for field in selected
        if left_values.get(field) != right_values.get(field)
    ]


def _restore_snapshot(
    session: DesignSession,
    target: dict[str, Any],
    *,
    fields: list[str] | None = None,
) -> list[str]:
    current = _snapshot(session)
    target_design = target.get("design_state", {})
    target_stage = target.get("stage_design_state", {})
    if not isinstance(target_design, dict) or not isinstance(target_stage, dict):
        return []
    chosen = fields or [*DESIGN_TEXT_FIELDS, "baseline_comparisons", *STAGE_ACT_FIELDS]
    changed: list[str] = []
    design = ensure_design_state(session)
    stage = session.design_context.setdefault("stage_design_state", {})
    if not isinstance(stage, dict):
        stage = {}
        session.design_context["stage_design_state"] = stage
    current_values = _public_values(current)
    target_values = _public_values(target)
    target_outputs = target.get("stage_outputs", {})
    target_outputs = target_outputs if isinstance(target_outputs, dict) else {}
    for field in chosen:
        if current_values.get(field) == target_values.get(field):
            continue
        if field in {*DESIGN_TEXT_FIELDS, "baseline_comparisons"}:
            design[field] = deepcopy(target_design.get(field, "" if field != "baseline_comparisons" else []))
        elif field in STAGE_ACT_FIELDS:
            stage[field] = deepcopy(target_stage.get(field, ""))
        changed.append(field)
        binding = _REPORT_FIELD_BINDINGS.get(field)
        if binding is not None:
            stage_id, payload_fields = binding
            source_output = target_outputs.get(stage_id, {})
            source_payload = (
                source_output.get("stage_payload", {})
                if isinstance(source_output, dict)
                else {}
            )
            destination_output = session.stage_outputs.get(stage_id)
            destination_payload = (
                destination_output.get("stage_payload")
                if isinstance(destination_output, dict)
                else None
            )
            if isinstance(source_payload, dict) and isinstance(destination_payload, dict):
                for payload_field in payload_fields:
                    if payload_field in source_payload:
                        destination_payload[payload_field] = deepcopy(
                            source_payload[payload_field]
                        )
                    else:
                        destination_payload.pop(payload_field, None)
    if changed:
        design["revision"] = int(design.get("revision") or 0) + 1
        sync_design_state_to_legacy(session)
    return changed


def execute_version_request(session: DesignSession, raw: Any) -> dict[str, Any] | None:
    request = normalize_version_request(raw)
    if request is None:
        return None
    versions = ensure_initial_version(session)
    action = request["action"]
    if action == "VIEW_RECENT":
        return {
            "action": action,
            "changed_fields": [],
            "versions": [
                {
                    key: deepcopy(item.get(key))
                    for key in ("version_id", "created_at", "reason", "source", "changed_fields")
                } | {
                    "changed_field_labels": [
                        _FIELD_LABELS.get(str(field), str(field))
                        for field in item.get("changed_fields", [])
                    ]
                }
                for item in versions[-5:]
            ],
        }
    if action == "COMPARE":
        left = _find_version(versions, request.get("version_id")) or (
            versions[-2] if len(versions) > 1 else versions[-1]
        )
        right = _find_version(versions, request.get("other_version_id")) or versions[-1]
        return {
            "action": action,
            "changed_fields": [],
            "left_version_id": left.get("version_id"),
            "right_version_id": right.get("version_id"),
            "differences": _diff_values(
                left.get("snapshot"), right.get("snapshot"), request.get("fields") or None
            ),
        }
    if action == "RESTORE":
        target = _find_version(versions, request.get("version_id"))
        if target is None:
            return {"action": action, "error": "找不到指定的设计版本。", "changed_fields": []}
        changed = _restore_snapshot(
            session,
            target.get("snapshot", {}),
            fields=request.get("fields") or None,
        )
        return {
            "action": action,
            "restored_version_id": target.get("version_id"),
            "changed_fields": changed,
        }
    current = _snapshot(session)
    fields = request.get("fields") or []
    target = None
    if fields:
        for candidate in reversed(versions[:-1] if len(versions) > 1 else versions):
            if _diff_values(candidate.get("snapshot"), current, fields):
                target = candidate
                break
    elif len(versions) > 1:
        target = versions[-2]
    if target is None:
        return {"action": action, "error": "目前没有可撤销的设计修改。", "changed_fields": []}
    changed = _restore_snapshot(
        session,
        target.get("snapshot", {}),
        fields=fields or None,
    )
    return {
        "action": action,
        "restored_version_id": target.get("version_id"),
        "changed_fields": changed,
    }


def format_version_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return str(result["error"])
    action = result.get("action")
    if action == "VIEW_RECENT":
        versions = result.get("versions", [])
        if not versions:
            return "目前还没有可查看的设计修改记录。"
        lines = ["最近的设计版本："]
        for item in versions:
            changed = "、".join(
                _FIELD_LABELS.get(str(field), str(field))
                for field in item.get("changed_fields", [])
            ) or "设计起点"
            lines.append(f"- {item.get('version_id')}：{item.get('reason')}（{changed}）")
        return "\n".join(lines)
    if action == "COMPARE":
        differences = result.get("differences", [])
        if not differences:
            return "这两个版本在所比较的设计项上没有差异。"
        lines = [
            f"{result.get('left_version_id')} 与 {result.get('right_version_id')} 的差异："
        ]
        for item in differences[:8]:
            lines.append(
                f"- {item.get('label') or _FIELD_LABELS.get(str(item.get('field')), item.get('field'))}：由“{item.get('before') or '未明确'}”变为“{item.get('after') or '未明确'}”"
            )
        return "\n".join(lines)
    changed = result.get("changed_fields", [])
    if not changed:
        return "目标版本与当前设计一致，因此没有改动其他内容。"
    return (
        f"已恢复{result.get('restored_version_id')}中的"
        f"{'、'.join(_FIELD_LABELS.get(str(field), str(field)) for field in changed)}；未点名的设计内容保持不变。"
    )
