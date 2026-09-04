from __future__ import annotations

import re
from typing import Any, Protocol

from .dialogue_state import UserIntent, build_carried_context
from .design_state import seen_scene_signatures
from .emvr_design import (
    EMVR_THEORY_RELATIONS,
    emvr_formula_support_map,
    formulas_for_emvr_relations,
    merge_emvr_structured_requirements,
)
from .emvr_formula_flow import formula_support_map_for_selection
from .guardrails import (
    BREADTH_EXPLORATION,
    COURSE_CONTENT,
    DEPTH_EXPANSION,
    INTEREST_DESCRIPTION,
    OUT_OF_SCOPE,
    UNREASONABLE_REQUEST,
    classify_stage_one_input,
    course_example_options,
    shown_exploration_option_ids,
)
from .knowledge_base import KNOWLEDGE
from .models import DesignSession, InteractionState, Stage, StepOutput


class StageGenerator(Protocol):
    def generate(self, session: DesignSession, user_message: str) -> StepOutput: ...


NO_DIRECTION_ACKNOWLEDGEMENT = (
    "好的，那我来帮助你拓展思路。暂时没有具体方向也没关系。"
)


_GUIDED_STAGE_ENTRY_QUESTIONS: dict[Stage, str] = {
    Stage.VARIABLES_AND_CONDITIONS: (
        "先不急着列完整变量表。按照你的理解，这个实验中哪些量应该主动改变、"
        "哪些现象需要观察，又有哪些条件应该保持不变？可以先说你认为最重要的部分。"
    ),
    Stage.CONCEPTUAL_PROCEDURE: (
        "先不急着写标准流程。你认为在这个实验中，从建立比较基准到改变条件、观察现象和比较结果，"
        "需要经历哪些关键环节？请先按自己的思路描述。"
    ),
    Stage.EXPECTED_DATA_VISUALIZATION: (
        "前面已经有研究问题和预期趋势了。你觉得用一条曲线、几幅场分布图，"
        "还是两种显示配合起来，最容易判断预期有没有出现？如果暂时不确定，"
        "我可以先按已有变量搭一版参考。"
    ),
    Stage.RESULT_INTERPRETATION: (
        "回到前面提出的预期：如果显示结果与它一致，能支持哪部分解释；"
        "如果不一致，又该先检查条件、模型还是原来的判断？"
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: (
        "前面已经明确了学习目标，这里不再重复回答“能学到什么”。"
        "请看看现有变量、流程和显示是否真能实现这个目标，并指出一个最可能限制结论的理想化条件。"
    ),
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: (
        "最后请用自己的话把已经确定的研究问题、基础比较、预期现象和课程关系串成一段简短总结。"
        "不需要再次单独说明学习收获，也不需要写实现步骤。"
    ),
}

_GUIDED_STAGE_REFERENCE_STEPS: dict[Stage, tuple[str, ...]] = {
    Stage.VARIABLES_AND_CONDITIONS: (
        "把前面确定的变化主轴列为主动改变的量",
        "把准备观察或比较的现象列为观察量",
        "把其余会影响比较的源、几何、材料和观察方式列为控制条件",
    ),
    Stage.CONCEPTUAL_PROCEDURE: (
        "先建立一组可重复的基准条件",
        "按照前面确定的变化主轴逐步改变条件",
        "每次用相同方式观察并记录目标现象",
        "完成各组基础情形后并排比较，再联系ECE329课程关系解释",
    ),
    Stage.EXPECTED_DATA_VISUALIZATION: (
        "用前面确定的主动改变量作为横轴或交互控制量",
        "把最重要的观察量作为曲线、场图或通量显示",
        "把已经保留的基础情形并列呈现",
        "明确标注这是理论预测而非实测数据",
    ),
    Stage.RESULT_INTERPRETATION: (
        "先讨论结果符合预期时支持哪条物理解释",
        "再讨论偏离预期时应检查的条件或假设",
        "最后区分模型局限、展示误差与真正的物理差异",
    ),
    Stage.DESIGN_VALUE_AND_LIMITATIONS: (
        "回看最初确定的学习目标，检查现有变量、流程和显示是否足以支撑它",
        "指出它依赖的理想化条件",
        "区分概念展示能说明什么，以及不能据此推出什么",
    ),
}

def _student_idea_summary(session: DesignSession) -> str:
    idea = session.design_context.get("idea", {})
    if isinstance(idea, dict):
        for key in ("direction_summary", "current_focus", "main_direction", "core_phenomenon"):
            value = str(idea.get(key) or "").strip()
            if value:
                return value[-180:]
    outline = session.design_context.get("experiment_outline_seed", {})
    if isinstance(outline, dict):
        value = str(outline.get("core_phenomenon") or "").strip()
        if value:
            return value[-180:]
    return "前面已经完善的实验想法"


def _compact_context_items(
    value: Any,
    limit: int = 3,
    item_length: int = 90,
) -> str:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                preferred = (
                    item.get("cases")
                    or item.get("recommended_cases")
                    or item.get("name")
                    or item.get("label")
                )
                if isinstance(preferred, list):
                    items.extend(str(child) for child in preferred)
                elif preferred is not None:
                    items.append(str(preferred))
            else:
                items.append(str(item))
    else:
        items = []
    cleaned = list(dict.fromkeys(item.strip() for item in items if item.strip()))
    def compact_item(item: str) -> str:
        if len(item) <= item_length:
            return item
        prefix = item[:item_length]
        boundary = max(prefix.rfind(mark) for mark in "。！？；.!?;")
        if boundary >= max(20, item_length // 3):
            return prefix[: boundary + 1]
        return f"{prefix[: item_length - 1].rstrip('，、：；,;:-—')}…"

    cleaned = [compact_item(item) for item in cleaned]
    return "、".join(cleaned[:limit])


def _confirmed_context_summary(
    carried: dict[str, Any],
    limit: int = 4,
    stage: Stage | None = None,
) -> list[str]:
    """Use structured design facts, never raw conversational control phrases."""

    summaries: list[str] = []
    stage_summary = carried.get("stage_context_summary", {})
    confirmed = (
        stage_summary.get("confirmed", {})
        if isinstance(stage_summary, dict)
        else {}
    )
    confirmed = confirmed if isinstance(confirmed, dict) else {}
    field_values = {
        "research_object": confirmed.get("research_object")
        or carried.get("research_direction"),
        "course_relationship": confirmed.get("course_relationship")
        or carried.get("course_relationships"),
        "learning_objective": confirmed.get("learning_objective")
        or carried.get("learning_objective"),
        "research_question": confirmed.get("research_question")
        or carried.get("research_question"),
        "baseline_comparisons": confirmed.get("baseline_comparisons"),
        "independent_variable": confirmed.get("independent_variable")
        or carried.get("independent_variable"),
        "observations": confirmed.get("observations")
        or carried.get("observations"),
        "controlled_conditions": confirmed.get("controlled_conditions")
        or carried.get("controlled_conditions"),
        "hypothesis": confirmed.get("hypothesis") or carried.get("hypothesis"),
        "procedure_steps": confirmed.get("procedure_steps")
        or carried.get("procedure_steps"),
        "visualization_plan": confirmed.get("visualization_plan")
        or carried.get("visualization_plan"),
    }
    label_by_field = {
        "research_object": "研究方向",
        "course_relationship": "课程关系",
        "learning_objective": "学习目标",
        "research_question": "研究问题",
        "baseline_comparisons": "基础比较",
        "independent_variable": "主动改变量",
        "observations": "观察量",
        "controlled_conditions": "控制条件",
        "hypothesis": "预期趋势",
        "procedure_steps": "流程",
        "visualization_plan": "显示方式",
    }
    stage_order = {
        Stage.VARIABLES_AND_CONDITIONS: (
            "research_question",
            "baseline_comparisons",
            "hypothesis",
            "learning_objective",
        ),
        Stage.CONCEPTUAL_PROCEDURE: (
            "research_question",
            "independent_variable",
            "observations",
            "controlled_conditions",
        ),
        Stage.EXPECTED_DATA_VISUALIZATION: (
            "hypothesis",
            "independent_variable",
            "observations",
            "baseline_comparisons",
        ),
        Stage.RESULT_INTERPRETATION: (
            "hypothesis",
            "research_question",
            "visualization_plan",
            "controlled_conditions",
        ),
        Stage.DESIGN_VALUE_AND_LIMITATIONS: (
            "learning_objective",
            "research_question",
            "procedure_steps",
            "visualization_plan",
        ),
    }
    order = stage_order.get(
        stage,
        (
            "research_object",
            "course_relationship",
            "learning_objective",
            "research_question",
        ),
    )
    for field in order:
        label = label_by_field[field]
        value = field_values.get(field)
        compact = _compact_context_items(value)
        if compact:
            summaries.append(f"{label}：{compact}")
        if len(summaries) >= limit:
            break
    return summaries


def _contextual_reference_steps(
    stage: Stage,
    carried: dict[str, Any],
) -> list[str]:
    """Turn confirmed earlier decisions into a stage-specific reference draft."""

    variable = _compact_context_items(carried.get("independent_variable"))
    observations = _compact_context_items(carried.get("observations"))
    controls = _compact_context_items(carried.get("controlled_conditions"))
    stage_summary = carried.get("stage_context_summary", {})
    confirmed = (
        stage_summary.get("confirmed", {})
        if isinstance(stage_summary, dict)
        else {}
    )
    comparisons = _compact_context_items(
        confirmed.get("baseline_comparisons")
        if isinstance(confirmed, dict)
        else None
    )
    if stage is Stage.VARIABLES_AND_CONDITIONS:
        return [
            f"把{variable or '前面确定的变化主轴'}整理为主动改变的量",
            f"把{observations or '准备观察或比较的现象'}整理为观察量",
            f"把{controls or '其余会影响比较的条件'}整理为控制条件",
        ]
    if stage is Stage.CONCEPTUAL_PROCEDURE:
        return [
            f"建立基准状态；保持以下控制条件：{controls or '其余影响比较的条件'}",
            f"主动改变量的推进方式：{variable or '前面确定的变化主轴'}",
            f"每个状态下的记录内容：{observations or '目标现象'}",
            f"分别完成并比较：{comparisons or '已经保留的基础情形'}",
            "结合ECE329课上所学关系解释差异",
        ]
    return list(_GUIDED_STAGE_REFERENCE_STEPS.get(stage, ()))


def _contextual_stage_question(
    stage: Stage,
    carried: dict[str, Any],
) -> str:
    """Connect later-stage questions to decisions already made in Stage 1."""

    objective = str(carried.get("learning_objective") or "").strip()
    research_question = str(carried.get("research_question") or "").strip()
    hypothesis = str(carried.get("hypothesis") or "").strip()
    if stage is Stage.VARIABLES_AND_CONDITIONS:
        return (
            "上面的变量分工是否符合你的实验设想？如果有偏差，直接指出要调整的"
            "改变量、观察量或控制条件；如果合适，也可以按这份参考继续。"
        )
    if stage is Stage.CONCEPTUAL_PROCEDURE:
        return (
            "这套顺序是否能完成你想要的比较？如果不符合，请直接指出需要保留、"
            "删改或补充的环节。"
        )
    if stage is Stage.EXPECTED_DATA_VISUALIZATION:
        anchor = hypothesis or research_question
        return (
            f"前面你已经提出“{anchor}”。上面的显示参考就是为了看清这个变化。"
            "你觉得它能不能帮助你判断预期有没有出现？如果还不够，直接说最想补哪种画面；"
            "如果暂时拿不准，我也可以先搭一版。"
            if anchor
            else _GUIDED_STAGE_ENTRY_QUESTIONS[stage]
        )
    if stage is Stage.RESULT_INTERPRETATION:
        return (
            f"前面的预期是“{hypothesis}”。先把它当成检查结果的参照："
            "你觉得结果一致或不一致时，最应该保留或重新检查哪一部分解释？"
            "如果还没有判断，我可以先示范一种可能结果。"
            if hypothesis
            else _GUIDED_STAGE_ENTRY_QUESTIONS[stage]
        )
    if stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
        return (
            f"你前面希望做到“{objective}”，现有变量、流程和显示也都围绕这个目标组织起来了。"
            "现在只差看看它的边界：你觉得哪一种理想化条件最可能让结论不再成立？"
            "如果一时想不到，我可以先给一个贴合当前实验的参考。"
            if objective
            else _GUIDED_STAGE_ENTRY_QUESTIONS[stage]
        )
    return _GUIDED_STAGE_ENTRY_QUESTIONS.get(
        stage,
        "先说说你对这一部分的想法，我会在这个基础上继续帮你完善。",
    )


def guided_stage_entry_output(
    session: DesignSession,
    *,
    retry: bool = False,
) -> StepOutput:
    """Offer a contextual scaffold, then invite the student to revise it."""

    title = {
        Stage.VARIABLES_AND_CONDITIONS: "变量与条件",
        Stage.CONCEPTUAL_PROCEDURE: "概念实验流程",
        Stage.EXPECTED_DATA_VISUALIZATION: "预期数据可视化",
        Stage.RESULT_INTERPRETATION: "可能结果及解释",
        Stage.DESIGN_VALUE_AND_LIMITATIONS: "设计价值与局限",
        Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "学生总结",
    }.get(session.current_stage, "当前阶段")
    carried = build_carried_context(session)
    question = _contextual_stage_question(session.current_stage, carried)
    prior_context = _confirmed_context_summary(
        carried,
        stage=session.current_stage,
    )
    reference_steps = _contextual_reference_steps(
        session.current_stage,
        carried,
    )
    opening = (
        f"我们接着看“{title}”。刚才已经谈清楚的内容都还在，不用重新来一遍。"
        if retry
        else f"好，前面的实验想法已经保留下来了。我们接着看看“{title}”。"
    )
    if reference_steps:
        numbered = "\n".join(
            f"{index}. {step}" for index, step in enumerate(reference_steps, start=1)
        )
        basis_text = (
            f"我们已经有这些线索：{'；'.join(prior_context)}。\n"
            if prior_context
            else ""
        )
        reference_text = (
            "我先把已有线索顺成一份可以随手修改的参考：\n"
            f"{basis_text}"
            f"{numbered}\n\n"
            "觉得合适的部分可以留下；想改哪里，直接告诉我就行。"
        )
    else:
        reference_text = ""
    return StepOutput(
        assistant_message="\n\n".join(
            part for part in (opening, reference_text, question) if part
        ),
        stage_payload={
            "guided_entry": True,
            "awaiting_student_description": True,
            "preserved_idea_summary": _student_idea_summary(session),
            "reference_draft": reference_steps,
            "reference_basis": prior_context,
            "pending_action": {
                "type": "ANSWER_STAGE_QUESTION",
                "subject": session.current_stage.value,
                "proposal": {
                    "stage_title": title,
                    "reference_draft": reference_steps,
                },
                "question": question,
                "advance_on_accept": bool(
                    reference_steps
                    and session.current_stage is Stage.CONCEPTUAL_PROCEDURE
                ),
                "allowed_intents": [
                    "ANSWER_CURRENT_QUESTION",
                    "ACCEPT_PREVIOUS_PROPOSAL",
                    "MODIFY_PREVIOUS_PROPOSAL",
                    "REJECT_PREVIOUS_PROPOSAL",
                    "ADVANCE_STAGE",
                    "REQUEST_MORE_EXAMPLES",
                    "RETURN_TO_PREVIOUS_POINT",
                    "NEW_TOPIC",
                    "UNCLEAR",
                ],
            },
        },
        student_task=None,
    )


def _idea(session: DesignSession, user_message: str) -> str:
    if session.interaction_state is InteractionState.EMVR_DIRECT:
        emvr_design = session.design_context.get("emvr_design", {})
        if isinstance(emvr_design, dict):
            structured = merge_emvr_structured_requirements(emvr_design)
            current_brief = str(
                structured.get("experiment_brief")
                or emvr_design.get("experiment_brief")
                or emvr_design.get("current_brief")
                or emvr_design.get("brief")
                or ""
            ).strip()
            if current_brief:
                return current_brief
    idea_context = session.design_context.get("idea", {})
    if isinstance(idea_context, dict):
        for key in ("current_focus", "current_summary", "main_direction"):
            value = idea_context.get(key)
            if value:
                return str(value)
    resolved = session.turn_context.get("resolved_intent", {})
    resolved_name = str(resolved.get("intent") or "") if isinstance(resolved, dict) else ""
    if (
        session.current_stage is Stage.IDEA_BRAINSTORMING
        and user_message.strip()
        and resolved_name in {"ANSWER_CURRENT_QUESTION", "MODIFY_PREVIOUS_PROPOSAL", "NEW_TOPIC"}
    ):
        return user_message.strip()
    if isinstance(idea_context, dict) and idea_context.get("original"):
        return str(idea_context["original"])
    for item in reversed(session.history):
        if item.get("user_message"):
            return str(item["user_message"])
    return "尚未明确的ECE329实验想法"


def _formula_brief_object_inventory(
    session: DesignSession,
    requirements: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]]]:
    """Materialize the confirmed formula brief into concrete Unity roles.

    This is a deterministic projection of already confirmed semantic fields;
    it does not infer objects from words in the current turn.  It prevents the
    final Builder document from containing generic placeholders such as
    "student-defined object" after Stage 1 already named the actual objects.
    """

    emvr = session.design_context.get("emvr_design", {})
    brief = (
        emvr.get("authoritative_experiment_brief", {})
        if isinstance(emvr, dict)
        else {}
    )
    brief = brief if isinstance(brief, dict) else {}

    def items(value: Any) -> list[str]:
        if isinstance(value, list):
            return list(
                dict.fromkeys(str(item).strip() for item in value if str(item).strip())
            )
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    objects = items(brief.get("objects"))
    research_object = str(requirements.get("research_object") or "").strip()
    if research_object:
        objects = [research_object]
    operations = items(requirements.get("required_behaviors")) or items(
        brief.get("operations")
    )
    changed = items(requirements.get("changed_quantities")) or items(
        brief.get("changed_quantities")
    )
    observed = items(requirements.get("observed_quantities")) or items(
        brief.get("observed_quantities")
    )
    operation_text = "；".join(operations) or "按已确认的实验操作改变模型状态"
    changed_text = "、".join(changed) or "已确认的模型输入"
    observed_text = "、".join(observed) or "已确认的理论响应"

    inventory: list[dict[str, Any]] = []
    for name in objects:
        inventory.append(
            {
                "object_name": name,
                "category": "实验物理对象",
                "purpose": f"承载研究对象，并使学生能够改变{changed_text}",
                "student_interaction": operation_text,
                "physics_or_data_state": f"保存与{changed_text}有关的当前物理状态",
                "visual_feedback": f"对象状态变化后同步显示{observed_text}",
                "required": True,
            }
        )

    supporting_objects = [
        {
            "object_name": "XR Origin与左右控制器",
            "category": "VR交互基础",
            "purpose": "提供学生视角、射线选择、抓取和界面输入",
            "student_interaction": "移动、指向、选择、抓取或触发已确认的实验操作",
            "physics_or_data_state": "不直接参与电磁计算，只传递交互事件",
            "visual_feedback": "射线、选中状态和操作确认",
            "required": True,
        },
        {
            "object_name": "参数与状态控制面板",
            "category": "用户界面",
            "purpose": f"显示并控制{changed_text}，同时锁定其余条件",
            "student_interaction": "操作带单位控件、比较条件按钮和参考状态重置",
            "physics_or_data_state": "保存参数值、单位、允许范围、比较条件和参考状态",
            "visual_feedback": "显示当前值、范围、有效性和已选择的比较情形",
            "required": True,
        },
        {
            "object_name": "理论计算组件",
            "category": "计算与数据",
            "purpose": "依据已确认的主要公式和辅助公式计算当前实验状态",
            "student_interaction": "不直接操作；由对象或参数变化触发",
            "physics_or_data_state": f"读取{changed_text}并计算{observed_text}",
            "visual_feedback": "显示计算状态、模型边界和无效条件说明",
            "required": True,
        },
        {
            "object_name": "空间观察与测量工具",
            "category": "观察与测量",
            "purpose": f"在指定位置、路径或区域读取{observed_text}",
            "student_interaction": "移动观察工具、选择采样位置或切换已确认的观察方式",
            "physics_or_data_state": "保存采样位置、观察方式和当前理论读数",
            "visual_feedback": f"以数值、单位和空间标记呈现{observed_text}",
            "required": True,
        },
        {
            "object_name": "场量可视化系统",
            "category": "教学可视化",
            "purpose": f"把{observed_text}映射为空间图形、颜色、箭头或曲线",
            "student_interaction": "切换已确认的显示层和比较视图",
            "physics_or_data_state": "只读取理论计算输出，不独立生成物理结论",
            "visual_feedback": "同步更新空间表现，并标明理论计算与教学示意的区别",
            "required": True,
        },
        {
            "object_name": "结果记录与比较面板",
            "category": "实验控制与反馈",
            "purpose": "保存参考状态和各次操作结果，支持公平对照",
            "student_interaction": "记录、比较、撤销或重置实验状态",
            "physics_or_data_state": "保存参数快照、理论结果和比较顺序",
            "visual_feedback": "显示记录列表、差异摘要和重置确认",
            "required": True,
        },
    ]
    existing = {str(item.get("object_name") or "").casefold() for item in inventory}
    for item in supporting_objects:
        if str(item["object_name"]).casefold() not in existing:
            inventory.append(item)
    object_names = [str(item["object_name"]) for item in inventory]
    interactions = [
        {
            "user_action": operation_text,
            "physical_meaning": f"改变{changed_text}",
            "system_response": f"重新计算并更新{observed_text}",
        },
        {
            "user_action": "记录当前设置并与参考状态比较",
            "physical_meaning": "在其余条件保持一致时比较主要实验情形",
            "system_response": "保存参数与结果快照，并显示差异",
        },
    ]
    return object_names, inventory, interactions


def _topic_options(
    text: str,
    session: DesignSession | None = None,
    *,
    course_domain: str | None = None,
) -> list[dict[str, Any]]:
    shown = shown_exploration_option_ids(session.history) if session else set()
    seed_key = (
        f"{session.design_id}:{len(shown)}"
        if session
        else "catalog-default"
    )
    return KNOWLEDGE.brainstorm_options(
        text,
        exclude_option_ids=shown,
        seed_key=seed_key,
        course_domain=course_domain,
    )


def _course_topics(text: str) -> list[str]:
    matches = KNOWLEDGE.concept_references(text)
    if matches:
        return [item["title"] for item in matches]
    return [item["direction"] for item in KNOWLEDGE.broad_entry_points()]


def _course_references(text: str) -> list[dict[str, Any]]:
    return KNOWLEDGE.concept_references(text)


def _formula_references(text: str) -> list[dict[str, Any]]:
    return KNOWLEDGE.formula_references(text)


def _emvr_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "；".join(
            text
            for text in (_emvr_content_text(item) for item in value.values())
            if text
        )
    if isinstance(value, list):
        return "；".join(
            text
            for text in (_emvr_content_text(item) for item in value)
            if text
        )
    return str(value).strip() if value is not None else ""


def _emvr_stage_input_texts(session: DesignSession, stage: Stage) -> list[str]:
    emvr_design = session.design_context.get("emvr_design", {})
    stage_inputs = (
        emvr_design.get("stage_inputs", {})
        if isinstance(emvr_design, dict)
        else {}
    )
    entries = stage_inputs.get(stage.value, []) if isinstance(stage_inputs, dict) else []
    if not isinstance(entries, list):
        return []
    values: list[str] = []
    for entry in entries:
        content = entry.get("content") if isinstance(entry, dict) else entry
        text = _emvr_content_text(content)
        if text and text not in values:
            values.append(text)
    return values


def _emvr_latest_stage_input(session: DesignSession, stage: Stage) -> str:
    values = _emvr_stage_input_texts(session, stage)
    return values[-1] if values else ""


def _emvr_context_text(session: DesignSession, fallback: str) -> str:
    emvr_design = session.design_context.get("emvr_design", {})
    parts = []
    if isinstance(emvr_design, dict):
        brief = _emvr_content_text(
            merge_emvr_structured_requirements(emvr_design).get("experiment_brief")
            or emvr_design.get("experiment_brief")
            or emvr_design.get("current_brief")
            or emvr_design.get("brief")
        )
        if brief:
            parts.append(brief)
    for stage in Stage:
        parts.extend(_emvr_stage_input_texts(session, stage))
    if fallback.strip():
        parts.append(fallback.strip())
    return "\n".join(dict.fromkeys(parts))


def _emvr_structured_requirements(session: DesignSession) -> dict[str, Any]:
    """Return the latest complete semantic interpretation of the EMVR design."""

    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return {}
    return merge_emvr_structured_requirements(
        session.design_context.get("emvr_design", {})
    )


def _focused_emvr_topics(text: str) -> list[str]:
    matches = KNOWLEDGE.match_concepts(text, limit=6)
    if len(matches) > 1:
        specific = [item for item in matches if item.get("id") != "lecture_01"]
        if specific:
            matches = specific
    if matches:
        return [str(item["title"]) for item in matches]
    return _course_topics(text)


def _focused_emvr_formula_references(
    relation_ids: list[str] | tuple[str, ...],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Select EMVR formulas from structured physical roles, never raw wording."""

    return formulas_for_emvr_relations(relation_ids, limit=limit)


ILLUSTRATIVE_EXTENSION_SCOPE = "ILLUSTRATIVE_ONLY_NOT_COURSE_EVIDENCE"


def _clean_focus_text(value: Any) -> str:
    text = str(value or "").strip()
    for prefix in ("例如：", "例如:", "比如：", "比如:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text.rstrip("？?")


def _format_standard_comparison_status(
    comparisons: list[dict[str, Any]],
) -> str:
    summaries: list[str] = []
    for comparison in comparisons:
        status = str(comparison.get("adoption_status") or "PENDING").upper()
        cases = [
            str(case).strip()
            for case in comparison.get("cases", [])
            if str(case).strip()
        ]
        recommended_cases = [
            str(case).strip()
            for case in comparison.get("recommended_cases", cases)
            if str(case).strip()
        ]
        if status == "REJECTED":
            summaries.append("已按你的决定不采用这组默认对照。")
        elif status == "MODIFIED":
            summaries.append(
                f"按你的决定，基本情形只保留{'与'.join(cases)}。"
                if cases
                else "已按你的决定移除这组默认对照。"
            )
        elif status == "ACCEPTED":
            summaries.append(f"已采纳{'与'.join(cases)}作为一组基本对照。")
        elif recommended_cases:
            summaries.append(
                f"这组对照先作为建议保留：{'与'.join(recommended_cases)}。"
                "如果符合你的想法，可以直接沿用；想删掉或替换其中一种也可以直接说。"
            )
    return "".join(summaries)


def build_experiment_outline_seed(
    *,
    phenomenon: str,
    selected_course_relations: list[dict[str, Any]],
    standard_comparisons: list[dict[str, Any]],
    observation_focus: list[str],
) -> dict[str, Any]:
    """Build the Stage 1 draft without inventing later-stage design decisions."""

    relations = _course_relationships_for_selected_relations(
        selected_course_relations
    )
    comparisons = [
        {
            "comparison_id": str(item.get("comparison_id") or "").strip(),
            "cases": [str(case).strip() for case in item.get("cases", []) if str(case).strip()],
            "adoption_status": str(item.get("adoption_status") or "PENDING").upper(),
        }
        for item in standard_comparisons
        if isinstance(item, dict)
    ]
    return {
        "status": "DRAFT_TO_BE_REFINED",
        "core_phenomenon": phenomenon.strip(),
        "course_relationships": relations,
        "baseline_comparisons": comparisons,
        "observation_focus": [str(item).strip() for item in observation_focus if str(item).strip()][-3:],
        "next_refinement_points": [
            "课程映射说明",
            "学习目标",
            "研究问题",
            "理论依据",
            "假设与预期趋势",
            "概念实验结构",
        ],
    }


def _course_relationships_for_selected_relations(
    selected_course_relations: list[dict[str, Any]],
) -> list[str]:
    """Translate an internal scene selection into student-facing course links.

    A scene's display direction can be a broad lecture heading (for example,
    ``Divergence and curl``).  The formula-scene knowledge graph is more
    specific and already records which course relationship makes that scene
    useful.  Prefer that stable binding so selecting one scene cannot leave a
    different scene's course relationship in the canonical design state.
    """

    relationships: list[str] = []
    for relation in selected_course_relations:
        if not isinstance(relation, dict):
            continue
        focus = str(relation.get("focus") or "").strip().rstrip("？?")
        scene_id = str(relation.get("catalog_scene_id") or "").strip()
        linked = KNOWLEDGE.formula_links_for_scene(scene_id) if scene_id else None
        relation_domain = str(relation.get("course_block") or "").strip()
        linked_profiles = (
            linked.get("formula_design_profiles", [])
            if isinstance(linked, dict)
            else []
        )
        domain_profiles = [
            profile
            for profile in linked_profiles
            if isinstance(profile, dict)
            and (
                not relation_domain
                or str(profile.get("course_block") or "").strip()
                == relation_domain
            )
        ]
        profile_titles = [
            str(profile.get("title_zh") or "").strip()
            for profile in domain_profiles
            if isinstance(profile, dict) and str(profile.get("title_zh") or "").strip()
        ]
        if profile_titles:
            base = "、".join(dict.fromkeys(profile_titles))
            direction = str(relation.get("direction") or "").strip()
            course_heading = (
                f"（课程主题：{direction}）"
                if direction and direction not in base
                else ""
            )
            rendered_base = f"{base}{course_heading}"
            rendered = (
                f"{rendered_base}：{focus}"
                if focus and focus not in rendered_base
                else rendered_base
            )
        else:
            direction = str(
                relation.get("direction") or relation.get("focus") or ""
            ).strip()
            rendered = (
                f"{direction}：{focus}"
                if direction and focus and focus not in direction
                else direction or focus
            )
        if rendered and rendered not in relationships:
            relationships.append(rendered)
    return relationships


def _format_experiment_outline_seed(outline: dict[str, Any]) -> str:
    relations = outline.get("course_relationships", [])
    comparisons = outline.get("baseline_comparisons", [])
    observations = outline.get("observation_focus", [])
    status_labels = {
        "PENDING": "建议作为基础比较",
        "ACCEPTED": "已采用",
        "MODIFIED": "已按你的想法调整",
        "REJECTED": "已移除",
    }
    comparison_text = "；".join(
        f"{'、'.join(item.get('cases', []))}"
        f"（{status_labels.get(str(item.get('adoption_status', 'PENDING')).upper(), '待确认')}）"
        for item in comparisons
        if item.get("cases")
    ) or "暂未提出基础对照"
    lines = [
        "实验大纲雏形",
        f"研究对象：{outline.get('research_object') or outline.get('core_phenomenon') or '待补充'}",
        f"课程关系：{outline.get('course_relationship') or ('；'.join(relations) if relations else '将结合当前现象继续说明')}",
        f"学习目标：{outline.get('learning_objective') or '待补充'}",
        f"研究问题：{outline.get('research_question') or '待补充'}",
        f"基础比较：{comparison_text}",
        f"假设与预期现象：{outline.get('expected_phenomenon') or outline.get('hypothesis') or '待补充'}",
        f"概念实验结构：{outline.get('conceptual_structure') or '待补充'}",
        f"观察重点：{'；'.join(observations) if observations else '围绕核心现象继续细化'}",
    ]
    return "\n".join(lines)


def _scene_components(
    direction: str,
    index: int,
    *,
    excluded_signatures: set[str] | None = None,
) -> tuple[str, str, str, str]:
    return KNOWLEDGE.scene_components(
        direction,
        index,
        excluded_signatures=excluded_signatures,
    )


def build_exploration_scenes(
    options: list[dict[str, Any]],
    *,
    excluded_scene_signatures: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Turn catalog-grounded relationships into vivid but clearly scoped scenes."""

    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    scenes: list[dict[str, Any]] = []
    used_signatures: set[str] = set(excluded_scene_signatures or set())
    for index, option in enumerate(options):
        direction = str(option.get("direction") or "ECE329课程关系").strip()
        focus = _clean_focus_text(option.get("focus"))
        template = KNOWLEDGE.scene_template(
            f"{direction} {focus}",
            index,
            excluded_signatures=used_signatures,
        )
        title = str(template["title"])
        physical_frame = str(template["physical_picture"])
        thinking_prompt = str(template["thinking_prompt"])
        extension = str(template["illustrative_extension"])
        template_signature = str(template["template_signature"])
        used_signatures.add(template_signature)
        focus_sentence = (
            f"这个画面围绕“{direction}”展开，课程内可以追问：{focus}？"
            if focus
            else f"这个画面围绕“{direction}”展开。"
        )
        next_label = labels[(index + 1) % max(len(options), 1)]
        scenes.append(
            {
                "scene_id": f"scene_{labels[index].lower()}",
                "catalog_scene_id": option.get("catalog_scene_id"),
                "catalog_scene_number": option.get("catalog_scene_number"),
                "scene_template_id": template["template_id"],
                "scene_template_signature": template_signature,
                "label": f"图景 {labels[index]}",
                "title": title,
                "course_anchor": option,
                "physical_picture": f"{physical_frame}{focus_sentence}",
                "thinking_prompt": thinking_prompt,
                "combination_seed": (
                    f"你也可以把这个图景中的对象、材料或边界与图景 {next_label} 的"
                    "物理关系交换、叠加或重新组合。"
                ),
                "illustrative_extension": extension,
                "extension_scope": ILLUSTRATIVE_EXTENSION_SCOPE,
            }
        )
    return scenes


def _format_exploration_scenes(scenes: list[dict[str, Any]]) -> str:
    if not scenes:
        return ""
    blocks: list[str] = []
    for scene in scenes:
        anchor = scene["course_anchor"]
        blocks.append(
            f"{scene['label']}｜{scene['title']}\n"
            f"{scene['physical_picture']}\n"
            f"启发性延伸：{scene['illustrative_extension']}\n"
            f"可以继续想：{scene['thinking_prompt']}\n"
            f"组合提示：{scene['combination_seed']}"
        )
        if not str(anchor.get("direction") or "").strip():
            raise ValueError("Every exploration scene requires a course direction")
    invitation = (
        "如果这三个图景都没有引起你的兴趣，也没关系。"
        "我还可以从其他ECE329课程关系中再为你展示一组不同的图景。"
    )
    formatted_blocks = "\n\n".join(blocks)
    return f"{formatted_blocks}\n\n{invitation}"


def _visualization(
    idea: str,
    emvr: bool,
    *,
    formula_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "interactive_line_chart",
        "title": "ECE329理论预测参考窗口",
        "x_axis": {"label": "主要自变量", "unit": "由当前设计定义"},
        "y_axis": {"label": "主要因变量", "unit": "由当前设计定义"},
        "series": [
            {
                "id": "theory",
                "label": "理论预测",
                "points": [],
                "source": KNOWLEDGE.source_reference,
                # The visualization may expose only formulas selected through
                # the structured theory-binding contract. Topic retrieval is
                # a candidate source, not authority to attach adjacent theory.
                "formula_candidates": list(formula_candidates or []),
            }
        ],
        "controls": [
            {"type": "slider", "binds_to": "independent_variable"},
            {"type": "button", "action": "reset_to_reference"},
        ],
        "annotations": ["数值或曲线必须由当前报告中已经筛选的理论关系生成"],
        "unity_binding": (
            {
                "suggested_surface": "world-space data panel",
                "updates_from": "simulation parameter controller",
            }
            if emvr
            else None
        ),
        "idea_reference": idea,
        "data_type": "theoretical_prediction",
        "measured": False,
        "disclaimer": "该窗口表示理论预测，不是实际测量数据。",
    }


def _guided_reference_output(session: DesignSession) -> StepOutput:
    """Give a contextual example without treating the request as a decision."""

    stage = session.current_stage
    carried = build_carried_context(session)
    idea = _student_idea_summary(session)
    variable = _compact_context_items(carried.get("independent_variable"))
    observations = _compact_context_items(carried.get("observations"))
    controls = _compact_context_items(carried.get("controlled_conditions"))
    stage_summary = carried.get("stage_context_summary", {})
    confirmed = (
        stage_summary.get("confirmed", {})
        if isinstance(stage_summary, dict)
        else {}
    )
    comparisons = _compact_context_items(
        confirmed.get("baseline_comparisons")
        if isinstance(confirmed, dict)
        else None
    )
    objective = str(carried.get("learning_objective") or "").strip()
    follow_up = "你可以直接说哪里符合你的想法，或者指出一处想改的地方。"
    readiness = {
        "ready_for_confirmation": False,
        "remaining_gaps": ["student_review_of_reference"],
    }

    if stage is Stage.VARIABLES_AND_CONDITIONS:
        message = (
            "当然可以，我先按前面讨论的方向搭一个可修改的版本："
            f"把{variable or '最能体现研究变化的量'}作为主动改变量，"
            f"重点观察{observations or '与研究问题直接相关的场或响应'}，"
            f"并让{controls or '源、几何、材料和观察方式'}在各组比较中保持一致。"
            "这只是一个起点，你不需要从空白开始列变量表。"
        )
        payload = {
            "independent_variable": variable or "前面确定的变化主轴",
            "observations": [observations or "目标场或响应"],
            "controlled_variables": [controls or "其余影响比较的条件"],
            "reference_variable_roles": {
                "independent_variable": variable or "前面确定的变化主轴",
                "observations": observations or "目标场或响应",
                "controlled_conditions": controls or "其余影响比较的条件",
            },
            "stage_readiness": readiness,
        }
        visualization = None
    elif stage is Stage.CONCEPTUAL_PROCEDURE:
        steps = _contextual_reference_steps(stage, carried)
        message = (
            "可以。我先把前面的信息顺成一套可改的参考流程：\n"
            + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
            + "\n它不是标准答案，主要是让你更容易看出哪里需要增删或调整顺序。"
        )
        payload = {
            "procedure_steps": steps,
            "reference_procedure_steps": steps,
            "stage_readiness": readiness,
        }
        visualization = None
    elif stage is Stage.EXPECTED_DATA_VISUALIZATION:
        message = (
            "可以，我先给你一个能直接拿来改的理论参考："
            f"用{variable or '主要改变量'}作为横轴或交互控制量，"
            f"把{observations or '最关心的响应'}作为曲线、场图或通量显示；"
            f"{comparisons or '前面保留的基础情形'}可以并排呈现，方便看出差异。"
            "具体曲线形状还要服从前面确定的物理关系；这里展示的是理论预测，不是实测数据。"
        )
        payload = {
            "reference_prediction_view": {
                "control": variable or "主要改变量",
                "observations": observations or "目标响应",
                "comparisons": comparisons or "基础比较情形",
            },
            "stage_readiness": readiness,
        }
        visualization = _visualization(idea, emvr=False)
    elif stage is Stage.RESULT_INTERPRETATION:
        message = (
            "可以先用这套思路判断可能结果：如果观察到的变化与预测一致，"
            "就说明前面选定的课程关系能够解释主要趋势；如果趋势不明显或方向相反，"
            f"先检查{controls or '控制条件和理想化假设'}，再判断是否需要修改解释。"
            "这样既保留一种你可以参考的结果，也不会把未经验证的趋势说成事实。"
        )
        payload = {
            "result_case": "prediction_supported_or_needs_revision",
            "reference_result_cases": [
                "结果与理论趋势一致",
                "变化不明显或与理论趋势不同",
            ],
            "stage_readiness": readiness,
        }
        visualization = None
    elif stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
        message = (
            f"前面已经把学习目标说成“{objective or '解释当前实验中的核心物理关系'}”，"
            "这里不用再重复一遍。可以直接检查两点：现有比较和显示是否足以支撑这个目标；"
            "结论是否会受到理想化模型、有限显示方式，以及未纳入比较条件的限制。"
        )
        payload = {
            "review_dimension": "learning_value_and_model_limits",
            "limitations": ["理想化模型", "有限的显示方式", "未纳入比较的条件"],
            "reference_review_dimensions": ["课程理解价值", "模型与展示边界"],
            "stage_readiness": readiness,
        }
        visualization = None
    else:
        message = (
            "我可以帮你检查总结是否覆盖研究对象、课程关系、比较方式和预期现象，"
            "但最后的总结仍由你来写。你可以先写两三句话，我再帮你找遗漏。"
        )
        payload = {
            "summary_checklist": ["研究对象", "课程关系", "比较方式", "预期现象"],
            "final_proposal_generated": False,
            "stage_readiness": readiness,
        }
        visualization = None

    return StepOutput(
        assistant_message=message,
        stage_payload={
            **payload,
            "reference_only": True,
            "pending_action": {
                "type": "CONFIRM_OR_MODIFY",
                "subject": stage.value,
                "proposal": payload,
                "question": follow_up,
                "advance_on_accept": False,
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
            },
        },
        student_task=follow_up,
        visualization=visualization,
    )


class RuleBasedStageGenerator:
    """A safe fallback generator.

    Production deployments can replace it with an LLM adapter using the prompt
    packet from ``prompts.build_prompt_packet``. Stage transitions remain in the
    engine so a model cannot skip stages.
    """

    @staticmethod
    def runtime_info() -> dict[str, Any]:
        return {
            "provider": "rule_based",
            "model": None,
            "fallback_enabled": False,
        }

    def generate(self, session: DesignSession, user_message: str) -> StepOutput:
        if classify_stage_one_input(user_message) == UNREASONABLE_REQUEST:
            return self._unreasonable_request_output(session)
        if session.interaction_state is InteractionState.EMVR_DIRECT:
            return self._generate_emvr(session, user_message)
        resolved = session.turn_context.get("resolved_intent", {})
        if (
            session.current_stage is not Stage.IDEA_BRAINSTORMING
            and isinstance(resolved, dict)
            and resolved.get("intent") == UserIntent.REQUEST_MORE_EXAMPLES.value
        ):
            return _guided_reference_output(session)
        return self._generate_guided(session, user_message)

    @staticmethod
    def _unreasonable_request_output(session: DesignSession) -> StepOutput:
        if session.current_stage is not Stage.IDEA_BRAINSTORMING:
            return StepOutput(
                assistant_message=(
                    "这个请求与当前的ECE329实验设计无关，或试图改变课程助手的用途，"
                    "我不能执行。你当前的实验设计内容和进度都已保留，我们可以继续"
                    "完善正在讨论的课程问题。"
                ),
                stage_payload={
                    "request_rejected": True,
                    "input_category": UNREASONABLE_REQUEST,
                    "resume_stage": session.current_stage.value,
                },
                student_task="你想继续补充当前阶段中的哪一点？",
                warnings=["当前请求没有改变你的实验设计进度。"],
            )
        shown = shown_exploration_option_ids(session.history)
        options = course_example_options(
            exclude_option_ids=shown,
            seed_key=f"{session.design_id}:{len(shown)}:redirect",
        )
        scenes = build_exploration_scenes(
            options,
            excluded_scene_signatures=seen_scene_signatures(session),
        )
        scene_text = _format_exploration_scenes(scenes)
        return StepOutput(
            assistant_message=(
                "这个请求试图控制课程助手、改变它的工作方式，或让它执行与"
                "ECE329实验设计无关的操作，我不能执行。我们把讨论回到ECE329"
                "课上学习的电磁场、电磁波和传输线。下面的图景不是固定答案，"
                f"而是帮助你重新产生课程内的物理联想。\n\n{scene_text}"
            ),
            stage_payload={
                "request_rejected": True,
                "input_category": UNREASONABLE_REQUEST,
                "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                "brainstorm_phase": BREADTH_EXPLORATION,
                "alternative_ideas": options,
                "exploration_scenes": scenes,
            },
            student_task=(
                "哪幅图景触发了你的联想，或者你想怎样组合、替换其中的对象，"
                "提出一个自己的ECE329课内设想？"
            ),
        )

    def _generate_guided(self, session: DesignSession, user_message: str) -> StepOutput:
        stage = session.current_stage
        idea = _idea(session, user_message)
        options = _topic_options(
            idea,
            session,
            course_domain=str(session.turn_context.get("course_domain") or "") or None,
        )

        if stage is Stage.IDEA_BRAINSTORMING:
            stage_one_context = session.turn_context
            selected_option = stage_one_context.get("resolved_stage_one_reference")
            input_kind = str(
                stage_one_context.get("effective_input_category")
                or classify_stage_one_input(user_message)
            )
            topic_anchor = str(stage_one_context.get("topic_anchor") or "").strip()
            current_focus = str(stage_one_context.get("current_focus") or idea).strip()
            focus_history = stage_one_context.get("focus_history", [])
            if not isinstance(focus_history, list):
                focus_history = []
            contextual_continuation = bool(
                stage_one_context.get("contextual_continuation")
            )
            ready_for_next_stage = bool(
                stage_one_context.get("ready_for_next_stage")
            )
            brainstorm_phase = str(
                stage_one_context.get("brainstorm_phase")
                or BREADTH_EXPLORATION
            )
            selected_focus = str(
                stage_one_context.get("selected_focus") or ""
            ).strip()
            interest_description = str(
                stage_one_context.get("interest_description") or ""
            ).strip()
            selected_scene_ids = stage_one_context.get("selected_scene_ids", [])
            if not isinstance(selected_scene_ids, list):
                selected_scene_ids = []
            selected_course_relations = stage_one_context.get(
                "selected_course_relations",
                [],
            )
            if not isinstance(selected_course_relations, list):
                selected_course_relations = []
            selected_course_relations = [
                item for item in selected_course_relations if isinstance(item, dict)
            ]
            standard_comparisons = stage_one_context.get("standard_comparisons", [])
            if not isinstance(standard_comparisons, list):
                standard_comparisons = []
            standard_comparisons = [
                item for item in standard_comparisons if isinstance(item, dict)
            ]
            core_phenomenon = str(
                stage_one_context.get("core_phenomenon") or ""
            ).strip()
            refinement_notes = stage_one_context.get("refinement_notes", [])
            if not isinstance(refinement_notes, list):
                refinement_notes = []
            direction_summary = str(
                stage_one_context.get("direction_summary") or current_focus
            ).strip()
            relation_directions = [
                str(item.get("direction") or item.get("focus") or "").strip()
                for item in selected_course_relations
                if str(item.get("direction") or item.get("focus") or "").strip()
            ]
            relation_sentence = (
                f"组合关系完整保留为：{'；'.join(relation_directions)}。"
                if len(relation_directions) > 1
                else (
                    f"课程关系是：{relation_directions[0]}。"
                    if relation_directions
                    else ""
                )
            )
            comparison_sentence = _format_standard_comparison_status(
                standard_comparisons
            )
            phenomenon = core_phenomenon or interest_description or direction_summary
            experiment_outline_seed = (
                build_experiment_outline_seed(
                    phenomenon=phenomenon,
                    selected_course_relations=selected_course_relations,
                    standard_comparisons=standard_comparisons,
                    observation_focus=[*refinement_notes, interest_description],
                )
                if ready_for_next_stage and input_kind == COURSE_CONTENT
                else None
            )
            retrieval_text = " ".join(
                item
                for item in (
                    topic_anchor,
                    " ".join(str(value) for value in focus_history),
                    current_focus,
                )
                if item
            )
            if input_kind == COURSE_CONTENT and retrieval_text:
                idea = current_focus or topic_anchor or idea
                options = _topic_options(
                    retrieval_text,
                    session,
                    course_domain=str(stage_one_context.get("course_domain") or "") or None,
                )
            no_direction = stage_one_context.get("stage_one_no_direction") is True
            if input_kind != COURSE_CONTENT:
                shown = shown_exploration_option_ids(session.history)
                options = course_example_options(
                    exclude_option_ids=shown,
                    seed_key=f"{session.design_id}:{len(shown)}:redirect",
                )
                brainstorm_phase = BREADTH_EXPLORATION
            alternatives = (
                options
                if brainstorm_phase == BREADTH_EXPLORATION
                or input_kind != COURSE_CONTENT
                else []
            )
            exploration_scenes = (
                build_exploration_scenes(
                    alternatives,
                    excluded_scene_signatures=seen_scene_signatures(session),
                )
                if alternatives
                else []
            )
            deepening_connections = (
                options
                if input_kind == COURSE_CONTENT
                and brainstorm_phase == DEPTH_EXPANSION
                else []
            )
            scene_text = _format_exploration_scenes(exploration_scenes)
            if input_kind == UNREASONABLE_REQUEST:
                introduction = (
                    "这个请求试图控制课程助手、改变它的工作方式，或让它执行与"
                    "ECE329实验设计无关的操作，我不能执行。我们把讨论回到ECE329"
                    "课上学习的电磁场、电磁波和传输线。"
                )
            elif no_direction:
                introduction = (
                    f"{NO_DIRECTION_ACKNOWLEDGEMENT}我们可以先从ECE329课上学习的"
                    "电磁场、电磁波和传输线中寻找你感兴趣的关系。"
                )
            elif input_kind == OUT_OF_SCOPE:
                introduction = (
                    "你提出的主题不属于ECE329课程的内容范围，"
                    "因此不适合作为这门课实验设计的核心。"
                    "ECE329主要学习电磁场、电磁波和传输线，你可以先参考下面三个例子。"
                )
            elif (
                selected_option is not None
                and brainstorm_phase == INTEREST_DESCRIPTION
            ):
                selected_direction = str(
                    selected_option.get("direction")
                    or selected_option.get("focus")
                    or "上一轮所选方向"
                )
                introduction = (
                    f"你已经把方向收到了“{selected_direction}”。我先不继续给你新的"
                    "选项，因为同一个方向可能对应完全不同的兴趣。你可以"
                    "描述让你注意到它的现象、你觉得最值得解释的联系，或者目前仍感到"
                    "疑惑的地方；不需要写成正式的实验问题。"
                )
            elif brainstorm_phase == INTEREST_DESCRIPTION:
                if len(relation_directions) > 1:
                    introduction = (
                        f"你提出的组合已经按两条课程关系保留：{'；'.join(relation_directions)}。"
                        "接下来只需要说明你希望这两条关系共同解释什么核心现象；它们不会在"
                        "后续描述中被拆成二选一。"
                    )
                else:
                    introduction = (
                        f"现在把“{selected_focus or current_focus}”作为感兴趣的方向。"
                        "请用自己的话说明最想理解的现象或物理联系，不需要预先判断结果，"
                        "也不需要确定变量、公式或实验结构。"
                    )
            elif brainstorm_phase == DEPTH_EXPANSION:
                if ready_for_next_stage:
                    comparison_prefix = (
                        f"{comparison_sentence}\n\n" if comparison_sentence else ""
                    )
                    introduction = (
                        f"{comparison_prefix}"
                        f"{_format_experiment_outline_seed(experiment_outline_seed or {})}\n\n"
                        "接下来会一直沿着这个方向完善，不会再让你重新选题。"
                    )
                else:
                    introduction = (
                        f"当前核心现象是：{interest_description or user_message.strip()}。"
                        f"{relation_sentence}{comparison_sentence}"
                        "下一步只需确认这是否准确表达你真正想理解的物理联系。"
                    )
            elif contextual_continuation:
                previous_focus = str(
                    stage_one_context.get("previous_focus") or topic_anchor
                ).strip()
                introduction = (
                    f"我会把“{user_message.strip()}”理解为对前面“{previous_focus}”"
                    "这一实验方向的继续补充，而不是一个新的实验。我们把目前的想法"
                    f"保留为“{current_focus}”，再看看它还能和哪些ECE329课内现象建立联系。"
                )
            else:
                introduction = (
                    f"“{idea}”可以继续从不同的ECE329概念关系中展开。"
                    "现在先不确定变量、公式或实验结构，而是找出你真正感兴趣的物理联系。"
                )
            if brainstorm_phase == INTEREST_DESCRIPTION and input_kind == COURSE_CONTENT:
                closing_task = (
                    "请用自己的话描述这组关系共同要解释的核心现象。"
                    if len(relation_directions) > 1
                    else (
                        "请用自己的话描述：这个方向中什么现象或物理联系最吸引你，"
                        "以及你最希望进一步弄清什么？"
                    )
                )
            elif brainstorm_phase == DEPTH_EXPANSION and input_kind == COURSE_CONTENT:
                closing_task = (
                    "请检查这个大纲雏形是否准确；若有关键遗漏，请直接补充。"
                )
            else:
                closing_task = (
                    "哪幅图景触发了你的联想，或者你想怎样组合、替换其中的对象，"
                    "提出一个自己的ECE329课内设想？"
                )
            assistant_message = introduction
            if scene_text:
                assistant_message = (
                    f"{introduction}\n\n下面不是一组标准答案，而是几幅可以继续改造、"
                    f"交换或组合的物理图景：\n\n{scene_text}"
                )
            return StepOutput(
                assistant_message=assistant_message,
                stage_payload={
                    "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                    "brainstorm_phase": brainstorm_phase,
                    "input_category": input_kind,
                    "resolved_option_reference": selected_option,
                    "current_idea_summary": idea,
                    "topic_anchor": topic_anchor,
                    "current_focus": current_focus,
                    "focus_history": focus_history,
                    "contextual_continuation": contextual_continuation,
                    "selected_focus": selected_focus,
                    "selected_scene_ids": selected_scene_ids,
                    "selected_course_relations": selected_course_relations,
                    "combination_intent": len(selected_course_relations) > 1,
                    "core_phenomenon": core_phenomenon,
                    "refinement_notes": refinement_notes,
                    "standard_comparisons": standard_comparisons,
                    "direction_summary": direction_summary,
                    "interest_description": interest_description,
                    "direction_locked": bool(
                        stage_one_context.get("direction_locked")
                    ),
                    "stage_one_direction_detail": str(
                        stage_one_context.get("stage_one_direction_detail") or ""
                    ).strip()
                    or None,
                    "alternative_ideas": alternatives,
                    "exploration_scenes": exploration_scenes,
                    "deepening_connections": deepening_connections,
                    "course_source": KNOWLEDGE.source_reference,
                    "reference_sources": KNOWLEDGE.source_references,
                    "source_policy": KNOWLEDGE.supplemental_data["policy"][
                        "course_scope_rule"
                    ],
                    "ready_for_next_stage": ready_for_next_stage,
                    "experiment_outline_seed": experiment_outline_seed,
                },
                student_task=closing_task,
            )
        if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
            topics = _course_topics(idea)
            references = _course_references(idea)
            prior_output = session.stage_outputs.get(Stage.IDEA_BRAINSTORMING.value, {})
            prior_payload = prior_output.get("stage_payload", {}) if isinstance(prior_output, dict) else {}
            outline = prior_payload.get("experiment_outline_seed", {}) if isinstance(prior_payload, dict) else {}
            relationships = outline.get("course_relationships", []) if isinstance(outline, dict) else []
            primary_anchor: dict[str, Any] = (
                dict(references[0])
                if references
                else {"title": topics[0], "concepts": relationships}
            )
            supporting_anchors = [dict(item) for item in references[1:]]
            primary_title = str(primary_anchor.get("title") or topics[0])
            relationship_text = "；".join(str(item) for item in relationships if str(item))
            return StepOutput(
                assistant_message=(
                    "课程映射已经根据前面形成的实验大纲雏形整理如下：\n"
                    f"主要课程支点：{primary_title}\n"
                    f"已保留的物理关系：{relationship_text or idea}\n"
                    f"辅助课程联系：{'；'.join(str(item.get('title') or '') for item in supporting_anchors if item.get('title')) or '无需额外增加'}\n\n"
                    "这里是在解释已确定方向为什么属于ECE329，而不是重新选择实验方向。"
                ),
                stage_payload={
                    "primary_course_anchor": primary_anchor,
                    "supporting_course_anchors": supporting_anchors,
                    "mapped_relationships": list(relationships),
                    "mapping_explanation": "从核心现象和已保留的物理关系映射到课程概念。",
                    "course_references": references,
                    "idea_reference": idea,
                    "experiment_outline_seed": outline,
                },
                student_task="看看这段课程联系是否准确；没有遗漏的话，我们就继续完善学习目标。",
            )
        if stage is Stage.LEARNING_OBJECTIVES:
            return StepOutput(
                assistant_message="先确定学习目标的重点类型，不同时写完整目标列表。",
                stage_payload={"objective_types": ["概念理解", "定量计算", "结果解释"]},
                student_task="你最希望通过这个实验获得哪一种能力？",
            )
        if stage is Stage.RESEARCH_QUESTION:
            return StepOutput(
                assistant_message="研究问题需要先确定一个主要变化因素。",
                stage_payload={"candidate_independent_variables": [item["direction"] for item in options]},
                student_task="你希望实验主要改变哪一个因素？",
            )
        if stage is Stage.THEORETICAL_FRAMEWORK:
            formulas = _formula_references(idea)
            return StepOutput(
                assistant_message="理论框架先从一个核心课程关系开始。",
                stage_payload={
                    "candidate_topics": _course_topics(idea),
                    "lecture_formula_candidates": formulas,
                    "formula_source_required": True,
                },
                student_task="你认为哪一个ECE329公式或边界关系最直接连接自变量和观察量？",
            )
        if stage is Stage.HYPOTHESIS:
            return StepOutput(
                assistant_message="请先依据已经选定的理论关系判断趋势，不需要写完整实验结论。",
                stage_payload={"trend_choices": ["随自变量增大而增大", "随自变量增大而减小", "可能非单调"]},
                student_task="当主要自变量增大时，你预计因变量怎样变化，理由是什么？",
            )
        if stage is Stage.CONCEPTUAL_OR_VR_SETUP:
            return StepOutput(
                assistant_message="概念结构先确定实验中负责产生电磁现象的部分。",
                stage_payload={"module_focus": "excitation_or_source"},
                student_task="这个设计需要由什么对象或条件产生目标电磁场？",
            )
        if stage is Stage.VARIABLES_AND_CONDITIONS:
            return StepOutput(
                assistant_message="我先把你提到的量按“主动改变、重点观察、保持不变”整理一下，再补最关键的一处。",
                stage_payload={
                    "variable_type": "independent_variable",
                    "stage_readiness": {
                        "ready_for_confirmation": True,
                        "remaining_gaps": [],
                    },
                },
                student_task="这里最想主动改变的是哪个量？你打算让它怎样变化？",
            )
        if stage is Stage.CONCEPTUAL_PROCEDURE:
            return StepOutput(
                assistant_message="你给出的顺序已经能形成一条比较线索，我先帮你把基准、改变条件和观察结果接起来。",
                stage_payload={
                    "procedure_unit": "reference_condition",
                    "stage_readiness": {
                        "ready_for_confirmation": True,
                        "remaining_gaps": [],
                    },
                },
                student_task="按你的思路，哪一步最适合作为后面比较的起点？",
            )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            visualization = _visualization(idea, emvr=False)
            return StepOutput(
                assistant_message="我先按你的观察重点搭了一个理论预测窗口。它用来帮助比较趋势，不代表真实测量结果。",
                stage_payload={
                    "observation_focus": "relationship_shape",
                    "stage_readiness": {
                        "ready_for_confirmation": True,
                        "remaining_gaps": [],
                    },
                },
                student_task="结合前面的物理判断，你觉得图中最值得看清的是哪一种变化？",
                visualization=visualization,
            )
        if stage is Stage.RESULT_INTERPRETATION:
            return StepOutput(
                assistant_message="你的解释思路可以作为主线。我们再用一种不同结果检验一下它是否站得住。",
                stage_payload={
                    "result_case": "no_clear_change",
                    "stage_readiness": {
                        "ready_for_confirmation": True,
                        "remaining_gaps": [],
                    },
                },
                student_task="如果预期中的变化并不明显，你会先检查哪个条件或物理假设？",
            )
        if stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
            return StepOutput(
                assistant_message="你指出的价值和限制已经能形成一组对照，我们先把最影响结论的边界说清楚。",
                stage_payload={
                    "review_dimension": "model_limitation",
                    "stage_readiness": {
                        "ready_for_confirmation": True,
                        "remaining_gaps": [],
                    },
                },
                student_task="你的设计依赖的哪个理想化假设最可能限制结论？",
            )
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

    def _generate_emvr(self, session: DesignSession, user_message: str) -> StepOutput:
        stage = session.current_stage
        idea = _idea(session, user_message)
        design_text = _emvr_context_text(session, idea)
        topics = _focused_emvr_topics(design_text)
        stage_inputs = _emvr_stage_input_texts(session, stage)
        latest_stage_input = stage_inputs[-1] if stage_inputs else ""
        structured_requirements = _emvr_structured_requirements(session)
        theory_relation_ids = structured_requirements.get(
            "theory_relation_ids", []
        )
        if not isinstance(theory_relation_ids, list):
            theory_relation_ids = []

        if stage is Stage.IDEA_BRAINSTORMING:
            experiment_brief = str(
                structured_requirements.get("experiment_brief") or idea
            ).strip()
            saved_observations = structured_requirements.get(
                "observed_quantities", []
            )
            saved_observations = (
                [str(item).strip() for item in saved_observations if str(item).strip()]
                if isinstance(saved_observations, list)
                else []
            )
            saved_interactions = structured_requirements.get(
                "required_behaviors", []
            )
            saved_interactions = (
                [str(item).strip() for item in saved_interactions if str(item).strip()]
                if isinstance(saved_interactions, list)
                else []
            )
            return StepOutput(
                assistant_message="已将你的初步想法整理为Unity VR模拟实验的设计起点。",
                stage_payload={
                    "original_idea": experiment_brief,
                    "normalized_idea": experiment_brief,
                    "research_object": structured_requirements.get("research_object"),
                    "target_phenomenon": (
                        "；".join(saved_observations)
                        if saved_observations
                        else None
                    ),
                    "possible_vr_interactions": saved_interactions,
                    "design_scope": "概念设计与Unity VR模拟规划，不包含真实实验实施",
                    "course_references": _course_references(idea),
                    "supplemental_references": KNOWLEDGE.supplemental_concept_references(
                        idea,
                        limit=3,
                    ),
                },
                assumptions=["暂时以你提出的想法为设计边界，接下来再补充参数和理论模型。"],
            )
        if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
            selected_direction = (
                structured_requirements.get("experiment_brief")
                or structured_requirements.get("direction_summary")
                or structured_requirements.get("research_summary")
                or latest_stage_input
                or f"围绕“{idea}”比较学生主动改变条件前后的空间电磁分布"
            )
            course_relationship = str(
                structured_requirements.get("course_relationship") or ""
            ).strip() or (
                f"以{topics[0]}中的课程关系解释已确认变化量与观察量之间的联系"
            )
            return StepOutput(
                assistant_message="已选择兼顾ECE329相关性、理论可解释性和VR交互价值的实验方向。",
                stage_payload={
                    "lab_title": structured_requirements.get("lab_title"),
                    "lab_id": structured_requirements.get("lab_id"),
                    "primary_topic": topics[0],
                    "secondary_topics": topics[1:],
                    "selected_direction": selected_direction,
                    "course_relationship": course_relationship,
                    "course_references": _course_references(design_text),
                    "vr_suitability": "参数可调、结果可计算、现象可空间化展示",
                    "selection_reason": "优先保留你原本的研究意图，并选择能够形成明确输入—输出反馈的方向。",
                },
            )
        if stage is Stage.LEARNING_OBJECTIVES:
            saved_objectives = structured_requirements.get("learning_objectives", [])
            saved_objectives = saved_objectives if isinstance(saved_objectives, list) else []
            conceptual_objective = (
                structured_requirements.get("conceptual_objective")
                or (saved_objectives[0] if saved_objectives else "")
                or latest_stage_input
                or f"解释{topics[0]}中的核心物理机制"
            )
            return StepOutput(
                assistant_message="已将课程学习与VR操作组织为一致的学习目标。",
                stage_payload={
                    "conceptual_objective": conceptual_objective,
                    "calculation_objective": structured_requirements.get(
                        "calculation_objective"
                    )
                    or "依据与当前研究问题直接相关的ECE329关系式作出理论预测",
                    "analysis_objective": structured_requirements.get(
                        "analysis_objective"
                    )
                    or "比较学生定义的条件变化、理论输出和空间电磁分布",
                    "vr_interaction_objective": structured_requirements.get(
                        "vr_interaction_objective"
                    )
                    or "通过学生定义的VR操作改变具有明确物理意义的模型输入",
                    "observation_objective": structured_requirements.get(
                        "observation_objective"
                    )
                    or "从数值和空间表现中判断预期关系是否成立",
                },
            )
        if stage is Stage.RESEARCH_QUESTION:
            # A raw turn may contain several edit instructions.  The semantic
            # field state is authoritative, so the same sentence cannot be
            # copied into the question, controls and observations at once.
            research_focus = (
                structured_requirements.get("research_question")
                or structured_requirements.get("research_summary")
                or latest_stage_input
                or idea
            )
            changed_quantities = structured_requirements.get("changed_quantities", [])
            observed_quantities = structured_requirements.get("observed_quantities", [])
            return StepOutput(
                assistant_message="已形成一个可在Unity中调整参数并观察理论输出的研究问题。",
                stage_payload={
                    "main_research_question": research_focus,
                    "adjustable_quantity_in_vr": changed_quantities
                    or [f"按学生定义的变化条件进行调整：{research_focus}"],
                    "observable_quantity_in_vr": observed_quantities
                    or ["研究问题中指定的理论量、场线形态和空间分布"],
                    "comparison_cases": structured_requirements.get(
                        "comparison_cases", []
                    ),
                    "question_boundary": topics[0],
                },
                assumptions=["后续变量设计只负责给已有研究问题补充单位、范围和控制条件，不改写问题。"],
            )
        if stage is Stage.THEORETICAL_FRAMEWORK:
            research_focus = (
                structured_requirements.get("research_question")
                or _emvr_latest_stage_input(session, Stage.RESEARCH_QUESTION)
            )
            emvr_design = session.design_context.get("emvr_design", {})
            selected_formula_ids = (
                [
                    *emvr_design.get("selected_primary_formula_ids", []),
                    *emvr_design.get("selected_supporting_formula_ids", []),
                ]
                if isinstance(emvr_design, dict)
                else []
            )
            formula_by_id = {
                str(formula.get("id") or ""): formula
                for formula in KNOWLEDGE.formulas
                if isinstance(formula, dict)
            }
            formulas = [
                dict(formula_by_id[formula_id])
                for formula_id in dict.fromkeys(str(item) for item in selected_formula_ids)
                if formula_id in formula_by_id
            ] or _focused_emvr_formula_references(theory_relation_ids)
            theory_selection_status = (
                "selected_for_current_research"
                if formulas
                else "needs_semantic_theory_confirmation"
            )
            relation_labels = [
                EMVR_THEORY_RELATIONS[relation_id]["label"]
                for relation_id in theory_relation_ids
                if relation_id in EMVR_THEORY_RELATIONS
            ]
            if formulas and not relation_labels:
                relation_labels = [
                    str(profile.get("title_zh") or "")
                    for profile_id in (
                        emvr_design.get("formula_flow", {})
                        .get("formula_selection", {})
                        .get("primary_profile_ids", [])
                        if isinstance(emvr_design, dict)
                        and isinstance(emvr_design.get("formula_flow"), dict)
                        else []
                    )
                    for profile in KNOWLEDGE.public_formula_design_profiles()
                    if profile.get("profile_id") == profile_id
                ]
            support_map = emvr_formula_support_map(
                theory_relation_ids,
                structured_requirements,
            ) or formula_support_map_for_selection(session)
            return StepOutput(
                assistant_message=(
                    "我已经只保留能直接解释当前变化条件和观察现象的课程关系。"
                    if formulas
                    else "目前还不能从已确认内容中判断哪些公式会真正参与这个实验；我先不替你堆叠无关公式。"
                ),
                stage_payload={
                    "physical_mechanism": relation_labels,
                    "core_equations": formulas,
                    "formula_support_map": support_map,
                    "theory_selection_status": theory_selection_status,
                    "simulation_inputs": [
                        *structured_requirements.get("changed_quantities", []),
                        "其余保持不变的控制条件",
                        "用于比较的基准状态",
                    ],
                    "calculated_outputs": structured_requirements.get(
                        "observed_quantities", []
                    ) or ["研究问题中指定的电磁响应"],
                    "visual_only_elements": ["方向箭头", "波前或场线动画", "颜色强度映射"],
                    "model_type": "课程层面的解析模型或预计算数据",
                    "research_question_preserved": research_focus,
                },
                warnings=["视觉动画必须标明是计算映射还是教学示意。"],
            )
        if stage is Stage.HYPOTHESIS:
            hypothesis = (
                structured_requirements.get("hypothesis")
                or latest_stage_input
                or "学生尚未给出具体方向性假设"
            )
            changed_text = "、".join(
                str(item).strip()
                for item in structured_requirements.get("changed_quantities", [])
                if str(item).strip()
            ) or "主要参数"
            observed_text = "、".join(
                str(item).strip()
                for item in structured_requirements.get("observed_quantities", [])
                if str(item).strip()
            ) or "目标响应"
            return StepOutput(
                assistant_message="这个假设已经对应到你调整参数后能够立即观察的VR反馈。",
                stage_payload={
                    "research_hypothesis": hypothesis,
                    "null_hypothesis": "在设计范围内，主要自变量变化不会造成可分辨响应。",
                    "expected_trend": (
                        f"当{changed_text}变化时，{observed_text}应呈现假设所述的方向性响应；"
                        "具体方向以已确认假设为准。"
                    ),
                    "limiting_cases": ["基准条件", "参数下限", "参数上限或模型失效边界"],
                    "vr_feedback_for_trend": ["数值更新", "曲线更新", "空间视觉编码更新"],
                },
            )
        if stage is Stage.CONCEPTUAL_OR_VR_SETUP:
            research_focus = (
                structured_requirements.get("research_question")
                or _emvr_latest_stage_input(session, Stage.RESEARCH_QUESTION)
            )
            unity_objects, object_inventory, interactions = (
                _formula_brief_object_inventory(session, structured_requirements)
            )
            return StepOutput(
                assistant_message="你原有的场景条件已经保留；我在此基础上补全了Unity VR模拟实验的对象、交互、物理计算和反馈设计。",
                stage_payload={
                    "desktop_interaction_plan": structured_requirements.get(
                        "desktop_interaction_plan"
                    ),
                    "room_spatial_requirements": structured_requirements.get(
                        "room_spatial_requirements"
                    ),
                    "hidden_object_lifecycle": structured_requirements.get(
                        "hidden_object_lifecycle"
                    ),
                    "user_original_design": idea,
                    "existing_context": "保留你已有的场景设定；这一部分不额外改写VR场景。",
                    "student_constraints": stage_inputs,
                    "user_role": latest_stage_input or "通过有物理意义的交互调整参数、观察结果并进行比较",
                    "core_learning_task": research_focus or f"探索学生定义的条件变化与{topics[0]}响应之间的关系",
                    "unity_objects": unity_objects,
                    "object_inventory": object_inventory,
                    "interactions": interactions,
                    "physics_layer": {
                        "user_inputs": structured_requirements.get(
                            "changed_quantities", []
                        ) or [research_focus or "学生定义的变化条件"],
                        "calculated_outputs": structured_requirements.get(
                            "observed_quantities", []
                        ) or ["研究问题指定的理论量"],
                        "model_type": "根据当前交互状态重新计算的课程理论模型",
                        "real_time_updates": ["数值", "曲线", "场表现"],
                        "update_policy": "对象或参数改变后重新计算并刷新显示，不把预设动画或固定序列当作实验结果",
                        "parameter_limits": ["限制在理论模型适用范围内"],
                        "invalid_conditions": ["参数超界时停止计算并解释原因"],
                    },
                    "visualization_layer": [
                        {"visual_element": "箭头或曲线", "physical_quantity": "当前对象状态对应的矢量场方向或传播方向", "calculated_or_illustrative": "由当前理论输出实时映射"},
                        {"visual_element": "颜色或透明度", "physical_quantity": "归一化强度或衰减", "calculated_or_illustrative": "由理论输出映射"},
                    ],
                    "measurement_interface": ["当前参数及单位", "理论输出", "比较曲线", "模型假设提示", "记录与重置状态"],
                    "internal_experiment_states": ["INTRO", "BASELINE", "PARAMETER_ADJUSTMENT", "OBSERVATION", "DATA_RECORDING", "COMPARISON", "REFLECTION", "COMPLETE"],
                    "design_improvements": [
                        "确保每个交互都有物理意义",
                        "学生最新修改优先于通用对象模板",
                        "分开实时理论输出与教学示意动画",
                        "保留参数基准和重置闭环",
                    ],
                },
                warnings=["这一部分只整理实验结构，不另外定义VR场景，也不扩展可访问性与舒适性设计。"],
            )
        if stage is Stage.VARIABLES_AND_CONDITIONS:
            saved_changed = structured_requirements.get("changed_quantities", [])
            saved_changed = saved_changed if isinstance(saved_changed, list) else []
            saved_observed = structured_requirements.get("observed_quantities", [])
            saved_observed = saved_observed if isinstance(saved_observed, list) else []
            variable_definition = (
                "；".join(saved_changed)
                or latest_stage_input
                or structured_requirements.get("research_question")
                or _emvr_latest_stage_input(session, Stage.RESEARCH_QUESTION)
            )
            return StepOutput(
                assistant_message="已把实验变量映射到Unity控制、显示和模型约束。",
                stage_payload={
                    "parameter_specifications": structured_requirements.get(
                        "parameter_specifications", []
                    ),
                    "student_variable_definition": variable_definition,
                    "independent_variable": {"name": variable_definition or "学生定义的主要变化条件", "unity_control": "与VR对象操作或带单位控件绑定", "range": "；".join(structured_requirements.get("parameter_specifications", [])) or "需要明确范围与单位"},
                    "dependent_variable": {"name": "；".join(saved_observed) or "研究问题中指定的观察响应", "vr_representation": "数值、曲线和空间编码"},
                    "controlled_variables": ["源条件", "几何条件", "材料或边界中未被选为自变量的参数"],
                    "reference_condition": {"purpose": "建立比较基线", "unity_action": "Reset/Reference preset"},
                    "confounding_factors": ["视觉缩放与真实单位混淆", "多个参数同时变化", "超出模型范围"],
                },
            )
        if stage is Stage.CONCEPTUAL_PROCEDURE:
            saved_steps = structured_requirements.get("procedure_steps", [])
            saved_steps = saved_steps if isinstance(saved_steps, list) else []
            reference_steps = [
                "进入VR实验并阅读本次学习目标、研究问题与模型适用范围",
                "检查实验对象、源、探测器和显示面板的初始状态",
                "加载参考条件并记录基准数值、曲线与空间场表现",
                "只调整当前研究问题规定的一个主要参数",
                "等待理论计算与空间可视化同步更新",
                "在固定观察方式下读取数值、曲线和空间现象",
                "保存当前参数与结果快照，并恢复或切换到下一比较条件",
                "完成全部保留情形后并列比较结果",
                "依据ECE329理论关系解释趋势并检查异常或无效条件",
                "回到学习目标完成反思，确认哪些结论受模型假设限制",
            ]
            return StepOutput(
                assistant_message="已将实验逻辑整理为单一、可重复的VR学习闭环。",
                stage_payload={
                    "procedure_type": "conceptual_vr_flow",
                    "student_required_steps": stage_inputs,
                    # A short student paragraph is retained in
                    # ``student_required_steps`` but cannot replace the full
                    # ordered Builder flow.  Five or more explicit semantic
                    # steps are considered a complete student-authored list.
                    "procedure_steps": (
                        saved_steps if len(saved_steps) >= 5 else reference_steps
                    ),
                    "comparison_logic": "每次只改变主要自变量，其余条件保持锁定。",
                    "derived_quantities": ["由当前报告中已筛选的理论关系定义的派生量"],
                },
            )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            output = StepOutput(
                assistant_message="已生成理论预测窗口规范，并给出与Unity参数控制器联动的接口。",
                stage_payload={
                    "student_visualization_requirements": stage_inputs,
                    "trend_annotation": "由当前理论模型计算后标注",
                    "unity_update_event": "OnSimulationParameterChanged",
                },
                visualization=_visualization(
                    idea,
                    emvr=True,
                    formula_candidates=formulas_for_emvr_relations(
                        structured_requirements.get("theory_relation_ids", [])
                    ),
                ),
            )
            return output
        if stage is Stage.RESULT_INTERPRETATION:
            return StepOutput(
                assistant_message="已为不同结果情形设计物理解释和教学反馈。",
                stage_payload={
                    "expected_results": structured_requirements.get(
                        "expected_results", []
                    ),
                    "acceptance_criteria": structured_requirements.get(
                        "acceptance_criteria", []
                    ),
                    "report_questions": structured_requirements.get(
                        "report_questions", []
                    ),
                    "if_prediction_supported": "提示理论关系与当前参数条件的一致性。",
                    "if_opposite_trend": "检查符号、边界条件、变量映射和可视化方向。",
                    "if_no_clear_change": "检查参数范围、归一化尺度和模型灵敏度。",
                    "inconclusive_conditions": ["参数超出模型范围", "多个输入同时变化", "显示尺度掩盖变化"],
                    "alternative_explanations": ["理想化假设不适用", "视觉编码与物理量映射错误"],
                },
            )
        if stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
            return StepOutput(
                assistant_message="已从课程、模型、教学和VR附加价值四个方面评价设计。",
                stage_payload={
                    "conceptual_feasibility": {
                        "rating": "可行",
                        "reasoning": "前序阶段已明确参数范围、观察量、控制条件和实验流程，可按课程模型进行实现与验收。",
                    },
                    "limitations": ["理想化边界条件", "忽略部分损耗或边缘效应", "视觉缩放不等于真实尺度", "VR结果来自模型而非实测"],
                    "teaching_value": {"rating": "high_if_aligned", "learning_contribution": "让不可见场量、空间分布和参数关系可观察"},
                    "innovation": {"rating": "context_dependent", "innovative_elements": ["空间探测", "实时参数—理论反馈", "多条件叠加比较"]},
                    "vr_added_value": {"rating": "high_if_spatial", "reasoning": "只有空间观察和交互对理解有贡献时才值得使用VR"},
                    "recommended_improvements": ["删除无物理意义的交互", "优先保留一个清晰的参数—响应闭环"],
                    "student_value_and_limit_notes": stage_inputs,
                },
            )
        return StepOutput(
            assistant_message="已汇总为面向Unity VR模拟实验的最终设计结构。",
            stage_payload={
                "proposal_status": "complete",
                "proposal_sections": [
                    "实验标题与课程联系", "学习目标", "研究问题", "理论框架", "假设与趋势",
                    "用户任务与Unity对象", "交互及物理意义", "变量和参数控制", "物理计算层",
                    "电磁现象可视化", "测量与反馈界面", "模拟实验流程", "预期数据窗口",
                    "结果解释", "教学价值与模型局限", "VR附加价值与扩展",
                ],
                "source_stage_outputs": list(session.stage_outputs.keys()),
                "final_design": {"idea": idea, "course_topic": topics[0], "stage_outputs": session.stage_outputs},
                "builder_pack_handoff": {
                    "purpose": "供EMVR_Blind_BuilderPack的Brief与Design阶段人工审阅，不自动启动或批准任何Gate。",
                    "lab_identity": {
                        "title": structured_requirements.get("lab_title"),
                        "lab_id": structured_requirements.get("lab_id"),
                        "domain": topics[0],
                    },
                    "learning_goals": build_carried_context(session).get("learning_objectives", []),
                    "core_student_flow": build_carried_context(session).get("procedure_steps", []),
                    "physics_and_presets": _focused_emvr_formula_references(
                        theory_relation_ids
                    ),
                    "objects_and_feedback": build_carried_context(session).get("unity_objects", []),
                    "desktop_xr_interaction_meaning": structured_requirements.get(
                        "desktop_interaction_plan"
                    ),
                    "room_spatial_requirements": structured_requirements.get(
                        "room_spatial_requirements"
                    ),
                    "initial_and_post_action_states": structured_requirements.get(
                        "hidden_object_lifecycle"
                    ),
                    "parameter_specifications": structured_requirements.get(
                        "parameter_specifications", []
                    ),
                    "acceptance_criteria": structured_requirements.get(
                        "acceptance_criteria", []
                    ),
                    "report_questions": structured_requirements.get(
                        "report_questions", []
                    ),
                    "unresolved_builder_inputs": [],
                },
                "course_knowledge_source": KNOWLEDGE.source_reference,
            },
        )
