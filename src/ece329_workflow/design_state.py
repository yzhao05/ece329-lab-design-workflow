from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .models import DesignSession


DESIGN_TEXT_FIELDS = (
    "research_object",
    "course_relationship",
    "learning_objective",
    "research_question",
    "theoretical_framework",
    "hypothesis",
    "expected_phenomenon",
    "conceptual_structure",
)

FACET_TO_DESIGN_FIELD = {
    "direction_outline": "research_object",
    "course_mapping": "course_relationship",
    "learning_objective": "learning_objective",
    "research_question": "research_question",
    "theoretical_framework": "theoretical_framework",
    "hypothesis": "hypothesis",
    "conceptual_structure": "conceptual_structure",
}
DESIGN_FIELD_TO_FACET = {
    value: key for key, value in FACET_TO_DESIGN_FIELD.items()
}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(
            item for item in (_text(child) for child in value) if item
        )
    if isinstance(value, dict):
        return "；".join(
            item for item in (_text(child) for child in value.values()) if item
        )
    return str(value).strip() if value is not None else ""


def _normalized(value: str) -> str:
    return "".join(value.split()).casefold()


def _migrate_seen_scenes_from_history(
    session: DesignSession,
    state: dict[str, Any],
) -> None:
    """Recover template identities for conversations created before this schema."""

    if state.get("scene_history_migrated") is True:
        return
    from .knowledge_base import KNOWLEDGE

    known_templates = [
        *KNOWLEDGE.scene_templates,
        *KNOWLEDGE.generic_scene_frames,
    ]
    by_title = {
        _normalized(str(template.get("title") or "")): template
        for template in known_templates
        if str(template.get("title") or "").strip()
    }
    template_ids = state.get("seen_scene_template_ids", [])
    signatures = state.get("seen_scene_signatures", [])
    template_ids = template_ids if isinstance(template_ids, list) else []
    signatures = signatures if isinstance(signatures, list) else []
    for history_item in session.history:
        output = history_item.get("output", {}) if isinstance(history_item, dict) else {}
        payload = output.get("stage_payload", {}) if isinstance(output, dict) else {}
        scenes = payload.get("exploration_scenes", []) if isinstance(payload, dict) else []
        for scene in scenes if isinstance(scenes, list) else []:
            if not isinstance(scene, dict):
                continue
            template_id = str(scene.get("scene_template_id") or "").strip()
            signature = str(scene.get("scene_template_signature") or "").strip()
            if not signature:
                template = by_title.get(_normalized(str(scene.get("title") or "")))
                if template is not None:
                    signature = KNOWLEDGE.scene_signature(template)
                    template_id = template_id or str(
                        template.get("template_id") or ""
                    ).strip()
                    if not template_id:
                        template_id = "generic_" + hashlib.sha256(
                            signature.encode("utf-8")
                        ).hexdigest()[:16]
            if template_id and template_id not in template_ids:
                template_ids.append(template_id)
            if signature and signature not in signatures:
                signatures.append(signature)
    state["seen_scene_template_ids"] = template_ids
    state["seen_scene_signatures"] = signatures
    state["scene_history_migrated"] = True


def _legacy_values(session: DesignSession) -> dict[str, str]:
    idea = session.design_context.get("idea", {})
    outline = session.design_context.get("experiment_outline_seed", {})
    development = session.design_context.get("idea_development", {})
    idea = idea if isinstance(idea, dict) else {}
    outline = outline if isinstance(outline, dict) else {}
    development = development if isinstance(development, dict) else {}
    facets = development.get("facets", {})
    facets = facets if isinstance(facets, dict) else {}

    def facet_value(facet_id: str) -> str:
        facet = facets.get(facet_id, {})
        if not isinstance(facet, dict) or facet.get("status") != "CLEAR":
            return ""
        return _text(facet.get("evidence"))

    hypothesis = facet_value("hypothesis") or _text(outline.get("hypothesis"))
    return {
        "research_object": (
            facet_value("direction_outline")
            or _text(outline.get("research_object"))
            or _text(outline.get("core_phenomenon"))
            or _text(idea.get("direction_summary"))
            or _text(idea.get("current_focus"))
            or _text(idea.get("original"))
        ),
        "course_relationship": (
            facet_value("course_mapping")
            or _text(outline.get("course_relationship"))
            or _text(outline.get("course_relationships"))
            or _text(idea.get("selected_course_relations"))
        ),
        "learning_objective": (
            facet_value("learning_objective")
            or _text(outline.get("learning_objective"))
        ),
        "research_question": (
            facet_value("research_question")
            or _text(outline.get("research_question"))
        ),
        "theoretical_framework": (
            facet_value("theoretical_framework")
            or _text(outline.get("theoretical_framework"))
        ),
        "hypothesis": hypothesis,
        "expected_phenomenon": (
            _text(outline.get("expected_phenomenon")) or hypothesis
        ),
        "conceptual_structure": (
            facet_value("conceptual_structure")
            or _text(outline.get("conceptual_structure"))
        ),
    }


def ensure_design_state(session: DesignSession) -> dict[str, Any]:
    """Return the canonical design snapshot, migrating legacy fields once.

    The snapshot is the only cross-turn source of truth.  Legacy facet and
    outline objects remain as compatibility projections for the existing UI
    and report code; they are never allowed to erase a canonical value.
    """

    state = session.design_context.get("design_state")
    if not isinstance(state, dict):
        state = {}
        session.design_context["design_state"] = state
    state.setdefault("schema_version", 1)
    state.setdefault("revision", 0)
    state.setdefault("seen_scene_template_ids", [])
    state.setdefault("seen_scene_signatures", [])
    state.setdefault("pending_action", None)
    state.setdefault("applied_update_ids", [])
    state.setdefault("explicitly_cleared_fields", [])
    for field in DESIGN_TEXT_FIELDS:
        state.setdefault(field, "")
    if state.get("legacy_migrated") is not True:
        for field, value in _legacy_values(session).items():
            if not _text(state.get(field)) and value:
                state[field] = value
        state["legacy_migrated"] = True
    else:
        explicitly_cleared = {
            str(item) for item in state.get("explicitly_cleared_fields", [])
        }
        for field, value in _legacy_values(session).items():
            if (
                field not in explicitly_cleared
                and not _text(state.get(field))
                and value
            ):
                state[field] = value
    _migrate_seen_scenes_from_history(session, state)
    return state


def set_pending_action_snapshot(
    session: DesignSession,
    pending_action: dict[str, Any] | None,
) -> None:
    state = ensure_design_state(session)
    state["pending_action"] = (
        deepcopy(pending_action) if isinstance(pending_action, dict) else None
    )


def _merge_text(previous: str, addition: str) -> str:
    previous = previous.strip()
    addition = addition.strip()
    if not previous:
        return addition
    if not addition or _normalized(addition) in _normalized(previous):
        return previous
    if _normalized(previous) in _normalized(addition):
        return addition
    return f"{previous}；补充：{addition}"


def _update_identity(
    action_id: str,
    field: str,
    operation: str,
    value: str,
) -> str:
    material = json.dumps(
        [action_id, field, operation, _normalized(value)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def apply_design_updates(
    session: DesignSession,
    updates: Any,
    *,
    pending_action: dict[str, Any] | None = None,
) -> list[str]:
    """Validate and idempotently commit semantic field updates."""

    if not isinstance(updates, list):
        return []
    state = ensure_design_state(session)
    applied_ids = state.get("applied_update_ids", [])
    if not isinstance(applied_ids, list):
        applied_ids = []
    known_ids = {str(item) for item in applied_ids}
    explicitly_cleared = {
        str(item) for item in state.get("explicitly_cleared_fields", [])
    }
    action_id = (
        str(pending_action.get("action_id") or "")
        if isinstance(pending_action, dict)
        else ""
    ) or f"revision:{session.revision}"
    changed: list[str] = []
    for raw in updates:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or raw.get("field_id") or "")
        operation = str(raw.get("operation") or "REPLACE").upper()
        if field not in DESIGN_TEXT_FIELDS or operation not in {
            "MERGE",
            "REPLACE",
            "CLEAR",
        }:
            continue
        value = "" if operation == "CLEAR" else _text(raw.get("value"))[:4000]
        if operation != "CLEAR" and not value:
            continue
        update_id = str(raw.get("update_id") or "").strip() or _update_identity(
            action_id,
            field,
            operation,
            value,
        )
        if update_id in known_ids:
            continue
        previous = _text(state.get(field))
        if operation == "MERGE":
            next_value = _merge_text(previous, value)
        elif operation == "CLEAR":
            next_value = ""
        else:
            next_value = value
        known_ids.add(update_id)
        applied_ids.append(update_id)
        if next_value != previous:
            state[field] = next_value
            changed.append(field)
        if operation == "CLEAR":
            explicitly_cleared.add(field)
        else:
            explicitly_cleared.discard(field)
    if changed:
        state["revision"] = int(state.get("revision") or 0) + 1
    state["applied_update_ids"] = applied_ids[-200:]
    state["explicitly_cleared_fields"] = sorted(explicitly_cleared)
    return list(dict.fromkeys(changed))


def design_updates_from_facets(
    facet_updates: Any,
    *,
    evidence: str,
) -> list[dict[str, Any]]:
    """Compatibility bridge for older semantic responders and saved tests."""

    if not isinstance(facet_updates, list):
        return []
    result: list[dict[str, Any]] = []
    for update in facet_updates:
        if not isinstance(update, dict) or update.get("status") != "CLEAR":
            continue
        field = FACET_TO_DESIGN_FIELD.get(str(update.get("facet_id") or ""))
        if not field:
            continue
        value = _text(update.get("value")) or evidence.strip()
        if not value:
            continue
        result.append(
            {
                "field": field,
                "operation": str(update.get("operation") or "REPLACE").upper(),
                "value": value,
            }
        )
        if field == "hypothesis":
            result.append(
                {
                    "field": "expected_phenomenon",
                    "operation": str(update.get("operation") or "REPLACE").upper(),
                    "value": value,
                }
            )
    return result


def sync_design_state_to_legacy(session: DesignSession) -> None:
    state = ensure_design_state(session)
    outline = session.design_context.get("experiment_outline_seed")
    if isinstance(outline, dict):
        outline.update(
            {
                "research_object": _text(state.get("research_object")),
                "course_relationship": _text(state.get("course_relationship")),
                "learning_objective": _text(state.get("learning_objective")),
                "research_question": _text(state.get("research_question")),
                "theoretical_framework": _text(
                    state.get("theoretical_framework")
                ),
                "hypothesis": _text(state.get("hypothesis")),
                "expected_phenomenon": _text(
                    state.get("expected_phenomenon")
                ),
                "conceptual_structure": _text(
                    state.get("conceptual_structure")
                ),
            }
        )
    development = session.design_context.get("idea_development")
    facets = development.get("facets", {}) if isinstance(development, dict) else {}
    if isinstance(facets, dict):
        for field, facet_id in DESIGN_FIELD_TO_FACET.items():
            value = _text(state.get(field))
            facet = facets.get(facet_id)
            if value and isinstance(facet, dict):
                facet.update(
                    {
                        "status": "CLEAR",
                        "evidence": value[:4000],
                        "source": "CANONICAL_DESIGN_STATE",
                    }
                )
            elif isinstance(facet, dict):
                facet.update(
                    {
                        "status": "MISSING",
                        "evidence": "",
                        "source": None,
                    }
                )


def record_seen_scenes(session: DesignSession, scenes: Any) -> None:
    if not isinstance(scenes, list):
        return
    state = ensure_design_state(session)
    template_ids = state.get("seen_scene_template_ids", [])
    signatures = state.get("seen_scene_signatures", [])
    template_ids = template_ids if isinstance(template_ids, list) else []
    signatures = signatures if isinstance(signatures, list) else []
    changed = False
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        template_id = str(scene.get("scene_template_id") or "").strip()
        signature = str(scene.get("scene_template_signature") or "").strip()
        if template_id and template_id not in template_ids:
            template_ids.append(template_id)
            changed = True
        if signature and signature not in signatures:
            signatures.append(signature)
            changed = True
    state["seen_scene_template_ids"] = template_ids
    state["seen_scene_signatures"] = signatures
    if changed:
        state["revision"] = int(state.get("revision") or 0) + 1


def seen_scene_signatures(session: DesignSession) -> set[str]:
    state = ensure_design_state(session)
    return {
        str(item)
        for item in state.get("seen_scene_signatures", [])
        if str(item).strip()
    }


def design_state_snapshot(session: DesignSession) -> dict[str, Any]:
    """Return public design fields without internal state-machine metadata."""

    state = ensure_design_state(session)
    return {
        field: deepcopy(state.get(field, "")) for field in DESIGN_TEXT_FIELDS
    } | {
        "revision": int(state.get("revision") or 0),
    }


def format_design_summary(
    session: DesignSession,
    requested_fields: list[str] | None = None,
) -> str:
    state = ensure_design_state(session)
    labels = (
        ("research_object", "研究对象"),
        ("course_relationship", "课程关系"),
        ("learning_objective", "学习目标"),
        ("research_question", "研究问题"),
        ("theoretical_framework", "理论依据"),
        ("hypothesis", "假设"),
        ("expected_phenomenon", "预期现象"),
        ("conceptual_structure", "概念实验结构"),
    )
    allowed = {
        str(field) for field in requested_fields or [] if str(field) in DESIGN_TEXT_FIELDS
    }
    selected = (
        tuple(item for item in labels if item[0] in allowed)
        if allowed
        else labels
    )
    return "\n".join(
        f"• {label}：{_text(state.get(field)) or '暂未明确'}"
        for field, label in selected
    )
