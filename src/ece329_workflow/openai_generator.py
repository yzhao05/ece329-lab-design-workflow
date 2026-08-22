from __future__ import annotations

import json
import logging
import os
import socket
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .generator import ILLUSTRATIVE_EXTENSION_SCOPE, RuleBasedStageGenerator
from .guardrails import (
    AMBIGUOUS,
    BREADTH_EXPLORATION,
    COURSE_CONTENT,
    DEPTH_EXPANSION,
    INTEREST_DESCRIPTION,
    OUT_OF_SCOPE,
    UNREASONABLE_REQUEST,
    classify_stage_one_input,
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
            if len(exploration_scenes) != len(alternatives):
                raise ModelOutputError(
                    "Breadth exploration requires one scene per grounded alternative"
                )
            seen_scene_ids: set[str] = set()
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
    ):
        stage_one_thread = packet["context"].get("stage_one_thread", {})
        if isinstance(stage_one_thread, dict):
            for key in (
                "topic_anchor",
                "current_focus",
                "focus_history",
                "contextual_continuation",
                "brainstorm_phase",
                "selected_focus",
                "interest_description",
                "ready_for_next_stage",
                "resolved_stage_one_reference",
            ):
                output.stage_payload[key] = deepcopy(stage_one_thread.get(key))
            phase = stage_one_thread.get("brainstorm_phase")
            if phase in {INTEREST_DESCRIPTION, DEPTH_EXPANSION}:
                output.stage_payload["alternative_ideas"] = []
                output.stage_payload["exploration_scenes"] = []
            if phase == DEPTH_EXPANSION:
                output.stage_payload["deepening_connections"] = deepcopy(
                    packet["context"]["knowledge_retrieval"]["brainstorm_options"]
                )
            current_focus = str(stage_one_thread.get("current_focus") or "").strip()
            if (
                stage_one_thread.get("contextual_continuation") is True
                and current_focus
                and current_focus not in output.assistant_message
            ):
                output.assistant_message = (
                    f"我们继续沿着已经形成的“{current_focus}”方向讨论；"
                    "你这次的回答是在补充它，不是开始一个新实验。\n\n"
                    f"{output.assistant_message}"
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
    _metrics_lock: RLock = field(default_factory=RLock, init=False, repr=False)

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
        }


@dataclass(slots=True)
class FallbackStageGenerator:
    primary: OpenAIStageGenerator
    fallback: RuleBasedStageGenerator
    _fallback_calls: int = field(default=0, init=False, repr=False)
    _last_fallback_reason: str | None = field(default=None, init=False, repr=False)
    _metrics_lock: RLock = field(default_factory=RLock, init=False, repr=False)

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
            output.warnings.append(
                "本轮暂时使用课程内置引导；之前的实验方向和选择已保留。"
            )
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
