from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .dialogue_state import (
    ALL_INTENTS,
    CONFIRMATION_PENDING_TYPES,
    OPEN_QUESTION_PENDING_TYPES,
    degraded_context_intent,
    pending_question_answer_needs_review,
    pending_question_decision_missing,
    required_pending_facet_id,
    resolved_intent,
    serialize_intent_input,
    validate_resolved_intent,
)
from .dialogue_acts import DIALOGUE_ACT_TYPES
from .emvr_design import EMVR_THEORY_RELATIONS
from .design_state import seen_scene_signatures
from .generator import (
    ILLUSTRATIVE_EXTENSION_SCOPE,
    NO_DIRECTION_ACKNOWLEDGEMENT,
    RuleBasedStageGenerator,
    _emvr_context_text,
    _focused_emvr_formula_references,
    _emvr_latest_stage_input,
    _emvr_structured_requirements,
    _format_exploration_scenes,
    _format_experiment_outline_seed,
    _format_standard_comparison_status,
    build_exploration_scenes,
    build_experiment_outline_seed,
)
from .guardrails import (
    AMBIGUOUS,
    BREADTH_EXPLORATION,
    COURSE_CONTENT,
    DEPTH_EXPANSION,
    INTEREST_DESCRIPTION,
    OUT_OF_SCOPE,
    UNREASONABLE_REQUEST,
    classify_stage_one_input,
    latest_stage_one_options,
)
from .knowledge_base import KNOWLEDGE
from .models import DesignSession, InteractionState, Stage, StepOutput, WorkflowError
from .prompts import build_prompt_packet


RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4-mini"
ALLOWED_VISUALIZATION_TYPES = {
    "theoretical_prediction",
    "illustrative_synthetic_data",
}
GUIDED_REQUIRED_PAYLOAD_FIELDS: dict[Stage, tuple[str, ...]] = {
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
EMVR_REQUIRED_PAYLOAD_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.IDEA_BRAINSTORMING: (
        "original_idea",
        "target_phenomenon",
        "possible_vr_interactions",
    ),
    Stage.COURSE_MAPPING_AND_DIRECTION: (
        "course_references",
        "selected_direction",
        "vr_suitability",
    ),
    Stage.LEARNING_OBJECTIVES: (
        "conceptual_objective",
        "calculation_objective",
        "analysis_objective",
        "vr_interaction_objective",
    ),
    Stage.RESEARCH_QUESTION: (
        "main_research_question",
        "adjustable_quantity_in_vr",
        "observable_quantity_in_vr",
    ),
    Stage.THEORETICAL_FRAMEWORK: (
        "core_equations",
        "formula_support_map",
        "theory_selection_status",
        "simulation_inputs",
        "calculated_outputs",
        "visual_only_elements",
    ),
    Stage.HYPOTHESIS: ("research_hypothesis", "expected_trend", "limiting_cases"),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "unity_objects",
        "object_inventory",
        "interactions",
        "physics_layer",
        "visualization_layer",
        "measurement_interface",
    ),
    Stage.VARIABLES_AND_CONDITIONS: (
        "independent_variable",
        "dependent_variable",
        "controlled_variables",
        "reference_condition",
    ),
    Stage.CONCEPTUAL_PROCEDURE: ("procedure_steps", "comparison_logic"),
    Stage.EXPECTED_DATA_VISUALIZATION: ("trend_annotation", "unity_update_event"),
    Stage.RESULT_INTERPRETATION: (
        "if_prediction_supported",
        "if_opposite_trend",
        "if_no_clear_change",
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: (
        "conceptual_feasibility",
        "limitations",
        "teaching_value",
        "vr_added_value",
    ),
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: (
        "proposal_status",
        "proposal_sections",
        "final_design",
        "builder_pack_handoff",
    ),
}


def _validated_model_standard_comparisons(
    raw_comparisons: Any,
    allowed_concept_ids: set[str],
) -> list[dict[str, Any]]:
    """Validate a model-proposed basic case bundle against retrieved course scope."""

    if raw_comparisons in (None, []):
        return []
    if not isinstance(raw_comparisons, list) or len(raw_comparisons) > 1:
        raise ModelOutputError("Stage 1 may propose at most one basic case bundle")
    if not allowed_concept_ids:
        raise ModelOutputError("A basic case bundle requires retrieved course grounding")

    validated: list[dict[str, Any]] = []
    for comparison in raw_comparisons:
        if not isinstance(comparison, dict):
            raise ModelOutputError("A basic case bundle must be an object")
        comparison_id = comparison.get("comparison_id")
        cases = comparison.get("recommended_cases", comparison.get("cases"))
        reason = comparison.get("reason")
        concept_ids = comparison.get("course_concept_ids")
        if (
            not isinstance(comparison_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,79}", comparison_id)
        ):
            raise ModelOutputError("A basic case bundle needs a stable comparison_id")
        if (
            not isinstance(cases, list)
            or not 2 <= len(cases) <= 4
            or any(
                not isinstance(case, str)
                or not case.strip()
                or len(case.strip()) > 40
                for case in cases
            )
        ):
            raise ModelOutputError("A basic case bundle needs two to four concise cases")
        normalized_cases = [str(case).strip() for case in cases]
        if len(set(normalized_cases)) != len(normalized_cases):
            raise ModelOutputError("A basic case bundle cannot contain duplicate cases")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 180:
            raise ModelOutputError("A basic case bundle needs a concise course-based reason")
        if (
            not isinstance(concept_ids, list)
            or not concept_ids
            or any(
                not isinstance(concept_id, str)
                or concept_id not in allowed_concept_ids
                for concept_id in concept_ids
            )
        ):
            raise ModelOutputError(
                "A basic case bundle must cite retrieved course concept IDs"
            )
        validated.append(
            {
                "comparison_id": comparison_id,
                "cases": normalized_cases,
                "recommended_cases": normalized_cases,
                "case_aliases": {
                    str(case): [
                        str(alias).strip()
                        for alias in aliases
                        if isinstance(alias, str) and alias.strip()
                    ][:5]
                    for case, aliases in (
                        comparison.get("case_aliases", {}).items()
                        if isinstance(comparison.get("case_aliases"), dict)
                        else []
                    )
                    if str(case) in normalized_cases and isinstance(aliases, list)
                },
                "role": "PROPOSED_BASELINE_COMPARISON",
                "adoption_status": "PENDING",
                "reason": reason.strip(),
                "course_concept_ids": list(dict.fromkeys(concept_ids)),
                "proposal_source": "COURSE_GROUNDED_MODEL",
            }
        )
    return validated
LOGGER = logging.getLogger(__name__)


class ModelServiceError(WorkflowError):
    """A safe-to-report model transport or response error."""


class ModelConfigurationError(ModelServiceError):
    pass


class ModelOutputError(ModelServiceError):
    pass


class ModelHTTPError(ModelServiceError):
    def __init__(self, status_code: int, error_code: str | None = None) -> None:
        super().__init__(f"OpenAI Responses API returned HTTP {status_code}")
        self.status_code = status_code
        self.error_code = error_code


class ResponsesTransport(Protocol):
    def create(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class OpenAIResponsesHTTPTransport:
    """Minimal Responses API transport with no third-party dependency."""

    def __init__(self, api_key: str, timeout_seconds: float = 45.0) -> None:
        if not api_key.strip():
            raise ModelConfigurationError("OPENAI_API_KEY is required")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            RESPONSES_ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ece329-lab-design-workflow/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            error_code: str | None = None
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                error_object = error_payload.get("error", {})
                if isinstance(error_object, dict) and isinstance(
                    error_object.get("code"), str
                ):
                    error_code = error_object["code"]
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
            raise ModelHTTPError(exc.code, error_code) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise ModelServiceError("OpenAI Responses API is temporarily unavailable") from exc
        except json.JSONDecodeError as exc:
            raise ModelOutputError("OpenAI Responses API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ModelOutputError("OpenAI Responses API returned an unexpected payload")
        return result


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "assistant_message": {"type": "string"},
            "stage_payload_json": {
                "type": "string",
                "description": "A JSON-serialized object containing only the current stage payload.",
            },
            "student_task": {"type": ["string", "null"]},
            "visualization_json": {
                "type": ["string", "null"],
                "description": "A JSON-serialized visualization object for Stage 10, otherwise null.",
            },
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "assistant_message",
            "stage_payload_json",
            "student_task",
            "visualization_json",
            "assumptions",
            "warnings",
        ],
        "additionalProperties": False,
    }


def _intent_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": list(ALL_INTENTS)},
            "target": {"type": ["string", "null"]},
            "resolved_value_json": {"type": ["string", "null"]},
            "semantic_updates_json": {"type": ["string", "null"]},
            "dialogue_acts_json": {"type": ["string", "null"]},
            "advance_requested": {"type": "boolean"},
            "preserve_current_design": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "intent",
            "target",
            "resolved_value_json",
            "semantic_updates_json",
            "dialogue_acts_json",
            "advance_requested",
            "preserve_current_design",
            "confidence",
        ],
        "additionalProperties": False,
    }


def _compact_intent_response_schema() -> dict[str, Any]:
    """A small recovery contract for turns that break the rich JSON envelope.

    The main resolver carries optional compatibility summaries and detailed
    quality metadata.  If a model truncates or corrupts that envelope, this
    schema asks only for atomic dialogue acts, which are the sole write path
    anyway.  Keeping content textual makes the contract substantially easier
    to complete while still requiring an explicit canonical target.
    """

    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(DIALOGUE_ACT_TYPES)},
                        "target": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": ["MERGE", "REPLACE", "CLEAR", "EXECUTE"],
                        },
                        "content": {"type": "string"},
                        "semantic_key": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "type",
                        "target",
                        "operation",
                        "content",
                        "semantic_key",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
                "maxItems": 12,
            }
        },
        "required": ["actions"],
        "additionalProperties": False,
    }


def _parse_compact_intent_response(
    response: dict[str, Any],
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    try:
        payload = json.loads(_extract_output_text(response))
    except json.JSONDecodeError as exc:
        raise ModelOutputError("Compact intent model output was invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
        raise ModelOutputError("Compact intent output must contain an action array")
    acts: list[dict[str, Any]] = []
    for raw_act in payload["actions"]:
        if not isinstance(raw_act, dict):
            continue
        act = deepcopy(raw_act)
        if not str(act.get("content") or "").strip() and act.get("type") in {
            "CONTROL",
            "REQUEST_REFERENCE",
            "REQUEST_SUMMARY",
            "REQUEST_QUALITY_REVIEW",
        }:
            act["content"] = None
        acts.append(act)
    executable = [act for act in acts if act.get("type") != "UNRESOLVED"]
    primary = executable[0] if executable else (acts[0] if acts else {})
    primary_type = str(primary.get("type") or "UNRESOLVED")
    primary_target = str(primary.get("target") or "")
    intent_by_type = {
        "ANSWER_PENDING_QUESTION": "ANSWER_CURRENT_QUESTION",
        "MODIFY_DESIGN_FIELD": "MODIFY_PREVIOUS_PROPOSAL",
        "MODIFY_STAGE_FIELD": "MODIFY_PREVIOUS_PROPOSAL",
        "MODIFY_COMPARISON": "MODIFY_PREVIOUS_PROPOSAL",
        "ASK_COURSE_QUESTION": "ASK_COURSE_QUESTION",
        "REQUEST_REFERENCE": "REQUEST_MORE_EXAMPLES",
        "REQUEST_SUMMARY": "REQUEST_CURRENT_DESIGN_SUMMARY",
        "REQUEST_QUALITY_REVIEW": "REQUEST_DESIGN_REVIEW",
        "COMPARE_OPTIONS": "COMPARE_DESIGN_OPTIONS",
        "VERSION_CONTROL": "MANAGE_DESIGN_VERSION",
        "CORRECT_ASSISTANT": "PROVIDE_FEEDBACK",
        "NEW_TOPIC": "NEW_TOPIC",
        "UNRESOLVED": "UNCLEAR",
    }
    intent = intent_by_type.get(primary_type, "UNCLEAR")
    if primary_type == "CONTROL":
        intent = {
            "ACCEPT": "ACCEPT_PREVIOUS_PROPOSAL",
            "REJECT": "REJECT_PREVIOUS_PROPOSAL",
            "ADVANCE": "ADVANCE_STAGE",
            "RETURN": "RETURN_TO_PREVIOUS_POINT",
            "SET_GUIDED_MODE": "SET_INTERACTION_STATE",
            "SET_EMVR_MODE": "SET_INTERACTION_STATE",
            "ACCEPT_QUALITY_REVIEW": "ADVANCE_STAGE",
        }.get(primary_target, "UNCLEAR")
    resolved_value = primary.get("content")
    raw = {
        "intent": intent,
        "target": primary_target or None,
        "resolved_value_json": (
            json.dumps(resolved_value, ensure_ascii=False)
            if resolved_value not in (None, "")
            else None
        ),
        "semantic_updates_json": None,
        "dialogue_acts_json": json.dumps(acts, ensure_ascii=False),
        "dialogue_acts": acts,
        "advance_requested": bool(
            any(
                act.get("type") == "CONTROL"
                and act.get("target") in {"ADVANCE", "ACCEPT_QUALITY_REVIEW"}
                for act in acts
            )
        ),
        "preserve_current_design": True,
        "confidence": max(
            (float(act.get("confidence") or 0.0) for act in acts),
            default=0.0,
        ),
    }
    return raw, resolved_value, {}


def _parse_intent_response(
    response: dict[str, Any],
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    try:
        raw = json.loads(_extract_output_text(response))
    except json.JSONDecodeError as exc:
        raise ModelOutputError("Intent model output was invalid") from exc
    if not isinstance(raw, dict):
        raise ModelOutputError("Intent model output must be an object")
    # Decode the authoritative action list first. The other encoded fields are
    # compatibility summaries for older integrations; a malformed summary must
    # never discard valid field-level actions and send the whole turn into the
    # generic clarification fallback.
    encoded_acts = raw.get("dialogue_acts_json")
    if isinstance(encoded_acts, str):
        try:
            parsed_acts = json.loads(encoded_acts)
        except json.JSONDecodeError as exc:
            raise ModelOutputError("Intent dialogue_acts_json was invalid") from exc
        if not isinstance(parsed_acts, list):
            raise ModelOutputError("Intent dialogue_acts_json must encode an array")
        raw["dialogue_acts"] = parsed_acts
    executable_actions = bool(
        isinstance(raw.get("dialogue_acts"), list)
        and any(
            isinstance(item, dict)
            and str(item.get("type") or "").upper() in DIALOGUE_ACT_TYPES
            and str(item.get("type") or "").upper() != "UNRESOLVED"
            for item in raw["dialogue_acts"]
        )
    )

    resolved_value = None
    encoded_value = raw.get("resolved_value_json")
    if isinstance(encoded_value, str):
        try:
            resolved_value = json.loads(encoded_value)
        except json.JSONDecodeError as exc:
            if not executable_actions:
                raise ModelOutputError("Intent resolved_value_json was invalid") from exc

    semantic_updates: dict[str, Any] = {}
    encoded_updates = raw.get("semantic_updates_json")
    if isinstance(encoded_updates, str):
        try:
            parsed_updates = json.loads(encoded_updates)
        except json.JSONDecodeError as exc:
            if not executable_actions:
                raise ModelOutputError("Intent semantic_updates_json was invalid") from exc
        else:
            if not isinstance(parsed_updates, dict):
                if not executable_actions:
                    raise ModelOutputError(
                        "Intent semantic_updates_json must encode an object"
                    )
            else:
                semantic_updates = parsed_updates
    return raw, resolved_value, semantic_updates


def _extract_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    text_parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise ModelOutputError("The model declined to generate this stage")
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                text_parts.append(text)
    if not text_parts:
        raise ModelOutputError("The model response contained no output text")
    return "".join(text_parts)


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ModelOutputError(f"Model field {field_name} must be a string array")
    return value


def _json_object(value: Any, field_name: str, allow_null: bool = False) -> dict[str, Any] | None:
    if value is None and allow_null:
        return None
    if not isinstance(value, str):
        raise ModelOutputError(f"Model field {field_name} must be a JSON string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"Model field {field_name} contains invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelOutputError(f"Model field {field_name} must encode a JSON object")
    return parsed


def _contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in forbidden or _contains_forbidden_key(child, forbidden):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item, forbidden) for item in value)
    return False


def _validate_stage_constraints(session: DesignSession, output: StepOutput) -> None:
    stage = session.current_stage
    student_visible_prose = " ".join(
        [output.assistant_message, output.student_task or "", *output.warnings]
    )
    if re.search(r"阶段\s*\d+", student_visible_prose, flags=re.IGNORECASE):
        raise ModelOutputError(
            "Student-facing text exposed an internal numbered stage reference"
        )
    if stage is not Stage.IDEA_BRAINSTORMING:
        question_count = student_visible_prose.count("？") + student_visible_prose.count("?")
        if question_count > 1:
            raise ModelOutputError(
                "A student-facing turn may ask only one next question"
            )
    if session.interaction_state is InteractionState.EMVR_DIRECT:
        visible_text = json.dumps(
            {
                "assistant_message": output.assistant_message,
                "stage_payload": output.stage_payload,
                "student_task": output.student_task,
            },
            ensure_ascii=False,
        )
        if re.search(r"(?:由|来自)阶段\s*\d+|阶段\s*\d+\s*(?:确定|补充|处理)", visible_text):
            raise ModelOutputError("EMVR output exposed an internal stage placeholder")
    visual = output.visualization
    if session.interaction_state is InteractionState.GUIDED_DESIGN:
        required_fields = GUIDED_REQUIRED_PAYLOAD_FIELDS.get(stage)
        if required_fields and not any(
            output.stage_payload.get(field) for field in required_fields
        ):
            raise ModelOutputError(
                f"Guided stage {stage.value} is missing its required design artifact"
            )
        if stage not in {
            Stage.IDEA_BRAINSTORMING,
            Stage.COURSE_MAPPING_AND_DIRECTION,
            Stage.LEARNING_OBJECTIVES,
            Stage.RESEARCH_QUESTION,
            Stage.THEORETICAL_FRAMEWORK,
            Stage.HYPOTHESIS,
            Stage.CONCEPTUAL_OR_VR_SETUP,
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        }:
            readiness = output.stage_payload.get("stage_readiness")
            if (
                not isinstance(readiness, dict)
                or not isinstance(readiness.get("ready_for_confirmation"), bool)
                or not isinstance(readiness.get("remaining_gaps"), list)
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in readiness.get("remaining_gaps", [])
                )
            ):
                raise ModelOutputError(
                    "A later guided stage must return structured stage_readiness"
                )
    else:
        required_fields = EMVR_REQUIRED_PAYLOAD_FIELDS.get(stage, ())
        empty_allowed = (
            {"core_equations", "formula_support_map"}
            if stage is Stage.THEORETICAL_FRAMEWORK
            else set()
        )
        missing_fields = [
            field
            for field in required_fields
            if field not in output.stage_payload
            or (
                field not in empty_allowed
                and not output.stage_payload.get(field)
            )
        ]
        if missing_fields:
            raise ModelOutputError(
                f"EMVR stage {stage.value} is missing required fields: "
                + ", ".join(missing_fields)
            )
        latest_stage_input = _emvr_latest_stage_input(session, stage)
        resolved = session.turn_context.get("resolved_intent", {})
        resolved_intent_name = (
            str(resolved.get("intent") or "")
            if isinstance(resolved, dict)
            else ""
        )
        semantic_updates = (
            resolved.get("semantic_updates", {})
            if isinstance(resolved, dict)
            else {}
        )
        emvr_update = (
            semantic_updates.get("emvr_design_update", {})
            if isinstance(semantic_updates, dict)
            else {}
        )
        field_updates = (
            emvr_update.get("field_updates", [])
            if isinstance(emvr_update, dict)
            else []
        )
        field_updates = field_updates if isinstance(field_updates, list) else []
        if (
            latest_stage_input
            and resolved_intent_name
            in {
                "ANSWER_CURRENT_QUESTION",
                "MODIFY_PREVIOUS_PROPOSAL",
                "NEW_TOPIC",
            }
            and not field_updates
            and latest_stage_input
            not in json.dumps(output.stage_payload, ensure_ascii=False)
        ):
            raise ModelOutputError(
                "EMVR output did not preserve the student's latest stage input"
            )
        payload_fields_by_design_field = {
            "direction_summary": ("selected_direction",),
            "research_question": ("main_research_question",),
            "changed_quantities": (
                "adjustable_quantity_in_vr", "independent_variable", "simulation_inputs"
            ),
            "observed_quantities": (
                "observable_quantity_in_vr", "dependent_variable", "calculated_outputs"
            ),
            "hypothesis": ("research_hypothesis", "expected_trend"),
            "procedure_steps": ("procedure_steps",),
        }
        for edit in field_updates:
            if not isinstance(edit, dict) or edit.get("operation") == "CLEAR":
                continue
            field_id = str(edit.get("field_id") or "")
            relevant_keys = payload_fields_by_design_field.get(field_id, ())
            present_keys = [key for key in relevant_keys if key in output.stage_payload]
            if not present_keys:
                continue
            expected = edit.get("value")
            actual_text = json.dumps(
                [output.stage_payload[key] for key in present_keys],
                ensure_ascii=False,
            )
            expected_values = expected if isinstance(expected, list) else [expected]
            if any(
                isinstance(value, str)
                and value.strip()
                and value.strip() not in actual_text
                for value in expected_values
            ):
                raise ModelOutputError(
                    f"EMVR field edit was not applied to its target: {field_id}"
                )
    visible_text = student_visible_prose.casefold()
    forbidden_student_facing_terms = (
        "knowledge_retrieval",
        "知识检索",
        "知识目录",
        "stage_payload",
        "结构化字段",
        "状态机",
        "待办状态",
        "提交状态",
        "pending_action",
        "resolved_intent",
        "dialogue_acts",
        "task_plan",
        "design_state",
        "concept_id",
        "supplemental_concept_id",
        "pdf",
        "讲义第",
        "内部阶段",
        "系统指令",
        "提示词",
        "内部指令",
        "模型服务",
        "api",
        "前端",
        "后端",
        "服务器",
        "部署",
        "源代码",
    )
    if any(term in visible_text for term in forbidden_student_facing_terms):
        raise ModelOutputError(
            "Student-facing text contains internal implementation terminology"
        )
    if (
        session.interaction_state is InteractionState.GUIDED_DESIGN
        and stage is Stage.COURSE_MAPPING_AND_DIRECTION
    ):
        if not output.stage_payload.get("course_references") or not output.stage_payload.get(
            "primary_course_anchor"
        ):
            raise ModelOutputError(
                "Guided course mapping must display one grounded primary anchor"
            )
        if re.search(
            r"(?:请选择|你希望把哪|选哪|哪一个课程方向|从.{0,20}中选)",
            visible_text,
        ):
            raise ModelOutputError(
                "Guided course mapping cannot ask the student to choose the direction again"
            )
    if stage is Stage.EXPECTED_DATA_VISUALIZATION:
        if visual is None:
            raise ModelOutputError("Stage 10 requires a visualization object")
        if visual.get("data_type") not in ALLOWED_VISUALIZATION_TYPES:
            raise ModelOutputError("Stage 10 visualization has an invalid data_type")
        if visual.get("measured") is not False:
            raise ModelOutputError("Stage 10 visualization must set measured=false")
        if not isinstance(visual.get("x_axis"), dict) or not isinstance(
            visual.get("y_axis"), dict
        ):
            raise ModelOutputError("Stage 10 visualization requires both axes")
        series = visual.get("series")
        if not isinstance(series, list) or not series:
            raise ModelOutputError("Stage 10 visualization requires at least one series")
        if not isinstance(visual.get("disclaimer"), str) or not visual[
            "disclaimer"
        ].strip():
            raise ModelOutputError("Stage 10 visualization requires a disclaimer")
        for item in series:
            if not isinstance(item, dict):
                raise ModelOutputError("Stage 10 series entries must be objects")
            points = item.get("points", [])
            if not isinstance(points, list) or len(points) > 500:
                raise ModelOutputError("Stage 10 series points are invalid")
    elif visual is not None:
        raise ModelOutputError("Only Stage 10 may return a visualization object")

    if (
        session.interaction_state is InteractionState.GUIDED_DESIGN
        and stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
        and output.stage_payload.get("final_proposal_generated") is not False
    ):
        raise ModelOutputError("Guided Stage 13 cannot generate a final proposal")

    if (
        session.interaction_state is InteractionState.EMVR_DIRECT
        and stage is Stage.CONCEPTUAL_OR_VR_SETUP
        and _contains_forbidden_key(
            output.stage_payload,
            {
                "scene",
                "vr_scene",
                "scene_definition",
                "accessibility",
                "comfort",
                "comfort_and_accessibility",
            },
        )
    ):
        raise ModelOutputError("EMVR Stage 7 contains a forbidden design field")
    if (
        session.interaction_state is InteractionState.EMVR_DIRECT
        and stage is Stage.CONCEPTUAL_OR_VR_SETUP
    ):
        inventory = output.stage_payload.get("object_inventory")
        required_object_fields = {
            "object_name",
            "category",
            "purpose",
            "student_interaction",
            "physics_or_data_state",
            "visual_feedback",
            "required",
        }
        if not isinstance(inventory, list) or len(inventory) < 5:
            raise ModelOutputError("EMVR Stage 7 requires a complete object inventory")
        for item in inventory:
            if (
                not isinstance(item, dict)
                or any(key not in item for key in required_object_fields)
                or not isinstance(item.get("required"), bool)
            ):
                raise ModelOutputError(
                    "Every EMVR object inventory item must contain the complete object contract"
                )
    if (
        session.interaction_state is InteractionState.EMVR_DIRECT
        and stage is Stage.THEORETICAL_FRAMEWORK
    ):
        allowed_formulas = _focused_emvr_formula_references(
                _emvr_structured_requirements(session).get(
                    "theory_relation_ids", []
                ),
            )
        allowed_formula_ids = {
            str(item.get("id") or "") for item in allowed_formulas
        }
        allowed_relations = {
            str(item.get("id") or ""): str(
                item.get("supports_relation_id") or ""
            )
            for item in allowed_formulas
        }
        returned_formula_ids = {
            str(item.get("id") or "")
            for item in output.stage_payload.get("core_equations", [])
            if isinstance(item, dict)
        }
        if returned_formula_ids - allowed_formula_ids:
            raise ModelOutputError(
                "EMVR theory output included formulas outside the focused experiment context"
            )
        support_map = output.stage_payload.get("formula_support_map", [])
        mapped_formula_ids = {
            str(item.get("formula_id") or "")
            for item in support_map
            if isinstance(item, dict)
            and str(item.get("supports_design_content") or "").strip()
        }
        if returned_formula_ids != mapped_formula_ids:
            raise ModelOutputError(
                "Every EMVR formula must state which current design content it supports"
            )
        if any(
            not isinstance(item, dict)
            or allowed_relations.get(str(item.get("formula_id") or ""))
            != str(item.get("relation_id") or "")
            for item in support_map
        ):
            raise ModelOutputError(
                "EMVR formula support mapping used the wrong physical relation"
            )


def _validate_lecture_grounding(
    session: DesignSession,
    output: StepOutput,
    prompt_packet: dict[str, Any],
) -> None:
    visible_text = " ".join(
        [output.assistant_message, output.student_task or "", *output.warnings]
    ).casefold()
    retrieval = prompt_packet["context"]["knowledge_retrieval"]
    allowed_concepts = {
        item["concept_id"]
        for item in retrieval["concepts"] + retrieval["brainstorm_options"]
        if isinstance(item, dict) and isinstance(item.get("concept_id"), str)
    }
    allowed_formulas = {
        item["id"]
        for item in retrieval["formulas"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    allowed_supplemental = {
        item["supplemental_concept_id"]
        for item in retrieval["supplemental_concepts"] + retrieval["brainstorm_options"]
        if isinstance(item, dict)
        and isinstance(item.get("supplemental_concept_id"), str)
    }
    concept_catalog = {
        item["id"]: item for item in KNOWLEDGE.lectures
    }
    overview_pages = KNOWLEDGE.concept_data["overview"]["pages"]
    for block in KNOWLEDGE.concept_data["overview"]["course_blocks"]:
        concept_catalog[block["id"]] = {"pages": overview_pages}
    formula_catalog = {item["id"]: item for item in KNOWLEDGE.formulas}
    supplemental_catalog = {
        item["supplemental_concept_id"]: item
        for item in KNOWLEDGE.supplemental_concepts
    }
    retrieved_brainstorm_options = [
        item for item in retrieval["brainstorm_options"] if isinstance(item, dict)
    ]
    cited_concepts: set[str] = set()
    cited_formulas: set[str] = set()
    cited_supplemental: set[str] = set()

    def validate(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, list):
            for item in value:
                validate(item, path)
            return
        if not isinstance(value, dict):
            return

        concept_id = value.get("concept_id")
        if concept_id is not None:
            if not isinstance(concept_id, str) or concept_id not in allowed_concepts:
                raise ModelOutputError(f"Unknown or unretrieved concept_id: {concept_id}")
            cited_concepts.add(concept_id)
            pages = value.get("source_pages", value.get("pages"))
            if pages != concept_catalog[concept_id]["pages"]:
                raise ModelOutputError(f"Concept {concept_id} must use its catalog PDF pages")

        supplemental_id = value.get("supplemental_concept_id")
        if supplemental_id is not None:
            if (
                not isinstance(supplemental_id, str)
                or supplemental_id not in allowed_supplemental
                or supplemental_id not in supplemental_catalog
            ):
                raise ModelOutputError(
                    f"Unknown or unretrieved supplemental_concept_id: {supplemental_id}"
                )
            cited_supplemental.add(supplemental_id)
            expected_scope = supplemental_catalog[supplemental_id][
                "course_scope_concept_ids"
            ]
            if value.get("course_scope_concept_ids") != expected_scope:
                raise ModelOutputError(
                    f"Supplemental concept {supplemental_id} must preserve course scope ids"
                )

        formula_id = value.get("formula_id")
        path_is_formula = any("formula" in part or "equation" in part for part in path)
        if formula_id is None and path_is_formula and isinstance(value.get("id"), str):
            formula_id = value["id"]
        if formula_id is not None:
            if not isinstance(formula_id, str) or formula_id not in allowed_formulas:
                raise ModelOutputError(f"Unknown or unretrieved formula id: {formula_id}")
            cited_formulas.add(formula_id)
            canonical = formula_catalog[formula_id]
            if value.get("pages") != canonical["pages"]:
                raise ModelOutputError(f"Formula {formula_id} must use its catalog PDF pages")
            if "expression" in value and value["expression"] != canonical["expression"]:
                raise ModelOutputError(f"Formula {formula_id} expression differs from the lecture catalog")

        for key, child in value.items():
            validate(child, (*path, str(key).casefold()))

    validate(output.stage_payload, ("stage_payload",))
    if output.visualization is not None:
        validate(output.visualization, ("visualization",))

    if (
        session.current_stage is Stage.IDEA_BRAINSTORMING
        and session.interaction_state is InteractionState.GUIDED_DESIGN
    ):
        input_category = output.stage_payload.get("input_category")
        allowed_input_categories = {
            COURSE_CONTENT,
            OUT_OF_SCOPE,
            UNREASONABLE_REQUEST,
        }
        if input_category not in allowed_input_categories:
            raise ModelOutputError(
                "Stage 1 must classify the request into one supported input category"
            )
        preclassified_category = prompt_packet["context"].get(
            "stage_one_preclassification"
        )
        if (
            preclassified_category == UNREASONABLE_REQUEST
            and input_category != UNREASONABLE_REQUEST
        ):
            raise ModelOutputError(
                "A locally detected unreasonable request cannot be downgraded"
            )
        if (
            preclassified_category == COURSE_CONTENT
            and input_category != COURSE_CONTENT
        ):
            raise ModelOutputError(
                "A course-content request must remain in the course-content category"
            )
        if prompt_packet["context"].get("stage_one_no_direction") is True:
            if input_category != COURSE_CONTENT:
                raise ModelOutputError(
                    "A no-direction request must be treated as course-content brainstorming"
                )
            if re.search(
                r"(?:不属于|不在|超出).{0,16}ECE329|ECE329.{0,16}(?:不属于|不在|超出)",
                visible_text,
                re.IGNORECASE,
            ):
                raise ModelOutputError(
                    "A no-direction reply cannot be presented as outside the course scope"
                )
        if preclassified_category not in {
            COURSE_CONTENT,
            UNREASONABLE_REQUEST,
            AMBIGUOUS,
        }:
            raise ModelOutputError("Stage 1 preclassification was invalid")
        phase = output.stage_payload.get("brainstorm_phase")
        allowed_phases = {
            BREADTH_EXPLORATION,
            INTEREST_DESCRIPTION,
            DEPTH_EXPANSION,
        }
        if phase not in allowed_phases:
            raise ModelOutputError("Stage 1 returned an invalid brainstorm phase")
        alternatives = output.stage_payload.get("alternative_ideas")
        exploration_scenes = output.stage_payload.get("exploration_scenes")
        if output.stage_payload.get("brainstorm_activity") != "RELATIONSHIP_DISCOVERY":
            raise ModelOutputError(
                "Stage 1 must use RELATIONSHIP_DISCOVERY before later-stage refinement"
            )
        if not isinstance(alternatives, list):
            raise ModelOutputError("Stage 1 alternative_ideas must be an array")
        if not isinstance(exploration_scenes, list):
            raise ModelOutputError("Stage 1 exploration_scenes must be an array")
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                raise ModelOutputError("Every Stage 1 alternative must be an object")
            if alternative not in retrieved_brainstorm_options:
                raise ModelOutputError(
                    "Every Stage 1 alternative must exactly reuse a retrieved brainstorm option"
                )
        if phase == BREADTH_EXPLORATION:
            if len(alternatives) != 3:
                raise ModelOutputError(
                    "Breadth exploration must contain exactly three sampled alternatives"
                )
            if len(exploration_scenes) != len(alternatives):
                raise ModelOutputError(
                    "Breadth exploration requires one scene per grounded alternative"
                )
            seen_scene_ids: set[str] = set()
            seen_physical_frames: set[str] = set()
            scene_string_fields = {
                "scene_id",
                "label",
                "title",
                "physical_picture",
                "thinking_prompt",
                "combination_seed",
                "illustrative_extension",
                "extension_scope",
            }
            expected_labels = ["图景 A", "图景 B", "图景 C"]
            for index, scene in enumerate(exploration_scenes):
                if not isinstance(scene, dict):
                    raise ModelOutputError("Every Stage 1 exploration scene must be an object")
                if any(
                    not isinstance(scene.get(field), str)
                    or not str(scene.get(field)).strip()
                    for field in scene_string_fields
                ):
                    raise ModelOutputError(
                        "Every Stage 1 exploration scene requires complete descriptive fields"
                    )
                scene_id = str(scene["scene_id"]).strip()
                if scene_id in seen_scene_ids:
                    raise ModelOutputError("Stage 1 exploration scene ids must be unique")
                seen_scene_ids.add(scene_id)
                physical_signature = "|".join(
                    " ".join(str(scene.get(field) or "").split()).casefold()
                    for field in ("title", "physical_picture", "thinking_prompt")
                )
                if physical_signature in seen_physical_frames:
                    raise ModelOutputError(
                        "Stage 1 exploration scenes must use distinct visible physical frames"
                    )
                seen_physical_frames.add(physical_signature)
                if str(scene.get("label") or "").strip() != expected_labels[index]:
                    raise ModelOutputError(
                        "Displayed exploration scenes must be relabeled A, B, and C"
                    )
                if scene.get("course_anchor") != alternatives[index]:
                    raise ModelOutputError(
                        "Every Stage 1 scene must exactly bind its matching course alternative"
                    )
                if scene.get("extension_scope") != ILLUSTRATIVE_EXTENSION_SCOPE:
                    raise ModelOutputError(
                        "Illustrative scene extensions must be separated from course evidence"
                    )
                if len(str(scene["physical_picture"]).strip()) < 35:
                    raise ModelOutputError(
                        "Stage 1 scenes must contain a concrete, vivid physical picture"
                    )
            visible_message = output.assistant_message
            if re.search(r"ECE329-S\d{3}", visible_message, re.IGNORECASE):
                raise ModelOutputError(
                    "Internal exploration scene numbers cannot appear in student-facing text"
                )
            if "图景" not in visible_message or not any(
                phrase in visible_message
                for phrase in ("组合", "改造", "交换", "叠加")
            ):
                raise ModelOutputError(
                    "Breadth replies must present combinable physical scenes"
                )
            if not any(
                phrase in visible_message
                for phrase in ("启发性延伸", "启发性设想")
            ):
                raise ModelOutputError(
                    "Breadth replies must label illustrative extensions"
                )
        elif exploration_scenes:
            raise ModelOutputError(
                "Interest description and depth expansion cannot return exploration scenes"
            )
        if input_category in {OUT_OF_SCOPE, UNREASONABLE_REQUEST}:
            if phase != BREADTH_EXPLORATION:
                raise ModelOutputError(
                    "Redirected Stage 1 responses must return to breadth exploration"
                )
            if len(alternatives) != 3:
                raise ModelOutputError(
                    "Redirected Stage 1 responses must contain exactly three course examples"
                )
            visible_message = output.assistant_message.casefold()
            if input_category == OUT_OF_SCOPE and not (
                "不属于ece329" in visible_message
                or "超出ece329" in visible_message
                or "不在ece329" in visible_message
            ):
                raise ModelOutputError(
                    "Out-of-scope responses must explicitly state the ECE329 course boundary"
                )
            if input_category == UNREASONABLE_REQUEST and not any(
                phrase in visible_message
                for phrase in ("不能执行", "不会执行", "无法执行", "拒绝")
            ):
                raise ModelOutputError(
                    "Unreasonable requests must be explicitly refused"
                )
        elif phase == BREADTH_EXPLORATION:
            if not alternatives or not (cited_concepts or cited_supplemental):
                raise ModelOutputError(
                    "Breadth exploration must return catalog-grounded alternatives"
                )
        elif alternatives:
            raise ModelOutputError(
                "Interest description and depth expansion cannot return choice options"
            )
        if phase != BREADTH_EXPLORATION:
            visible_scene_labels = re.findall(
                r"图景\s*(?:[A-CＡ-Ｃ]|[一二三123])\s*[｜|]",
                f"{output.assistant_message}\n{output.student_task or ''}",
                re.IGNORECASE,
            )
            if len(visible_scene_labels) >= 2:
                raise ModelOutputError(
                    "A selected or described Stage 1 direction cannot display a new scene list"
                )
        if input_category == COURSE_CONTENT and phase == DEPTH_EXPANSION:
            deepening_connections = output.stage_payload.get(
                "deepening_connections"
            )
            if not isinstance(deepening_connections, list) or not deepening_connections:
                raise ModelOutputError(
                    "Depth expansion requires catalog-grounded deepening connections"
                )
            for connection in deepening_connections:
                if (
                    not isinstance(connection, dict)
                    or connection not in retrieved_brainstorm_options
                ):
                    raise ModelOutputError(
                        "Depth connections must exactly reuse retrieved brainstorm options"
                    )
        stage_one_thread = prompt_packet["context"].get("stage_one_thread", {})
        if isinstance(stage_one_thread, dict):
            if (
                stage_one_thread.get("direction_locked") is True
                and phase == BREADTH_EXPLORATION
            ):
                raise ModelOutputError(
                    "A locked Stage 1 direction cannot return to breadth exploration"
                )
            selected_relations = stage_one_thread.get("selected_course_relations", [])
            preserved_relation_catalog = [
                *retrieved_brainstorm_options,
                *latest_stage_one_options(session.history),
            ]
            if not isinstance(selected_relations, list) or any(
                not isinstance(item, dict) for item in selected_relations
            ):
                raise ModelOutputError(
                    "Stage 1 selected_course_relations must be an array of objects"
            )
            for relation in selected_relations:
                if relation not in preserved_relation_catalog:
                    raise ModelOutputError(
                        "Every preserved Stage 1 relation must remain grounded"
                    )
            if phase == INTEREST_DESCRIPTION and len(selected_relations) > 1:
                combined_visible = " ".join(
                    [output.assistant_message, output.student_task or ""]
                )
                if re.search(r"更想.{0,40}还是|选择.{0,30}(?:一个|其中)", combined_visible):
                    raise ModelOutputError(
                        "A combined Stage 1 direction cannot be turned back into a choice"
                    )
                if output.student_task != "请用自己的话描述这组关系共同要解释的核心现象。":
                    raise ModelOutputError(
                        "A combined Stage 1 direction should request one shared core phenomenon"
                    )
            if stage_one_thread.get("ready_for_next_stage") is True:
                visible = output.assistant_message.strip()
                if len(visible) > 650:
                    raise ModelOutputError(
                        "A ready Stage 1 direction must be summarized concisely"
                    )
                if "？" in visible or "?" in visible:
                    raise ModelOutputError(
                        "A ready Stage 1 direction cannot introduce another content question"
                    )
                forced_choice_patterns = (
                    r"更想.{0,30}还是",
                    r"先看.{0,30}还是",
                    r"告诉我.{0,30}(?:还是|或者)",
                    r"可以继续补",
                    r"如果愿意.{0,40}(?:下一|继续|补充)",
                    r"我们继续沿着已经形成的",
                )
                if any(
                    re.search(pattern, visible)
                    for pattern in forced_choice_patterns
                ):
                    raise ModelOutputError(
                        "A ready Stage 1 direction cannot create another artificial choice"
                    )
                for relation in selected_relations:
                    direction = str(
                        relation.get("direction") or relation.get("focus") or ""
                    ).strip()
                    if direction and direction not in visible:
                        raise ModelOutputError(
                            "A ready Stage 1 summary forgot a combined course relation"
                        )
                standard_comparisons = output.stage_payload.get(
                    "standard_comparisons",
                    [],
                )
                if isinstance(standard_comparisons, list):
                    for comparison in standard_comparisons:
                        if not isinstance(comparison, dict):
                            raise ModelOutputError(
                                "Stage 1 standard comparisons must be objects"
                            )
                        status = str(
                            comparison.get("adoption_status") or "PENDING"
                        ).upper()
                        if status not in {
                            "PENDING",
                            "ACCEPTED",
                            "MODIFIED",
                            "REJECTED",
                        }:
                            raise ModelOutputError(
                                "Stage 1 standard comparison status is invalid"
                            )
                        visible_cases = (
                            comparison.get("recommended_cases", comparison.get("cases", []))
                            if status == "PENDING"
                            else comparison.get("cases", [])
                        )
                        for case in visible_cases:
                            if str(case).strip() not in visible:
                                raise ModelOutputError(
                                    "A ready Stage 1 summary omitted a standard comparison case"
                                )
                        if status == "PENDING" and re.search(
                            r"自动.{0,4}纳入",
                            visible,
                        ):
                            raise ModelOutputError(
                                "A pending baseline cannot be described as automatically adopted"
                            )
                        if status == "PENDING" and re.search(
                            r"已采纳|已接受|已经纳入",
                            visible,
                        ):
                            raise ModelOutputError(
                                "A pending baseline cannot be described as accepted"
                            )
                        if status != "PENDING" and re.search(
                            r"待采纳|确认.{0,8}(?:表示|即).{0,4}采纳",
                            visible,
                        ):
                            raise ModelOutputError(
                                "A decided baseline cannot be presented as pending"
                            )
                expected_task = (
                    "请检查这个大纲雏形是否准确；若有关键遗漏，请直接补充。"
                )
                if output.student_task != expected_task:
                    raise ModelOutputError(
                        "A ready Stage 1 turn must ask only for confirmation or correction"
                    )
        resolved_reference = prompt_packet["context"].get(
            "resolved_stage_one_reference"
        )
        if isinstance(resolved_reference, dict):
            selected_direction = str(resolved_reference.get("direction", "")).strip()
            if (
                selected_direction
                and selected_direction not in output.assistant_message
            ):
                raise ModelOutputError(
                    "A contextual option selection must acknowledge the selected direction"
                )
    if session.current_stage is Stage.COURSE_MAPPING_AND_DIRECTION:
        references = output.stage_payload.get("course_references")
        if allowed_concepts and (
            not isinstance(references, list)
            or not references
            or any(
                not isinstance(item, dict)
                or item not in retrieval["concepts"]
                for item in references
            )
        ):
            raise ModelOutputError(
                "Stage 2 must return only retrieved concepts in course_references"
            )
        if allowed_concepts and not cited_concepts:
            raise ModelOutputError("Stage 2 must cite a retrieved lecture concept")
    if session.current_stage is Stage.THEORETICAL_FRAMEWORK:
        equations = output.stage_payload.get(
            "core_equations",
            output.stage_payload.get("lecture_formula_candidates"),
        )
        if allowed_formulas and (
            not isinstance(equations, list)
            or not equations
            or any(
                not isinstance(item, dict)
                or item not in retrieval["formulas"]
                for item in equations
            )
        ):
            raise ModelOutputError(
                "Stage 5 must return only retrieved formulas with stable formula ids"
            )
        if allowed_formulas and not cited_formulas:
            raise ModelOutputError("Stage 5 must cite a retrieved lecture formula")


def _step_output_from_response(
    session: DesignSession,
    response: dict[str, Any],
    packet: dict[str, Any],
) -> StepOutput:
    try:
        raw_output = json.loads(_extract_output_text(response))
    except json.JSONDecodeError as exc:
        raise ModelOutputError("The model output was not valid JSON") from exc
    if not isinstance(raw_output, dict):
        raise ModelOutputError("The model output must be a JSON object")

    assistant_message = raw_output.get("assistant_message")
    stage_payload = _json_object(
        raw_output.get("stage_payload_json"),
        "stage_payload_json",
    )
    student_task = raw_output.get("student_task")
    visualization = _json_object(
        raw_output.get("visualization_json"),
        "visualization_json",
        allow_null=True,
    )
    if not isinstance(assistant_message, str) or not assistant_message.strip():
        raise ModelOutputError("Model field assistant_message must be a non-empty string")
    if student_task is not None and not isinstance(student_task, str):
        raise ModelOutputError("Model field student_task must be a string or null")

    output = StepOutput(
        assistant_message=assistant_message.strip(),
        stage_payload=stage_payload,
        student_task=student_task,
        visualization=visualization,
        assumptions=_string_list(raw_output.get("assumptions"), "assumptions"),
        warnings=_string_list(raw_output.get("warnings"), "warnings"),
    )
    if (
        session.current_stage is Stage.IDEA_BRAINSTORMING
        and session.interaction_state is InteractionState.GUIDED_DESIGN
        and output.stage_payload.get("brainstorm_phase") == BREADTH_EXPLORATION
    ):
        alternatives = output.stage_payload.get("alternative_ideas")
        if (
            isinstance(alternatives, list)
            and len(alternatives) == 3
            and all(isinstance(item, dict) for item in alternatives)
        ):
            # Scene wording is rendered from the catalog in one place.  This
            # prevents two distinct option IDs from being shown through the
            # same physical frame and keeps the visible A/B/C labels stable.
            scenes = build_exploration_scenes(
                alternatives,
                excluded_scene_signatures=seen_scene_signatures(session),
            )
            output.stage_payload["exploration_scenes"] = scenes
            category = output.stage_payload.get("input_category")
            if category == UNREASONABLE_REQUEST:
                introduction = (
                    "这个请求会改变课程助手的用途，或让它执行与ECE329实验设计无关的操作，"
                    "我不能执行。我们把讨论带回ECE329课上学习的物理关系。"
                )
            elif category == OUT_OF_SCOPE:
                introduction = (
                    "你提到的主题不属于ECE329课程内容，不能直接作为这门课实验设计的核心。"
                    "下面用三个课内图景帮你换一个方向。"
                )
            else:
                introduction = (
                    "这个想法可以从几种不同的ECE329物理关系展开。我们先不急着定变量、"
                    "公式或流程，而是看看哪一种画面最能触发你的兴趣。"
                )
            output.assistant_message = (
                f"{introduction}\n\n"
                "下面不是标准答案，三个图景都可以继续改造、交换或组合：\n\n"
                f"{_format_exploration_scenes(scenes)}"
            )
            output.student_task = (
                "哪幅图景最接近你想研究的现象？你也可以组合其中两个，或直接说出自己的改法。"
            )
    if (
        session.current_stage is Stage.IDEA_BRAINSTORMING
        and session.interaction_state is InteractionState.GUIDED_DESIGN
    ):
        stage_one_thread = packet["context"].get("stage_one_thread", {})
        if isinstance(stage_one_thread, dict):
            context_comparisons = stage_one_thread.get("standard_comparisons", [])
            context_comparisons = (
                [
                    deepcopy(comparison)
                    for comparison in context_comparisons
                    if isinstance(comparison, dict)
                ]
                if isinstance(context_comparisons, list)
                else []
            )
            model_comparisons = output.stage_payload.get("standard_comparisons", [])
            ready_for_next_stage = stage_one_thread.get("ready_for_next_stage") is True
            if context_comparisons:
                standard_comparisons = context_comparisons
            elif ready_for_next_stage:
                retrieval_concepts = packet["context"]["knowledge_retrieval"].get(
                    "concepts",
                    [],
                )
                allowed_concept_ids = {
                    str(concept.get("concept_id") or "").strip()
                    for concept in retrieval_concepts
                    if isinstance(concept, dict)
                    and str(concept.get("concept_id") or "").strip()
                }
                standard_comparisons = _validated_model_standard_comparisons(
                    model_comparisons,
                    allowed_concept_ids,
                )
            else:
                if model_comparisons not in (None, []):
                    raise ModelOutputError(
                        "Basic case bundles may be proposed only after Stage 1 is ready"
                    )
                standard_comparisons = []
            for key in (
                "topic_anchor",
                "current_focus",
                "focus_history",
                "contextual_continuation",
                "brainstorm_phase",
                "selected_focus",
                "selected_scene_ids",
                "selected_course_relations",
                "combination_intent",
                "core_phenomenon",
                "refinement_notes",
                "direction_summary",
                "interest_description",
                "direction_locked",
                "stage_one_direction_detail",
                "ready_for_next_stage",
                "resolved_stage_one_reference",
            ):
                output.stage_payload[key] = deepcopy(stage_one_thread.get(key))
            output.stage_payload["standard_comparisons"] = standard_comparisons
            phase = stage_one_thread.get("brainstorm_phase")
            if phase in {INTEREST_DESCRIPTION, DEPTH_EXPANSION}:
                output.stage_payload["alternative_ideas"] = []
                output.stage_payload["exploration_scenes"] = []
            if phase == DEPTH_EXPANSION:
                output.stage_payload["deepening_connections"] = deepcopy(
                    packet["context"]["knowledge_retrieval"]["brainstorm_options"]
                )
            if packet["context"].get("stage_one_no_direction") is True:
                if not output.assistant_message.startswith(
                    NO_DIRECTION_ACKNOWLEDGEMENT
                ):
                    output.assistant_message = (
                        f"{NO_DIRECTION_ACKNOWLEDGEMENT}\n\n"
                        f"{output.assistant_message}"
                    )
            selected_relations = stage_one_thread.get("selected_course_relations", [])
            relation_directions = [
                str(item.get("direction") or item.get("focus") or "").strip()
                for item in selected_relations
                if isinstance(item, dict)
                and str(item.get("direction") or item.get("focus") or "").strip()
            ] if isinstance(selected_relations, list) else []
            missing_relations = [
                direction
                for direction in relation_directions
                if direction not in output.assistant_message
            ]
            if missing_relations:
                label = "组合关系保留" if len(relation_directions) > 1 else "课程关系"
                output.assistant_message = (
                    f"{label}：{'；'.join(relation_directions)}。\n\n"
                    f"{output.assistant_message}"
                )
            standard_comparisons = output.stage_payload.get("standard_comparisons", [])
            comparison_summary = _format_standard_comparison_status(
                [
                    comparison
                    for comparison in standard_comparisons
                    if isinstance(comparison, dict)
                ]
                if isinstance(standard_comparisons, list)
                else []
            )
            if ready_for_next_stage and comparison_summary:
                if not output.assistant_message.startswith(comparison_summary):
                    output.assistant_message = (
                        f"{comparison_summary}\n\n{output.assistant_message}"
                    )
            if ready_for_next_stage:
                if re.search(
                    r"你(?:想|希望|更倾向|更想).{0,28}(?:哪|还是)|"
                    r"请(?:选择|选).{0,20}(?:一个|方向)|"
                    r"先看.{0,24}还是",
                    output.assistant_message,
                ):
                    raise ModelOutputError(
                        "A ready Stage 1 draft cannot ask the student to choose another direction"
                    )
                outline_seed = build_experiment_outline_seed(
                    phenomenon=str(
                        stage_one_thread.get("core_phenomenon")
                        or stage_one_thread.get("interest_description")
                        or stage_one_thread.get("direction_summary")
                        or ""
                    ),
                    selected_course_relations=(
                        selected_relations if isinstance(selected_relations, list) else []
                    ),
                    standard_comparisons=(
                        standard_comparisons if isinstance(standard_comparisons, list) else []
                    ),
                    observation_focus=[
                        *(
                            stage_one_thread.get("refinement_notes", [])
                            if isinstance(stage_one_thread.get("refinement_notes"), list)
                            else []
                        ),
                        str(stage_one_thread.get("interest_description") or ""),
                    ],
                )
                output.stage_payload["experiment_outline_seed"] = outline_seed
                comparison_prefix = f"{comparison_summary}\n\n" if comparison_summary else ""
                output.assistant_message = (
                    f"{comparison_prefix}{_format_experiment_outline_seed(outline_seed)}\n\n"
                    "接下来会一直沿着这个方向完善，不会再让你重新选题。"
                )
                output.student_task = (
                    "请检查这个大纲雏形是否准确；若有关键遗漏，请直接补充。"
                )
            elif phase == INTEREST_DESCRIPTION and len(relation_directions) > 1:
                output.student_task = "请用自己的话描述这组关系共同要解释的核心现象。"
            elif (
                stage_one_thread.get("contextual_continuation") is True
                and not relation_directions
            ):
                selected_focus = str(
                    stage_one_thread.get("selected_focus") or ""
                ).strip()
                if selected_focus and selected_focus not in output.assistant_message:
                    output.assistant_message = (
                        f"当前仍围绕“{selected_focus}”讨论。\n\n"
                        f"{output.assistant_message}"
                    )
    resolved_turn = packet["context"].get("resolved_intent")
    pending_action = packet["context"].get("pending_action")
    latest_user_message = str(packet.get("latest_user_message") or "").strip()
    if (
        session.interaction_state is InteractionState.GUIDED_DESIGN
        and session.current_stage is not Stage.IDEA_BRAINSTORMING
        and len(latest_user_message) >= 36
        and latest_user_message in output.assistant_message
    ):
        raise ModelOutputError(
            "A guided reply must respond to the student's content without quoting the full input"
        )
    if (
        session.interaction_state is InteractionState.GUIDED_DESIGN
        and session.current_stage is not Stage.IDEA_BRAINSTORMING
    ):
        visible_reply = f"{output.assistant_message}\n{output.student_task or ''}"
        if len(re.findall(r"[？?]", visible_reply)) > 1:
            raise ModelOutputError(
                "A guided reply outside Stage 1 may ask only one student-facing question"
            )
    if isinstance(resolved_turn, dict) and str(resolved_turn.get("intent")) in {
        "ANSWER_CURRENT_QUESTION",
        "ACCEPT_PREVIOUS_PROPOSAL",
        "MODIFY_PREVIOUS_PROPOSAL",
        "REJECT_PREVIOUS_PROPOSAL",
    }:
        previous_task = str(
            pending_action.get("question") if isinstance(pending_action, dict) else ""
        ).strip()
        visible_reply = f"{output.assistant_message} {output.student_task or ''}"
        if (
            re.search(r"还需要先听听|请先描述你期待看到|请先用自己的话描述", visible_reply)
            or (previous_task and previous_task in visible_reply)
        ):
            raise ModelOutputError(
                "A contextual guided reply cannot reset or repeat the previous question"
            )
    _validate_stage_constraints(session, output)
    _validate_lecture_grounding(session, output, packet)
    return output


@dataclass(slots=True)
class OpenAIStageGenerator:
    transport: ResponsesTransport
    model: str = DEFAULT_MODEL
    reasoning_effort: str = "medium"
    intent_max_output_tokens: int = 1400
    max_output_tokens: int = 2400
    stage_one_max_output_tokens: int = 3200
    final_max_output_tokens: int = 5000
    stateful: bool = False
    repair_attempts: int = 1
    _api_successes: int = field(default=0, init=False, repr=False)
    _api_failures: int = field(default=0, init=False, repr=False)
    _chain_resets: int = field(default=0, init=False, repr=False)
    _output_rejections: int = field(default=0, init=False, repr=False)
    _repair_successes: int = field(default=0, init=False, repr=False)
    _intent_api_successes: int = field(default=0, init=False, repr=False)
    _intent_api_failures: int = field(default=0, init=False, repr=False)
    _intent_repair_successes: int = field(default=0, init=False, repr=False)
    _metrics_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate direct construction as strictly as environment loading.

        Production normally creates this class through
        ``generator_from_environment``.  Tests, scripts, and integrations may
        instantiate it directly, however; without this check an invalid
        reasoning value or zero token budget would only fail after an API call.
        Keeping the validation here makes every construction path obey the
        same request contract.
        """

        self.reasoning_effort = _reasoning_effort(self.reasoning_effort)
        self.intent_max_output_tokens = _positive_int(
            self.intent_max_output_tokens,
            "OPENAI_INTENT_MAX_OUTPUT_TOKENS",
        )
        self.max_output_tokens = _positive_int(
            self.max_output_tokens,
            "OPENAI_MAX_OUTPUT_TOKENS",
        )
        self.stage_one_max_output_tokens = _positive_int(
            self.stage_one_max_output_tokens,
            "OPENAI_STAGE_ONE_MAX_OUTPUT_TOKENS",
        )
        self.final_max_output_tokens = _positive_int(
            self.final_max_output_tokens,
            "OPENAI_FINAL_MAX_OUTPUT_TOKENS",
        )

    def _recover_compact_intent(
        self,
        intent_input: str,
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        """Retry a rejected rich intent envelope with action-only output."""

        compact_payload = {
            "model": self.model,
            "instructions": (
                "只把学生本轮消息拆成原子对话动作，不回答学生。读取输入中的previous_question、"
                "pending_action.answer_fields和carried_context。回答开放问题时使用"
                "ANSWER_PENDING_QUESTION，并把target设为实际规范化字段；若answer_fields有多项，"
                "按学生表达拆成多个动作。修改已保存内容使用MODIFY_DESIGN_FIELD或"
                "MODIFY_STAGE_FIELD。课程提问、参考请求、总结请求、纠错和控制动作必须分别列出。"
                "不能把整条混合消息塞进一个字段；能确定的动作照常返回，剩余片段才用UNRESOLVED。"
            ),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": intent_input}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ece329_compact_dialogue_acts",
                    "schema": _compact_intent_response_schema(),
                    "strict": True,
                }
            },
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": max(self.intent_max_output_tokens, 1800),
            "store": False,
        }
        response = self.transport.create(compact_payload)
        return _parse_compact_intent_response(response)

    def resolve_intent(
        self,
        session: DesignSession,
        user_message: str,
        pending_action: dict[str, Any] | None,
        carried_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify one turn before the deterministic workflow state machine runs."""

        intent_input = serialize_intent_input(
            session,
            user_message,
            pending_action,
            carried_context,
        )
        emvr_relation_catalog = {
            relation_id: relation["label"]
            for relation_id, relation in EMVR_THEORY_RELATIONS.items()
        }
        payload = {
            "model": self.model,
            "instructions": (
                "你只负责把学生本轮消息拆成一个或多个可执行对话动作，不回答课程问题，也不决定阶段编号。"
                "必须结合previous_question、pending_action、carried_context和user_message。"
                "dialogue_acts_json必须是JSON序列化数组；它是主要输出。不要假设学生只会按"
                "previous_question预设的格式回答。同一句可以同时包含：回答当前问题、补充或修改"
                "其他设计字段、修改基础比较、提出课程问题、索取参考或总结、纠正助手理解，以及"
                "继续或返回等控制动作。必须逐项拆开，不能把整句复制进一个动作，也不能因为一项"
                "不清楚就丢弃其他清楚项。每个动作包含type、target、operation、content、confidence，"
                "设计内容动作还应包含semantic_key。semantic_key是简短、稳定、与措辞无关的物理含义"
                "标识；同一字段中语义等价的说法必须返回相同semantic_key。每个设计动作只表达一个"
                "原子含义，同一句涉及多个字段或同一字段中的多个独立要点时必须拆成多个动作；"
                "type只能为ANSWER_PENDING_QUESTION、MODIFY_DESIGN_FIELD、MODIFY_STAGE_FIELD、"
                "MODIFY_COMPARISON、ASK_COURSE_QUESTION、REQUEST_REFERENCE、REQUEST_SUMMARY、"
                "REQUEST_QUALITY_REVIEW、COMPARE_OPTIONS、VERSION_CONTROL、CORRECT_ASSISTANT、"
                "CONTROL、NEW_TOPIC或UNRESOLVED。"
                "ANSWER_PENDING_QUESTION只保存真正回答当前待办的内容。pending_action.answer_fields"
                "列出这个问题允许写入的规范化字段：只有一个字段时target直接使用该字段；有多个字段时，"
                "把学生长回答按含义拆成多个字段级动作，不要把整段绑定到公开阶段ID；"
                "MODIFY_DESIGN_FIELD的target只能是research_object、course_relationship、"
                "learning_objective、research_question、theoretical_framework、hypothesis、"
                "expected_phenomenon、conceptual_structure；MODIFY_STAGE_FIELD的target只能是"
                "independent_variable、observations、controlled_conditions、procedure_steps、"
                "visualization_plan、result_interpretation、limitations、unity_objects、interactions。"
                "operation只能为MERGE、REPLACE或CLEAR。课程疑问必须单独作为ASK_COURSE_QUESTION，"
                "不能保存成实验观点；对遗漏、曲解或重复的反馈必须作为CORRECT_ASSISTANT，除非同句"
                "还明确给出字段新值，否则不能据此覆盖设计。若能从user_message、上一轮真实回复和"
                "当前状态确定助手究竟错改或漏改了哪个字段，CORRECT_ASSISTANT.content应返回对象，"
                "包含error_type、explanation、affected_fields，以及经过核对的design_updates或"
                "stage_field_updates；只修复有证据的字段，不能用道歉代替修正。无法理解的片段单独放UNRESOLVED，"
                "其他动作仍正常返回。CONTROL的target只能为ACCEPT、REJECT、ADVANCE、RETURN、"
                "SET_GUIDED_MODE、SET_EMVR_MODE或ACCEPT_QUALITY_REVIEW。VERSION_CONTROL.content"
                "必须包含action，取值VIEW_RECENT、UNDO_LAST、RESTORE或COMPARE；可包含version_id、"
                "other_version_id与fields。学生要求分析设计是否合理时使用REQUEST_QUALITY_REVIEW；"
                "提出两个以上方案并要求比较时使用COMPARE_OPTIONS，content保留各方案的实质内容。"
                "每个动作的content只含该动作的实质内容。"
                "外层intent、target、resolved_value_json和semantic_updates_json继续返回，只作为旧接口"
                "兼容摘要和非写入型分析；程序不会用这些外层字段写入实验设计。所有设计字段、阶段字段、"
                "基础比较和纠错修改都必须有对应dialogue_acts_json动作。外层intent应概括最主要动作，"
                "但不得用它压掉dialogue_acts_json中的并行动作。"
                "可选意图只有ANSWER_CURRENT_QUESTION、ACCEPT_PREVIOUS_PROPOSAL、"
                "MODIFY_PREVIOUS_PROPOSAL、REJECT_PREVIOUS_PROPOSAL、ADVANCE_STAGE、"
                "REQUEST_MORE_EXAMPLES、REQUEST_CURRENT_DESIGN_SUMMARY、"
                "ASK_COURSE_QUESTION、PROVIDE_FEEDBACK、RETURN_TO_PREVIOUS_POINT、NEW_TOPIC、"
                "SET_INTERACTION_STATE、REQUEST_DESIGN_REVIEW、COMPARE_DESIGN_OPTIONS、"
                "MANAGE_DESIGN_VERSION、UNCLEAR。"
                "凡是必须依赖上一轮才能理解的表达都要走这一套语义判断，包括指代某个或多个选项、"
                "组合前述图景、表示暂无方向、回答或撤回想法完整性要点、接受或局部修改建议、"
                "继续推进、索取其他例子、返回前项和更换主题；不能用孤立词语代替上下文判断。"
                "类似‘沿用刚才安排’‘两个都留下’‘不用改，接着做’应根据上一项待办解析，"
                "不能按孤立关键词判断。只有语义确实不足时才返回UNCLEAR。"
                "carried_context.topic_lock.locked=true时，学生后续补充默认属于当前研究主题；"
                "除非学生明确表示放弃当前实验并重新开始，否则不得返回NEW_TOPIC，也不得清空已经"
                "确认的研究问题、比较对象、观察现象或保留内容。current_edit_target表示当前正在"
                "讨论的设计项，但不限制同一句对其他字段的明确修改。"
                "学生明确表示暂时不知道并要求你给出一个可能、参考、示例或你的判断时，应返回"
                "REQUEST_MORE_EXAMPLES；这不是学生对当前问题给出的设计答案，不得返回"
                "ANSWER_CURRENT_QUESTION，也不得把请求文字作为resolved_value。"
                "pending_action.type为CONFIRM_STAGE_OR_MODIFY时，‘是’‘确认’‘合适’‘就这样’等"
                "语义同意当前阶段安排的回复必须返回ACCEPT_PREVIOUS_PROPOSAL，不得当作新的"
                "阶段内容；程序会据此进入下一阶段。"
                "no_direction表示学生当前没有可供继续完善的实验方向，或不知道从哪里开始；"
                "必须依据整句话的含义判断，暂时想不到、脑中空白、希望先看课程例子等只是"
                "可能表达而不是固定口令。若学生已经提出明确课程主题，只是在询问如何继续，"
                "则no_direction必须为false。被判为no_direction的输入仍属于课程想法探索，"
                "不得判成课外主题。"
                "resolved_value_json必须是JSON序列化后的值；没有值时为null。若一条消息同时包含"
                "‘保留/沿用/删改’等会话操作和实质设计内容，在ANSWER_CURRENT_QUESTION或"
                "MODIFY_PREVIOUS_PROPOSAL下只返回实质设计内容，不要把会话操作写入该字段。"
                "学生要求列出、检查、确认当前已经保存的研究对象、课程关系、学习目标、研究问题、"
                "假设、预期现象、概念结构或基础比较时，返回REQUEST_CURRENT_DESIGN_SUMMARY。这是只读请求，"
                "semantic_updates_json为null，resolved_value_json返回学生要求查看的field ID数组；"
                "基础比较的field ID为baseline_comparisons；若要求全部则返回null。不得把它当成"
                "回答、修改或阶段推进。若同一句还明确修改了字段，则必须同时返回"
                "REQUEST_SUMMARY动作和对应的字段修改动作；此时不能因为有总结请求就把"
                "semantic_updates_json清空。索取参考与保留、继续、返回等控制动作同理，"
                "都必须与同句中的实质更新并列保留。"
                "总结请求必须以carried_context中的规范化设计状态为依据，不能重新从历史消息自由"
                "归纳。如果学生同时指出已保存字段混入了会话说明、遗漏某项或含有重复内容，先返回"
                "可执行的CORRECT_ASSISTANT字段修复，再并列返回REQUEST_SUMMARY；不得只表达同意后"
                "继续展示旧状态。修复动作中的value只能是清洗后的实验设计内容，不能含有‘请确认’、"
                "‘另外我想问’等对话操作本身。若学生只指出受影响字段而没有提供足以确定的新值，"
                "CORRECT_ASSISTANT只返回affected_fields，不得猜写字段值。"
                "semantic_updates_json用于返回同一轮已经明确的结构化更新，只能包含："
                "selected_option_ids（必须来自pending_action中的真实option_id）、"
                "no_direction、course_scope_status（只能为COURSE_CONTENT、OUT_OF_SCOPE或UNCERTAIN）、"
                "stage_one_direction_detail（只在学生回应三幅图景时，同时说出了自己想研究的"
                "具体物理现象或关系时填写其实质描述；只选A/B/C时为null）、"
                "topic_change_explicit（只有学生明确放弃、替换当前研究方向时为true）、"
                "facet_updates（facet_id只能使用carried_context.idea_development中的ID，"
                "仅在学生明确回答或明确撤回该项时标CLEAR或MISSING；学生是在原内容上补充且"
                "要求其他内容不变时operation=MERGE，明确替换时operation=REPLACE；CLEAR更新"
                "必须用value保存该字段最终应写入的实质内容，不能把会话操作写入value）、"
                "design_updates（所有普通引导模式下的实质设计修改都必须逐项返回，元素包含field、"
                "operation、value和semantic_key。field只能为research_object、course_relationship、"
                "learning_objective、research_question、theoretical_framework、hypothesis、"
                "expected_phenomenon、conceptual_structure；operation只能为MERGE、REPLACE、CLEAR。"
                "一个请求修改几项就返回几项，不得合并字段。学生补充且保留原内容时必须MERGE；"
                "明确改写时REPLACE。模型只提出修改，程序负责验证和提交）、"
                "stage_field_updates（后续阶段和EMVR中的字段级更新；每项同样包含semantic_key；field只能为"
                "independent_variable、observations、controlled_conditions、procedure_steps、"
                "visualization_plan、result_interpretation、limitations、unity_objects、interactions；"
                "operation同样只能为MERGE、REPLACE、CLEAR。用户一轮修改几项就逐项返回几项）、"
                "student_questions、feedback_items与unresolved_content（分别保存用户课程问题、"
                "对助手理解的纠错反馈和仍无法解析的局部内容；这些文字不能写入设计字段）、"
                "comparison_updates（修改现有组时comparison_id必须来自pending_action或"
                "carried_context，action为ACCEPT、MODIFY或REJECT；学生提出与现有组不同的"
                "新比较维度时action=CREATE、comparison_id留空，title和new_cases必须取自学生原话。"
                "学生可以新增自己明确提出的比较情形，"
                "但新增case必须逐字取自user_message，不能由模型补写；若是在现有对照上追加且"
                "保留原项，merge_with_existing=true，否则false。修改已有情形时优先使用"
                "semantic_case_catalog中的case_ref表达语义身份：case_refs列出保留的身份，"
                "renames用case_ref和学生原话中的新label改名，new_cases只放真正新增且逐字来自"
                "user_message的情形；学生要求用一组简洁表述替换整组时replace_all=true。"
                "canonical=false表示旧会话中的未核对自定义表述；若它与canonical=true项语义等价，"
                "只保留canonical项并用renames记录学生希望显示的说法。同一物理情形的缩写、"
                "完整说法或改名不是新增项，不能把新旧表述同时保留），"
                "comparison_updates还应为比较组返回semantic_key，并在case_semantic_keys中用"
                "{显示表述:稳定物理含义标识}标记各case；跨轮语义相同但措辞不同的比较组或case"
                "必须复用同一semantic_key，不能作为新内容追加。"
                "quality_assessment（根据提交后的完整设计含义检查一致性、因果链、概念可行性、"
                "边界情形、课程依据和多方案差异；issues逐项包含category、status、severity、fields、"
                "finding、suggestion、student_question；causal_chain包含cause、response、mechanism、"
                "comparison、answerability、status；boundary_cases只提出与当前设计有关的边界；"
                "traceability逐项说明design_field、course_item、purpose、source_type；若学生要求"
                "比较方案，option_comparison按observability、course_alignment、controllability、"
                "vr_suitability、discrimination、extra_assumptions和recommendation比较，不替学生"
                "静默作决定），guidance_need（只能为BRIEF_HINT、CONCRETE_EXAMPLE、REFERENCE_DRAFT、"
                "FORMULA_EXPLANATION、DESIGN_REVIEW或OPTION_COMPARISON，应依据学生当前困难选择帮助深度），"
                "以及interaction_state_request"
                "（只能为GUIDED_DESIGN、EMVR_DIRECT或null），以及仅供EMVR_DIRECT使用的"
                "emvr_design_update。不得臆造ID或把宽泛主题当成已回答学习目标。"
                "当interaction_state=EMVR_DIRECT且本轮包含实质实验内容时，emvr_design_update"
                "必须根据整句含义和carried_context.emvr_merged_requirements返回结构化物理设计解释。快照字段为："
                "direction_summary、research_summary、research_question、learning_objectives、"
                "changed_quantities、observed_quantities、comparison_cases、hypothesis、"
                "required_behaviors、object_constraints、procedure_steps、"
                "visualization_requirements、limitations和theory_links。各项使用学生"
                "实际表达的具体内容；例如变化方向、连续变化方式和指定观察现象都必须保留，"
                "不能压缩成‘主要参数影响目标响应’。修改请求还必须返回field_updates数组，"
                "每项为field_id、operation和value；operation只能是REPLACE、MERGE、CLEAR，"
                "field_id只能来自上述非theory字段。一个请求修改几项就返回几项，逐项绑定，"
                "不能把整条消息或多个指令合并成每个字段的value。抽象要求如‘把研究问题"
                "改成清晰的因果句’必须读取旧研究问题，生成改写后的research_question并以"
                "REPLACE保存；value中不得包含操作说明。只改一个字段时，不得顺带重写其他字段；"
                "明确替换时不能把旧值和新值并列。GUIDED_DESIGN下不得返回此对象。"
                "theory_links的每项包含relation_id、supports_design_content与supports_design_fields；"
                "supports_design_fields只能从research_question、changed_quantities、observed_quantities、"
                "comparison_cases、object_constraints中选择至少一项，并表示这条理论实际支持的结构化"
                "设计字段；supports_design_content必须明确"
                "指出该关系支持当前哪一个变化量、观察量、比较情形或边界条件，不能只重复"
                "关系名称。relation_id表示真正进入当前实验的物理机制，不是与主题沾边的"
                "课程知识列表；只选择能计算、解释或约束changed_quantities、"
                "observed_quantities或边界条件的关系。比如只有实验研究带电粒子的受力或运动时才需要"
                "CHARGED_PARTICLE_FORCE，只有研究导电电流响应时才需要OHMIC_CONDUCTION，"
                "只有研究自由电荷随时间消散时才需要CHARGE_RELAXATION。不能因为它们都属于"
                "电磁学就自动加入。可用关系ID及含义为："
                f"{json.dumps(emvr_relation_catalog, ensure_ascii=False)}。"
                "学生增加或删除理论关系时，还必须在emvr_design_update.theory_link_updates中逐项返回"
                "relation_id和operation（ADD或REMOVE）；ADD必须同时在theory_links中提供上述字段绑定，"
                "REMOVE只删除指定关系，不能重新生成或覆盖其他理论关系。"
                "程序会把包含EMVR标记的安全输入直接切换为EMVR_DIRECT；不要推翻这个明确状态。"
                "对于不含该标记的其他模式表达，仍需依据整句话语义设置interaction_state_request，"
                "不能靠扩充词表猜测。若这句话只有模式切换而没有实验内容，intent返回"
                "SET_INTERACTION_STATE；若同时包含实质实验想法，仍按该实质内容返回"
                "ANSWER_CURRENT_QUESTION或NEW_TOPIC，并同时设置interaction_state_request。"
                "course_scope_status必须结合整句话、carried_context.current_course_evidence和"
                "scope_summary判断：与课程概念存在合理物理联系就返回COURSE_CONTENT；明确属于"
                "其他课程且无法与ECE329建立实验核心关系才返回OUT_OF_SCOPE；证据不足返回UNCERTAIN。"
                "不能因为没有命中某个词、用户只说序号或使用了代词就判OUT_OF_SCOPE；此时必须结合"
                "previous_question、pending_action和已保存方向。没有具体思路属于课程内头脑风暴，"
                "course_scope_status返回COURSE_CONTENT并把no_direction设为true。"
                "学生在三幅图景后可能在同一句里完成两件事：指向一个或多个图景，并进一步说明"
                "自己的研究设想。此时selected_option_ids与stage_one_direction_detail必须同时返回；"
                "不得只记录选项而丢掉实质想法，也不得因为说得较长就重新展示图景。学生不引用图景"
                "但直接给出明确的课内研究设想时，也应把该设想写入stage_one_direction_detail。"
                "一旦carried_context中的方向已锁定，后续材料、对象、边界或观察细节默认都是对当前"
                "方向的完善，不是NEW_TOPIC。只有学生明确表示不要原方向、改做另一主题或重新开始时"
                "才能返回NEW_TOPIC，并把topic_change_explicit设为true。请求更多帮助时应围绕已锁定"
                "方向给参考，不得把target设为exploration_scenes。"
                "当pending_action.type为ANSWER_IDEA_FACET时，subject就是当前唯一需要判断的facet。"
                "REQUEST_MORE_EXAMPLES必须区分请求对象：若学生明确要另一组三幅广度图景，"
                "target返回exploration_scenes；若学生要当前开放问题或facet的课程内参考，target"
                "返回pending_action.subject。不得仅凭REQUEST_MORE_EXAMPLES这个意图重置阶段1方向。"
                "若intent为ANSWER_CURRENT_QUESTION或MODIFY_PREVIOUS_PROPOSAL，facet_updates必须且只需"
                "包含对这个subject的CLEAR或MISSING判断：学生的回答在语义上已经回答previous_question"
                "就标CLEAR。若学生明确表示需要当前问题的课程内参考、例子或可能判断，应返回"
                "REQUEST_MORE_EXAMPLES而不是ANSWER_CURRENT_QUESTION加MISSING；确实没有回答当前"
                "问题时才可标MISSING。"
                "不能因为措辞不同于示例而标MISSING，也不能遗漏该facet。学生同一轮既明确基础对照"
                "又回答当前facet时，comparison_updates和facet_updates必须同时保留。"
                "当学生说要补充一个对照且其他内容不变时，comparison_updates必须返回该对照组"
                "真实comparison_id、action=MODIFY、新增case以及merge_with_existing=true；"
                "不得只在resolved_value中复述修改而漏掉可执行更新。"
                "research_question不要求使用问号或疑问句：只要学生说明了要比较或改变的条件以及"
                "准备观察的现象，就必须标CLEAR；即使同一句还包含对现象形态的预测，也仍然可以同时"
                "构成有效研究问题。pending_action若包含candidate_answer，学生用任何语义确认上一句"
                "就是当前回答、要求沿用上一句或确认该答案时，返回ACCEPT_PREVIOUS_PROPOSAL；不得"
                "要求学生再复述candidate_answer。若还包含candidate_turns，必须结合这些连续候选"
                "判断本轮是在确认、修订还是补充，不能只看孤立短句；学生给出改写后的完整表述时，"
                "应以本轮的新字段级动作替换旧候选。"
                "当pending_action.type为ANSWER_STAGE_QUESTION时，若intent为"
                "ANSWER_CURRENT_QUESTION，semantic_updates_json必须包含pending_answer_status："
                "学生在语义上回答了previous_question就填CLEAR；只有明确没有想法或确实没有回答"
                "当前问题时才填MISSING；同时必须依照pending_action.answer_fields为回答中已经明确的"
                "每一项生成字段级动作。学生同时调整自变量、观察量或控制条件时应并行保存，不得要求"
                "把综合回答拆成几轮。请求参考、例子或可能判断时必须返回"
                "REQUEST_MORE_EXAMPLES。不能因为措辞与问题示例不同而遗漏或填MISSING。"
                "上述规则同样适用于pending_action.type=ANSWER_EMVR_STAGE_QUESTION；这是EMVR"
                "开放问题，不是要求学生确认既有方案。学生给出对象、操作、观察现象和目标等"
                "完整陈述时必须识别为ANSWER_CURRENT_QUESTION与CLEAR。若candidate_answer已保存，"
                "学生说明上一轮就是回答当前问题时，应接受该candidate_answer，不要求再次复述。"
                "当pending_action.type为CONFIRM_STAGE_OR_MODIFY或CONFIRM_OR_MODIFY时，"
                "学生补充对象、交互、约束、对照、"
                "观察量或物理说明属于MODIFY_PREVIOUS_PROPOSAL；resolved_value只保留实质补充，"
                "不要把‘我是在回答’等会话说明写成实验观点。除非学生明确放弃原实验并提出"
                "替代主题，否则EMVR补充不得返回NEW_TOPIC。"
                "若subject=STUDENT_SYNTHESIS_OR_EMVR_OUTPUT，一段学生自己写的总结只要已经串联"
                "研究问题或对象、主要比较或观察现象，以及ECE329课程关系，就应返回CLEAR；"
                "不得要求拆成多轮，也不得因为没有逐字重复‘为什么值得研究’而返回UNCLEAR。"
                "这段总结本身就是引导流程的最终动作，不需要再返回要求二次确认的意图。"
                "没有结构化更新时semantic_updates_json为null。"
            ),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": intent_input}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ece329_context_intent",
                    "schema": _intent_response_schema(),
                    "strict": True,
                }
            },
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.intent_max_output_tokens,
            "store": False,
        }
        try:
            response = self.transport.create(payload)
        except ModelServiceError:
            with self._metrics_lock:
                self._intent_api_failures += 1
            raise
        try:
            raw, resolved_value, semantic_updates = _parse_intent_response(response)
        except ModelOutputError as initial_error:
            if self.repair_attempts < 1:
                with self._metrics_lock:
                    self._intent_api_failures += 1
                raise
            # Retry the semantic task itself before degrading the whole turn to
            # UNCLEAR. This is stage-independent and keeps multi-action turns
            # intact when one nested JSON string was malformed or truncated.
            repair_payload = deepcopy(payload)
            repair_payload["max_output_tokens"] = max(
                self.intent_max_output_tokens,
                2400,
            )
            repair_payload["input"][0]["content"].append(
                {
                    "type": "input_text",
                    "text": (
                        "上一份结构化结果无法解析。请重新处理同一轮学生消息。"
                        "dialogue_acts_json是唯一可执行写入来源：逐项列出本轮所有可执行动作；"
                        "resolved_value_json与semantic_updates_json只作兼容摘要，内容必须是有效JSON。"
                        "若一部分无法确定，把那一部分写成UNRESOLVED，但仍保留其他已确定动作。"
                        "不要回答学生，只返回符合schema的完整结构化结果。"
                    ),
                }
            )
            try:
                repair_response = self.transport.create(repair_payload)
                raw, resolved_value, semantic_updates = _parse_intent_response(
                    repair_response
                )
            except ModelOutputError:
                # The rich envelope was rejected twice. Fall back to a much
                # smaller action-only contract instead of treating a format
                # failure as a total semantic outage and asking the student to
                # confirm an answer they already gave.
                try:
                    raw, resolved_value, semantic_updates = (
                        self._recover_compact_intent(intent_input)
                    )
                except ModelServiceError as compact_error:
                    with self._metrics_lock:
                        self._intent_api_failures += 1
                    raise compact_error from initial_error
            except ModelServiceError as repair_error:
                with self._metrics_lock:
                    self._intent_api_failures += 1
                raise repair_error from initial_error
            with self._metrics_lock:
                self._intent_repair_successes += 1
        raw_intent = str(raw.get("intent") or "UNCLEAR")
        raw_dialogue_acts = raw.get("dialogue_acts", [])
        has_executable_dialogue_acts = bool(
            isinstance(raw_dialogue_acts, list)
            and any(
                isinstance(item, dict)
                and str(item.get("type") or "").upper() in DIALOGUE_ACT_TYPES
                and str(item.get("type") or "").upper() != "UNRESOLVED"
                for item in raw_dialogue_acts
            )
        )
        pending_type = (
            str(pending_action.get("type") or "")
            if isinstance(pending_action, dict)
            else ""
        )
        unresolved_pending_response = bool(
            raw_intent == "UNCLEAR"
            and not has_executable_dialogue_acts
            and isinstance(pending_action, dict)
            and (
                pending_action.get("type") in OPEN_QUESTION_PENDING_TYPES
                or pending_action.get("type") in CONFIRMATION_PENDING_TYPES
            )
        )
        unresolved_stage_entry_response = bool(
            raw_intent == "UNCLEAR"
            and not has_executable_dialogue_acts
            and pending_action is None
            and session.current_stage is Stage.IDEA_BRAINSTORMING
        )
        confirmed_candidate_modification = bool(
            raw_intent == "ACCEPT_PREVIOUS_PROPOSAL"
            and isinstance(pending_action, dict)
            and pending_action.get("type") in CONFIRMATION_PENDING_TYPES
            and pending_action.get("candidate_resolution")
            == "MODIFY_PREVIOUS_PROPOSAL"
            and str(pending_action.get("candidate_answer") or "").strip()
        )
        unbacked_open_question_acceptance = bool(
            raw_intent == "ACCEPT_PREVIOUS_PROPOSAL"
            and pending_type in OPEN_QUESTION_PENDING_TYPES
            and isinstance(pending_action, dict)
            and not str(pending_action.get("candidate_answer") or "").strip()
        )
        answer_status_conflict = pending_question_answer_needs_review(
            raw_intent,
            semantic_updates,
            pending_action,
        )
        missing_authoritative_actions = bool(
            not has_executable_dialogue_acts
            and raw_intent
            in {
                "ANSWER_CURRENT_QUESTION",
                "MODIFY_PREVIOUS_PROPOSAL",
                "NEW_TOPIC",
            }
        )
        if (
            unresolved_pending_response
            or unresolved_stage_entry_response
            or confirmed_candidate_modification
            or unbacked_open_question_acceptance
            or missing_authoritative_actions
            or (answer_status_conflict and not has_executable_dialogue_acts)
            or (not has_executable_dialogue_acts and pending_question_decision_missing(
                raw_intent,
                semantic_updates,
                pending_action,
            ))
        ):
            required_facet = required_pending_facet_id(pending_action)
            repair_payload = deepcopy(payload)
            if confirmed_candidate_modification and isinstance(pending_action, dict):
                candidate_pending = deepcopy(pending_action)
                candidate_pending["candidate_confirmation_received"] = True
                repair_payload["input"][0]["content"][0]["text"] = (
                    serialize_intent_input(
                        session,
                        str(pending_action.get("candidate_answer") or ""),
                        candidate_pending,
                        carried_context,
                    )
                )
            repair_payload["input"][0]["content"].append(
                {
                    "type": "input_text",
                    "text": (
                        (
                            "上一份结构化判断遗漏了当前想法完整性要点。请重新判断同一条学生消息；"
                            f"当前必须判断的facet是{required_facet}。若学生已经在语义上回答了"
                            "previous_question，在facet_updates中返回CLEAR与最终value，并在"
                            "design_updates中返回对应字段的MERGE或REPLACE更新；若明确不知道、撤回或"
                            "没有回答，不能一边返回ANSWER_CURRENT_QUESTION一边标MISSING："
                            "请改用UNCLEAR；若学生正在请你给一个课程内参考、例子或可能判断，"
                            "返回REQUEST_MORE_EXAMPLES。如同一轮还处理了基础对照，必须同时"
                            "保留comparison_updates。"
                        )
                        if pending_type == "ANSWER_IDEA_FACET"
                        else (
                            "上一份结构化判断没有解决学生对当前草稿的处理。请结合"
                            "previous_question、proposal、candidate_answer与整条学生消息重新判断："
                            "若学生认可原草稿，返回ACCEPT_PREVIOUS_PROPOSAL；若学生增加、替换或"
                            "纠正了对象、操作、条件、观察量、目标或解释，返回"
                            "MODIFY_PREVIOUS_PROPOSAL，resolved_value_json只写实质修改内容；"
                             "若candidate_answer保存了上一轮实质补充，而学生说明上一轮就是在"
                            "回应草稿，应把candidate_answer当作本轮需要执行的原始学生修改，返回"
                            "MODIFY_PREVIOUS_PROPOSAL，并把candidate_answer作为resolved_value；"
                            "同时必须为其中每个修改生成design_updates、comparison_updates或"
                            "emvr_design_update。新增且不属于已有基础比较组的维度使用"
                            "comparison_updates.action=CREATE。只有语义仍确实无法确定时才返回UNCLEAR。"
                        )
                        if pending_type in CONFIRMATION_PENDING_TYPES
                        else (
                            "上一份结构化判断没有解决当前阶段的开放问题。请重新判断同一条"
                            "学生消息：若它在语义上回答了previous_question，intent返回"
                            "ANSWER_CURRENT_QUESTION，并在semantic_updates_json中返回"
                            "pending_answer_status=CLEAR；若学生正在请求当前问题的参考、例子或"
                            "可能判断，返回REQUEST_MORE_EXAMPLES。不能同时返回"
                            "ANSWER_CURRENT_QUESTION和MISSING，也不要仅因为学生用陈述句或"
                            "综合段落作答就返回UNCLEAR。"
                        )
                    ) + (
                        "如果pending_action中已有candidate_answer，而学生是在确认、沿用或指认"
                        "上一句为开放问题的当前回答，应返回ACCEPT_PREVIOUS_PROPOSAL。"
                    ) + (
                        "所有将写入设计的内容都必须出现在dialogue_acts_json的字段级动作中；"
                        "不能只返回外层resolved_value_json、facet_updates、design_updates或"
                        "emvr_design_update。"
                    ) + "不要回答学生，只返回完整的结构化意图结果。",
                }
            )
            try:
                repair_response = self.transport.create(repair_payload)
                raw, resolved_value, semantic_updates = _parse_intent_response(
                    repair_response
                )
            except ModelOutputError:
                try:
                    raw, resolved_value, semantic_updates = (
                        self._recover_compact_intent(intent_input)
                    )
                except ModelServiceError:
                    with self._metrics_lock:
                        self._intent_api_failures += 1
                    raise
            except ModelServiceError:
                with self._metrics_lock:
                    self._intent_api_failures += 1
                raise
        raw_dialogue_acts = raw.get("dialogue_acts", [])
        has_executable_dialogue_acts = bool(
            isinstance(raw_dialogue_acts, list)
            and any(
                isinstance(item, dict)
                and str(item.get("type") or "").upper() in DIALOGUE_ACT_TYPES
                and str(item.get("type") or "").upper() != "UNRESOLVED"
                for item in raw_dialogue_acts
            )
        )
        repaired_intent = str(raw.get("intent") or "UNCLEAR")
        required_facet_after_repair = required_pending_facet_id(pending_action)
        facet_explicitly_missing = any(
            isinstance(item, dict)
            and item.get("facet_id") == required_facet_after_repair
            and item.get("status") == "MISSING"
            for item in semantic_updates.get("facet_updates", [])
        ) if required_facet_after_repair is not None else False
        explicitly_unanswered = bool(
            semantic_updates.get("no_direction") is True
            or semantic_updates.get("pending_answer_status") == "MISSING"
            or facet_explicitly_missing
            or semantic_updates.get("course_scope_status") == "OUT_OF_SCOPE"
        )
        if (
            not has_executable_dialogue_acts
            and not explicitly_unanswered
            and pending_type in OPEN_QUESTION_PENDING_TYPES
            and isinstance(pending_action, dict)
            and pending_action.get("answer_fields")
        ):
            # A syntactically valid rich response can still omit the only
            # executable part. Use the same compact semantic pass before any
            # candidate-confirmation fallback. This is especially important
            # for variable turns, where one paragraph can update three fields.
            raw, resolved_value, semantic_updates = self._recover_compact_intent(
                intent_input
            )
            raw_dialogue_acts = raw.get("dialogue_acts", [])
            has_executable_dialogue_acts = bool(
                isinstance(raw_dialogue_acts, list)
                and any(
                    isinstance(item, dict)
                    and str(item.get("type") or "").upper() in DIALOGUE_ACT_TYPES
                    and str(item.get("type") or "").upper() != "UNRESOLVED"
                    for item in raw_dialogue_acts
                )
            )
            repaired_intent = str(raw.get("intent") or "UNCLEAR")
        if has_executable_dialogue_acts:
            # Field-level acts are validated independently downstream.  The
            # compatibility intent may be UNCLEAR without invalidating clear
            # acts from the same turn.
            pass
        elif (
            repaired_intent == "UNCLEAR"
            and pending_action is None
            and session.current_stage is Stage.IDEA_BRAINSTORMING
            and semantic_updates.get("no_direction") is True
        ):
            raw["intent"] = "REQUEST_MORE_EXAMPLES"
            raw["target"] = "exploration_scenes"
            raw["resolved_value_json"] = None
            raw["confidence"] = max(float(raw.get("confidence") or 0.0), 0.72)
            resolved_value = None
        elif (
            repaired_intent == "ACCEPT_PREVIOUS_PROPOSAL"
            and pending_type in OPEN_QUESTION_PENDING_TYPES
            and isinstance(pending_action, dict)
            and not str(pending_action.get("candidate_answer") or "").strip()
        ):
            # An open question has nothing to accept until a reference or a
            # saved candidate exists. Bind the student's substantive turn to
            # the exact pending subject instead of silently accepting a null
            # proposal and reopening the same question.
            raw["intent"] = "ANSWER_CURRENT_QUESTION"
            raw["target"] = str(pending_action.get("subject") or "")
            raw["resolved_value_json"] = json.dumps(
                user_message,
                ensure_ascii=False,
            )
            raw["confidence"] = max(float(raw.get("confidence") or 0.0), 0.72)
            resolved_value = user_message
            required_facet = required_pending_facet_id(pending_action)
            if required_facet is not None:
                semantic_updates = {
                    **semantic_updates,
                    "facet_updates": [
                        {
                            "facet_id": required_facet,
                            "status": "CLEAR",
                            "operation": "REPLACE",
                            "value": user_message,
                        }
                    ],
                }
            else:
                semantic_updates = {
                    **semantic_updates,
                    "pending_answer_status": "CLEAR",
                }
        elif (
            repaired_intent == "UNCLEAR"
            and pending_type in OPEN_QUESTION_PENDING_TYPES
            and explicitly_unanswered
        ):
            # Structured MISSING/no_direction is itself meaningful context:
            # the student has responded but wants help forming this part. Route
            # to a contextual reference instead of declaring their statement
            # to be a completed design answer or asking them to repeat it.
            raw["intent"] = "REQUEST_MORE_EXAMPLES"
            raw["target"] = str(pending_action.get("subject") or "")
            raw["resolved_value_json"] = None
            raw["confidence"] = max(float(raw.get("confidence") or 0.0), 0.72)
            resolved_value = None
        elif (
            repaired_intent == "UNCLEAR"
            and pending_type in OPEN_QUESTION_PENDING_TYPES
            and not explicitly_unanswered
        ):
            # The model has twice seen the complete pending-question context
            # and has not marked the question unanswered, a reference request,
            # a course question, or a multi-action turn.  Recover the single
            # answer as the same field-level action required from a successful
            # parse.  This fixes the old bug where an outer ANSWER intent was
            # created but then correctly discarded because its authoritative
            # action array was empty.
            answer_target = str(pending_action.get("subject") or "")
            answer_act = {
                "type": "ANSWER_PENDING_QUESTION",
                "target": answer_target,
                "operation": "REPLACE",
                "content": user_message,
                "confidence": 0.72,
            }
            raw["intent"] = "ANSWER_CURRENT_QUESTION"
            raw["target"] = answer_target
            raw["resolved_value_json"] = json.dumps(
                user_message,
                ensure_ascii=False,
            )
            raw["dialogue_acts"] = [answer_act]
            raw["dialogue_acts_json"] = json.dumps(
                [answer_act],
                ensure_ascii=False,
            )
            raw["confidence"] = max(float(raw.get("confidence") or 0.0), 0.72)
            resolved_value = user_message
            semantic_updates = {}
        elif confirmed_candidate_modification:
            # The first pass already understood the short turn as acceptance.
            # If reparsing the saved candidate still cannot produce field-level
            # actions, its outer compatibility intent (UNCLEAR, ACCEPT, etc.)
            # must not reopen the same confirmation. Do not pretend the raw
            # candidate is a safe design update; consume ACCEPT and let the
            # state machine preserve the existing normalized design.
            accept_act = {
                "type": "CONTROL",
                "target": "ACCEPT",
                "operation": "EXECUTE",
                "content": None,
                "confidence": 0.98,
            }
            raw["intent"] = "ACCEPT_PREVIOUS_PROPOSAL"
            raw["target"] = str(pending_action.get("subject") or "")
            raw["resolved_value_json"] = None
            raw["dialogue_acts"] = [accept_act]
            raw["dialogue_acts_json"] = json.dumps(
                [accept_act], ensure_ascii=False
            )
            raw["advance_requested"] = bool(
                pending_action.get("advance_on_accept") is True
                or pending_action.get("type") == "CONFIRM_STAGE_OR_MODIFY"
            )
            raw["confidence"] = max(float(raw.get("confidence") or 0.0), 0.72)
            resolved_value = None
            semantic_updates = {}
        elif pending_question_answer_needs_review(
            repaired_intent,
            semantic_updates,
            pending_action,
        ):
            required_facet = required_pending_facet_id(pending_action)
            if required_facet is not None:
                semantic_updates = {
                    **semantic_updates,
                    "facet_updates": [
                        {"facet_id": required_facet, "status": "CLEAR"}
                    ],
                }
            elif pending_type in {
                "ANSWER_STAGE_QUESTION",
                "ANSWER_EMVR_STAGE_QUESTION",
            }:
                semantic_updates = {
                    **semantic_updates,
                    "pending_answer_status": "CLEAR",
                }
        candidate = resolved_intent(
            str(raw.get("intent") or "UNCLEAR"),
            target=str(raw.get("target") or "") or None,
            resolved_value=resolved_value,
            advance_requested=raw.get("advance_requested"),
            preserve_current_design=raw.get("preserve_current_design", True),
            confidence=raw.get("confidence", 0.0),
            source="SEMANTIC_MODEL",
            semantic_updates=semantic_updates,
            dialogue_acts=(
                raw.get("dialogue_acts")
                if isinstance(raw.get("dialogue_acts"), list)
                else []
            ),
            # The current Responses schema always includes this field.  Older
            # saved integrations that predate the action contract may omit it
            # entirely; only those legacy payloads retain compatibility
            # behavior while every current API response is action-only.
            actions_authoritative="dialogue_acts_json" in raw,
        )
        validated = validate_resolved_intent(candidate, pending_action)
        if (
            validated.get("intent") == "UNCLEAR"
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.IDEA_BRAINSTORMING
        ):
            # Stage 1 is exploratory and its pending item is non-blocking.
            # Recover from course evidence and topic state instead of
            # replaying a global clarification prompt.
            validated = validate_resolved_intent(
                degraded_context_intent(
                    session,
                    user_message,
                    pending_action,
                    carried_context,
                    source="SEMANTIC_UNCLEAR_RECOVERY",
                ),
                pending_action,
            )
        with self._metrics_lock:
            self._intent_api_successes += 1
        return validated

    def generate(self, session: DesignSession, user_message: str) -> StepOutput:
        if classify_stage_one_input(user_message) == UNREASONABLE_REQUEST:
            return RuleBasedStageGenerator().generate(session, user_message)
        packet = build_prompt_packet(
            session,
            user_message,
            include_recent_history=not self.stateful,
        )
        input_text = (
            f"{packet['user']}\n\n"
            "传输契约说明：把stage_payload对象序列化到stage_payload_json字符串中；"
            "只有阶段10才把visualization对象序列化到visualization_json字符串中，"
            "其他阶段visualization_json必须为null。\n\n"
            "CONTEXT_JSON:\n"
            f"{packet['serialized_context']}"
        )
        if (
            session.interaction_state is InteractionState.EMVR_DIRECT
            and session.current_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
        ):
            output_budget = max(
                self.max_output_tokens,
                self.final_max_output_tokens,
            )
        elif (
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.IDEA_BRAINSTORMING
        ):
            output_budget = max(
                self.max_output_tokens,
                self.stage_one_max_output_tokens,
            )
        else:
            output_budget = self.max_output_tokens
        request_payload: dict[str, Any] = {
            "model": self.model,
            "instructions": packet["system"],
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": input_text}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ece329_stage_output",
                    "schema": _response_schema(),
                    "strict": True,
                }
            },
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": output_budget,
            "store": self.stateful,
        }
        if self.stateful:
            previous_response_id = session.model_context.get(
                "openai_previous_response_id"
            )
            if isinstance(previous_response_id, str) and previous_response_id:
                request_payload["previous_response_id"] = previous_response_id
        try:
            response = self.transport.create(request_payload)
        except ModelHTTPError as exc:
            can_reset_chain = (
                self.stateful
                and "previous_response_id" in request_payload
                and exc.status_code in {400, 404}
            )
            if not can_reset_chain:
                with self._metrics_lock:
                    self._api_failures += 1
                raise
            session.model_context.pop("openai_previous_response_id", None)
            request_payload.pop("previous_response_id", None)
            with self._metrics_lock:
                self._chain_resets += 1
            LOGGER.warning(
                "Resetting invalid OpenAI response chain for design %s",
                session.design_id,
            )
            try:
                response = self.transport.create(request_payload)
            except ModelServiceError:
                with self._metrics_lock:
                    self._api_failures += 1
                raise
        except ModelServiceError:
            with self._metrics_lock:
                self._api_failures += 1
            raise
        try:
            output = _step_output_from_response(session, response, packet)
        except ModelOutputError:
            with self._metrics_lock:
                self._output_rejections += 1
            if self.repair_attempts < 1:
                raise
            repair_payload = deepcopy(request_payload)
            repair_payload.pop("previous_response_id", None)
            repair_payload["input"][0]["content"].append(
                {
                    "type": "input_text",
                    "text": (
                        "上一份回答未通过当前阶段的结构或课程约束检查。请重新读取同一份"
                        "CONTEXT_JSON，只修正回答格式、上下文承接和检索对象复制问题；"
                        "不要改变学生已经选择的实验方向。"
                    ),
                }
            )
            try:
                repair_response = self.transport.create(repair_payload)
            except ModelServiceError:
                with self._metrics_lock:
                    self._api_failures += 1
                raise
            try:
                output = _step_output_from_response(session, repair_response, packet)
            except ModelOutputError:
                with self._metrics_lock:
                    self._output_rejections += 1
                raise
            response = repair_response
            with self._metrics_lock:
                self._repair_successes += 1
        if self.stateful:
            response_id = response.get("id")
            if isinstance(response_id, str) and response_id:
                session.model_context["openai_previous_response_id"] = response_id
        with self._metrics_lock:
            self._api_successes += 1
        return output

    def runtime_info(self) -> dict[str, Any]:
        with self._metrics_lock:
            successes = self._api_successes
            failures = self._api_failures
            chain_resets = self._chain_resets
            output_rejections = self._output_rejections
            repair_successes = self._repair_successes
            intent_successes = self._intent_api_successes
            intent_failures = self._intent_api_failures
            intent_repair_successes = self._intent_repair_successes
        return {
            "provider": "openai",
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "intent_max_output_tokens": self.intent_max_output_tokens,
            "fallback_enabled": False,
            "stateful": self.stateful,
            "api_successes": successes,
            "api_failures": failures,
            "response_chain_resets": chain_resets,
            "output_rejections": output_rejections,
            "repair_successes": repair_successes,
            "intent_api_successes": intent_successes,
            "intent_api_failures": intent_failures,
            "intent_repair_successes": intent_repair_successes,
        }


@dataclass(slots=True)
class FallbackStageGenerator:
    primary: OpenAIStageGenerator
    fallback: RuleBasedStageGenerator
    _fallback_calls: int = field(default=0, init=False, repr=False)
    _last_fallback_reason: str | None = field(default=None, init=False, repr=False)
    _metrics_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def resolve_intent(
        self,
        session: DesignSession,
        user_message: str,
        pending_action: dict[str, Any] | None,
        carried_context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.primary.resolve_intent(
                session,
                user_message,
                pending_action,
                carried_context,
            )
        except ModelServiceError as exc:
            if isinstance(exc, ModelOutputError):
                fallback_reason = "intent_output_rejected"
            elif isinstance(exc, ModelHTTPError):
                fallback_reason = f"intent_http_{exc.status_code}"
            elif isinstance(exc, ModelConfigurationError):
                fallback_reason = "intent_configuration_error"
            else:
                fallback_reason = "intent_transport_error"
            with self._metrics_lock:
                self._fallback_calls += 1
                self._last_fallback_reason = fallback_reason
            return degraded_context_intent(
                session,
                user_message,
                pending_action,
                carried_context,
                source="SEMANTIC_SERVICE_FALLBACK",
            )

    def generate(self, session: DesignSession, user_message: str) -> StepOutput:
        try:
            return self.primary.generate(session, user_message)
        except ModelServiceError as exc:
            if isinstance(exc, ModelOutputError):
                fallback_reason = "model_output_rejected"
            elif isinstance(exc, ModelHTTPError):
                fallback_reason = f"model_http_{exc.status_code}"
            elif isinstance(exc, ModelConfigurationError):
                fallback_reason = "model_configuration_error"
            else:
                fallback_reason = "model_transport_error"
            with self._metrics_lock:
                self._fallback_calls += 1
                self._last_fallback_reason = fallback_reason
            LOGGER.warning(
                "Using rule-based fallback after %s",
                type(exc).__name__,
            )
            output = self.fallback.generate(session, user_message)
            return output

    def runtime_info(self) -> dict[str, Any]:
        primary_info = self.primary.runtime_info()
        with self._metrics_lock:
            fallback_calls = self._fallback_calls
            last_fallback_reason = self._last_fallback_reason
        return {
            "provider": "openai",
            "model": self.primary.model,
            "reasoning_effort": self.primary.reasoning_effort,
            "intent_max_output_tokens": self.primary.intent_max_output_tokens,
            "fallback_enabled": True,
            "fallback_provider": "rule_based",
            "stateful": self.primary.stateful,
            "api_successes": primary_info["api_successes"],
            "api_failures": primary_info["api_failures"],
            "response_chain_resets": primary_info["response_chain_resets"],
            "output_rejections": primary_info["output_rejections"],
            "repair_successes": primary_info["repair_successes"],
            "intent_api_successes": primary_info["intent_api_successes"],
            "intent_api_failures": primary_info["intent_api_failures"],
            "intent_repair_successes": primary_info["intent_repair_successes"],
            "fallback_calls": fallback_calls,
            "last_fallback_reason": last_fallback_reason,
        }


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ModelConfigurationError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ModelConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(parsed) or not parsed > 0:
        raise ModelConfigurationError(f"{name} must be greater than zero")
    return parsed


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise ModelConfigurationError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ModelConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ModelConfigurationError(f"{name} must be greater than zero")
    return parsed


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, str):
        raise ModelConfigurationError(f"{name} must be true or false")
    normalized = value.strip().casefold()
    if normalized not in {"true", "false"}:
        raise ModelConfigurationError(f"{name} must be true or false")
    return normalized == "true"


def _reasoning_effort(value: Any) -> str:
    if not isinstance(value, str):
        raise ModelConfigurationError(
            "OPENAI_REASONING_EFFORT must be none, low, medium, high, or xhigh"
        )
    normalized = value.strip().casefold()
    allowed = {"none", "low", "medium", "high", "xhigh"}
    if normalized not in allowed:
        raise ModelConfigurationError(
            "OPENAI_REASONING_EFFORT must be none, low, medium, high, or xhigh"
        )
    return normalized


def generator_from_environment(
    environ: Mapping[str, str] | None = None,
    transport: ResponsesTransport | None = None,
) -> RuleBasedStageGenerator | OpenAIStageGenerator | FallbackStageGenerator:
    env = os.environ if environ is None else environ
    mode = env.get("ECE329_GENERATOR", "auto").strip().casefold()
    if mode not in {"auto", "openai", "rule"}:
        raise ModelConfigurationError("ECE329_GENERATOR must be auto, openai, or rule")
    if mode == "rule":
        return RuleBasedStageGenerator()

    api_key = env.get("OPENAI_API_KEY", "").strip()
    if not api_key and transport is None:
        if mode == "openai":
            raise ModelConfigurationError(
                "ECE329_GENERATOR=openai requires OPENAI_API_KEY"
            )
        return RuleBasedStageGenerator()

    model = env.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    reasoning_effort = _reasoning_effort(
        env.get("OPENAI_REASONING_EFFORT", "medium")
    )
    intent_max_tokens = _positive_int(
        env.get("OPENAI_INTENT_MAX_OUTPUT_TOKENS", "1400"),
        "OPENAI_INTENT_MAX_OUTPUT_TOKENS",
    )
    timeout = _positive_float(env.get("OPENAI_TIMEOUT_SECONDS", "60"), "OPENAI_TIMEOUT_SECONDS")
    max_tokens = _positive_int(
        env.get("OPENAI_MAX_OUTPUT_TOKENS", "2400"),
        "OPENAI_MAX_OUTPUT_TOKENS",
    )
    stage_one_max_tokens = _positive_int(
        env.get("OPENAI_STAGE_ONE_MAX_OUTPUT_TOKENS", "3200"),
        "OPENAI_STAGE_ONE_MAX_OUTPUT_TOKENS",
    )
    final_max_tokens = _positive_int(
        env.get("OPENAI_FINAL_MAX_OUTPUT_TOKENS", "5000"),
        "OPENAI_FINAL_MAX_OUTPUT_TOKENS",
    )
    primary = OpenAIStageGenerator(
        transport=transport or OpenAIResponsesHTTPTransport(api_key, timeout),
        model=model,
        reasoning_effort=reasoning_effort,
        intent_max_output_tokens=intent_max_tokens,
        max_output_tokens=max_tokens,
        stage_one_max_output_tokens=stage_one_max_tokens,
        final_max_output_tokens=final_max_tokens,
        stateful=_boolean(
            env.get("ECE329_OPENAI_STATEFUL", "false"),
            "ECE329_OPENAI_STATEFUL",
        ),
    )
    fallback_enabled = _boolean(
        env.get("ECE329_OPENAI_FALLBACK", "true"),
        "ECE329_OPENAI_FALLBACK",
    )
    if not fallback_enabled:
        return primary
    return FallbackStageGenerator(primary=primary, fallback=RuleBasedStageGenerator())
