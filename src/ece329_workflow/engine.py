from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from copy import deepcopy
from difflib import SequenceMatcher
from threading import RLock
from typing import Any

from .dialogue_state import (
    UserIntent,
    accept_pending_comparisons_on_advance,
    apply_resolved_intent,
    build_carried_context,
    clarification_output,
    current_pending_action,
    dialogue_state,
    deterministic_intent,
    fallback_intent,
    hydrate_pending_action_from_history,
    record_pending_clarification,
    record_scene_direction_confirmation,
    recoverable_pending_field,
    recover_repeated_pending_answer,
    required_pending_facet_id,
    resolved_intent,
    save_pending_action,
    validate_resolved_intent,
)
from .generator import (
    StageGenerator,
    _guided_reference_output,
    guided_stage_entry_output,
)
from .emvr_design import (
    apply_emvr_field_updates,
    emvr_stage_one_readiness,
    merge_emvr_structured_requirements,
)
from .dialogue_acts import apply_stage_field_updates, stage_design_state_snapshot
from .design_state import (
    apply_design_updates,
    baseline_comparisons_snapshot,
    design_state_snapshot,
    ensure_design_state,
    format_design_summary,
    is_topic_locked,
    record_seen_scenes,
    set_baseline_comparisons,
    sync_design_state_to_legacy,
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
    build_facet_reference_output,
    build_gap_output,
    decorate_outline_output,
    has_idea_development,
    initialize_idea_development,
    public_idea_development_status,
    refresh_idea_development,
    update_idea_development,
)
from .knowledge_base import KNOWLEDGE
from .models import (
    STAGE_SEQUENCE,
    DesignAccessDenied,
    DesignSession,
    InteractionState,
    Stage,
    StageCompletionError,
    StepOutput,
    TurnRequest,
    SessionConflict,
    WorkflowStatus,
)
from .prompts import build_prompt_packet
from .openai_generator import generator_from_environment
from .reporting import (
    build_emvr_task_report,
    render_emvr_report_pdf,
    stage_report_section,
    validate_emvr_report_completeness,
)
from .builder_input import render_builder_gate1_input_pdf
from .builder_requirements import (
    builder_handoff_status,
    next_due_builder_requirement,
    validate_builder_requirements,
)
from .stages import (
    IDEA_DEVELOPMENT_STAGES,
    STAGES_BY_ID,
    public_stage_catalog,
    stage_group_metadata,
    stage_title,
)
from .store import SessionStore, store_from_environment
from .turn_planning import (
    compute_design_diff,
    finalize_turn_task_plan,
    student_change_notice,
    workflow_design_snapshot,
)
from .design_quality import (
    evaluate_design_quality,
    format_option_comparison,
    format_quality_review,
    public_quality_review,
)
from .design_versions import (
    ensure_initial_version,
    execute_version_request,
    format_version_result,
    normalize_version_request,
    record_design_version,
)


_TURN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_TURN_CACHE_LIMIT = 50


def _validated_turn_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("turn_id must be a string or null")
    normalized = value.strip()
    if not normalized:
        return None
    if _TURN_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("turn_id must be 8-128 URL-safe characters")
    return normalized


def _turn_request_fingerprint(request: TurnRequest) -> str:
    payload = {
        "message": request.message,
        "complete_stage": request.complete_stage,
        "context_patch": request.context_patch,
        "interaction_state": (
            request.interaction_state.value if request.interaction_state else None
        ),
        "selected_option_id": request.selected_option_id,
        "version_request": request.version_request,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cached_turn_response(
    session: DesignSession,
    request: TurnRequest,
) -> dict[str, Any] | None:
    if not request.turn_id:
        return None
    cache = session.model_context.get("turn_idempotency", [])
    if not isinstance(cache, list):
        return None
    fingerprint = _turn_request_fingerprint(request)
    for item in reversed(cache):
        if not isinstance(item, dict) or item.get("turn_id") != request.turn_id:
            continue
        if item.get("fingerprint") != fingerprint:
            raise SessionConflict(
                "The same turn_id cannot be reused for different turn content."
            )
        response = item.get("response")
        return deepcopy(response) if isinstance(response, dict) else None
    return None


def _cache_turn_response(
    session: DesignSession,
    request: TurnRequest,
    response: dict[str, Any],
) -> None:
    if not request.turn_id:
        return
    cache = session.model_context.setdefault("turn_idempotency", [])
    if not isinstance(cache, list):
        cache = []
        session.model_context["turn_idempotency"] = cache
    cache.append(
        {
            "turn_id": request.turn_id,
            "fingerprint": _turn_request_fingerprint(request),
            "response": deepcopy(response),
        }
    )
    del cache[:-_TURN_CACHE_LIMIT]


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

_STRUCTURED_STAGE_COMPLETION_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.CONCEPTUAL_OR_VR_SETUP: ("unity_objects", "interactions"),
    Stage.VARIABLES_AND_CONDITIONS: (
        "independent_variable",
        "observations",
        "controlled_conditions",
    ),
    Stage.CONCEPTUAL_PROCEDURE: ("procedure_steps",),
    Stage.RESULT_INTERPRETATION: ("result_interpretation",),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: ("limitations",),
}

_EMVR_DESIGN_COMPLETION_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.IDEA_BRAINSTORMING: ("research_object", "course_relationship"),
    Stage.LEARNING_OBJECTIVES: ("learning_objective",),
    Stage.RESEARCH_QUESTION: ("research_question",),
    Stage.HYPOTHESIS: ("hypothesis", "expected_phenomenon"),
}


def _has_structured_stage_content(session: DesignSession, stage: Stage) -> bool:
    if (
        session.interaction_state is InteractionState.EMVR_DIRECT
        and stage is Stage.THEORETICAL_FRAMEWORK
    ):
        requirements = merge_emvr_structured_requirements(
            session.design_context.get("emvr_design", {})
        )
        return bool(requirements.get("theory_links"))
    design = design_state_snapshot(session)
    if any(
        design.get(field)
        for field in _EMVR_DESIGN_COMPLETION_FIELDS.get(stage, ())
    ):
        return True
    structured = stage_design_state_snapshot(session)
    return any(
        structured.get(field)
        for field in _STRUCTURED_STAGE_COMPLETION_FIELDS.get(stage, ())
    ) or bool(
        stage is Stage.EXPECTED_DATA_VISUALIZATION
        and structured.get("visualization_plan")
    )


def _contains_emvr_marker(text: str) -> bool:
    """Return whether the user explicitly included the EMVR mode marker.

    This is intentionally the only natural-language mode shortcut.  All other
    conversational meaning is resolved from ``pending_action`` and context.
    """

    return "EMVR" in text.upper()


def _emvr_mode_control_only(text: str) -> bool:
    """Identify the product's explicit mode command, never experiment content.

    This is deliberately scoped to the one literal EMVR shortcut authorized by
    the product.  All other conversational control continues through semantic
    dialogue acts rather than an expanding phrase list.
    """

    compact = re.sub(r"[\s，,。；;：:！!？?、（）()\-—_]+", "", text).upper()
    return compact in {"EMVR", "进入EMVR模式", "切换到EMVR模式", "使用EMVR模式"}


def _record_mode_handoff(
    session: DesignSession,
    previous_state: InteractionState,
    next_state: InteractionState,
) -> dict[str, Any]:
    """Preserve design meaning while only changing the interaction strategy."""

    design = design_state_snapshot(session)
    stage_state = stage_design_state_snapshot(session)
    comparisons = design.get("baseline_comparisons", [])
    rejected = [
        deepcopy(item)
        for item in (comparisons if isinstance(comparisons, list) else [])
        if isinstance(item, dict) and item.get("adoption_status") == "REJECTED"
    ]
    handoff = {
        "from": previous_state.value,
        "to": next_state.value,
        "research_question": design.get("research_question", ""),
        "learning_objective": design.get("learning_objective", ""),
        "variables": {
            "independent_variable": stage_state.get("independent_variable", ""),
            "observations": stage_state.get("observations", ""),
            "controlled_conditions": stage_state.get("controlled_conditions", ""),
        },
        "rejected_content": rejected,
        "unresolved_quality_issues": deepcopy(
            session.design_context.get("quality_review", {}).get("issues", [])
            if isinstance(session.design_context.get("quality_review"), dict)
            else []
        ),
    }
    session.model_context["mode_handoff"] = deepcopy(handoff)
    if next_state is InteractionState.EMVR_DIRECT:
        emvr = session.design_context.setdefault("emvr_design", {})
        if not isinstance(emvr, dict):
            emvr = {}
            session.design_context["emvr_design"] = emvr
        transferred_brief = str(
            design.get("research_question")
            or design.get("research_object")
            or session.design_context.get("idea", {}).get("original", "")
        ).strip()
        if transferred_brief and not _emvr_mode_control_only(transferred_brief):
            field_state = emvr.setdefault("field_state", {})
            if not isinstance(field_state, dict):
                field_state = {}
                emvr["field_state"] = field_state
            field_state["experiment_brief"] = transferred_brief
            emvr["experiment_brief"] = transferred_brief
            emvr["current_brief"] = transferred_brief
        emvr["mode_handoff"] = deepcopy(handoff)
    return handoff


def _keep_locked_topic_as_refinement(
    turn_intent: dict[str, Any],
    pending_action: dict[str, Any] | None,
    target: str,
    source: str,
) -> None:
    """Downgrade an unsupported topic reset to a current-design refinement."""

    resolved_target = (
        str(pending_action.get("subject") or "")
        if isinstance(pending_action, dict)
        else ""
    ) or target
    turn_intent.update(
        {
            "intent": (
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
                if isinstance(pending_action, dict)
                and pending_action.get("type")
                in {"CONFIRM_STAGE_OR_MODIFY", "CONFIRM_OR_MODIFY"}
                else UserIntent.ANSWER_CURRENT_QUESTION.value
            ),
            "target": resolved_target,
            "advance_requested": False,
            "preserve_current_design": True,
            "source": source,
        }
    )
    updates = turn_intent.get("semantic_updates")
    if isinstance(updates, dict):
        controls = updates.get("control_actions", [])
        if isinstance(controls, list):
            updates["control_actions"] = [
                item
                for item in controls
                if item not in {"NEW_TOPIC", "NEW_TOPIC_CONTENT", "REQUEST_NEW_TOPIC"}
            ]
    acts = turn_intent.get("dialogue_acts", [])
    if isinstance(acts, list):
        for act in acts:
            if not isinstance(act, dict) or act.get("type") not in {
                "NEW_TOPIC",
                "NEW_TOPIC_CONTENT",
                "REQUEST_NEW_TOPIC",
            }:
                continue
            act["type"] = "ANSWER_PENDING_QUESTION"
            act["target"] = resolved_target
            act["operation"] = "MERGE"
    plan = turn_intent.get("task_plan")
    if isinstance(plan, dict):
        for task in plan.get("tasks", []):
            if not isinstance(task, dict) or task.get("type") not in {
                "NEW_TOPIC",
                "NEW_TOPIC_CONTENT",
                "REQUEST_NEW_TOPIC",
            }:
                continue
            task["type"] = "CURRENT_TOPIC_REFINEMENT"
            task["target"] = resolved_target
            task["status"] = "READY"

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

_STUDENT_FIELD_LABELS = {
    "research_object": "研究对象",
    "course_relationship": "课程关系",
    "learning_objective": "学习目标",
    "research_question": "研究问题",
    "theoretical_framework": "理论依据",
    "hypothesis": "假设",
    "expected_phenomenon": "预期现象",
    "conceptual_structure": "实验结构",
    "baseline_comparisons": "基础比较",
    "independent_variable": "主动改变量",
    "observations": "观察内容",
    "controlled_conditions": "控制条件",
    "procedure_steps": "实验流程",
    "visualization_plan": "显示方式",
    "result_interpretation": "结果解释",
    "design_rationale": "设计依据",
    "design_value": "设计价值",
    "limitations": "设计局限",
    "unity_objects": "VR实验对象",
    "interactions": "VR交互",
}


def _multi_act_student_notice(
    semantic_updates: dict[str, Any],
    interaction_state: InteractionState,
    design_diff: dict[str, Any] | None = None,
) -> str:
    """Describe committed and unresolved parts without exposing internals."""

    correction_items = semantic_updates.get("correction_items", [])
    has_correction = bool(
        isinstance(correction_items, list)
        and any(isinstance(item, dict) for item in correction_items)
    )
    # Self-correction has its own concise notice below. Replaying the ordinary
    # before/after delta here can expose the very contaminated or mistaken old
    # text the student asked us to remove, and also acknowledges the same edit
    # twice.
    delta_notice = (
        "" if has_correction else student_change_notice(design_diff, interaction_state)
    )
    changed = [
        *semantic_updates.get("applied_design_fields", []),
        *semantic_updates.get("applied_stage_fields", []),
    ]
    labels = list(
        dict.fromkeys(
            _STUDENT_FIELD_LABELS.get(str(field), str(field))
            for field in changed
            if str(field).strip()
        )
    )
    notices: list[str] = [delta_notice] if delta_notice else []
    if labels and not delta_notice and not has_correction:
        if interaction_state is InteractionState.EMVR_DIRECT:
            notices.append(f"已同步修订设计中的{'、'.join(labels)}。")
        else:
            notices.append(f"我把你补充的{'、'.join(labels)}接到现有想法里了。")
    applied_comparisons = semantic_updates.get("applied_comparison_updates", [])
    if (
        isinstance(applied_comparisons, list)
        and applied_comparisons
        and not delta_notice
        and not has_correction
    ):
        notices.append(
            "比较条件的修订也已同步到设计草稿。"
            if interaction_state is InteractionState.EMVR_DIRECT
            else "你对比较情形的调整也一起保留下来了。"
        )
    unresolved = semantic_updates.get("unresolved_content", [])
    if isinstance(unresolved, list) and unresolved:
        excerpt = str(unresolved[0]).strip()[:180]
        notices.append(
            (
                "其余可确定的设计项已正常提交。"
                f"以下片段的目标还不够明确：“{excerpt}”。请只说明它要修改哪一项。"
            )
            if interaction_state is InteractionState.EMVR_DIRECT
            else (
                "前面已经说清的内容先保留。"
                f"还有一句我没完全理解：“{excerpt}”。你只要补充说明这一句就可以。"
            )
        )
    return "".join(notices)


def _self_correction_notice(
    semantic_updates: dict[str, Any],
    design_diff: dict[str, Any],
    interaction_state: InteractionState,
) -> str:
    corrections = semantic_updates.get("correction_items", [])
    corrections = [item for item in corrections if isinstance(item, dict)] \
        if isinstance(corrections, list) else []
    if not corrections:
        return ""
    affected = list(
        dict.fromkeys(
            _STUDENT_FIELD_LABELS.get(str(field), str(field))
            for item in corrections
            for field in item.get("affected_fields", [])
            if str(field).strip()
        )
    )
    changed = design_diff.get("changed_fields", []) if isinstance(design_diff, dict) else []
    if changed:
        labels = "、".join(
            _STUDENT_FIELD_LABELS.get(str(field), str(field)) for field in changed[:4]
        )
        return (
            f"你指出的理解偏差已经具体修正：这次只调整了{labels}，其他设计内容保持不变。"
            if interaction_state is InteractionState.EMVR_DIRECT
            else f"你提醒得对，我刚才没有按你的修改处理好。现在只改了{labels}，其他想法都保留。"
        )
    if affected:
        labels = "、".join(affected[:4])
        return (
            f"我核对了你指出的{labels}；目前保存值没有发生变化，因此不会假装已经改过。"
            if interaction_state is InteractionState.EMVR_DIRECT
            else f"我重新核对了{labels}，这次没有实际变化，所以不会只口头说“已经修改”。"
        )
    explanation = str(corrections[0].get("explanation") or "").strip()
    return (
        f"我确认上一轮的问题是：{explanation}。这条反馈不会被写进实验内容；接下来会沿用已经确认的设计。"
        if explanation
        else "我确认上一轮的处理有偏差。这条反馈不会被写进实验内容，已经确认的设计继续保留。"
    )


def _normalized_question(text: str) -> str:
    return re.sub(r"[\s，,。；;：:！!？?、（）()\-—]+", "", text).casefold()


def _question_similarity(left: str, right: str) -> float:
    """Compare visible questions without relying on phrase lists."""

    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    shorter = min(len(left), len(right))
    longer = max(len(left), len(right))
    if shorter < 8 or longer > shorter * 3:
        return 0.0
    return SequenceMatcher(
        None,
        left[:1200],
        right[:1200],
        autojunk=False,
    ).ratio()


def _remove_repeated_guided_question(
    output: Any,
    pending_action: dict[str, Any] | None,
    student_message: str,
    interaction_state: InteractionState = InteractionState.GUIDED_DESIGN,
) -> None:
    """Prevent a completed answer from triggering the same question again."""

    if not isinstance(pending_action, dict):
        return
    previous = _normalized_question(str(pending_action.get("question") or ""))
    if len(previous) < 8:
        return
    next_task = _normalized_question(str(output.student_task or ""))
    assistant = _normalized_question(str(output.assistant_message or ""))
    task_repeated = _question_similarity(previous, next_task) >= 0.84
    # A long, useful response can legitimately quote the previous question
    # before answering it.  Only treat the whole assistant response as a
    # replay when the response unit itself is semantically near-identical;
    # substring containment used to erase complete scene sets and outlines.
    assistant_repeated = bool(
        assistant
        and (
            assistant == previous
            or (
                max(len(previous), len(assistant))
                <= min(len(previous), len(assistant)) * 2.2
                and SequenceMatcher(
                    None,
                    previous[:1200],
                    assistant[:1200],
                    autojunk=False,
                ).ratio()
                >= 0.84
            )
        )
    )
    if not task_repeated and not assistant_repeated:
        return
    if interaction_state is InteractionState.EMVR_DIRECT:
        if assistant_repeated:
            output.assistant_message = (
                "已记录本轮对当前设计项的回应，无需再次回答同一问题。"
                "如需修订，请只指出要调整的设计字段；否则可以继续下一项评审。"
            )
        else:
            output.assistant_message = (
                f"{output.assistant_message}\n\n"
                "本轮回应已记录，同一问题不再重复；后续只处理尚未明确的设计项。"
            )
        output.student_task = None
        output.stage_payload["repeated_question_avoided"] = True
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


def _prevent_unrequested_scene_replay(
    session: DesignSession,
    output: StepOutput,
    pending_action: dict[str, Any] | None,
    student_message: str,
    turn_intent: dict[str, Any],
) -> None:
    """Require an explicit structured action before showing another A/B/C batch.

    This operates on response units and dialogue acts, not phrases.  A parser
    fallback or an incomplete compatibility intent therefore cannot turn a
    scene choice, elaboration, correction, or course question into another
    breadth-exploration batch.
    """

    scenes = output.stage_payload.get("exploration_scenes")
    if (
        session.interaction_state is not InteractionState.GUIDED_DESIGN
        or session.current_stage is not Stage.IDEA_BRAINSTORMING
        or not latest_stage_one_scenes(session.history)
        or not isinstance(scenes, list)
        or not scenes
    ):
        return
    updates = turn_intent.get("semantic_updates", {})
    scene_response = str(
        updates.get("stage_one_scene_response") or "NONE"
        if isinstance(updates, dict)
        else "NONE"
    )
    controls = set(
        str(item)
        for item in (
            updates.get("control_actions", [])
            if isinstance(updates, dict)
            and isinstance(updates.get("control_actions"), list)
            else []
        )
    )
    idea = session.design_context.get("idea", {})
    idea = idea if isinstance(idea, dict) else {}
    committed_direction = bool(
        idea.get("direction_locked") is True
        or idea.get("selected_course_relations")
        or str(idea.get("core_phenomenon") or "").strip()
        or str(idea.get("interest_description") or "").strip()
    )

    def continue_from_locked_direction(prefix: str) -> None:
        """Replace an accidental scene replay with the next unresolved facet."""

        direction = str(
            idea.get("direction_summary")
            or idea.get("current_focus")
            or idea.get("topic_anchor")
            or idea.get("confirmed_direction_candidate")
            or ""
        ).strip()
        if not has_idea_development(session):
            initialize_idea_development(
                session,
                {"core_phenomenon": direction},
            )
        next_output = build_gap_output(session, "")
        output.assistant_message = (
            f"{prefix}\n\n{next_output.assistant_message}"
        ).strip()
        output.student_task = next_output.student_task
        output.stage_payload.update(deepcopy(next_output.stage_payload))
        output.stage_payload.update(
            {
                "alternative_ideas": [],
                "exploration_scenes": [],
                "scene_replay_avoided": True,
                "direction_locked": True,
                "clarification_required": False,
                "preserve_pending_action": False,
            }
        )

    if idea.get("direction_locked") is True:
        # Once the student has established a direction, no later control turn,
        # long elaboration, parser fallback, or stale generator response may
        # reopen breadth exploration.  Continue from the structured idea gaps
        # instead of retaining the control utterance as another direction
        # candidate—the latter was the source of the repeated confirmation
        # loop after students typed "continue" more than once.
        continue_from_locked_direction(
            "这个研究方向已经确定，我们直接沿着它继续完善。"
        )
        return
    # An explicitly directionless student is still in breadth exploration.
    # This state is structured and may legitimately produce successive course
    # overviews even when the semantic API is temporarily unavailable.
    if (
        isinstance(updates, dict)
        and updates.get("no_direction") is True
        and scene_response != "SELECT_OR_DEVELOP"
        and not committed_direction
        and updates.get("scene_batch_authorized") is True
    ):
        return
    # A broad course topic supplied after a directionless overview still
    # needs one topic-specific breadth batch.  This is distinct from selecting
    # or elaborating one of the visible scenes, which must never replay them.
    if (
        scene_response == "PROVIDE_BROAD_TOPIC"
        and not committed_direction
        and isinstance(updates, dict)
        and updates.get("scene_batch_authorized") is True
    ):
        return
    established_topic = bool(
        idea.get("direction_locked") is True
        or any(
            str(idea.get(field) or "").strip()
            for field in (
                "topic_anchor",
                "current_focus",
                "direction_summary",
                "core_phenomenon",
                "interest_description",
            )
        )
    )
    unresolved_direction = str(
        idea.get("unresolved_direction_candidate") or ""
    ).strip()
    requests_scene_batch = bool(
        turn_intent.get("intent") == UserIntent.REQUEST_MORE_EXAMPLES.value
        and str(turn_intent.get("target") or "")
        in {"exploration_scenes", BREADTH_EXPLORATION}
    )
    explicitly_requested = bool(
        requests_scene_batch
        and "REQUEST_REFERENCE" in controls
        and scene_response == "REQUEST_NEW_BATCH"
        and isinstance(updates, dict)
        and updates.get("scene_batch_authorized") is True
    )
    if explicitly_requested:
        return
    if output.stage_payload.get("input_category") == UNREASONABLE_REQUEST:
        return
    if (
        output.stage_payload.get("input_category") == "OUT_OF_SCOPE"
        and not established_topic
        and not unresolved_direction
    ):
        return
    explicit_topic_change = bool(
        turn_intent.get("intent") == UserIntent.NEW_TOPIC.value
        and (
            (
                isinstance(updates, dict)
                and updates.get("topic_change_explicit") is True
            )
            or turn_intent.get("preserve_current_design") is False
        )
    )
    if explicit_topic_change:
        return
    # After an explicit "no direction yet" course overview, one subsequent
    # course topic may legitimately receive a tailored scene set.  This is an
    # explicit persisted state, not an inference from an empty topic field.
    if (
        idea.get("directionless_browse_active") is True
        and isinstance(updates, dict)
        and updates.get("course_scope_status") == COURSE_CONTENT
        and updates.get("no_direction") is not True
        and not committed_direction
        and scene_response == "NONE"
        and not updates.get("selected_option_ids")
        and not str(updates.get("stage_one_direction_detail") or "").strip()
        and updates.get("scene_batch_authorized") is True
    ):
        idea["directionless_browse_active"] = False
        return
    if (
        not established_topic
        and not unresolved_direction
        and scene_response == "NONE"
        and not (
            isinstance(updates, dict)
            and (
                updates.get("selected_option_ids")
                or str(updates.get("stage_one_direction_detail") or "").strip()
            )
        )
        and isinstance(updates, dict)
        and updates.get("scene_batch_authorized") is True
    ):
        # Compatibility sessions may have shown a generic redirection batch
        # before the semantic-state fields existed.  With no candidate or
        # topic to protect, allow one tailored batch when the next message is
        # course-grounded.  A course-grounded degraded idea is stored above as
        # unresolved_direction_candidate and therefore never takes this path.
        return

    candidate = str(
        updates.get("stage_one_direction_detail") or ""
        if isinstance(updates, dict)
        else ""
    ).strip()
    if not candidate and isinstance(turn_intent.get("resolved_value"), str):
        candidate = str(turn_intent.get("resolved_value") or "").strip()
    if not candidate:
        candidate = unresolved_direction or student_message.strip()
    retained_pending = record_scene_direction_confirmation(
        session,
        candidate,
    ) or pending_action
    output.stage_payload["alternative_ideas"] = []
    output.stage_payload["exploration_scenes"] = []
    output.stage_payload["scene_replay_avoided"] = True
    output.stage_payload["clarification_required"] = True
    output.stage_payload["preserve_pending_action"] = True
    structured_direction_answer = bool(
        (
            turn_intent.get("intent")
            in {
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            }
            or scene_response == "SELECT_OR_DEVELOP"
        )
        and candidate
        and isinstance(updates, dict)
        and (
            updates.get("course_scope_status") == COURSE_CONTENT
            or updates.get("stage_one_direction_detail")
            or updates.get("selected_option_ids")
            or scene_response == "SELECT_OR_DEVELOP"
        )
    )
    accepted_existing_direction = bool(
        turn_intent.get("intent") == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        or "ACCEPT" in controls
        or structured_direction_answer
    )
    if accepted_existing_direction:
        idea["direction_locked"] = True
        idea["brainstorm_phase"] = INTEREST_DESCRIPTION
        # Once a scene or a concrete direction has been accepted, the earlier
        # "browse because no direction was available" state is no longer
        # active. Leaving it behind could authorize one unrelated breadth
        # batch during a later clarification turn.
        idea.pop("directionless_browse_active", None)
        candidate_value = (
            retained_pending.get("candidate_answer")
            if isinstance(retained_pending, dict)
            else ""
        )
        candidate = str(candidate_value or "").strip()
        canonical_design = design_state_snapshot(session)
        canonical_direction_parts = [
            str(canonical_design.get(field) or "").strip()
            for field in ("research_object", "research_question")
        ]
        canonical_direction = "；".join(
            dict.fromkeys(part for part in canonical_direction_parts if part)
        )
        if canonical_direction:
            # The semantic field updates have already been committed before
            # this response guard runs.  Prefer those clean field values to a
            # raw recovery candidate that may still contain conversational
            # framing such as a scene label or "I am interested in...".
            candidate = canonical_direction
        if candidate:
            # Keep the confirmed raw turn only as internal recovery evidence;
            # canonical design fields still require field-level actions.
            idea["confirmed_direction_candidate"] = candidate[:2000]
            idea["topic_anchor"] = str(
                idea.get("topic_anchor") or unresolved_direction or candidate
            ).strip()[:2000]
            idea["current_focus"] = candidate[:2000]
            idea["direction_summary"] = candidate[:2000]
            idea["course_scope_confirmed"] = True
            idea.pop("unresolved_direction_candidate", None)
        output.stage_payload["direction_locked"] = True
        output.stage_payload["brainstorm_phase"] = INTEREST_DESCRIPTION
        output.stage_payload["clarification_required"] = False
        output.stage_payload["preserve_pending_action"] = False
        if turn_intent.get("intent") == UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value:
            acknowledgement = (
                "已经沿用刚才确定的研究方向，接下来不会再回到三幅图景。"
            )
        else:
            acknowledgement = (
                "你选定并补充的研究重点已经保留下来，接下来不会再回到三幅图景。"
            )
        continue_from_locked_direction(acknowledgement)
    else:
        output.assistant_message = (
            "我已经保留你刚才对现有方向的选择、补充或纠正，不会再换一组三幅图景。"
            "为了避免替你改错方向，请确认沿用刚才说明的研究重点；如果还想补充，直接接着描述即可。"
        )
        action_id = (
            str(retained_pending.get("action_id") or "")
            if isinstance(retained_pending, dict)
            else ""
        )
        output.stage_payload["clarification_choices"] = (
            [
                {
                    "option_id": f"pending_accept::{action_id}",
                    "label": "沿用这个研究重点",
                }
            ]
            if action_id
            else []
        )
    if not accepted_existing_direction:
        output.student_task = None
    if isinstance(retained_pending, dict):
        output.stage_payload["repetition_guard_subject"] = str(
            retained_pending.get("subject") or ""
        )


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
    structured = stage_design_state_snapshot(session)
    structured_fields = _STRUCTURED_STAGE_COMPLETION_FIELDS.get(stage, ())
    if any(structured.get(field) for field in structured_fields):
        return True
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


def _guided_stage_should_auto_advance(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
    pending_action: dict[str, Any] | None,
    intent_name: str,
    semantic_updates: dict[str, Any],
) -> bool:
    """Advance a complete guided section without asking for an empty confirmation.

    The decision uses the pending action, field-level semantic result and the
    generator's structured readiness declaration.  It deliberately does not
    inspect phrases such as "继续" or "没问题".
    """

    if (
        session.interaction_state is not InteractionState.GUIDED_DESIGN
        or stage
        not in {
            Stage.VARIABLES_AND_CONDITIONS,
            Stage.CONCEPTUAL_PROCEDURE,
            Stage.EXPECTED_DATA_VISUALIZATION,
            Stage.RESULT_INTERPRETATION,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
        }
        or not isinstance(pending_action, dict)
        or pending_action.get("type") != "ANSWER_STAGE_QUESTION"
        or intent_name
        not in {
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        }
        or semantic_updates.get("pending_answer_status") != "CLEAR"
    ):
        return False
    if any(
        semantic_updates.get(key)
        for key in (
            "student_questions",
            "feedback_items",
            "correction_items",
            "unresolved_content",
            "quality_review_requests",
            "option_comparison_requests",
            "version_requests",
        )
    ):
        return False
    controls = semantic_updates.get("control_actions", [])
    if isinstance(controls, list) and controls:
        return False
    readiness = output.stage_payload.get("stage_readiness")
    if not (
        isinstance(readiness, dict)
        and readiness.get("ready_for_confirmation") is True
        and readiness.get("remaining_gaps") == []
    ):
        return False
    structured = stage_design_state_snapshot(session)
    required_fields = {
        Stage.VARIABLES_AND_CONDITIONS: (
            "independent_variable",
            "observations",
            "controlled_conditions",
        ),
        Stage.CONCEPTUAL_PROCEDURE: ("procedure_steps",),
        Stage.EXPECTED_DATA_VISUALIZATION: ("visualization_plan",),
        Stage.RESULT_INTERPRETATION: ("result_interpretation",),
        Stage.DESIGN_VALUE_AND_LIMITATIONS: ("limitations",),
    }.get(stage, ())
    if not required_fields or not all(structured.get(field) for field in required_fields):
        return False
    if stage is Stage.EXPECTED_DATA_VISUALIZATION:
        return isinstance(output.visualization, dict) or isinstance(
            session.stage_outputs.get(stage.value, {}).get("visualization"),
            dict,
        )
    return True


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
    stage_state = stage_design_state_snapshot(session)
    summary = str(stage_state.get("student_summary") or message).strip()
    if len(summary) < 20:
        return False
    synthesis.update(
        {
            "student_summary": summary,
            "student_summary_sections": [summary],
            "student_summary_complete": True,
            "student_summary_confirmed": True,
            "completion_source": "SEMANTIC_SUMMARY_REVIEW",
        }
    )
    return True


def _guided_summary_completion_output(session: DesignSession) -> StepOutput:
    design = design_state_snapshot(session)
    stage_state = stage_design_state_snapshot(session)
    retained_fields = [
        label
        for field, label in (
            ("research_question", "研究问题"),
            ("learning_objective", "学习目标"),
            ("independent_variable", "自变量"),
            ("observations", "观察量"),
            ("procedure_steps", "实验流程"),
            ("visualization_plan", "可视化方式"),
            ("result_interpretation", "结果解释"),
            ("limitations", "局限与边界"),
        )
        if design.get(field) or stage_state.get(field)
    ]
    retained_text = "、".join(retained_fields)
    return StepOutput(
        assistant_message=(
            "你已经把研究问题、主要比较、预期现象和课程关系串起来了。"
            "这段学生总结已按原意保存。"
            + (
                f"前面确认的{retained_text}也仍在当前设计中，没有被这次总结覆盖。"
                if retained_text
                else "前面确认的设计内容也仍然保留。"
            )
            + "这次实验设计到这里就完成了。"
        ),
        stage_payload={
            "student_summary_received": True,
            "student_summary_confirmed": True,
            "final_proposal_generated": False,
            "preserved_design_fields": retained_fields,
        },
        student_task=None,
    )


_EMVR_STAGE_LEADS: dict[Stage, str] = {
    Stage.IDEA_BRAINSTORMING: "我先把你提出的电磁现象、实验对象和VR操作整理为设计边界。",
    Stage.COURSE_MAPPING_AND_DIRECTION: "沿用已经确认的方向，下面核对它与ECE329课程关系的对应。",
    Stage.LEARNING_OBJECTIVES: "下面把学习目标转换为可由VR交互、理论计算和反馈验证的能力要求。",
    Stage.RESEARCH_QUESTION: "结合已有目标，下面将变化条件与指定观察响应组织为可检验的研究问题。",
    Stage.THEORETICAL_FRAMEWORK: "下面区分参与理论计算的物理量、模型假设与仅承担教学表达的视觉元素。",
    Stage.HYPOTHESIS: "依据已筛选的课程关系，下面给出可由参数变化和理论输出检验的方向性假设。",
    Stage.CONCEPTUAL_OR_VR_SETUP: "下面把实验要求映射为Unity VR对象、交互职责、计算状态和反馈通道。",
    Stage.VARIABLES_AND_CONDITIONS: "下面明确可调参数、观察量、控制条件及其Unity交互映射。",
    Stage.CONCEPTUAL_PROCEDURE: "依据已确认的对象与变量，下面形成可复现、可比较的VR实验流程。",
    Stage.EXPECTED_DATA_VISUALIZATION: "下面定义理论输出的显示编码，以及它与Unity参数更新事件的对应。",
    Stage.RESULT_INTERPRETATION: "下面为不同理论结果建立解释路径，并区分物理偏差、模型边界与显示映射问题。",
    Stage.DESIGN_VALUE_AND_LIMITATIONS: "下面评估现有交互对学习目标的支持程度，并明确模型与VR展示的适用边界。",
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "EMVR模拟实验设计已汇总，以下内容构成最终设计报告。",
}

_EMVR_INTERACTIVE_ENTRY_STAGES = {
    Stage.IDEA_BRAINSTORMING,
    Stage.COURSE_MAPPING_AND_DIRECTION,
    Stage.LEARNING_OBJECTIVES,
    Stage.RESEARCH_QUESTION,
    Stage.THEORETICAL_FRAMEWORK,
    Stage.HYPOTHESIS,
    Stage.CONCEPTUAL_OR_VR_SETUP,
    Stage.VARIABLES_AND_CONDITIONS,
    Stage.CONCEPTUAL_PROCEDURE,
    Stage.EXPECTED_DATA_VISUALIZATION,
    Stage.RESULT_INTERPRETATION,
    Stage.DESIGN_VALUE_AND_LIMITATIONS,
}

_EMVR_ENTRY_QUESTIONS: dict[Stage, tuple[str, str]] = {
    Stage.IDEA_BRAINSTORMING: (
        "先把你的模糊想法整理成VR实验的设计边界。这里不需要考虑代码或具体实现。",
        "你希望学生进入这个VR实验后，主要操作什么对象、观察什么电磁现象，并最终理解什么关系？",
    ),
    Stage.LEARNING_OBJECTIVES: (
        "课程方向已经确定，接下来需要把目标写成后续交互、计算和反馈都能支持的可检验表述。",
        "完成这个VR实验后，你最希望学生能够解释、计算、比较或通过操作验证什么？",
    ),
    Stage.RESEARCH_QUESTION: (
        "我会保留前面确定的现象和学习目标，把它们收束成一个可由VR参数与观察结果回答的问题。",
        "在这个实验中，你认为最值得改变的条件是什么，又希望重点观察哪一种响应？",
    ),
    Stage.THEORETICAL_FRAMEWORK: (
        "研究问题已经确定；这里要逐条核对哪些课程关系真正计算、解释或约束当前变化量与观察量。",
        "请说明你认为最直接的一条ECE329理论关系，以及它具体支持当前哪个变化量、观察量、比较情形或边界条件。",
    ),
    Stage.HYPOTHESIS: (
        "研究问题和理论关系已经保留，这一步需要明确参数变化时应出现的方向性结果及适用边界。",
        "当主要条件改变时，你预计VR中的数值、曲线或空间场分布会怎样变化，物理理由是什么？",
    ),
    Stage.CONCEPTUAL_OR_VR_SETUP: (
        "现在开始整理Unity VR实验构成。我会在下一轮给出完整物体清单、交互职责、计算状态与反馈对应关系。",
        "你希望学生在VR中完成哪些核心操作，项目里又有哪些现有对象或交互设定必须保留？",
    ),
    Stage.VARIABLES_AND_CONDITIONS: (
        "前面的对象和操作会继续保留；这里要把每个可调参数、观察量、控制条件和基准状态定义清楚。",
        "你希望学生主动改变哪些参数，并用哪些读数、曲线或空间表现判断结果？",
    ),
    Stage.CONCEPTUAL_PROCEDURE: (
        "我会根据已经确定的对象、变量和观察方式，把实验整理成可重复、可比较的VR流程。",
        "从进入实验到完成比较，你认为学生必须亲自经历哪些关键操作或判断？",
    ),
    Stage.EXPECTED_DATA_VISUALIZATION: (
        "这一步只设计理论输出和教学显示，不会把动画当作真实测量或高精度仿真。",
        "为了看清前面的预期趋势，你希望同时保留哪些数值、曲线、场线、矢量或颜色映射？",
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: (
        "最后需要回到最初的学习目标，检查当前VR交互是否真正支持它，并界定模型不能说明什么。",
        "你认为这套VR设计最有价值的空间或交互优势是什么，又有哪些理想化条件需要明确提醒学生？",
    ),
}


def _emvr_entry_reference(
    stage: Stage,
    context: dict[str, Any],
) -> list[str]:
    """Draft the current EMVR layer from confirmed facts before asking for edits."""

    def compact(value: Any, fallback: str) -> str:
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, list):
            text = "、".join(
                dict.fromkeys(str(item).strip() for item in value if str(item).strip())
            )
        else:
            text = str(value).strip() if value is not None else ""
        text = text[:240]
        return text or fallback

    direction = compact(context.get("research_direction"), "当前电磁现象")
    objective = compact(context.get("learning_objective"), "解释核心物理关系")
    question = compact(context.get("research_question"), "当前研究问题")
    variable = compact(context.get("independent_variable"), "主要可调参数")
    observations = compact(context.get("observations"), "目标场量或响应")
    controls = compact(context.get("controlled_conditions"), "其余物理条件")
    hypothesis = compact(context.get("hypothesis"), "预期变化趋势")
    references: dict[Stage, list[str]] = {
        Stage.LEARNING_OBJECTIVES: [
            f"概念目标：能够用ECE329关系解释{direction}",
            f"比较目标：能够根据可视化结果判断不同条件下的差异",
            "交互目标：能够通过VR操作建立参数变化与理论响应之间的对应",
        ],
        Stage.RESEARCH_QUESTION: [
            f"问题主线：围绕“{question}”组织可调条件与观察响应",
            f"条件端：{variable}",
            f"响应端：{observations}",
        ],
        Stage.THEORETICAL_FRAMEWORK: [
            f"研究问题：{question}",
            f"需要连接的变化量：{variable}",
            f"需要解释的观察量：{observations}",
            "筛选原则：每条理论关系都要明确绑定以上设计内容，不能仅因属于相邻课程主题而加入",
        ],
        Stage.HYPOTHESIS: [
            f"待检验趋势：{hypothesis}",
            f"变化输入：{variable}",
            f"判定依据：{observations}",
        ],
        Stage.CONCEPTUAL_OR_VR_SETUP: [
            f"物理层：围绕{direction}枚举场源、受影响对象、边界与观察载体",
            "Unity层：分别定义可交互对象、参数控制器、理论计算状态与反馈面板",
            "对应原则：每个视觉反馈都必须能追溯到研究问题中的物理量",
        ],
        Stage.VARIABLES_AND_CONDITIONS: [
            f"可调参数：{variable}",
            f"观察量：{observations}",
            f"控制条件：{controls}",
        ],
        Stage.CONCEPTUAL_PROCEDURE: [
            f"建立基准：固定{controls}",
            f"执行变化：按设计范围调整{variable}",
            f"观察比较：记录{observations}并完成各基础情形的对照",
        ],
        Stage.EXPECTED_DATA_VISUALIZATION: [
            f"交互输入：{variable}",
            f"理论输出：{observations}",
            "显示约束：数值、曲线与空间可视化采用同一计算状态并标注模型适用范围",
        ],
        Stage.DESIGN_VALUE_AND_LIMITATIONS: [
            f"目标核对：当前交互是否真正支持“{objective}”",
            "空间价值：哪些三维关系通过VR比平面图更容易观察",
            "模型边界：明确理想化条件、计算近似与视觉编码不能代表的内容",
        ],
    }
    return references.get(stage, [])


def _emvr_stage_entry_output(session: DesignSession, stage: Stage) -> StepOutput:
    requirement = next_due_builder_requirement(session, stage)
    if requirement is not None:
        field = str(requirement["field"])
        question = str(requirement["question"])
        return StepOutput(
            assistant_message=(
                f"为了让这份设计可以直接交给 EMVR Builder 使用，"
                f"现在先明确{requirement['label']}。"
            ),
            stage_payload={
                "emvr_guided_entry": True,
                "awaiting_user_design_input": True,
                "builder_requirement_field": field,
                "builder_handoff_status": builder_handoff_status(session),
                "pending_action": {
                    "type": "ANSWER_EMVR_STAGE_QUESTION",
                    "interaction_state": InteractionState.EMVR_DIRECT.value,
                    "subject": field,
                    "answer_fields": [field],
                    "question": question,
                    "advance_on_accept": False,
                    "allowed_intents": [
                        UserIntent.ANSWER_CURRENT_QUESTION.value,
                        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                        UserIntent.REQUEST_MORE_EXAMPLES.value,
                        UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                        UserIntent.UNCLEAR.value,
                    ],
                },
            },
            student_task=question,
        )
    lead, question = _EMVR_ENTRY_QUESTIONS[stage]
    context = build_carried_context(session)
    direction = str(context.get("research_direction") or "").strip()
    if stage is Stage.IDEA_BRAINSTORMING and (
        not direction or direction.casefold() in {"进入emvr模式", "emvr模式", "使用emvr"}
    ):
        acknowledgement = "目前还没有具体实验主题，我们先确定设计起点。"
    elif direction:
        acknowledgement = f"我会继续沿用“{direction}”这个实验方向。"
    else:
        acknowledgement = "我会承接前面已经确定的实验内容。"
    reference_draft = _emvr_entry_reference(stage, context)
    reference_text = (
        "\n\n我先依据现有设计整理一份本阶段草稿：\n"
        + "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(reference_draft, start=1)
        )
        if reference_draft
        else ""
    )
    # The theory reference lists committed variables and the binding rule; it
    # is context, not a proposed theory answer. A bare acceptance must not skip
    # generation of the theory payload consumed by the final report.
    reference_is_confirmable = bool(reference_draft) and stage is not Stage.THEORETICAL_FRAMEWORK
    review_question = (
        "这份草稿是否准确承接了当前研究问题？如需修订，请直接指出对应的物理内容、"
        "Unity映射或展示要求。"
        if reference_is_confirmable
        else question
    )
    pending_type = (
        "CONFIRM_STAGE_OR_MODIFY"
        if reference_is_confirmable
        else "ANSWER_EMVR_STAGE_QUESTION"
    )
    pending_subject = (
        "experiment_brief"
        if stage is Stage.IDEA_BRAINSTORMING and not reference_draft
        else stage.value
    )
    allowed_intents = (
        [
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
            UserIntent.ADVANCE_STAGE.value,
            UserIntent.REQUEST_MORE_EXAMPLES.value,
            UserIntent.RETURN_TO_PREVIOUS_POINT.value,
            UserIntent.NEW_TOPIC.value,
            UserIntent.UNCLEAR.value,
        ]
        if reference_is_confirmable
        else [
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.REQUEST_MORE_EXAMPLES.value,
            UserIntent.RETURN_TO_PREVIOUS_POINT.value,
            UserIntent.NEW_TOPIC.value,
            UserIntent.UNCLEAR.value,
        ]
    )
    return StepOutput(
        assistant_message=f"{acknowledgement}{lead}{reference_text}",
        stage_payload={
            "emvr_guided_entry": True,
            "awaiting_user_design_input": True,
            "reference_draft": reference_draft,
            "pending_action": {
                "type": pending_type,
                "interaction_state": InteractionState.EMVR_DIRECT.value,
                "subject": pending_subject,
                "answer_fields": (
                    ["experiment_brief"]
                    if pending_subject == "experiment_brief"
                    else []
                ),
                "proposal": {
                    "carried_context": deepcopy(context),
                    "reference_draft": reference_draft,
                },
                "question": review_question,
                "advance_on_accept": bool(reference_draft),
                "allowed_intents": allowed_intents,
            },
        },
        student_task=review_question,
    )


def _prepare_emvr_stage_output(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
) -> None:
    """Make the stage artifact visible and wait for a contextual decision."""

    # The report payload is a view of canonical state, not a second source of
    # truth.  In particular, a model may describe Stage 1 with a broad course
    # topic even after the student has supplied a precise observation or
    # interaction.  Project those saved fields back into the visible draft so
    # the next revision refers to the content that was actually committed.
    if stage is Stage.IDEA_BRAINSTORMING:
        emvr_design = session.design_context.get("emvr_design", {})
        requirements = merge_emvr_structured_requirements(emvr_design)
        observations = requirements.get("observed_quantities", [])
        observations = (
            [str(item).strip() for item in observations if str(item).strip()]
            if isinstance(observations, list)
            else []
        )
        interactions = requirements.get("required_behaviors", [])
        interactions = (
            [str(item).strip() for item in interactions if str(item).strip()]
            if isinstance(interactions, list)
            else []
        )
        if observations:
            output.stage_payload["target_phenomenon"] = "；".join(observations)
        else:
            # A broad experiment brief is not an observation.  Do not let a
            # model or rule fallback echo it into the target-phenomenon slot;
            # that field remains visibly absent until the student actually
            # defines an observable response.
            output.stage_payload.pop("target_phenomenon", None)
        if interactions:
            output.stage_payload["possible_vr_interactions"] = interactions
        else:
            # Generic VR affordances are useful as reference examples, but
            # they are not committed interactions chosen for this lab.
            output.stage_payload.pop("possible_vr_interactions", None)

    section = stage_report_section(
        stage,
        output.stage_payload,
        visualization=output.visualization,
    )
    output.stage_payload["emvr_report_section"] = deepcopy(section)
    visible_items = section.get("items", [])
    lines = []
    for item in visible_items:
        value = str(item.get("value") or "").strip()
        if len(value) > 520:
            value = f"{value[:517]}……"
        if value:
            lines.append(f"• {item.get('label')}：{value}")

    lead = _EMVR_STAGE_LEADS[stage]
    if lines:
        output.assistant_message = f"{lead}\n\n" + "\n".join(lines)
    else:
        output.assistant_message = f"{lead}\n\n{output.assistant_message.strip()}"

    if stage is Stage.IDEA_BRAINSTORMING:
        readiness = emvr_stage_one_readiness(
            session.design_context.get("emvr_design", {})
        )
        if not readiness["ready"]:
            missing_key = str(readiness["missing_fields"][0])
            field_by_gap = {
                "experiment_brief": "experiment_brief",
                "research_object": "research_object",
                "operation_or_change": "changed_quantities",
                "observation": "observed_quantities",
            }
            question_by_gap = {
                "experiment_brief": "请用一段完整的话说明这个VR实验要操作什么、改变什么并观察什么。",
                "research_object": "这个VR实验中，学生具体会操作或比较哪些物理对象？",
                "operation_or_change": "围绕这些对象，学生要执行什么核心操作，或主动改变哪个条件？",
                "observation": "完成操作或改变条件后，学生需要重点观察哪一种电磁现象或响应？",
            }
            target_field = field_by_gap[missing_key]
            task = question_by_gap[missing_key]
            output.assistant_message = (
                f"{output.assistant_message.rstrip()}\n\n"
                f"你已经给出的方向信息都会保留；现在只补齐"
                f"{readiness['missing'][0]}，不会要求你重写前面的内容。"
            )
            output.student_task = task
            output.stage_payload["emvr_stage_one_readiness"] = readiness
            output.stage_payload["awaiting_user_design_input"] = True
            output.stage_payload["pending_action"] = {
                "type": "ANSWER_EMVR_STAGE_QUESTION",
                "interaction_state": InteractionState.EMVR_DIRECT.value,
                "subject": target_field,
                "answer_fields": [target_field],
                "question": task,
                "advance_on_accept": False,
                "allowed_intents": [
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.REQUEST_MORE_EXAMPLES.value,
                    UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                    UserIntent.NEW_TOPIC.value,
                    UserIntent.UNCLEAR.value,
                ],
            }
            return

    requirement = next_due_builder_requirement(session, stage)
    output.stage_payload["builder_handoff_status"] = builder_handoff_status(session)
    if requirement is not None:
        field = str(requirement["field"])
        task = str(requirement["question"])
        output.assistant_message = (
            f"{output.assistant_message.rstrip()}\n\n"
            f"这部分还需要明确{requirement['label']}，确认后才会进入 Builder 交接文档。"
        )
        output.student_task = task
        output.stage_payload["awaiting_user_design_input"] = True
        output.stage_payload["builder_requirement_field"] = field
        output.stage_payload["pending_action"] = {
            "type": "ANSWER_EMVR_STAGE_QUESTION",
            "interaction_state": InteractionState.EMVR_DIRECT.value,
            "subject": field,
            "answer_fields": [field],
            "question": task,
            "advance_on_accept": False,
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        return

    if stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:
        output.assistant_message = (
            f"{output.assistant_message.rstrip()}\n\n"
            "任务报告和PDF记录的是实验目标、物体清单、交互职责、变量、流程、"
            "理论显示与设计局限；它们用于辅助后续设计评审，不代表已经生成或实现Unity实验。"
        )
        output.student_task = None
        output.stage_payload.pop("pending_action", None)
        return

    if stage is Stage.IDEA_BRAINSTORMING:
        task = (
            "请核对这里是否完整保留了你要研究的电磁现象、VR对象和核心操作；"
            "需要修订时直接指出对应内容，设计边界准确时也可以确认继续。"
        )
    else:
        task = (
            "这份设计草稿将写入任务报告。请核对物理关系、Unity映射和模型边界；"
            "需要调整时指出对应设计层，内容准确时确认继续即可。"
        )
    output.student_task = task
    output.stage_payload["pending_action"] = {
        "type": "CONFIRM_STAGE_OR_MODIFY",
        "interaction_state": InteractionState.EMVR_DIRECT.value,
        "subject": stage.value,
        "proposal": deepcopy(section),
        "question": task,
        "advance_on_accept": True,
        "allowed_intents": [
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


def _project_committed_stage_fields(
    session: DesignSession,
    stage: Stage,
    output: StepOutput,
) -> None:
    """Make every editable later-stage field visible from committed state.

    Model output remains a draft.  These projections ensure a successful
    supplement cannot disappear merely because the next prose generation
    omitted it, in either interaction mode.
    """

    values = stage_design_state_snapshot(session)

    def value(field: str) -> str:
        return str(values.get(field) or "").strip()

    if (
        stage is Stage.LEARNING_OBJECTIVES
        and session.interaction_state is InteractionState.EMVR_DIRECT
    ):
        requirements = merge_emvr_structured_requirements(
            session.design_context.get("emvr_design", {})
        )
        for field in (
            "conceptual_objective",
            "calculation_objective",
            "analysis_objective",
            "vr_interaction_objective",
            "observation_objective",
        ):
            saved = str(requirements.get(field) or "").strip()
            if saved:
                output.stage_payload[field] = saved
    elif stage is Stage.COURSE_MAPPING_AND_DIRECTION:
        if value("design_rationale"):
            output.stage_payload["selection_reason"] = value("design_rationale")
        if value("lab_title"):
            output.stage_payload["lab_title"] = value("lab_title")
        if value("lab_id"):
            output.stage_payload["lab_id"] = value("lab_id")
    elif stage is Stage.CONCEPTUAL_OR_VR_SETUP:
        if value("unity_objects"):
            output.stage_payload["unity_objects"] = value("unity_objects")
        if value("interactions"):
            output.stage_payload["interactions"] = value("interactions")
        if value("visualization_plan"):
            output.stage_payload["visualization_layer"] = value(
                "visualization_plan"
            )
        for field in (
            "desktop_interaction_plan",
            "room_spatial_requirements",
            "hidden_object_lifecycle",
        ):
            if value(field):
                output.stage_payload[field] = value(field)
    elif stage is Stage.VARIABLES_AND_CONDITIONS:
        if value("independent_variable"):
            output.stage_payload["independent_variable"] = value(
                "independent_variable"
            )
        if value("observations"):
            output.stage_payload["dependent_variable"] = value("observations")
        if value("controlled_conditions"):
            output.stage_payload["controlled_variables"] = value(
                "controlled_conditions"
            )
        if value("parameter_specifications"):
            output.stage_payload["parameter_specifications"] = value(
                "parameter_specifications"
            )
    elif stage is Stage.CONCEPTUAL_PROCEDURE and value("procedure_steps"):
        committed_steps = values.get("procedure_steps")
        drafted_steps = output.stage_payload.get("procedure_steps")
        if (
            isinstance(drafted_steps, list)
            and len([item for item in drafted_steps if str(item).strip()]) >= 5
            and not (
                isinstance(committed_steps, list)
                and len([item for item in committed_steps if str(item).strip()]) >= 5
            )
        ):
            # A concise student description may summarize the complete draft
            # without enumerating every row.  Keep that statement visible as
            # provenance, but do not replace a Builder-ready ordered flow with
            # a single sentence.  A genuine field-level list edit still wins.
            output.stage_payload["student_procedure_notes"] = value(
                "procedure_steps"
            )
        else:
            output.stage_payload["procedure_steps"] = deepcopy(committed_steps)
    elif (
        stage is Stage.EXPECTED_DATA_VISUALIZATION
        and value("visualization_plan")
    ):
        output.stage_payload["student_visualization_requirements"] = value(
            "visualization_plan"
        )
    elif stage is Stage.RESULT_INTERPRETATION:
        if value("result_interpretation"):
            output.stage_payload["student_result_interpretation"] = value(
                "result_interpretation"
            )
        for field in ("expected_results", "acceptance_criteria", "report_questions"):
            if value(field):
                output.stage_payload[field] = value(field)
    elif stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
        if value("design_value"):
            output.stage_payload["student_value_and_limit_notes"] = value(
                "design_value"
            )
        if value("limitations"):
            output.stage_payload["limitations"] = value("limitations")


def _persist_emvr_brief(
    session: DesignSession,
    message: str,
    intent_name: str,
    stage: Stage | None = None,
    turn_intent: dict[str, Any] | None = None,
) -> None:
    if (
        session.interaction_state is not InteractionState.EMVR_DIRECT
        or intent_name
        not in {
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.NEW_TOPIC.value,
        }
    ):
        return
    emvr_design = session.design_context.setdefault("emvr_design", {})
    if not isinstance(emvr_design, dict):
        emvr_design = {}
        session.design_context["emvr_design"] = emvr_design
    dialogue_acts = (
        turn_intent.get("dialogue_acts", [])
        if isinstance(turn_intent, dict)
        else []
    )
    brief_operation = ""
    brief_value = ""
    for act in dialogue_acts if isinstance(dialogue_acts, list) else []:
        if not isinstance(act, dict):
            continue
        act_type = str(act.get("type") or "")
        target = str(act.get("target") or "")
        if act_type in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"}:
            brief_operation = "REPLACE"
        elif (
            act_type in {"ANSWER_PENDING_QUESTION", "MODIFY_EMVR_FIELD"}
            and target == "experiment_brief"
        ):
            brief_operation = str(act.get("operation") or "REPLACE").upper()
        else:
            continue
        brief_value = _turn_content_text(act.get("content"))
        if brief_value:
            break
    if not brief_value:
        return

    field_state = emvr_design.setdefault("field_state", {})
    if not isinstance(field_state, dict):
        field_state = {}
        emvr_design["field_state"] = field_state
    prior = str(field_state.get("experiment_brief") or "").strip()
    if brief_operation == "CLEAR":
        current_brief = ""
    elif brief_operation == "MERGE" and prior:
        current_brief = "；".join(dict.fromkeys((prior, brief_value)))
    else:
        current_brief = brief_value
    if not current_brief:
        return
    field_state["experiment_brief"] = current_brief
    emvr_design["experiment_brief"] = current_brief
    # Compatibility views mirror the single authoritative brief; they are no
    # longer assembled from an unbounded history of raw student revisions.
    emvr_design["brief"] = current_brief
    emvr_design["current_brief"] = current_brief
    emvr_design.pop("brief_revisions", None)
    idea = session.design_context.setdefault("idea", {})
    if isinstance(idea, dict):
        idea["current_summary"] = current_brief
        idea["main_direction"] = current_brief
        idea["current_focus"] = current_brief


def _substantive_turn_content(
    turn_intent: dict[str, Any] | None,
) -> Any:
    """Return design content only, excluding questions, feedback and controls."""

    if not isinstance(turn_intent, dict):
        return ""
    dialogue_acts = turn_intent.get("dialogue_acts", [])
    if not isinstance(dialogue_acts, list) or not dialogue_acts:
        return ""
    substantive_types = {
        "ANSWER_PENDING_QUESTION",
        "MODIFY_DESIGN_FIELD",
        "MODIFY_STAGE_FIELD",
        "MODIFY_EMVR_FIELD",
        "MODIFY_COMPARISON",
        "NEW_TOPIC_CONTENT",
        "NEW_TOPIC",
    }
    values: list[Any] = []
    for act in dialogue_acts:
        if not isinstance(act, dict) or act.get("type") not in substantive_types:
            continue
        content = deepcopy(act.get("content"))
        if content in (None, "", [], {}):
            continue
        if content not in values:
            values.append(content)
    if len(values) == 1:
        return values[0]
    return values


def _turn_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(
            part for part in (_turn_content_text(item) for item in value) if part
        )
    if isinstance(value, dict):
        return "；".join(
            part for part in (_turn_content_text(item) for item in value.values()) if part
        )
    return str(value).strip() if value is not None else ""


def _new_topic_content(
    turn_intent: dict[str, Any],
) -> str:
    """Return only content carried by an explicit content-bearing topic act."""

    dialogue_acts = turn_intent.get("dialogue_acts", [])
    if isinstance(dialogue_acts, list):
        for act in dialogue_acts:
            if not isinstance(act, dict) or act.get("type") not in {
                "NEW_TOPIC_CONTENT",
                "NEW_TOPIC",
            }:
                continue
            content = str(act.get("content") or "").strip()
            if content:
                return content
    return ""


def _persist_emvr_stage_input(
    session: DesignSession,
    stage: Stage,
    message: str,
    turn_intent: dict[str, Any],
) -> None:
    """Keep substantive EMVR answers and revisions as authoritative design input."""

    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return
    intent_name = str(turn_intent.get("intent") or "")
    if intent_name not in {
        UserIntent.ANSWER_CURRENT_QUESTION.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        UserIntent.NEW_TOPIC.value,
    }:
        return
    resolved_value = turn_intent.get("resolved_value")
    dialogue_acts = turn_intent.get("dialogue_acts", [])
    structured_content = _substantive_turn_content(turn_intent)
    if isinstance(dialogue_acts, list) and dialogue_acts:
        if structured_content in (None, "", [], {}):
            return
        content: Any = structured_content
    else:
        content = (
            resolved_value
            if resolved_value not in (None, "", [], {})
            else message
        )
    if isinstance(content, str):
        content = content.strip()
        if not content:
            return
    emvr_design = session.design_context.setdefault("emvr_design", {})
    if not isinstance(emvr_design, dict):
        emvr_design = {}
        session.design_context["emvr_design"] = emvr_design
    stage_inputs = emvr_design.setdefault("stage_inputs", {})
    if not isinstance(stage_inputs, dict):
        stage_inputs = {}
        emvr_design["stage_inputs"] = stage_inputs
    entries = stage_inputs.setdefault(stage.value, [])
    if not isinstance(entries, list):
        entries = []
        stage_inputs[stage.value] = entries
    entry = {
        "content": deepcopy(content),
        "intent": intent_name,
        "revision": session.revision + 1,
    }
    semantic_updates = turn_intent.get("semantic_updates", {})
    dialogue_acts = turn_intent.get("dialogue_acts", [])
    if isinstance(dialogue_acts, list) and dialogue_acts:
        entry["dialogue_acts"] = deepcopy(dialogue_acts)
    structured_update = (
        semantic_updates.get("emvr_design_update")
        if isinstance(semantic_updates, dict)
        else None
    )
    structured_update = (
        deepcopy(structured_update)
        if isinstance(structured_update, dict)
        else {}
    )
    # NEW_TOPIC_CONTENT and an explicit experiment_brief revision are
    # authoritative actions in their own right.  They are processed before
    # the per-field projection, so mirror that exact action into the update
    # batch.  Otherwise a simultaneously supplied research object, operation
    # and observation could cause the derived brief to overwrite the complete
    # wording that the student just supplied.
    explicit_brief_edit: dict[str, Any] | None = None
    for act in dialogue_acts if isinstance(dialogue_acts, list) else []:
        if not isinstance(act, dict):
            continue
        act_type = str(act.get("type") or "")
        target = str(act.get("target") or "")
        if act_type in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"} or (
            act_type in {"ANSWER_PENDING_QUESTION", "MODIFY_EMVR_FIELD"}
            and target == "experiment_brief"
        ):
            value = _turn_content_text(act.get("content"))
            if value:
                explicit_brief_edit = {
                    "field_id": "experiment_brief",
                    "operation": (
                        "REPLACE"
                        if act_type in {"NEW_TOPIC_CONTENT", "NEW_TOPIC"}
                        else str(act.get("operation") or "REPLACE").upper()
                    ),
                    "value": value,
                }
                break
    if explicit_brief_edit is not None:
        field_updates = [
            deepcopy(item)
            for item in structured_update.get("field_updates", [])
            if isinstance(item, dict)
            and str(item.get("field_id") or "") != "experiment_brief"
        ]
        field_updates.append(explicit_brief_edit)
        structured_update["field_updates"] = field_updates
    applied_comparisons = (
        semantic_updates.get("applied_comparison_updates", [])
        if isinstance(semantic_updates, dict)
        else []
    )
    if isinstance(applied_comparisons, list) and applied_comparisons:
        # Baseline comparisons are state-machine-owned.  Rebuild their
        # EMVR projection from the post-commit canonical state so the
        # report cannot retain a stale or model-misclassified case list.
        canonical_cases: list[str] = []
        for comparison in baseline_comparisons_snapshot(session):
            if (
                not isinstance(comparison, dict)
                or comparison.get("adoption_status") == "REJECTED"
            ):
                continue
            cases = comparison.get("cases", [])
            if not isinstance(cases, list):
                continue
            for case in cases:
                label = str(case).strip()
                if label and label not in canonical_cases:
                    canonical_cases.append(label)
        structured_update["comparison_cases"] = canonical_cases
        field_updates = [
            deepcopy(item)
            for item in structured_update.get("field_updates", [])
            if isinstance(item, dict)
            and str(item.get("field_id") or "") != "comparison_cases"
        ]
        field_updates.append(
            {
                "field_id": "comparison_cases",
                "operation": "REPLACE",
                "value": canonical_cases,
            }
        )
        structured_update["field_updates"] = field_updates
    if structured_update:
        entry["structured_update"] = deepcopy(structured_update)
        structured_requirements = emvr_design.setdefault(
            "structured_requirements", {}
        )
        if not isinstance(structured_requirements, dict):
            structured_requirements = {}
            emvr_design["structured_requirements"] = structured_requirements
        # The most recent semantic reading is authoritative for this stage.
        # Earlier stages remain available, so revisions do not erase unrelated
        # requirements such as an already confirmed learning objective.
        structured_requirements[stage.value] = deepcopy(structured_update)
        apply_emvr_field_updates(emvr_design, structured_update)
    if not entries or entries[-1].get("content") != entry["content"]:
        entries.append(entry)
        del entries[:-8]


def _persist_guided_stage_input(
    session: DesignSession,
    stage: Stage,
    message: str,
    turn_intent: dict[str, Any],
) -> None:
    """Preserve guided answers and revisions for later turns and stages."""

    if session.interaction_state is not InteractionState.GUIDED_DESIGN:
        return
    intent_name = str(turn_intent.get("intent") or "")
    dialogue_acts = turn_intent.get("dialogue_acts", [])
    substantive_acts = [
        deepcopy(act)
        for act in dialogue_acts
        if isinstance(act, dict)
        and act.get("type")
        in {
            "ANSWER_PENDING_QUESTION",
            "MODIFY_DESIGN_FIELD",
            "MODIFY_STAGE_FIELD",
            "MODIFY_COMPARISON",
        }
    ] if isinstance(dialogue_acts, list) else []
    if intent_name not in {
        UserIntent.ANSWER_CURRENT_QUESTION.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
    } and not substantive_acts:
        return
    resolved_value = turn_intent.get("resolved_value")
    dialogue_acts = turn_intent.get("dialogue_acts", [])
    structured_content = _substantive_turn_content(turn_intent)
    if isinstance(dialogue_acts, list) and dialogue_acts:
        if structured_content in (None, "", [], {}):
            return
        content: Any = structured_content
    else:
        content = (
            resolved_value
            if resolved_value not in (None, "", [], {})
            else message
        )
    if isinstance(content, str):
        content = content.strip()
        if not content:
            return
    stage_inputs = session.design_context.setdefault("guided_stage_inputs", {})
    if not isinstance(stage_inputs, dict):
        stage_inputs = {}
        session.design_context["guided_stage_inputs"] = stage_inputs
    entries = stage_inputs.setdefault(stage.value, [])
    if not isinstance(entries, list):
        entries = []
        stage_inputs[stage.value] = entries
    entry = {
        "content": deepcopy(content),
        "intent": intent_name,
        "revision": session.revision + 1,
    }
    if substantive_acts:
        entry["dialogue_acts"] = substantive_acts
    semantic_updates = turn_intent.get("semantic_updates", {})
    if isinstance(semantic_updates, dict) and semantic_updates:
        entry["semantic_updates"] = deepcopy(semantic_updates)
    if not entries or entries[-1].get("content") != entry["content"]:
        entries.append(entry)
        del entries[:-12]


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
        if (
            session.interaction_state is InteractionState.EMVR_DIRECT
            and isinstance(pending, dict)
            and pending.get("type")
            in {"CONFIRM_STAGE_OR_MODIFY", "CONFIRM_OR_MODIFY"}
        ):
            # Older persisted EMVR sessions predate the mode marker on pending
            # confirmations. Enrich the in-memory decision context so they use
            # the same normalization as newly created sessions.
            pending["interaction_state"] = InteractionState.EMVR_DIRECT.value
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
            # Give the semantic resolver the currently visible scene set, not
            # merely the full conversation history.  This makes a reference
            # such as “use scene B” resolve to the latest B after another batch
            # has been shown, rather than an older scene with the same label.
            carried_context["latest_exploration_scenes"] = [
                {
                    "label": str(scene.get("label") or ""),
                    "title": str(scene.get("title") or ""),
                    "scene_id": str(
                        scene.get("catalog_scene_id")
                        or scene.get("scene_id")
                        or ""
                    ),
                    "option_id": str(
                        (
                            scene.get("course_anchor", {}).get("option_id")
                            if isinstance(scene.get("course_anchor"), dict)
                            else ""
                        )
                        or ""
                    ),
                    "course_anchor": deepcopy(scene.get("course_anchor", {})),
                    "physical_picture": str(scene.get("physical_picture") or ""),
                }
                for scene in latest_stage_one_scenes(session.history)
                if isinstance(scene, dict)
            ]
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
            validated = validate_resolved_intent(semantic, pending)
            recovered = recover_repeated_pending_answer(
                validated,
                pending,
                message,
            )
            if recovered is not None:
                validated = validate_resolved_intent(recovered, pending)
            return validated, pending
        # Offline/rule-only deployments cannot resolve conversational commands.
        # Explicit UI actions still arrive through complete_stage above; typed
        # language remains an answer instead of being guessed from keywords.
        return (
            validate_resolved_intent(
                fallback_intent(
                    message,
                    pending,
                    interaction_state=session.interaction_state,
                ),
                pending,
            ),
            pending,
        )

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
    def _reset_for_new_topic(session: DesignSession) -> None:
        previous_design = {
            "idea": deepcopy(session.design_context.get("idea", {})),
            "emvr_design": deepcopy(session.design_context.get("emvr_design", {})),
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
        session.design_context = {"idea": {}}
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            session.design_context["emvr_design"] = {
                "awaiting_new_topic": True,
                "field_state": {},
            }
        session.model_context.pop("openai_previous_response_id", None)
        session.model_context.pop("dialogue_state", None)

    @classmethod
    def _request_new_topic(cls, session: DesignSession) -> None:
        """Reset the direction without turning the navigation command into data."""

        cls._reset_for_new_topic(session)

    @classmethod
    def _start_new_topic(cls, session: DesignSession, message: str) -> bool:
        """Start a supplied topic only when structured content is present."""

        content = message.strip()
        if not content or (
            session.interaction_state is InteractionState.EMVR_DIRECT
            and _emvr_mode_control_only(content)
        ):
            return False
        cls._reset_for_new_topic(session)
        session.design_context["idea"] = {"original": content}
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            emvr_design = session.design_context["emvr_design"]
            emvr_design["awaiting_new_topic"] = False
            emvr_design["experiment_brief"] = content
            emvr_design["brief"] = content
            emvr_design["current_brief"] = content
            emvr_design["field_state"] = {"experiment_brief": content}
        return True

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
        resume_token = secrets.token_urlsafe(32)
        initial_idea = (
            ""
            if state is InteractionState.EMVR_DIRECT
            and _emvr_mode_control_only(idea)
            else idea.strip()
        )
        initial_context: dict[str, Any] = {"idea": {"original": initial_idea}}
        if state is InteractionState.EMVR_DIRECT:
            initial_context["emvr_design"] = {
                "awaiting_new_topic": not bool(initial_idea),
                "field_state": {},
            }
        session = DesignSession(
            design_id=f"design_{uuid.uuid4().hex[:12]}",
            interaction_state=state,
            access_token_hash=hashlib.sha256(access_token.encode("utf-8")).hexdigest(),
            design_context=initial_context,
        )
        session.model_context["resume_token_hash"] = hashlib.sha256(
            resume_token.encode("utf-8")
        ).hexdigest()
        self.store.save(session)
        result = self.process_turn(
            session.design_id,
            TurnRequest(message=idea.strip()),
        )
        result["design_access_token"] = access_token
        result["design_resume_token"] = resume_token
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
        if isinstance(request, dict):
            request = self._request_from_dict(request)
        if not isinstance(request, TurnRequest):
            raise ValueError("turn request must be an object")
        request.turn_id = _validated_turn_id(request.turn_id)
        cached_response = _cached_turn_response(session, request)
        if cached_response is not None:
            return cached_response
        if session.status is WorkflowStatus.COMPLETE:
            response = {
                "design_id": session.design_id,
                "interaction_state": session.interaction_state.value,
                "status": session.status.value,
                "workflow_status": session.status.value,
                "message": "这份实验设计已经整理完成。",
                "assistant_message": "这份实验设计已经整理完成。",
                "current_stage": session.current_stage.value,
                "next_stage": None,
            }
            if session.interaction_state is InteractionState.EMVR_DIRECT:
                response.update(
                    {
                        "task_report": build_emvr_task_report(session),
                        "builder_handoff_status": builder_handoff_status(session),
                        "report_ready": True,
                        "report_url": f"/v1/designs/{session.design_id}/report.pdf",
                        "builder_input_ready": True,
                        "builder_input_url": (
                            f"/v1/designs/{session.design_id}/builder-gate1-input.pdf"
                        ),
                    }
                )
            response["turn_id"] = request.turn_id
            return response
        if not isinstance(request.message, str):
            raise ValueError("message must be a string")
        message = request.message.strip()
        if not message:
            raise ValueError("message must not be empty")
        ensure_design_state(session)
        ensure_initial_version(session)
        topic_locked_before_turn = is_topic_locked(session)
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
        if request.version_request is not None:
            turn_intent = resolved_intent(
                UserIntent.MANAGE_DESIGN_VERSION,
                confidence=1.0,
                source="STRUCTURED_UI_ACTION",
                semantic_updates={
                    "version_requests": [deepcopy(request.version_request)],
                },
            )
            pending_action = current_pending_action(session)
        elif input_kind == UNREASONABLE_REQUEST:
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
        idea_for_direction_lock = session.design_context.get("idea", {})
        semantic_for_direction_lock = turn_intent.get("semantic_updates", {})
        if (
            input_kind != UNREASONABLE_REQUEST
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.IDEA_BRAINSTORMING
            and not has_idea_development(session)
            and isinstance(idea_for_direction_lock, dict)
            and turn_intent.get("intent")
            in {
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            }
            and isinstance(turn_intent.get("resolved_value"), str)
            and str(turn_intent.get("resolved_value") or "").strip()
            and (
                idea_for_direction_lock.get("course_scope_confirmed") is True
                or (
                    isinstance(semantic_for_direction_lock, dict)
                    and semantic_for_direction_lock.get("course_scope_status")
                    == COURSE_CONTENT
                )
                or bool(latest_stage_one_scenes(session.history))
                or (
                    isinstance(pending_action, dict)
                    and isinstance(pending_action.get("proposal"), dict)
                    and bool(
                        pending_action["proposal"].get("alternative_ideas")
                    )
                )
            )
        ):
            if not isinstance(semantic_for_direction_lock, dict):
                semantic_for_direction_lock = {}
                turn_intent["semantic_updates"] = semantic_for_direction_lock
            if not str(
                semantic_for_direction_lock.get("stage_one_direction_detail") or ""
            ).strip():
                semantic_for_direction_lock["stage_one_direction_detail"] = str(
                    turn_intent["resolved_value"]
                ).strip()
        if (
            input_kind != UNREASONABLE_REQUEST
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.IDEA_BRAINSTORMING
            and (
                topic_locked_before_turn
                or (
                    isinstance(idea_for_direction_lock, dict)
                    and idea_for_direction_lock.get("direction_locked") is True
                )
            )
            and turn_intent.get("intent") == UserIntent.NEW_TOPIC.value
            and not (
                isinstance(semantic_for_direction_lock, dict)
                and semantic_for_direction_lock.get("topic_change_explicit") is True
                or turn_intent.get("preserve_current_design") is False
            )
        ):
            # Once the student has chosen or described a direction, incidental
            # new objects and examples refine that same design.  Replacing the
            # whole topic requires an explicit semantic decision, not a phrase
            # match or a model's unsupported NEW_TOPIC label.
            _keep_locked_topic_as_refinement(
                turn_intent,
                pending_action,
                str(
                    idea_for_direction_lock.get("direction_summary")
                    or idea_for_direction_lock.get("selected_focus")
                    or "locked_stage_one_direction"
                ),
                "SEMANTIC_DIRECTION_LOCK",
            )
        emvr_design_before_turn = session.design_context.get("emvr_design", {})
        emvr_direction_exists = bool(
            isinstance(emvr_design_before_turn, dict)
            and str(
                emvr_design_before_turn.get("current_brief")
                or emvr_design_before_turn.get("brief")
                or ""
            ).strip()
        )
        emvr_topic_action = any(
            isinstance(act, dict)
            and act.get("type") in {"REQUEST_NEW_TOPIC", "NEW_TOPIC_CONTENT"}
            for act in turn_intent.get("dialogue_acts", [])
        ) if isinstance(turn_intent.get("dialogue_acts"), list) else False
        if (
            input_kind != UNREASONABLE_REQUEST
            and session.interaction_state is InteractionState.EMVR_DIRECT
            and (emvr_direction_exists or topic_locked_before_turn)
            and turn_intent.get("intent") == UserIntent.NEW_TOPIC.value
            and not (
                isinstance(semantic_for_direction_lock, dict)
                and semantic_for_direction_lock.get("topic_change_explicit") is True
                or emvr_topic_action
            )
        ):
            # In EMVR, additional objects, interactions, observations and
            # questions refine the existing experiment.  Resetting the whole
            # workflow requires an explicit semantic topic-change decision.
            _keep_locked_topic_as_refinement(
                turn_intent,
                pending_action,
                "emvr_design",
                "SEMANTIC_EMVR_DIRECTION_LOCK",
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
                previous_interaction_state = session.interaction_state
                session.interaction_state = requested_interaction_state
                interaction_state_changed = True
                _record_mode_handoff(
                    session,
                    previous_interaction_state,
                    requested_interaction_state,
                )
                pending_action = None
                dialogue = session.model_context.get("dialogue_state")
                if isinstance(dialogue, dict):
                    dialogue.pop("pending_action", None)
        if session.interaction_state is InteractionState.GUIDED_DESIGN:
            # EMVR physical-role updates are an EMVR-only state channel.  Even
            # if a semantic service returns one unexpectedly, it must not leak
            # into guided prompts, carried context, or design persistence.
            mode_scoped_updates = turn_intent.get("semantic_updates")
            if isinstance(mode_scoped_updates, dict):
                mode_scoped_updates.pop("emvr_design_update", None)
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
        design_before_turn = workflow_design_snapshot(session)
        semantic_before_apply = turn_intent.get("semantic_updates", {})
        version_requests = (
            semantic_before_apply.get("version_requests", [])
            if isinstance(semantic_before_apply, dict)
            and isinstance(semantic_before_apply.get("version_requests"), list)
            else []
        )
        if (
            request.version_request is not None
            and request.version_request not in version_requests
        ):
            version_requests = [*version_requests, request.version_request]
        version_results = [
            result
            for version_request in version_requests
            for result in [execute_version_request(session, version_request)]
            if result is not None
        ]
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
                    or turn_intent.get("source")
                    in {
                        "CONFIRMED_PENDING_ANSWER",
                        "CONFIRMED_PENDING_MODIFICATION",
                    }
                )
            )
            else message
        )
        semantic_updates = (
            deepcopy(turn_intent.get("semantic_updates", {}))
            if (
                str(turn_intent.get("source") or "").startswith("SEMANTIC")
                or turn_intent.get("source")
                in {
                    "CONFIRMED_PENDING_ANSWER",
                    "CONFIRMED_PENDING_MODIFICATION",
                }
            )
            else None
        )
        has_semantic_update_packet = isinstance(semantic_updates, dict)
        semantic_updates = semantic_updates if has_semantic_update_packet else {}
        student_questions = semantic_updates.get("student_questions", [])
        student_questions = (
            [str(item) for item in student_questions if str(item).strip()]
            if isinstance(student_questions, list)
            else []
        )
        feedback_items = semantic_updates.get("feedback_items", [])
        feedback_items = (
            [str(item) for item in feedback_items if str(item).strip()]
            if isinstance(feedback_items, list)
            else []
        )
        has_structured_turn_updates = bool(
            semantic_updates.get("design_updates")
            or semantic_updates.get("stage_field_updates")
            or semantic_updates.get("comparison_updates")
            or semantic_updates.get("facet_updates")
            or (
                isinstance(semantic_updates.get("emvr_design_update"), dict)
                and (
                    semantic_updates["emvr_design_update"].get("field_updates")
                    or semantic_updates["emvr_design_update"].get(
                        "theory_link_updates"
                    )
                )
            )
        )
        control_actions = set(
            semantic_updates.get("control_actions", [])
            if isinstance(semantic_updates.get("control_actions"), list)
            else []
        )
        content_intent_name = intent_name
        design_summary_request = bool(
            intent_name == UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY.value
            or "REQUEST_SUMMARY" in control_actions
        )
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
            topic_content = _new_topic_content(turn_intent)
            if (
                not topic_content
                and session.interaction_state is InteractionState.GUIDED_DESIGN
                and turn_intent.get("preserve_current_design") is False
            ):
                # Preserve the pre-existing guided workflow contract.  The
                # strict control/content separation introduced here is scoped
                # to EMVR; guided mode still receives its already-classified
                # replacement topic as the turn value.
                topic_content = (
                    str(turn_intent.get("resolved_value") or "").strip()
                    or message.strip()
                )
            if topic_content:
                self._start_new_topic(session, topic_content)
                pending_action = None
                apply_resolved_intent(session, turn_intent, pending_action, message)
            elif (
                session.interaction_state is InteractionState.EMVR_DIRECT
                and "REQUEST_NEW_TOPIC" in control_actions
            ):
                self._request_new_topic(session)
                pending_action = None
                apply_resolved_intent(session, turn_intent, pending_action, message)
            else:
                # A compatibility label without a content-bearing act has no
                # authority to reset the design.  Preserve the current topic
                # and let the local clarification path handle the turn.
                turn_intent["intent"] = UserIntent.UNCLEAR.value
                turn_intent["source"] = "UNBOUND_NEW_TOPIC_REQUEST"
                intent_name = UserIntent.UNCLEAR.value
                content_intent_name = intent_name
        elif (
            intent_name == UserIntent.RETURN_TO_PREVIOUS_POINT.value
            or "RETURN" in control_actions
        ):
            self._return_to_previous_stage(session)

        explicit_transition_intent = bool(
            intent_name == UserIntent.ADVANCE_STAGE.value
            or "ADVANCE" in control_actions
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
            session.current_stage is not Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
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
        # A turn can both revise the current draft and request advancement.
        # The reply is generated for handled_stage (the destination), while
        # the student's content belongs to the stage that was just handled.
        # Keeping these roles separate prevents a "修改并继续" turn from being
        # filed under the next stage or omitted from the EMVR brief.
        content_stage = transitioned_from_stage or handled_stage
        stage_one_control_turn = bool(
            content_intent_name
            in {
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.REJECT_PREVIOUS_PROPOSAL.value,
                UserIntent.ADVANCE_STAGE.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY.value,
                UserIntent.ASK_COURSE_QUESTION.value,
                UserIntent.PROVIDE_FEEDBACK.value,
                UserIntent.REQUEST_DESIGN_REVIEW.value,
                UserIntent.COMPARE_DESIGN_OPTIONS.value,
                UserIntent.MANAGE_DESIGN_VERSION.value,
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
                semantic_updates=(
                    semantic_updates if has_semantic_update_packet else None
                ),
            )
            sync_design_state_to_legacy(session)
            refresh_idea_development(session)
        turn_design_diff = compute_design_diff(
            design_before_turn,
            workflow_design_snapshot(session),
            turn_intent.get("task_plan"),
        )
        recorded_version = record_design_version(
            session,
            changed_fields=turn_design_diff.get("changed_fields", []),
            reason=(
                "恢复或撤销设计版本"
                if version_results and turn_design_diff.get("has_changes")
                else "根据本轮讨论更新实验设计"
            ),
            source=("VERSION_CONTROL" if version_results else "STUDENT_TURN"),
        )
        turn_context: dict[str, Any] = {
            "selected_option_id": request.selected_option_id,
            "resolved_intent": deepcopy(turn_intent),
            "pending_action": deepcopy(pending_action),
            "carried_context": build_carried_context(session),
        }
        _persist_emvr_brief(
            session,
            resolved_student_message,
            content_intent_name,
            content_stage,
            turn_intent,
        )
        _persist_emvr_stage_input(
            session,
            content_stage,
            resolved_student_message,
            turn_intent,
        )
        _persist_guided_stage_input(
            session,
            content_stage,
            resolved_student_message,
            turn_intent,
        )
        quality_review_requested = bool(
            semantic_updates.get("quality_review_requests")
            or intent_name == UserIntent.REQUEST_DESIGN_REVIEW.value
        )
        option_comparison_requested = bool(
            semantic_updates.get("option_comparison_requests")
            or intent_name == UserIntent.COMPARE_DESIGN_OPTIONS.value
        )
        final_quality_review = handled_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
        quality_review = evaluate_design_quality(
            session,
            semantic_updates.get("quality_assessment"),
            final_review=final_quality_review,
        )
        turn_context["quality_review"] = deepcopy(quality_review)
        turn_context["guidance_need"] = semantic_updates.get("guidance_need")
        turn_context["option_comparison_requests"] = deepcopy(
            semantic_updates.get("option_comparison_requests", [])
        )
        turn_context["version_results"] = deepcopy(version_results)
        turn_context["recorded_version"] = deepcopy(recorded_version)
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
                resolved_intent_target=str(turn_intent.get("target") or ""),
                pending_action=pending_action,
            )
            if isinstance(idea_context, dict):
                comparisons = stage_one_context.get("standard_comparisons")
                if isinstance(comparisons, list):
                    idea_context["standard_comparisons"] = deepcopy(comparisons)
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
                    resolved_intent_target=str(turn_intent.get("target") or ""),
                    pending_action=pending_action,
                )
            )
        # Persisted answers, field-level EMVR edits and comparison decisions
        # above are authoritative for the response generated in this same
        # turn.  Refresh after every write so the online model never receives
        # the pre-edit snapshot while the rule generator sees the new state.
        turn_context["carried_context"] = build_carried_context(session)
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
        emvr_stage_entry_turn = bool(
            session.interaction_state is InteractionState.EMVR_DIRECT
            and handled_stage in _EMVR_INTERACTIVE_ENTRY_STAGES
            and (
                transitioned_from_stage is not None
                or not handled_stage_seen
                or interaction_state_changed
            )
        )
        clarification_turn = intent_name == UserIntent.UNCLEAR.value
        dialogue_question_turn = bool(student_questions)
        feedback_only_turn = bool(
            feedback_items
            and not student_questions
            and not has_structured_turn_updates
        )
        version_only_turn = bool(
            version_results
            and (
                intent_name == UserIntent.MANAGE_DESIGN_VERSION.value
                or request.version_request is not None
            )
            and not has_structured_turn_updates
            and not student_questions
        )
        idea_facet_reference_turn = bool(
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and (
                intent_name == UserIntent.REQUEST_MORE_EXAMPLES.value
                or "REQUEST_REFERENCE" in control_actions
            )
            and isinstance(pending_action, dict)
            and pending_action.get("type") == "ANSWER_IDEA_FACET"
            and (
                str(turn_intent.get("target") or "")
                not in {"exploration_scenes", "BREADTH_EXPLORATION"}
                or (
                    isinstance(session.design_context.get("idea"), dict)
                    and session.design_context["idea"].get("direction_locked")
                    is True
                )
            )
            and has_idea_development(session)
        )
        guided_stage_reference_turn = bool(
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and handled_stage is not Stage.IDEA_BRAINSTORMING
            and "REQUEST_REFERENCE" in control_actions
        )
        emvr_stage_reference_turn = bool(
            session.interaction_state is InteractionState.EMVR_DIRECT
            and (
                intent_name == UserIntent.REQUEST_MORE_EXAMPLES.value
                or "REQUEST_REFERENCE" in control_actions
            )
        )
        if version_only_turn:
            output = StepOutput(
                assistant_message="\n\n".join(
                    item for item in (format_version_result(result) for result in version_results) if item
                ),
                stage_payload={
                    "version_control": deepcopy(version_results),
                    "preserve_pending_action": True,
                },
                student_task=None,
            )
            session.turn_context = {}
            completion_error = None
        elif quality_review_requested:
            priority = quality_review.get("priority_issue")
            output = StepOutput(
                assistant_message=format_quality_review(
                    quality_review,
                    session.interaction_state,
                    final_review=final_quality_review,
                ),
                stage_payload={
                    "quality_review": public_quality_review(
                        quality_review,
                        max_issues=2 if final_quality_review else 1,
                    ),
                    "preserve_pending_action": True,
                },
                student_task=(
                    str(priority.get("student_question") or "").strip() or None
                    if isinstance(priority, dict)
                    else None
                ),
            )
            session.turn_context = {}
            completion_error = None
        elif option_comparison_requested:
            output = StepOutput(
                assistant_message=format_option_comparison(
                    quality_review,
                    session.interaction_state,
                ),
                stage_payload={
                    "option_comparison": deepcopy(
                        quality_review.get("option_comparison", [])
                    ),
                    "preserve_pending_action": True,
                },
                student_task="你更看重哪一项判断标准，或者已经想采用其中一个方案？",
            )
            session.turn_context = {}
            completion_error = None
        elif emvr_stage_reference_turn:
            # A request for examples is a temporary response strategy, not a
            # stage answer.  Let the online generator answer it from the
            # current EMVR context, but do not pass the result through the
            # normal EMVR report formatter: that formatter deliberately
            # rebuilds the stage draft and used to erase the examples before
            # they reached the student.  The outstanding design question stays
            # active so the student can answer it after reading the reference.
            try:
                output = self.generator.generate(
                    session,
                    resolved_student_message or message,
                )
            finally:
                session.turn_context = {}
            reference_payload = output.stage_payload
            reference_has_detail = bool(
                isinstance(reference_payload, dict)
                and (
                    reference_payload.get("reference_examples")
                    or reference_payload.get("reference_draft")
                )
            ) or len(output.assistant_message.strip()) >= 80
            if (
                not reference_has_detail
                and isinstance(pending_action, dict)
                and pending_action.get("subject") == "experiment_brief"
            ):
                retained_idea = str(
                    pending_action.get("candidate_answer") or ""
                ).strip()
                starting_point = (
                    f"你目前给出的起点是“{retained_idea}”。\n\n"
                    if retained_idea
                    else ""
                )
                example = (
                    "可修改的参考写法是：学生进入VR后操作两个带电物体，通过拖动改变"
                    "它们的间距或相对位置，同时观察电场线与场强分布的变化，并用库仑定律"
                    "和叠加原理解释不同条件下的差异。"
                )
                output.assistant_message = (
                    f"{starting_point}{example}\n\n"
                    "这只是用来展示完整设计方向应包含的结构；对象、操作、变化条件和观察现象"
                    "都可以按你的真实设想修改。"
                )
                output.student_task = (
                    "请直接说明这份参考中哪些内容符合你的想法，以及需要怎样修改。"
                )
                output.stage_payload["reference_scaffold"] = {
                    "field": "experiment_brief",
                    "components": [
                        "操作对象",
                        "学生操作",
                        "主动变化条件",
                        "观察现象",
                        "课程物理关系",
                    ],
                }
            output.stage_payload["reference_only"] = True
            output.stage_payload["preserve_pending_action"] = True
            if isinstance(pending_action, dict):
                dialogue_state(session)["pending_action"] = deepcopy(
                    pending_action
                )
            completion_error = None
        elif design_summary_request:
            summary_snapshot = design_state_snapshot(session)
            requested_summary_fields = (
                resolved_value
                if isinstance(resolved_value, list)
                and all(isinstance(item, str) for item in resolved_value)
                else None
            )
            dialogue_state(session)["last_presented_design_summary"] = {
                "display_order": [
                    "research_object",
                    "course_relationship",
                    "learning_objective",
                    "research_question",
                    "baseline_comparisons",
                    "theoretical_framework",
                    "hypothesis",
                    "expected_phenomenon",
                    "conceptual_structure",
                ],
                "values": deepcopy(summary_snapshot),
            }
            output = StepOutput(
                assistant_message=(
                    "可以。下面是目前已经保存的设计内容，我只把它们整理出来，"
                    "不会改变你的实验方向或进度：\n\n"
                    f"{format_design_summary(session, requested_summary_fields)}\n\n"
                    "如果其中某一项需要补充或改写，直接指出那一项和你的新表述就可以。"
                ),
                stage_payload={
                    "read_only_design_summary": True,
                    "design_state": summary_snapshot,
                    "preserve_pending_action": True,
                },
                student_task=None,
            )
            session.turn_context = {}
            completion_error = None
        elif final_summary_confirmation_turn:
            output = _guided_summary_completion_output(session)
            session.turn_context = {}
            completion_error = None
        elif summary_completed_this_turn:
            output = _guided_summary_completion_output(session)
            session.turn_context = {}
            completion_error = None
        elif dialogue_question_turn:
            try:
                generated_answer = self.generator.generate(
                    session,
                    message,
                )
            finally:
                session.turn_context = {}
            if (
                handled_stage is Stage.IDEA_BRAINSTORMING
                and session.interaction_state is InteractionState.GUIDED_DESIGN
                and has_idea_development(session)
            ):
                output = build_gap_output(session, "")
                output.assistant_message = generated_answer.assistant_message
                output.assumptions = generated_answer.assumptions
                output.warnings = generated_answer.warnings
                output.stage_payload["answered_student_questions"] = deepcopy(
                    student_questions
                )
            else:
                output = generated_answer
                output.stage_payload["answered_student_questions"] = deepcopy(
                    student_questions
                )
            if not has_structured_turn_updates:
                output.stage_payload["preserve_pending_action"] = True
            completion_error = None
        elif (
            feedback_only_turn
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and handled_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            and isinstance(pending_action, dict)
            and len(str(pending_action.get("candidate_answer") or "").strip()) >= 20
        ):
            # A correction such as “我上一轮已经总结了” carries no new
            # design value.  Recover the substantive candidate attached to
            # the final-summary pending action instead of asking the student
            # to repeat or explain the correction.
            recovered_summary = str(
                pending_action.get("candidate_answer") or ""
            ).strip()
            apply_stage_field_updates(
                session,
                [
                    {
                        "field": "student_summary",
                        "operation": "REPLACE",
                        "value": recovered_summary,
                        "provenance": "STUDENT_CONFIRMED",
                    }
                ],
                stage=handled_stage,
                provenance="STUDENT_CONFIRMED",
            )
            summary_completed_this_turn = _persist_guided_student_summary(
                session,
                recovered_summary,
                {"pending_answer_status": "CLEAR"},
            )
            output = _guided_summary_completion_output(session)
            output.stage_payload["summary_recovered_from_previous_turn"] = True
            session.turn_context = {}
            completion_error = None
        elif feedback_only_turn:
            correction_items = semantic_updates.get("correction_items", [])
            correction_items = (
                correction_items if isinstance(correction_items, list) else []
            )
            affected_fields = list(
                dict.fromkeys(
                    str(field)
                    for item in correction_items
                    if isinstance(item, dict)
                    for field in item.get("affected_fields", [])
                    if isinstance(field, str) and field in _STUDENT_FIELD_LABELS
                )
            )
            affected_labels = [
                _STUDENT_FIELD_LABELS[field] for field in affected_fields
            ]
            if session.interaction_state is InteractionState.EMVR_DIRECT:
                feedback_message = (
                    "收到这项校正。它不会被误记为新的实验要求，现有实验草稿也不会因此回退。"
                    + (
                        f"目前只需要你给出“{'、'.join(affected_labels)}”的目标表述；"
                        if affected_labels
                        else "请指出需要修订的是物理模型、Unity交互还是展示方式，并给出目标表述；"
                    )
                    + "我会只调整对应部分。"
                )
            else:
                feedback_message = (
                    "你提醒得对，我先不把这句话当成新的实验内容，前面已经确定的想法也继续保留。"
                    + (
                        f"现在只需要补充“{'、'.join(affected_labels)}”应该怎样表达。"
                        if affected_labels
                        else "请告诉我具体要改哪一项，以及你希望怎样表达，我们就从那里接着完善。"
                    )
                )
            output = StepOutput(
                assistant_message=feedback_message,
                stage_payload={
                    "feedback_received": True,
                    "preserve_pending_action": True,
                },
                student_task=None,
            )
            session.turn_context = {}
            completion_error = None
        elif emvr_stage_entry_turn:
            output = _emvr_stage_entry_output(session, handled_stage)
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
                clarification_candidate = message
                clarification_updates = turn_intent.get("semantic_updates", {})
                # A control-only turn is never a new design answer.  In
                # particular, a low-confidence ADVANCE/ACCEPT parse must not
                # overwrite an already bound recovery candidate with the
                # literal control utterance.  The candidate remains available
                # for a later high-confidence semantic decision or UI action.
                existing_bound_candidate = bool(
                    isinstance(pending_action, dict)
                    and str(pending_action.get("candidate_answer") or "").strip()
                    and pending_action.get("candidate_binding_authorized") is True
                )
                low_confidence_control = str(
                    turn_intent.get("source") or ""
                ).startswith(
                    (
                        f"LOW_CONFIDENCE_{UserIntent.ADVANCE_STAGE.value}:",
                        f"LOW_CONFIDENCE_{UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value}:",
                    )
                )
                if (
                    existing_bound_candidate
                    and low_confidence_control
                    and not has_structured_turn_updates
                ):
                    clarification_candidate = ""
                required_facet = required_pending_facet_id(pending_action)
                facet_explicitly_missing = bool(
                    required_facet
                    and isinstance(clarification_updates, dict)
                    and any(
                        isinstance(item, dict)
                        and item.get("facet_id") == required_facet
                        and item.get("status") == "MISSING"
                        for item in clarification_updates.get("facet_updates", [])
                    )
                )
                recoverable_field = recoverable_pending_field(pending_action)
                recoverable_exact_field = bool(
                    # The complete EMVR brief is intentionally a free-form
                    # statement, so retaining the whole answer in that one
                    # authoritative field is safe. Other prompts name a
                    # narrower field: a long answer may also contain actions,
                    # variables and observations, and must be semantically
                    # split rather than copied wholesale into that field.
                    recoverable_field == "experiment_brief"
                    and isinstance(pending_action, dict)
                    and pending_action.get("interaction_state")
                    == InteractionState.EMVR_DIRECT.value
                )
                if not recoverable_exact_field and isinstance(
                    clarification_updates, dict
                ) and (
                    clarification_updates.get("no_direction") is True
                    or clarification_updates.get("pending_answer_status") == "MISSING"
                    or facet_explicitly_missing
                    or clarification_updates.get("course_scope_status") == "OUT_OF_SCOPE"
                ):
                    clarification_candidate = ""
                pending_action = record_pending_clarification(
                    session,
                    clarification_candidate,
                    allow_exact_field_binding=recoverable_exact_field,
                ) or pending_action
                output = clarification_output(
                    pending_action,
                    session.interaction_state,
                )
            session.turn_context = {}
            completion_error = None
        elif idea_facet_reference_turn:
            output = build_facet_reference_output(session)
            session.turn_context = {}
            completion_error = None
        elif guided_stage_reference_turn:
            output = _guided_reference_output(session)
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
            _project_committed_stage_fields(session, handled_stage, output)
            if session.interaction_state is InteractionState.EMVR_DIRECT:
                _prepare_emvr_stage_output(session, handled_stage, output)
                # If the student tried to continue from an unanswered EMVR
                # entry question, this generated draft is the requested
                # professional reference. Keep the stage active for review
                # without showing a stale completion warning.
                completion_error = None
            if (
                session.interaction_state is InteractionState.GUIDED_DESIGN
                and handled_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
                and output.stage_payload.get("student_summary_received") is True
            ):
                summary_completed_this_turn = _persist_guided_student_summary(
                    session,
                    generation_message,
                    {"pending_answer_status": "CLEAR"},
                )
                if summary_completed_this_turn:
                    output = _guided_summary_completion_output(session)
            if (
                transitioned_from_stage is None
                and pending_action is not None
                and intent_name
                in {
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                }
            ):
                _remove_repeated_guided_question(
                    output,
                    pending_action,
                    generation_message,
                    session.interaction_state,
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
                if _guided_stage_should_auto_advance(
                    session,
                    handled_stage,
                    output,
                    pending_action,
                    intent_name,
                    semantic_updates,
                ):
                    completed_stage = handled_stage
                    completed_output = output
                    _persist_guided_stage_draft(
                        session,
                        completed_stage,
                        completed_output.stage_payload,
                    )
                    # Completion validation reads the stored visualization for
                    # the prediction stage.  Save this completed-stage artifact
                    # before advancing, then let the normal end-of-turn path
                    # store the destination stage entry.
                    session.stage_outputs[completed_stage.value] = {
                        "revision": session.revision + 1,
                        **completed_output.to_dict(),
                    }
                    try:
                        self._validate_completion(session, completed_stage)
                        self._advance(session, completed_stage)
                    except StageCompletionError as exc:
                        completion_error = str(exc)
                        _prepare_guided_stage_completion(
                            session,
                            completed_stage,
                            completed_output,
                        )
                    else:
                        transitioned_from_stage = completed_stage
                        handled_stage = session.current_stage
                        definition = STAGES_BY_ID[handled_stage]
                        next_output = guided_stage_entry_output(session)
                        next_output.assistant_message = (
                            f"{completed_output.assistant_message.rstrip()}\n\n"
                            "这个环节已经能支撑后面的设计，我们直接接着完善下一项。\n\n"
                            f"{next_output.assistant_message}"
                        )
                        next_output.visualization = (
                            completed_output.visualization
                            or next_output.visualization
                        )
                        next_output.stage_payload["auto_advanced_from_stage"] = (
                            completed_stage.value
                        )
                        output = next_output
                        guided_stage_entry_turn = True
                        emvr_stage_entry_turn = False
                        final_quality_review = (
                            handled_stage
                            is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
                        )
                        quality_review = evaluate_design_quality(
                            session,
                            semantic_updates.get("quality_assessment"),
                            final_review=final_quality_review,
                        )
                        turn_context["quality_review"] = deepcopy(quality_review)
                        completion_error = None
                else:
                    _prepare_guided_stage_completion(
                        session,
                        handled_stage,
                        output,
                    )
        # Some valid turns are served by a reference/entry/recovery branch
        # rather than the ordinary generator branch above.  Reconcile the
        # final visible payload from committed canonical state in every route,
        # so a supplement cannot be saved internally yet omitted from the
        # page merely because the response took a different dialogue path.
        _project_committed_stage_fields(session, handled_stage, output)
        multi_act_notice = _multi_act_student_notice(
            semantic_updates,
            session.interaction_state,
            turn_design_diff,
        )
        if multi_act_notice and turn_intent.get("dialogue_acts"):
            output.assistant_message = (
                f"{multi_act_notice}\n\n{output.assistant_message}"
                if output.assistant_message
                else multi_act_notice
            )
        correction_notice = _self_correction_notice(
            semantic_updates,
            turn_design_diff,
            session.interaction_state,
        )
        if correction_notice:
            output.assistant_message = (
                f"{correction_notice}\n\n{output.assistant_message}"
                if output.assistant_message
                else correction_notice
            )
        if version_results and not version_only_turn:
            version_notice = "\n\n".join(
                item for item in (format_version_result(result) for result in version_results) if item
            )
            if version_notice:
                output.assistant_message = f"{version_notice}\n\n{output.assistant_message}".strip()
        if final_quality_review and not quality_review_requested:
            quality_notice = format_quality_review(
                quality_review,
                session.interaction_state,
                final_review=True,
            )
            if quality_notice and (
                transitioned_from_stage is not None
                or guided_stage_entry_turn
                or emvr_stage_entry_turn
            ):
                output.assistant_message = f"{quality_notice}\n\n{output.assistant_message}".strip()
        if (
            not final_quality_review
            and (guided_stage_entry_turn or emvr_stage_entry_turn)
            and isinstance(quality_review.get("priority_issue"), dict)
        ):
            priority_issue = quality_review["priority_issue"]
            finding = str(priority_issue.get("finding") or "").strip()
            next_question = str(priority_issue.get("student_question") or "").strip()
            # In EMVR, the stage entry already asks the exact Builder-facing
            # question for the current deliverable.  A generic quality issue
            # may still be shown in the side panel, but it must not replace
            # that question and send the conversation back to an earlier
            # field.  Guided mode keeps the existing coaching bridge.
            quality_may_steer_response = not (
                session.interaction_state is InteractionState.EMVR_DIRECT
                and emvr_stage_entry_turn
                and bool(output.student_task)
            )
            if finding and quality_may_steer_response:
                bridge = (
                    f"从当前方案的衔接看，{finding}"
                    if session.interaction_state is InteractionState.EMVR_DIRECT
                    else f"结合前面已经确定的内容，{finding}"
                )
                output.assistant_message = f"{output.assistant_message.rstrip()}\n\n{bridge}"
            if next_question and quality_may_steer_response:
                output.student_task = next_question
        output.stage_payload["quality_review"] = public_quality_review(
            quality_review,
            max_issues=2 if final_quality_review else 1,
        )
        if recorded_version is not None:
            output.stage_payload["design_version"] = {
                key: deepcopy(recorded_version.get(key))
                for key in ("version_id", "reason", "source", "changed_fields")
            }
        _prevent_unrequested_scene_replay(
            session,
            output,
            pending_action,
            message,
            turn_intent,
        )
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
                apply_design_updates(
                    session,
                    [
                        {
                            "field": "research_object",
                            "operation": "REPLACE",
                            "value": outline_seed.get("core_phenomenon", ""),
                        },
                        {
                            "field": "course_relationship",
                            "operation": "REPLACE",
                            "value": outline_seed.get("course_relationships", []),
                        },
                    ],
                    provenance="AGENT_SUGGESTION",
                )
                sync_design_state_to_legacy(session)
                refresh_idea_development(session)
                decorate_outline_output(output, development)
            elif has_idea_development(session):
                output.stage_payload.setdefault(
                    "idea_development_status",
                    public_idea_development_status(
                        session.design_context["idea_development"]
                    ),
                )
        record_seen_scenes(
            session,
            output.stage_payload.get("exploration_scenes"),
        )
        if (
            output.stage_payload.get("clarification_required") is not True
            and output.stage_payload.get("preserve_pending_action") is not True
        ):
            save_pending_action(session, handled_stage, output)
        output.stage_payload["design_state"] = design_state_snapshot(session)
        output.stage_payload["stage_design_state"] = stage_design_state_snapshot(session)
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
            (
                (request.complete_stage or explicit_transition_intent)
                and not pre_transition_attempted
            )
            or (
                session.interaction_state is InteractionState.EMVR_DIRECT
                and handled_stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
                and output.stage_payload.get("proposal_status") == "complete"
            )
        ) and output.stage_payload.get("request_rejected") is not True
        if summary_completed_this_turn:
            # A complete student-written summary is the terminal action in
            # guided mode.  Do not add an otherwise content-free confirmation
            # turn after the student has already done the requested synthesis.
            should_complete = True
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

        dialogue_state(session)["last_task_plan"] = finalize_turn_task_plan(
            turn_intent.get("task_plan"),
            turn_design_diff,
            response_generated=True,
            transition_requested=explicit_transition_intent,
            transition_completed=bool(
                transitioned_from_stage is not None
                or session.status is WorkflowStatus.COMPLETE
                or (
                    explicit_transition_intent
                    and completion_error is None
                    and handled_stage.value in session.completed_stages
                )
            ),
        )

        if session.interaction_state is InteractionState.EMVR_DIRECT:
            task_report = build_emvr_task_report(session)
        else:
            task_report = None

        state = session.model_context.get("dialogue_state", {})
        if isinstance(state, dict):
            state["carried_context"] = build_carried_context(session)

        next_stage = session.next_stage.value if session.next_stage else None
        response_message = output.assistant_message
        if (
            session.interaction_state is InteractionState.EMVR_DIRECT
            and session.status is WorkflowStatus.COMPLETE
        ):
            response_message = (
                f"{response_message.rstrip()}\n\n"
                "完整设计总结PDF已经生成，其中包含学习目标、Unity VR实验物体清单、"
                "交互与理论计算关系、实验流程以及设计局限。你可以在右侧“任务报告”中"
                "展开各部分，并分别下载学生版设计报告和用于 EMVR Builder Pack Gate 1 的输入PDF。"
            )
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
            "assistant_message": response_message,
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
            "turn_id": request.turn_id,
        }
        if task_report is not None:
            response["task_report"] = task_report
            response["builder_handoff_status"] = builder_handoff_status(session)
            response["report_ready"] = session.status is WorkflowStatus.COMPLETE
            if response["report_ready"]:
                response["report_url"] = f"/v1/designs/{session.design_id}/report.pdf"
                response["builder_input_ready"] = True
                response["builder_input_url"] = (
                    f"/v1/designs/{session.design_id}/builder-gate1-input.pdf"
                )
        if (
            session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.status is WorkflowStatus.COMPLETE
        ):
            response["guided_export_ready"] = True
            response["guided_export_url"] = (
                f"/v1/designs/{session.design_id}/guided-summary.txt"
            )
        _cache_turn_response(session, request, response)
        self.store.save(session, expected_revision=expected_revision)
        return response

    def _lock_for_design(self, design_id: str) -> RLock:
        digest = hashlib.sha256(design_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(self._session_locks)
        return self._session_locks[index]

    def get_design(self, design_id: str, include_history: bool = False) -> dict[str, Any]:
        session = self.store.get(design_id)
        result = session.to_dict(include_history=include_history)
        result["quality_review"] = public_quality_review(
            evaluate_design_quality(
                session,
                final_review=(session.status is WorkflowStatus.COMPLETE),
            ),
            max_issues=8 if session.status is WorkflowStatus.COMPLETE else 2,
        )
        result["recent_versions"] = execute_version_request(
            session, {"action": "VIEW_RECENT"}
        )
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            result["task_report"] = build_emvr_task_report(session)
            result["builder_handoff_status"] = builder_handoff_status(session)
            result["report_ready"] = session.status is WorkflowStatus.COMPLETE
            if result["report_ready"]:
                result["report_url"] = f"/v1/designs/{session.design_id}/report.pdf"
                result["builder_input_ready"] = True
                result["builder_input_url"] = (
                    f"/v1/designs/{session.design_id}/builder-gate1-input.pdf"
                )
        elif session.status is WorkflowStatus.COMPLETE:
            result["guided_export_ready"] = True
            result["guided_export_url"] = (
                f"/v1/designs/{session.design_id}/guided-summary.txt"
            )
        return result

    def resume_design(self, design_id: str, resume_token: str) -> dict[str, Any]:
        if not isinstance(resume_token, str) or not resume_token.strip():
            raise DesignAccessDenied("A valid design resume token is required.")
        with self._lock_for_design(design_id):
            session = self.store.get(design_id)
            expected_revision = session.revision
            candidate = hashlib.sha256(resume_token.strip().encode("utf-8")).hexdigest()
            stored = str(session.model_context.get("resume_token_hash") or "")
            if not stored or not hmac.compare_digest(stored, candidate):
                raise DesignAccessDenied("A valid design resume token is required.")
            access_token = secrets.token_urlsafe(32)
            next_resume_token = secrets.token_urlsafe(32)
            session.access_token_hash = hashlib.sha256(
                access_token.encode("utf-8")
            ).hexdigest()
            session.model_context["resume_token_hash"] = hashlib.sha256(
                next_resume_token.encode("utf-8")
            ).hexdigest()
            session.revision += 1
            self.store.save(session, expected_revision=expected_revision)
        result = self.get_design(design_id)
        result["design_access_token"] = access_token
        result["design_resume_token"] = next_resume_token
        return result

    def render_guided_summary_text(self, design_id: str) -> bytes:
        session = self.store.get(design_id)
        if session.interaction_state is not InteractionState.GUIDED_DESIGN:
            raise StageCompletionError("这项导出只适用于引导模式。")
        if session.status is not WorkflowStatus.COMPLETE:
            raise StageCompletionError("请先完成学生总结，再导出当前设计。")
        synthesis = session.design_context.get("synthesis", {})
        summary = (
            str(synthesis.get("student_summary") or "").strip()
            if isinstance(synthesis, dict)
            else ""
        )
        if not summary:
            raise StageCompletionError("尚未找到学生自己完成的总结。")
        stage_state = stage_design_state_snapshot(session)
        stage_lines = [
            f"• {label}：{stage_state[field]}"
            for field, label in (
                ("independent_variable", "自变量"),
                ("observations", "观察量"),
                ("controlled_conditions", "控制条件"),
                ("procedure_steps", "实验流程"),
                ("visualization_plan", "可视化方式"),
                ("result_interpretation", "结果解释"),
                ("limitations", "局限与边界"),
            )
            if stage_state.get(field)
        ]
        confirmed_record = format_design_summary(session)
        if stage_lines:
            confirmed_record = (
                f"{confirmed_record}\n" + "\n".join(stage_lines)
            )
        body = (
            "ECE329 实验设计学生总结\n"
            f"设计编号：{session.design_id}\n\n"
            "【学生完成的总结】\n"
            f"{summary}\n\n"
            "【已确认并保留的设计记录】\n"
            f"{confirmed_record}\n"
        )
        return body.encode("utf-8")

    def render_report_pdf(self, design_id: str) -> bytes:
        session = self.store.get(design_id)
        if session.status is not WorkflowStatus.COMPLETE:
            raise StageCompletionError("EMVR设计完成后才会生成PDF总结。")
        return render_emvr_report_pdf(session)

    def render_builder_input_pdf(self, design_id: str) -> bytes:
        session = self.store.get(design_id)
        if session.interaction_state is not InteractionState.EMVR_DIRECT:
            raise StageCompletionError("Builder Gate 1输入PDF只适用于EMVR设计。")
        if session.status is not WorkflowStatus.COMPLETE:
            raise StageCompletionError("EMVR设计完成后才会生成Builder Gate 1输入PDF。")
        return render_builder_gate1_input_pdf(session)

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
        turn_id = _validated_turn_id(data.get("turn_id"))
        version_request = None
        if data.get("version_request") is not None:
            version_request = normalize_version_request(data.get("version_request"))
            if version_request is None:
                raise ValueError("version_request is invalid")
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
            turn_id=turn_id,
            version_request=version_request,
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
        if (
            turn_context.get("more_brainstorm_requested") is True
            or turn_context.get("stage_one_no_direction") is True
        ):
            # Breadth requests and an explicitly missing direction may still
            # carry the student's full sentence into response planning. They
            # are not research-direction updates and must not be committed as
            # topic_anchor/current_focus by this compatibility persistence
            # hook. Facet-reference control turns continue through the legacy
            # path because it maintains their non-repeating reference cycle.
            idea["course_scope_confirmed"] = True
            idea.setdefault("brainstorm_phase", BREADTH_EXPLORATION)
            if turn_context.get("stage_one_no_direction") is True:
                idea["directionless_browse_active"] = True
            elif message.strip():
                # Keep a parser-degraded but course-grounded first idea as
                # internal recovery context.  It is not yet a canonical design
                # field and therefore cannot pollute the final summary.
                idea["unresolved_direction_candidate"] = message.strip()[:2000]
            return
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
        idea.pop("unresolved_direction_candidate", None)
        idea.pop("directionless_browse_active", None)
        for key in (
            "selected_focus",
            "core_phenomenon",
            "interest_description",
            "direction_summary",
        ):
            value = str(turn_context.get(key) or "").strip()
            if value:
                idea[key] = value
        idea["direction_locked"] = bool(turn_context.get("direction_locked"))
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
            set_baseline_comparisons(session, output_comparisons)
        elif isinstance(context_comparisons, list):
            idea["standard_comparisons"] = deepcopy(context_comparisons)
            set_baseline_comparisons(session, context_comparisons)
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
            if stage is Stage.IDEA_BRAINSTORMING:
                readiness = emvr_stage_one_readiness(
                    session.design_context.get("emvr_design", {})
                )
                if not readiness["ready"]:
                    missing = "、".join(readiness["missing"][:3])
                    raise StageCompletionError(
                        f"实验方向还需要补齐：{missing}。"
                        "我会继续围绕当前想法逐项确认，不会把模式指令或单个对象当成完整方案。"
                    )
            current = next_due_builder_requirement(session, stage)
            if current is not None:
                raise StageCompletionError(
                    f"在继续之前，请先明确{current['label']}。{current['question']}"
                )
            stage_output = session.stage_outputs.get(stage.value, {})
            stage_payload = (
                stage_output.get("stage_payload", {})
                if isinstance(stage_output, dict)
                else {}
            )
            if (
                stage is not Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
                and isinstance(stage_payload, dict)
                and stage_payload.get("awaiting_user_design_input") is True
                and not _has_structured_stage_content(session, stage)
            ):
                raise StageCompletionError(
                    "请先结合当前VR实验回答这一问；如果暂时不确定，也可以让我先给一版专业参考。"
                )
            if stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:
                try:
                    validate_builder_requirements(session)
                    validate_emvr_report_completeness(session)
                except ValueError as exc:
                    raise StageCompletionError(str(exc)) from exc
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
            structured = stage_design_state_snapshot(session)
            structured_fields = _STRUCTURED_STAGE_COMPLETION_FIELDS.get(stage, ())
            has_structured_content = any(
                structured.get(field) for field in structured_fields
            )
            if not has_structured_content and not any(
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
