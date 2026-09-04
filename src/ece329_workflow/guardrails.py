from __future__ import annotations

import re
from typing import Any, Sequence

from .knowledge_base import KNOWLEDGE


COURSE_CONTENT = "COURSE_CONTENT"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
UNREASONABLE_REQUEST = "UNREASONABLE_REQUEST"
AMBIGUOUS = "AMBIGUOUS"
BREADTH_EXPLORATION = "BREADTH_EXPLORATION"
INTEREST_DESCRIPTION = "INTEREST_DESCRIPTION"
DEPTH_EXPANSION = "DEPTH_EXPANSION"


_UNREASONABLE_REQUEST_PATTERNS = (
    # Attempts to inspect or alter the assistant rather than design an ECE329 lab.
    r"(工作流|workflow|\bagent\b|智能体).{0,12}(提示|内部|规则|原理|关闭|修改|绕过|任意输出)",
    r"(提示|内部|规则|原理|关闭|修改|绕过).{0,12}(工作流|workflow|\bagent\b|智能体)",
    r"system\s*prompt|系统提示|提示词|内部指令|隐藏指令",
    r"\bapi\b|后端|前端|服务器|源代码|github|render|部署",
    r"密钥|access[ _-]*token|令牌",
    r"角色扮演|role\s*play|扮演.{0,12}(角色|老师|学生|专家|人物)",
    r"忽略.{0,12}(之前|以上|系统|规则|指令)",
    r"越狱|jailbreak|捣乱|输出.{0,8}(无关|随机|违规)内容",
    r"你的.{0,8}(工作原理|内部机制|规则|提示|身份|能力)",
    r"(关闭|关掉|停止|终止|禁用|卸载|删除|重启|重置).{0,12}(你|助手|agent|智能体|网页|网站|系统|服务|工作流)",
    r"(shut\s*down|turn\s*off|disable|kill|stop|restart|reset).{0,20}(agent|assistant|website|system|service|workflow)",
    # Code, command, or script execution is outside this design-only conversation.
    r"(写|生成|执行|运行|注入|提交).{0,8}(代码|脚本|程序|命令|指令)",
    r"(代码|脚本|程序|命令).{0,8}(执行|运行|控制|修改|输出|关闭)",
    r"```|<\s*script\b|javascript\s*:|\beval\s*\(|\bexec\s*\(|\bfetch\s*\(",
    r"\b(subprocess|os\.system|document\.|window\.|localstorage|process\.env)\b",
    r"\b(import|def|class|function)\b.{0,24}\b(code|script|execute|run|agent|assistant)\b",
    r"\b(code|script|execute|run)\b.{0,24}\b(import|def|class|function)\b",
    r"\b(python|javascript|typescript|powershell|bash|cmd|sql|html|css)\b",
    # Platform-independent attempts to repurpose the course assistant or force output.
    r"(接入|调用|连接|控制|转发|发布|上传).{0,40}(\bagent\b|智能体|助手|机器人|\bbot\b)",
    r"(\bagent\b|智能体|助手|机器人|\bbot\b).{0,40}(接入|调用|连接|控制|转发|发布|上传).{0,40}(网站|平台|应用|服务|插件|频道|论坛|直播)",
    r"(网站|平台|应用|服务|插件|频道|论坛|直播).{0,32}(强制|控制|改写|指定).{0,24}(输出|回答|翻译|内容)",
)


def preclassify_stage_one_input(text: str) -> str:
    """Return hard-safety or retrieval evidence, not conversational intent."""

    normalized = text.strip()
    if any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _UNREASONABLE_REQUEST_PATTERNS
    ):
        return UNREASONABLE_REQUEST
    if (
        KNOWLEDGE.match_concepts(normalized, limit=1)
        or KNOWLEDGE.match_supplemental_concepts(normalized, limit=1)
    ):
        return COURSE_CONTENT
    return AMBIGUOUS


def classify_stage_one_input(text: str) -> str:
    """Conservative rule-only fallback used when no semantic model is present."""

    preclassification = preclassify_stage_one_input(text)
    return OUT_OF_SCOPE if preclassification == AMBIGUOUS else preclassification


def resolve_option_id(
    option_id: str | None,
    options: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if not option_id:
        return None
    return next(
        (
            dict(option)
            for option in options
            if option.get("option_id") == option_id
        ),
        None,
    )


def latest_stage_one_options(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    for history_item in reversed(history):
        output = history_item.get("output")
        if not isinstance(output, dict):
            continue
        payload = output.get("stage_payload")
        if not isinstance(payload, dict):
            continue
        options = payload.get("alternative_ideas")
        if (
            isinstance(options, list)
            and options
            and all(isinstance(item, dict) for item in options)
        ):
            return [dict(item) for item in options]
    return []


def latest_stage_one_scenes(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    for history_item in reversed(history):
        output = history_item.get("output")
        if not isinstance(output, dict):
            continue
        payload = output.get("stage_payload")
        if not isinstance(payload, dict):
            continue
        scenes = payload.get("exploration_scenes")
        if (
            isinstance(scenes, list)
            and scenes
            and all(isinstance(item, dict) for item in scenes)
        ):
            return [dict(item) for item in scenes]
    return []


def shown_exploration_option_ids(history: Sequence[dict[str, Any]]) -> set[str]:
    """Collect every internally numbered exploration point already shown."""

    shown: set[str] = set()
    for history_item in history:
        output = history_item.get("output")
        if not isinstance(output, dict):
            continue
        payload = output.get("stage_payload")
        if not isinstance(payload, dict):
            continue
        options = payload.get("alternative_ideas")
        if not isinstance(options, list):
            continue
        shown.update(
            str(option.get("option_id"))
            for option in options
            if isinstance(option, dict) and str(option.get("option_id") or "").strip()
        )
    return shown


def infer_standard_comparisons(text: str) -> list[dict[str, Any]]:
    """Propose course-cataloged baseline bundles without topic-specific code."""

    return KNOWLEDGE.standard_comparison_suggestions(text, limit=1)


def build_stage_one_turn_context(
    text: str,
    *,
    options: Sequence[dict[str, Any]],
    scenes: Sequence[dict[str, Any]] = (),
    idea_context: dict[str, Any] | None,
    selected_option_id: str | None = None,
    semantic_updates: dict[str, Any] | None = None,
    resolved_intent_name: str | None = None,
    resolved_intent_target: str | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a Stage 1 turn against the active idea thread.

    Ambiguous fragments are treated as refinements only after the student has
    established an ECE329 topic. An explicit topic switch starts a fresh scope
    decision instead of inheriting the old topic.
    """

    idea = idea_context if isinstance(idea_context, dict) else {}
    no_direction = bool(
        isinstance(semantic_updates, dict)
        and semantic_updates.get("no_direction") is True
    )
    semantic_scope = (
        str(semantic_updates.get("course_scope_status") or "UNCERTAIN")
        if isinstance(semantic_updates, dict)
        else "UNCERTAIN"
    )
    semantic_course_domain = (
        str(semantic_updates.get("course_domain") or "").strip().casefold()
        if isinstance(semantic_updates, dict)
        else ""
    )
    if semantic_course_domain not in {
        "electrostatics",
        "magnetism",
        "electromagnetics",
    }:
        semantic_course_domain = ""
    semantic_option_ids = (
        semantic_updates.get("selected_option_ids", [])
        if isinstance(semantic_updates, dict)
        else []
    )
    semantic_options = [
        option
        for option_id in semantic_option_ids
        if isinstance(option_id, str)
        for option in [resolve_option_id(option_id, options)]
        if option is not None
    ]
    resolved = resolve_option_id(selected_option_id, options)
    if resolved is None and len(semantic_options) == 1:
        resolved = semantic_options[0]
    resolved_scene_relations = (
        [dict(item) for item in semantic_options]
        if len(semantic_options) > 1
        else []
    )
    lexical_preclassification = preclassify_stage_one_input(text)
    if lexical_preclassification == UNREASONABLE_REQUEST:
        preclassification = UNREASONABLE_REQUEST
    elif no_direction or semantic_scope == COURSE_CONTENT:
        preclassification = COURSE_CONTENT
    else:
        preclassification = lexical_preclassification
    previous_focus = str(
        idea.get("current_focus")
        or idea.get("main_direction")
        or idea.get("current_summary")
        or ""
    ).strip()
    topic_anchor = str(idea.get("topic_anchor", "")).strip()
    scope_confirmed = idea.get("course_scope_confirmed") is True
    explicit_switch = resolved_intent_name == "NEW_TOPIC"
    contextual_continuation = bool(
        resolved is not None
        or resolved_scene_relations
        or (
            scope_confirmed
            and previous_focus
            and not explicit_switch
            and preclassification != UNREASONABLE_REQUEST
        )
    )
    if preclassification == UNREASONABLE_REQUEST:
        effective_classification = UNREASONABLE_REQUEST
    elif semantic_scope == OUT_OF_SCOPE and not contextual_continuation:
        effective_classification = OUT_OF_SCOPE
    elif resolved is not None or resolved_scene_relations or (
        preclassification == AMBIGUOUS and contextual_continuation
    ):
        effective_classification = COURSE_CONTENT
    elif preclassification == AMBIGUOUS:
        effective_classification = OUT_OF_SCOPE
    else:
        effective_classification = preclassification
    if semantic_scope == OUT_OF_SCOPE:
        prompt_preclassification = AMBIGUOUS
    elif (
        effective_classification == COURSE_CONTENT
        and (
            semantic_scope == COURSE_CONTENT
            or resolved is not None
            or resolved_scene_relations
            or contextual_continuation
        )
    ):
        prompt_preclassification = COURSE_CONTENT
    else:
        prompt_preclassification = preclassification

    selected_text = "——".join(
        str(resolved.get(key, "")).strip()
        for key in ("direction", "focus")
        if resolved is not None and str(resolved.get(key, "")).strip()
    )
    previous_relations = idea.get("selected_course_relations", [])
    selected_course_relations = (
        [dict(item) for item in previous_relations if isinstance(item, dict)]
        if isinstance(previous_relations, list)
        else []
    )
    previous_scene_ids = idea.get("selected_scene_ids", [])
    selected_scene_ids = (
        [str(item) for item in previous_scene_ids if str(item).strip()]
        if isinstance(previous_scene_ids, list)
        else []
    )
    if resolved_scene_relations:
        selected_course_relations = resolved_scene_relations
        selected_scene_ids = [
            str(scene.get("catalog_scene_id") or scene.get("scene_id"))
            for scene in scenes
            if isinstance(scene, dict)
            and isinstance(scene.get("course_anchor"), dict)
            and scene.get("course_anchor") in resolved_scene_relations
            and str(scene.get("catalog_scene_id") or scene.get("scene_id") or "").strip()
        ]
    elif resolved is not None:
        selected_course_relations = [dict(resolved)]
        selected_scene_ids = []
    selected_domains = {
        str(item.get("course_block") or "").strip().casefold()
        for item in selected_course_relations
        if str(item.get("course_block") or "").strip().casefold()
        in {"electrostatics", "magnetism", "electromagnetics"}
    }
    course_domain = (
        next(iter(selected_domains))
        if len(selected_domains) == 1
        else semantic_course_domain
        or str(idea.get("course_domain") or "").strip().casefold()
    )
    if course_domain not in {
        "electrostatics",
        "magnetism",
        "electromagnetics",
    }:
        course_domain = ""
    relation_selection_text = " + ".join(
        str(item.get("direction") or item.get("focus") or "").strip()
        for item in selected_course_relations
        if str(item.get("direction") or item.get("focus") or "").strip()
    )
    normalized = text.strip()
    semantic_direction_detail = str(
        semantic_updates.get("stage_one_direction_detail") or ""
        if isinstance(semantic_updates, dict)
        else ""
    ).strip()
    direction_detail = (
        semantic_direction_detail
        if (
            scope_confirmed
            or semantic_scope == COURSE_CONTENT
            or resolved is not None
            or resolved_scene_relations
        )
        else ""
    )
    direction_locked_before_turn = bool(
        idea.get("direction_locked") is True
        or selected_course_relations
        or str(idea.get("core_phenomenon") or "").strip()
        or str(idea.get("interest_description") or "").strip()
    )
    direction_locked = bool(
        direction_locked_before_turn
        or resolved is not None
        or resolved_scene_relations
        or direction_detail
    )
    pending_reference_requested = bool(
        resolved_intent_name == "REQUEST_MORE_EXAMPLES"
        and (
            direction_locked
            or (
                isinstance(pending_action, dict)
                and pending_action.get("type")
                in {"ANSWER_IDEA_FACET", "ANSWER_STAGE_QUESTION"}
                and resolved_intent_target
                not in {"exploration_scenes", "BREADTH_EXPLORATION"}
            )
        )
    )
    more_brainstorm_requested = bool(
        resolved_intent_name == "REQUEST_MORE_EXAMPLES"
        and not pending_reference_requested
    )
    history = idea.get("focus_history", [])
    focus_history = [str(item).strip() for item in history if str(item).strip()] \
        if isinstance(history, list) else []
    control_turn = bool(
        resolved_intent_name
        in {
            "ACCEPT_PREVIOUS_PROPOSAL",
            "REJECT_PREVIOUS_PROPOSAL",
            "ADVANCE_STAGE",
            "RETURN_TO_PREVIOUS_POINT",
        }
        or pending_reference_requested
    )
    if no_direction or control_turn or more_brainstorm_requested:
        focus_component = ""
    elif resolved_scene_relations and relation_selection_text:
        focus_component = relation_selection_text
    elif selected_text:
        focus_component = selected_text
    elif effective_classification == COURSE_CONTENT:
        focus_component = normalized
    else:
        focus_component = ""
    if focus_component and (not focus_history or focus_history[-1] != focus_component):
        focus_history.append(focus_component)
    focus_history = focus_history[-8:]
    if not topic_anchor and effective_classification == COURSE_CONTENT and not no_direction:
        topic_anchor = normalized
    elif explicit_switch and effective_classification == COURSE_CONTENT:
        topic_anchor = normalized
        focus_history = [normalized]
        selected_course_relations = []
        selected_scene_ids = []
        relation_selection_text = ""

    previous_phase = str(
        idea.get("brainstorm_phase") or BREADTH_EXPLORATION
    ).strip()
    previous_selected_focus = str(idea.get("selected_focus") or "").strip()
    if effective_classification != COURSE_CONTENT:
        brainstorm_phase = BREADTH_EXPLORATION
    elif more_brainstorm_requested:
        brainstorm_phase = BREADTH_EXPLORATION
    elif control_turn:
        brainstorm_phase = previous_phase
    elif direction_detail:
        brainstorm_phase = DEPTH_EXPANSION
    elif resolved is not None or resolved_scene_relations:
        brainstorm_phase = INTEREST_DESCRIPTION
    elif previous_phase == INTEREST_DESCRIPTION:
        brainstorm_phase = DEPTH_EXPANSION
    elif previous_phase == DEPTH_EXPANSION:
        brainstorm_phase = DEPTH_EXPANSION
    elif contextual_continuation and scope_confirmed:
        brainstorm_phase = INTEREST_DESCRIPTION
    else:
        brainstorm_phase = BREADTH_EXPLORATION
    resolved_focus = relation_selection_text
    if not resolved_focus and resolved is not None:
        resolved_focus = str(
            resolved.get("direction") or resolved.get("focus") or ""
        ).strip()
    selected_focus = resolved_focus or previous_selected_focus or direction_detail
    if brainstorm_phase == INTEREST_DESCRIPTION and not selected_focus:
        selected_focus = normalized or previous_focus or topic_anchor
    interest_description = (
        direction_detail or normalized
        if brainstorm_phase == DEPTH_EXPANSION and not control_turn
        else str(idea.get("interest_description") or "").strip()
    )
    previous_core_phenomenon = str(idea.get("core_phenomenon") or "").strip()
    core_phenomenon = previous_core_phenomenon
    if (
        brainstorm_phase == DEPTH_EXPANSION
        and not control_turn
        and (
            previous_phase in {BREADTH_EXPLORATION, INTEREST_DESCRIPTION}
            or not previous_core_phenomenon
        )
    ):
        core_phenomenon = direction_detail or normalized
    refinement_history = idea.get("refinement_notes", [])
    refinement_notes = (
        [str(item).strip() for item in refinement_history if str(item).strip()]
        if isinstance(refinement_history, list)
        else []
    )
    if (
        brainstorm_phase == DEPTH_EXPANSION
        and previous_phase == DEPTH_EXPANSION
        and not control_turn
        and (direction_detail or normalized)
        and (
            not refinement_notes
            or refinement_notes[-1] != (direction_detail or normalized)
        )
    ):
        refinement_notes.append(direction_detail or normalized)
    refinement_notes = refinement_notes[-6:]
    focus_parts = [topic_anchor]
    if relation_selection_text and relation_selection_text != topic_anchor:
        focus_parts.append(relation_selection_text)
    if core_phenomenon and core_phenomenon not in focus_parts:
        focus_parts.append(core_phenomenon)
    for note in refinement_notes[-2:]:
        if note not in focus_parts:
            focus_parts.append(note)
    proposed_focus = (
        previous_focus
        if control_turn and previous_focus
        else " → ".join(item for item in focus_parts if item) or previous_focus
    )
    comparison_text = " ".join(
        [topic_anchor, relation_selection_text, core_phenomenon, *refinement_notes, normalized]
    )
    previous_comparisons = idea.get("standard_comparisons", [])
    if explicit_switch:
        previous_comparisons = []
    preserved_comparisons = (
        [dict(item) for item in previous_comparisons if isinstance(item, dict)]
        if isinstance(previous_comparisons, list)
        else []
    )
    inferred_comparisons = infer_standard_comparisons(comparison_text)
    previous_ids = {
        str(item.get("comparison_id") or "").strip()
        for item in preserved_comparisons
    }
    standard_comparisons = preserved_comparisons + [
        item
        for item in inferred_comparisons
        if str(item.get("comparison_id") or "").strip() not in previous_ids
    ]
    previous_direction_summary = str(idea.get("direction_summary") or "").strip()
    direction_summary = (
        previous_direction_summary
        if control_turn and previous_direction_summary
        else core_phenomenon or selected_focus or topic_anchor or proposed_focus
    )
    if refinement_notes and not control_turn:
        direction_summary = f"{direction_summary}；观察重点：{'；'.join(refinement_notes[-2:])}"

    return {
        "stage_one_preclassification": prompt_preclassification,
        "semantic_course_scope": semantic_scope,
        "course_domain": course_domain or None,
        "effective_input_category": effective_classification,
        "raw_stage_one_preclassification": preclassification,
        "resolved_stage_one_reference": resolved,
        "resolved_scene_relations": resolved_scene_relations,
        "contextual_continuation": contextual_continuation,
        "explicit_topic_switch": explicit_switch,
        "previous_focus": previous_focus,
        "topic_anchor": topic_anchor,
        "current_focus": proposed_focus,
        "focus_history": focus_history,
        "course_scope_confirmed": scope_confirmed
        or effective_classification == COURSE_CONTENT,
        "previous_brainstorm_phase": previous_phase,
        "brainstorm_phase": brainstorm_phase,
        "selected_focus": selected_focus,
        "selected_scene_ids": selected_scene_ids,
        "selected_course_relations": selected_course_relations,
        "combination_intent": len(selected_course_relations) > 1,
        "core_phenomenon": core_phenomenon,
        "refinement_notes": refinement_notes,
        "standard_comparisons": standard_comparisons,
        "direction_summary": direction_summary,
        "interest_description": interest_description,
        "ready_for_next_stage": (
            brainstorm_phase == DEPTH_EXPANSION and bool(interest_description)
        ),
        "control_turn": control_turn,
        "more_brainstorm_requested": more_brainstorm_requested,
        "pending_reference_requested": pending_reference_requested,
        "direction_locked": direction_locked,
        "stage_one_direction_detail": direction_detail,
        "stage_one_no_direction": no_direction,
    }


def course_example_options(
    *,
    exclude_option_ids: set[str] | None = None,
    seed_key: str = "",
) -> list[dict[str, object]]:
    return KNOWLEDGE.brainstorm_options(
        "",
        limit=3,
        exclude_option_ids=exclude_option_ids,
        seed_key=seed_key,
    )
