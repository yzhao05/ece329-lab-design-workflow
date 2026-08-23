from __future__ import annotations

from copy import deepcopy
from typing import Any

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

_MISSING_PRIORITY = (
    "research_question",
    "learning_objective",
    "hypothesis",
    "conceptual_structure",
)


def has_idea_development(session: DesignSession) -> bool:
    value = session.design_context.get("idea_development")
    return isinstance(value, dict) and isinstance(value.get("facets"), dict)


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
            if facet.get("status") != CLEAR:
                clarified.append(facet_id)
            facet.update(
                {
                    "status": CLEAR,
                    "evidence": evidence.strip()[:500],
                    "source": "STUDENT_SEMANTIC",
                }
            )
        elif status == MISSING:
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
    acknowledgement = (
        _student_facing_acknowledgement(acknowledged_message, clarified_titles)
        if clarified_titles
        else _student_facing_retry(status, acknowledged_message)
    )
    comparison_update = _comparison_update_summary(session, acknowledged_message)
    assistant_message = (
        f"{acknowledgement}{comparison_update}\n\n"
        f"{_student_facing_next_turn(status)}"
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
            **preserved_payload,
        },
        student_task=None,
    )


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
) -> str:
    if status.get("complete") is True:
        return (
            "现在，这个实验想法中的研究对象、课程依据、学习目标和预期现象已经能够相互对应。"
            "请整体看一遍；如果与自己的想法一致，直接告诉我进入“变量与条件”。"
            "如果还有想调整的地方，也可以直接说明。"
        )
    active = str(status.get("active_facet_id") or "")
    facet = status.get("facets_by_id", {}).get(active, {})
    title = str(facet.get("title") or "下一部分")
    prefix = (
        "这个方向已经形成了可以继续发展的实验雏形。"
        if first_review
        else "我们继续沿着同一个实验方向往下完善。"
    )
    return f"{prefix} 接下来先把“{title}”说清楚：{_next_task(status)}"


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
    message: str,
    clarified_titles: list[str],
) -> str:
    excerpt = " ".join(message.split())
    if len(excerpt) > 150:
        excerpt = f"{excerpt[:147]}……"
    title_text = "、".join(f"“{title}”" for title in clarified_titles)
    if clarified_titles == ["学习目标"]:
        return f"这个学习目标表达得很清楚：“{excerpt}”。"
    if clarified_titles == ["研究问题"]:
        return f"这个研究问题已经很具体：“{excerpt}”。"
    if clarified_titles == ["假设与预期趋势"]:
        return f"你的预测已经同时给出了现象和判断：“{excerpt}”。"
    if clarified_titles == ["概念实验结构"]:
        return f"你已经把实验中的主要组成说得很具体：“{excerpt}”。"
    return f"你的回答很清楚：“{excerpt}”。这已经把{title_text}说明得更具体。"


def _comparison_update_summary(session: DesignSession, message: str) -> str:
    idea = session.design_context.get("idea", {})
    comparisons = idea.get("standard_comparisons", []) if isinstance(idea, dict) else []
    if not isinstance(comparisons, list):
        return ""
    resolved = session.turn_context.get("resolved_intent", {})
    semantic_source = str(resolved.get("source") or "").startswith("SEMANTIC") \
        if isinstance(resolved, dict) else False
    semantic_updates = resolved.get("semantic_updates", {}) \
        if isinstance(resolved, dict) else {}
    comparison_updates = semantic_updates.get("comparison_updates", []) \
        if isinstance(semantic_updates, dict) else []
    if not semantic_source or not comparison_updates:
        return ""
    summaries: list[str] = []
    for comparison in comparisons:
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
