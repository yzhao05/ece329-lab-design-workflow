from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from copy import deepcopy
from threading import RLock
from typing import Any

from .generator import StageGenerator
from .guardrails import (
    BREADTH_EXPLORATION,
    COURSE_CONTENT,
    INTEREST_DESCRIPTION,
    UNREASONABLE_REQUEST,
    build_stage_one_turn_context,
    classify_stage_one_input,
    is_explicit_topic_switch,
    is_no_direction_request,
    is_progression_intent,
    is_stage_one_control_message,
    latest_stage_one_options,
    latest_stage_one_scenes,
    update_standard_comparison_decisions,
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


def _emvr_intent(text: str) -> bool | None:
    """Return an explicit EMVR opt-in/out intent; bare absence returns None."""

    normalized = text.casefold()
    if "emvr" not in normalized:
        return None
    negative_patterns = (
        r"(?:不|不要|不想|无需|不需要|拒绝|取消|退出|关闭|停止).{0,12}emvr",
        r"emvr.{0,12}(?:不要|不需要|取消|退出|关闭|停止)",
        r"(?:do\s+not|don't|without|avoid|disable|stop|leave|exit|not).{0,20}emvr",
        r"emvr.{0,20}(?:off|disabled|stop|leave|exit|not)",
    )
    if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in negative_patterns):
        return False
    positive_patterns = (
        r"(?:放入|使用|采用|切换|进入|启用|按照|通过|想用|要用).{0,16}emvr",
        r"emvr.{0,16}(?:模式下|工作流中|设计|完善|构建|完成)",
        r"(?:use|enable|enter|switch\s+to|with).{0,20}emvr",
    )
    if normalized.strip() == "emvr":
        return True
    if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in positive_patterns):
        return True
    return None


def _is_pure_stage_transition(text: str) -> bool:
    normalized = text.strip()
    if is_progression_intent(normalized):
        return True
    if normalized in {
        "继续",
        "继续完善下一阶段",
        "确认本阶段并进入下一阶段",
        "确认当前方向并进入下一阶段",
        "确认想法完善并进入变量与条件",
        "进入下一阶段",
        "完成本阶段",
    }:
        return True
    return re.fullmatch(
        r"确认.{0,30}(?:并)?(?:进入下一阶段|继续(?:下一阶段|小点\s*\d+))",
        normalized,
    ) is not None


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


class WorkflowEngine:
    def __init__(
        self,
        generator: StageGenerator | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.generator = generator or generator_from_environment()
        self.store = store or store_from_environment()
        self._session_locks = tuple(RLock() for _ in range(64))

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
        emvr_intent = _emvr_intent(idea)
        if requested_state is InteractionState.EMVR_DIRECT and emvr_intent is not True:
            raise ValueError("EMVR_DIRECT requires an explicit EMVR request in idea")
        if requested_state is InteractionState.GUIDED_DESIGN and emvr_intent is True:
            raise ValueError("interaction_state conflicts with the explicit EMVR request")
        state = (
            InteractionState.EMVR_DIRECT
            if emvr_intent is True
            and classify_stage_one_input(idea) != UNREASONABLE_REQUEST
            else InteractionState.GUIDED_DESIGN
        )
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
        input_kind = classify_stage_one_input(message)
        emvr_intent = _emvr_intent(message)
        if input_kind != UNREASONABLE_REQUEST:
            if request.interaction_state is not None:
                expected_intent = (
                    request.interaction_state is InteractionState.EMVR_DIRECT
                )
                if emvr_intent is not expected_intent:
                    raise ValueError(
                        "interaction_state requires a matching explicit mode request"
                    )
                session.interaction_state = request.interaction_state
            elif emvr_intent is True:
                session.interaction_state = InteractionState.EMVR_DIRECT
            elif emvr_intent is False:
                session.interaction_state = InteractionState.GUIDED_DESIGN

        idea_before_patch = session.design_context.get("idea", {})
        authoritative_course_scope = bool(
            isinstance(idea_before_patch, dict)
            and idea_before_patch.get("course_scope_confirmed") is True
        )
        _deep_merge(session.design_context, request.context_patch)
        if (
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage in IDEA_DEVELOPMENT_STAGES[1:]
        ):
            # Migrate pre-change sessions back into the single dynamic idea
            # development stage. Existing stage outputs remain available as
            # evidence, but no fixed substep order is preserved.
            session.current_stage_index = 0
        if (
            session.current_stage is Stage.IDEA_BRAINSTORMING
            and is_explicit_topic_switch(message)
        ):
            session.design_context.pop("idea_development", None)
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
        idea_development = session.design_context.get("idea_development", {})
        idea_development_complete = bool(
            session.current_stage is Stage.IDEA_BRAINSTORMING
            and isinstance(idea_development, dict)
            and idea_development.get("complete") is True
        )
        explicit_transition_intent = bool(
            _is_pure_stage_transition(message)
            or is_progression_intent(
                message,
                allow_confirmation=idea_development_complete,
            )
        )
        pre_transition_attempted = bool(
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and (request.complete_stage or explicit_transition_intent)
            and session.current_stage is not Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            and explicit_transition_intent
        )
        if pre_transition_attempted:
            previous_stage = session.current_stage
            if previous_stage is Stage.IDEA_BRAINSTORMING:
                idea = session.design_context.get("idea", {})
                if isinstance(idea, dict):
                    comparisons = idea.get("standard_comparisons", [])
                    if isinstance(comparisons, list):
                        idea["standard_comparisons"] = update_standard_comparison_decisions(
                            message,
                            comparisons,
                            control_turn=True,
                        )
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
                self._advance(session, previous_stage)
                transitioned_from_stage = previous_stage
            except StageCompletionError as exc:
                completion_error = str(exc)
        handled_stage = session.current_stage
        stage_one_control_turn = is_stage_one_control_message(message)
        dynamic_idea_turn = bool(
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and has_idea_development(session)
            and input_kind != UNREASONABLE_REQUEST
            and (not stage_one_control_turn or completion_error is not None)
            and not is_explicit_topic_switch(message)
        )
        if dynamic_idea_turn and not stage_one_control_turn:
            update_idea_development(session, message)
        turn_context: dict[str, Any] = {
            "selected_option_id": request.selected_option_id,
        }
        if dynamic_idea_turn:
            idea_context = session.design_context.get("idea", {})
            stage_one_context = build_stage_one_turn_context(
                message,
                options=latest_stage_one_options(session.history),
                scenes=latest_stage_one_scenes(session.history),
                idea_context=idea_context if isinstance(idea_context, dict) else {},
                selected_option_id=request.selected_option_id,
            )
            if isinstance(idea_context, dict):
                comparisons = stage_one_context.get("standard_comparisons")
                if isinstance(comparisons, list):
                    idea_context["standard_comparisons"] = deepcopy(comparisons)
            turn_context["idea_development"] = deepcopy(
                session.design_context.get("idea_development", {})
            )
        elif (
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
        ):
            self._hydrate_legacy_stage_one_thread(session)
            idea_context = session.design_context.get("idea", {})
            turn_context.update(
                build_stage_one_turn_context(
                    message,
                    options=latest_stage_one_options(session.history),
                    scenes=latest_stage_one_scenes(session.history),
                    idea_context=idea_context if isinstance(idea_context, dict) else {},
                    selected_option_id=request.selected_option_id,
                )
            )
        session.turn_context = turn_context
        if transitioned_from_stage is None:
            self._record_student_decision(
                session,
                handled_stage,
                message,
                request.selected_option_id,
            )
        definition = STAGES_BY_ID[handled_stage]
        if dynamic_idea_turn:
            output = build_gap_output(session, message)
            session.turn_context = {}
            if stage_one_control_turn:
                completion_error = None
        else:
            try:
                output = self.generator.generate(session, message)
            finally:
                session.turn_context = {}
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
                development = initialize_idea_development(session, outline_seed)
                decorate_outline_output(output, development)
            elif has_idea_development(session):
                output.stage_payload.setdefault(
                    "idea_development_status",
                    public_idea_development_status(
                        session.design_context["idea_development"]
                    ),
                )

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
            or (request.complete_stage and not pre_transition_attempted)
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

        self.store.save(session, expected_revision=expected_revision)
        next_stage = session.next_stage.value if session.next_stage else None
        response = {
            "design_id": session.design_id,
            "interaction_state": session.interaction_state.value,
            "handled_stage": handled_stage.value,
            "handled_stage_number": definition.number,
            "handled_stage_title": definition.title_zh,
            **stage_group_metadata(handled_stage),
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
    ) -> None:
        normalized = message.strip()
        control_messages = {
            "继续",
            "继续完善下一阶段",
            "确认本阶段并进入下一阶段",
            "确认当前方向并进入下一阶段",
            "进入下一阶段",
            "完成本阶段",
        }
        if (
            not normalized
            or normalized in control_messages
            or (
                stage is Stage.IDEA_BRAINSTORMING
                and is_stage_one_control_message(normalized)
            )
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
            candidate = str(
                payload.get("current_focus")
                or payload.get("current_idea_summary")
                or item.get("user_message")
                or ""
            ).strip()
            if (
                candidate
                and not is_no_direction_request(candidate)
                and not is_stage_one_control_message(candidate)
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
                    "阶段10尚未完成：需要先生成理论预测可视化窗口。"
                )
        required_fields = _GUIDED_COMPLETION_FIELDS.get(stage)
        if required_fields:
            stage_output = session.stage_outputs.get(stage.value, {})
            payload = stage_output.get("stage_payload", {})
            if not isinstance(payload, dict) or not any(
                payload.get(field) for field in required_fields
            ):
                raise StageCompletionError(
                    f"当前阶段尚未形成必要设计内容：需要至少包含{', '.join(required_fields)}之一。"
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
                or len(sections) < 2
                or any(
                    not isinstance(section, str) or len(section.strip()) < 10
                    for section in sections
                )
            ):
                raise StageCompletionError(
                    "引导状态下必须由学生分至少两次完成总结，每部分至少10个字符，"
                    "再确认完成；系统不会代写最终方案。"
                )

    @staticmethod
    def _advance(session: DesignSession, handled_stage: Stage) -> None:
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
