"""Read-only answers about the conversation, shared by both design modes."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .builder_requirements import BUILDER_REQUIREMENT_SPECS
from .design_versions import _diff_values
from .dialogue_state import hydrate_pending_action_from_history
from .models import DesignSession, InteractionState, WorkflowStatus
from .stages import stage_group_metadata, stage_title
from .turn_planning import FIELD_LABELS


_META_PATTERNS = {
    "changes": r"(?:刚才|刚刚|上轮|上一轮|之前|这次).{0,14}(?:改了什么|改了啥|修改了什么|改动了什么|改了哪些|改动了哪些|修改了哪些|哪些.{0,6}(?:改动|修改))|what did you (?:just )?change",
    "mode": r"(?:现在|当前|我们).{0,8}(?:什么|哪个|哪种)模式|(?:两个|两种|引导|EMVR).{0,10}模式.{0,8}(?:区别|不同)|what mode are we",
    "progress": r"(?:现在|当前|我们|流程).{0,12}(?:到哪|哪一?个?阶段|哪一?步|进展|进度)|(?:到|在)第几(?:步|阶段)|where are we|what (?:stage|step) are we",
    "reason": r"(?:为什么|为何|怎么|为啥).{0,15}(?:还在问|又问|重复|卡住|卡在|不继续|不能继续|不往下|还没进入|问这个|需要我确认)|why (?:are we stuck|do you keep asking|are you asking)",
    "question": r"(?:你|现在|当前|刚才|这次).{0,12}(?:问什么|问的是什么|在问啥|什么问题|需要我.{0,6}(?:回答|补充|确认|做什么)|要我.{0,6}(?:回答|补充|确认|做什么))|(?:把|将)(?:当前|现在|刚才|上一轮)的问题.{0,8}(?:再说|重述|说明|解释)|(?:这个|刚才的|当前的)问题.{0,8}(?:什么意思|是什么意思|再说一遍)|what are you asking|what do you need from me|what (?:do you need|am I supposed) to",
    "guidance": r"(?:我|现在|接下来).{0,10}(?:该回答什么|该怎么回答|应该回答什么|需要做什么|该做什么)|(?:我(?:还是|现在)?有点乱|我搞混了|我被绕晕了|我糊涂了|我有点混乱|我没跟上)|what should I (?:answer|do)|I(?:'m| am) (?:confused|lost)",
}


def meta_question_kinds(message: str) -> list[str]:
    """Only intercept conversation questions; mixed substantive edits keep their route."""
    # A question quoted inside a replacement is data, not a read-only request.
    if re.search(r"改为|改成|替换为|设置为|设为|删除|新增|添加|采用下面|使用下面|恢复之前|撤销|切换到|换成|实验流程\s*[：:]|\b(?:replace|set|delete|add|undo|switch to)\b", message, re.I):
        return []
    if re.search(r"\d\s*(?:nC|mA|mT|cm|mm|Hz|[AVTCΩ]|安培|伏特|米|特斯拉)(?![a-z])|(?:默认|实验对象|自变量|观察量)(?:是|为|设为|[：:])", message, re.I):
        return []
    # A conversational preface cannot authorize dropping a following edit.
    clauses = [part.strip() for part in re.split(r"[。！？!?；;，,\n]", message) if part.strip()]
    for clause in clauses:
        if any(re.search(pattern, clause, re.I) for pattern in _META_PATTERNS.values()):
            continue
        if re.fullmatch(r"(?:请)?(?:先别继续|先不要继续|暂时别继续|明确告诉我|直接告诉我|说清楚一点|请说清楚|麻烦说明一下|谢谢)|(?:please|don't advance|do not advance|thanks)", clause, re.I):
            continue
        return []
    return [kind for kind, pattern in _META_PATTERNS.items() if re.search(pattern, message, re.I)]


def _visible_output(session: DesignSession) -> dict:
    for entry in reversed(session.history):
        if entry.get("handled_stage", entry.get("stage")) != session.current_stage.value:
            continue
        output = entry.get("output", {})
        if isinstance(output, dict) and not output.get("stage_payload", {}).get("meta_question"):
            return output
    return session.stage_outputs.get(session.current_stage.value, {})


def _render_value(value) -> str:
    if value is None:
        return "未填写"
    if isinstance(value, list):
        return "；".join(_render_value(item) for item in value) or "空列表"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _recent_changes(session: DesignSession) -> str:
    versions = session.model_context.get("design_versions", [])
    for index in range(len(versions) - 1, 0, -1):
        changes = _diff_values(versions[index - 1].get("snapshot"), versions[index].get("snapshot"))
        if changes:
            return "最近一次已保存的修改：\n" + "\n".join(
                f"- {change['label']}：从“{_render_value(change['before'])}”改为“{_render_value(change['after'])}”。"
                for change in changes
            )
    return "现有记录没有可核对的修改前后版本，无法确定刚才改了哪些内容。"


def meta_question_output(session: DesignSession, kinds: list[str]) -> dict:
    # Legacy pending hydration can migrate state. Do it only on a copy so a
    # request for orientation cannot approve, repair or reset the live design.
    view = deepcopy(session)
    visible = _visible_output(view)
    # Skip previous meta replies when recovering a pre-upgrade pending pointer.
    view.history = [entry for entry in view.history
                    if not entry.get("output", {}).get("stage_payload", {}).get("meta_question")]
    complete = view.status is WorkflowStatus.COMPLETE
    pending = None if complete else hydrate_pending_action_from_history(view)
    meta = stage_group_metadata(view.current_stage, view.interaction_state)
    label = stage_title(view.current_stage, view.interaction_state)
    lines = []
    names = []
    if complete:
        lines.append("这份设计已完成，目前没有等待你回答的新问题。")
    else:
        question = str((pending or {}).get("question") or visible.get("student_task") or "").strip()
        fields = (pending or {}).get("answer_fields", [])
        labels = {**FIELD_LABELS, **{item['field']: item['label'] for item in BUILDER_REQUIREMENT_SPECS}}
        names = [str(labels[field]) for field in fields if field in labels and labels[field] != field]
        if names:
            lines.append("当前需要你处理的是：" + "、".join(names) + "。")
        lines.append("我现在的问题是：" + question if question else "当前没有保存可核对的具体待答问题。")
    lines.append(f"目前在第{meta['workflow_stage_number']}阶段“{meta['workflow_stage_title']}”"
                 + (f"，正在整理“{'、'.join(names) or label}”。" if meta['workflow_stage_number'] == 1 else "。"))
    if "mode" in kinds:
        lines.append("当前使用" + ("EMVR 模式" if view.interaction_state is InteractionState.EMVR_DIRECT else "引导模式")
                     + "。引导模式通过提问帮助你形成设计；EMVR 模式会整理可修改的实验及 Builder 交接方案。两种模式都先回应你的问题，再推进设计。")
    if "changes" in kinds:
        lines.append(_recent_changes(view))
    if "reason" in kinds:
        blocker = view.model_context.get("artifact_blocker", {})
        if isinstance(blocker, dict) and blocker.get("error"):
            lines.append("最近一次完成检查记录的问题是：" + str(blocker['error']))
        elif pending:
            lines.append("当前记录仍有上面的待办，所以还没有完成这一项。仅凭待办记录不能断定你之前没有回答。")
        elif not complete:
            lines.append("记录里没有明确的阻塞原因，不能据此推断卡住的原因。")
    elif not complete and not pending:
        blocker = view.model_context.get("artifact_blocker", {})
        if isinstance(blocker, dict) and blocker.get("error"):
            lines.append("最近一次完成检查记录的问题是：" + str(blocker['error']))
    if "guidance" in kinds and not complete and pending:
        if pending.get("default_proposal_value") or str(pending.get("type", "")).startswith("CONFIRM"):
            lines.append("这一步需要审阅上次展示的方案：认可时可确认，有疑问可以继续问，想调整时可指出具体位置。")
        else:
            lines.append("你可以直接回答上面这一个问题；拿不准时可以索取参考或要求解释，不必重新描述整份实验。")
    payload = {"meta_question": kinds, "presentation_only": True, "preserve_pending_action": True}
    if pending:
        payload['pending_action'] = deepcopy(pending)
        options = visible.get('stage_payload', {}).get('decision_options')
        if isinstance(options, list):
            payload['decision_options'] = deepcopy(options)
    return {"assistant_message": "\n\n".join(lines), "stage_payload": payload,
            "student_task": None, "visualization": None, "assumptions": [], "warnings": []}
