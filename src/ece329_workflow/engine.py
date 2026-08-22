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
from .guardrails import UNREASONABLE_REQUEST, classify_stage_one_input
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
from .stages import STAGES_BY_ID, public_stage_catalog
from .store import SessionStore, store_from_environment


_GUIDED_COMPLETION_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.COURSE_MAPPING_AND_DIRECTION: ("course_references", "candidate_course_directions"),
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

        _deep_merge(session.design_context, request.context_patch)
        expected_revision = session.revision
        handled_stage = session.current_stage
        session.turn_context = {"selected_option_id": request.selected_option_id}
        self._record_student_decision(
            session,
            handled_stage,
            message,
            request.selected_option_id,
        )
        definition = STAGES_BY_ID[handled_stage]
        try:
            output = self.generator.generate(session, message)
        finally:
            session.turn_context = {}
        self._validate_step_output(session.interaction_state, output.student_task)

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
                "output": output_dict,
            }
        )

        should_complete = (
            session.interaction_state is InteractionState.EMVR_DIRECT
            or request.complete_stage
        ) and output.stage_payload.get("request_rejected") is not True
        completion_error: str | None = None
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
        if not normalized or normalized in control_messages:
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
            required = (
                bool(idea.get("phenomenon")),
                bool(idea.get("main_direction")),
                idea.get("student_confirmed") is True,
            ) if isinstance(idea, dict) else (False, False, False)
            if not all(required):
                raise StageCompletionError(
                    "阶段1尚未完成：需要记录phenomenon、main_direction，并由学生设置student_confirmed=true。"
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
        if handled_stage.value not in session.completed_stages:
            session.completed_stages.append(handled_stage.value)
        if session.current_stage_index >= len(STAGE_SEQUENCE) - 1:
            session.status = WorkflowStatus.COMPLETE
            return
        session.current_stage_index += 1
