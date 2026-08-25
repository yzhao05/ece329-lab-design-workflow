from __future__ import annotations

import json
import logging
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
    pending_question_answer_needs_review,
    pending_question_decision_missing,
    required_pending_facet_id,
    resolved_intent,
    serialize_intent_input,
    validate_resolved_intent,
)
from .generator import (
    ILLUSTRATIVE_EXTENSION_SCOPE,
    NO_DIRECTION_ACKNOWLEDGEMENT,
    RuleBasedStageGenerator,
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
        "simulation_inputs",
        "calculated_outputs",
        "visual_only_elements",
    ),
    Stage.HYPOTHESIS: ("research_hypothesis", "expected_trend", "limiting_cases"),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "unity_objects",
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
            "advance_requested": {"type": "boolean"},
            "preserve_current_design": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "intent",
            "target",
            "resolved_value_json",
            "semantic_updates_json",
            "advance_requested",
            "preserve_current_design",
            "confidence",
        ],
        "additionalProperties": False,
    }


def _parse_intent_response(
    response: dict[str, Any],
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    try:
        raw = json.loads(_extract_output_text(response))
    except json.JSONDecodeError as exc:
        raise ModelOutputError("Intent model output was invalid") from exc
    if not isinstance(raw, dict):
        raise ModelOutputError("Intent model output must be an object")
    resolved_value = None
    encoded_value = raw.get("resolved_value_json")
    if isinstance(encoded_value, str):
        try:
            resolved_value = json.loads(encoded_value)
        except json.JSONDecodeError as exc:
            raise ModelOutputError("Intent resolved_value_json was invalid") from exc
    semantic_updates: dict[str, Any] = {}
    encoded_updates = raw.get("semantic_updates_json")
    if isinstance(encoded_updates, str):
        try:
            parsed_updates = json.loads(encoded_updates)
        except json.JSONDecodeError as exc:
            raise ModelOutputError("Intent semantic_updates_json was invalid") from exc
        if not isinstance(parsed_updates, dict):
            raise ModelOutputError("Intent semantic_updates_json must encode an object")
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
        missing_fields = [
            field for field in required_fields if not output.stage_payload.get(field)
        ]
        if missing_fields:
            raise ModelOutputError(
                f"EMVR stage {stage.value} is missing required fields: "
                + ", ".join(missing_fields)
            )
    visible_text = " ".join(
        [output.assistant_message, output.student_task or "", *output.warnings]
    ).casefold()
    forbidden_student_facing_terms = (
        "knowledge_retrieval",
        "知识检索",
        "知识目录",
        "stage_payload",
        "结构化字段",
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
            scenes = build_exploration_scenes(alternatives)
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
                    "这个雏形保留了你已经确定的方向，后续讨论会继续沿着同一个物理关系展开，"
                    "不会让你重新选择已经确定的内容。"
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
    _metrics_lock: RLock = field(default_factory=RLock, init=False, repr=False)

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
        payload = {
            "model": self.model,
            "instructions": (
                "你只负责识别学生本轮对话意图，不回答课程问题，也不决定阶段编号。"
                "必须结合previous_question、pending_action、carried_context和user_message。"
                "可选意图只有ANSWER_CURRENT_QUESTION、ACCEPT_PREVIOUS_PROPOSAL、"
                "MODIFY_PREVIOUS_PROPOSAL、REJECT_PREVIOUS_PROPOSAL、ADVANCE_STAGE、"
                "REQUEST_MORE_EXAMPLES、RETURN_TO_PREVIOUS_POINT、NEW_TOPIC、"
                "SET_INTERACTION_STATE、UNCLEAR。"
                "凡是必须依赖上一轮才能理解的表达都要走这一套语义判断，包括指代某个或多个选项、"
                "组合前述图景、表示暂无方向、回答或撤回想法完整性要点、接受或局部修改建议、"
                "继续推进、索取其他例子、返回前项和更换主题；不能用孤立词语代替上下文判断。"
                "类似‘沿用刚才安排’‘两个都留下’‘不用改，接着做’应根据上一项待办解析，"
                "不能按孤立关键词判断。只有语义确实不足时才返回UNCLEAR。"
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
                "semantic_updates_json用于返回同一轮已经明确的结构化更新，只能包含："
                "selected_option_ids（必须来自pending_action中的真实option_id）、"
                "no_direction、course_scope_status（只能为COURSE_CONTENT、OUT_OF_SCOPE或UNCERTAIN）、"
                "facet_updates（facet_id只能使用carried_context.idea_development中的ID，"
                "仅在学生明确回答或明确撤回该项时标CLEAR或MISSING）、"
                "comparison_updates（comparison_id和cases必须来自pending_action或carried_context，"
                "action只能为ACCEPT、MODIFY、REJECT），以及interaction_state_request"
                "（只能为GUIDED_DESIGN、EMVR_DIRECT或null）。不得臆造ID或把宽泛主题当成已回答学习目标。"
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
                "research_question不要求使用问号或疑问句：只要学生说明了要比较或改变的条件以及"
                "准备观察的现象，就必须标CLEAR；即使同一句还包含对现象形态的预测，也仍然可以同时"
                "构成有效研究问题。pending_action若包含candidate_answer，学生用任何语义确认上一句"
                "就是当前回答、要求沿用上一句或确认该答案时，返回ACCEPT_PREVIOUS_PROPOSAL；不得"
                "要求学生再复述candidate_answer。"
                "当pending_action.type为ANSWER_STAGE_QUESTION时，若intent为"
                "ANSWER_CURRENT_QUESTION，semantic_updates_json必须包含pending_answer_status："
                "学生在语义上回答了previous_question就填CLEAR；只有明确没有想法或确实没有回答"
                "当前问题时才填MISSING；请求参考、例子或可能判断时必须返回"
                "REQUEST_MORE_EXAMPLES。不能因为措辞与问题示例不同而遗漏或填MISSING。"
                "若subject=STUDENT_SYNTHESIS_OR_EMVR_OUTPUT，一段学生自己写的总结只要已经串联"
                "研究问题或对象、主要比较或观察现象，以及ECE329课程关系，就应返回CLEAR；"
                "不得要求拆成多轮，也不得因为没有逐字重复‘为什么值得研究’而返回UNCLEAR。"
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
            "max_output_tokens": 600,
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
        except ModelOutputError:
            with self._metrics_lock:
                self._intent_api_failures += 1
            raise
        raw_intent = str(raw.get("intent") or "UNCLEAR")
        unresolved_open_question = bool(
            raw_intent == "UNCLEAR"
            and isinstance(pending_action, dict)
            and pending_action.get("type")
            in {"ANSWER_IDEA_FACET", "ANSWER_STAGE_QUESTION"}
        )
        answer_status_conflict = pending_question_answer_needs_review(
            raw_intent,
            semantic_updates,
            pending_action,
        )
        if (
            unresolved_open_question
            or answer_status_conflict
            or pending_question_decision_missing(
                raw_intent,
                semantic_updates,
                pending_action,
            )
        ):
            required_facet = required_pending_facet_id(pending_action)
            pending_type = str(pending_action.get("type") or "") \
                if isinstance(pending_action, dict) else ""
            repair_payload = deepcopy(payload)
            repair_payload["input"][0]["content"].append(
                {
                    "type": "input_text",
                    "text": (
                        (
                            "上一份结构化判断遗漏了当前想法完整性要点。请重新判断同一条学生消息；"
                            f"当前必须判断的facet是{required_facet}。若学生已经在语义上回答了"
                            "previous_question，在facet_updates中返回CLEAR；若明确不知道、撤回或"
                            "没有回答，不能一边返回ANSWER_CURRENT_QUESTION一边标MISSING："
                            "请改用UNCLEAR；若学生正在请你给一个课程内参考、例子或可能判断，"
                            "返回REQUEST_MORE_EXAMPLES。如同一轮还处理了基础对照，必须同时"
                            "保留comparison_updates。"
                        )
                        if pending_type == "ANSWER_IDEA_FACET"
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
                        "上一句为当前回答，应返回ACCEPT_PREVIOUS_PROPOSAL。"
                    ) + "不要回答学生，只返回完整的结构化意图结果。",
                }
            )
            try:
                repair_response = self.transport.create(repair_payload)
                raw, resolved_value, semantic_updates = _parse_intent_response(
                    repair_response
                )
            except ModelServiceError:
                with self._metrics_lock:
                    self._intent_api_failures += 1
                raise
            except ModelOutputError:
                with self._metrics_lock:
                    self._intent_api_failures += 1
                raise
        candidate = resolved_intent(
            str(raw.get("intent") or "UNCLEAR"),
            target=str(raw.get("target") or "") or None,
            resolved_value=resolved_value,
            advance_requested=raw.get("advance_requested"),
            preserve_current_design=raw.get("preserve_current_design", True),
            confidence=raw.get("confidence", 0.0),
            source="SEMANTIC_MODEL",
            semantic_updates=semantic_updates,
        )
        validated = validate_resolved_intent(candidate, pending_action)
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
        return {
            "provider": "openai",
            "model": self.model,
            "fallback_enabled": False,
            "stateful": self.stateful,
            "api_successes": successes,
            "api_failures": failures,
            "response_chain_resets": chain_resets,
            "output_rejections": output_rejections,
            "repair_successes": repair_successes,
            "intent_api_successes": intent_successes,
            "intent_api_failures": intent_failures,
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
        except ModelServiceError:
            with self._metrics_lock:
                self._fallback_calls += 1
                self._last_fallback_reason = "intent_service_unavailable"
            return resolved_intent(
                "UNCLEAR",
                confidence=0.0,
                source="INTENT_SERVICE_UNAVAILABLE",
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
            "fallback_calls": fallback_calls,
            "last_fallback_reason": last_fallback_reason,
        }


def _positive_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ModelConfigurationError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise ModelConfigurationError(f"{name} must be greater than zero")
    return parsed


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ModelConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ModelConfigurationError(f"{name} must be greater than zero")
    return parsed


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().casefold()
    if normalized not in {"true", "false"}:
        raise ModelConfigurationError(f"{name} must be true or false")
    return normalized == "true"


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
    timeout = _positive_float(env.get("OPENAI_TIMEOUT_SECONDS", "45"), "OPENAI_TIMEOUT_SECONDS")
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
