from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from copy import deepcopy
from threading import RLock
from typing import Any

from .dialogue_state import (
    UserIntent,
    accept_pending_comparisons_on_advance,
    apply_resolved_intent,
    build_carried_context,
    clarification_output,
    current_pending_action,
    deterministic_intent,
    fallback_intent,
    hydrate_pending_action_from_history,
    record_pending_clarification,
    resolved_intent,
    save_pending_action,
    validate_resolved_intent,
)
from .generator import (
    StageGenerator,
    guided_stage_entry_output,
)
from .guardrails import (
    BREADTH_EXPLORATION,
    COURSE_CONTENT,
    INTEREST_DESCRIPTION,
    UNREASONABLE_REQUEST,
    build_stage_one_turn_context,
    latest_stage_one_options,
    latest_stage_one_scenes,
    preclassify_stage_one_input,
)
from .idea_development import (
    build_gap_output,
    decorate_outline_output,
    has_idea_development,
    initialize_idea_development,
    public_idea_development_status,
    update_idea_development,
)
from .knowledge_base import KNOWLEDGE
from .models import (
    STAGE_SEQUENCE,
    DesignSession,
    InteractionState,
    Stage,
    StageCompletionError,
    StepOutput,
    TurnRequest,
    WorkflowStatus,
)
from .prompts import build_prompt_packet
from .openai_generator import generator_from_environment
from .stages import (
    IDEA_DEVELOPMENT_STAGES,
    STAGES_BY_ID,
    public_stage_catalog,
    stage_group_metadata,
    stage_title,
)
from .store import SessionStore, store_from_environment


_GUIDED_COMPLETION_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.COURSE_MAPPING_AND_DIRECTION: ("course_references", "primary_course_anchor"),
    Stage.LEARNING_OBJECTIVES: ("objective_types",),
    Stage.RESEARCH_QUESTION: ("candidate_independent_variables", "main_research_question"),
    Stage.THEORETICAL_FRAMEWORK: ("core_equations", "lecture_formula_candidates"),
    Stage.HYPOTHESIS: ("trend_choices", "research_hypothesis"),
    Stage.CONCEPTUAL_OR_VR_SETUP: ("module_focus",),
    Stage.VARIABLES_AND_CONDITIONS: ("variable_type", "independent_variable"),
    Stage.CONCEPTUAL_PROCEDURE: ("procedure_unit", "procedure_steps"),
    Stage.RESULT_INTERPRETATION: ("result_case", "if_prediction_supported"),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: ("review_dimension", "limitations"),
}

_GUIDED_COMPLETION_HINTS: dict[Stage, str] = {
    Stage.COURSE_MAPPING_AND_DIRECTION: (
        "课程联系还没有整理清楚。请先说明这个想法主要对应ECE329中的哪一类物理关系。"
    ),
    Stage.LEARNING_OBJECTIVES: (
        "学习目标还差一点。请先说明完成这个实验后，你希望能够解释、判断或比较什么。"
    ),
    Stage.RESEARCH_QUESTION: (
        "研究问题还没有完整连起来。请说清准备比较什么条件，以及观察什么现象。"
    ),
    Stage.THEORETICAL_FRAMEWORK: (
        "理论依据还没有确定。请先指出最能解释当前现象的一条ECE329课程关系。"
    ),
    Stage.HYPOTHESIS: (
        "预期趋势还不够明确。请说明关键条件变化时，你预计观察结果会怎样变化。"
    ),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "实验结构还差一个清楚的组成说明。请补充需要哪些对象、条件或观察方式。"
    ),
    Stage.VARIABLES_AND_CONDITIONS: (
        "变量关系还没有完全说明。请补充主动改变的量，或准备观察的结果。"
    ),
    Stage.CONCEPTUAL_PROCEDURE: (
        "实验流程还缺少可比较的关键环节。请补充基准、改变条件、观察或比较中的一项。"
    ),
    Stage.RESULT_INTERPRETATION: (
        "结果解释还没有形成。请先说明结果符合预期或偏离预期时，分别可能意味着什么。"
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: (
        "设计边界还没有说清楚。请补充一个可能限制结论的理想化条件或展示局限。"
    ),
}


def _contains_emvr_marker(text: str) -> bool:
    """Return whether the user explicitly included the EMVR mode marker.

    This is intentionally the only natural-language mode shortcut.  All other
    conversational meaning is resolved from ``pending_action`` and context.
    """

    return "EMVR" in text.upper()

_TRANSIENT_GUIDED_PAYLOAD_KEYS = {
    "guided_entry",
    "awaiting_student_description",
    "preserved_idea_summary",
    "reference_basis",
    "pending_action",
    "clarification_required",
    "repeated_question_avoided",
    "stage_ready_for_confirmation",
    "stage_readiness",
    "contextual_continuation",
}


def _normalized_question(text: str) -> str:
    return re.sub(r"[\s，,。；;：:！!？?、（）()\-—]+", "", text).casefold()


def _remove_repeated_guided_question(
    output: Any,
    pending_action: dict[str, Any] | None,
    student_message: str,
) -> None:
    """Prevent a completed guided answer from triggering the same question again."""

    if not isinstance(pending_action, dict):
        return
    previous = _normalized_question(str(pending_action.get("question") or ""))
    if len(previous) < 8:
        return
    next_task = _normalized_question(str(output.student_task or ""))
    assistant = _normalized_question(str(output.assistant_message or ""))
    task_repeated = bool(next_task and (next_task == previous or previous in next_task))
    assistant_repeated = previous in assistant
    if not task_repeated and not assistant_repeated:
        return
    acknowledgement = "收到，这一部分已经按你的意思更新了。"
    if assistant_repeated:
        output.assistant_message = (
            f"{acknowledgement}不用再回答同一个问题；"
            "还想补充就接着说，觉得已经合适也可以继续下一步。"
        )
    else:
        output.assistant_message = (
            f"{output.assistant_message}\n\n{acknowledgement}"
            "这一问不再重复；还想补充就接着说，觉得合适也可以继续下一步。"
        )
    output.student_task = None
    output.stage_payload["repeated_question_avoided"] = True


def _guided_stage_has_minimum_content(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
) -> bool:
    if stage is Stage.EXPECTED_DATA_VISUALIZATION:
        return isinstance(output.visualization, dict) or isinstance(
            session.stage_outputs.get(stage.value, {}).get("visualization"),
            dict,
        )
    required_fields = _GUIDED_COMPLETION_FIELDS.get(stage)
    if not required_fields:
        return False
    drafts = session.design_context.get("guided_stage_drafts", {})
    draft = drafts.get(stage.value, {}) if isinstance(drafts, dict) else {}
    combined = deepcopy(draft) if isinstance(draft, dict) else {}
    _deep_merge(combined, output.stage_payload)
    return any(combined.get(field) for field in required_fields)


def _prepare_guided_stage_completion(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
) -> None:
    """Create one stage-level decision from structured answer state and artifacts."""

    readiness = output.stage_payload.get("stage_readiness")
    ready_for_confirmation = bool(
        isinstance(readiness, dict)
        and readiness.get("ready_for_confirmation") is True
        and readiness.get("remaining_gaps") == []
    )
    if (
        session.interaction_state is not InteractionState.GUIDED_DESIGN
        or stage in {
            Stage.IDEA_BRAINSTORMING,
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        }
        or not ready_for_confirmation
        or not _guided_stage_has_minimum_content(session, stage, output)
    ):
        return
    completion_task = (
        "如果这部分和你的想法一致，直接告诉我继续就可以；"
        "如果想改，也只要指出要调整的地方。"
    )
    output.assistant_message = (
        f"{output.assistant_message.rstrip()}\n\n"
        "这一部分已经能连起来了，不用再逐项确认。"
    )
    output.student_task = completion_task
    output.stage_payload["stage_ready_for_confirmation"] = True
    output.stage_payload["pending_action"] = {
        "type": "CONFIRM_STAGE_OR_MODIFY",
        "subject": stage.value,
        "proposal": {"stage": stage.value, "ready": True},
        "question": completion_task,
        "advance_on_accept": True,
        "allowed_intents": [
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.ADVANCE_STAGE.value,
            UserIntent.REQUEST_MORE_EXAMPLES.value,
            UserIntent.RETURN_TO_PREVIOUS_POINT.value,
            UserIntent.NEW_TOPIC.value,
            UserIntent.UNCLEAR.value,
        ],
    }


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _persist_guided_stage_draft(
    session: DesignSession,
    stage: Stage,
    payload: dict[str, Any],
) -> None:
    if (
        session.interaction_state is not InteractionState.GUIDED_DESIGN
        or stage is Stage.IDEA_BRAINSTORMING
        or payload.get("clarification_required") is True
    ):
        return
    persistent = {
        key: deepcopy(value)
        for key, value in payload.items()
        if key not in _TRANSIENT_GUIDED_PAYLOAD_KEYS
    }
    if not persistent:
        return
    drafts = session.design_context.setdefault("guided_stage_drafts", {})
    if not isinstance(drafts, dict):
        drafts = {}
        session.design_context["guided_stage_drafts"] = drafts
    draft = drafts.setdefault(stage.value, {})
    if not isinstance(draft, dict):
        draft = {}
        drafts[stage.value] = draft
    _deep_merge(draft, persistent)


def _persist_guided_student_summary(
    session: DesignSession,
    message: str,
    semantic_updates: dict[str, Any] | None,
) -> bool:
    """Save a student-written final summary after semantic completeness review."""

    if (
        session.interaction_state is not InteractionState.GUIDED_DESIGN
        or session.current_stage is not Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
        or not isinstance(semantic_updates, dict)
        or semantic_updates.get("pending_answer_status") != "CLEAR"
        or not message.strip()
    ):
        return False
    synthesis = session.design_context.setdefault("synthesis", {})
    if not isinstance(synthesis, dict):
        synthesis = {}
        session.design_context["synthesis"] = synthesis
    summary = message.strip()
    synthesis.update(
        {
            "student_summary": summary,
            "student_summary_sections": [summary],
            "student_summary_complete": True,
            "completion_source": "SEMANTIC_SUMMARY_REVIEW",
        }
    )
    return True


def _guided_summary_review_output() -> StepOutput:
    task = (
        "如果这就是你想保留的最终总结，直接告诉我确认完成；"
        "想调整的话，也可以直接补充或改写。"
    )
    return StepOutput(
        assistant_message=(
            "这段总结已经把研究问题、主要比较、预期现象和课程关系串起来了。"
            "我保留了你的原意，没有替你改写成另一份方案。"
        ),
        stage_payload={
            "student_summary_received": True,
            "final_proposal_generated": False,
            "pending_action": {
                "type": "CONFIRM_STAGE_OR_MODIFY",
                "subject": Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT.value,
                "proposal": {"student_summary_complete": True},
                "question": task,
                "advance_on_accept": True,
                "allowed_intents": [
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.ADVANCE_STAGE.value,
                    UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                    UserIntent.UNCLEAR.value,
                ],
            },
        },
        student_task=task,
    )


def _guided_summary_completion_output() -> StepOutput:
    return StepOutput(
        assistant_message="你的总结已经按原意保留，整个实验设计流程到这里完成。",
        stage_payload={
            "student_summary_confirmed": True,
            "final_proposal_generated": False,
        },
        student_task=None,
    )


class WorkflowEngine:
    def __init__(
        self,
        generator: StageGenerator | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.generator = generator or generator_from_environment()
        self.store = store or store_from_environment()
        self._session_locks = tuple(RLock() for _ in range(64))

    def _resolve_turn_intent(
        self,
        session: DesignSession,
        request: TurnRequest,
        message: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        pending = hydrate_pending_action_from_history(session)
        direct = deterministic_intent(
            message,
            pending,
            selected_option_id=request.selected_option_id,
            complete_stage=request.complete_stage,
            interaction_state=request.interaction_state,
        )
        if direct is not None:
            return validate_resolved_intent(direct, pending), pending
        resolver = getattr(self.generator, "resolve_intent", None)
        if callable(resolver):
            carried_context = build_carried_context(session)
            carried_context["current_course_evidence"] = {
                "lecture_concepts": [
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "concepts": item.get("concepts", []),
                    }
                    for item in KNOWLEDGE.match_concepts(message, limit=4)
                ],
                "supplemental_concepts": [
                    {
                        "id": item.get("supplemental_concept_id"),
                        "title": item.get("title"),
                        "concepts": item.get("concepts", []),
                    }
                    for item in KNOWLEDGE.match_supplemental_concepts(
                        message,
                        limit=4,
                    )
                ],
                "scope_summary": (
                    "ECE329 covers electrostatics, electric potential and materials; "
                    "magnetostatics and induction; Maxwell equations, electromagnetic "
                    "waves, polarization, interfaces, conductors, and transmission lines."
                ),
            }
            semantic = resolver(
                session,
                message,
                pending,
                carried_context,
            )
            return validate_resolved_intent(semantic, pending), pending
        # Offline/rule-only deployments cannot resolve conversational commands.
        # Explicit UI actions still arrive through complete_stage above; typed
        # language remains an answer instead of being guessed from keywords.
        return validate_resolved_intent(fallback_intent(message, pending), pending), pending

    @staticmethod
    def _interaction_state_from_intent(
        turn_intent: dict[str, Any],
    ) -> InteractionState | None:
        """Read a validated mode request from structured intent output."""

        updates = turn_intent.get("semantic_updates", {})
        requested = (
            updates.get("interaction_state_request")
            if isinstance(updates, dict)
            else None
        )
        if (
            requested is None
            and turn_intent.get("intent")
            == UserIntent.SET_INTERACTION_STATE.value
        ):
            requested = turn_intent.get("resolved_value")
        try:
            return InteractionState(str(requested)) if requested else None
        except ValueError:
            return None

    @staticmethod
    def _return_to_previous_stage(session: DesignSession) -> None:
        if session.current_stage_index <= 0:
            return
        session.current_stage_index -= 1
        session.status = WorkflowStatus.ACTIVE
        previous = session.current_stage.value
        session.completed_stages = [
            stage for stage in session.completed_stages if stage != previous
        ]

    @staticmethod
    def _start_new_topic(session: DesignSession, message: str) -> None:
        previous_design = {
            "idea": deepcopy(session.design_context.get("idea", {})),
            "stage_outputs": deepcopy(session.stage_outputs),
        }
        archive = session.model_context.setdefault("previous_designs", [])
        if isinstance(archive, list):
            archive.append(previous_design)
            del archive[:-3]
        session.current_stage_index = 0
        session.status = WorkflowStatus.ACTIVE
        session.completed_stages = []
        session.stage_outputs = {}
        session.design_context = {"idea": {"original": message.strip()}}
        session.model_context.pop("openai_previous_response_id", None)
        session.model_context.pop("dialogue_state", None)

    def create_design(
        self,
        idea: str,
        interaction_state: InteractionState | str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(idea, str):
            raise ValueError("idea must be a string")
        if not idea.strip():
            raise ValueError("idea must not be empty")
        requested_state = self._coerce_state(interaction_state)
        # Structured API/UI state remains authoritative except for the single
        # public shortcut requested by the product: any safe message that
        # contains the literal marker "EMVR" enters EMVR mode.
        state = requested_state or InteractionState.GUIDED_DESIGN
        input_kind = preclassify_stage_one_input(idea)
        if input_kind == UNREASONABLE_REQUEST:
            state = InteractionState.GUIDED_DESIGN
        elif _contains_emvr_marker(idea):
            state = InteractionState.EMVR_DIRECT
        access_token = secrets.token_urlsafe(32)
        session = DesignSession(
            design_id=f"design_{uuid.uuid4().hex[:12]}",
            interaction_state=state,
            access_token_hash=hashlib.sha256(access_token.encode("utf-8")).hexdigest(),
            design_context={"idea": {"original": idea.strip()}},
        )
        self.store.save(session)
        result = self.process_turn(
            session.design_id,
            TurnRequest(message=idea.strip()),
        )
        result["design_access_token"] = access_token
        return result

    def process_turn(
        self,
        design_id: str,
        request: TurnRequest | dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock_for_design(design_id):
            return self._process_turn_locked(design_id, request)

    def _process_turn_locked(
        self,
        design_id: str,
        request: TurnRequest | dict[str, Any],
    ) -> dict[str, Any]:
        session = self.store.get(design_id)
        if session.status is WorkflowStatus.COMPLETE:
            return {
                "design_id": session.design_id,
                "interaction_state": session.interaction_state.value,
                "status": session.status.value,
                "workflow_status": session.status.value,
                "message": "该设计工作流已经完成。",
                "assistant_message": "该设计工作流已经完成。",
                "current_stage": session.current_stage.value,
                "next_stage": None,
            }
        if isinstance(request, dict):
            request = self._request_from_dict(request)
        if not isinstance(request.message, str):
            raise ValueError("message must be a string")
        message = request.message.strip()
        if not message:
            raise ValueError("message must not be empty")
        # This deterministic pass is only the hard safety gate. Course scope
        # is resolved later from structured semantic output plus retrieval.
        input_kind = preclassify_stage_one_input(message)

        idea_before_patch = session.design_context.get("idea", {})
        authoritative_course_scope = bool(
            isinstance(idea_before_patch, dict)
            and idea_before_patch.get("course_scope_confirmed") is True
        )
        _deep_merge(session.design_context, request.context_patch)
        if (
            session.current_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
        ):
            patched_idea = session.design_context.get("idea", {})
            if isinstance(patched_idea, dict):
                if authoritative_course_scope:
                    patched_idea["course_scope_confirmed"] = True
                else:
                    patched_idea.pop("course_scope_confirmed", None)
        expected_revision = session.revision
        transitioned_from_stage: Stage | None = None
        completion_error: str | None = None
        if input_kind == UNREASONABLE_REQUEST:
            turn_intent = resolved_intent(
                UserIntent.ANSWER_CURRENT_QUESTION,
                confidence=1.0,
                source="SAFETY_GUARDRAIL",
            )
            pending_action = current_pending_action(session)
        else:
            turn_intent, pending_action = self._resolve_turn_intent(
                session,
                request,
                message,
            )
        interaction_state_changed = False
        if input_kind != UNREASONABLE_REQUEST:
            if _contains_emvr_marker(message):
                # Keep the state transition visible in the structured turn
                # record while preserving any substantive experiment intent
                # returned by the contextual resolver.
                marker_updates = turn_intent.setdefault("semantic_updates", {})
                if not isinstance(marker_updates, dict):
                    marker_updates = {}
                    turn_intent["semantic_updates"] = marker_updates
                marker_updates["interaction_state_request"] = (
                    InteractionState.EMVR_DIRECT.value
                )
                turn_intent["emvr_marker_applied"] = True
                if turn_intent.get("intent") == UserIntent.UNCLEAR.value:
                    turn_intent.update(
                        {
                            "intent": UserIntent.SET_INTERACTION_STATE.value,
                            "target": "interaction_state",
                            "resolved_value": InteractionState.EMVR_DIRECT.value,
                            "confidence": 1.0,
                            "source": "EMVR_MARKER",
                        }
                    )
                requested_interaction_state = InteractionState.EMVR_DIRECT
            else:
                requested_interaction_state = self._interaction_state_from_intent(
                    turn_intent
                )
            if (
                requested_interaction_state is not None
                and requested_interaction_state is not session.interaction_state
            ):
                session.interaction_state = requested_interaction_state
                interaction_state_changed = True
                pending_action = None
                dialogue = session.model_context.get("dialogue_state")
                if isinstance(dialogue, dict):
                    dialogue.pop("pending_action", None)
        if (
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage in IDEA_DEVELOPMENT_STAGES[1:]
        ):
            # Guided mode treats the former fixed Stages 2-7 as facets of the
            # dynamic first stage. The state transition is based on the
            # resolver's structured result, never on wording in the message.
            session.current_stage_index = 0
        if (
            turn_intent.get("intent") == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
            and isinstance(pending_action, dict)
            and pending_action.get("type") == "ANSWER_STAGE_QUESTION"
            and pending_action.get("subject") == session.current_stage.value
        ):
            stored_output = session.stage_outputs.get(session.current_stage.value, {})
            stored_payload = (
                stored_output.get("stage_payload", {})
                if isinstance(stored_output, dict)
                else {}
            )
            compatibility_output = StepOutput(
                assistant_message="",
                stage_payload=(
                    deepcopy(stored_payload)
                    if isinstance(stored_payload, dict)
                    else {}
                ),
                visualization=(
                    deepcopy(stored_output.get("visualization"))
                    if isinstance(stored_output, dict)
                    and isinstance(stored_output.get("visualization"), dict)
                    else None
                ),
            )
            if _guided_stage_has_minimum_content(
                session,
                session.current_stage,
                compatibility_output,
            ):
                turn_intent["advance_requested"] = True
                pending_action["advance_on_accept"] = True
        apply_resolved_intent(session, turn_intent, pending_action, message)
        intent_name = str(turn_intent.get("intent") or UserIntent.UNCLEAR.value)
        resolved_value = turn_intent.get("resolved_value")
        resolved_student_message = (
            resolved_value.strip()
            if (
                isinstance(resolved_value, str)
                and resolved_value.strip()
                and intent_name
                in {
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                }
                and (
                    str(turn_intent.get("source") or "").startswith("SEMANTIC")
                    or turn_intent.get("source") == "CONFIRMED_PENDING_ANSWER"
                )
            )
            else message
        )
        semantic_updates = (
            deepcopy(turn_intent.get("semantic_updates", {}))
            if (
                str(turn_intent.get("source") or "").startswith("SEMANTIC")
                or turn_intent.get("source") == "CONFIRMED_PENDING_ANSWER"
            )
            else None
        )
        content_intent_name = intent_name
        summary_completed_this_turn = False
        if intent_name in {
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        }:
            summary_completed_this_turn = _persist_guided_student_summary(
                session,
                resolved_student_message,
                semantic_updates,
            )

        if intent_name == UserIntent.NEW_TOPIC.value:
            self._start_new_topic(session, message)
            pending_action = None
            apply_resolved_intent(session, turn_intent, pending_action, message)
        elif intent_name == UserIntent.RETURN_TO_PREVIOUS_POINT.value:
            self._return_to_previous_stage(session)

        explicit_transition_intent = bool(
            intent_name == UserIntent.ADVANCE_STAGE.value
            or (
                turn_intent.get("advance_requested") is True
                and intent_name
                in {
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                }
            )
        )
        pre_transition_attempted = bool(
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is not Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            and explicit_transition_intent
        )
        final_summary_confirmation_turn = bool(
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            and explicit_transition_intent
            and isinstance(session.design_context.get("synthesis"), dict)
            and session.design_context["synthesis"].get("student_summary_complete")
            is True
        )
        if pre_transition_attempted:
            previous_stage = session.current_stage
            if previous_stage is Stage.IDEA_BRAINSTORMING:
                idea = session.design_context.get("idea", {})
                if isinstance(idea, dict):
                    development = session.design_context.get("idea_development", {})
                    outline = session.design_context.get("experiment_outline_seed", {})
                    if (
                        isinstance(development, dict)
                        and development.get("complete") is True
                        and isinstance(outline, dict)
                    ):
                        phenomenon = str(
                            idea.get("core_phenomenon")
                            or outline.get("core_phenomenon")
                            or idea.get("topic_anchor")
                            or ""
                        ).strip()
                        main_direction = str(
                            idea.get("direction_summary")
                            or idea.get("current_focus")
                            or phenomenon
                        ).strip()
                        if phenomenon:
                            idea["phenomenon"] = phenomenon
                        if main_direction:
                            idea["main_direction"] = main_direction
                        idea["student_confirmed"] = True
            try:
                self._validate_completion(session, previous_stage)
                if previous_stage is Stage.IDEA_BRAINSTORMING:
                    accept_pending_comparisons_on_advance(session)
                self._advance(session, previous_stage)
                transitioned_from_stage = previous_stage
            except StageCompletionError as exc:
                completion_error = str(exc)
        handled_stage = session.current_stage
        stage_one_control_turn = bool(
            content_intent_name
            in {
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
                UserIntent.ADVANCE_STAGE.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                UserIntent.SET_INTERACTION_STATE.value,
            }
        )
        dynamic_idea_turn = bool(
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and has_idea_development(session)
            and input_kind != UNREASONABLE_REQUEST
            and (not stage_one_control_turn or completion_error is not None)
            and intent_name != UserIntent.NEW_TOPIC.value
        )
        if (
            dynamic_idea_turn
            and not stage_one_control_turn
            and intent_name
            in {
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            }
        ):
            idea_answer_message = (
                resolved_student_message
            )
            update_idea_development(
                session,
                idea_answer_message,
                semantic_updates=semantic_updates,
            )
        turn_context: dict[str, Any] = {
            "selected_option_id": request.selected_option_id,
            "resolved_intent": deepcopy(turn_intent),
            "pending_action": deepcopy(pending_action),
            "carried_context": build_carried_context(session),
        }
        if dynamic_idea_turn:
            idea_context = session.design_context.get("idea", {})
            stage_one_context = build_stage_one_turn_context(
                resolved_student_message,
                options=latest_stage_one_options(session.history),
                scenes=latest_stage_one_scenes(session.history),
                idea_context=idea_context if isinstance(idea_context, dict) else {},
                selected_option_id=request.selected_option_id,
                semantic_updates=semantic_updates,
                resolved_intent_name=content_intent_name,
            )
            if isinstance(idea_context, dict):
                comparisons = stage_one_context.get("standard_comparisons")
                if isinstance(comparisons, list):
                    idea_context["standard_comparisons"] = deepcopy(comparisons)
            if intent_name == UserIntent.REQUEST_MORE_EXAMPLES.value:
                stage_one_context["brainstorm_phase"] = BREADTH_EXPLORATION
                stage_one_context["more_brainstorm_requested"] = True
            turn_context["idea_development"] = deepcopy(
                session.design_context.get("idea_development", {})
            )
            turn_context.update(stage_one_context)
        elif (
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
        ):
            self._hydrate_legacy_stage_one_thread(session)
            idea_context = session.design_context.get("idea", {})
            turn_context.update(
                build_stage_one_turn_context(
                    resolved_student_message,
                    options=latest_stage_one_options(session.history),
                    scenes=latest_stage_one_scenes(session.history),
                    idea_context=idea_context if isinstance(idea_context, dict) else {},
                    selected_option_id=request.selected_option_id,
                    semantic_updates=semantic_updates,
                    resolved_intent_name=content_intent_name,
                )
            )
        session.turn_context = turn_context
        if transitioned_from_stage is None:
            self._record_student_decision(
                session,
                handled_stage,
                resolved_student_message,
                request.selected_option_id,
                content_intent_name,
            )
        definition = STAGES_BY_ID[handled_stage]
        handled_stage_seen = bool(
            handled_stage.value in session.stage_outputs
            or any(
                item.get("handled_stage") == handled_stage.value
                for item in session.history
            )
        )
        guided_stage_entry_turn = bool(
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and handled_stage is not Stage.IDEA_BRAINSTORMING
            and (
                transitioned_from_stage is not None
                or not handled_stage_seen
                or interaction_state_changed
            )
        )
        clarification_turn = intent_name == UserIntent.UNCLEAR.value
        substantive_guided_reply = bool(
            intent_name == UserIntent.ANSWER_CURRENT_QUESTION.value
            and input_kind != UNREASONABLE_REQUEST
        )
        if final_summary_confirmation_turn:
            output = _guided_summary_completion_output()
            session.turn_context = {}
            completion_error = None
        elif summary_completed_this_turn:
            output = _guided_summary_review_output()
            session.turn_context = {}
            completion_error = None
        elif clarification_turn:
            if (
                turn_intent.get("source") == "CONSERVATIVE_FALLBACK"
                and handled_stage is Stage.IDEA_BRAINSTORMING
                and has_idea_development(session)
            ):
                output = build_gap_output(session, "")
            else:
                pending_action = record_pending_clarification(
                    session,
                    message,
                ) or pending_action
                output = clarification_output(pending_action)
            session.turn_context = {}
            completion_error = None
        elif dynamic_idea_turn:
            confirmed_answer = (
                resolved_student_message
                if turn_intent.get("source") == "CONFIRMED_PENDING_ANSWER"
                else ""
            )
            output = build_gap_output(
                session,
                confirmed_answer or resolved_student_message,
            )
            session.turn_context = {}
            if stage_one_control_turn:
                completion_error = None
        elif guided_stage_entry_turn:
            output = guided_stage_entry_output(session)
            session.turn_context = {}
        else:
            generation_message = (
                resolved_student_message
            )
            try:
                output = self.generator.generate(session, generation_message)
            finally:
                session.turn_context = {}
            if (
                session.interaction_state is InteractionState.GUIDED_DESIGN
                and handled_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
                and output.stage_payload.get("student_summary_received") is True
            ):
                _persist_guided_student_summary(
                    session,
                    generation_message,
                    {"pending_answer_status": "CLEAR"},
                )
            if (
                session.interaction_state is InteractionState.GUIDED_DESIGN
                and handled_stage is not Stage.IDEA_BRAINSTORMING
                and transitioned_from_stage is None
                and substantive_guided_reply
                and intent_name == UserIntent.ANSWER_CURRENT_QUESTION.value
            ):
                _remove_repeated_guided_question(
                    output,
                    pending_action,
                    generation_message,
                )
            if (
                session.interaction_state is InteractionState.GUIDED_DESIGN
                and handled_stage is not Stage.IDEA_BRAINSTORMING
                and transitioned_from_stage is None
                and intent_name
                in {
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.REQUEST_MORE_EXAMPLES.value,
                }
            ):
                _prepare_guided_stage_completion(session, handled_stage, output)
        self._validate_step_output(session.interaction_state, output.student_task)
        if not dynamic_idea_turn:
            self._commit_stage_one_thread(
                session,
                handled_stage,
                message,
                turn_context,
                output,
            )
        if (
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and output.stage_payload.get("request_rejected") is not True
        ):
            outline_seed = output.stage_payload.get("experiment_outline_seed")
            if (
                isinstance(outline_seed, dict)
                and outline_seed
                and not has_idea_development(session)
            ):
                development = initialize_idea_development(
                    session,
                    outline_seed,
                    semantic_updates=semantic_updates,
                )
                decorate_outline_output(output, development)
            elif has_idea_development(session):
                output.stage_payload.setdefault(
                    "idea_development_status",
                    public_idea_development_status(
                        session.design_context["idea_development"]
                    ),
                )
        if output.stage_payload.get("clarification_required") is not True:
            save_pending_action(session, handled_stage, output)
        _persist_guided_stage_draft(session, handled_stage, output.stage_payload)
        session.revision += 1
        output_dict = output.to_dict()
        session.stage_outputs[handled_stage.value] = {
            "revision": session.revision,
            **output_dict,
        }
        session.history.append(
            {
                "revision": session.revision,
                "handled_stage": handled_stage.value,
                "interaction_state": session.interaction_state.value,
                "user_message": message,
                "selected_option_id": request.selected_option_id,
                "resolved_intent": {
                    "intent": intent_name,
                    "target": turn_intent.get("target"),
                    "advance_requested": turn_intent.get("advance_requested") is True,
                    "source": turn_intent.get("source"),
                },
                "transitioned_from_stage": (
                    transitioned_from_stage.value
                    if transitioned_from_stage is not None
                    else None
                ),
                "output": output_dict,
            }
        )

        should_complete = (
            session.interaction_state is InteractionState.EMVR_DIRECT
            or (
                (request.complete_stage or explicit_transition_intent)
                and not pre_transition_attempted
            )
        ) and output.stage_payload.get("request_rejected") is not True
        if (
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and handled_stage is Stage.IDEA_BRAINSTORMING
        ):
            # The student must see and confirm the newly formed outline in a
            # separate turn before course mapping is displayed.
            should_complete = False
        if should_complete:
            try:
                self._validate_completion(session, handled_stage)
                self._advance(session, handled_stage)
            except StageCompletionError as exc:
                completion_error = str(exc)

        state = session.model_context.get("dialogue_state", {})
        if isinstance(state, dict):
            state["carried_context"] = build_carried_context(session)

        self.store.save(session, expected_revision=expected_revision)
        next_stage = session.next_stage.value if session.next_stage else None
        response = {
            "design_id": session.design_id,
            "interaction_state": session.interaction_state.value,
            "handled_stage": handled_stage.value,
            "handled_stage_number": definition.number,
            "handled_stage_title": stage_title(handled_stage, session.interaction_state),
            **stage_group_metadata(handled_stage, session.interaction_state),
            "transitioned_from_stage": (
                transitioned_from_stage.value
                if transitioned_from_stage is not None
                else None
            ),
            "stage_status": "completed" if handled_stage.value in session.completed_stages else "active",
            "workflow_status": session.status.value,
            "assistant_message": output.assistant_message,
            "stage_payload": output.stage_payload,
            "student_task": output.student_task,
            "visualization": output.visualization,
            "assumptions": output.assumptions,
            "warnings": output.warnings,
            "request_rejected": output.stage_payload.get("request_rejected") is True,
            "knowledge_source": KNOWLEDGE.source_reference,
            "knowledge_sources": KNOWLEDGE.source_references,
            "completion_error": completion_error,
            "current_stage": session.current_stage.value,
            "next_stage": next_stage,
            "revision": session.revision,
        }
        return response

    def _lock_for_design(self, design_id: str) -> RLock:
        digest = hashlib.sha256(design_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(self._session_locks)
        return self._session_locks[index]

    def get_design(self, design_id: str, include_history: bool = False) -> dict[str, Any]:
        return self.store.get(design_id).to_dict(include_history=include_history)

    def delete_design(self, design_id: str) -> None:
        self.store.delete(design_id)

    def readiness_info(self) -> dict[str, Any]:
        self.store.healthcheck()
        return {**self.store_info(), "read_write_check": "ok"}

    def get_prompt_packet(self, design_id: str, user_message: str = "") -> dict[str, Any]:
        if not isinstance(user_message, str):
            raise ValueError("message must be a string")
        return build_prompt_packet(self.store.get(design_id), user_message)

    def verify_design_token(self, design_id: str, token: str) -> bool:
        if not token:
            return False
        session = self.store.get(design_id)
        candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return bool(session.access_token_hash) and hmac.compare_digest(
            session.access_token_hash,
            candidate,
        )

    def generator_info(self) -> dict[str, Any]:
        runtime_info = getattr(self.generator, "runtime_info", None)
        if callable(runtime_info):
            return runtime_info()
        return {
            "provider": "custom",
            "model": None,
            "fallback_enabled": False,
        }

    def store_info(self) -> dict[str, Any]:
        runtime_info = getattr(self.store, "runtime_info", None)
        if callable(runtime_info):
            return runtime_info()
        return {"provider": "custom", "durable": False}

    @staticmethod
    def knowledge_source() -> dict[str, Any]:
        return {
            "source": KNOWLEDGE.source_reference,
            "course_scope_source": KNOWLEDGE.source_reference,
            "supplemental_sources": KNOWLEDGE.supplemental_sources,
            "candidate_sources_not_used_for_retrieval": KNOWLEDGE.supplemental_data[
                "candidate_sources_not_used_for_retrieval"
            ],
            "policies": {
                "lecture_extraction": KNOWLEDGE.manifest["extraction_policy"],
                "multi_source": KNOWLEDGE.supplemental_data["policy"],
            },
        }

    @staticmethod
    def list_knowledge_concepts() -> list[dict[str, Any]]:
        return KNOWLEDGE.public_concepts()

    @staticmethod
    def list_supplemental_concepts() -> list[dict[str, Any]]:
        return KNOWLEDGE.public_supplemental_concepts()

    @staticmethod
    def list_knowledge_formulas() -> list[dict[str, Any]]:
        return KNOWLEDGE.public_formulas()

    @staticmethod
    def search_knowledge(query: str) -> dict[str, Any]:
        return KNOWLEDGE.search(query)

    @staticmethod
    def list_stages() -> list[dict[str, object]]:
        return public_stage_catalog()

    @staticmethod
    def _coerce_state(value: InteractionState | str | None) -> InteractionState | None:
        if value is None or isinstance(value, InteractionState):
            return value
        return InteractionState(value)

    def _request_from_dict(self, data: dict[str, Any]) -> TurnRequest:
        if not isinstance(data, dict):
            raise ValueError("turn request must be an object")
        message = data.get("message", "")
        if not isinstance(message, str):
            raise ValueError("message must be a string")
        complete_stage = data.get("complete_stage", False)
        if not isinstance(complete_stage, bool):
            raise ValueError("complete_stage must be a boolean")
        context_patch = data.get("context_patch", {})
        if not isinstance(context_patch, dict):
            raise ValueError("context_patch must be an object")
        if "interaction_state" in data and data["interaction_state"] is not None and not isinstance(data["interaction_state"], str):
            raise ValueError("interaction_state must be a string or null")
        raw_state = data.get("interaction_state")
        selected_option_id = data.get("selected_option_id")
        if selected_option_id is not None:
            if not isinstance(selected_option_id, str):
                raise ValueError("selected_option_id must be a string or null")
            selected_option_id = selected_option_id.strip()
            if not selected_option_id:
                selected_option_id = None
            elif len(selected_option_id) > 160:
                raise ValueError("selected_option_id is too long")
        return TurnRequest(
            message=message,
            complete_stage=complete_stage,
            context_patch=context_patch,
            interaction_state=self._coerce_state(raw_state),
            selected_option_id=selected_option_id,
        )

    @staticmethod
    def _record_student_decision(
        session: DesignSession,
        stage: Stage,
        message: str,
        selected_option_id: str | None = None,
        resolved_intent_name: str | None = None,
    ) -> None:
        normalized = message.strip()
        if (
            not normalized
            or resolved_intent_name
            not in {
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            }
        ):
            return
        decisions = session.design_context.setdefault("student_decisions", {})
        if not isinstance(decisions, dict):
            decisions = {}
            session.design_context["student_decisions"] = decisions
        stage_decisions = decisions.setdefault(stage.value, [])
        if not isinstance(stage_decisions, list):
            stage_decisions = []
            decisions[stage.value] = stage_decisions
        if not stage_decisions or (
            stage_decisions[-1].get("message") != normalized
            or stage_decisions[-1].get("selected_option_id") != selected_option_id
        ):
            stage_decisions.append(
                {
                    "message": normalized,
                    "selected_option_id": selected_option_id,
                    "before_revision": session.revision,
                }
            )
            del stage_decisions[:-8]

    @staticmethod
    def _hydrate_legacy_stage_one_thread(session: DesignSession) -> None:
        """Backfill idea-thread state for sessions created before this feature."""

        idea = session.design_context.setdefault("idea", {})
        if not isinstance(idea, dict):
            return
        if idea.get("course_scope_confirmed") is True:
            if not idea.get("brainstorm_phase"):
                existing_history = idea.get("focus_history", [])
                idea["brainstorm_phase"] = (
                    INTEREST_DESCRIPTION
                    if isinstance(existing_history, list) and len(existing_history) >= 2
                    else BREADTH_EXPLORATION
                )
            return
        focus_history: list[str] = []
        for item in session.history:
            if item.get("handled_stage") != Stage.IDEA_BRAINSTORMING.value:
                continue
            output = item.get("output")
            payload = output.get("stage_payload") if isinstance(output, dict) else None
            if not isinstance(payload, dict) or payload.get("input_category") != COURSE_CONTENT:
                continue
            resolved = item.get("resolved_intent")
            if isinstance(resolved, dict) and resolved.get("intent") in {
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
                UserIntent.ADVANCE_STAGE.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.RETURN_TO_PREVIOUS_POINT.value,
            }:
                continue
            candidate = str(
                payload.get("current_focus")
                or payload.get("current_idea_summary")
                or ""
            ).strip()
            if (
                candidate
                and (not focus_history or focus_history[-1] != candidate)
            ):
                focus_history.append(candidate)
        if not focus_history:
            return
        idea["topic_anchor"] = str(
            idea.get("topic_anchor") or idea.get("original") or focus_history[0]
        ).strip()
        idea["focus_history"] = focus_history[-8:]
        idea["current_focus"] = " → ".join(focus_history[-4:])
        idea["course_scope_confirmed"] = True
        idea["brainstorm_phase"] = (
            INTEREST_DESCRIPTION
            if len(focus_history) >= 2
            else BREADTH_EXPLORATION
        )
        idea["stage_one_turns"] = max(
            int(idea.get("stage_one_turns", 0)),
            len(focus_history),
        )

    @staticmethod
    def _commit_stage_one_thread(
        session: DesignSession,
        handled_stage: Stage,
        message: str,
        turn_context: dict[str, Any],
        output: Any,
    ) -> None:
        if (
            handled_stage is not Stage.IDEA_BRAINSTORMING
            or session.interaction_state is not InteractionState.GUIDED_DESIGN
            or output.stage_payload.get("request_rejected") is True
            or output.stage_payload.get("input_category") != COURSE_CONTENT
        ):
            return
        idea = session.design_context.setdefault("idea", {})
        if not isinstance(idea, dict):
            idea = {}
            session.design_context["idea"] = idea
        topic_anchor = str(turn_context.get("topic_anchor") or "").strip()
        current_focus = str(turn_context.get("current_focus") or "").strip()
        focus_history = turn_context.get("focus_history", [])
        if not current_focus and (
            not isinstance(focus_history, list) or not focus_history
        ):
            idea["course_scope_confirmed"] = True
            idea["brainstorm_phase"] = BREADTH_EXPLORATION
            if turn_context.get("control_turn") is not True:
                idea["stage_one_turns"] = int(idea.get("stage_one_turns", 0)) + 1
            return
        if not topic_anchor:
            topic_anchor = str(idea.get("topic_anchor") or message).strip()
        if not current_focus:
            current_focus = message.strip()
        if not isinstance(focus_history, list) or not focus_history:
            focus_history = [current_focus]
        idea.update(
            {
                "topic_anchor": topic_anchor,
                "current_focus": current_focus,
                "focus_history": deepcopy(focus_history[-8:]),
                "course_scope_confirmed": True,
                "brainstorm_phase": str(
                    turn_context.get("brainstorm_phase") or BREADTH_EXPLORATION
                ),
            }
        )
        for key in (
            "selected_focus",
            "core_phenomenon",
            "interest_description",
            "direction_summary",
        ):
            value = str(turn_context.get(key) or "").strip()
            if value:
                idea[key] = value
        for key in (
            "selected_scene_ids",
            "selected_course_relations",
            "refinement_notes",
        ):
            value = turn_context.get(key)
            if isinstance(value, list):
                idea[key] = deepcopy(value)
        output_comparisons = output.stage_payload.get("standard_comparisons")
        context_comparisons = turn_context.get("standard_comparisons")
        if isinstance(output_comparisons, list):
            idea["standard_comparisons"] = deepcopy(output_comparisons)
        elif isinstance(context_comparisons, list):
            idea["standard_comparisons"] = deepcopy(context_comparisons)
        idea["combination_intent"] = bool(turn_context.get("combination_intent"))
        outline_seed = output.stage_payload.get("experiment_outline_seed")
        if isinstance(outline_seed, dict) and outline_seed:
            session.design_context["experiment_outline_seed"] = deepcopy(outline_seed)
        if turn_context.get("control_turn") is not True:
            idea["stage_one_turns"] = int(idea.get("stage_one_turns", 0)) + 1

    @staticmethod
    def _validate_step_output(
        interaction_state: InteractionState,
        student_task: str | None,
    ) -> None:
        if interaction_state is InteractionState.GUIDED_DESIGN and student_task:
            question_count = student_task.count("？") + student_task.count("?")
            if question_count > 1:
                raise ValueError("Guided output may contain at most one student question")

    @staticmethod
    def _validate_completion(session: DesignSession, stage: Stage) -> None:
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            return
        if stage is Stage.IDEA_BRAINSTORMING:
            idea = session.design_context.get("idea", {})
            outline_seed = session.design_context.get("experiment_outline_seed")
            development = session.design_context.get("idea_development", {})
            required = (
                bool(idea.get("phenomenon")),
                bool(idea.get("main_direction")),
                idea.get("student_confirmed") is True,
                idea.get("course_scope_confirmed") is True,
                isinstance(outline_seed, dict) and bool(outline_seed),
                isinstance(development, dict)
                and development.get("complete") is True,
            ) if isinstance(idea, dict) else (False, False, False, False, False, False)
            if not all(required):
                raise StageCompletionError(
                    "实验想法完善尚未完成：需要先形成ECE329课内方向和大纲雏形，"
                    "并把完整性清单中的缺口补齐后再确认。"
                )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            stage_output = session.stage_outputs.get(stage.value, {})
            if not isinstance(stage_output.get("visualization"), dict):
                raise StageCompletionError(
                    "理论预测图还没有形成。请先确定要显示哪些量，以及怎样比较不同情形。"
                )
        required_fields = _GUIDED_COMPLETION_FIELDS.get(stage)
        if required_fields:
            stage_output = session.stage_outputs.get(stage.value, {})
            payload = stage_output.get("stage_payload", {})
            drafts = session.design_context.get("guided_stage_drafts", {})
            draft_payload = drafts.get(stage.value, {}) if isinstance(drafts, dict) else {}
            combined_payload = deepcopy(draft_payload) if isinstance(draft_payload, dict) else {}
            if isinstance(payload, dict):
                _deep_merge(combined_payload, payload)
            if not any(
                combined_payload.get(field) for field in required_fields
            ):
                raise StageCompletionError(
                    _GUIDED_COMPLETION_HINTS.get(
                        stage,
                        "这一部分还没有整理完整。请先补充一个与当前问题直接相关的设计判断。",
                    )
                )
        if stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:
            synthesis = session.design_context.get("synthesis", {})
            summary = synthesis.get("student_summary", "") if isinstance(synthesis, dict) else ""
            sections = (
                synthesis.get("student_summary_sections", [])
                if isinstance(synthesis, dict)
                else []
            )
            if (
                not isinstance(synthesis, dict)
                or synthesis.get("student_summary_complete") is not True
                or not isinstance(summary, str)
                or len(summary.strip()) < 20
                or not isinstance(sections, list)
                or len(sections) < 1
                or any(
                    not isinstance(section, str) or len(section.strip()) < 10
                    for section in sections
                )
            ):
                raise StageCompletionError(
                    "引导状态下需要先由你写出一段完整总结，再确认完成；"
                    "课程助手不会代写最终方案。"
                )

    @staticmethod
    def _advance(session: DesignSession, handled_stage: Stage) -> None:
        dialogue = session.model_context.get("dialogue_state", {})
        if isinstance(dialogue, dict):
            dialogue.pop("pending_action", None)
        if (
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and handled_stage is Stage.IDEA_BRAINSTORMING
            and has_idea_development(session)
            and session.design_context["idea_development"].get("complete") is True
        ):
            for stage in IDEA_DEVELOPMENT_STAGES:
                if stage.value not in session.completed_stages:
                    session.completed_stages.append(stage.value)
            session.current_stage_index = len(IDEA_DEVELOPMENT_STAGES)
            return
        if handled_stage.value not in session.completed_stages:
            session.completed_stages.append(handled_stage.value)
        if session.current_stage_index >= len(STAGE_SEQUENCE) - 1:
            session.status = WorkflowStatus.COMPLETE
            return
        session.current_stage_index += 1
