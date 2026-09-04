from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .models import STAGE_SEQUENCE, DesignSession, InteractionState, Stage


LAB_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_UNRESOLVED_VALUE = re.compile(
    r"^(?:暂未(?:明确|确定|填写)|尚未(?:明确|确定|填写)|"
    r"待(?:补充|确定|确认|完善)|之后再(?:决定|确定)|"
    r"稍后再?(?:补充|确定)|tbd|todo|unresolved)"
    r"(?:[，,。；;：:\s].*)?$",
    flags=re.IGNORECASE,
)


BUILDER_REQUIREMENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "field": "lab_title",
        "stage": Stage.COURSE_MAPPING_AND_DIRECTION,
        "label": "实验名称",
        "question": "请为这个实验确定一个简洁、能体现研究对象与主要比较关系的名称。",
    },
    {
        "field": "lab_id",
        "stage": Stage.COURSE_MAPPING_AND_DIRECTION,
        "label": "Builder实验ID",
        "question": (
            "请确定 Builder 使用的实验ID：以小写字母开头，只使用小写字母、数字和下划线，"
            "长度为3到64个字符，例如 ece329_charge_field。"
        ),
    },
    {
        "field": "desktop_interaction_plan",
        "stage": Stage.CONCEPTUAL_OR_VR_SETUP,
        "label": "桌面鼠标操作与VR映射",
        "question": (
            "请说明桌面端具体用鼠标怎样操作哪些对象，并写清每项桌面操作对应的VR操作；"
            "例如单击选择对象、拖动改变位置，对应VR射线选择与手柄抓取。"
        ),
    },
    {
        "field": "room_spatial_requirements",
        "stage": Stage.CONCEPTUAL_OR_VR_SETUP,
        "label": "房间空间、摆放与视觉要求",
        "question": (
            "请描述实验在房间中的空间需求和相对摆放关系，包括学生主要站位、实验对象与面板"
            "应位于哪里、需要保留多少观察或操作空间，以及灯光和视觉风格要求；不需要给精确Unity坐标。"
        ),
    },
    {
        "field": "hidden_object_lifecycle",
        "stage": Stage.CONCEPTUAL_OR_VR_SETUP,
        "label": "初始隐藏对象与触发后状态",
        "question": (
            "请说明实验开始时哪些对象或模板隐藏、由什么操作触发、出现后应处于什么状态；"
            "如果没有初始隐藏对象，请直接回答“无”。"
        ),
    },
    {
        "field": "parameter_specifications",
        "stage": Stage.VARIABLES_AND_CONDITIONS,
        "label": "参数范围与单位",
        "question": (
            "请逐项给出学生可调参数的名称、最小值、最大值、单位和建议步长；"
            "离散参数请列出允许选项。所有主要自变量都需要明确范围与单位。"
        ),
    },
    {
        "field": "expected_results",
        "stage": Stage.RESULT_INTERPRETATION,
        "label": "Lab特有预期结果",
        "question": (
            "请给出这个Lab在各主要比较情形下应出现的具体预期结果，说明哪些读数、曲线或空间现象"
            "能够支持前面的研究假设。"
        ),
    },
    {
        "field": "acceptance_criteria",
        "stage": Stage.RESULT_INTERPRETATION,
        "label": "Lab特有通过条件",
        "question": (
            "请定义这个Lab特有的完成与通过条件：学生必须完成哪些操作、产生哪些可观察结果，"
            "以及用什么标准判断核心任务已经完成。"
        ),
    },
    {
        "field": "report_questions",
        "stage": Stage.RESULT_INTERPRETATION,
        "label": "实验报告问题",
        "question": (
            "请给出学生完成实验后需要回答的Lab特有报告问题。问题应直接检验研究问题、"
            "理论解释和比较结果；通用报告格式不需要在这里定义。"
        ),
    },
)


BUILDER_REQUIREMENT_FIELDS = frozenset(
    str(item["field"]) for item in BUILDER_REQUIREMENT_SPECS
)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(part for item in value if (part := _text(item)))
    if isinstance(value, dict):
        return "；".join(part for item in value.values() if (part := _text(item)))
    return str(value).strip() if value is not None else ""


def is_resolved_design_value(value: Any) -> bool:
    """Return whether a final artifact value contains confirmed content.

    This is an export-integrity check, not conversational intent routing.
    Explicitly optional answers such as ``无`` remain valid, while temporary
    placeholders cannot make a Builder requirement or PDF appear complete.
    """

    if isinstance(value, list):
        return bool(value) and all(is_resolved_design_value(item) for item in value)
    if isinstance(value, dict):
        substantive = [
            item for item in value.values() if item not in (None, "", [], {})
        ]
        return bool(substantive) and all(
            is_resolved_design_value(item) for item in substantive
        )
    text = _text(value)
    return bool(text) and _UNRESOLVED_VALUE.fullmatch(text) is None


def builder_requirement_values(session: DesignSession) -> dict[str, str]:
    stage_state = session.design_context.get("stage_design_state", {})
    stage_state = stage_state if isinstance(stage_state, dict) else {}
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    field_state = emvr.get("field_state", {})
    field_state = field_state if isinstance(field_state, dict) else {}
    return {
        # EMVR field_state is the latest field-level source of truth.  A
        # student may revise an earlier Builder item from a later stage; the
        # old stage snapshot must never override that newer correction.
        field: _text(field_state.get(field) or stage_state.get(field))
        for field in BUILDER_REQUIREMENT_FIELDS
    }


def _field_valid(field: str, value: str) -> bool:
    if not is_resolved_design_value(value):
        return False
    if field == "lab_id":
        return LAB_ID_PATTERN.fullmatch(value) is not None
    if field == "parameter_specifications":
        # This is content validation, not conversational intent matching: a
        # continuous parameter needs at least one numeric boundary and a unit;
        # a categorical parameter must explicitly identify itself as discrete.
        has_numeric_boundary = re.search(r"\d", value) is not None
        has_unit = re.search(
            r"(?:m|cm|mm|km|Hz|kHz|MHz|GHz|V|mV|A|mA|C|μC|uC|F|H|Ω|ohm|S|W|T|Wb|rad|°|deg|s|ms|μs|ns|无量纲)",
            value,
            flags=re.IGNORECASE,
        ) is not None
        explicitly_discrete = re.search(r"(?:离散|选项)", value) is not None
        return explicitly_discrete or (has_numeric_boundary and has_unit)
    return True


def missing_builder_requirements(
    session: DesignSession,
    *,
    stage: Stage | None = None,
) -> list[dict[str, Any]]:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return []
    values = builder_requirement_values(session)
    return [
        deepcopy(spec)
        for spec in BUILDER_REQUIREMENT_SPECS
        if (stage is None or spec["stage"] is stage)
        and not _field_valid(str(spec["field"]), values.get(str(spec["field"]), ""))
    ]


def next_builder_requirement(
    session: DesignSession,
    stage: Stage,
) -> dict[str, Any] | None:
    missing = missing_builder_requirements(session, stage=stage)
    return missing[0] if missing else None


def next_due_builder_requirement(
    session: DesignSession,
    stage: Stage,
) -> dict[str, Any] | None:
    """Return the earliest unfilled requirement that should already be known.

    This also repairs mode switches or resumed legacy sessions: an item from an
    earlier stage remains due, but a future-stage item is not asked early.
    """

    current_index = STAGE_SEQUENCE.index(stage)
    missing = missing_builder_requirements(session)
    return next(
        (
            item
            for item in missing
            if STAGE_SEQUENCE.index(item["stage"]) <= current_index
        ),
        None,
    )


def builder_handoff_status(session: DesignSession) -> dict[str, Any]:
    values = builder_requirement_values(session)
    items = [
        {
            "field": str(spec["field"]),
            "label": str(spec["label"]),
            "complete": _field_valid(
                str(spec["field"]), values.get(str(spec["field"]), "")
            ),
        }
        for spec in BUILDER_REQUIREMENT_SPECS
    ]
    completed = sum(1 for item in items if item["complete"])
    return {
        "ready": completed == len(items),
        "completed": completed,
        "required": len(items),
        "current_requirement": next(
            (item["label"] for item in items if not item["complete"]),
            None,
        ),
        "items": items,
    }


def validate_builder_requirements(session: DesignSession) -> None:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        raise ValueError("Builder handoff validation requires an EMVR design")
    missing = missing_builder_requirements(session)
    if missing:
        labels = "、".join(str(item["label"]) for item in missing)
        raise ValueError(f"Builder Gate 1交接内容仍未明确：{labels}")
