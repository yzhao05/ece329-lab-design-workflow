from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Protocol

from .emvr_design import (
    EMVR_EDITABLE_FIELDS,
    merge_emvr_structured_requirements,
    normalize_emvr_design_update,
)
from .builder_requirements import BUILDER_REQUIREMENT_FIELDS
from .design_state import (
    DESIGN_FIELD_TO_FACET,
    FACET_TO_DESIGN_FIELD,
    apply_design_updates,
    baseline_comparisons_snapshot,
    design_state_snapshot,
    design_updates_from_facets,
    ensure_design_state,
    refresh_topic_lock,
    topic_lock_snapshot,
    set_baseline_comparisons,
    set_pending_action_snapshot,
)
from .dialogue_acts import (
    CONTROL_TARGETS,
    DESIGN_ACT_FIELDS,
    STAGE_ACT_FIELDS,
    apply_stage_field_updates,
    compile_dialogue_acts,
    normalize_dialogue_acts,
    stage_design_state_snapshot,
)
from .turn_planning import build_stage_context_summary, build_turn_task_plan
from .models import DesignSession, InteractionState, Stage, StepOutput


_EMVR_FIELDS_BY_PENDING_SUBJECT: dict[str, frozenset[str]] = {
    Stage.IDEA_BRAINSTORMING.value: frozenset(
        {
            "experiment_brief",
            "research_object",
            "direction_summary",
            "research_summary",
            "observed_quantities",
            "required_behaviors",
            "object_constraints",
        }
    ),
    Stage.COURSE_MAPPING_AND_DIRECTION.value: frozenset(
        {"course_relationship", "design_rationale", "lab_title", "lab_id"}
    ),
    Stage.LEARNING_OBJECTIVES.value: frozenset(
        {
            "learning_objectives",
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        }
    ),
    Stage.RESEARCH_QUESTION.value: frozenset(
        {"research_question", "changed_quantities", "observed_quantities"}
    ),
    Stage.THEORETICAL_FRAMEWORK.value: frozenset(),
    Stage.HYPOTHESIS.value: frozenset({"hypothesis", "observed_quantities"}),
    Stage.CONCEPTUAL_OR_VR_SETUP.value: frozenset(
        {
            "required_behaviors",
            "object_constraints",
            "visualization_requirements",
            "desktop_interaction_plan",
            "room_spatial_requirements",
            "hidden_object_lifecycle",
        }
    ),
    Stage.VARIABLES_AND_CONDITIONS.value: frozenset(
        {
            "changed_quantities",
            "observed_quantities",
            "object_constraints",
            "parameter_specifications",
        }
    ),
    Stage.CONCEPTUAL_PROCEDURE.value: frozenset({"procedure_steps"}),
    Stage.EXPECTED_DATA_VISUALIZATION.value: frozenset(
        {"visualization_requirements"}
    ),
    Stage.RESULT_INTERPRETATION.value: frozenset(
        {"expected_results", "acceptance_criteria", "report_questions"}
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS.value: frozenset(
        {"design_values", "limitations"}
    ),
}

_EMVR_FIELDS_BY_CANONICAL_FIELD: dict[str, frozenset[str]] = {
    "research_object": frozenset(
        {"research_object", "direction_summary", "research_summary"}
    ),
    "course_relationship": frozenset({"course_relationship"}),
    "learning_objective": frozenset({"learning_objectives"}),
    "research_question": frozenset({"research_question"}),
    "hypothesis": frozenset({"hypothesis"}),
    "expected_phenomenon": frozenset({"hypothesis", "observed_quantities"}),
    # Conceptual structure describes objects, boundaries and model relations.
    # Student-operable behavior has its own ``interactions`` field and must not
    # inherit explanatory text from this design facet.
    "conceptual_structure": frozenset({"object_constraints"}),
    "independent_variable": frozenset({"changed_quantities"}),
    "observations": frozenset({"observed_quantities"}),
    "controlled_conditions": frozenset({"object_constraints"}),
    "procedure_steps": frozenset({"procedure_steps"}),
    "visualization_plan": frozenset({"visualization_requirements"}),
    "limitations": frozenset({"limitations"}),
    "design_rationale": frozenset({"design_rationale"}),
    "design_value": frozenset({"design_values"}),
    "unity_objects": frozenset({"object_constraints"}),
    "interactions": frozenset({"required_behaviors"}),
    "lab_title": frozenset({"lab_title"}),
    "lab_id": frozenset({"lab_id"}),
    "desktop_interaction_plan": frozenset({"desktop_interaction_plan"}),
    "room_spatial_requirements": frozenset({"room_spatial_requirements"}),
    "hidden_object_lifecycle": frozenset({"hidden_object_lifecycle"}),
    "parameter_specifications": frozenset({"parameter_specifications"}),
    "expected_results": frozenset({"expected_results"}),
    "acceptance_criteria": frozenset({"acceptance_criteria"}),
    "report_questions": frozenset({"report_questions"}),
}

_THEORY_SUPPORT_FIELDS = frozenset(
    {
        "research_question",
        "changed_quantities",
        "observed_quantities",
        "comparison_cases",
        "object_constraints",
    }
)


# An open stage question is a request for one or more canonical design fields,
# not for an opaque answer attached to the public stage id. Persist these
# targets with the pending action so long turns can be split and a stage-id
# answer can still be bound when the target is unambiguous.
_GUIDED_PENDING_ANSWER_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.VARIABLES_AND_CONDITIONS: (
        "independent_variable",
        "observations",
        "controlled_conditions",
    ),
    Stage.CONCEPTUAL_PROCEDURE: ("procedure_steps",),
    Stage.EXPECTED_DATA_VISUALIZATION: ("visualization_plan",),
    Stage.RESULT_INTERPRETATION: ("result_interpretation",),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: ("limitations",),
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: ("student_summary",),
}

_EMVR_PENDING_ANSWER_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.IDEA_BRAINSTORMING: (
        "research_object",
        "course_relationship",
        "observations",
        "interactions",
        "conceptual_structure",
    ),
    Stage.COURSE_MAPPING_AND_DIRECTION: ("course_relationship",),
    Stage.LEARNING_OBJECTIVES: ("learning_objective",),
    Stage.RESEARCH_QUESTION: ("research_question",),
    Stage.THEORETICAL_FRAMEWORK: ("theoretical_framework",),
    Stage.HYPOTHESIS: ("hypothesis", "expected_phenomenon"),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "conceptual_structure",
        "unity_objects",
        "interactions",
    ),
    Stage.VARIABLES_AND_CONDITIONS: (
        "independent_variable",
        "observations",
        "controlled_conditions",
    ),
    Stage.CONCEPTUAL_PROCEDURE: ("procedure_steps",),
    Stage.EXPECTED_DATA_VISUALIZATION: ("visualization_plan",),
    Stage.RESULT_INTERPRETATION: ("result_interpretation",),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: ("limitations",),
}

_GUIDED_CONFIRMATION_FIELDS: dict[Stage, tuple[str, ...]] = {
    **_GUIDED_PENDING_ANSWER_FIELDS,
    Stage.DESIGN_VALUE_AND_LIMITATIONS: ("design_value", "limitations"),
}

_EMVR_CONFIRMATION_FIELDS: dict[Stage, tuple[str, ...]] = {
    **_EMVR_PENDING_ANSWER_FIELDS,
    Stage.COURSE_MAPPING_AND_DIRECTION: (
        "research_object",
        "course_relationship",
        "conceptual_structure",
        "design_rationale",
    ),
    Stage.RESEARCH_QUESTION: (
        "research_question",
        "independent_variable",
        "observations",
    ),
    Stage.LEARNING_OBJECTIVES: (
        "conceptual_objective",
        "calculation_objective",
        "analysis_objective",
        "vr_interaction_objective",
        "observation_objective",
    ),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "conceptual_structure",
        "unity_objects",
        "interactions",
        "visualization_plan",
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: ("design_value", "limitations"),
}


# Public labels are presentation vocabulary, while the values on the right
# are the only state-machine fields that may be written.  Passing this map to
# the semantic resolver lets a student revise what they actually see on the
# page (for example "目标现象" or "可用交互") without inventing a new field,
# binding the whole sentence to the stage id, or relying on phrase matching.
_PUBLIC_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "experiment_brief": ("完整实验方向", "实验方向", "实验概述"),
    "research_object": ("研究对象", "设计方向", "原始想法", "设计起点"),
    "direction_summary": ("方向摘要",),
    "research_summary": ("研究摘要",),
    "course_relationship": ("课程关系", "课程主题", "课程依据"),
    "learning_objective": ("学习目标",),
    "learning_objectives": ("学习目标", "概念目标"),
    "conceptual_objective": ("概念目标",),
    "calculation_objective": ("计算目标",),
    "analysis_objective": ("分析目标",),
    "vr_interaction_objective": ("交互目标", "VR交互目标"),
    "observation_objective": ("观察目标",),
    "research_question": ("研究问题",),
    "theoretical_framework": ("理论框架", "理论关系", "核心公式", "物理机制"),
    "hypothesis": ("研究假设",),
    "expected_phenomenon": ("预期现象", "预期趋势", "边界情形"),
    "conceptual_structure": ("实验结构", "概念结构", "模型边界", "设计边界"),
    "independent_variable": ("自变量", "可调内容", "改变的量"),
    "observations": ("目标现象", "观察量", "可观察内容", "显示现象"),
    "controlled_conditions": ("控制条件", "控制变量", "基准条件"),
    "procedure_steps": ("实验流程", "流程环节",),
    "visualization_plan": ("可视化方案", "显示内容", "数据显示"),
    "result_interpretation": ("结果解释", "结果判断",),
    "design_rationale": ("采用理由", "选择理由", "适合VR的原因", "设计依据"),
    "design_value": ("教学价值", "设计价值", "设计特点", "VR附加价值", "完善建议"),
    "limitations": ("设计局限", "模型局限", "适用边界"),
    "unity_objects": ("Unity对象", "VR对象", "实验物体"),
    "interactions": ("可用交互", "核心操作", "学生操作", "交互与反馈"),
    "lab_title": ("实验名称", "Lab名称"),
    "lab_id": ("实验ID", "Lab ID", "Builder实验ID"),
    "desktop_interaction_plan": ("桌面鼠标操作", "桌面与VR映射"),
    "room_spatial_requirements": ("房间摆放", "空间需求", "灯光与视觉风格"),
    "hidden_object_lifecycle": ("初始隐藏对象", "触发后状态"),
    "parameter_specifications": ("参数范围", "参数单位", "参数规格"),
    "expected_results": ("Lab预期结果", "具体预期结果"),
    "acceptance_criteria": ("通过条件", "验收条件"),
    "report_questions": ("报告问题", "实验报告问题"),
    "changed_quantities": ("变化量", "可调参数", "自变量"),
    "observed_quantities": ("观察量", "目标现象", "记录量"),
    "comparison_cases": ("比较情形", "基础比较", "对照情形"),
    "required_behaviors": ("核心操作", "交互行为", "对象行为"),
    "object_constraints": ("对象约束", "模型边界", "控制条件"),
    "visualization_requirements": ("可视化要求", "显示要求"),
    "design_values": ("设计价值", "教学价值"),
    "student_summary": ("学生总结",),
}


def recoverable_emvr_pending_field(
    pending_action: dict[str, Any] | None,
) -> str | None:
    """Return the exact EMVR field that may be confirmed after parse failure.

    Recovery is deliberately a two-turn operation: the failed turn is retained
    as a candidate but writes nothing; only a later explicit acceptance can
    bind it.  This is safe for every EMVR question whose pending contract names
    exactly one canonical field, including later Builder requirements, without
    guessing from the student's wording or copying a mixed message into the
    current stage.
    """

    if (
        not isinstance(pending_action, dict)
        or pending_action.get("interaction_state")
        != InteractionState.EMVR_DIRECT.value
        or pending_action.get("type") != "ANSWER_EMVR_STAGE_QUESTION"
    ):
        return None
    answer_fields = pending_action.get("answer_fields", [])
    if not isinstance(answer_fields, list) or len(answer_fields) != 1:
        return None
    field = str(answer_fields[0] or "").strip()
    return (
        field
        if field in {*DESIGN_ACT_FIELDS, *STAGE_ACT_FIELDS, *EMVR_EDITABLE_FIELDS}
        else None
    )


def recoverable_guided_pending_field(
    pending_action: dict[str, Any] | None,
) -> str | None:
    """Return one canonical guided field named by an open-question contract."""

    if not isinstance(pending_action, dict):
        return None
    pending_type = str(pending_action.get("type") or "")
    if pending_type == "ANSWER_IDEA_FACET":
        facet_id = str(pending_action.get("subject") or "").strip()
        field = FACET_TO_DESIGN_FIELD.get(facet_id)
        return field if field in DESIGN_ACT_FIELDS else None
    if pending_type != "ANSWER_STAGE_QUESTION":
        return None
    answer_fields = pending_action.get("answer_fields", [])
    if not isinstance(answer_fields, list) or len(answer_fields) != 1:
        return None
    field = str(answer_fields[0] or "").strip()
    return field if field in {*DESIGN_ACT_FIELDS, *STAGE_ACT_FIELDS} else None


def recoverable_pending_field(
    pending_action: dict[str, Any] | None,
) -> str | None:
    """Return an exact single-field recovery target for either interaction mode."""

    return (
        recoverable_emvr_pending_field(pending_action)
        or recoverable_guided_pending_field(pending_action)
    )


def _editable_field_bindings(fields: list[str]) -> list[dict[str, Any]]:
    """Describe visible labels without granting any additional write target."""

    return [
        {
            "canonical_field": field,
            "visible_labels": list(_PUBLIC_FIELD_ALIASES.get(field, (field,))),
        }
        for field in fields
        if field in {*DESIGN_ACT_FIELDS, *STAGE_ACT_FIELDS, *EMVR_EDITABLE_FIELDS}
    ]


def _pending_answer_fields(
    stage: Stage,
    interaction_state: InteractionState,
    subject: str = "",
) -> list[str]:
    if subject in {
        *DESIGN_ACT_FIELDS,
        *STAGE_ACT_FIELDS,
        *EMVR_EDITABLE_FIELDS,
        *FACET_TO_DESIGN_FIELD,
    }:
        return [subject]
    catalog = (
        _EMVR_PENDING_ANSWER_FIELDS
        if interaction_state is InteractionState.EMVR_DIRECT
        else _GUIDED_PENDING_ANSWER_FIELDS
    )
    return list(catalog.get(stage, ()))


def _confirmation_answer_fields(
    stage: Stage,
    interaction_state: InteractionState,
) -> list[str]:
    catalog = (
        _EMVR_CONFIRMATION_FIELDS
        if interaction_state is InteractionState.EMVR_DIRECT
        else _GUIDED_CONFIRMATION_FIELDS
    )
    return list(catalog.get(stage, ()))


def _authoritative_emvr_update(
    raw_update: Any,
    acts: list[dict[str, Any]],
    compiled_acts: dict[str, Any],
    pending_action: dict[str, Any] | None,
) -> dict[str, Any]:
    """Restrict EMVR persistence to fields authorized by dialogue acts."""

    # The canonical dialogue acts are the write contract.  The model may also
    # provide a richer EMVR projection, but persistence must not depend on it
    # repeating the same edit in a second JSON branch.  Otherwise a perfectly
    # valid supplement updates stage_design_state while silently disappearing
    # from the EMVR report state.
    raw_update = raw_update if isinstance(raw_update, dict) else {}
    allowed_fields: set[str] = set()
    theory_allowed = False

    def authorize_target(target: str) -> None:
        nonlocal theory_allowed
        canonical = FACET_TO_DESIGN_FIELD.get(target, target)
        allowed_fields.update(
            _EMVR_FIELDS_BY_CANONICAL_FIELD.get(canonical, frozenset())
        )
        allowed_fields.update(
            _EMVR_FIELDS_BY_PENDING_SUBJECT.get(target, frozenset())
        )
        if canonical == "theoretical_framework" or target == Stage.THEORETICAL_FRAMEWORK.value:
            theory_allowed = True

    for act in acts:
        if not isinstance(act, dict):
            continue
        act_type = str(act.get("type") or "")
        target = str(act.get("target") or "")
        if act_type == "ANSWER_PENDING_QUESTION":
            canonical = FACET_TO_DESIGN_FIELD.get(target, target)
            if canonical in {*DESIGN_ACT_FIELDS, *STAGE_ACT_FIELDS}:
                authorize_target(canonical)
            elif target == Stage.THEORETICAL_FRAMEWORK.value:
                authorize_target("theoretical_framework")
        elif act_type in {"MODIFY_DESIGN_FIELD", "MODIFY_STAGE_FIELD"}:
            authorize_target(target)
        elif act_type == "MODIFY_EMVR_FIELD":
            allowed_fields.add(target)
        elif act_type == "MODIFY_COMPARISON":
            allowed_fields.add("comparison_cases")
        elif act_type in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"}:
            allowed_fields.add("experiment_brief")

    # CORRECT_ASSISTANT compiles nested field repairs into the same validated
    # update lists, so they receive exactly the same field authorization.
    for item in [
        *compiled_acts.get("design_updates", []),
        *compiled_acts.get("stage_field_updates", []),
    ]:
        if isinstance(item, dict):
            authorize_target(str(item.get("field") or ""))
    for item in compiled_acts.get("emvr_field_updates", []):
        if isinstance(item, dict):
            allowed_fields.add(str(item.get("field_id") or ""))

    # Canonical acts have already separated physical roles.  Their projected
    # targets must not be contaminated by the model's parallel EMVR snapshot
    # (for example, a theory explanation placed under required_behaviors).
    canonically_projected_fields = {
        field_id
        for item in [
            *compiled_acts.get("design_updates", []),
            *compiled_acts.get("stage_field_updates", []),
        ]
        if isinstance(item, dict)
        for field_id in _EMVR_FIELDS_BY_CANONICAL_FIELD.get(
            str(item.get("field") or ""), frozenset()
        )
    }
    if compiled_acts.get("comparison_updates"):
        # Comparison bundles are committed by the deterministic state machine.
        # Never trust a second model-authored comparison_cases snapshot, which
        # could disagree with the canonical CREATE/MODIFY/REJECT operation.
        canonically_projected_fields.add("comparison_cases")

    filtered: dict[str, Any] = {
        field: deepcopy(raw_update.get(field))
        for field in allowed_fields
        if field in EMVR_EDITABLE_FIELDS
        and field not in canonically_projected_fields
        and raw_update.get(field) not in (None, "", [], {})
    }
    field_updates = []
    for item in raw_update.get("field_updates", []):
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("field_id") or "")
        if (
            field_id in allowed_fields
            and field_id not in canonically_projected_fields
        ):
            field_updates.append(deepcopy(item))
    if field_updates:
        filtered["field_updates"] = field_updates

    # Back-project every validated field-level edit into the corresponding
    # EMVR fields when the richer model projection omitted it.  Stage-id
    # answers are intentionally excluded because their content can span more
    # than one field; only compiled, explicitly targeted updates are safe.
    projected_updates = list(field_updates)
    projected_signatures = {
        (
            str(item.get("field_id") or ""),
            str(item.get("operation") or "").upper(),
            json.dumps(
                item.get("value"), ensure_ascii=False, sort_keys=True, default=str
            ),
        )
        for item in projected_updates
        if isinstance(item, dict)
    }
    for item in [
        *compiled_acts.get("design_updates", []),
        *compiled_acts.get("stage_field_updates", []),
    ]:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("field") or "")
        operation = str(item.get("operation") or "").upper()
        if operation not in {"REPLACE", "MERGE", "CLEAR"}:
            continue
        for field_id in _EMVR_FIELDS_BY_CANONICAL_FIELD.get(
            canonical, frozenset()
        ):
            signature = (
                field_id,
                operation,
                json.dumps(
                    item.get("value"),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            )
            if (
                field_id not in allowed_fields
                or field_id not in EMVR_EDITABLE_FIELDS
                or signature in projected_signatures
            ):
                continue
            projected_updates.append(
                {
                    "field_id": field_id,
                    "operation": operation,
                    "value": deepcopy(item.get("value")),
                }
            )
            projected_signatures.add(signature)
    for item in compiled_acts.get("emvr_field_updates", []):
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("field_id") or "")
        operation = str(item.get("operation") or "").upper()
        if field_id not in EMVR_EDITABLE_FIELDS or operation not in {
            "REPLACE",
            "MERGE",
            "CLEAR",
        }:
            continue
        value = deepcopy(item.get("value"))
        signature = (
            field_id,
            operation,
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
        )
        if signature in projected_signatures:
            continue
        projected_updates.append(
            {"field_id": field_id, "operation": operation, "value": value}
        )
        projected_signatures.add(signature)
    if projected_updates:
        filtered["field_updates"] = projected_updates

    if theory_allowed:
        theory_links = []
        for item in raw_update.get("theory_links", []):
            if not isinstance(item, dict):
                continue
            support_fields = item.get("supports_design_fields", [])
            support_fields = (
                [
                    str(field)
                    for field in support_fields
                    if isinstance(field, str)
                    and field in _THEORY_SUPPORT_FIELDS
                ]
                if isinstance(support_fields, list)
                else []
            )
            if not support_fields:
                continue
            theory_links.append(
                {**deepcopy(item), "supports_design_fields": support_fields}
            )
        if theory_links:
            filtered["theory_links"] = theory_links
        permitted_relation_ids = {
            str(item.get("relation_id") or "")
            for item in theory_links
            if isinstance(item, dict)
        }
        theory_link_updates = []
        for item in raw_update.get("theory_link_updates", []):
            if not isinstance(item, dict):
                continue
            relation_id = str(item.get("relation_id") or "").strip().upper()
            operation = str(item.get("operation") or "").strip().upper()
            if operation == "REMOVE" and relation_id:
                theory_link_updates.append(
                    {"relation_id": relation_id, "operation": "REMOVE"}
                )
            elif operation == "ADD" and relation_id in permitted_relation_ids:
                link = next(
                    (
                        deepcopy(candidate)
                        for candidate in theory_links
                        if str(candidate.get("relation_id") or "").strip().upper()
                        == relation_id
                    ),
                    None,
                )
                theory_link_updates.append(
                    {
                        "relation_id": relation_id,
                        "operation": "ADD",
                        "link": link,
                    }
                )
        if theory_link_updates:
            filtered["theory_link_updates"] = theory_link_updates
    return filtered


class UserIntent(str, Enum):
    ANSWER_CURRENT_QUESTION = "ANSWER_CURRENT_QUESTION"
    ACCEPT_PREVIOUS_PROPOSAL = "ACCEPT_PREVIOUS_PROPOSAL"
    MODIFY_PREVIOUS_PROPOSAL = "MODIFY_PREVIOUS_PROPOSAL"
    REJECT_PREVIOUS_PROPOSAL = "REJECT_PREVIOUS_PROPOSAL"
    ADVANCE_STAGE = "ADVANCE_STAGE"
    REQUEST_MORE_EXAMPLES = "REQUEST_MORE_EXAMPLES"
    REQUEST_CURRENT_DESIGN_SUMMARY = "REQUEST_CURRENT_DESIGN_SUMMARY"
    ASK_COURSE_QUESTION = "ASK_COURSE_QUESTION"
    PROVIDE_FEEDBACK = "PROVIDE_FEEDBACK"
    REQUEST_DESIGN_REVIEW = "REQUEST_DESIGN_REVIEW"
    COMPARE_DESIGN_OPTIONS = "COMPARE_DESIGN_OPTIONS"
    MANAGE_DESIGN_VERSION = "MANAGE_DESIGN_VERSION"
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
OPEN_QUESTION_PENDING_TYPES = frozenset(
    {
        "ANSWER_IDEA_FACET",
        "ANSWER_STAGE_QUESTION",
        "ANSWER_EMVR_STAGE_QUESTION",
    }
)
CONFIRMATION_PENDING_TYPES = frozenset(
    {
        "CONFIRM_OR_MODIFY",
        "CONFIRM_STAGE_OR_MODIFY",
    }
)


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
    *,
    allow_exact_field_binding: bool | None = None,
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
    recoverable_confirmation = bool(
        pending.get("type") in CONFIRMATION_PENDING_TYPES
    )
    if normalized_candidate and (
        pending.get("type") in OPEN_QUESTION_PENDING_TYPES
        or recoverable_confirmation
    ):
        candidate_turns = pending.get("candidate_turns", [])
        candidate_turns = (
            [str(item).strip()[:2000] for item in candidate_turns if str(item).strip()]
            if isinstance(candidate_turns, list)
            else []
        )
        if normalized_candidate[:2000] not in candidate_turns:
            candidate_turns.append(normalized_candidate[:2000])
        pending["candidate_turns"] = candidate_turns[-4:]
        if allow_exact_field_binding is None:
            # Preserve the established EMVR recovery behavior for direct
            # callers. Guided binding is enabled only when the engine knows
            # the semantic service itself failed, not merely because the model
            # judged an ordinary answer unclear.
            allow_exact_field_binding = (
                session.interaction_state is InteractionState.EMVR_DIRECT
            )
        exact_field_binding = bool(
            allow_exact_field_binding and recoverable_pending_field(pending)
        )
        # For an exact field the most recent failed answer supersedes an
        # earlier candidate. This lets a student correct or complete a retry
        # before confirming it; accepting must never commit a stale first
        # attempt. Multi-field candidates remain unbound because copying them
        # into one field would recreate the mixed-message contamination this
        # recovery path is designed to avoid.
        if (
            not str(pending.get("candidate_answer") or "").strip()
            or exact_field_binding
        ):
            pending["candidate_answer"] = normalized_candidate[:2000]
            # A failed parse normally cannot authorize a field write.  The one
            # safe exception is an open question whose pending contract names
            # exactly one canonical field. The candidate is still not written
            # until a separate acceptance turn confirms that binding.
            pending["candidate_binding_authorized"] = exact_field_binding
            if exact_field_binding:
                pending["candidate_resolution"] = (
                    UserIntent.ANSWER_CURRENT_QUESTION.value
                )
            if recoverable_confirmation:
                pending["candidate_resolution"] = (
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
                )
    return deepcopy(pending)


def recover_repeated_pending_answer(
    resolved: dict[str, Any],
    pending_action: dict[str, Any] | None,
    user_message: str,
) -> dict[str, Any] | None:
    """Bind a repeated substantive answer to the exact open question.

    A semantic outage may leave the first answer as ``candidate_answer``.
    When the student then repeats essentially the same answer, that repetition
    is itself strong contextual confirmation.  Recover it as one structured
    pending-answer act instead of replaying the question again.  This compares
    the two complete utterances; it does not maintain a vocabulary of answer
    phrases and it never guesses a field when no open question exists.
    """

    if (
        not isinstance(resolved, dict)
        or resolved.get("intent") != UserIntent.UNCLEAR.value
        or not isinstance(pending_action, dict)
        or pending_action.get("type") not in OPEN_QUESTION_PENDING_TYPES
        or pending_action.get("candidate_binding_authorized") is not True
    ):
        return None
    candidate = str(pending_action.get("candidate_answer") or "").strip()
    current = user_message.strip()
    if not candidate or not current:
        return None

    def comparable(value: str) -> str:
        return re.sub(
            r"[\s，,。；;：:！!？?、（）()\-—\"'“”‘’]+",
            "",
            value,
        ).casefold()

    left = comparable(candidate)
    right = comparable(current)
    if min(len(left), len(right)) < 8:
        return None
    similarity = (
        1.0
        if left in right or right in left
        else SequenceMatcher(None, left, right, autojunk=False).ratio()
    )
    if similarity < 0.82:
        return None

    subject = str(pending_action.get("subject") or "").strip()
    if not subject:
        return None
    act = {
        "type": "ANSWER_PENDING_QUESTION",
        "target": subject,
        "operation": "REPLACE",
        "content": candidate,
        "confidence": max(0.9, similarity),
    }
    return resolved_intent(
        UserIntent.ANSWER_CURRENT_QUESTION,
        target=subject,
        resolved_value=candidate,
        confidence=max(0.9, similarity),
        source="SEMANTIC_CONTEXTUAL_REPEATED_ANSWER",
        dialogue_acts=[act],
        actions_authoritative=True,
    )


def hydrate_pending_action_from_history(
    session: DesignSession,
) -> dict[str, Any] | None:
    """Migrate a pre-upgrade conversation without exposing internal fields."""

    current = current_pending_action(session)
    if current is not None:
        if current.get("type") in {
            *OPEN_QUESTION_PENDING_TYPES,
            *CONFIRMATION_PENDING_TYPES,
        }:
            if not current.get("answer_fields"):
                current["answer_fields"] = (
                    _confirmation_answer_fields(
                        session.current_stage,
                        session.interaction_state,
                    )
                    if current.get("type") in CONFIRMATION_PENDING_TYPES
                    else _pending_answer_fields(
                        session.current_stage,
                        session.interaction_state,
                        str(current.get("subject") or ""),
                    )
                )
            if not current.get("editable_field_bindings"):
                current["editable_field_bindings"] = _editable_field_bindings(
                    current.get("answer_fields", [])
                )
            dialogue_state(session)["pending_action"] = deepcopy(current)
            set_pending_action_snapshot(session, current)
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
    from .idea_development import canonical_idea_pending_action

    canonical = canonical_idea_pending_action(session)
    if not isinstance(canonical, dict):
        return pending
    same_subject = bool(
        pending.get("type") == canonical.get("type")
        and pending.get("subject") == canonical.get("subject")
    )
    migrated = deepcopy(pending)
    preserved_proposal = (
        deepcopy(pending.get("proposal"))
        if same_subject and isinstance(pending.get("proposal"), dict)
        else {}
    )
    canonical_proposal = canonical.get("proposal", {})
    if isinstance(canonical_proposal, dict):
        preserved_proposal.update(deepcopy(canonical_proposal))
    migrated.update(deepcopy(canonical))
    migrated["proposal"] = preserved_proposal
    migrated["status"] = "PENDING"
    if same_subject:
        if str(pending.get("question") or "").strip():
            migrated["question"] = str(pending["question"]).strip()
    else:
        migrated["action_id"] = (
            f"action_{session.revision}_{uuid.uuid4().hex[:8]}"
        )
        migrated["repeat_count"] = 1
        migrated.pop("candidate_answer", None)
        migrated.pop("candidate_resolution", None)
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

    canonical = ensure_design_state(session)
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

    emvr_design = session.design_context.get("emvr_design", {})
    emvr_design = emvr_design if isinstance(emvr_design, dict) else {}
    emvr_requirements = merge_emvr_structured_requirements(emvr_design)
    direction = str(
        (
            emvr_requirements.get("experiment_brief")
            if session.interaction_state is InteractionState.EMVR_DIRECT
            else ""
        )
        or idea.get("main_direction")
        or idea.get("direction_summary")
        or idea.get("current_focus")
        or outline.get("core_phenomenon")
        or idea.get("original")
        or ""
    ).strip()
    learning_objectives = _find_payload_values(
        session,
        {
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        },
    )
    research_questions = _find_payload_values(
        session,
        {"main_research_question", "research_question"},
    )
    hypotheses = _find_payload_values(
        session,
        {"research_hypothesis", "expected_trend"},
    )
    unity_objects = _find_payload_values(
        session,
        {"object_name", "unity_objects"},
    )
    interactions = _find_payload_values(
        session,
        {"user_action", "student_interaction", "interactions"},
    )
    emvr_design = session.design_context.get("emvr_design", {})
    emvr_stage_inputs = (
        deepcopy(emvr_design.get("stage_inputs", {}))
        if isinstance(emvr_design, dict)
        and isinstance(emvr_design.get("stage_inputs"), dict)
        else {}
    )
    emvr_structured_requirements = (
        deepcopy(emvr_design.get("structured_requirements", {}))
        if isinstance(emvr_design, dict)
        and isinstance(emvr_design.get("structured_requirements"), dict)
        else {}
    )
    emvr_merged_requirements = merge_emvr_structured_requirements(emvr_design)
    guided_stage_inputs = session.design_context.get("guided_stage_inputs", {})
    guided_stage_inputs = (
        deepcopy(guided_stage_inputs)
        if isinstance(guided_stage_inputs, dict)
        else {}
    )
    last_summary = dialogue_state(session).get("last_presented_design_summary")
    stage_fields = stage_design_state_snapshot(session)
    pending = current_pending_action(session)
    topic_lock = topic_lock_snapshot(session)
    return {
        "research_direction": str(
            (
                emvr_merged_requirements.get("experiment_brief")
                if session.interaction_state is InteractionState.EMVR_DIRECT
                else ""
            )
            or canonical.get("research_object")
            or direction
        ),
        "direction_locked": idea.get("direction_locked") is True,
        "topic_lock": topic_lock,
        "current_edit_target": (
            str(pending.get("subject") or "") if isinstance(pending, dict) else ""
        ),
        "course_relationships": (
            str(canonical.get("course_relationship") or "")
            or deepcopy(
                outline.get("course_relationships")
                or idea.get("selected_course_relations")
                or []
            )
        ),
        "learning_objective": (
            str(canonical.get("learning_objective") or "")
            or facet_evidence("learning_objective")
            or ("；".join(learning_objectives) if learning_objectives else "")
        ),
        "learning_objectives": learning_objectives,
        "research_question": (
            str(canonical.get("research_question") or "")
            or facet_evidence("research_question")
            or (research_questions[0] if research_questions else "")
        ),
        "hypothesis": (
            str(canonical.get("hypothesis") or "")
            or facet_evidence("hypothesis")
            or ("；".join(hypotheses) if hypotheses else "")
        ),
        "conceptual_structure": (
            str(canonical.get("conceptual_structure") or "")
            or facet_evidence("conceptual_structure")
            or ("；".join(unity_objects) if unity_objects else "")
        ),
        "baseline_comparisons": baseline_comparisons_snapshot(session),
        "independent_variable": _find_payload_values(
            session,
            {"independent_variable", "adjustable_quantity_in_vr"},
        ) if not stage_fields["independent_variable"] else stage_fields["independent_variable"],
        "observations": _find_payload_values(
            session,
            {
                "dependent_variable",
                "observation_focus",
                "observable_quantity_in_vr",
                "observations",
                "calculated_outputs",
            },
        ) if not stage_fields["observations"] else stage_fields["observations"],
        "controlled_conditions": _find_payload_values(
            session,
            {"controlled_variables", "controlled_conditions", "reference_condition"},
        ) if not stage_fields["controlled_conditions"] else stage_fields["controlled_conditions"],
        "procedure_steps": _find_payload_values(
            session,
            {"procedure_steps", "reference_draft"},
        ) if not stage_fields["procedure_steps"] else stage_fields["procedure_steps"],
        "unity_objects": stage_fields["unity_objects"] or unity_objects,
        "interactions": stage_fields["interactions"] or interactions,
        "emvr_stage_inputs": emvr_stage_inputs,
        "emvr_structured_requirements": emvr_structured_requirements,
        "emvr_merged_requirements": emvr_merged_requirements,
        "guided_stage_inputs": guided_stage_inputs,
        "visualization_plan": _find_payload_values(
            session,
            {
                "visualization_layer",
                "measurement_interface",
                "trend_annotation",
            },
        ) if not stage_fields["visualization_plan"] else stage_fields["visualization_plan"],
        "result_interpretation": stage_fields["result_interpretation"],
        "design_rationale": stage_fields["design_rationale"],
        "design_value": stage_fields["design_value"],
        "limitations": _find_payload_values(
            session,
            {"limitations", "invalid_conditions", "parameter_limits"},
        ) if not stage_fields["limitations"] else stage_fields["limitations"],
        "student_summary": stage_fields["student_summary"],
        "stage_design_state": stage_fields,
        "resolved_decisions": deepcopy(
            session.design_context.get("resolved_decisions", {})
            if isinstance(session.design_context.get("resolved_decisions"), dict)
            else {}
        ),
        "idea_development": deepcopy(
            development
        ),
        "design_state": design_state_snapshot(session),
        "last_presented_design_summary": (
            deepcopy(last_summary) if isinstance(last_summary, dict) else None
        ),
        "stage_context_summary": build_stage_context_summary(session),
        "quality_review": deepcopy(
            session.design_context.get("quality_review", {})
            if isinstance(session.design_context.get("quality_review"), dict)
            else {}
        ),
        "recent_design_versions": [
            {
                "version_id": item.get("version_id"),
                "reason": item.get("reason"),
                "changed_fields": deepcopy(item.get("changed_fields", [])),
            }
            for item in (
                session.model_context.get("design_versions", [])[-5:]
                if isinstance(session.model_context.get("design_versions"), list)
                else []
            )
            if isinstance(item, dict)
        ],
        "mode_handoff": deepcopy(
            session.model_context.get("mode_handoff", {})
            if isinstance(session.model_context.get("mode_handoff"), dict)
            else {}
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
    if UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY.value not in allowed:
        allowed.append(UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY.value)
    normalized = {
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
    answer_fields = raw.get("answer_fields", [])
    if isinstance(answer_fields, list):
        normalized["answer_fields"] = list(
            dict.fromkeys(
                str(field).strip()
                for field in answer_fields
                if str(field).strip()
                in {
                    *DESIGN_ACT_FIELDS,
                    *STAGE_ACT_FIELDS,
                    *EMVR_EDITABLE_FIELDS,
                    *FACET_TO_DESIGN_FIELD,
                }
            )
        )
    normalized_answer_fields = normalized.get("answer_fields", [])
    if isinstance(normalized_answer_fields, list) and normalized_answer_fields:
        normalized["editable_field_bindings"] = _editable_field_bindings(
            normalized_answer_fields
        )
    candidate_answer = raw.get("candidate_answer")
    if isinstance(candidate_answer, str) and candidate_answer.strip():
        normalized["candidate_answer"] = candidate_answer.strip()[:2000]
        normalized["candidate_binding_authorized"] = bool(
            # Legacy sessions did not distinguish a system-authored reference
            # from raw text retained after a failed parse.  Defaulting legacy
            # candidates to unbound prevents an old ambiguous message from
            # becoming writable merely because the student later confirms it.
            raw.get("candidate_binding_authorized", False)
        )
    candidate_turns = raw.get("candidate_turns")
    if isinstance(candidate_turns, list):
        normalized["candidate_turns"] = list(
            dict.fromkeys(
                str(item).strip()[:2000]
                for item in candidate_turns
                if isinstance(item, str) and item.strip()
            )
        )[-4:]
    candidate_resolution = str(raw.get("candidate_resolution") or "").strip()
    if candidate_resolution in {
        UserIntent.ANSWER_CURRENT_QUESTION.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
    }:
        normalized["candidate_resolution"] = candidate_resolution
    interaction_state = str(raw.get("interaction_state") or "").strip()
    if interaction_state in {item.value for item in InteractionState}:
        normalized["interaction_state"] = interaction_state
    return normalized


def save_pending_action(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
) -> dict[str, Any] | None:
    """Persist the assistant's next expected decision without exposing it in chat."""

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
    if raw.get("type") in {
        *OPEN_QUESTION_PENDING_TYPES,
        *CONFIRMATION_PENDING_TYPES,
    } and not raw.get("answer_fields"):
        raw["answer_fields"] = (
            _confirmation_answer_fields(stage, session.interaction_state)
            if raw.get("type") in CONFIRMATION_PENDING_TYPES
            else _pending_answer_fields(
                stage,
                session.interaction_state,
                str(raw.get("subject") or ""),
            )
        )
    if not question and proposal is None:
        dialogue_state(session).pop("pending_action", None)
        set_pending_action_snapshot(session, None)
        return None
    pending = _normalize_pending_action(
        raw,
        stage=stage,
        revision=session.revision + 1,
        fallback_question=question,
        fallback_proposal=proposal,
    )
    if pending.get("type") in {
        *OPEN_QUESTION_PENDING_TYPES,
        *CONFIRMATION_PENDING_TYPES,
    } and not pending.get("answer_fields"):
        pending["answer_fields"] = (
            _confirmation_answer_fields(
                session.current_stage,
                session.interaction_state,
            )
            if pending.get("type") in CONFIRMATION_PENDING_TYPES
            else _pending_answer_fields(
                session.current_stage,
                session.interaction_state,
                str(pending.get("subject") or ""),
            )
        )
        pending["editable_field_bindings"] = _editable_field_bindings(
            pending["answer_fields"]
        )
    if stage is Stage.IDEA_BRAINSTORMING:
        pending = _migrate_legacy_idea_facet_pending(session, pending)
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
    set_pending_action_snapshot(session, pending)
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
    if selected_option_id and isinstance(_pending_action, dict):
        action_id = str(_pending_action.get("action_id") or "")
        if (
            action_id
            and selected_option_id == f"pending_accept::{action_id}"
            and str(_pending_action.get("candidate_answer") or "").strip()
            and _pending_action.get("candidate_binding_authorized") is True
        ):
            return resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                target=str(_pending_action.get("subject") or "") or None,
                confidence=1.0,
                source="EXPLICIT_PENDING_UI_ACTION",
            )
        if action_id and selected_option_id == f"pending_reference::{action_id}":
            return resolved_intent(
                UserIntent.REQUEST_MORE_EXAMPLES,
                target=str(_pending_action.get("subject") or "") or None,
                confidence=1.0,
                source="EXPLICIT_PENDING_UI_ACTION",
                semantic_updates={"control_actions": ["REQUEST_REFERENCE"]},
            )
        if selected_option_id.startswith(("pending_accept::", "pending_reference::")):
            # The page can retain an old button while another tab advances the
            # same design. Never reinterpret a stale internal action id as the
            # student's answer to the new pending question.
            return resolved_intent(
                UserIntent.UNCLEAR,
                target=str(_pending_action.get("subject") or "") or None,
                confidence=1.0,
                source="STALE_PENDING_UI_ACTION",
            )
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
    *,
    interaction_state: InteractionState | None = None,
) -> dict[str, Any]:
    """Conservative offline behavior when no semantic model is available."""

    compact = re.sub(r"[\s，,。；;！!？?]+", "", user_message)
    answer_fields = (
        pending_action.get("answer_fields", [])
        if isinstance(pending_action, dict)
        else []
    )
    if (
        interaction_state is InteractionState.EMVR_DIRECT
        and isinstance(answer_fields, list)
        and len(answer_fields) == 1
        and str(answer_fields[0])
        in {*BUILDER_REQUIREMENT_FIELDS, *EMVR_EDITABLE_FIELDS}
    ):
        field = str(answer_fields[0])
        return resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target=field,
            resolved_value=user_message.strip(),
            confidence=0.9,
            source="STRUCTURED_BUILDER_QUESTION_FALLBACK",
            semantic_updates={"pending_answer_status": "CLEAR"},
            dialogue_acts=[
                {
                    "type": "ANSWER_PENDING_QUESTION",
                    "target": field,
                    "operation": "REPLACE",
                    "content": user_message.strip(),
                    "confidence": 0.9,
                }
            ],
            actions_authoritative=True,
        )
    if len(compact) < 6:
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.3,
            source="CONSERVATIVE_FALLBACK",
        )
    if pending_action is None:
        # The rule-only first turn has no prior question or competing field to
        # mis-bind. Preserve the student's initial idea so the offline breadth
        # explorer can start; once a pending item exists, field-level semantic
        # parsing is required for every state write.
        return resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target=None,
            resolved_value=user_message.strip(),
            confidence=0.62,
            source="CONSERVATIVE_FALLBACK",
            semantic_updates={"pending_answer_status": "CLEAR"},
        )
    # This branch is used only when the configured generator has no semantic
    # resolver (the offline demo and its deterministic tests). It passes the
    # text to the stage generator as an opaque stage answer; API-backed modes
    # never use it for canonical field writes and instead require dialogue acts.
    fallback_user_intent = (
        UserIntent.MODIFY_PREVIOUS_PROPOSAL
        if interaction_state is InteractionState.EMVR_DIRECT
        and isinstance(pending_action, dict)
        and pending_action.get("type") == "CONFIRM_STAGE_OR_MODIFY"
        else UserIntent.ANSWER_CURRENT_QUESTION
    )
    return resolved_intent(
        fallback_user_intent,
        target=(
            str(pending_action.get("subject") or "") or None
            if isinstance(pending_action, dict)
            else None
        ),
        resolved_value=user_message.strip(),
        confidence=0.62,
        source="CONSERVATIVE_FALLBACK",
        semantic_updates={"pending_answer_status": "CLEAR"},
    )


def degraded_context_intent(
    session: DesignSession,
    user_message: str,
    pending_action: dict[str, Any] | None,
    carried_context: dict[str, Any],
    *,
    source: str = "SEMANTIC_DEGRADED",
) -> dict[str, Any]:
    """Preserve useful work when contextual semantic parsing is unavailable.

    This recovery is intentionally structural: it uses the current stage,
    topic lock, pending-action type and course-retrieval evidence. It does not
    inspect a list of conversational phrases. The original turn is retained
    so a later model call can revisit it instead of silently losing it.
    """

    message = user_message.strip()
    subject = (
        str(pending_action.get("subject") or "").strip()
        if isinstance(pending_action, dict)
        else ""
    )
    preserved_input = [
        {
            "type": "UNRESOLVED",
            "target": subject or session.current_stage.value,
            "content": message,
            "reason": "contextual_semantic_parser_unavailable",
        }
    ]
    entry_context = carried_context.get("intent_entry_context", {})
    entry_context = entry_context if isinstance(entry_context, dict) else {}
    topic_lock = carried_context.get("topic_lock", {})
    topic_lock = topic_lock if isinstance(topic_lock, dict) else {}
    direction_locked = bool(
        entry_context.get("direction_status") == "LOCKED"
        or topic_lock.get("locked") is True
    )
    evidence = carried_context.get("current_course_evidence", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    idea_development = carried_context.get("idea_development", {})
    idea_development = (
        idea_development if isinstance(idea_development, dict) else {}
    )
    has_course_evidence = any(
        isinstance(evidence.get(key), list) and bool(evidence.get(key))
        for key in ("lecture_concepts", "supplemental_concepts")
    )
    course_anchor = ""
    for evidence_key in ("lecture_concepts", "supplemental_concepts"):
        entries = evidence.get(evidence_key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            candidates: list[Any] = []
            if isinstance(entry, str):
                candidates.append(entry)
            elif isinstance(entry, dict):
                candidates.extend(
                    entry.get(key)
                    for key in ("title", "concept", "name")
                )
                concepts = entry.get("concepts", [])
                if isinstance(concepts, list):
                    candidates.extend(concepts)
            course_anchor = next(
                (
                    str(candidate).strip()[:300]
                    for candidate in candidates
                    if isinstance(candidate, str) and candidate.strip()
                ),
                "",
            )
            if course_anchor:
                break
        if course_anchor:
            break
    stage_one_entry = bool(
        session.interaction_state is InteractionState.GUIDED_DESIGN
        and session.current_stage is Stage.IDEA_BRAINSTORMING
        and not direction_locked
        and not idea_development
    )

    if stage_one_entry and has_course_evidence and course_anchor:
        # Retrieval proves that the turn touches course material, but a broad
        # match is not precise enough to become the student's research object.
        # Keep the original text for a later semantic pass and remain in
        # non-writing exploration instead of inventing or copying a direction.
        return resolved_intent(
            UserIntent.REQUEST_MORE_EXAMPLES,
            target="exploration_scenes",
            confidence=0.72,
            source=f"{source}_COURSE_EVIDENCE",
            semantic_updates={
                "no_direction": False,
                "course_scope_status": "COURSE_CONTENT",
                "pending_answer_status": "MISSING",
                "guidance_need": "CONCRETE_EXAMPLE",
            },
            unresolved_content=preserved_input,
        )

    if stage_one_entry:
        # Without a locked direction or positive course match, breadth
        # exploration is safer than inventing an out-of-scope judgment or
        # storing a help request as the research object.
        return resolved_intent(
            UserIntent.REQUEST_MORE_EXAMPLES,
            target="exploration_scenes",
            confidence=0.72,
            source=f"{source}_STAGE_ONE_ENTRY",
            semantic_updates={
                "no_direction": True,
                "course_scope_status": "COURSE_CONTENT",
                "pending_answer_status": "MISSING",
                "guidance_need": "CONCRETE_EXAMPLE",
            },
            unresolved_content=preserved_input,
        )

    pending_type = (
        str(pending_action.get("type") or "")
        if isinstance(pending_action, dict)
        else ""
    )
    if pending_type in OPEN_QUESTION_PENDING_TYPES:
        # A parser outage proves only that the original message exists; it
        # does not prove that the whole turn answers the visible question.
        # Keep the pending item open and retain the raw turn for one local
        # clarification.  In particular, never use the entire mixed message
        # as a facet value merely because an open question is visible.
        return resolved_intent(
            UserIntent.UNCLEAR,
            target=subject or session.current_stage.value,
            resolved_value=None,
            confidence=0.62,
            source=f"{source}_OPEN_QUESTION_LOCAL_CLARIFICATION",
            semantic_updates={"pending_answer_status": "MISSING"},
            unresolved_content=preserved_input,
        )

    # A confirmation or unbound control message cannot be executed safely
    # without semantics. Keep the text and clarify only the active decision.
    return resolved_intent(
        UserIntent.UNCLEAR,
        target=subject or None,
        resolved_value=None,
        confidence=0.62,
        source=f"{source}_LOCAL_CLARIFICATION",
        unresolved_content=preserved_input,
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
    dialogue_acts: list[dict[str, Any]] | None = None,
    unresolved_content: list[dict[str, str]] | None = None,
    task_plan: dict[str, Any] | None = None,
    actions_authoritative: bool = False,
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
        "dialogue_acts": deepcopy(dialogue_acts or []),
        "unresolved_content": deepcopy(unresolved_content or []),
        "task_plan": deepcopy(task_plan or {}),
        "actions_authoritative": bool(actions_authoritative),
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
    pending_type = (
        str(pending_action.get("type") or "")
        if isinstance(pending_action, dict)
        else ""
    )
    dialogue_acts, unresolved_acts = normalize_dialogue_acts(
        raw.get("dialogue_acts"),
        pending_action=pending_action,
    )
    preserved_unresolved = [
        {
            "type": "UNRESOLVED",
            "target": str(item.get("target") or "")[:120],
            "content": str(item.get("content") or "")[:1200],
            "reason": str(item.get("reason") or "")[:160],
        }
        for item in raw.get("unresolved_content", [])
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ] if isinstance(raw.get("unresolved_content"), list) else []
    actions_authoritative = raw.get("actions_authoritative") is True
    raw_acts_supplied = isinstance(raw.get("dialogue_acts"), list)
    compiled_acts = compile_dialogue_acts(
        dialogue_acts,
        pending_action=pending_action,
    )
    task_plan = build_turn_task_plan(dialogue_acts, unresolved_acts)
    authoritative_state_acts = False
    if dialogue_acts:
        merged_updates = (
            deepcopy(raw.get("semantic_updates"))
            if isinstance(raw.get("semantic_updates"), dict)
            else {}
        )
        # The action array is the only model-authored state-write contract.
        # Compatibility summary fields may still describe the turn, but they
        # cannot add a second, potentially contradictory set of field writes.
        for update_key in (
            "design_updates",
            "facet_updates",
            "stage_field_updates",
            "emvr_field_updates",
            "comparison_updates",
        ):
            merged_updates[update_key] = deepcopy(
                compiled_acts.get(update_key, [])
            )
        if actions_authoritative:
            merged_updates["emvr_design_update"] = _authoritative_emvr_update(
                merged_updates.get("emvr_design_update"),
                dialogue_acts,
                compiled_acts,
                pending_action,
            ) or None
        for value_key in (
            "student_questions",
            "feedback_items",
            "unresolved_content",
            "control_actions",
        ):
            existing = (
                []
                if actions_authoritative
                else merged_updates.get(value_key, [])
            )
            existing = existing if isinstance(existing, list) else []
            merged_updates[value_key] = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in [
                        *existing,
                        *compiled_acts.get(value_key, []),
                        *(
                            [
                                str(item.get("content") or "").strip()
                                for item in unresolved_acts
                                if isinstance(item, dict)
                                and str(item.get("content") or "").strip()
                            ]
                            if value_key == "unresolved_content"
                            else []
                        ),
                    ]
                    if str(item).strip()
                )
            )
        for structured_key in (
            "correction_items",
            "quality_review_requests",
            "option_comparison_requests",
            "version_requests",
        ):
            existing = (
                []
                if actions_authoritative
                else merged_updates.get(structured_key, [])
            )
            existing = existing if isinstance(existing, list) else []
            compiled = compiled_acts.get(structured_key, [])
            compiled = compiled if isinstance(compiled, list) else []
            merged_updates[structured_key] = [*existing, *deepcopy(compiled)][:16]
        if compiled_acts.get("answered_pending"):
            merged_updates["pending_answer_status"] = "CLEAR"
        controls = set(compiled_acts.get("control_actions", []))
        if actions_authoritative:
            merged_updates["interaction_state_request"] = None
        if pending_action and {"ACCEPT", "REJECT"} & controls:
            # Acceptance/rejection is an independent resolution of the open
            # proposal even when the same message also edits another field.
            merged_updates["pending_answer_status"] = "CLEAR"
        if "SET_EMVR_MODE" in controls:
            merged_updates["interaction_state_request"] = (
                InteractionState.EMVR_DIRECT.value
            )
        elif "SET_GUIDED_MODE" in controls:
            merged_updates["interaction_state_request"] = (
                InteractionState.GUIDED_DESIGN.value
            )
        state_acts = any(
            compiled_acts.get(key)
            for key in (
                "design_updates",
                "facet_updates",
                "stage_field_updates",
                "emvr_field_updates",
                "comparison_updates",
            )
        )
        stage_one_direction_content = bool(
            merged_updates.get("selected_option_ids")
            or str(merged_updates.get("stage_one_direction_detail") or "").strip()
            or merged_updates.get("stage_one_scene_response")
            == "SELECT_OR_DEVELOP"
        )
        stage_one_scene_response = str(
            merged_updates.get("stage_one_scene_response") or "NONE"
        )
        authoritative_state_acts = bool(actions_authoritative and state_acts)
        if {"SET_EMVR_MODE", "SET_GUIDED_MODE"} & controls:
            intent = UserIntent.SET_INTERACTION_STATE.value
        elif {"REQUEST_NEW_TOPIC", "NEW_TOPIC_CONTENT"} & controls:
            intent = UserIntent.NEW_TOPIC.value
        elif "RETURN" in controls and not state_acts:
            intent = UserIntent.RETURN_TO_PREVIOUS_POINT.value
        elif "REQUEST_SUMMARY" in controls and not state_acts:
            intent = UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY.value
        elif (
            "REQUEST_REFERENCE" in controls
            and not state_acts
            and not stage_one_direction_content
            and stage_one_scene_response != "SELECT_OR_DEVELOP"
        ):
            intent = UserIntent.REQUEST_MORE_EXAMPLES.value
        elif "ADVANCE" in controls and not state_acts:
            intent = UserIntent.ADVANCE_STAGE.value
        elif "REJECT" in controls and not state_acts:
            intent = UserIntent.REJECT_PREVIOUS_PROPOSAL.value
        elif "ACCEPT" in controls and not state_acts:
            intent = UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        elif state_acts or stage_one_direction_content:
            intent = (
                UserIntent.ANSWER_CURRENT_QUESTION.value
                if compiled_acts.get("answered_pending")
                or stage_one_direction_content
                else UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
            )
            if "ADVANCE" in controls:
                raw = {**raw, "advance_requested": True}
            elif (
                "ACCEPT" in controls
                and isinstance(pending_action, dict)
                and pending_action.get("advance_on_accept") is True
            ):
                raw = {**raw, "advance_requested": True}
        elif compiled_acts.get("version_requests"):
            intent = UserIntent.MANAGE_DESIGN_VERSION.value
        elif compiled_acts.get("option_comparison_requests"):
            intent = UserIntent.COMPARE_DESIGN_OPTIONS.value
        elif compiled_acts.get("quality_review_requests"):
            intent = UserIntent.REQUEST_DESIGN_REVIEW.value
        elif compiled_acts.get("student_questions"):
            intent = UserIntent.ASK_COURSE_QUESTION.value
        elif compiled_acts.get("feedback_items"):
            intent = UserIntent.PROVIDE_FEEDBACK.value
        # Prefer experiment content over questions and feedback regardless of
        # the order in which the model listed the acts.  This keeps a mixed
        # turn such as "you misunderstood me; replace the research question"
        # from carrying the correction sentence into later design prompts.
        primary_content = raw.get("resolved_value")
        for preferred_type in (
            "ANSWER_PENDING_QUESTION",
            "MODIFY_DESIGN_FIELD",
            "MODIFY_STAGE_FIELD",
            "MODIFY_EMVR_FIELD",
            "MODIFY_COMPARISON",
            "NEW_TOPIC_CONTENT",
            "NEW_TOPIC",
            "ASK_COURSE_QUESTION",
            "CORRECT_ASSISTANT",
        ):
            selected_content = next(
                (
                    deepcopy(act.get("content"))
                    for act in dialogue_acts
                    if act.get("type") == preferred_type
                    and act.get("content") not in (None, "", [], {})
                ),
                None,
            )
            if selected_content is not None:
                primary_content = selected_content
                break
        raw = {
            **raw,
            "intent": intent,
            "target": str(
                next(
                    (
                        act.get("target")
                        for act in dialogue_acts
                        if act.get("type")
                        in {
                            "ANSWER_PENDING_QUESTION",
                            "MODIFY_DESIGN_FIELD",
                            "MODIFY_STAGE_FIELD",
                            "MODIFY_COMPARISON",
                            "ASK_COURSE_QUESTION",
                            "CORRECT_ASSISTANT",
                            "NEW_TOPIC",
                        }
                        if str(act.get("target") or "").strip()
                    ),
                    raw.get("target") or "",
                )
            ),
            "resolved_value": primary_content,
            "semantic_updates": merged_updates,
            "source": "SEMANTIC_MULTI_ACT",
        }
    elif raw_acts_supplied and (unresolved_acts or actions_authoritative):
        # The action array is the authoritative semantic result.  If every
        # proposed action is invalid—or the model returned no executable
        # action—do not execute a contradictory legacy outer intent or copy
        # the complete student message into the current field.
        intent = UserIntent.UNCLEAR.value
        raw = {
            **raw,
            "intent": intent,
            "resolved_value": None,
            "semantic_updates": {},
            "source": "SEMANTIC_INVALID_ACTIONS",
        }
    is_stage_confirmation = bool(
        pending_type in CONFIRMATION_PENDING_TYPES
    )
    is_emvr_confirmation = bool(
        is_stage_confirmation
        and isinstance(pending_action, dict)
        and pending_action.get("interaction_state")
        == InteractionState.EMVR_DIRECT.value
    )
    # A substantive response to "review this draft and add anything missing"
    # is a modification even if the semantic service used the more general
    # ANSWER_CURRENT_QUESTION label.  Normalize the structured intent from the
    # pending action instead of inspecting the student's wording.
    if (
        intent == UserIntent.ANSWER_CURRENT_QUESTION.value
        and is_emvr_confirmation
    ):
        intent = UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
        raw = {**raw, "source": "SEMANTIC_CONFIRMATION_CONTENT"}
    allowed = set(pending_action.get("allowed_intents", [])) if pending_action else set(ALL_INTENTS)
    confirms_saved_candidate = bool(
        intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        and isinstance(pending_action, dict)
        and pending_action.get("type")
        in OPEN_QUESTION_PENDING_TYPES
        and str(pending_action.get("candidate_answer") or "").strip()
        and pending_action.get("candidate_binding_authorized") is True
    )
    requests_reference_for_open_question = bool(
        intent == UserIntent.REQUEST_MORE_EXAMPLES.value
        and isinstance(pending_action, dict)
        and pending_action.get("type")
        in OPEN_QUESTION_PENDING_TYPES
    )
    if (
        intent not in allowed
        and not authoritative_state_acts
        and not confirms_saved_candidate
        and not requests_reference_for_open_question
        and intent not in {
        UserIntent.NEW_TOPIC.value,
        UserIntent.RETURN_TO_PREVIOUS_POINT.value,
        UserIntent.SET_INTERACTION_STATE.value,
        UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY.value,
        UserIntent.ASK_COURSE_QUESTION.value,
        UserIntent.PROVIDE_FEEDBACK.value,
        UserIntent.REQUEST_DESIGN_REVIEW.value,
        UserIntent.COMPARE_DESIGN_OPTIONS.value,
        UserIntent.MANAGE_DESIGN_VERSION.value,
        }
    ):
        intent = UserIntent.UNCLEAR.value
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if dialogue_acts and confidence < 0.55:
        confidence = max(
            (float(item.get("confidence") or 0.0) for item in dialogue_acts),
            default=confidence,
        )
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
        intent == UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
        and is_stage_confirmation
        and not actions_authoritative
        and not dialogue_acts
        and resolved_value in (None, "", [], {})
        and isinstance(pending_action, dict)
        and str(pending_action.get("candidate_answer") or "").strip()
        and pending_action.get("candidate_binding_authorized") is True
    ):
        resolved_value = str(pending_action["candidate_answer"]).strip()
        source = "CONFIRMED_PENDING_MODIFICATION"
    if (
        intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        and isinstance(pending_action, dict)
        and pending_action.get("type")
        in OPEN_QUESTION_PENDING_TYPES
        and str(pending_action.get("candidate_answer") or "").strip()
        and pending_action.get("candidate_binding_authorized") is True
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
        recovered_field = recoverable_pending_field(pending_action)
        if recovered_field and not any(
            isinstance(act, dict)
            and act.get("type") == "ANSWER_PENDING_QUESTION"
            and act.get("target") == recovered_field
            for act in dialogue_acts
        ):
            recovered_act = {
                "type": "ANSWER_PENDING_QUESTION",
                "target": recovered_field,
                "operation": "REPLACE",
                "content": candidate_answer,
                "confidence": 1.0,
                "semantic_key": f"confirmed_pending:{recovered_field}",
            }
            dialogue_acts = [*dialogue_acts, recovered_act]
            # This synthetic act is created after the model-authored acts were
            # compiled, so compile it independently and merge only its
            # validated field-level operations. This keeps the canonical state,
            # EMVR projection, version history and UI change echo in sync.
            recovered_compiled = compile_dialogue_acts(
                [recovered_act],
                pending_action=pending_action,
            )
            for update_key in (
                "design_updates",
                "facet_updates",
                "stage_field_updates",
                "emvr_field_updates",
            ):
                existing = semantic_updates.get(update_key, [])
                existing = deepcopy(existing) if isinstance(existing, list) else []
                additions = recovered_compiled.get(update_key, [])
                if isinstance(additions, list):
                    for addition in additions:
                        if (
                            update_key == "facet_updates"
                            and isinstance(addition, dict)
                            and any(
                                isinstance(item, dict)
                                and item.get("facet_id") == addition.get("facet_id")
                                and item.get("status") == addition.get("status")
                                for item in existing
                            )
                        ):
                            continue
                        existing.append(deepcopy(addition))
                if existing:
                    semantic_updates[update_key] = existing
            projected = (
                _authoritative_emvr_update(
                    {},
                    [recovered_act],
                    recovered_compiled,
                    pending_action,
                )
                if pending_action.get("interaction_state")
                == InteractionState.EMVR_DIRECT.value
                else {}
            )
            if projected:
                existing_emvr = semantic_updates.get("emvr_design_update")
                existing_emvr = (
                    deepcopy(existing_emvr)
                    if isinstance(existing_emvr, dict)
                    else {}
                )
                existing_fields = existing_emvr.get("field_updates", [])
                existing_fields = (
                    deepcopy(existing_fields)
                    if isinstance(existing_fields, list)
                    else []
                )
                new_fields = projected.get("field_updates", [])
                if isinstance(new_fields, list):
                    existing_fields.extend(deepcopy(new_fields))
                for key, value in projected.items():
                    if key != "field_updates":
                        existing_emvr[key] = deepcopy(value)
                if existing_fields:
                    existing_emvr["field_updates"] = existing_fields
                semantic_updates["emvr_design_update"] = existing_emvr
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
                and pending_action.get("type")
                in {"ANSWER_STAGE_QUESTION", "ANSWER_EMVR_STAGE_QUESTION"}
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
        dialogue_acts=dialogue_acts,
        unresolved_content=[*unresolved_acts, *preserved_unresolved],
        task_plan=task_plan,
        actions_authoritative=actions_authoritative,
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
    facet_updates: list[dict[str, Any]] = []
    for item in raw.get("facet_updates", []) if isinstance(raw.get("facet_updates"), list) else []:
        if not isinstance(item, dict):
            continue
        facet_id = str(item.get("facet_id") or "")
        status = str(item.get("status") or "").upper()
        if facet_id in _IDEA_FACET_IDS and status in {"CLEAR", "MISSING"}:
            normalized_facet = {"facet_id": facet_id, "status": status}
            operation = str(item.get("operation") or "").upper()
            if status == "CLEAR" and operation in {"MERGE", "REPLACE"}:
                normalized_facet["operation"] = operation
            if status == "CLEAR" and item.get("value") not in (None, "", [], {}):
                normalized_facet["value"] = deepcopy(item.get("value"))
            facet_updates.append(normalized_facet)
    design_updates: list[dict[str, Any]] = []
    raw_design_updates = raw.get("design_updates", [])
    for item in raw_design_updates if isinstance(raw_design_updates, list) else []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("field_id") or "")
        operation = str(item.get("operation") or "").upper()
        if field not in DESIGN_ACT_FIELDS or operation not in {"MERGE", "REPLACE", "CLEAR"}:
            continue
        normalized_update: dict[str, Any] = {
            "field": field,
            "operation": operation,
            "value": deepcopy(item.get("value")),
        }
        if str(item.get("update_id") or "").strip():
            normalized_update["update_id"] = str(item["update_id"])[:100]
        if str(item.get("semantic_key") or "").strip():
            normalized_update["semantic_key"] = str(item["semantic_key"])[:180]
        if str(item.get("provenance") or "").strip():
            normalized_update["provenance"] = str(item["provenance"])[:80]
        design_updates.append(normalized_update)
    stage_field_updates: list[dict[str, Any]] = []
    raw_stage_updates = raw.get("stage_field_updates", [])
    for item in raw_stage_updates if isinstance(raw_stage_updates, list) else []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("field_id") or "")
        operation = str(item.get("operation") or "").upper()
        if field not in STAGE_ACT_FIELDS or operation not in {
            "MERGE",
            "REPLACE",
            "CLEAR",
        }:
            continue
        normalized_update = {
            "field": field,
            "operation": operation,
            "value": deepcopy(item.get("value")),
        }
        if str(item.get("update_id") or "").strip():
            normalized_update["update_id"] = str(item["update_id"])[:100]
        if str(item.get("semantic_key") or "").strip():
            normalized_update["semantic_key"] = str(item["semantic_key"])[:180]
        if str(item.get("provenance") or "").strip():
            normalized_update["provenance"] = str(item["provenance"])[:80]
        stage_field_updates.append(normalized_update)
    comparison_updates: list[dict[str, Any]] = []
    for item in raw.get("comparison_updates", []) if isinstance(raw.get("comparison_updates"), list) else []:
        if not isinstance(item, dict):
            continue
        comparison_id = str(item.get("comparison_id") or "")[:80]
        action = str(item.get("action") or "").upper()
        cases = item.get("cases", [])
        if (
            comparison_id and action in {"ACCEPT", "MODIFY", "REJECT"}
        ) or action == "CREATE":
            normalized_comparison = {
                "comparison_id": comparison_id,
                "action": action,
                "cases": [str(case)[:160] for case in cases if isinstance(case, str)][:12]
                if isinstance(cases, list)
                else [],
            }
            title = str(item.get("title") or "").strip()[:240]
            if title:
                normalized_comparison["title"] = title
            if item.get("merge_with_existing") is True:
                normalized_comparison["merge_with_existing"] = True
            case_refs = item.get("case_refs", [])
            if isinstance(case_refs, list):
                normalized_comparison["case_refs"] = list(
                    dict.fromkeys(
                        str(ref).strip()[:140]
                        for ref in case_refs
                        if isinstance(ref, str) and ref.strip()
                    )
                )[:12]
            renames: list[dict[str, str]] = []
            for rename in item.get("renames", []) \
                if isinstance(item.get("renames"), list) else []:
                if not isinstance(rename, dict):
                    continue
                case_ref = str(rename.get("case_ref") or "").strip()[:140]
                label = str(rename.get("label") or "").strip()[:160]
                if case_ref and label:
                    renames.append({"case_ref": case_ref, "label": label})
            if renames:
                normalized_comparison["renames"] = renames
            new_cases = item.get("new_cases", [])
            if isinstance(new_cases, list):
                normalized_comparison["new_cases"] = [
                    str(case).strip()[:160]
                    for case in new_cases
                    if isinstance(case, str) and case.strip()
                ][:12]
            if item.get("replace_all") is True:
                normalized_comparison["replace_all"] = True
            semantic_key = str(item.get("semantic_key") or "").strip()[:180]
            if semantic_key:
                normalized_comparison["semantic_key"] = semantic_key
            raw_case_keys = item.get("case_semantic_keys", {})
            if isinstance(raw_case_keys, dict):
                normalized_comparison["case_semantic_keys"] = {
                    str(label).strip()[:160]: str(key).strip()[:180]
                    for label, key in raw_case_keys.items()
                    if str(label).strip() and str(key).strip()
                }
            comparison_updates.append(normalized_comparison)
    requested_state = str(raw.get("interaction_state_request") or "").upper()
    course_scope_status = str(raw.get("course_scope_status") or "").upper()
    stage_one_direction_detail = str(
        raw.get("stage_one_direction_detail") or ""
    ).strip()[:1200]
    stage_one_scene_response = str(
        raw.get("stage_one_scene_response") or "NONE"
    ).strip().upper()
    if stage_one_scene_response not in {
        "SELECT_OR_DEVELOP",
        "PROVIDE_BROAD_TOPIC",
        "REQUEST_NEW_BATCH",
        "NONE",
    }:
        stage_one_scene_response = "NONE"
    emvr_design_update = normalize_emvr_design_update(raw.get("emvr_design_update"))
    from .design_quality import normalize_quality_assessment

    quality_assessment = normalize_quality_assessment(raw.get("quality_assessment"))
    guidance_need = str(raw.get("guidance_need") or "").upper()
    if guidance_need not in {
        "BRIEF_HINT",
        "CONCRETE_EXAMPLE",
        "REFERENCE_DRAFT",
        "FORMULA_EXPLANATION",
        "DESIGN_REVIEW",
        "OPTION_COMPARISON",
    }:
        guidance_need = None
    def normalized_text_list(key: str) -> list[str]:
        value = raw.get(key, [])
        if not isinstance(value, list):
            return []
        return list(
            dict.fromkeys(
                str(item).strip()[:1200]
                for item in value
                if isinstance(item, str) and item.strip()
            )
        )[:12]

    return {
        "selected_option_ids": selected_option_ids,
        "no_direction": raw.get("no_direction") is True,
        "facet_updates": facet_updates,
        "design_updates": design_updates,
        "stage_field_updates": stage_field_updates,
        "comparison_updates": comparison_updates,
        "student_questions": normalized_text_list("student_questions"),
        "feedback_items": normalized_text_list("feedback_items"),
        "correction_items": [
            deepcopy(item)
            for item in raw.get("correction_items", [])
            if isinstance(item, dict)
        ][:12]
        if isinstance(raw.get("correction_items"), list)
        else [],
        "quality_review_requests": [
            deepcopy(item)
            for item in raw.get("quality_review_requests", [])
            if isinstance(item, dict)
        ][:8]
        if isinstance(raw.get("quality_review_requests"), list)
        else [],
        "option_comparison_requests": [
            deepcopy(item)
            for item in raw.get("option_comparison_requests", [])
            if isinstance(item, (dict, list))
        ][:8]
        if isinstance(raw.get("option_comparison_requests"), list)
        else [],
        "version_requests": [
            deepcopy(item)
            for item in raw.get("version_requests", [])
            if isinstance(item, dict)
        ][:8]
        if isinstance(raw.get("version_requests"), list)
        else [],
        "unresolved_content": normalized_text_list("unresolved_content"),
        "control_actions": list(
            dict.fromkeys(
                str(item).upper()
                for item in raw.get("control_actions", [])
                if isinstance(item, str)
                and str(item).upper()
                in {
                    *CONTROL_TARGETS,
                    "REQUEST_REFERENCE",
                    "REQUEST_SUMMARY",
                    "NEW_TOPIC",
                }
            )
        )
        if isinstance(raw.get("control_actions"), list)
        else [],
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
        # This is the substantive idea expressed alongside (or instead of)
        # an A/B/C scene reference.  Keeping it separate lets the state
        # machine accept "I choose A because I want to compare ..." as both
        # a direction choice and a useful design contribution, without
        # guessing from message length or topic keywords.
        "stage_one_direction_detail": stage_one_direction_detail or None,
        # Stage-one scene handling is deliberately separate from the generic
        # reference intent.  A substantive continuation and a request for a
        # fresh A/B/C batch are different dialogue acts even when both mention
        # the displayed examples.  The state machine consumes this semantic
        # distinction instead of inferring it from message length or words.
        "stage_one_scene_response": stage_one_scene_response,
        # A locked direction may be replaced only when the semantic resolver
        # confirms that the student explicitly abandoned or replaced it.
        "topic_change_explicit": raw.get("topic_change_explicit") is True,
        "emvr_design_update": emvr_design_update or None,
        "quality_assessment": quality_assessment or None,
        "guidance_need": guidance_need,
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
            or semantic_updates.get("design_updates")
            or semantic_updates.get("stage_field_updates")
            or semantic_updates.get("student_questions")
            or semantic_updates.get("feedback_items")
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
    if pending_type in {"ANSWER_STAGE_QUESTION", "ANSWER_EMVR_STAGE_QUESTION"}:
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
        or pending_action.get("type")
        not in {"ANSWER_STAGE_QUESTION", "ANSWER_EMVR_STAGE_QUESTION"}
    ):
        return False
    status = (
        semantic_updates.get("pending_answer_status")
        if isinstance(semantic_updates, dict)
        else None
    )
    return status not in {"CLEAR", "MISSING"}


def _normalized_evidence_text(value: str) -> str:
    return "".join(value.split()).casefold()


def _comparison_case_catalog(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Give existing semantic cases stable turn-local identities."""

    comparison_id = str(item.get("comparison_id") or "")
    recommended = [
        str(case).strip()
        for case in item.get("recommended_cases", item.get("cases", []))
        if str(case).strip()
    ]
    current = [
        str(case).strip() for case in item.get("cases", []) if str(case).strip()
    ]
    aliases = item.get("case_aliases", {})
    aliases = aliases if isinstance(aliases, dict) else {}
    catalog: list[dict[str, Any]] = []
    known: set[str] = set()
    for index, label in enumerate(recommended, start=1):
        catalog.append(
            {
                "case_ref": f"{comparison_id}:case:{index}",
                "label": label,
                "canonical": True,
                "aliases": [
                    str(alias) for alias in aliases.get(label, []) if str(alias).strip()
                ],
            }
        )
        known.add(label)
    extra_index = 1
    for label in current:
        if label in known:
            continue
        catalog.append(
            {
                "case_ref": f"{comparison_id}:custom:{extra_index}",
                "label": label,
                "canonical": False,
                "aliases": [],
            }
        )
        known.add(label)
        extra_index += 1
    return catalog


def _semantic_case_identity(
    label: str,
    semantic_keys: dict[str, Any] | None = None,
) -> str:
    keys = semantic_keys if isinstance(semantic_keys, dict) else {}
    supplied = str(keys.get(label) or "").strip().casefold()
    return supplied or _normalized_evidence_text(label)


def _deduplicate_cases_by_semantics(
    cases: list[str],
    semantic_keys: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Keep one display label for each model-canonicalized physical case."""

    result: list[str] = []
    result_keys: dict[str, str] = {}
    seen: set[str] = set()
    for case in cases:
        label = str(case).strip()
        if not label:
            continue
        identity = _semantic_case_identity(label, semantic_keys)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(label)
        result_keys[label] = identity
    return result, result_keys


def _apply_comparison_updates(
    session: DesignSession,
    updates: Any,
    user_message: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(updates, list):
        return []
    comparisons = baseline_comparisons_snapshot(session)
    applied: list[dict[str, Any]] = []
    by_id = {
        str(item.get("comparison_id")): item
        for item in comparisons
        if isinstance(item, dict) and str(item.get("comparison_id") or "")
    }
    by_semantic_key = {
        str(item.get("semantic_key") or "").strip().casefold(): item
        for item in comparisons
        if isinstance(item, dict) and str(item.get("semantic_key") or "").strip()
    }
    for update in updates:
        if not isinstance(update, dict):
            continue
        action = str(update.get("action") or "").upper()
        if action == "CREATE":
            message_evidence = _normalized_evidence_text(user_message)
            candidate_cases = [
                str(case).strip()
                for key in ("new_cases", "cases")
                for case in (
                    update.get(key, [])
                    if isinstance(update.get(key), list)
                    else []
                )
                if str(case).strip()
            ]
            supported_cases = list(
                dict.fromkeys(
                    case
                    for case in candidate_cases
                    if message_evidence
                    and _normalized_evidence_text(case) in message_evidence
                )
            )
            if not supported_cases:
                continue
            case_semantic_keys = (
                update.get("case_semantic_keys")
                if isinstance(update.get("case_semantic_keys"), dict)
                else {}
            )
            supported_cases, normalized_case_keys = _deduplicate_cases_by_semantics(
                supported_cases,
                case_semantic_keys,
            )
            comparison_semantic_key = str(
                update.get("semantic_key") or ""
            ).strip().casefold()
            identity_material = comparison_semantic_key or "|".join(
                sorted(normalized_case_keys.values())
            )
            comparison_id = "student_" + uuid.uuid5(
                uuid.NAMESPACE_URL,
                identity_material,
            ).hex[:16]
            title = str(update.get("title") or "").strip()
            if not (
                title
                and _normalized_evidence_text(title) in message_evidence
            ):
                title = "学生补充的基础比较"
            existing = by_semantic_key.get(comparison_semantic_key) \
                if comparison_semantic_key else None
            existing = existing or by_id.get(comparison_id)
            if existing is None:
                existing = {
                    "comparison_id": comparison_id,
                    "title": title,
                    "recommended_cases": list(supported_cases),
                    "cases": list(supported_cases),
                    "adoption_status": "MODIFIED",
                    "semantic_key": comparison_semantic_key,
                    "case_semantic_keys": normalized_case_keys,
                }
                comparisons.append(existing)
                by_id[comparison_id] = existing
                if comparison_semantic_key:
                    by_semantic_key[comparison_semantic_key] = existing
                applied.append(
                    {
                        "comparison_id": comparison_id,
                        "action": "CREATE",
                        "cases": list(supported_cases),
                    }
                )
            else:
                before = deepcopy(existing)
                combined_keys = {
                    **(
                        existing.get("case_semantic_keys", {})
                        if isinstance(existing.get("case_semantic_keys"), dict)
                        else {}
                    ),
                    **normalized_case_keys,
                }
                existing["cases"], existing["case_semantic_keys"] = (
                    _deduplicate_cases_by_semantics(
                        [
                            *[
                                str(case)
                                for case in existing.get("cases", [])
                                if str(case).strip()
                            ],
                            *supported_cases,
                        ],
                        combined_keys,
                    )
                )
                existing["adoption_status"] = "MODIFIED"
                if existing != before:
                    applied.append(
                        {
                            "comparison_id": comparison_id,
                            "action": "MODIFY",
                            "cases": list(existing["cases"]),
                        }
                    )
            continue
        item = by_id.get(str(update.get("comparison_id") or ""))
        if item is None:
            continue
        before = deepcopy(item)
        recommended = [
            str(case)
            for case in item.get("recommended_cases", item.get("cases", []))
            if str(case).strip()
        ]
        raw_cases = update.get("cases", [])
        current_cases = [
            str(case)
            for case in item.get("cases", [])
            if str(case).strip()
        ]
        case_semantic_keys = {
            **(
                item.get("case_semantic_keys", {})
                if isinstance(item.get("case_semantic_keys"), dict)
                else {}
            ),
            **(
                update.get("case_semantic_keys", {})
                if isinstance(update.get("case_semantic_keys"), dict)
                else {}
            ),
        }
        message_evidence = _normalized_evidence_text(user_message)
        catalog = _comparison_case_catalog(item)
        catalog_by_ref = {
            str(entry["case_ref"]): entry for entry in catalog
        }
        rename_by_ref: dict[str, str] = {}
        raw_renames = update.get("renames", [])
        for rename in raw_renames if isinstance(raw_renames, list) else []:
            if not isinstance(rename, dict):
                continue
            case_ref = str(rename.get("case_ref") or "")
            label = str(rename.get("label") or "").strip()
            if (
                case_ref in catalog_by_ref
                and label
                and _normalized_evidence_text(label) in message_evidence
            ):
                rename_by_ref[case_ref] = label
        referenced_cases: list[str] = []
        raw_refs = update.get("case_refs", [])
        for case_ref in raw_refs if isinstance(raw_refs, list) else []:
            case_ref = str(case_ref)
            entry = catalog_by_ref.get(case_ref)
            if entry is not None:
                referenced_cases.append(
                    rename_by_ref.get(case_ref, str(entry["label"]))
                )
        supported_new_cases: list[str] = []
        raw_new_cases = update.get("new_cases", [])
        for raw_case in raw_new_cases if isinstance(raw_new_cases, list) else []:
            case = str(raw_case).strip()
            if case and _normalized_evidence_text(case) in message_evidence:
                supported_new_cases.append(case)
        cases = []
        if isinstance(raw_cases, list):
            for raw_case in raw_cases:
                case = str(raw_case).strip()
                if not case:
                    continue
                supported_by_state = case in recommended or case in current_cases
                supported_by_student = bool(
                    message_evidence
                    and _normalized_evidence_text(case) in message_evidence
                )
                if supported_by_state or supported_by_student:
                    cases.append(case)
        if action == "ACCEPT":
            item["cases"] = list(recommended)
            item["adoption_status"] = "ACCEPTED"
        elif action == "REJECT":
            item["cases"] = []
            item["adoption_status"] = "REJECTED"
        elif action == "MODIFY" and cases and not (
            referenced_cases or supported_new_cases
        ):
            merged_cases = (
                [*current_cases, *cases]
                if update.get("merge_with_existing") is True
                else cases
            )
            item["cases"] = list(dict.fromkeys(merged_cases))
            item["adoption_status"] = (
                "ACCEPTED"
                if set(item["cases"]) == set(recommended)
                else "MODIFIED"
            )
        elif action == "MODIFY" and (referenced_cases or supported_new_cases):
            selected = list(dict.fromkeys([*referenced_cases, *supported_new_cases]))
            if update.get("replace_all") is True:
                item["cases"] = selected
            else:
                item["cases"] = list(dict.fromkeys([*current_cases, *selected]))
            item["adoption_status"] = (
                "ACCEPTED" if item["cases"] == recommended else "MODIFIED"
            )
        if item != before:
            item["cases"], item["case_semantic_keys"] = (
                _deduplicate_cases_by_semantics(
                    [str(case) for case in item.get("cases", [])],
                    case_semantic_keys,
                )
            )
            applied.append(
                {
                    "comparison_id": str(item.get("comparison_id") or ""),
                    "action": action,
                    "cases": [
                        str(case)
                        for case in item.get("cases", [])
                        if str(case).strip()
                    ],
                }
            )
    set_baseline_comparisons(session, comparisons)
    return applied


def accept_pending_comparisons_on_advance(session: DesignSession) -> None:
    """Treat advancing as acceptance of still-pending baseline proposals."""

    comparisons = baseline_comparisons_snapshot(session)
    for item in comparisons:
        if not isinstance(item, dict) or item.get("adoption_status") != "PENDING":
            continue
        recommended = item.get("recommended_cases", item.get("cases", []))
        if isinstance(recommended, list):
            item["cases"] = [str(case) for case in recommended if str(case).strip()]
        item["adoption_status"] = "ACCEPTED"
    set_baseline_comparisons(session, comparisons)


def apply_semantic_design_updates(
    session: DesignSession,
    resolved: dict[str, Any],
    user_message: str,
    pending_action: dict[str, Any] | None = None,
) -> None:
    """Apply only validated IDs/cases from semantic analysis to design state."""

    updates = resolved.get("semantic_updates", {})
    if (
        resolved.get("intent") == UserIntent.UNCLEAR.value
        or not isinstance(updates, dict)
    ):
        return
    authoritative_actions = resolved.get("actions_authoritative") is True
    if authoritative_actions and not resolved.get("dialogue_acts"):
        # A model summary without executable field-level acts is read-only.
        # Never fall back to copying the whole student turn into a pending
        # field; retain it for localized clarification instead.
        return
    # A clarification turn may preserve the student's substantive revision in
    # pending_action and then receive only a short confirmation (for example,
    # "对，就是我刚才补充的内容").  Once the state machine has explicitly
    # marked that value as a confirmed pending modification, its provenance is
    # the original student turn—not the short confirmation message.  Use that
    # recovered value as evidence so structured comparison additions are not
    # silently discarded after clarification.
    comparison_evidence = user_message
    resolved_value = resolved.get("resolved_value")
    if (
        resolved.get("source") == "CONFIRMED_PENDING_MODIFICATION"
        and isinstance(resolved_value, str)
        and resolved_value.strip()
    ):
        comparison_evidence = resolved_value
    design_evidence = (
        resolved_value
        if isinstance(resolved_value, str) and resolved_value.strip()
        else user_message
    )
    design_updates = updates.get("design_updates")
    if (
        not authoritative_actions
        and (not isinstance(design_updates, list) or not design_updates)
    ):
        design_updates = design_updates_from_facets(
            updates.get("facet_updates"),
            evidence=design_evidence,
        )
    elif not isinstance(design_updates, list):
        design_updates = []
    if (
        isinstance(pending_action, dict)
        and pending_action.get("type") == "ANSWER_IDEA_FACET"
        and not resolved.get("dialogue_acts")
    ):
        allowed_fields = {
            FACET_TO_DESIGN_FIELD.get(str(pending_action.get("subject") or ""), "")
        }
        for facet_update in updates.get("facet_updates", []):
            if isinstance(facet_update, dict) and facet_update.get("status") == "CLEAR":
                allowed_fields.add(
                    FACET_TO_DESIGN_FIELD.get(
                        str(facet_update.get("facet_id") or ""),
                        "",
                    )
                )
        if "hypothesis" in allowed_fields:
            allowed_fields.add("expected_phenomenon")
        design_updates = [
            item
            for item in design_updates
            if isinstance(item, dict)
            and str(item.get("field") or item.get("field_id") or "")
            in allowed_fields
        ]
    changed_fields = apply_design_updates(
        session,
        design_updates,
        pending_action=pending_action,
        provenance="STUDENT_CONFIRMED",
    )
    if changed_fields:
        if (
            isinstance(pending_action, dict)
            and updates.get("pending_answer_status") == "CLEAR"
        ):
            set_pending_action_snapshot(session, None)
        facet_updates = updates.get("facet_updates")
        if not isinstance(facet_updates, list):
            facet_updates = []
            updates["facet_updates"] = facet_updates
        existing_facet_ids = {
            str(item.get("facet_id") or "")
            for item in facet_updates
            if isinstance(item, dict)
        }
        canonical = ensure_design_state(session)
        for field in changed_fields:
            facet_id = DESIGN_FIELD_TO_FACET.get(field)
            if not facet_id or facet_id in existing_facet_ids:
                continue
            facet_updates.append(
                {
                    "facet_id": facet_id,
                    "status": "CLEAR",
                    "operation": "REPLACE",
                    "value": canonical.get(field, ""),
                }
            )
    stage_field_updates = updates.get("stage_field_updates")
    stage_field_updates = (
        stage_field_updates if isinstance(stage_field_updates, list) else []
    )
    mode_allowed_stage_fields = (
        STAGE_ACT_FIELDS - {"student_summary"}
        if session.interaction_state is InteractionState.EMVR_DIRECT
        else STAGE_ACT_FIELDS
        - {"unity_objects", "interactions", *BUILDER_REQUIREMENT_FIELDS}
    )
    stage_field_updates = [
        item
        for item in stage_field_updates
        if isinstance(item, dict)
        and str(item.get("field") or "") in mode_allowed_stage_fields
    ]
    updates["stage_field_updates"] = stage_field_updates
    changed_stage_fields = apply_stage_field_updates(
        session,
        stage_field_updates,
        stage=session.current_stage,
        provenance="STUDENT_CONFIRMED",
    )
    updates["applied_design_fields"] = changed_fields
    updates["applied_stage_fields"] = changed_stage_fields
    applied_comparison_updates = _apply_comparison_updates(
        session,
        updates.get("comparison_updates"),
        comparison_evidence,
    )
    # This is state-machine output, not model-authored input.  Response
    # generation uses it to acknowledge only changes that were actually
    # committed during this turn.
    updates["applied_comparison_updates"] = applied_comparison_updates
    pending_fields = (
        [
            FACET_TO_DESIGN_FIELD.get(str(field), str(field))
            for field in pending_action.get("answer_fields", [])
            if str(field)
        ]
        if isinstance(pending_action, dict)
        and isinstance(pending_action.get("answer_fields"), list)
        else []
    )
    touched_fields: set[str] = set()
    for key in ("design_updates", "stage_field_updates"):
        raw_field_updates = updates.get(key)
        if not isinstance(raw_field_updates, list):
            continue
        for item in raw_field_updates:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or item.get("field_id") or "")
            if field:
                touched_fields.add(FACET_TO_DESIGN_FIELD.get(field, field))
    touched_pending_fields = touched_fields & set(pending_fields)
    canonical_design = design_state_snapshot(session)
    canonical_stage = stage_design_state_snapshot(session)
    pending_fields_complete = bool(
        pending_fields
        and all(
            bool(canonical_design.get(field) or canonical_stage.get(field))
            for field in pending_fields
        )
    )
    emvr_update = updates.get("emvr_design_update", {})
    emvr_revision_present = bool(
        session.interaction_state is InteractionState.EMVR_DIRECT
        and isinstance(emvr_update, dict)
        and (
            emvr_update.get("field_updates")
            or emvr_update.get("theory_link_updates")
        )
    )
    confirmation_updated = bool(
        isinstance(pending_action, dict)
        and pending_action.get("type") in CONFIRMATION_PENDING_TYPES
        and (
            changed_fields
            or changed_stage_fields
            or applied_comparison_updates
            or emvr_revision_present
        )
    )
    pending_resolved = bool(
        isinstance(pending_action, dict)
        and (
            updates.get("pending_answer_status") == "CLEAR"
            or (touched_pending_fields and pending_fields_complete)
            # A review/modify prompt has been answered once any validated
            # revision is committed.  Keeping the old review pending after a
            # cross-field or comparison edit caused the next short reply to
            # confirm a stale proposal and produced a loop.  Open questions
            # remain stricter and close only when their own answer fields are
            # complete.
            or confirmation_updated
        )
    )
    if pending_resolved:
        updates["pending_answer_status"] = "CLEAR"
        # The visible question has been answered. Remove that exact pending
        # item before response generation; a genuinely new question may be
        # saved afterwards with a fresh action id and repeat_count=1. Keeping
        # the answered item here was the source of many apparent loops.
        state = dialogue_state(session)
        current = state.get("pending_action")
        same_pending = bool(
            isinstance(current, dict)
            and (
                (
                    current.get("action_id")
                    and current.get("action_id") == pending_action.get("action_id")
                )
                or (
                    current.get("type") == pending_action.get("type")
                    and current.get("subject") == pending_action.get("subject")
                )
            )
        )
        if same_pending:
            state.pop("pending_action", None)
            set_pending_action_snapshot(session, None)
        elif current is None:
            # A legacy session can carry only the canonical snapshot. Clear it
            # when no newer dialogue pending item exists, but never erase a
            # different question that has already replaced the resolved one.
            set_pending_action_snapshot(session, None)


def apply_resolved_intent(
    session: DesignSession,
    resolved: dict[str, Any],
    pending_action: dict[str, Any] | None,
    user_message: str = "",
) -> None:
    state = dialogue_state(session)
    state["resolved_intent"] = deepcopy(resolved)
    intent = str(resolved.get("intent") or UserIntent.UNCLEAR.value)
    semantic_updates = resolved.get("semantic_updates", {})
    control_actions = set(
        semantic_updates.get("control_actions", [])
        if isinstance(semantic_updates, dict)
        and isinstance(semantic_updates.get("control_actions"), list)
        else []
    )
    if pending_action and intent == UserIntent.ADVANCE_STAGE.value:
        pending_action["status"] = "PRESERVED_ON_ADVANCE"
        state["pending_action"] = deepcopy(pending_action)
    if (
        pending_action
        and not resolved.get("dialogue_acts")
        and intent
        in {
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
        }
    ):
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
        if intent == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value:
            subject = str(pending_action.get("subject") or "")
            refresh_topic_lock(
                session,
                preserved_fields=[FACET_TO_DESIGN_FIELD.get(subject, subject)],
            )
    elif pending_action and {"ACCEPT", "REJECT"} & control_actions:
        # A mixed turn can accept/reject the visible proposal and edit another
        # field at the same time.  Resolve the proposal independently instead
        # of losing this control action behind the primary MODIFY intent.
        accepted = "ACCEPT" in control_actions
        subject = str(pending_action.get("subject") or "current_proposal")
        decisions = session.design_context.setdefault("resolved_decisions", {})
        if not isinstance(decisions, dict):
            decisions = {}
            session.design_context["resolved_decisions"] = decisions
        decisions[subject] = (
            deepcopy(pending_action.get("proposal")) if accepted else None
        )
        pending_action["status"] = "ACCEPTED" if accepted else "REJECTED"
        state["pending_action"] = deepcopy(pending_action)
        if accepted:
            subject = str(pending_action.get("subject") or "")
            refresh_topic_lock(
                session,
                preserved_fields=[FACET_TO_DESIGN_FIELD.get(subject, subject)],
            )
    log = state.setdefault("decision_log", [])
    if isinstance(log, list):
        log.append(deepcopy(resolved))
        del log[:-40]
    apply_semantic_design_updates(
        session,
        resolved,
        user_message,
        pending_action=pending_action,
    )
    state["carried_context"] = build_carried_context(session)


def clarification_output(
    pending_action: dict[str, Any] | None,
    interaction_state: InteractionState = InteractionState.GUIDED_DESIGN,
) -> StepOutput:
    emvr_mode = interaction_state is InteractionState.EMVR_DIRECT
    if pending_action:
        if pending_action.get("type") == "ANSWER_IDEA_FACET":
            proposal = pending_action.get("proposal", {})
            title = str(
                proposal.get("title") or ""
                if isinstance(proposal, dict)
                else ""
            ).strip() or "当前部分"
            try:
                repeat_count = int(pending_action.get("repeat_count", 1))
            except (TypeError, ValueError):
                repeat_count = 1
            question = str(pending_action.get("question") or "").strip()
            candidate_saved = bool(
                str(pending_action.get("candidate_answer") or "").strip()
                and pending_action.get("candidate_binding_authorized") is True
            )
            unbound_candidate = bool(
                str(pending_action.get("candidate_answer") or "").strip()
                and not candidate_saved
            )
            if unbound_candidate:
                message = (
                    (
                        "我把你这几次补充都保留在本轮待处理中，但还没有写入设计，以免归错栏目。"
                        "请把要修改的栏目和最终表述放在一起再发一次；一句话可以同时修改多项。"
                    )
                    if repeat_count > 2
                    else (
                        "我保留了你刚才的补充，但还没有把它写进这一项，以免归错位置。"
                        "请直接重试这项修改；如果一句话里改了多处，也可以一起说明。"
                    )
                )
            elif candidate_saved:
                if repeat_count > 2:
                    message = (
                        f"“{title}”已经作为待确认草稿保留，我不会再重复原来的问题。"
                        "你可以直接沿用这份表述，也可以输入修改后的完整版本；"
                        "需要帮助时，我也可以先给一份课程内参考。"
                    )
                else:
                    message = (
                        f"你刚才对“{title}”的回答已经保留，不需要再重复。"
                        "如果这就是你的最终表述，可以直接沿用；"
                        "如果还要调整，直接写出修改后的版本。"
                    )
                action_id = str(pending_action.get("action_id") or "")
                clarification_choices = [
                    {
                        "option_id": f"pending_accept::{action_id}",
                        "label": "沿用刚才的表述",
                    },
                    {
                        "option_id": f"pending_reference::{action_id}",
                        "label": "先看一份课程内参考",
                    },
                ] if action_id else []
            elif repeat_count > 2:
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
                stage_payload={
                    "clarification_required": True,
                    "clarification_choices": (
                        clarification_choices if candidate_saved else []
                    ),
                },
                student_task=None,
            )
        question = str(pending_action.get("question") or "").strip()
        if pending_action.get("type") in {
            "ANSWER_STAGE_QUESTION",
            "ANSWER_EMVR_STAGE_QUESTION",
        }:
            try:
                repeat_count = int(pending_action.get("repeat_count", 1))
            except (TypeError, ValueError):
                repeat_count = 1
            candidate_saved = bool(
                str(pending_action.get("candidate_answer") or "").strip()
                and pending_action.get("candidate_binding_authorized") is True
            )
            unbound_candidate = bool(
                str(pending_action.get("candidate_answer") or "").strip()
                and not candidate_saved
            )
            if (
                pending_action.get("type") == "ANSWER_EMVR_STAGE_QUESTION"
                and candidate_saved
            ):
                message = (
                    "上一轮提供的设计描述已经保留，无需重新录入。"
                    "如果它就是当前设计项的最终表述，请确认沿用；"
                    "如需修订，只补充缺失的物理关系或Unity映射即可。"
                )
            elif unbound_candidate:
                message = (
                    (
                        "我保留了你连续补充的内容，但仍没有自动写入设计，以免把多个要求混进一个栏目。"
                        "请把每个要调整的栏目及其最终表述写在同一条消息里，我会分别处理。"
                    )
                    if repeat_count > 2
                    else (
                        "我保留了你刚才的设计补充，但没有把它自动塞进当前栏目，以免改错。"
                        "请直接重试这项修改；你可以在同一句里列出多个需要调整的部分。"
                    )
                )
            elif repeat_count > 2:
                if emvr_mode:
                    message = (
                        "当前设计项先沿用现有草稿，不要求重复输入。"
                        "你可以要求一份可修订的专业参考，也可以直接补充新的物理约束或Unity交互要求。"
                    )
                else:
                    message = (
                        "这一点先不用重复。你可以让我给一个可修改的参考，"
                        "也可以直接补充一个新的关键细节。"
                    )
            else:
                if emvr_mode:
                    message = (
                        "当前设计评审还缺少一项信息："
                        f"{question or '请补充与研究问题直接相关的物理或Unity设计要求。'}"
                        " 如需参考，我可以先给出一份可修订的专业草稿。"
                    )
                else:
                    message = (
                        "我们还差一个关键点："
                        f"{question or '请补充这一部分最重要的设计内容。'}"
                        " 如果暂时没有想法，我可以先给一个可修改的参考。"
                    )
            action_id = str(pending_action.get("action_id") or "")
            clarification_choices = (
                [
                    {
                        "option_id": f"pending_accept::{action_id}",
                        "label": "沿用刚才的表述",
                    },
                    {
                        "option_id": f"pending_reference::{action_id}",
                        "label": "先看一份参考草稿",
                    },
                ]
                if candidate_saved and action_id
                else []
            )
            return StepOutput(
                assistant_message=message,
                stage_payload={
                    "clarification_required": True,
                    "clarification_choices": clarification_choices,
                },
                student_task=None,
            )
        if (
            pending_action.get("type") in CONFIRMATION_PENDING_TYPES
            and str(pending_action.get("candidate_answer") or "").strip()
        ):
            if pending_action.get("candidate_binding_authorized") is not True:
                try:
                    repeat_count = int(pending_action.get("repeat_count", 1))
                except (TypeError, ValueError):
                    repeat_count = 1
                message = (
                    (
                        "我已经保留这几次修订说明，但尚未改动现有设计，因为其中包含多个可能的修改对象。"
                        "请把各栏目与最终内容对应写出；其他已确认内容都会保持不变。"
                    )
                    if repeat_count > 2
                    else (
                        "我保留了你刚才的修订说明，但没有据此覆盖现有设计，以免把它归入错误栏目。"
                        "请直接重试这项修改；已经确认的其他内容都会保持不变。"
                    )
                )
                return StepOutput(
                    assistant_message=message,
                    stage_payload={"clarification_required": True},
                    student_task=None,
                )
            if emvr_mode:
                message = (
                    "现有设计草稿和你刚补充的内容都已保留，无需重新表述。"
                    "请确认是否将这项修订并入当前EMVR设计；如有遗漏，直接指出对应的物理模型或Unity设计层即可。"
                )
            else:
                message = (
                    "前面的想法和你刚补充的内容都保留着，不用重写。"
                    "请告诉我是否把这项补充接进当前设计；如果还有遗漏，直接补充就可以。"
                )
            return StepOutput(
                assistant_message=message,
                stage_payload={"clarification_required": True},
                student_task=None,
            )
        if emvr_mode:
            prompt = (
                "我还不能确定你希望如何处理当前设计草稿。"
                "请说明是沿用、修订、撤回，还是确认后进入下一项设计评审。"
            )
        else:
            prompt = (
                "我还没完全明白你想怎样处理刚才的想法。"
                "请简单告诉我是保留、修改、取消，还是整理好这一部分后继续。"
            )
        if question:
            prompt += f" 刚才我们讨论的是：{question}"
    else:
        if emvr_mode:
            prompt = (
                "我还不能确定你希望继续当前EMVR设计、返回已有设计项，还是建立新的实验方向。"
                "请简短说明要执行哪一种调整。"
            )
        else:
            prompt = (
                "我还没完全明白你想继续现在的想法、回到前面，还是换一个新方向。"
                "请简单说明一下，我们再接着完善。"
            )
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
    intent_context = deepcopy(carried_context)
    comparisons = intent_context.get("baseline_comparisons", [])
    if isinstance(comparisons, list):
        for comparison in comparisons:
            if isinstance(comparison, dict):
                comparison["semantic_case_catalog"] = _comparison_case_catalog(
                    comparison
                )
    topic_lock = intent_context.get("topic_lock", {})
    topic_lock = topic_lock if isinstance(topic_lock, dict) else {}
    idea_development = intent_context.get("idea_development", {})
    idea_development = (
        idea_development if isinstance(idea_development, dict) else {}
    )
    direction_locked = topic_lock.get("locked") is True
    direction_status = (
        "LOCKED"
        if direction_locked
        else "DEVELOPING"
        if idea_development
        else "NOT_ESTABLISHED"
    )
    intent_entry_context = {
        "is_stage_one_entry": bool(
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.IDEA_BRAINSTORMING
            and direction_status == "NOT_ESTABLISHED"
        ),
        "direction_status": direction_status,
        "pending_action_is_non_blocking": bool(
            session.current_stage is Stage.IDEA_BRAINSTORMING
        ),
        "allowed_parallel_results": [
            "NO_DIRECTION",
            "REQUEST_COURSE_REFERENCE",
            "COURSE_DIRECTION_CONTENT",
            "NEW_TOPIC_CONTENT",
        ],
    }
    intent_context["intent_entry_context"] = intent_entry_context
    return json.dumps(
        {
            "current_stage": session.current_stage.value,
            "interaction_state": session.interaction_state.value,
            "previous_question": previous_question,
            "pending_action": pending_action,
            "pending_action_role": (
                "当前仍待明确的内容；它不是限制学生本轮只能回答这一项的指令"
                if pending_action
                else None
            ),
            "intent_entry_context": intent_entry_context,
            "carried_context": intent_context,
            "user_message": user_message,
        },
        ensure_ascii=False,
    )
