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
from .emvr_design import apply_emvr_field_updates
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
from .reporting import (
    build_emvr_task_report,
    render_emvr_report_pdf,
    stage_report_section,
    validate_emvr_report_completeness,
)
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
            "student_summary_confirmed": True,
            "completion_source": "SEMANTIC_SUMMARY_REVIEW",
        }
    )
    return True


def _guided_summary_completion_output() -> StepOutput:
    return StepOutput(
        assistant_message=(
            "你已经把研究问题、主要比较、预期现象和课程关系串起来了。"
            "我按你的原意保存，这次实验设计到这里就完成了。"
        ),
        stage_payload={
            "student_summary_received": True,
            "student_summary_confirmed": True,
            "final_proposal_generated": False,
        },
        student_task=None,
    )


_EMVR_STAGE_LEADS: dict[Stage, str] = {
    Stage.IDEA_BRAINSTORMING: "我先把你提出的现象、对象和VR操作整理成一个设计起点。",
    Stage.COURSE_MAPPING_AND_DIRECTION: "沿用已经确定的想法，我把它和ECE329课程内容的联系整理出来了。",
    Stage.LEARNING_OBJECTIVES: "这一步先把实验真正要支持的学习目标摆在一起，后面的交互和反馈都要能对应它们。",
    Stage.RESEARCH_QUESTION: "结合前面的方向和学习目标，我把模拟实验要回答的问题收得更清楚了一些。",
    Stage.THEORETICAL_FRAMEWORK: "下面把Unity中真正参与计算的量和只用于帮助理解的画面分开。",
    Stage.HYPOTHESIS: "根据前面的课程关系，我整理了一版可以被后续显示结果检验的预期。",
    Stage.CONCEPTUAL_OR_VR_SETUP: "现在把已有想法转成Unity VR中的对象、操作、计算和反馈关系。",
    Stage.VARIABLES_AND_CONDITIONS: "我把参数控制、观察结果和需要固定的条件对应到了Unity交互中。",
    Stage.CONCEPTUAL_PROCEDURE: "根据前面已经确定的对象和变量，我整理了一套可重复比较的VR学习流程。",
    Stage.EXPECTED_DATA_VISUALIZATION: "这一步把理论预测如何显示、又如何随Unity参数更新说明清楚。",
    Stage.RESULT_INTERPRETATION: "为了避免只看见动画却不会解释，我把几类可能结果和检查顺序整理出来了。",
    Stage.DESIGN_VALUE_AND_LIMITATIONS: "最后检查这套设计能帮助学生理解什么，以及哪些地方不能过度解释。",
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "你的EMVR模拟实验设计已经汇总完成，下面是最终报告中保留的主要内容。",
}

_EMVR_INTERACTIVE_ENTRY_STAGES = {
    Stage.IDEA_BRAINSTORMING,
    Stage.LEARNING_OBJECTIVES,
    Stage.RESEARCH_QUESTION,
    Stage.HYPOTHESIS,
    Stage.CONCEPTUAL_OR_VR_SETUP,
    Stage.VARIABLES_AND_CONDITIONS,
    Stage.CONCEPTUAL_PROCEDURE,
    Stage.EXPECTED_DATA_VISUALIZATION,
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


def _emvr_stage_entry_output(session: DesignSession, stage: Stage) -> StepOutput:
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
    return StepOutput(
        assistant_message=f"{acknowledgement}{lead}",
        stage_payload={
            "emvr_guided_entry": True,
            "awaiting_user_design_input": True,
            "preserved_context": deepcopy(context),
            "pending_action": {
                "type": "ANSWER_EMVR_STAGE_QUESTION",
                "interaction_state": InteractionState.EMVR_DIRECT.value,
                "subject": stage.value,
                "proposal": {"carried_context": deepcopy(context)},
                "question": question,
                "advance_on_accept": False,
                "allowed_intents": [
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.REQUEST_MORE_EXAMPLES.value,
                    UserIntent.RETURN_TO_PREVIOUS_POINT.value,
                    UserIntent.NEW_TOPIC.value,
                    UserIntent.UNCLEAR.value,
                ],
            },
        },
        student_task=question,
    )


def _prepare_emvr_stage_output(
    stage: Stage,
    output: StepOutput,
) -> None:
    """Make the stage artifact visible and wait for a contextual decision."""

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
            "请看看我有没有漏掉你原本想保留的现象、对象或操作；"
            "你可以直接补充或修改，觉得这份起点准确也可以告诉我继续。"
        )
    else:
        task = (
            "这份阶段草稿会写进右侧任务报告。你可以直接指出要修改或补充的地方；"
            "如果符合你的想法，告诉我继续就会保留它并进入下一部分。"
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


def _persist_emvr_brief(
    session: DesignSession,
    message: str,
    intent_name: str,
    stage: Stage | None = None,
) -> None:
    content_stage = stage or session.current_stage
    if (
        session.interaction_state is not InteractionState.EMVR_DIRECT
        or content_stage is not Stage.IDEA_BRAINSTORMING
        or intent_name
        not in {
            UserIntent.ANSWER_CURRENT_QUESTION.value,
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            UserIntent.NEW_TOPIC.value,
        }
        or len(message.strip()) < 16
    ):
        return
    emvr_design = session.design_context.setdefault("emvr_design", {})
    if not isinstance(emvr_design, dict):
        emvr_design = {}
        session.design_context["emvr_design"] = emvr_design
    normalized_message = message.strip()
    original_brief = str(emvr_design.get("brief") or "").strip()
    revisions = emvr_design.get("brief_revisions", [])
    if not isinstance(revisions, list):
        revisions = []
    if intent_name == UserIntent.NEW_TOPIC.value or not original_brief:
        original_brief = normalized_message
        revisions = []
    elif (
        normalized_message != original_brief
        and normalized_message not in revisions
    ):
        revisions.append(normalized_message)
        revisions = revisions[-8:]
    current_brief = "；补充：".join([original_brief, *revisions])
    emvr_design["brief"] = original_brief
    emvr_design["brief_revisions"] = revisions
    emvr_design["current_brief"] = current_brief
    idea = session.design_context.setdefault("idea", {})
    if isinstance(idea, dict):
        idea["current_summary"] = current_brief
        idea["main_direction"] = current_brief
        idea["current_focus"] = current_brief


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
    content: Any = resolved_value if resolved_value not in (None, "", [], {}) else message
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
    structured_update = (
        semantic_updates.get("emvr_design_update")
        if isinstance(semantic_updates, dict)
        else None
    )
    if isinstance(structured_update, dict) and structured_update:
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
    if intent_name not in {
        UserIntent.ANSWER_CURRENT_QUESTION.value,
        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
    }:
        return
    resolved_value = turn_intent.get("resolved_value")
    content: Any = resolved_value if resolved_value not in (None, "", [], {}) else message
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
                        "report_ready": True,
                        "report_url": f"/v1/designs/{session.design_id}/report.pdf",
                    }
                )
            return response
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
        idea_for_direction_lock = session.design_context.get("idea", {})
        semantic_for_direction_lock = turn_intent.get("semantic_updates", {})
        if (
            input_kind != UNREASONABLE_REQUEST
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and session.current_stage is Stage.IDEA_BRAINSTORMING
            and not has_idea_development(session)
            and isinstance(idea_for_direction_lock, dict)
            and idea_for_direction_lock.get("course_scope_confirmed") is True
            and turn_intent.get("intent")
            in {
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            }
            and isinstance(turn_intent.get("resolved_value"), str)
            and str(turn_intent.get("resolved_value") or "").strip()
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
            and isinstance(idea_for_direction_lock, dict)
            and idea_for_direction_lock.get("direction_locked") is True
            and turn_intent.get("intent") == UserIntent.NEW_TOPIC.value
            and not (
                isinstance(semantic_for_direction_lock, dict)
                and semantic_for_direction_lock.get("topic_change_explicit") is True
            )
        ):
            # Once the student has chosen or described a direction, incidental
            # new objects and examples refine that same design.  Replacing the
            # whole topic requires an explicit semantic decision, not a phrase
            # match or a model's unsupported NEW_TOPIC label.
            turn_intent.update(
                {
                    "intent": (
                        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
                        if isinstance(pending_action, dict)
                        and pending_action.get("type")
                        in {"CONFIRM_STAGE_OR_MODIFY", "CONFIRM_OR_MODIFY"}
                        else UserIntent.ANSWER_CURRENT_QUESTION.value
                    ),
                    "target": str(
                        idea_for_direction_lock.get("direction_summary")
                        or idea_for_direction_lock.get("selected_focus")
                        or "locked_stage_one_direction"
                    ),
                    "advance_requested": False,
                    "preserve_current_design": True,
                    "source": "SEMANTIC_DIRECTION_LOCK",
                }
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
        if (
            input_kind != UNREASONABLE_REQUEST
            and session.interaction_state is InteractionState.EMVR_DIRECT
            and emvr_direction_exists
            and turn_intent.get("intent") == UserIntent.NEW_TOPIC.value
            and not (
                isinstance(semantic_for_direction_lock, dict)
                and semantic_for_direction_lock.get("topic_change_explicit") is True
            )
        ):
            # In EMVR, additional objects, interactions, observations and
            # questions refine the existing experiment.  Resetting the whole
            # workflow requires an explicit semantic topic-change decision.
            turn_intent.update(
                {
                    "intent": (
                        UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
                        if isinstance(pending_action, dict)
                        and pending_action.get("type")
                        in {"CONFIRM_STAGE_OR_MODIFY", "CONFIRM_OR_MODIFY"}
                        else UserIntent.ANSWER_CURRENT_QUESTION.value
                    ),
                    "target": (
                        str(pending_action.get("subject") or "emvr_design")
                        if isinstance(pending_action, dict)
                        else "emvr_design"
                    ),
                    "advance_requested": False,
                    "preserve_current_design": True,
                    "source": "SEMANTIC_EMVR_DIRECTION_LOCK",
                }
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
        _persist_emvr_brief(
            session,
            resolved_student_message,
            content_intent_name,
            content_stage,
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
        substantive_guided_reply = bool(
            intent_name == UserIntent.ANSWER_CURRENT_QUESTION.value
            and input_kind != UNREASONABLE_REQUEST
        )
        idea_facet_reference_turn = bool(
            handled_stage is Stage.IDEA_BRAINSTORMING
            and session.interaction_state is InteractionState.GUIDED_DESIGN
            and intent_name == UserIntent.REQUEST_MORE_EXAMPLES.value
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
        if final_summary_confirmation_turn:
            output = _guided_summary_completion_output()
            session.turn_context = {}
            completion_error = None
        elif summary_completed_this_turn:
            output = _guided_summary_completion_output()
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
                pending_action = record_pending_clarification(
                    session,
                    message,
                ) or pending_action
                output = clarification_output(pending_action)
            session.turn_context = {}
            completion_error = None
        elif idea_facet_reference_turn:
            output = build_facet_reference_output(session)
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
            if session.interaction_state is InteractionState.EMVR_DIRECT:
                _prepare_emvr_stage_output(handled_stage, output)
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
                    output = _guided_summary_completion_output()
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

        if session.interaction_state is InteractionState.EMVR_DIRECT:
            task_report = build_emvr_task_report(session)
        else:
            task_report = None

        state = session.model_context.get("dialogue_state", {})
        if isinstance(state, dict):
            state["carried_context"] = build_carried_context(session)

        self.store.save(session, expected_revision=expected_revision)
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
                "展开各部分并下载PDF。"
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
        }
        if task_report is not None:
            response["task_report"] = task_report
            response["report_ready"] = session.status is WorkflowStatus.COMPLETE
            if response["report_ready"]:
                response["report_url"] = f"/v1/designs/{session.design_id}/report.pdf"
        return response

    def _lock_for_design(self, design_id: str) -> RLock:
        digest = hashlib.sha256(design_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(self._session_locks)
        return self._session_locks[index]

    def get_design(self, design_id: str, include_history: bool = False) -> dict[str, Any]:
        session = self.store.get(design_id)
        result = session.to_dict(include_history=include_history)
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            result["task_report"] = build_emvr_task_report(session)
            result["report_ready"] = session.status is WorkflowStatus.COMPLETE
            if result["report_ready"]:
                result["report_url"] = f"/v1/designs/{session.design_id}/report.pdf"
        return result

    def render_report_pdf(self, design_id: str) -> bytes:
        session = self.store.get(design_id)
        if session.status is not WorkflowStatus.COMPLETE:
            raise StageCompletionError("EMVR设计完成后才会生成PDF总结。")
        return render_emvr_report_pdf(session)

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
            ):
                raise StageCompletionError(
                    "请先结合当前VR实验回答这一问；如果暂时不确定，也可以让我先给一版专业参考。"
                )
            if stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:
                try:
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
