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

_CHINESE_ORDINALS = {"一": 0, "二": 1, "三": 2}
_OPTION_REFERENCE_PATTERNS = (
    r"第\s*([一二三123])\s*(?:个|项|类|条|种|方向|例子)",
    r"第\s*([一二三123])\s*$",
    r"(?:选|选择|研究|想要|考虑)\s*(?:第\s*)?([一二三123])\s*(?:个|项|类|条|种|方向|例子)",
    r"(?:选|选择)\s*([123])\s*$",
    r"(?:上面|刚才|之前).{0,6}([一二三123])\s*(?:个|项|类|条|种|方向|例子)",
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
    # Attempts to repurpose the course assistant through unrelated platforms or services.
    r"(接入|调用|连接|控制).{0,16}(b站|哔哩哔哩|youtube|抖音|网站|平台|机器人|bot|agent|智能体)",
    r"(b站|哔哩哔哩|youtube|抖音).{0,20}(翻译|输出|脚本|代码|agent|智能体)",
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


def referenced_option_index(text: str) -> int | None:
    normalized = text.strip()
    for pattern in _OPTION_REFERENCE_PATTERNS:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if not match:
            continue
        raw_index = match.group(1)
        if raw_index in _CHINESE_ORDINALS:
            return _CHINESE_ORDINALS[raw_index]
        numeric_index = int(raw_index) - 1
        return numeric_index if numeric_index >= 0 else None
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


def build_stage_one_turn_context(
    text: str,
    *,
    options: Sequence[dict[str, Any]],
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
        or (
            scope_confirmed
            and previous_focus
            and not explicit_switch
            and preclassification != UNREASONABLE_REQUEST
        )
    )
    if preclassification == UNREASONABLE_REQUEST:
        effective_classification = UNREASONABLE_REQUEST
    elif resolved is not None or (
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
        and (resolved is not None or contextual_continuation)
        else preclassification
    )

    selected_text = "——".join(
        str(resolved.get(key, "")).strip()
        for key in ("direction", "focus")
        if resolved is not None and str(resolved.get(key, "")).strip()
    )
    normalized = text.strip()
    history = idea.get("focus_history", [])
    focus_history = [str(item).strip() for item in history if str(item).strip()] \
        if isinstance(history, list) else []
    control_turn = is_stage_one_control_message(normalized)
    if is_no_direction_request(normalized) or control_turn:
        focus_component = ""
    elif selected_text:
        focus_component = selected_text
    elif effective_classification == COURSE_CONTENT:
        focus_component = normalized
    else:
        focus_component = ""
    if focus_component and (not focus_history or focus_history[-1] != focus_component):
        focus_history.append(focus_component)
    focus_history = focus_history[-8:]
    proposed_focus = " → ".join(focus_history[-4:]) or previous_focus
    if not topic_anchor and effective_classification == COURSE_CONTENT and not is_no_direction_request(normalized):
        topic_anchor = normalized
    elif explicit_switch and effective_classification == COURSE_CONTENT:
        topic_anchor = normalized
        focus_history = [normalized]
        proposed_focus = normalized

    previous_phase = str(
        idea.get("brainstorm_phase") or BREADTH_EXPLORATION
    ).strip()
    previous_selected_focus = str(idea.get("selected_focus") or "").strip()
    if effective_classification != COURSE_CONTENT:
        brainstorm_phase = BREADTH_EXPLORATION
    elif control_turn:
        brainstorm_phase = previous_phase
    elif resolved is not None:
        brainstorm_phase = INTEREST_DESCRIPTION
    elif previous_phase == INTEREST_DESCRIPTION:
        brainstorm_phase = DEPTH_EXPANSION
    elif previous_phase == DEPTH_EXPANSION:
        brainstorm_phase = DEPTH_EXPANSION
    elif contextual_continuation and scope_confirmed:
        brainstorm_phase = INTEREST_DESCRIPTION
    else:
        brainstorm_phase = BREADTH_EXPLORATION
    resolved_focus = str(
        resolved.get("direction") or resolved.get("focus") or ""
    ).strip() if resolved is not None else ""
    selected_focus = resolved_focus or previous_selected_focus
    if brainstorm_phase == INTEREST_DESCRIPTION and not selected_focus:
        selected_focus = normalized or proposed_focus
    interest_description = (
        normalized
        if brainstorm_phase == DEPTH_EXPANSION and not control_turn
        else str(idea.get("interest_description") or "").strip()
    )

    return {
        "stage_one_preclassification": prompt_preclassification,
        "effective_input_category": effective_classification,
        "raw_stage_one_preclassification": preclassification,
        "resolved_stage_one_reference": resolved,
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
        "interest_description": interest_description,
        "ready_for_next_stage": (
            brainstorm_phase == DEPTH_EXPANSION and len(focus_history) >= 3
        ),
        "control_turn": control_turn,
    }


def course_example_options() -> list[dict[str, object]]:
    return KNOWLEDGE.broad_entry_points()
