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
    SET_INTERACTION_STATE = "SET_INTERACTION_STATE"
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


def record_pending_clarification(
    session: DesignSession,
    candidate_answer: str = "",
) -> dict[str, Any] | None:
    """Count a clarification without replacing the decision still awaiting input."""

    state = dialogue_state(session)
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        return None
    try:
        repeat_count = int(pending.get("repeat_count", 1))
    except (TypeError, ValueError):
        repeat_count = 1
    pending["repeat_count"] = max(1, repeat_count + 1)
    normalized_candidate = candidate_answer.strip()
    if (
        normalized_candidate
        and pending.get("type")
        in {"ANSWER_IDEA_FACET", "ANSWER_STAGE_QUESTION"}
        and not str(pending.get("candidate_answer") or "").strip()
    ):
        pending["candidate_answer"] = normalized_candidate[:2000]
    return deepcopy(pending)


def hydrate_pending_action_from_history(
    session: DesignSession,
) -> dict[str, Any] | None:
    """Migrate a pre-upgrade conversation without exposing internal fields."""

    current = current_pending_action(session)
    if current is not None:
        migrated = _migrate_legacy_idea_facet_pending(session, current)
        if migrated != current:
            dialogue_state(session)["pending_action"] = deepcopy(migrated)
        return migrated
    if not session.history:
        return None
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


def _migrate_legacy_idea_facet_pending(
    session: DesignSession,
    pending: dict[str, Any],
) -> dict[str, Any]:
    """Bind old generic Stage 1 pending data to the facet already in progress."""

    if session.current_stage != Stage.IDEA_BRAINSTORMING:
        return pending
    development = session.design_context.get("idea_development")
    if not isinstance(development, dict) or development.get("complete") is True:
        return pending
    active = str(development.get("active_facet_id") or "")
    if active not in _IDEA_FACET_IDS:
        return pending
    if (
        pending.get("type") == "ANSWER_IDEA_FACET"
        and pending.get("subject") == active
    ):
        return pending
    facets = development.get("facets", {})
    facet = facets.get(active, {}) if isinstance(facets, dict) else {}
    migrated = deepcopy(pending)
    migrated.update(
        {
            "type": "ANSWER_IDEA_FACET",
            "subject": active,
            "proposal": {
                "facet_id": active,
                "title": str(facet.get("title") or "当前部分")
                if isinstance(facet, dict)
                else "当前部分",
            },
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                UserIntent.ADVANCE_STAGE.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                UserIntent.NEW_TOPIC.value,
                UserIntent.UNCLEAR.value,
            ],
            "status": "PENDING",
        }
    )
    return migrated


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
    payloads: list[dict[str, Any]] = []
    drafts = session.design_context.get("guided_stage_drafts", {})
    if isinstance(drafts, dict):
        payloads.extend(
            value for value in drafts.values() if isinstance(value, dict)
        )
    for stage in reversed(tuple(Stage)):
        stage_output = session.stage_outputs.get(stage.value, {})
        payload = stage_output.get("stage_payload", {}) if isinstance(stage_output, dict) else {}
        if isinstance(payload, dict):
            payloads.append(payload)
    for payload in payloads:
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
    development = session.design_context.get("idea_development", {})
    development = development if isinstance(development, dict) else {}
    facets = development.get("facets", {})
    facets = facets if isinstance(facets, dict) else {}

    def facet_evidence(facet_id: str) -> str:
        facet = facets.get(facet_id, {})
        if not isinstance(facet, dict) or facet.get("status") != "CLEAR":
            return ""
        return str(facet.get("evidence") or "").strip()

    direction = str(
        idea.get("main_direction")
        or idea.get("direction_summary")
        or idea.get("current_focus")
        or outline.get("core_phenomenon")
        or idea.get("original")
        or ""
    ).strip()
    return {
        "research_direction": direction,
        "course_relationships": deepcopy(
            outline.get("course_relationships")
            or idea.get("selected_course_relations")
            or []
        ),
        "learning_objective": facet_evidence("learning_objective"),
        "research_question": facet_evidence("research_question"),
        "hypothesis": facet_evidence("hypothesis"),
        "conceptual_structure": facet_evidence("conceptual_structure"),
        "baseline_comparisons": deepcopy(
            outline.get("baseline_comparisons")
            or idea.get("standard_comparisons")
            or []
        ),
        "independent_variable": _find_payload_values(
            session,
            {"independent_variable", "adjustable_quantity_in_vr"},
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
            development
        ),
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
        "repeat_count": 1,
        "advance_on_accept": bool(raw.get("advance_on_accept", False)),
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
    question = str(output.student_task or raw.get("question") or "").strip()
    if not question and ("？" in output.assistant_message or "?" in output.assistant_message):
        question = output.assistant_message[-600:]
    proposal = _proposal_from_output(output)
    if proposal is None and raw.get("proposal") is not None:
        proposal = deepcopy(raw.get("proposal"))
    if (
        stage is not Stage.IDEA_BRAINSTORMING
        and question
        and not raw.get("type")
    ):
        raw.update(
            {
                "type": "ANSWER_STAGE_QUESTION",
                "subject": stage.value,
                "proposal": deepcopy(proposal) if proposal is not None else {"stage": stage.value},
                "question": question,
                "allowed_intents": [
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
                    UserIntent.ADVANCE_STAGE.value,
                    UserIntent.REQUEST_MORE_EXAMPLES.value,
                    UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                    UserIntent.NEW_TOPIC.value,
                    UserIntent.UNCLEAR.value,
                ],
            }
        )
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
    previous = state.get("pending_action")
    if (
        isinstance(previous, dict)
        and previous.get("type") == pending.get("type")
        and previous.get("subject") == pending.get("subject")
    ):
        try:
            previous_count = int(previous.get("repeat_count", 1))
        except (TypeError, ValueError):
            previous_count = 1
        pending["repeat_count"] = max(1, previous_count + 1)
    state["pending_action"] = pending
    state["carried_context"] = build_carried_context(session)
    return deepcopy(pending)


def deterministic_intent(
    _user_message: str,
    _pending_action: dict[str, Any] | None,
    *,
    selected_option_id: str | None = None,
    complete_stage: bool = False,
    interaction_state: InteractionState | None = None,
) -> dict[str, Any] | None:
    """Handle explicit UI events only; typed language always uses semantic resolution."""

    if interaction_state is not None:
        return resolved_intent(
            UserIntent.SET_INTERACTION_STATE,
            target="interaction_state",
            resolved_value=interaction_state.value,
            confidence=1.0,
            semantic_updates={"interaction_state_request": interaction_state.value},
        )
    if complete_stage:
        return resolved_intent(UserIntent.ADVANCE_STAGE, confidence=1.0)
    if selected_option_id:
        return resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target=selected_option_id,
            resolved_value=selected_option_id,
            confidence=1.0,
        )
    return None


def fallback_intent(
    user_message: str,
    pending_action: dict[str, Any] | None,
) -> dict[str, Any]:
    """Conservative offline behavior when no semantic model is available."""

    compact = re.sub(r"[\s，,。；;！!？?]+", "", user_message)
    if len(compact) < 6:
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.3,
            source="CONSERVATIVE_FALLBACK",
        )
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
    confirms_saved_candidate = bool(
        intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        and isinstance(pending_action, dict)
        and pending_action.get("type")
        in {"ANSWER_IDEA_FACET", "ANSWER_STAGE_QUESTION"}
        and str(pending_action.get("candidate_answer") or "").strip()
    )
    requests_reference_for_open_question = bool(
        intent == UserIntent.REQUEST_MORE_EXAMPLES.value
        and isinstance(pending_action, dict)
        and pending_action.get("type")
        in {"ANSWER_IDEA_FACET", "ANSWER_STAGE_QUESTION"}
    )
    if (
        intent not in allowed
        and not confirms_saved_candidate
        and not requests_reference_for_open_question
        and intent not in {
        UserIntent.NEW_TOPIC.value,
        UserIntent.RETURN_TO_PREVIOUS_POINT.value,
        UserIntent.SET_INTERACTION_STATE.value,
        }
    ):
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
    source = str(raw.get("source") or "SEMANTIC")
    resolved_value = raw.get("resolved_value")
    if (
        intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        and isinstance(pending_action, dict)
        and pending_action.get("type")
        in {"ANSWER_IDEA_FACET", "ANSWER_STAGE_QUESTION"}
        and str(pending_action.get("candidate_answer") or "").strip()
    ):
        candidate_answer = str(pending_action["candidate_answer"]).strip()
        intent = UserIntent.ANSWER_CURRENT_QUESTION.value
        resolved_value = candidate_answer
        source = "CONFIRMED_PENDING_ANSWER"
        required_facet = required_pending_facet_id(pending_action)
        if required_facet is not None:
            semantic_updates["facet_updates"] = [
                {"facet_id": required_facet, "status": "CLEAR"}
            ]
        else:
            semantic_updates["pending_answer_status"] = "CLEAR"
    if (
        source.startswith("SEMANTIC")
        and intent
        in {
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        }
        and pending_question_decision_missing(
            intent,
            semantic_updates,
            pending_action,
        )
    ):
        # If the semantic resolver confidently identified an ordinary reply
        # as the answer to the currently pending question, a missing status is
        # a structured-output omission. Do not make the student classify or
        # repeat their own answer; bind it to the exact pending question.
        if (
            confidence >= 0.8
            and intent == UserIntent.ANSWER_CURRENT_QUESTION.value
        ):
            required_facet = required_pending_facet_id(pending_action)
            if required_facet is not None:
                semantic_updates["facet_updates"] = [
                    {"facet_id": required_facet, "status": "CLEAR"}
                ]
            elif (
                isinstance(pending_action, dict)
                and pending_action.get("type") == "ANSWER_STAGE_QUESTION"
            ):
                semantic_updates["pending_answer_status"] = "CLEAR"
            source = "SEMANTIC_RECOVERED_ANSWER_STATUS"
        else:
            intent = UserIntent.UNCLEAR.value
            semantic_updates = {}
    if (
        source.startswith("SEMANTIC")
        and pending_question_answer_needs_review(
            intent,
            semantic_updates,
            pending_action,
        )
    ):
        # A model must not make an open question remain missing while also
        # claiming that the student answered it.  The semantic resolver gets
        # one dedicated repair attempt before validation; if the contradiction
        # survives, keep the design unchanged and offer help for the exact
        # pending question instead of making the student repeat it.
        intent = UserIntent.REQUEST_MORE_EXAMPLES.value
        semantic_updates = {}
        source = "SEMANTIC_RECOVERED_REFERENCE_REQUEST"
    advance_requested = raw.get("advance_requested")
    if (
        intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        and isinstance(pending_action, dict)
        and pending_action.get("advance_on_accept") is True
    ):
        advance_requested = True
    return resolved_intent(
        intent,
        target=str(raw.get("target") or pending_action.get("subject") or "")
        if pending_action
        else str(raw.get("target") or ""),
        resolved_value=resolved_value,
        advance_requested=advance_requested,
        preserve_current_design=raw.get("preserve_current_design", True),
        confidence=confidence,
        source=source,
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
    requested_state = str(raw.get("interaction_state_request") or "").upper()
    course_scope_status = str(raw.get("course_scope_status") or "").upper()
    return {
        "selected_option_ids": selected_option_ids,
        "no_direction": raw.get("no_direction") is True,
        "facet_updates": facet_updates,
        "comparison_updates": comparison_updates,
        "pending_answer_status": (
            str(raw.get("pending_answer_status") or "").upper()
            if str(raw.get("pending_answer_status") or "").upper()
            in {"CLEAR", "MISSING"}
            else None
        ),
        "interaction_state_request": (
            requested_state
            if requested_state in {
                InteractionState.GUIDED_DESIGN.value,
                InteractionState.EMVR_DIRECT.value,
            }
            else None
        ),
        "course_scope_status": (
            course_scope_status
            if course_scope_status
            in {"COURSE_CONTENT", "OUT_OF_SCOPE", "UNCERTAIN"}
            else "UNCERTAIN"
        ),
    }


def required_pending_facet_id(
    pending_action: dict[str, Any] | None,
) -> str | None:
    if not isinstance(pending_action, dict):
        return None
    if pending_action.get("type") != "ANSWER_IDEA_FACET":
        return None
    subject = str(pending_action.get("subject") or "")
    return subject if subject in _IDEA_FACET_IDS else None


def pending_facet_decision_missing(
    intent: UserIntent | str,
    semantic_updates: dict[str, Any] | None,
    pending_action: dict[str, Any] | None,
) -> bool:
    intent_value = intent.value if isinstance(intent, UserIntent) else str(intent)
    if intent_value not in {
        UserIntent.ANSWER_CURRENT_QUESTION.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
    }:
        return False
    required_facet = required_pending_facet_id(pending_action)
    if required_facet is None:
        return False
    if (
        intent_value == UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
        and isinstance(semantic_updates, dict)
        and (
            semantic_updates.get("comparison_updates")
            or semantic_updates.get("selected_option_ids")
        )
    ):
        # A student can edit a concrete proposal that is still visible in the
        # conversation without answering the separate idea-facet question in
        # the same sentence.  Treat the structured edit as its own complete
        # action and leave the active facet pending for the next turn.
        return False
    updates = (
        semantic_updates.get("facet_updates", [])
        if isinstance(semantic_updates, dict)
        else []
    )
    return not any(
        isinstance(item, dict)
        and item.get("facet_id") == required_facet
        and item.get("status") in {"CLEAR", "MISSING"}
        for item in updates
    )


def pending_question_answer_needs_review(
    intent: UserIntent | str,
    semantic_updates: dict[str, Any] | None,
    pending_action: dict[str, Any] | None,
) -> bool:
    """Detect a semantic answer/status contradiction for any open question.

    ``MISSING`` is a useful description of the design state, but it is not a
    coherent result when the resolver has simultaneously classified the same
    turn as the student's answer.  In that situation the semantic model must
    review the whole turn again and choose one of two meaningful outcomes:
    either the answer clears the pending item, or the student is requesting a
    reference/has not answered it.  Keeping this check at the pending-action
    layer makes it apply to every idea facet and every later guided stage.
    """

    intent_value = intent.value if isinstance(intent, UserIntent) else str(intent)
    if intent_value not in {
        UserIntent.ANSWER_CURRENT_QUESTION.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
    } or not isinstance(pending_action, dict):
        return False
    pending_type = pending_action.get("type")
    if pending_type == "ANSWER_IDEA_FACET":
        required_facet = required_pending_facet_id(pending_action)
        if required_facet is None:
            return False
        updates = (
            semantic_updates.get("facet_updates", [])
            if isinstance(semantic_updates, dict)
            else []
        )
        return any(
            isinstance(item, dict)
            and item.get("facet_id") == required_facet
            and item.get("status") == "MISSING"
            for item in updates
        )
    if pending_type == "ANSWER_STAGE_QUESTION":
        return bool(
            isinstance(semantic_updates, dict)
            and semantic_updates.get("pending_answer_status") == "MISSING"
        )
    return False


def pending_question_decision_missing(
    intent: UserIntent | str,
    semantic_updates: dict[str, Any] | None,
    pending_action: dict[str, Any] | None,
) -> bool:
    """Require a semantic answer decision for every guided question type."""

    if pending_facet_decision_missing(intent, semantic_updates, pending_action):
        return True
    intent_value = intent.value if isinstance(intent, UserIntent) else str(intent)
    if (
        intent_value != UserIntent.ANSWER_CURRENT_QUESTION.value
        or not isinstance(pending_action, dict)
        or pending_action.get("type") != "ANSWER_STAGE_QUESTION"
    ):
        return False
    status = (
        semantic_updates.get("pending_answer_status")
        if isinstance(semantic_updates, dict)
        else None
    )
    return status not in {"CLEAR", "MISSING"}


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
        if pending_action.get("type") == "ANSWER_IDEA_FACET":
            proposal = pending_action.get("proposal", {})
            title = str(
                proposal.get("title")
                if isinstance(proposal, dict)
                else ""
            ).strip() or "当前部分"
            try:
                repeat_count = int(pending_action.get("repeat_count", 1))
            except (TypeError, ValueError):
                repeat_count = 1
            question = str(pending_action.get("question") or "").strip()
            if repeat_count > 2:
                message = (
                    f"“{title}”这一点先不要求你重复。"
                    "如果暂时没有想法，可以请我先给一个课程内参考；"
                    "也可以直接补充一个你认为重要的新细节。"
                )
            else:
                focus = question or f"请用一句话说明你对“{title}”的想法。"
                message = f"我们先聚焦“{title}”：{focus}"
                message += " 如果暂时没有想法，我可以先给一个课程内参考。"
            return StepOutput(
                assistant_message=message,
                stage_payload={"clarification_required": True},
                student_task=None,
            )
        question = str(pending_action.get("question") or "").strip()
        if pending_action.get("type") == "ANSWER_STAGE_QUESTION":
            try:
                repeat_count = int(pending_action.get("repeat_count", 1))
            except (TypeError, ValueError):
                repeat_count = 1
            if repeat_count > 2:
                message = (
                    "这一点先不要求你重复。你可以请我给一个可修改的参考，"
                    "也可以直接补充一个新的关键细节。"
                )
            else:
                message = f"当前还需要明确：{question or '请补充这一阶段最关键的设计内容。'}"
                message += " 如果暂时没有想法，我可以先给一个可修改的参考。"
            return StepOutput(
                assistant_message=message,
                stage_payload={"clarification_required": True},
                student_task=None,
            )
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
            "interaction_state": session.interaction_state.value,
            "previous_question": previous_question,
            "pending_action": pending_action,
            "carried_context": carried_context,
            "user_message": user_message,
        },
        ensure_ascii=False,
    )
