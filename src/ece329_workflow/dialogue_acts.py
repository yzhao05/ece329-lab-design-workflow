from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .design_state import FACET_TO_DESIGN_FIELD
from .builder_requirements import BUILDER_REQUIREMENT_FIELDS
from .emvr_design import EMVR_EDITABLE_FIELDS
from .emvr_formula_flow import (
    EMVR_FORMULA_ACTION_TYPES,
    normalize_formula_flow_action,
)
from .models import DesignSession, Stage


DIALOGUE_ACT_TYPES = frozenset(
    {
        "ANSWER_PENDING_QUESTION",
        "MODIFY_DESIGN_FIELD",
        "MODIFY_STAGE_FIELD",
        "MODIFY_EMVR_FIELD",
        "MODIFY_COMPARISON",
        "ASK_COURSE_QUESTION",
        "REQUEST_REFERENCE",
        "REQUEST_SUMMARY",
        "REQUEST_QUALITY_REVIEW",
        "COMPARE_OPTIONS",
        "VERSION_CONTROL",
        "CORRECT_ASSISTANT",
        "CONTROL",
        # Topic navigation and topic content are separate.  ``NEW_TOPIC`` is
        # retained only as a content-bearing compatibility action.
        "REQUEST_NEW_TOPIC",
        "NEW_TOPIC_CONTENT",
        "NEW_TOPIC",
        # EMVR-only formula-first onboarding. These actions update the
        # candidate/selection state; they never write ordinary design fields.
        *EMVR_FORMULA_ACTION_TYPES,
        "UNRESOLVED",
    }
)

DESIGN_ACT_FIELD_ORDER = (
    "research_object",
    "course_relationship",
    "learning_objective",
    "research_question",
    "theoretical_framework",
    "hypothesis",
    "expected_phenomenon",
    "conceptual_structure",
)
DESIGN_ACT_FIELDS = frozenset(DESIGN_ACT_FIELD_ORDER)

STAGE_ACT_FIELD_ORDER = (
    "independent_variable",
    "observations",
    "controlled_conditions",
    "procedure_steps",
    "visualization_plan",
    "result_interpretation",
    "design_rationale",
    "design_value",
    "limitations",
    "unity_objects",
    "interactions",
    "lab_title",
    "lab_id",
    "desktop_interaction_plan",
    "room_spatial_requirements",
    "hidden_object_lifecycle",
    "parameter_specifications",
    "expected_results",
    "acceptance_criteria",
    "report_questions",
    "student_summary",
)
STAGE_ACT_FIELDS = frozenset(STAGE_ACT_FIELD_ORDER)

# Canonical design collections are state-machine owned like text fields, but
# use dedicated update contracts because replacing one item must not overwrite
# the whole design.  They are nevertheless valid correction targets: a student
# can point out an incorrect comparison bundle and provide its replacement in
# the same sentence.
COLLECTION_ACT_FIELDS = frozenset({"baseline_comparisons"})

CONTROL_TARGETS = frozenset(
    {
        "ACCEPT",
        "REJECT",
        "ADVANCE",
        "RETURN",
        "SET_GUIDED_MODE",
        "SET_EMVR_MODE",
        "ACCEPT_QUALITY_REVIEW",
        "REQUEST_NEW_TOPIC",
    }
)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        return "；".join(part for part in (_text(item) for item in value.values()) if part)
    return str(value).strip() if value is not None else ""


def _act_identity(act_type: str, target: str, operation: str, content: Any) -> str:
    material = json.dumps(
        [act_type, target, operation, content],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return "act_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _normalize_comparison_contract(
    value: Any,
    *,
    operation: str = "",
) -> dict[str, Any] | None:
    """Normalize comparison edits into the state-machine contract.

    Ordinary field edits use ``MERGE/REPLACE/CLEAR`` while comparison edits
    use ``MODIFY/REJECT``.  A semantic model may preserve the ordinary edit
    verb after correctly identifying the comparison target.  Treat that as a
    schema dialect instead of discarding the student's explicit revision.
    """

    if not isinstance(value, dict):
        return None
    normalized = deepcopy(value)
    action = str(normalized.get("action") or operation or "").upper()
    if action == "REPLACE":
        action = "MODIFY"
        normalized["replace_all"] = True
    elif action == "MERGE":
        action = "MODIFY"
        normalized["merge_with_existing"] = True
    elif action == "CLEAR":
        action = "REJECT"
    if action not in {"ACCEPT", "MODIFY", "REJECT", "CREATE"}:
        return None
    normalized["action"] = action
    return normalized


def normalize_dialogue_acts(
    raw: Any,
    *,
    pending_action: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Validate each model-authored act independently.

    Invalid acts are returned as unresolved items instead of invalidating other
    executable acts from the same student message.
    """

    if not isinstance(raw, list):
        return [], []
    pending_subject = (
        str(pending_action.get("subject") or "").strip()
        if isinstance(pending_action, dict)
        else ""
    )
    accepted: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in raw[:16]:
        if not isinstance(item, dict):
            unresolved.append({"content": _text(item), "reason": "动作格式不完整"})
            continue
        act_type = str(item.get("type") or "").upper()
        target = str(item.get("target") or "").strip()
        operation = str(item.get("operation") or "MERGE").upper()
        content = deepcopy(item.get("content"))
        try:
            confidence = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if act_type not in DIALOGUE_ACT_TYPES or confidence < 0.55:
            unresolved.append(
                {"content": _text(content), "reason": "动作类型或置信度不足"}
            )
            continue
        if act_type == "ANSWER_PENDING_QUESTION":
            target = target or pending_subject
            valid_answer_targets = {
                pending_subject,
                *DESIGN_ACT_FIELDS,
                *STAGE_ACT_FIELDS,
                *EMVR_EDITABLE_FIELDS,
                *FACET_TO_DESIGN_FIELD,
            }
            if (
                not target
                or target not in valid_answer_targets
                or not _text(content)
            ):
                unresolved.append(
                    {"content": _text(content), "reason": "未能绑定到当前待明确内容"}
                )
                continue
            operation = operation if operation in {"MERGE", "REPLACE"} else "REPLACE"
        elif act_type == "MODIFY_DESIGN_FIELD":
            if target not in DESIGN_ACT_FIELDS or operation not in {
                "MERGE",
                "REPLACE",
                "CLEAR",
            }:
                unresolved.append(
                    {"content": _text(content), "reason": "设计字段或操作无效"}
                )
                continue
            if operation != "CLEAR" and not _text(content):
                unresolved.append({"content": "", "reason": f"{target}缺少修改内容"})
                continue
        elif act_type == "MODIFY_STAGE_FIELD":
            if target not in STAGE_ACT_FIELDS or operation not in {
                "MERGE",
                "REPLACE",
                "CLEAR",
            }:
                unresolved.append(
                    {"content": _text(content), "reason": "阶段字段或操作无效"}
                )
                continue
        elif act_type == "MODIFY_EMVR_FIELD":
            if target not in EMVR_EDITABLE_FIELDS or operation not in {
                "MERGE",
                "REPLACE",
                "CLEAR",
            }:
                unresolved.append(
                    {"content": _text(content), "reason": "EMVR字段或操作无效"}
                )
                continue
            if operation != "CLEAR" and not _text(content):
                unresolved.append({"content": "", "reason": f"{target}缺少修改内容"})
                continue
        elif act_type == "MODIFY_COMPARISON":
            content = _normalize_comparison_contract(
                content,
                operation=operation,
            )
            if content is None:
                unresolved.append(
                    {"content": _text(content), "reason": "基础比较修改缺少结构"}
                )
                continue
        elif act_type in {"ASK_COURSE_QUESTION", "UNRESOLVED"}:
            if not _text(content):
                unresolved.append({"content": "", "reason": "动作缺少内容"})
                continue
        elif act_type == "CORRECT_ASSISTANT":
            if not _text(content):
                unresolved.append({"content": "", "reason": "纠错动作缺少内容"})
                continue
        elif act_type == "VERSION_CONTROL":
            if not isinstance(content, dict) or str(content.get("action") or "").upper() not in {
                "VIEW_RECENT",
                "UNDO_LAST",
                "RESTORE",
                "COMPARE",
            }:
                unresolved.append({"content": _text(content), "reason": "版本操作缺少结构"})
                continue
            operation = "EXECUTE"
        elif act_type == "COMPARE_OPTIONS":
            if not isinstance(content, (list, dict)) or not _text(content):
                unresolved.append({"content": _text(content), "reason": "缺少待比较方案"})
                continue
            operation = "EXECUTE"
        elif act_type == "REQUEST_QUALITY_REVIEW":
            operation = "EXECUTE"
        elif act_type == "CONTROL":
            target = target.upper()
            if target not in CONTROL_TARGETS:
                unresolved.append(
                    {"content": _text(content), "reason": "会话控制动作无效"}
                )
                continue
            operation = "EXECUTE"
        elif act_type == "REQUEST_NEW_TOPIC":
            target = "REQUEST_NEW_TOPIC"
            content = None
            operation = "EXECUTE"
        elif act_type in EMVR_FORMULA_ACTION_TYPES:
            content = normalize_formula_flow_action(act_type, content)
            if content is None:
                unresolved.append(
                    {"content": _text(item.get("content")), "reason": "EMVR公式入口动作缺少有效结构"}
                )
                continue
            operation = "EXECUTE"
        elif act_type in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"}:
            if not _text(content):
                unresolved.append({"content": "", "reason": "新实验方向缺少具体内容"})
                continue
            operation = "EXECUTE"
        elif act_type in {"REQUEST_REFERENCE", "REQUEST_SUMMARY"}:
            operation = "EXECUTE"
        act_id = str(item.get("act_id") or "").strip()[:100] or _act_identity(
            act_type,
            target,
            operation,
            content,
        )
        if act_id in seen_ids:
            continue
        seen_ids.add(act_id)
        normalized = {
            "act_id": act_id,
            "type": act_type,
            "target": target,
            "operation": operation,
            "content": content,
            "confidence": max(0.0, min(confidence, 1.0)),
        }
        # Exact source spans let the resolver prove that every independent
        # request in a long turn was considered. They are trace metadata only
        # and never influence which field is writable.
        source_text = str(item.get("source_text") or "").strip()[:1200]
        if source_text:
            normalized["source_text"] = source_text
        try:
            source_start = int(item.get("source_start"))
            source_end = int(item.get("source_end"))
        except (TypeError, ValueError):
            source_start = source_end = -1
        if 0 <= source_start < source_end:
            normalized["source_start"] = source_start
            normalized["source_end"] = source_end
        semantic_key = str(item.get("semantic_key") or "").strip()[:180]
        if semantic_key:
            normalized["semantic_key"] = semantic_key
        accepted.append(normalized)
        if act_type == "UNRESOLVED":
            unresolved.append(
                {"content": _text(content), "reason": "需要进一步确认"}
            )
    return accepted, unresolved


def compile_dialogue_acts(
    acts: list[dict[str, Any]],
    *,
    pending_action: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compile independent acts into validated state-machine operations."""

    pending_subject = (
        str(pending_action.get("subject") or "").strip()
        if isinstance(pending_action, dict)
        else ""
    )
    pending_answer_fields = (
        [
            str(field).strip()
            for field in pending_action.get("answer_fields", [])
            if str(field).strip()
            in {
                *DESIGN_ACT_FIELDS,
                *STAGE_ACT_FIELDS,
                *EMVR_EDITABLE_FIELDS,
                *FACET_TO_DESIGN_FIELD,
            }
        ]
        if isinstance(pending_action, dict)
        and isinstance(pending_action.get("answer_fields"), list)
        else []
    )
    design_updates: list[dict[str, Any]] = []
    facet_updates: list[dict[str, Any]] = []
    stage_field_updates: list[dict[str, Any]] = []
    emvr_field_updates: list[dict[str, Any]] = []
    comparison_updates: list[dict[str, Any]] = []
    questions: list[str] = []
    feedback: list[str] = []
    correction_items: list[dict[str, Any]] = []
    quality_review_requests: list[dict[str, Any]] = []
    option_comparison_requests: list[Any] = []
    version_requests: list[dict[str, Any]] = []
    controls: list[str] = []
    emvr_formula_actions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    answered_pending = False
    for act in acts:
        act_type = str(act.get("type") or "")
        target = str(act.get("target") or "")
        operation = str(act.get("operation") or "MERGE")
        content = deepcopy(act.get("content"))
        act_id = str(act.get("act_id") or "")
        semantic_key = str(act.get("semantic_key") or "").strip()
        if act_type == "ANSWER_PENDING_QUESTION":
            # The pending subject can be a public stage id while the useful
            # answer targets one concrete field inside that stage.  The act
            # type—not surface wording—declares that this content answers the
            # open question, so preserve the field-level target and clear the
            # pending item independently.
            if target == pending_subject and len(pending_answer_fields) == 1:
                target = pending_answer_fields[0]
            elif (
                target == pending_subject
                and len(pending_answer_fields) > 1
                and isinstance(content, dict)
            ):
                field_values = [
                    (field, deepcopy(content.get(field)))
                    for field in pending_answer_fields
                    if content.get(field) not in (None, "", [], {})
                ]
                for field, value in field_values:
                    canonical = FACET_TO_DESIGN_FIELD.get(field, field)
                    update = {
                        "field": canonical,
                        "operation": operation,
                        "value": value,
                        "update_id": f"{act_id}:{field}",
                        "semantic_key": semantic_key,
                    }
                    if canonical in DESIGN_ACT_FIELDS:
                        design_updates.append(update)
                    elif canonical in STAGE_ACT_FIELDS:
                        stage_field_updates.append(update)
                    elif canonical in EMVR_EDITABLE_FIELDS:
                        emvr_field_updates.append(
                            {
                                "field_id": canonical,
                                "operation": operation,
                                "value": value,
                                "update_id": f"{act_id}:{field}",
                                "semantic_key": semantic_key,
                            }
                        )
                answered_pending = answered_pending or bool(field_values)
                if not field_values:
                    unresolved.append(_text(content))
                continue
            field = FACET_TO_DESIGN_FIELD.get(target)
            if field:
                design_updates.append(
                    {
                        "field": field,
                        "operation": operation,
                        "value": content,
                        "update_id": act_id,
                        "semantic_key": semantic_key,
                    }
                )
                facet_updates.append(
                    {
                        "facet_id": target,
                        "status": "CLEAR",
                        "operation": operation,
                        "value": content,
                    }
                )
                answered_pending = True
            elif target in DESIGN_ACT_FIELDS:
                design_updates.append(
                    {
                        "field": target,
                        "operation": operation,
                        "value": content,
                        "update_id": act_id,
                        "semantic_key": semantic_key,
                    }
                )
                answered_pending = True
            elif target in STAGE_ACT_FIELDS:
                stage_field_updates.append(
                    {
                        "field": target,
                        "operation": operation,
                        "value": content,
                        "update_id": act_id,
                        "semantic_key": semantic_key,
                    }
                )
                answered_pending = True
            elif target in EMVR_EDITABLE_FIELDS:
                emvr_field_updates.append(
                    {
                        "field_id": target,
                        "operation": operation,
                        "value": content,
                        "update_id": act_id,
                        "semantic_key": semantic_key,
                    }
                )
                answered_pending = True
            else:
                # A multi-field stage answer must be split into canonical
                # targets by the semantic parser. Do not clear the pending
                # item when no field-level state update was produced.
                unresolved.append(_text(content))
        elif act_type == "MODIFY_DESIGN_FIELD":
            design_updates.append(
                {
                    "field": target,
                    "operation": operation,
                    "value": content,
                    "update_id": act_id,
                    "semantic_key": semantic_key,
                }
            )
            facet_id = next(
                (
                    facet
                    for facet, field in FACET_TO_DESIGN_FIELD.items()
                    if field == target
                ),
                None,
            )
            if facet_id and operation != "CLEAR":
                facet_updates.append(
                    {
                        "facet_id": facet_id,
                        "status": "CLEAR",
                        "operation": operation,
                        "value": content,
                    }
                )
        elif act_type == "MODIFY_STAGE_FIELD":
            stage_field_updates.append(
                {
                    "field": target,
                    "operation": operation,
                    "value": content,
                    "update_id": act_id,
                    "semantic_key": semantic_key,
                }
            )
        elif act_type == "MODIFY_EMVR_FIELD":
            emvr_field_updates.append(
                {
                    "field_id": target,
                    "operation": operation,
                    "value": deepcopy(content),
                    "update_id": act_id,
                    "semantic_key": str(act.get("semantic_key") or ""),
                }
            )
        elif act_type == "MODIFY_COMPARISON" and isinstance(content, dict):
            normalized_comparison = _normalize_comparison_contract(
                content,
                operation=operation,
            )
            if normalized_comparison is not None:
                comparison_updates.append(normalized_comparison)
        elif act_type == "ASK_COURSE_QUESTION":
            questions.append(_text(content))
        elif act_type == "CORRECT_ASSISTANT":
            if isinstance(content, dict):
                explanation = _text(
                    content.get("explanation") or content.get("error") or content
                )
                if explanation:
                    feedback.append(explanation)
                correction = {
                    "error_type": str(content.get("error_type") or "UNDERSTANDING_ERROR").upper()[:80],
                    "explanation": explanation,
                    "affected_fields": [
                        str(field)
                        for field in content.get("affected_fields", [])
                        if isinstance(field, str)
                        and field
                        in {
                            *DESIGN_ACT_FIELDS,
                            *STAGE_ACT_FIELDS,
                            *COLLECTION_ACT_FIELDS,
                        }
                    ][:8]
                    if isinstance(content.get("affected_fields"), list)
                    else [],
                }
                correction_items.append(correction)
                for index, update in enumerate(
                    content.get("design_updates", [])
                    if isinstance(content.get("design_updates"), list)
                    else []
                ):
                    if not isinstance(update, dict):
                        continue
                    field = str(update.get("field") or "")
                    op = str(update.get("operation") or "REPLACE").upper()
                    value = deepcopy(update.get("value"))
                    if field in DESIGN_ACT_FIELDS and op in {"MERGE", "REPLACE", "CLEAR"}:
                        design_updates.append(
                            {
                                "field": field,
                                "operation": op,
                                "value": value,
                                "update_id": f"{act_id}:correction:design:{index}",
                                "semantic_key": str(update.get("semantic_key") or ""),
                                "provenance": "AGENT_SELF_CORRECTION",
                            }
                        )
                for index, update in enumerate(
                    content.get("stage_field_updates", [])
                    if isinstance(content.get("stage_field_updates"), list)
                    else []
                ):
                    if not isinstance(update, dict):
                        continue
                    field = str(update.get("field") or "")
                    op = str(update.get("operation") or "REPLACE").upper()
                    value = deepcopy(update.get("value"))
                    if field in STAGE_ACT_FIELDS and op in {"MERGE", "REPLACE", "CLEAR"}:
                        stage_field_updates.append(
                            {
                                "field": field,
                                "operation": op,
                                "value": value,
                                "update_id": f"{act_id}:correction:stage:{index}",
                                "semantic_key": str(update.get("semantic_key") or ""),
                                "provenance": "AGENT_SELF_CORRECTION",
                            }
                        )
                for index, update in enumerate(
                    content.get("comparison_updates", [])
                    if isinstance(content.get("comparison_updates"), list)
                    else []
                ):
                    if not isinstance(update, dict):
                        continue
                    normalized = _normalize_comparison_contract(update)
                    if normalized is None:
                        continue
                    normalized.setdefault(
                        "update_id",
                        f"{act_id}:correction:comparison:{index}",
                    )
                    normalized.setdefault("provenance", "AGENT_SELF_CORRECTION")
                    comparison_updates.append(normalized)
            else:
                feedback.append(_text(content))
                correction_items.append(
                    {
                        "error_type": "UNDERSTANDING_ERROR",
                        "explanation": _text(content),
                        "affected_fields": [],
                    }
                )
        elif act_type == "REQUEST_QUALITY_REVIEW":
            quality_review_requests.append(
                {
                    "scope": str(target or "CURRENT_DESIGN")[:120],
                    "content": deepcopy(content),
                }
            )
        elif act_type == "COMPARE_OPTIONS":
            option_comparison_requests.append(deepcopy(content))
        elif act_type == "VERSION_CONTROL" and isinstance(content, dict):
            version_requests.append(deepcopy(content))
        elif act_type == "CONTROL":
            controls.append(target)
        elif act_type == "REQUEST_NEW_TOPIC":
            controls.append("REQUEST_NEW_TOPIC")
        elif act_type in EMVR_FORMULA_ACTION_TYPES:
            emvr_formula_actions.append(
                {
                    "type": act_type,
                    "content": deepcopy(content),
                    "act_id": act_id,
                }
            )
        elif act_type == "REQUEST_REFERENCE":
            controls.append("REQUEST_REFERENCE")
        elif act_type == "REQUEST_SUMMARY":
            controls.append("REQUEST_SUMMARY")
        elif act_type in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"}:
            controls.append("NEW_TOPIC_CONTENT")
        elif act_type == "UNRESOLVED":
            unresolved.append(_text(content))
    return {
        "design_updates": design_updates,
        "facet_updates": facet_updates,
        "stage_field_updates": stage_field_updates,
        "emvr_field_updates": emvr_field_updates,
        "comparison_updates": comparison_updates,
        "student_questions": list(dict.fromkeys(item for item in questions if item)),
        "feedback_items": list(dict.fromkeys(item for item in feedback if item)),
        "correction_items": correction_items,
        "quality_review_requests": quality_review_requests,
        "option_comparison_requests": option_comparison_requests,
        "version_requests": version_requests,
        "control_actions": list(dict.fromkeys(controls)),
        "emvr_formula_actions": emvr_formula_actions,
        "unresolved_content": list(dict.fromkeys(item for item in unresolved if item)),
        "answered_pending": answered_pending,
    }


def apply_stage_field_updates(
    session: DesignSession,
    updates: Any,
    *,
    stage: Stage,
    provenance: str = "STUDENT_CONFIRMED",
) -> list[str]:
    """Idempotently commit later-stage fields without copying the whole turn."""

    if not isinstance(updates, list):
        return []
    state = session.design_context.setdefault("stage_design_state", {})
    if not isinstance(state, dict):
        state = {}
        session.design_context["stage_design_state"] = state
    applied_ids = state.setdefault("applied_update_ids", [])
    if not isinstance(applied_ids, list):
        applied_ids = []
        state["applied_update_ids"] = applied_ids
    known_ids = {str(item) for item in applied_ids}
    semantic_signatures = state.setdefault("semantic_signatures", {})
    if not isinstance(semantic_signatures, dict):
        semantic_signatures = {}
        state["semantic_signatures"] = semantic_signatures
    field_provenance = state.setdefault("field_provenance", {})
    if not isinstance(field_provenance, dict):
        field_provenance = {}
        state["field_provenance"] = field_provenance
    changed: list[str] = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "")
        operation = str(item.get("operation") or "MERGE").upper()
        if field not in STAGE_ACT_FIELDS or operation not in {
            "MERGE",
            "REPLACE",
            "CLEAR",
        }:
            continue
        value = "" if operation == "CLEAR" else _text(item.get("value"))[:4000]
        if operation != "CLEAR" and not value:
            continue
        update_id = str(item.get("update_id") or "").strip() or _act_identity(
            "MODIFY_STAGE_FIELD",
            field,
            operation,
            value,
        )
        if update_id in known_ids:
            continue
        previous = _text(state.get(field))
        signature = str(item.get("semantic_key") or "").strip().casefold()
        if not signature:
            signature = "".join(value.split()).casefold()
        known_signatures = semantic_signatures.get(field, [])
        known_signatures = (
            [str(item) for item in known_signatures]
            if isinstance(known_signatures, list)
            else []
        )
        if operation == "CLEAR":
            next_value = ""
        elif operation == "REPLACE" or not previous:
            next_value = value
        elif signature and signature in known_signatures:
            next_value = previous
        elif not value or value.replace(" ", "") in previous.replace(" ", ""):
            next_value = previous
        else:
            next_value = f"{previous}；补充：{value}"
        known_ids.add(update_id)
        applied_ids.append(update_id)
        if next_value != previous:
            state[field] = next_value
            changed.append(field)
            records = field_provenance.get(field, [])
            records = records if isinstance(records, list) else []
            records.append(
                {
                    "revision": int(state.get("revision") or 0) + 1,
                    "source": str(item.get("provenance") or provenance)[:80],
                    "operation": operation,
                    "value": next_value[:1000],
                    "stage": stage.value,
                }
            )
            field_provenance[field] = records[-30:]
        if operation == "CLEAR":
            semantic_signatures[field] = []
        elif operation == "REPLACE":
            semantic_signatures[field] = [signature] if signature else []
        elif signature and signature not in known_signatures:
            known_signatures.append(signature)
            semantic_signatures[field] = known_signatures[-40:]
    if changed:
        state["revision"] = int(state.get("revision") or 0) + 1
        state["last_updated_stage"] = stage.value
    state["applied_update_ids"] = applied_ids[-240:]
    state["semantic_signatures"] = semantic_signatures
    return list(dict.fromkeys(changed))


def stage_design_state_snapshot(session: DesignSession) -> dict[str, str]:
    state = session.design_context.get("stage_design_state", {})
    if not isinstance(state, dict):
        return {field: "" for field in STAGE_ACT_FIELD_ORDER}
    return {field: _text(state.get(field)) for field in STAGE_ACT_FIELD_ORDER}
