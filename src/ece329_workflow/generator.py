from __future__ import annotations

import re
from typing import Any, Protocol

from .dialogue_state import UserIntent, build_carried_context
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
        "对照阶段1的学习目标，检查现有变量、流程和显示是否足以支撑它",
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
    cleaned = [
        item if len(item) <= item_length else f"{item[: item_length - 1]}…"
        for item in cleaned
    ]
    return "、".join(cleaned[:limit])


def _confirmed_context_summary(carried: dict[str, Any], limit: int = 3) -> list[str]:
    """Use structured design facts, never raw conversational control phrases."""

    summaries: list[str] = []
    labels = (
        ("研究方向", carried.get("research_direction")),
        ("主动改变量", carried.get("independent_variable")),
        ("观察量", carried.get("observations")),
        ("控制条件", carried.get("controlled_conditions")),
        ("流程", carried.get("procedure_steps")),
    )
    for label, value in labels:
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
    comparisons = _compact_context_items(carried.get("baseline_comparisons"))
    if stage is Stage.VARIABLES_AND_CONDITIONS:
        return [
            f"把{variable or '前面确定的变化主轴'}整理为主动改变的量",
            f"把{observations or '准备观察或比较的现象'}整理为观察量",
            f"把{controls or '其余会影响比较的条件'}整理为控制条件",
        ]
    if stage is Stage.CONCEPTUAL_PROCEDURE:
        return [
            f"建立基准状态{f'（保持：{controls}）' if controls else ''}",
            f"逐步改变{variable or '前面确定的变化主轴'}",
            f"用一致方式记录{observations or '目标现象'}",
            f"完成{comparisons or '已经保留的基础情形'}并排比较",
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
    prior_context = _confirmed_context_summary(carried)
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


def _topic_options(
    text: str,
    session: DesignSession | None = None,
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

    relations = [
        str(item.get("direction") or item.get("focus") or "").strip()
        for item in selected_course_relations
        if str(item.get("direction") or item.get("focus") or "").strip()
    ]
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
    return (
        "实验大纲雏形\n"
        f"核心现象：{outline.get('core_phenomenon') or '待补充'}\n"
        f"课程关系：{'；'.join(relations) if relations else '将结合当前现象继续说明'}\n"
        f"基础比较：{comparison_text}\n"
        f"观察重点：{'；'.join(observations) if observations else '围绕核心现象继续细化'}"
    )


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
) -> list[dict[str, Any]]:
    """Turn catalog-grounded relationships into vivid but clearly scoped scenes."""

    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    scenes: list[dict[str, Any]] = []
    used_signatures: set[str] = set()
    for index, option in enumerate(options):
        direction = str(option.get("direction") or "ECE329课程关系").strip()
        focus = _clean_focus_text(option.get("focus"))
        title, physical_frame, thinking_prompt, extension = _scene_components(
            f"{direction} {focus}",
            index,
            excluded_signatures=used_signatures,
        )
        used_signatures.add(
            "|".join(
                " ".join(item.split()).casefold()
                for item in (title, physical_frame, thinking_prompt)
            )
        )
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


def _visualization(idea: str, emvr: bool) -> dict[str, Any]:
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
                "formula_candidates": _formula_references(idea),
            }
        ],
        "controls": [
            {"type": "slider", "binds_to": "independent_variable"},
            {"type": "button", "action": "reset_to_reference"},
        ],
        "annotations": ["需要由阶段5的理论关系生成数值或曲线"],
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
    comparisons = _compact_context_items(carried.get("baseline_comparisons"))
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
        scenes = build_exploration_scenes(options)
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
        options = _topic_options(idea, session)

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
                options = _topic_options(retrieval_text, session)
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
                build_exploration_scenes(alternatives)
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
        topics = _course_topics(idea)

        if stage is Stage.IDEA_BRAINSTORMING:
            return StepOutput(
                assistant_message="已将用户的初步想法整理为适合继续发展为Unity VR模拟实验的设计起点。",
                stage_payload={
                    "original_idea": idea,
                    "normalized_idea": f"围绕“{idea}”设计ECE329交互式模拟实验",
                    "target_phenomenon": topics[0],
                    "possible_vr_interactions": [item["focus"] for item in _topic_options(idea)],
                    "design_scope": "概念设计与Unity VR模拟规划，不包含真实实验实施",
                    "course_references": _course_references(idea),
                    "supplemental_references": KNOWLEDGE.supplemental_concept_references(
                        idea,
                        limit=3,
                    ),
                },
                assumptions=["暂以用户提供的想法为设计边界，后续阶段再补充参数和理论模型。"],
            )
        if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
            return StepOutput(
                assistant_message="已选择兼顾ECE329相关性、理论可解释性和VR交互价值的实验方向。",
                stage_payload={
                    "primary_topic": topics[0],
                    "secondary_topics": topics[1:],
                    "selected_direction": _topic_options(idea)[0],
                    "course_references": _course_references(idea),
                    "vr_suitability": "参数可调、结果可计算、现象可空间化展示",
                    "selection_reason": "优先保留用户原始意图，并选择能够形成明确输入—输出反馈的方向。",
                },
            )
        if stage is Stage.LEARNING_OBJECTIVES:
            return StepOutput(
                assistant_message="已将课程学习与VR操作组织为一致的学习目标。",
                stage_payload={
                    "conceptual_objective": f"解释{topics[0]}中的核心物理机制",
                    "calculation_objective": "使用ECE329关系式预测参数变化造成的响应",
                    "analysis_objective": "比较参数设置、理论输出和空间可视化",
                    "vr_interaction_objective": "通过有物理意义的操作改变模型输入",
                    "observation_objective": "从数值和空间表现中解释趋势",
                },
            )
        if stage is Stage.RESEARCH_QUESTION:
            return StepOutput(
                assistant_message="已形成一个可在Unity中调整参数并观察理论输出的研究问题。",
                stage_payload={
                    "main_research_question": "在其余条件固定时，主要电磁参数的变化如何影响目标场量或传播响应？",
                    "adjustable_quantity_in_vr": "由阶段8确定的主要自变量",
                    "observable_quantity_in_vr": "理论数值、曲线和空间场表现",
                    "question_boundary": topics[0],
                },
                assumptions=["具体变量名称将在变量阶段依据用户设计固定。"],
            )
        if stage is Stage.THEORETICAL_FRAMEWORK:
            formulas = _formula_references(idea)
            return StepOutput(
                assistant_message="已把物理计算与教学可视化分开，避免把动画误认为精确数值模拟。",
                stage_payload={
                    "physical_mechanism": topics[0],
                    "core_equations": formulas,
                    "simulation_inputs": ["主要自变量", "控制参数", "基准条件"],
                    "calculated_outputs": ["目标场量或无量纲响应", "理论趋势曲线"],
                    "visual_only_elements": ["方向箭头", "波前或场线动画", "颜色强度映射"],
                    "model_type": "课程层面的解析模型或预计算数据",
                },
                warnings=["视觉动画必须标明是计算映射还是教学示意。"],
            )
        if stage is Stage.HYPOTHESIS:
            return StepOutput(
                assistant_message="已将理论假设映射为用户调整参数后可立即观察的VR反馈。",
                stage_payload={
                    "research_hypothesis": "改变主要自变量将按照ECE329理论关系引起可预测响应。",
                    "null_hypothesis": "在设计范围内，主要自变量变化不会造成可分辨响应。",
                    "expected_trend": "由阶段5确定的理论关系生成，不使用伪造实测数据。",
                    "limiting_cases": ["基准条件", "参数下限", "参数上限或模型失效边界"],
                    "vr_feedback_for_trend": ["数值更新", "曲线更新", "空间视觉编码更新"],
                },
            )
        if stage is Stage.CONCEPTUAL_OR_VR_SETUP:
            return StepOutput(
                assistant_message="已在保留用户现有场景条件的前提下，完善Unity VR模拟实验的对象、交互、物理计算和反馈设计。",
                stage_payload={
                    "user_original_design": idea,
                    "existing_context": "保留用户已有场景设定；工作流不新增或改写VR场景设计。",
                    "user_role": "通过有物理意义的交互调整参数、观察结果并进行比较",
                    "core_learning_task": f"探索参数变化与{topics[0]}响应之间的关系",
                    "unity_objects": [
                        "XR Origin与控制器（若项目已有则复用）",
                        "电磁源或激励对象",
                        "实验对象或介质对象",
                        "虚拟探测器",
                        "参数控制面板",
                        "数据与理论反馈面板",
                        "场或波的可视化对象",
                        "记录与重置组件",
                    ],
                    "interactions": [
                        {"user_action": "调整滑块或旋钮", "physical_meaning": "改变主要模型参数", "system_response": "重新计算并更新数值、曲线和视觉编码"},
                        {"user_action": "移动虚拟探测器", "physical_meaning": "改变观察位置", "system_response": "显示当前位置对应的理论场量"},
                        {"user_action": "记录当前设置", "physical_meaning": "保存一个比较条件", "system_response": "向数据面板添加理论预测记录"},
                    ],
                    "physics_layer": {
                        "user_inputs": ["主要自变量", "可选控制参数"],
                        "calculated_outputs": ["目标理论量", "派生比较量"],
                        "model_type": "解析公式或预计算数据",
                        "real_time_updates": ["数值", "曲线", "场表现"],
                        "parameter_limits": ["限制在理论模型适用范围内"],
                        "invalid_conditions": ["参数超界时停止计算并解释原因"],
                    },
                    "visualization_layer": [
                        {"visual_element": "箭头或曲线", "physical_quantity": "矢量场方向或传播方向", "calculated_or_illustrative": "必须在实现时明确"},
                        {"visual_element": "颜色或透明度", "physical_quantity": "归一化强度或衰减", "calculated_or_illustrative": "由理论输出映射"},
                    ],
                    "measurement_interface": ["当前参数及单位", "理论输出", "比较曲线", "模型假设提示", "记录与重置状态"],
                    "internal_experiment_states": ["INTRO", "BASELINE", "PARAMETER_ADJUSTMENT", "OBSERVATION", "DATA_RECORDING", "COMPARISON", "REFLECTION", "COMPLETE"],
                    "design_improvements": ["确保每个交互都有物理意义", "分开数值计算与教学动画", "保留参数基准和重置闭环"],
                },
                warnings=["本阶段不定义VR场景，也不包含可访问性与舒适性设计。"],
            )
        if stage is Stage.VARIABLES_AND_CONDITIONS:
            return StepOutput(
                assistant_message="已把实验变量映射到Unity控制、显示和模型约束。",
                stage_payload={
                    "independent_variable": {"name": "主要电磁参数", "unity_control": "有单位和范围的滑块或旋钮", "range": "限制在理论适用范围"},
                    "dependent_variable": {"name": "目标场量或传播响应", "vr_representation": "数值、曲线和空间编码"},
                    "controlled_variables": ["源条件", "几何条件", "材料或边界中未被选为自变量的参数"],
                    "reference_condition": {"purpose": "建立比较基线", "unity_action": "Reset/Reference preset"},
                    "confounding_factors": ["视觉缩放与真实单位混淆", "多个参数同时变化", "超出模型范围"],
                },
            )
        if stage is Stage.CONCEPTUAL_PROCEDURE:
            return StepOutput(
                assistant_message="已将实验逻辑整理为单一、可重复的VR学习闭环。",
                stage_payload={
                    "procedure_type": "conceptual_vr_flow",
                    "procedure_steps": ["读取目标", "建立基准", "只改变一个参数", "观察空间现象", "读取理论数值", "记录条件", "比较不同设置", "解释趋势"],
                    "comparison_logic": "每次只改变主要自变量，其余条件保持锁定。",
                    "derived_quantities": ["由阶段5理论关系定义的派生量"],
                },
            )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            return StepOutput(
                assistant_message="已生成理论预测窗口规范，并给出与Unity参数控制器联动的接口。",
                stage_payload={"trend_annotation": "由理论模型计算后标注", "unity_update_event": "OnSimulationParameterChanged"},
                visualization=_visualization(idea, emvr=True),
            )
        if stage is Stage.RESULT_INTERPRETATION:
            return StepOutput(
                assistant_message="已为不同结果情形设计物理解释和教学反馈。",
                stage_payload={
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
                    "conceptual_feasibility": {"rating": "待参数确定后复核", "reasoning": "使用课程解析模型时原则上可实现"},
                    "limitations": ["理想化边界条件", "忽略部分损耗或边缘效应", "视觉缩放不等于真实尺度", "VR结果来自模型而非实测"],
                    "teaching_value": {"rating": "high_if_aligned", "learning_contribution": "让不可见场量、空间分布和参数关系可观察"},
                    "innovation": {"rating": "context_dependent", "innovative_elements": ["空间探测", "实时参数—理论反馈", "多条件叠加比较"]},
                    "vr_added_value": {"rating": "high_if_spatial", "reasoning": "只有空间观察和交互对理解有贡献时才值得使用VR"},
                    "recommended_improvements": ["删除无物理意义的交互", "优先保留一个清晰的参数—响应闭环"],
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
                    "lab_identity": {"title": "待用户确认", "lab_id": "待按Builder Pack规则确定", "domain": topics[0]},
                    "learning_goals": "来自阶段3",
                    "core_student_flow": "来自阶段9",
                    "physics_and_presets": "来自阶段5、6和8",
                    "objects_and_feedback": "来自阶段7",
                    "desktop_xr_interaction_meaning": "来自阶段7的交互—物理意义映射",
                    "initial_and_post_action_states": "来自阶段9的基准状态和第一次参数调整",
                    "unresolved_builder_inputs": [
                        "Builder Pack要求的房间、XR Prefab和场景复用决策",
                        "真实Unity API签名及Common复用审计",
                        "验收证据与Unity测试结果",
                    ],
                },
                "course_knowledge_source": KNOWLEDGE.source_reference,
            },
        )
