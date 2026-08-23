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


_NO_DIRECTION_PATTERNS = (
    r"还没有.{0,6}(方向|想法)",
    r"没有.{0,6}(具体|明确).{0,6}(方向|想法)",
    r"不知道.{0,10}(研究|选|做什么)",
    r"帮我.{0,4}(想|brainstorm)",
    r"随便.{0,6}(推荐|举例|给.*方向)",
)

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_ORDINAL_TOKEN = r"(\d+|[一二三四五六七八九十]{1,3})"
_OPTION_REFERENCE_PATTERNS = (
    rf"第\s*{_ORDINAL_TOKEN}\s*(?:个|项|类|条|种|方向|例子)",
    rf"第\s*{_ORDINAL_TOKEN}\s*$",
    rf"(?:选|选择|研究|想要|考虑)\s*(?:第\s*)?{_ORDINAL_TOKEN}\s*(?:个|项|类|条|种|方向|例子)",
    rf"(?:选|选择)\s*(?:第\s*)?{_ORDINAL_TOKEN}\s*$",
    rf"(?:上面|刚才|之前).{{0,6}}{_ORDINAL_TOKEN}\s*(?:个|项|类|条|种|方向|例子)",
)

_EXPLICIT_TOPIC_SWITCH_PATTERNS = (
    r"(?:改成|换成|改为|换为|转向|另选|重新选|不研究这个).{0,24}",
    r"(?:另一个|新的).{0,8}(?:主题|方向|实验|想法)",
    r"(?:change|switch|move)\s+(?:the\s+)?(?:topic|direction)",
)

_COMPLETE_TOPIC_INTRODUCTION_PATTERN = (
    r"^(?:我)?(?:现在)?(?:想|要|准备|打算)?(?:研究|探索|设计|讨论|改做)"
)

_OPTION_TEXT_PREFIXES = (
    "我想研究",
    "我想探索",
    "我选择",
    "我选",
    "选择",
    "研究",
    "探索",
    "先看",
    "就看",
    "例如",
)

_STAGE_ONE_CONTROL_MESSAGES = {
    "继续",
    "继续完善下一阶段",
    "确认本阶段并进入下一阶段",
    "确认当前方向并进入下一阶段",
    "进入下一阶段",
    "完成本阶段",
}

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
    normalized = text.strip()
    if any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _UNREASONABLE_REQUEST_PATTERNS
    ):
        return UNREASONABLE_REQUEST
    if is_no_direction_request(normalized):
        return COURSE_CONTENT
    if (
        KNOWLEDGE.match_concepts(normalized, limit=1)
        or KNOWLEDGE.match_supplemental_concepts(normalized, limit=1)
    ):
        return COURSE_CONTENT
    return AMBIGUOUS


def classify_stage_one_input(text: str) -> str:
    preclassification = preclassify_stage_one_input(text)
    return OUT_OF_SCOPE if preclassification == AMBIGUOUS else preclassification


def is_explicit_topic_switch(text: str) -> bool:
    normalized = text.strip()
    return any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _EXPLICIT_TOPIC_SWITCH_PATTERNS
    )


def is_no_direction_request(text: str) -> bool:
    normalized = text.strip()
    return not normalized or any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _NO_DIRECTION_PATTERNS
    )


def is_stage_one_control_message(text: str) -> bool:
    return text.strip() in _STAGE_ONE_CONTROL_MESSAGES


def _parse_positive_ordinal(raw: str) -> int | None:
    if raw.isdigit():
        value = int(raw)
    elif raw == "十":
        value = 10
    elif "十" in raw:
        left, right = raw.split("十", maxsplit=1)
        if left and left not in _CHINESE_DIGITS:
            return None
        if right and right not in _CHINESE_DIGITS:
            return None
        value = _CHINESE_DIGITS.get(left, 1) * 10 + _CHINESE_DIGITS.get(right, 0)
    else:
        value = _CHINESE_DIGITS.get(raw, 0)
    return value if value > 0 else None


def referenced_option_index(text: str) -> int | None:
    normalized = text.strip()
    for pattern in _OPTION_REFERENCE_PATTERNS:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if not match:
            continue
        raw_index = match.group(1)
        ordinal = _parse_positive_ordinal(raw_index)
        return ordinal - 1 if ordinal is not None else None
    return None


def resolve_option_reference(
    text: str,
    options: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    index = referenced_option_index(text)
    if index is not None and index < len(options):
        return dict(options[index])
    return resolve_option_text(text, options)


def _compact_text(text: str) -> str:
    return re.sub(
        r"[\s，。！？、；：,.!?;:\-—_（）()\[\]{}\"'“”‘’]+",
        "",
        text.casefold(),
    )


def _normalize_option_text(text: str) -> str:
    normalized = _compact_text(text)
    changed = True
    while changed:
        changed = False
        for prefix in _OPTION_TEXT_PREFIXES:
            compact_prefix = _compact_text(prefix)
            if normalized.startswith(compact_prefix) and len(normalized) > len(compact_prefix):
                normalized = normalized[len(compact_prefix):]
                changed = True
    return normalized


def resolve_option_text(
    text: str,
    options: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a typed option label without requiring an ordinal or UI option id."""

    normalized = _normalize_option_text(text)
    if len(normalized) < 4:
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for option in options:
        labels = [
            _normalize_option_text(str(option.get(key, "")))
            for key in ("focus", "direction")
        ]
        labels = [label for label in labels if label]
        score = max(
            (
                min(len(normalized), len(label))
                for label in labels
                if normalized in label or label in normalized
            ),
            default=0,
        )
        if score:
            candidates.append((score, dict(option)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1]


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


def resolve_scene_references(
    text: str,
    scenes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve explicit scene labels such as ``图景A和图景B``."""

    labels = [
        label.upper()
        for label in re.findall(r"图景\s*([A-Z])", text, re.IGNORECASE)
    ]
    if not labels:
        return []
    resolved: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for label in labels:
        for scene in scenes:
            scene_label = str(scene.get("label") or "").upper().replace(" ", "")
            scene_id = str(scene.get("scene_id") or "").strip()
            if scene_label not in {f"图景{label}", label} and scene_id.casefold() != f"scene_{label.lower()}":
                continue
            anchor = scene.get("course_anchor")
            if not isinstance(anchor, dict):
                break
            anchor_id = str(anchor.get("option_id") or repr(anchor))
            if anchor_id not in seen_ids:
                resolved.append(dict(anchor))
                seen_ids.add(anchor_id)
            break
    return resolved


def infer_standard_comparisons(text: str) -> list[dict[str, Any]]:
    """Propose course-cataloged baseline bundles without topic-specific code."""

    return KNOWLEDGE.standard_comparison_suggestions(text, limit=1)


def update_standard_comparison_decisions(
    text: str,
    comparisons: Sequence[dict[str, Any]],
    *,
    control_turn: bool = False,
) -> list[dict[str, Any]]:
    """Apply an explicit accept, modification, or rejection to proposed baselines.

    The assistant may bundle scientifically routine cases as a recommendation,
    but the student's decision remains authoritative and persists on later turns.
    """

    normalized = text.strip()
    rejects_bundle = re.search(
        r"(?:不采用|不保留|不考虑|不需要|无需|不用|不要|取消)"
        r"(?:这组|这个|整组|全部)?(?:基本|标准)?(?:case|情况|情形|对照|比较)|"
        r"不需要.{0,6}(?:分类|分情况)讨论|(?:这些|上述|所有|全部).{0,4}都不要",
        normalized,
        re.IGNORECASE,
    )
    accepts_bundle = re.search(
        r"(?:接受|采纳|同意|保留|采用|恢复).{0,8}"
        r"(?:这组|这个|整组|全部)?(?:基本|标准)?(?:case|情况|情形|对照|比较)|"
        r"(?:全部|所有|这些|上述).{0,5}(?:都要|都考虑|都保留|一起做|同时做)",
        normalized,
        re.IGNORECASE,
    )
    updated: list[dict[str, Any]] = []
    for comparison in comparisons:
        item = dict(comparison)
        recommended_cases = item.get("recommended_cases", item.get("cases", []))
        recommended_cases = [
            str(case).strip()
            for case in recommended_cases
            if str(case).strip()
        ] if isinstance(recommended_cases, list) else []
        cases = item.get("cases", recommended_cases)
        cases = [
            str(case).strip() for case in cases if str(case).strip()
        ] if isinstance(cases, list) else list(recommended_cases)
        status = str(item.get("adoption_status") or "PENDING").upper()
        if status not in {"PENDING", "ACCEPTED", "MODIFIED", "REJECTED"}:
            status = "PENDING"

        aliases = item.get("case_aliases", {})
        aliases = aliases if isinstance(aliases, dict) else {}

        def labels_for(case: str) -> list[str]:
            raw_aliases = aliases.get(case, [])
            return [case, *(
                [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
                if isinstance(raw_aliases, list)
                else []
            )]

        mentioned_cases = [
            case
            for case in recommended_cases
            if any(label and label.casefold() in normalized.casefold() for label in labels_for(case))
        ]
        only_instruction = re.search(
            r"(?:只|仅).{0,8}(?:保留|采用|考虑|研究|观察|比较|看|做)",
            normalized,
        )
        removed_cases: list[str] = []
        restored_cases: list[str] = []
        replacement_pairs: list[tuple[str, str]] = []
        for case in recommended_cases:
            for label in labels_for(case):
                escaped = re.escape(label)
                if re.search(
                    rf"(?:不采用|不保留|不考虑|不要|不用|排除|去掉|删除|移除)"
                    rf"[^，,。；;！？?]{{0,3}}{escaped}|{escaped}[^，,。；;！？?]{{0,3}}"
                    rf"(?:不采用|不保留|不考虑|不要|不用|排除|去掉|删除|移除)",
                    normalized,
                    re.IGNORECASE,
                ):
                    removed_cases.append(case)
                    break
                if re.search(
                    rf"(?:加入|加回|恢复|重新采用|重新保留|也保留)"
                    rf"[^，,。；;！？?]{{0,3}}{escaped}|{escaped}[^，,。；;！？?]{{0,3}}"
                    rf"(?:加入|加回|恢复|重新采用|重新保留)",
                    normalized,
                    re.IGNORECASE,
                ):
                    restored_cases.append(case)
                    break
        for old_case in recommended_cases:
            for new_case in recommended_cases:
                if old_case == new_case:
                    continue
                if any(
                    re.search(
                        rf"(?:把)?{re.escape(old_label)}[^，,。；;！？?]{{0,4}}"
                        rf"(?:换成|替换为|改成){re.escape(new_label)}",
                        normalized,
                        re.IGNORECASE,
                    )
                    for old_label in labels_for(old_case)
                    for new_label in labels_for(new_case)
                ):
                    replacement_pairs.append((old_case, new_case))

        only_cases = [case for case in mentioned_cases if case not in set(removed_cases)]
        mentions_all_as_group = (
            bool(re.search(r"(?:都|一起|同时).{0,5}(?:要|考虑|保留|采用|研究|观察|比较|看|做)?", normalized))
            and set(mentioned_cases) == set(recommended_cases)
        )

        if rejects_bundle:
            cases = []
            status = "REJECTED"
        elif replacement_pairs:
            for old_case, new_case in replacement_pairs:
                cases = [case for case in cases if case != old_case]
                if new_case not in cases:
                    cases.append(new_case)
            status = (
                "ACCEPTED"
                if set(cases) == set(recommended_cases)
                else "MODIFIED"
            )
        elif only_instruction and only_cases:
            cases = list(dict.fromkeys(only_cases))
            status = (
                "ACCEPTED"
                if set(cases) == set(recommended_cases)
                else "MODIFIED"
            )
        elif removed_cases:
            cases = [case for case in cases if case not in set(removed_cases)]
            status = "MODIFIED" if cases else "REJECTED"
        elif restored_cases:
            cases = list(dict.fromkeys([*cases, *restored_cases]))
            status = (
                "ACCEPTED"
                if set(cases) == set(recommended_cases)
                else "MODIFIED"
            )
        elif accepts_bundle or mentions_all_as_group:
            cases = list(recommended_cases)
            status = "ACCEPTED"
        elif control_turn and normalized != "继续" and status == "PENDING":
            cases = list(recommended_cases)
            status = "ACCEPTED"

        item["recommended_cases"] = recommended_cases
        item["cases"] = cases
        item["role"] = "PROPOSED_BASELINE_COMPARISON"
        item["adoption_status"] = status
        updated.append(item)
    return updated


def build_stage_one_turn_context(
    text: str,
    *,
    options: Sequence[dict[str, Any]],
    scenes: Sequence[dict[str, Any]] = (),
    idea_context: dict[str, Any] | None,
    selected_option_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a Stage 1 turn against the active idea thread.

    Ambiguous fragments are treated as refinements only after the student has
    established an ECE329 topic. An explicit topic switch starts a fresh scope
    decision instead of inheriting the old topic.
    """

    idea = idea_context if isinstance(idea_context, dict) else {}
    resolved = resolve_option_id(selected_option_id, options) or resolve_option_reference(
        text,
        options,
    )
    resolved_scene_relations = resolve_scene_references(text, scenes)
    preclassification = preclassify_stage_one_input(text)
    previous_focus = str(
        idea.get("current_focus")
        or idea.get("main_direction")
        or idea.get("current_summary")
        or ""
    ).strip()
    topic_anchor = str(idea.get("topic_anchor", "")).strip()
    scope_confirmed = idea.get("course_scope_confirmed") is True
    explicit_switch = is_explicit_topic_switch(text) or bool(
        preclassification == AMBIGUOUS
        and re.search(_COMPLETE_TOPIC_INTRODUCTION_PATTERN, text.strip(), re.IGNORECASE)
    )
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
    elif resolved is not None or resolved_scene_relations or (
        preclassification == AMBIGUOUS and contextual_continuation
    ):
        effective_classification = COURSE_CONTENT
    elif preclassification == AMBIGUOUS:
        effective_classification = OUT_OF_SCOPE
    else:
        effective_classification = preclassification
    prompt_preclassification = (
        COURSE_CONTENT
        if effective_classification == COURSE_CONTENT
        and (resolved is not None or resolved_scene_relations or contextual_continuation)
        else preclassification
    )

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
            str(scene.get("scene_id"))
            for scene in scenes
            if isinstance(scene, dict)
            and isinstance(scene.get("course_anchor"), dict)
            and scene.get("course_anchor") in resolved_scene_relations
            and str(scene.get("scene_id") or "").strip()
        ]
    elif resolved is not None:
        selected_course_relations = [dict(resolved)]
        selected_scene_ids = []
    relation_selection_text = " + ".join(
        str(item.get("direction") or item.get("focus") or "").strip()
        for item in selected_course_relations
        if str(item.get("direction") or item.get("focus") or "").strip()
    )
    normalized = text.strip()
    history = idea.get("focus_history", [])
    focus_history = [str(item).strip() for item in history if str(item).strip()] \
        if isinstance(history, list) else []
    control_turn = is_stage_one_control_message(normalized)
    if is_no_direction_request(normalized) or control_turn:
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
    if not topic_anchor and effective_classification == COURSE_CONTENT and not is_no_direction_request(normalized):
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
    elif control_turn:
        brainstorm_phase = previous_phase
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
    selected_focus = resolved_focus or previous_selected_focus
    if brainstorm_phase == INTEREST_DESCRIPTION and not selected_focus:
        selected_focus = normalized or proposed_focus
    interest_description = (
        normalized
        if brainstorm_phase == DEPTH_EXPANSION and not control_turn
        else str(idea.get("interest_description") or "").strip()
    )
    previous_core_phenomenon = str(idea.get("core_phenomenon") or "").strip()
    core_phenomenon = previous_core_phenomenon
    if (
        brainstorm_phase == DEPTH_EXPANSION
        and previous_phase == INTEREST_DESCRIPTION
        and not control_turn
    ):
        core_phenomenon = normalized
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
        and normalized
        and (not refinement_notes or refinement_notes[-1] != normalized)
    ):
        refinement_notes.append(normalized)
    refinement_notes = refinement_notes[-6:]
    focus_parts = [topic_anchor]
    if relation_selection_text and relation_selection_text != topic_anchor:
        focus_parts.append(relation_selection_text)
    if core_phenomenon and core_phenomenon not in focus_parts:
        focus_parts.append(core_phenomenon)
    for note in refinement_notes[-2:]:
        if note not in focus_parts:
            focus_parts.append(note)
    proposed_focus = " → ".join(item for item in focus_parts if item) or previous_focus
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
    standard_comparisons = update_standard_comparison_decisions(
        normalized,
        standard_comparisons,
        control_turn=control_turn,
    )
    direction_summary = core_phenomenon or selected_focus or topic_anchor or proposed_focus
    if refinement_notes:
        direction_summary = f"{direction_summary}；观察重点：{'；'.join(refinement_notes[-2:])}"

    return {
        "stage_one_preclassification": prompt_preclassification,
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
    }


def course_example_options() -> list[dict[str, object]]:
    return KNOWLEDGE.broad_entry_points()
