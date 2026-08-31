from __future__ import annotations

from copy import deepcopy
from typing import Any

from .design_state import baseline_comparisons_snapshot, format_design_summary
from .knowledge_base import KNOWLEDGE
from .models import DesignSession, StepOutput
from .stages import IDEA_DEVELOPMENT_FACETS


CLEAR = "CLEAR"
MISSING = "MISSING"

_FACET_QUESTION: dict[str, str] = {
    "learning_objective": (
        "为了让这个实验的学习目标清楚，你希望自己完成实验后能够解释、判断或比较什么？"
        "请用自己的话描述一种最重要的能力。"
    ),
    "research_question": (
        "请把当前想法压缩成一个能够回答的问题：你想比较什么条件，并观察哪种电磁现象怎样改变？"
    ),
    "hypothesis": (
        "根据当前理论依据，你预计关键条件发生变化时，观察到的现象会朝什么方向变化？"
        "请同时说出你的物理理由。"
    ),
    "conceptual_structure": (
        "为了让这个想法能够被展示和比较，实验中至少需要哪些对象、边界或激励条件？"
        "这里只描述组成部分，不需要给出具体装置或实现步骤。"
    ),
}

_FACET_HINT: dict[str, str] = {
    "learning_objective": "例如解释材料边界为什么改变场分布，或判断某种变化是否符合课程理论。",
    "research_question": "例如比较两种边界条件，并观察场线形状、幅度或空间分布的变化。",
    "hypothesis": "可以描述增大、减小、位置移动、趋于均匀或出现非单调变化，并说明原因。",
    "conceptual_structure": "可以包含场源、研究对象、边界条件、参照情形以及用于观察结果的表示方式。",
}

_FACET_ACCEPTANCE_CRITERIA: dict[str, list[str]] = {
    "learning_objective": ["说明完成实验后希望能够解释、判断或比较什么"],
    "research_question": ["说明比较或改变的条件", "说明准备观察的现象或变化"],
    "hypothesis": ["说明预期现象", "说明支持预测的物理理由"],
    "conceptual_structure": ["说明参与比较的对象、边界或激励"],
}

_MISSING_PRIORITY = (
    "research_question",
    "learning_objective",
    "hypothesis",
    "conceptual_structure",
)


def has_idea_development(session: DesignSession) -> bool:
    value = session.design_context.get("idea_development")
    return isinstance(value, dict) and isinstance(value.get("facets"), dict)


def refresh_idea_development(session: DesignSession) -> None:
    """Recalculate completeness after the canonical design state is projected."""

    development = session.design_context.get("idea_development")
    if isinstance(development, dict):
        _refresh(development)


def initialize_idea_development(
    session: DesignSession,
    outline: dict[str, Any],
    semantic_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    idea = session.design_context.get("idea", {})
    idea_text = _idea_text(idea, outline)
    course_references = KNOWLEDGE.concept_references(idea_text, limit=3)
    formula_references = KNOWLEDGE.formula_references(idea_text, limit=3)
    facets = {
        definition.facet_id: {
            "facet_id": definition.facet_id,
            "title": definition.title_zh,
            "status": MISSING,
            "evidence": "",
            "source": None,
        }
        for definition in IDEA_DEVELOPMENT_FACETS
    }
    facets["direction_outline"].update(
        {
            "status": CLEAR,
            "evidence": str(outline.get("core_phenomenon") or idea_text).strip(),
            "source": "STUDENT_AND_AGENT",
        }
    )
    if course_references:
        facets["course_mapping"].update(
            {
                "status": CLEAR,
                "evidence": "；".join(
                    str(item.get("title") or item.get("lecture_title") or "").strip()
                    for item in course_references
                    if str(item.get("title") or item.get("lecture_title") or "").strip()
                ),
                "source": "COURSE_RETRIEVAL",
            }
        )
    if formula_references:
        facets["theoretical_framework"].update(
            {
                "status": CLEAR,
                "evidence": "；".join(
                    str(item.get("name") or item.get("title") or item.get("expression") or "").strip()
                    for item in formula_references
                    if str(item.get("name") or item.get("title") or item.get("expression") or "").strip()
                ),
                "source": "COURSE_RETRIEVAL",
            }
        )
    elif course_references:
        facets["theoretical_framework"].update(
            {
                "status": CLEAR,
                "evidence": "；".join(
                    str(item.get("title") or item.get("lecture_title") or "").strip()
                    for item in course_references
                    if str(item.get("title") or item.get("lecture_title") or "").strip()
                ),
                "source": "COURSE_RETRIEVAL_QUALITATIVE",
            }
        )
    if semantic_updates is not None:
        _apply_structured_facet_updates(
            facets,
            semantic_updates.get("facet_updates"),
            idea_text,
        )
    development = {
        "status": "ACTIVE",
        "facets": facets,
        "active_facet_id": None,
        "completed_facet_ids": [],
        "missing_facet_ids": [],
        "complete": False,
        "course_references": course_references,
        "formula_references": formula_references,
        "last_clarified_facet_ids": [],
    }
    _refresh(development)
    session.design_context["idea_development"] = development
    return development


def update_idea_development(
    session: DesignSession,
    message: str,
    semantic_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    development = session.design_context.get("idea_development")
    if not isinstance(development, dict):
        raise ValueError("idea_development has not been initialized")
    facets = development.get("facets", {})
    if not isinstance(facets, dict):
        raise ValueError("idea_development facets are invalid")
    normalized = message.strip()
    clarified: list[str] = []
    if semantic_updates is not None:
        clarified.extend(
            _apply_structured_facet_updates(
                facets,
                semantic_updates.get("facet_updates"),
                normalized,
            )
        )
    else:
        # A rule-only deployment cannot judge content quality. It can still
        # attach an ordinary answer to the exact facet currently being asked,
        # without guessing its meaning from a vocabulary list.
        active_id = development.get("active_facet_id")
        if normalized and isinstance(active_id, str) and active_id in facets:
            facets[active_id].update(
                {
                    "status": CLEAR,
                    "evidence": normalized[:500],
                    "source": "CURRENT_QUESTION_FALLBACK",
                }
            )
            clarified.append(active_id)
    development["last_clarified_facet_ids"] = clarified
    _refresh(development)
    return development


def _apply_structured_facet_updates(
    facets: dict[str, dict[str, Any]],
    updates: Any,
    evidence: str,
) -> list[str]:
    clarified: list[str] = []
    if not isinstance(updates, list):
        return clarified
    for update in updates:
        if not isinstance(update, dict):
            continue
        facet_id = str(update.get("facet_id") or "")
        facet = facets.get(facet_id)
        if not isinstance(facet, dict):
            continue
        status = str(update.get("status") or "").upper()
        if status == CLEAR:
            previous_status = facet.get("status")
            previous_evidence = str(facet.get("evidence") or "").strip()
            update_value = update.get("value")
            if isinstance(update_value, list):
                update_value = "；".join(
                    str(item).strip() for item in update_value if str(item).strip()
                )
            elif update_value is not None and not isinstance(update_value, str):
                update_value = str(update_value)
            new_evidence = str(update_value or evidence).strip()[:4000]
            if (
                str(update.get("operation") or "").upper() == "MERGE"
                and str(facet.get("evidence") or "").strip()
                and new_evidence
                and new_evidence not in str(facet.get("evidence") or "")
            ):
                new_evidence = (
                    f"{previous_evidence}；补充：{new_evidence}"
                )[:4000]
            if previous_status != CLEAR or new_evidence != previous_evidence:
                clarified.append(facet_id)
            facet.update(
                {
                    "status": CLEAR,
                    "evidence": new_evidence,
                    "source": "STUDENT_SEMANTIC",
                }
            )
        elif status == MISSING:
            # MISSING is an assessment of the current student turn, not an
            # instruction to erase previously confirmed evidence. Explicit
            # withdrawal is committed through a canonical design CLEAR update
            # and projected back into the facets by the state machine.
            if facet.get("status") != CLEAR:
                facet.update({"status": MISSING, "evidence": "", "source": None})
    return clarified


def decorate_outline_output(
    output: StepOutput,
    development: dict[str, Any],
) -> StepOutput:
    status = public_idea_development_status(development)
    output.stage_payload["idea_development_status"] = status
    output.stage_payload["course_references"] = deepcopy(
        development.get("course_references", [])
    )
    output.stage_payload["lecture_formula_candidates"] = deepcopy(
        development.get("formula_references", [])
    )
    output.stage_payload["pending_action"] = _pending_action_for_status(status)
    output.assistant_message = (
        f"{output.assistant_message}\n\n"
        f"{_student_facing_next_turn(status, first_review=True)}"
    )
    output.student_task = None
    return output


def build_gap_output(
    session: DesignSession,
    acknowledged_message: str,
) -> StepOutput:
    development = session.design_context.get("idea_development", {})
    status = public_idea_development_status(development)
    clarified_titles = [
        status["facets_by_id"][facet_id]["title"]
        for facet_id in development.get("last_clarified_facet_ids", [])
        if facet_id in status["facets_by_id"]
    ]
    repeated_facet_count = _pending_facet_repeat_count(session, status)
    comparison_update = _comparison_update_summary(session, acknowledged_message)
    if clarified_titles:
        acknowledgement = _student_facing_acknowledgement(
            acknowledged_message,
            clarified_titles,
        )
        if comparison_update:
            acknowledgement += " 你提出的对照调整也已经并入当前实验想法。"
    elif comparison_update:
        acknowledgement = "你提出的对照调整已经并入当前实验想法。"
    else:
        acknowledgement = _student_facing_retry(status, acknowledged_message)
    review = ""
    if status["complete"]:
        review = (
            "\n\n这是目前整理出的实验想法：\n"
            f"{format_design_summary(session)}"
        )
    assistant_message = (
        f"{acknowledgement}{comparison_update}{review}\n\n"
        f"{_student_facing_next_turn(status, repeat_count=repeated_facet_count)}"
    )
    idea = session.design_context.get("idea", {})
    preserved_payload: dict[str, Any] = {}
    if isinstance(idea, dict):
        for key in (
            "topic_anchor",
            "current_focus",
            "core_phenomenon",
            "interest_description",
            "direction_summary",
            "selected_scene_ids",
            "selected_course_relations",
            "standard_comparisons",
            "refinement_notes",
            "combination_intent",
        ):
            if key in idea:
                preserved_payload[key] = deepcopy(idea[key])
        preserved_payload["current_idea_summary"] = str(
            idea.get("direction_summary")
            or idea.get("current_focus")
            or idea.get("main_direction")
            or ""
        ).strip()
    outline = session.design_context.get("experiment_outline_seed")
    if isinstance(outline, dict) and outline:
        preserved_payload["experiment_outline_seed"] = deepcopy(outline)
    return StepOutput(
        assistant_message=assistant_message,
        stage_payload={
            "brainstorm_activity": "IDEA_COMPLETENESS_REVIEW",
            "brainstorm_phase": "IDEA_COMPLETENESS_REVIEW",
            "input_category": "COURSE_CONTENT",
            "idea_development_status": status,
            "course_references": deepcopy(development.get("course_references", [])),
            "lecture_formula_candidates": deepcopy(
                development.get("formula_references", [])
            ),
            "alternative_ideas": [],
            "exploration_scenes": [],
            "ready_for_next_stage": bool(status["complete"]),
            "contextual_continuation": True,
            "pending_action": _pending_action_for_status(status),
            **preserved_payload,
        },
        student_task=None,
    )


def build_facet_reference_output(session: DesignSession) -> StepOutput:
    """Offer one course-grounded reference for the currently pending facet.

    This is deliberately separate from Stage 1 breadth exploration.  A request
    for help while answering an idea-completeness question must preserve the
    selected experiment and the exact missing facet instead of reopening the
    scene catalog.
    """

    development = session.design_context.get("idea_development", {})
    status = public_idea_development_status(development)
    active = str(status.get("active_facet_id") or "")
    facets = status.get("facets_by_id", {})
    research_question = str(
        facets.get("research_question", {}).get("evidence") or ""
    ).strip()
    idea = session.design_context.get("idea", {})
    outline = session.design_context.get("experiment_outline_seed", {})
    direction = ""
    if isinstance(idea, dict):
        direction = str(
            idea.get("direction_summary")
            or idea.get("current_focus")
            or idea.get("topic_anchor")
            or idea.get("original")
            or ""
        ).strip()
    if not direction and isinstance(outline, dict):
        direction = str(outline.get("core_phenomenon") or "").strip()
    course_labels = [
        str(item.get("title") or item.get("lecture_title") or "").strip()
        for item in development.get("course_references", [])
        if isinstance(item, dict)
        and str(item.get("title") or item.get("lecture_title") or "").strip()
    ]
    formula_labels = [
        str(
            item.get("name")
            or item.get("title")
            or item.get("expression")
            or ""
        ).strip()
        for item in development.get("formula_references", [])
        if isinstance(item, dict)
        and str(
            item.get("name")
            or item.get("title")
            or item.get("expression")
            or ""
        ).strip()
    ]
    theory_anchor = "、".join((course_labels + formula_labels)[:3])
    if not theory_anchor:
        theory_anchor = "前面已经匹配到的ECE329物理关系"
    subject_text = research_question or direction or "当前实验方向"

    candidates = {
        "learning_objective": (
            f"完成这个实验后，能够围绕“{subject_text}”比较不同情形，并借助"
            f"“{theory_anchor}”解释观察到的差异。"
        ),
        "research_question": (
            f"在“{direction or '当前实验方向'}”中，当一个主要比较条件改变时，"
            "准备观察的场、波或响应会怎样变化？"
        ),
        "hypothesis": (
            f"可以先沿着“{theory_anchor}”判断：这项改变是否真的改变了材料响应、"
            f"边界条件或空间对称性。套回“{subject_text}”，如果物理条件确实改变，"
            "就预测场或响应会出现怎样的可见趋势并说明原因；如果只做了不破坏对称性"
            "的操作，也可以把“结果基本不变”写成一项对照预期。"
        ),
        "conceptual_structure": (
            f"围绕“{subject_text}”，先保留场源或激励、被比较的对象或边界、一个基准情形，"
            "以及用于观察场或响应的表示方式。"
        ),
    }
    candidate = candidates.get(
        active,
        f"可以先用“{theory_anchor}”把当前实验中的比较条件和观察现象连接起来。",
    )
    titles = {
        "learning_objective": "学习目标",
        "research_question": "研究问题",
        "hypothesis": "假设与预期趋势",
        "conceptual_structure": "概念实验结构",
    }
    title = titles.get(active, "当前这一点")
    base = build_gap_output(session, "")
    base.assistant_message = (
        f"可以，我们就接着“{direction or '你刚才确定的实验方向'}”往下看，不重新换题。\n\n"
        f"课程内可以先这样思考“{title}”：{candidate}\n\n"
        "这只是一条起步思路，不是标准答案。你可以把它改写成自己的判断，"
        "也可以直接补充你不同意或想调整的地方。"
    )
    base.stage_payload.update(
        {
            "reference_only": True,
            "reference_for_facet": active,
            "alternative_ideas": [],
            "exploration_scenes": [],
            "pending_action": {
                "type": "ANSWER_IDEA_FACET",
                "subject": active,
                "proposal": {"facet_id": active, "reference": candidate},
                "candidate_answer": candidate,
                "candidate_binding_authorized": True,
                "question": f"请沿着这份参考写出你的“{title}”，或直接说明需要调整的地方。",
                "allowed_intents": [
                    "ANSWER_CURRENT_QUESTION",
                    "ACCEPT_PREVIOUS_PROPOSAL",
                    "MODIFY_PREVIOUS_PROPOSAL",
                    "REQUEST_MORE_EXAMPLES",
                    "RETURN_TO_PREVIOUS_POINT",
                    "NEW_TOPIC",
                    "UNCLEAR",
                ],
            },
        }
    )
    base.student_task = None
    return base


def public_idea_development_status(development: dict[str, Any]) -> dict[str, Any]:
    facets = development.get("facets", {})
    ordered = [
        deepcopy(facets[definition.facet_id])
        for definition in IDEA_DEVELOPMENT_FACETS
        if definition.facet_id in facets
    ]
    return {
        "mode": "DYNAMIC_COMPLETENESS",
        "facets": ordered,
        "facets_by_id": {item["facet_id"]: deepcopy(item) for item in ordered},
        "active_facet_id": development.get("active_facet_id"),
        "completed_facet_ids": list(development.get("completed_facet_ids", [])),
        "missing_facet_ids": list(development.get("missing_facet_ids", [])),
        "complete": bool(development.get("complete")),
    }


def _idea_text(idea: Any, outline: dict[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(idea, dict):
        for key in (
            "original",
            "topic_anchor",
            "current_focus",
            "core_phenomenon",
            "interest_description",
            "direction_summary",
            "main_direction",
        ):
            value = idea.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    for key in ("core_phenomenon", "course_relationships", "observation_focus"):
        value = outline.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
    return " ".join(parts)


def _refresh(development: dict[str, Any]) -> None:
    facets = development.get("facets", {})
    completed = [
        definition.facet_id
        for definition in IDEA_DEVELOPMENT_FACETS
        if facets.get(definition.facet_id, {}).get("status") == CLEAR
    ]
    missing = [
        definition.facet_id
        for definition in IDEA_DEVELOPMENT_FACETS
        if facets.get(definition.facet_id, {}).get("status") != CLEAR
    ]
    active = next((facet_id for facet_id in _MISSING_PRIORITY if facet_id in missing), None)
    if active is None and missing:
        active = missing[0]
    development["completed_facet_ids"] = completed
    development["missing_facet_ids"] = missing
    development["active_facet_id"] = active
    development["complete"] = not missing
    development["status"] = "COMPLETE" if not missing else "ACTIVE"


def _next_task(status: dict[str, Any]) -> str:
    if status.get("complete") is True:
        return (
            "这份实验想法的必要内容已经齐全。请整体检查；若准确，可确认想法完善并进入变量与条件，"
            "若仍有遗漏请直接补充。"
        )
    active = str(status.get("active_facet_id") or "")
    question = _FACET_QUESTION.get(active, "请补充当前实验想法中仍未明确的关键内容。")
    hint = _FACET_HINT.get(active, "")
    return f"{question}{hint}"


def _student_facing_next_turn(
    status: dict[str, Any],
    *,
    first_review: bool = False,
    repeat_count: int = 0,
) -> str:
    if status.get("complete") is True:
        return (
            "现在，研究对象、课程关系、学习目标和预期现象已经能互相对上了。"
            "你可以整体看一遍；觉得没问题就告诉我继续到“变量与条件”，"
            "想调整哪里也可以直接说。"
        )
    active = str(status.get("active_facet_id") or "")
    facet = status.get("facets_by_id", {}).get(active, {})
    title = str(facet.get("title") or "下一部分")
    if repeat_count > 0:
        if repeat_count > 1:
            return (
                f"“{title}”暂时卡住也没关系，我先给你一个更具体的起点。"
                f"{_focused_facet_clarification(active)}"
            )
        return (
            f"“{title}”这里不用重写前面的内容，我们换一个更直接的角度。"
            f"{_focused_facet_clarification(active)}"
        )
    if first_review:
        return (
            "这个方向已经有了可以继续发展的实验雏形。"
            f" 接下来先把“{title}”说清楚：{_next_task(status)}"
        )
    transitions = {
        "research_question": "接下来把这个想法收成一个能回答的研究问题：",
        "learning_objective": "接下来看看这个实验最终要帮助你弄懂什么：",
        "hypothesis": "接下来做一个有物理依据的预测：",
        "conceptual_structure": "接下来把实验中需要出现的对象和关系理清：",
    }
    lead = transitions.get(active, f"接下来看看“{title}”：")
    return f"{lead}{_next_task(status)}"


def _focused_facet_clarification(facet_id: str) -> str:
    prompts = {
        "learning_objective": (
            "可以从“完成后我能解释哪条物理关系”来想。你希望自己最终能解释、判断或比较什么？"
        ),
        "research_question": (
            "可以把它写成“当比较条件变化时，准备观察的现象怎样变化”。在你的想法里，"
            "这两个位置分别是什么？"
        ),
        "hypothesis": (
            "先不用追求精确数值。你预计会看到什么变化，又准备借哪条物理关系解释它？"
        ),
        "conceptual_structure": (
            "把画面想成一张简图：要完成这组比较，里面至少需要哪些对象、边界或激励？"
        ),
    }
    return prompts.get(
        facet_id,
        "可以先抓住一个最重要的物理关系，用自己的话说明它怎样连接到这个实验想法。",
    )


def _pending_action_for_status(status: dict[str, Any]) -> dict[str, Any]:
    if status.get("complete") is True:
        return {
            "type": "CONFIRM_OR_MODIFY",
            "subject": "experiment_idea_outline",
            "proposal": {"complete": True},
            "question": _next_task(status),
            "allowed_intents": [
                "ACCEPT_PREVIOUS_PROPOSAL",
                "MODIFY_PREVIOUS_PROPOSAL",
                "ADVANCE_STAGE",
                "RETURN_TO_PREVIOUS_POINT",
                "NEW_TOPIC",
                "UNCLEAR",
            ],
        }
    active = str(status.get("active_facet_id") or "")
    facet = status.get("facets_by_id", {}).get(active, {})
    return {
        "type": "ANSWER_IDEA_FACET",
        "subject": active,
        "proposal": {
            "facet_id": active,
            "title": str(facet.get("title") or "当前部分"),
            "acceptance_criteria": deepcopy(
                _FACET_ACCEPTANCE_CRITERIA.get(active, [])
            ),
        },
        "question": _next_task(status),
        "allowed_intents": [
            "ANSWER_CURRENT_QUESTION",
            "ACCEPT_PREVIOUS_PROPOSAL",
            "MODIFY_PREVIOUS_PROPOSAL",
            "ADVANCE_STAGE",
            "REQUEST_MORE_EXAMPLES",
            "RETURN_TO_PREVIOUS_POINT",
            "NEW_TOPIC",
            "UNCLEAR",
        ],
    }


def canonical_idea_pending_action(
    session: DesignSession,
) -> dict[str, Any] | None:
    """Build the only valid Stage 1 pending action from canonical facet state."""

    development = session.design_context.get("idea_development")
    if not isinstance(development, dict) or not isinstance(
        development.get("facets"), dict
    ):
        return None
    return _pending_action_for_status(public_idea_development_status(development))


def _pending_facet_repeat_count(
    session: DesignSession,
    status: dict[str, Any],
) -> int:
    dialogue = session.model_context.get("dialogue_state", {})
    pending = dialogue.get("pending_action", {}) if isinstance(dialogue, dict) else {}
    active = str(status.get("active_facet_id") or "")
    if (
        not isinstance(pending, dict)
        or pending.get("type") != "ANSWER_IDEA_FACET"
        or pending.get("subject") != active
    ):
        return 0
    try:
        return max(1, int(pending.get("repeat_count", 1)))
    except (TypeError, ValueError):
        return 1


def _student_facing_retry(status: dict[str, Any], message: str) -> str:
    active = str(status.get("active_facet_id") or "")
    feedback = {
        "learning_objective": (
            "我理解了你补充的现象，但这里还需要更明确地说出你完成实验后能够解释、"
            "判断或比较什么。"
        ),
        "research_question": (
            "我保留了你刚才的补充，但研究问题还需要同时出现要比较的条件和准备观察的变化。"
        ),
        "hypothesis": (
            "你已经描述了可能看到的现象；要把它变成实验预期，还需要说明这种变化背后的物理理由。"
        ),
        "conceptual_structure": (
            "我理解了你的补充，但还需要说明这个想法中有哪些对象、边界或激励共同构成比较。"
        ),
    }
    return feedback.get(
        active,
        "我保留了你刚才的补充，但还需要把它与当前实验想法的物理关系说得更具体。",
    )


def _student_facing_acknowledgement(
    _message: str,
    clarified_titles: list[str],
) -> str:
    title_text = "、".join(f"“{title}”" for title in clarified_titles)
    if clarified_titles == ["学习目标"]:
        return "这个学习目标很清楚：接下来的设计要能帮助你真正解释这条物理关系。"
    if clarified_titles == ["研究问题"]:
        return "这个研究问题已经很具体，比较条件和准备观察的现象都对上了。"
    if clarified_titles == ["假设与预期趋势"]:
        return "你的预测既说清了可能的现象，也给出了判断依据，我们可以继续往下看。"
    if clarified_titles == ["概念实验结构"]:
        return "主要对象和它们之间的关系已经清楚了，这套结构可以继续沿用。"
    return f"这部分回答已经把{title_text}说明得更具体。"


def _comparison_update_summary(session: DesignSession, message: str) -> str:
    comparisons = baseline_comparisons_snapshot(session)
    resolved = session.turn_context.get("resolved_intent", {})
    semantic_source = (
        str(resolved.get("source") or "").startswith("SEMANTIC")
        or resolved.get("source") == "CONFIRMED_PENDING_MODIFICATION"
    ) if isinstance(resolved, dict) else False
    semantic_updates = resolved.get("semantic_updates", {}) \
        if isinstance(resolved, dict) else {}
    comparison_updates = semantic_updates.get("applied_comparison_updates", []) \
        if isinstance(semantic_updates, dict) else []
    if not semantic_source or not comparison_updates:
        return ""
    by_id = {
        str(comparison.get("comparison_id") or ""): comparison
        for comparison in comparisons
        if isinstance(comparison, dict)
    }
    summaries: list[str] = []
    for update in comparison_updates:
        if not isinstance(update, dict):
            continue
        action = str(update.get("action") or "").upper()
        if action == "CREATE":
            cases = [
                str(item).strip()
                for item in (
                    update.get("cases", [])
                    if isinstance(update.get("cases"), list)
                    else []
                )
                if str(item).strip()
            ]
            if cases:
                summaries.append(f"新增基础比较：{'、'.join(cases)}")
            continue
        comparison = by_id.get(str(update.get("comparison_id") or ""))
        if not isinstance(comparison, dict):
            continue
        status = str(comparison.get("adoption_status") or "PENDING")
        cases = [str(item) for item in comparison.get("cases", []) if str(item).strip()]
        if status == "REJECTED":
            summaries.append("这组基础对照已移除")
        elif status == "MODIFIED":
            summaries.append(f"基础对照已按你的要求调整为：{'、'.join(cases)}")
        elif status == "ACCEPTED":
            summaries.append(f"基础对照已确认包含：{'、'.join(cases)}")
    return f"\n{'；'.join(summaries)}。" if summaries else ""
