"""Evidence-backed feedback, shared by both modes. Local checks need no model.

Reviewed rules guide parsing and generation. User reports are bounded candidate
records, never executable instructions or automatically promoted global rules.
"""
from __future__ import annotations

from copy import copy, deepcopy
import hashlib
import json
import re
from typing import Any

from .models import DesignSession, StepOutput


RULE_VERSION = 1
RULES = (
    ("answered_pending", "FB01", "用户指出已回答时，核对已提交字段与待办版本；有证据才关闭旧待办，不把建议当批准。"),
    ("cross_stage_edit", "FB02", "跨阶段修改按字段定位执行，保留其他字段；核对提交结果和相关报告投影，不能只声称已修改。"),
    ("meta_question", "FB03", "元问题先回答当前问题、进度或原因；与设计修改混合时分别处理，不把元问题写入设计。"),
    ("missed_requests", "FB04", "逐项拆分本轮请求并绑定回答证据；回答一个问题不代表其他问题完成，未答项保留待处理。"),
    ("artifact_mismatch", "FB05", "对话、报告和Builder输入使用同一当前设计；以版本和字段值核对，不复用过期导出或声称已检查未知PDF。"),
)
PATTERNS = {
    "answered_pending": r"(?:已经|早就|之前|刚才|都).{0,10}(?:回答过|回答了|说过|确认过)|(?:反复|重复|又|还在).{0,8}(?:问|确认)|already (?:answered|confirmed)|keep asking",
    "cross_stage_edit": r"(?:修改|改动|改的|改了|跨阶段).{0,14}(?:没生效|没有生效|无效|丢了|没了|失效|不见|没保存)|(?:之前|前面|上个阶段).{0,15}(?:改为|改成|修改)|change.{0,12}(?:lost|not saved|didn't stick)",
    "meta_question": r"(?:现在|当前|你).{0,12}(?:问什么|在问啥|什么问题|哪一步|哪个阶段)|我.{0,6}(?:混乱|糊涂|没跟上)|what are you asking|where are we",
    "missed_requests": r"(?:没有|没|漏).{0,5}(?:回答|回应)|(?:多个|几个|两个).{0,8}(?:问题|请求).{0,8}(?:一个|漏|没)|missed.{0,12}(?:question|request)",
    "artifact_mismatch": r"(?:pdf|报告|导出|页面).{0,20}(?:不一致|矛盾|没更新|旧|漏|缺|不一样|错误)|(?:不一致|矛盾|没写入).{0,12}(?:pdf|报告)|pdf.{0,20}(?:stale|wrong|missing|different)",
}


def categories(message: str) -> list[str]:
    return [key for key, pattern in PATTERNS.items() if re.search(pattern, message, re.I)]


def feedback_only(message: str) -> list[str]:
    """Conservative interception: never swallow a following edit or course question."""
    found = categories(message)
    if not found or found == ["meta_question"]:
        return []
    # Named historical content already has a read-only lookup/explicit-restore
    # route. A generic complaint response must not hide that available evidence.
    if (re.search(r"阶段\s*\d|之前|前面|刚才|以前|找回|找到|还记得", message)
            and re.search(r"流程|研究问题|学习目标|参数|观察量|实验对象|数值方案", message)):
        return []
    clauses = [s.strip() for s in re.split(r"[。！？!?；;，,\n]", message) if s.strip()]
    for clause in clauses:
        from .meta_dialogue import meta_question_kinds
        if (re.search(r"为什么|为何|怎么|如何|什么|\b(?:why|how|what)\b", clause, re.I)
                and not meta_question_kinds(clause) and clause not in {"为什么", "怎么回事"}):
            return []
        if re.search(r"改为|改成|替换|设为|设置|删除|新增|采用|撤销|恢复|\b(?:set|replace|delete|add|undo)\b", clause, re.I):
            return []
        if categories(clause):
            continue
        if re.fullmatch(r"(?:请|麻烦)?(?:检查一下|修复一下|修复|检查|解决|别再问了|不要继续|先别继续|为什么|怎么回事|谢谢)", clause):
            continue
        return []
    return found


def snapshot(session: DesignSession) -> dict[str, Any]:
    from .turn_planning import workflow_design_snapshot
    # Snapshot migration only writes design_context; history and stage outputs
    # are read-only inputs. Do not copy the growing conversation for each check.
    view = copy(session)
    view.design_context = deepcopy(session.design_context)
    return workflow_design_snapshot(view)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def source_stamp(session: DesignSession) -> dict[str, Any]:
    # Include source inputs used by report builders, but exclude diagnostic logs,
    # revisions, UI history and credentials. Meta replies do not change the hash.
    fields = snapshot(session)
    fields.pop("revision", None)
    return {"revision": session.revision, "fingerprint": digest({
        "mode": session.interaction_state.value,
        "fields": fields,
        "references": session.design_context.get("builder_reference_material", []),
    })}


def validate_builder_projection(session: DesignSession, payload: dict) -> None:
    """Check actual emitted values as well as provenance, before PDF rendering."""
    from .builder_input import _text
    from .builder_requirements import builder_requirement_values
    from .reporting import effective_emvr_stage_payload
    from .models import Stage
    stamp = payload.get("document", {}).get("source_design", {})
    if stamp.get("fingerprint") != source_stamp(session)["fingerprint"]:
        raise ValueError("Builder input refers to an outdated design snapshot")
    bindings = {
        Stage.RESEARCH_QUESTION: {"research_question": "main_research_question"},
        Stage.VARIABLES_AND_CONDITIONS: {"independent_variable": "independent_variable", "dependent_variable": "dependent_variable",
                                       "controlled_variables": "controlled_variables", "reference_condition": "reference_condition"},
        Stage.HYPOTHESIS: {"research_hypothesis": "research_hypothesis", "expected_trend": "expected_trend", "limiting_cases": "limiting_cases"},
    }
    emitted = {r.get("key"): r.get("value") for r in payload.get("design_definition", []) if isinstance(r, dict)}
    expected_values = {}
    for stage, fields in bindings.items():
        values = effective_emvr_stage_payload(deepcopy(session), stage)
        for row, field in fields.items():
            expected = _text(values.get(field))
            if expected:
                expected_values[row] = expected
    expected_values["numerical_model"] = builder_requirement_values(deepcopy(session)).get("numerical_model_specifications")
    from .builder_portability import embed_supplied_references
    expected_values, _ = embed_supplied_references(expected_values, session.design_context.get("builder_reference_material", []))
    for row, expected in expected_values.items():
        if row != "numerical_model" and emitted.get(row) != expected:
            raise ValueError(f"Builder input differs from saved design: {row}")
    if payload.get("physics", {}).get("numerical_model") != expected_values["numerical_model"]:
        raise ValueError("Builder input differs from saved design: numerical_model_specifications")


def guidance(session: DesignSession, message: str, *, limit: int = 3, max_chars: int = 650) -> dict:
    if not session.model_context.get('model_config', {}).get('experience_enabled', True):
        return {'version': RULE_VERSION, 'rules': [], 'authority': 'experience disabled; deterministic validation remains enabled'}
    selected = categories(message)
    if re.search(r"改为|改成|修改|替换|删除|补充|replace|change", message, re.I):
        selected.append("cross_stage_edit")
    if len(re.findall(r"[?？]", message)) > 1:
        selected.append("missed_requests")
    # Only reviewed rules enter prompts; raw candidates and reports never do.
    rows = []
    used = 0
    for learned in session.turn_context.get('experience_rules', []):
        instruction = learned.get('instruction', '')
        if instruction and used + len(instruction) <= max_chars and len(rows) < min(max(limit, 0), 5):
            rows.append(deepcopy(learned))
            used += len(instruction)
    for kind in dict.fromkeys(selected):
        rule = next((r for r in RULES if r[0] == kind), None)
        if rule and used + len(rule[2]) <= max_chars and len(rows) < min(max(limit, 0), 5):
            rows.append({"id": rule[1], "instruction": rule[2]})
            used += len(rule[2])
    return {"version": RULE_VERSION, "rules": rows, "authority": "advisory; current user requirements and validated state take precedence"}


def reconcile_meta_requests(intent: dict, message: str) -> None:
    """Recover explicit unquoted meta clauses without recompiling valid writes."""
    from .meta_dialogue import meta_question_kinds
    from .turn_planning import build_turn_task_plan
    unquoted = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', message)
    questions = intent.setdefault("semantic_updates", {}).setdefault("student_questions", [])
    for clause in re.split(r"[。；;，,\n]|(?<=[？?])", unquoted):
        clause = clause.strip()
        if not clause or not meta_question_kinds(clause):
            continue
        if any(clause.rstrip("？?") in str(q) for q in questions):
            continue
        questions.append(clause)
        act = {"act_id": "meta_" + digest(clause)[:16], "type": "ASK_COURSE_QUESTION",
               "target": "", "content": clause, "confidence": 1.0}
        intent.setdefault("dialogue_acts", []).append(act)
        plan = intent.setdefault("task_plan", {"tasks": [], "execution_order": []})
        addition = build_turn_task_plan([act])["tasks"][0]
        plan.setdefault("tasks", []).append(addition)
        order = {"COMMIT_DESIGN": 0, "RESPOND_TO_REQUEST": 1, "CLARIFY": 2, "NAVIGATE": 3}
        plan["execution_order"] = [t["task_id"] for t in sorted(plan["tasks"], key=lambda t: order[t["execution_phase"]])]


def record_event(session: DesignSession, kind: str, evidence: dict, *, outcome: str) -> dict:
    state = session.model_context.setdefault("feedback", {})
    events = state.setdefault("events", [])
    identity = digest({"kind": kind, "source": source_stamp(session)["fingerprint"], "evidence": evidence})
    old = next((event for event in events if event["id"] == identity), None)
    if old:
        old["occurrences"] += 1
        old["outcome"] = outcome
        return old
    event = {"id": identity, "category": kind, "mode": session.interaction_state.value,
             "stage": session.current_stage.value, "revision": session.revision,
             "evidence": deepcopy(evidence), "outcome": outcome, "occurrences": 1,
             "experience_status": "candidate", "rule_version": RULE_VERSION}
    events.append(event)
    del events[:-50]
    return event


def _fields(pending: dict | None) -> list[str]:
    if not isinstance(pending, dict):
        return []
    return [f for f in pending.get("answer_fields", []) if isinstance(f, str)]


def _open_answer(pending: dict | None) -> bool:
    return bool(pending and pending.get("type") in {"ANSWER_STAGE_QUESTION", "ANSWER_EMVR_STAGE_QUESTION"}
                and not pending.get("default_proposal_value") and not pending.get("candidate_answer"))


def pending_key(pending: dict) -> str:
    identity = {k: pending.get(k) for k in ("type", "stage", "subject", "answer_fields", "required_facet_id")}
    # One field can contain several independent requirements (for example seed
    # placement and numerical tolerance). Saving one does not answer the next.
    identity["question"] = re.sub(r"\s+", "", str(pending.get("question") or "")).rstrip("?？。")
    return digest(identity)


def remember_answer(session: DesignSession, pending: dict | None, diff: dict) -> None:
    fields = _fields(pending)
    satisfied = set(diff.get("changed_fields", [])) | set(diff.get("unchanged_requested_fields", []))
    values = snapshot(session)
    if not _open_answer(pending) or not fields or not set(fields) <= satisfied:
        return
    if any(values.get(f) in (None, "", [], {}) for f in fields):
        return
    records = session.model_context.setdefault("feedback", {}).setdefault("answers", [])
    record = {"key": pending_key(pending), "fingerprint": source_stamp(session)["fingerprint"],
              "fields": fields, "values": {f: deepcopy(values[f]) for f in fields}, "revision": session.revision}
    records[:] = [r for r in records if r["key"] != record["key"]][-49:] + [record]


def answered_evidence(session: DesignSession, pending: dict | None) -> dict | None:
    if not _open_answer(pending):
        return None
    fingerprint = source_stamp(session)["fingerprint"]
    return next((r for r in reversed(session.model_context.get("feedback", {}).get("answers", []))
                 if r["key"] == pending_key(pending) and r["fingerprint"] == fingerprint), None)


def close_answered_pending(session: DesignSession, pending: dict) -> dict | None:
    evidence = answered_evidence(session, pending)
    if evidence:
        from .dialogue_state import dialogue_state
        from .design_state import set_pending_action_snapshot
        dialogue_state(session)["pending_action"] = None
        set_pending_action_snapshot(session, None)
        # Historical artifact prompts are not evidence of a new pending decision.
        session.model_context.setdefault("feedback", {})["closed_pending"] = {
            "key": pending_key(pending), "stage": session.current_stage.value,
            "fingerprint": source_stamp(session)["fingerprint"],
        }
    return evidence


def inspect_pending(session: DesignSession, output: StepOutput, *, permit_loop_guard: bool = True) -> bool:
    """One local correction per output; no recursive model or engine calls."""
    from .dialogue_state import current_pending_action
    pending = current_pending_action(session)
    if not pending:
        return False
    question = str(pending.get("question") or "")
    evidence = close_answered_pending(session, pending)
    if evidence:
        record_event(session, "answered_pending", {"pending": pending_key(pending), "answer_revision": evidence["revision"]}, outcome="repaired")
        output.student_task = None
        if question:
            output.assistant_message = output.assistant_message.replace(question, "").strip()
        output.assistant_message += "\n\n这一项已有保存的回答，已撤下重复待办；其他设计内容保持不变。"
        output.stage_payload["feedback_recovery"] = {"category": "answered_pending", "status": "repaired"}
        return False
    if not permit_loop_guard or not _open_answer(pending):
        return False
    state = session.model_context.setdefault("feedback", {})
    key = digest({"pending": pending_key(pending), "source": source_stamp(session)["fingerprint"], "question": question})
    last = state.get("last_pending", {})
    count = last.get("count", 0) + 1 if last.get("key") == key else 1
    state["last_pending"] = {"key": key, "count": min(count, 3)}
    if count < 3:
        return False
    record_event(session, "answered_pending", {"pending": pending_key(pending), "reason": "unchanged_question_and_state"}, outcome="needs_review")
    # Keep the actionable binding for the next substantive answer, but don't
    # replay the same question or spend another model call on this failed turn.
    output.student_task = None
    if question:
        output.assistant_message = output.assistant_message.replace(question, "").strip()
    output.assistant_message += "\n\n这项待办连续出现，但记录没有有效变化。我已停止重复追问，保留现有设计；目前还没有足够证据确认这一项已完成。你可以查看已保存内容，或指出需要恢复的那条回答。"
    output.stage_payload["feedback_recovery"] = {"category": "answered_pending", "status": "needs_review", "retry_requires_change": True}
    state["loop_blocker"] = {"pending": pending_key(pending), "fingerprint": source_stamp(session)["fingerprint"]}
    return True


def blocked_repeat(session: DesignSession, message: str) -> bool:
    from .dialogue_state import current_pending_action
    if re.sub(r"\s|[。.!！]", "", message).casefold() not in {"继续", "确认", "确认继续", "确认并继续", "下一步", "continue"}:
        return False
    pending = current_pending_action(session)
    blocker = session.model_context.get("feedback", {}).get("loop_blocker", {})
    return bool(_open_answer(pending) and blocker.get("pending") == pending_key(pending)
                and blocker.get("fingerprint") == source_stamp(session)["fingerprint"])


def question_task_evidence(plan: dict | None, payload: dict) -> dict[str, bool]:
    """Map answer receipts to individual tasks, never a blanket response type."""
    def norm(value: str) -> str:
        return re.sub(r"\s+", "", value).rstrip("?？。")
    answered = {norm(q) for key in ("answered_student_questions", "partially_answered_student_questions")
                for q in payload.get(key, []) if isinstance(q, str)}
    evidence = {}
    for task in (plan or {}).get("tasks", []):
        if task.get("type") != "ASK_COURSE_QUESTION":
            continue
        content = task.get("content")
        questions = [content] if isinstance(content, str) else content if isinstance(content, list) else []
        parts = [p for q in questions if isinstance(q, str) for p in re.split(r"(?<=[?？])\s*", q) if p.strip()]
        evidence[task["task_id"]] = bool(parts) and all(norm(p) in answered for p in parts)
    return evidence


def audit_task_plan(session: DesignSession, plan: dict, payload: dict) -> None:
    missing = [t["task_id"] for t in plan.get("tasks", []) if t.get("status") == "READY"
               and t.get("execution_phase") == "RESPOND_TO_REQUEST"]
    if missing:
        record_event(session, "missed_requests", {"task_ids": missing}, outcome="needs_review")
    session.model_context.setdefault("feedback", {})["last_tasks"] = deepcopy(plan)
    queue = session.model_context["feedback"].setdefault("unanswered_tasks", [])
    answered = {re.sub(r"\s+", "", q).rstrip("?？。")
                for key in ("answered_student_questions", "partially_answered_student_questions")
                for q in payload.get(key, []) if isinstance(q, str)}
    for task in plan.get("tasks", []):
        if task.get("type") != "ASK_COURSE_QUESTION":
            continue
        identity = digest(task.get("content"))
        queue[:] = [t for t in queue if digest(t.get("content")) != identity and t.get("origin_task_id") != task["task_id"]]
        if task.get("status") == "READY":
            content = task.get("content")
            parts = [p.strip() for p in re.split(r"(?<=[?？])\s*", content) if p.strip()] if isinstance(content, str) else []
            for index, part in enumerate(parts):
                if re.sub(r"\s+", "", part).rstrip("?？。") in answered:
                    continue
                item = deepcopy(task)
                item["content"] = part
                item["origin_task_id"] = task["task_id"]
                if len(parts) > 1:
                    item["task_id"] = task["task_id"] + f"_part_{index}"
                queue.append(item)
    del queue[:-50]


def restore_committed_fields(session: DesignSession, expected: dict, intent: dict, stage) -> list[str]:
    """Repair late generator drift once using already committed field values."""
    from .turn_planning import requested_fields_from_plan
    from .dialogue_acts import DESIGN_ACT_FIELDS, STAGE_ACT_FIELDS, apply_stage_field_updates
    from .design_state import apply_design_updates
    from .emvr_design import EMVR_EDITABLE_FIELDS, apply_emvr_field_updates
    actual = snapshot(session)
    drift = [f for f in requested_fields_from_plan(intent.get("task_plan")) if f in expected and actual.get(f) != expected[f]]
    for field in drift:
        update = {"field": field, "operation": "REPLACE", "value": deepcopy(expected[field])}
        if field in EMVR_EDITABLE_FIELDS and session.interaction_state.value == "EMVR_DIRECT":
            apply_emvr_field_updates(session.design_context.setdefault("emvr_design", {}), {
                "field_updates": [{"field_id": field, "operation": "REPLACE", "value": deepcopy(expected[field])}],
            })
        elif field in STAGE_ACT_FIELDS:
            apply_stage_field_updates(session, [update], stage=stage)
        elif field in DESIGN_ACT_FIELDS:
            apply_design_updates(session, [update])
    if drift:
        after = snapshot(session)
        record_event(session, "cross_stage_edit", {"fields": drift},
                     outcome="repaired" if all(after.get(f) == expected[f] for f in drift) else "needs_review")
    return drift


def feedback_output(session: DesignSession, message: str, found: list[str]) -> dict:
    from .dialogue_state import current_pending_action
    from .turn_planning import FIELD_LABELS
    from .design_state import format_design_summary
    lines = []
    details = {}
    for kind in found:
        if kind == "answered_pending":
            pending = current_pending_action(session)
            evidence = close_answered_pending(session, pending) if pending else None
            if evidence:
                values = "；".join(f"{FIELD_LABELS.get(f, f)}：{v}" for f, v in evidence["values"].items())
                lines.append("已找到与当前设计一致的回答：" + values + "。已撤下重复待办，不需要再次回答。")
            else:
                lines.append("我会保留已有设计，停止重复催答。目前没有足够的提交记录证明这项待办已解决，不能把它直接标为完成。")
            outcome = "repaired" if evidence else "needs_review"
        elif kind == "cross_stage_edit":
            lines.append("当前已保存的内容如下，可据此核对哪项修改没有落地：\n" + format_design_summary(deepcopy(session)))
            outcome = "needs_review"
        elif kind == "missed_requests":
            state = session.model_context.get("feedback", {})
            plan = state.get("last_tasks", {})
            tasks = state.get("unanswered_tasks", [t for t in plan.get("tasks", []) if t.get("status") in {"READY", "NEEDS_CLARIFICATION", "BLOCKED"} and t.get("execution_phase") != "NAVIGATE"])
            lines.append("已找到尚未完成的请求：\n" + "\n".join(f"- {t.get('content') or t.get('target')}" for t in tasks)
                         if tasks else "现有任务记录不足以确定漏答了哪一项，我不会把生成过回复当作全部问题都已回答的证据。")
            outcome = "needs_review"
        elif kind == "artifact_mismatch":
            lines.append(f"已记录报告与对话不一致的反馈。当前设计版本为 {session.revision}；重新下载会按当前保存内容生成报告。尚未读取你看到的旧PDF，不能声称其中的具体矛盾已经修复。")
            outcome = "needs_review"
        else:
            from .meta_dialogue import meta_question_output
            lines.append(meta_question_output(session, ["question"])["assistant_message"])
            outcome = "answered"
        details[kind] = lines[-1]
        record_event(session, kind, {"report": message[:1200]}, outcome=outcome)
    return StepOutput(assistant_message="\n\n".join(lines), stage_payload={
        "presentation_only": True, "preserve_pending_action": True,
        "feedback_response": found,
        "feedback_details": details,
    }).to_dict()
