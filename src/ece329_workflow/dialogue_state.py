from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from enum import Enum
from typing import Any, Protocol

from .models import DesignSession, InteractionState, Stage, StepOutput


class UserIntent(str, Enum):
    ANSWER_CURRENT_QUESTION = "ANSWER_CURRENT_QUESTION"
    ACCEPT_PREVIOUS_PROPOSAL = "ACCEPT_PREVIOUS_PROPOSAL"
    MODIFY_PREVIOUS_PROPOSAL = "MODIFY_PREVIOUS_PROPOSAL"
    REJECT_PREVIOUS_PROPOSAL = "REJECT_PREVIOUS_PROPOSAL"
    ADVANCE_STAGE = "ADVANCE_STAGE"
    REQUEST_MORE_EXAMPLES = "REQUEST_MORE_EXAMPLES"
    RETURN_TO_PREVIOUS_POINT = "RETURN_TO_PREVIOUS_POINT"
    NEW_TOPIC = "NEW_TOPIC"
    UNCLEAR = "UNCLEAR"


ALL_INTENTS = tuple(intent.value for intent in UserIntent)
_IDEA_FACET_IDS = {
    "direction_outline",
    "course_mapping",
    "learning_objective",
    "research_question",
    "theoretical_framework",
    "hypothesis",
    "conceptual_structure",
}


class ContextIntentResolver(Protocol):
    def resolve_intent(
        self,
        session: DesignSession,
        user_message: str,
        pending_action: dict[str, Any] | None,
        carried_context: dict[str, Any],
    ) -> dict[str, Any]: ...


def dialogue_state(session: DesignSession) -> dict[str, Any]:
    # Conversation-control data is persisted with the session but deliberately
    # kept out of design_context, which is part of the student-facing API.
    state = session.model_context.setdefault("dialogue_state", {})
    if not isinstance(state, dict):
        state = {}
        session.model_context["dialogue_state"] = state
    state.setdefault("decision_log", [])
    return state


def current_pending_action(session: DesignSession) -> dict[str, Any] | None:
    pending = dialogue_state(session).get("pending_action")
    return deepcopy(pending) if isinstance(pending, dict) else None


def hydrate_pending_action_from_history(
    session: DesignSession,
) -> dict[str, Any] | None:
    """Migrate a pre-upgrade conversation without exposing internal fields."""

    current = current_pending_action(session)
    if current is not None or not session.history:
        return current
    previous = session.history[-1]
    if previous.get("handled_stage") != session.current_stage.value:
        return None
    raw_output = previous.get("output", {})
    if not isinstance(raw_output, dict):
        return None
    stage_payload = raw_output.get("stage_payload", {})
    output = StepOutput(
        assistant_message=str(raw_output.get("assistant_message") or ""),
        stage_payload=deepcopy(stage_payload) if isinstance(stage_payload, dict) else {},
        student_task=(
            str(raw_output.get("student_task"))
            if raw_output.get("student_task") is not None
            else None
        ),
    )
    return save_pending_action(session, session.current_stage, output)


def current_resolved_intent(session: DesignSession) -> dict[str, Any] | None:
    resolved = dialogue_state(session).get("resolved_intent")
    return deepcopy(resolved) if isinstance(resolved, dict) else None


def _flatten_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value:
            flattened.extend(_flatten_values(item))
        return flattened
    if isinstance(value, dict):
        preferred = value.get("name") or value.get("label") or value.get("value")
        if preferred is not None:
            return _flatten_values(preferred)
        flattened = []
        for child in value.values():
            flattened.extend(_flatten_values(child))
        return flattened
    return []


def _find_payload_values(session: DesignSession, keys: set[str]) -> list[str]:
    values: list[str] = []
    for stage in reversed(tuple(Stage)):
        stage_output = session.stage_outputs.get(stage.value, {})
        payload = stage_output.get("stage_payload", {}) if isinstance(stage_output, dict) else {}
        if not isinstance(payload, dict):
            continue
        stack: list[Any] = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, child in current.items():
                    if str(key).casefold() in keys:
                        values.extend(_flatten_values(child))
                    elif isinstance(child, (dict, list)):
                        stack.append(child)
            elif isinstance(current, list):
                stack.extend(current)
    return list(dict.fromkeys(item for item in values if item))


def build_carried_context(session: DesignSession) -> dict[str, Any]:
    """Build a compact, stage-independent view of confirmed design decisions."""

    idea = session.design_context.get("idea", {})
    outline = session.design_context.get("experiment_outline_seed", {})
    idea = idea if isinstance(idea, dict) else {}
    outline = outline if isinstance(outline, dict) else {}
    direction = str(
        idea.get("main_direction")
        or idea.get("direction_summary")
        or idea.get("current_focus")
        or outline.get("core_phenomenon")
        or idea.get("original")
        or ""
    ).strip()
    recent_student_decisions = [
        str(item.get("user_message") or "").strip()
        for item in session.history[-8:]
        if str(item.get("user_message") or "").strip()
    ]
    return {
        "research_direction": direction,
        "course_relationships": deepcopy(
            outline.get("course_relationships")
            or idea.get("selected_course_relations")
            or []
        ),
        "baseline_comparisons": deepcopy(
            outline.get("baseline_comparisons")
            or idea.get("standard_comparisons")
            or []
        ),
        "independent_variable": _find_payload_values(
            session,
            {"independent_variable", "variable_type", "adjustable_quantity_in_vr"},
        ),
        "observations": _find_payload_values(
            session,
            {
                "dependent_variable",
                "observation_focus",
                "observable_quantity_in_vr",
                "observations",
                "calculated_outputs",
            },
        ),
        "controlled_conditions": _find_payload_values(
            session,
            {"controlled_variables", "controlled_conditions", "reference_condition"},
        ),
        "procedure_steps": _find_payload_values(
            session,
            {"procedure_steps", "reference_draft"},
        ),
        "resolved_decisions": deepcopy(
            session.design_context.get("resolved_decisions", {})
            if isinstance(session.design_context.get("resolved_decisions"), dict)
            else {}
        ),
        "idea_development": deepcopy(
            session.design_context.get("idea_development", {})
            if isinstance(session.design_context.get("idea_development"), dict)
            else {}
        ),
        "recent_student_decisions": recent_student_decisions,
    }


def _proposal_from_output(output: StepOutput) -> Any:
    payload = output.stage_payload
    selected: dict[str, Any] = {}
    for key in (
        "proposal",
        "reference_draft",
        "standard_comparisons",
        "alternative_ideas",
        "procedure_steps",
        "controlled_variables",
        "observation_focus",
        "trend_choices",
        "result_case",
        "review_dimension",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            selected[key] = deepcopy(value)
    return selected or None


def _normalize_pending_action(
    raw: dict[str, Any],
    *,
    stage: Stage,
    revision: int,
    fallback_question: str,
    fallback_proposal: Any,
) -> dict[str, Any]:
    allowed = raw.get("allowed_intents", [])
    allowed = [str(item) for item in allowed if str(item) in ALL_INTENTS] \
        if isinstance(allowed, list) else []
    if not allowed:
        allowed = [
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
            UserIntent.ADVANCE_STAGE.value,
            UserIntent.REQUEST_MORE_EXAMPLES.value,
            UserIntent.RETURN_TO_PREVIOUS_POINT.value,
            UserIntent.NEW_TOPIC.value,
        ]
    return {
        "action_id": str(raw.get("action_id") or f"action_{revision}_{uuid.uuid4().hex[:8]}"),
        "type": str(raw.get("type") or ("CONFIRM_OR_MODIFY" if fallback_proposal else "ANSWER_OR_ADVANCE")),
        "stage": stage.value,
        "subject": str(raw.get("subject") or stage.value.casefold()),
        "proposal": deepcopy(raw.get("proposal", fallback_proposal)),
        "question": str(raw.get("question") or fallback_question),
        "allowed_intents": list(dict.fromkeys(allowed)),
        "status": "PENDING",
        "created_at_revision": revision,
    }


def save_pending_action(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
) -> dict[str, Any] | None:
    """Persist the assistant's next expected decision without exposing it in chat."""

    if session.interaction_state is InteractionState.EMVR_DIRECT:
        dialogue_state(session).pop("pending_action", None)
        return None
    raw = output.stage_payload.pop("pending_action", None)
    raw = raw if isinstance(raw, dict) else {}
    question = str(output.student_task or "").strip()
    if not question and ("？" in output.assistant_message or "?" in output.assistant_message):
        question = output.assistant_message[-600:]
    proposal = _proposal_from_output(output)
    if not question and proposal is None:
        dialogue_state(session).pop("pending_action", None)
        return None
    pending = _normalize_pending_action(
        raw,
        stage=stage,
        revision=session.revision + 1,
        fallback_question=question,
        fallback_proposal=proposal,
    )
    state = dialogue_state(session)
    state["pending_action"] = pending
    state["carried_context"] = build_carried_context(session)
    return deepcopy(pending)


def deterministic_intent(
    user_message: str,
    pending_action: dict[str, Any] | None,
    *,
    selected_option_id: str | None = None,
    complete_stage: bool = False,
) -> dict[str, Any] | None:
    """Fast path only for unambiguous commands; contextual language is not guessed."""

    normalized = re.sub(r"[\s，,。；;！!？?]+", "", user_message).casefold()
    if complete_stage or normalized in {"继续", "下一步", "进入下一阶段", "继续下一阶段"}:
        return resolved_intent(UserIntent.ADVANCE_STAGE, confidence=1.0)
    if selected_option_id:
        return resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target=selected_option_id,
            resolved_value=selected_option_id,
            confidence=1.0,
        )
    if pending_action and normalized in {"确认", "同意", "接受", "保留"}:
        return resolved_intent(
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
            target=str(pending_action.get("subject") or ""),
            resolved_value=deepcopy(pending_action.get("proposal")),
            confidence=1.0,
        )
    if normalized in {"再来一组", "换一组"}:
        return resolved_intent(UserIntent.REQUEST_MORE_EXAMPLES, confidence=1.0)
    if normalized in {"返回上一步", "回到上一阶段"}:
        return resolved_intent(UserIntent.RETURN_TO_PREVIOUS_POINT, confidence=1.0)
    return None


def fallback_intent(
    user_message: str,
    pending_action: dict[str, Any] | None,
) -> dict[str, Any]:
    """Conservative offline behavior when no semantic model is available."""

    # Offline/rule-only deployments cannot reliably infer contextual intent.
    # Treat the message as an answer instead of inventing a decision; the
    # deterministic state machine will therefore preserve the current stage.
    return resolved_intent(
        UserIntent.ANSWER_CURRENT_QUESTION,
        confidence=0.62,
        source="CONSERVATIVE_FALLBACK",
    )


def resolved_intent(
    intent: UserIntent | str,
    *,
    target: str | None = None,
    resolved_value: Any = None,
    advance_requested: bool | None = None,
    preserve_current_design: bool = True,
    confidence: float = 1.0,
    source: str = "DETERMINISTIC",
    semantic_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent_value = intent.value if isinstance(intent, UserIntent) else str(intent)
    return {
        "intent": intent_value if intent_value in ALL_INTENTS else UserIntent.UNCLEAR.value,
        "target": target,
        "resolved_value": deepcopy(resolved_value),
        "advance_requested": (
            intent_value == UserIntent.ADVANCE_STAGE.value
            if advance_requested is None
            else bool(advance_requested)
        ),
        "preserve_current_design": bool(preserve_current_design),
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "source": source,
        "semantic_updates": deepcopy(semantic_updates or {}),
    }


def validate_resolved_intent(
    raw: dict[str, Any],
    pending_action: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return resolved_intent(UserIntent.UNCLEAR, confidence=0.0, source="INVALID")
    intent = str(raw.get("intent") or UserIntent.UNCLEAR.value)
    if intent not in ALL_INTENTS:
        intent = UserIntent.UNCLEAR.value
    allowed = set(pending_action.get("allowed_intents", [])) if pending_action else set(ALL_INTENTS)
    if intent not in allowed and intent not in {
        UserIntent.NEW_TOPIC.value,
        UserIntent.RETURN_TO_PREVIOUS_POINT.value,
    }:
        intent = UserIntent.UNCLEAR.value
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.55:
        intent = UserIntent.UNCLEAR.value
    semantic_updates = (
        {}
        if intent == UserIntent.UNCLEAR.value
        else _normalize_semantic_updates(raw.get("semantic_updates"))
    )
    return resolved_intent(
        intent,
        target=str(raw.get("target") or pending_action.get("subject") or "")
        if pending_action
        else str(raw.get("target") or ""),
        resolved_value=raw.get("resolved_value"),
        advance_requested=raw.get("advance_requested"),
        preserve_current_design=raw.get("preserve_current_design", True),
        confidence=confidence,
        source=str(raw.get("source") or "SEMANTIC"),
        semantic_updates=semantic_updates,
    )


def _normalize_semantic_updates(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    option_ids = raw.get("selected_option_ids", [])
    selected_option_ids = list(
        dict.fromkeys(
            str(item)[:160]
            for item in option_ids
            if isinstance(item, str) and item.strip()
        )
    )[:5] if isinstance(option_ids, list) else []
    facet_updates: list[dict[str, str]] = []
    for item in raw.get("facet_updates", []) if isinstance(raw.get("facet_updates"), list) else []:
        if not isinstance(item, dict):
            continue
        facet_id = str(item.get("facet_id") or "")
        status = str(item.get("status") or "").upper()
        if facet_id in _IDEA_FACET_IDS and status in {"CLEAR", "MISSING"}:
            facet_updates.append({"facet_id": facet_id, "status": status})
    comparison_updates: list[dict[str, Any]] = []
    for item in raw.get("comparison_updates", []) if isinstance(raw.get("comparison_updates"), list) else []:
        if not isinstance(item, dict):
            continue
        comparison_id = str(item.get("comparison_id") or "")[:80]
        action = str(item.get("action") or "").upper()
        cases = item.get("cases", [])
        if comparison_id and action in {"ACCEPT", "MODIFY", "REJECT"}:
            comparison_updates.append(
                {
                    "comparison_id": comparison_id,
                    "action": action,
                    "cases": [str(case)[:80] for case in cases if isinstance(case, str)][:8]
                    if isinstance(cases, list)
                    else [],
                }
            )
    return {
        "selected_option_ids": selected_option_ids,
        "no_direction": raw.get("no_direction") is True,
        "facet_updates": facet_updates,
        "comparison_updates": comparison_updates,
    }


def _apply_comparison_updates(
    session: DesignSession,
    updates: Any,
) -> None:
    if not isinstance(updates, list):
        return
    idea = session.design_context.get("idea", {})
    comparisons = idea.get("standard_comparisons", []) if isinstance(idea, dict) else []
    if not isinstance(comparisons, list):
        return
    by_id = {
        str(item.get("comparison_id")): item
        for item in comparisons
        if isinstance(item, dict) and str(item.get("comparison_id") or "")
    }
    for update in updates:
        if not isinstance(update, dict):
            continue
        item = by_id.get(str(update.get("comparison_id") or ""))
        if item is None:
            continue
        recommended = [
            str(case)
            for case in item.get("recommended_cases", item.get("cases", []))
            if str(case).strip()
        ]
        action = str(update.get("action") or "").upper()
        raw_cases = update.get("cases", [])
        cases = [
            str(case) for case in raw_cases
            if str(case) in recommended
        ] if isinstance(raw_cases, list) else []
        if action == "ACCEPT":
            item["cases"] = list(recommended)
            item["adoption_status"] = "ACCEPTED"
        elif action == "REJECT":
            item["cases"] = []
            item["adoption_status"] = "REJECTED"
        elif action == "MODIFY" and cases:
            item["cases"] = list(dict.fromkeys(cases))
            item["adoption_status"] = (
                "ACCEPTED" if set(cases) == set(recommended) else "MODIFIED"
            )


def accept_pending_comparisons_on_advance(session: DesignSession) -> None:
    """Treat advancing as acceptance of still-pending baseline proposals."""

    idea = session.design_context.get("idea", {})
    comparisons = idea.get("standard_comparisons", []) if isinstance(idea, dict) else []
    if not isinstance(comparisons, list):
        return
    for item in comparisons:
        if not isinstance(item, dict) or item.get("adoption_status") != "PENDING":
            continue
        recommended = item.get("recommended_cases", item.get("cases", []))
        if isinstance(recommended, list):
            item["cases"] = [str(case) for case in recommended if str(case).strip()]
        item["adoption_status"] = "ACCEPTED"


def apply_semantic_design_updates(
    session: DesignSession,
    resolved: dict[str, Any],
    user_message: str,
) -> None:
    """Apply only validated IDs/cases from semantic analysis to design state."""

    updates = resolved.get("semantic_updates", {})
    if (
        resolved.get("intent") == UserIntent.UNCLEAR.value
        or not isinstance(updates, dict)
    ):
        return
    _apply_comparison_updates(session, updates.get("comparison_updates"))


def apply_resolved_intent(
    session: DesignSession,
    resolved: dict[str, Any],
    pending_action: dict[str, Any] | None,
    user_message: str = "",
) -> None:
    state = dialogue_state(session)
    state["resolved_intent"] = deepcopy(resolved)
    intent = str(resolved.get("intent") or UserIntent.UNCLEAR.value)
    if pending_action and intent == UserIntent.ADVANCE_STAGE.value:
        pending_action["status"] = "PRESERVED_ON_ADVANCE"
        state["pending_action"] = deepcopy(pending_action)
    if pending_action and intent in {
        UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
    }:
        decision_value = resolved.get("resolved_value")
        if intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value and decision_value is None:
            decision_value = deepcopy(pending_action.get("proposal"))
        if intent == UserIntent.REJECT_PREVIOUS_PROPOSAL.value:
            decision_value = None
        decisions = session.design_context.setdefault("resolved_decisions", {})
        if not isinstance(decisions, dict):
            decisions = {}
            session.design_context["resolved_decisions"] = decisions
        subject = str(resolved.get("target") or pending_action.get("subject") or "current_proposal")
        decisions[subject] = deepcopy(decision_value)
        pending_action["status"] = {
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value: "ACCEPTED",
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value: "MODIFIED",
            UserIntent.REJECT_PREVIOUS_PROPOSAL.value: "REJECTED",
        }[intent]
        state["pending_action"] = deepcopy(pending_action)
    log = state.setdefault("decision_log", [])
    if isinstance(log, list):
        log.append(deepcopy(resolved))
        del log[:-40]
    apply_semantic_design_updates(session, resolved, user_message)
    state["carried_context"] = build_carried_context(session)


def clarification_output(
    pending_action: dict[str, Any] | None,
) -> StepOutput:
    if pending_action:
        question = str(pending_action.get("question") or "").strip()
        prompt = (
            "我还不能确定你想怎样处理刚才的安排。"
            "请简短说明是保留、修改、取消，还是完成当前部分后继续。"
        )
        if question:
            prompt += f" 我理解你是在回应这个问题：{question}"
    else:
        prompt = "我还不能确定你希望继续当前内容、返回前面，还是开始一个新方向。请简短说明你的意图。"
    return StepOutput(
        assistant_message=prompt,
        stage_payload={"clarification_required": True},
        student_task=None,
    )


def serialize_intent_input(
    session: DesignSession,
    user_message: str,
    pending_action: dict[str, Any] | None,
    carried_context: dict[str, Any],
) -> str:
    previous_question = str(pending_action.get("question") or "") if pending_action else ""
    return json.dumps(
        {
            "current_stage": session.current_stage.value,
            "previous_question": previous_question,
            "pending_action": pending_action,
            "carried_context": carried_context,
            "user_message": user_message,
        },
        ensure_ascii=False,
    )
