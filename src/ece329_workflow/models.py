from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from copy import deepcopy
from typing import Any


class InteractionState(str, Enum):
    GUIDED_DESIGN = "GUIDED_DESIGN"
    EMVR_DIRECT = "EMVR_DIRECT"


class Stage(str, Enum):
    IDEA_BRAINSTORMING = "IDEA_BRAINSTORMING"
    COURSE_MAPPING_AND_DIRECTION = "COURSE_MAPPING_AND_DIRECTION"
    LEARNING_OBJECTIVES = "LEARNING_OBJECTIVES"
    RESEARCH_QUESTION = "RESEARCH_QUESTION"
    THEORETICAL_FRAMEWORK = "THEORETICAL_FRAMEWORK"
    HYPOTHESIS = "HYPOTHESIS"
    CONCEPTUAL_OR_VR_SETUP = "CONCEPTUAL_OR_VR_SETUP"
    VARIABLES_AND_CONDITIONS = "VARIABLES_AND_CONDITIONS"
    CONCEPTUAL_PROCEDURE = "CONCEPTUAL_PROCEDURE"
    EXPECTED_DATA_VISUALIZATION = "EXPECTED_DATA_VISUALIZATION"
    RESULT_INTERPRETATION = "RESULT_INTERPRETATION"
    DESIGN_VALUE_AND_LIMITATIONS = "DESIGN_VALUE_AND_LIMITATIONS"
    STUDENT_SYNTHESIS_OR_EMVR_OUTPUT = "STUDENT_SYNTHESIS_OR_EMVR_OUTPUT"


STAGE_SEQUENCE: tuple[Stage, ...] = tuple(Stage)


class WorkflowStatus(str, Enum):
    ACTIVE = "active"
    COMPLETE = "complete"


@dataclass(slots=True)
class StepOutput:
    assistant_message: str
    stage_payload: dict[str, Any] = field(default_factory=dict)
    student_task: str | None = None
    visualization: dict[str, Any] | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DesignSession:
    design_id: str
    interaction_state: InteractionState
    access_token_hash: str = ""
    current_stage_index: int = 0
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    revision: int = 0
    completed_stages: list[str] = field(default_factory=list)
    design_context: dict[str, Any] = field(default_factory=dict)
    stage_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    model_context: dict[str, Any] = field(default_factory=dict)
    turn_context: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def current_stage(self) -> Stage:
        return STAGE_SEQUENCE[min(self.current_stage_index, len(STAGE_SEQUENCE) - 1)]

    @property
    def next_stage(self) -> Stage | None:
        next_index = self.current_stage_index + 1
        if next_index >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[next_index]

    def to_dict(self, include_history: bool = False) -> dict[str, Any]:
        public_design_context = deepcopy(self.design_context)
        public_design_state = public_design_context.get("design_state")
        if isinstance(public_design_state, dict):
            for internal_key in (
                "pending_action",
                "applied_update_ids",
                "seen_scene_template_ids",
                "seen_scene_signatures",
                "legacy_migrated",
                "explicitly_cleared_fields",
                "scene_history_migrated",
                "semantic_signatures",
                "topic_lock",
                "field_provenance",
            ):
                public_design_state.pop(internal_key, None)
        public_stage_state = public_design_context.get("stage_design_state")
        if isinstance(public_stage_state, dict):
            for internal_key in (
                "applied_update_ids",
                "semantic_signatures",
                "last_updated_stage",
                "field_provenance",
            ):
                public_stage_state.pop(internal_key, None)
        data = {
            "design_id": self.design_id,
            "interaction_state": self.interaction_state.value,
            "current_stage": self.current_stage.value,
            "stage_number": self.current_stage_index + 1,
            "status": self.status.value,
            "revision": self.revision,
            "completed_stages": list(self.completed_stages),
            "design_context": public_design_context,
            "stage_outputs": self.stage_outputs,
        }
        if include_history:
            data["history"] = self.history
        return data


@dataclass(slots=True)
class TurnRequest:
    message: str
    complete_stage: bool = False
    context_patch: dict[str, Any] = field(default_factory=dict)
    interaction_state: InteractionState | None = None
    selected_option_id: str | None = None
    turn_id: str | None = None
    version_request: dict[str, Any] | None = None


class WorkflowError(Exception):
    """Base workflow error."""


class SessionNotFound(WorkflowError):
    pass


class StageCompletionError(WorkflowError):
    pass


class SessionConflict(WorkflowError):
    pass


class DesignAccessDenied(WorkflowError):
    pass
