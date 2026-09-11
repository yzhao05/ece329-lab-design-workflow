from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .builder_defaults import (
    IMPLEMENTATION_DEFAULTS_FIELD,
    build_implementation_defaults,
    format_implementation_defaults,
    implementation_defaults_approval_valid,
    measurement_disables_probe,
)
from .models import STAGE_SEQUENCE, DesignSession, InteractionState, Stage
from .builder_portability import PACK_NAME, ROOT_DISCOVERY, embed_supplied_references, validate_portable_content


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
        "field": "builder_workspace_absolute_path",
        "stage": Stage.COURSE_MAPPING_AND_DIRECTION,
        "label": "本机BuilderPack定位规则",
        "question": ROOT_DISCOVERY,
    },
    {
        "field": "desktop_interaction_plan",
        "stage": Stage.CONCEPTUAL_OR_VR_SETUP,
        "label": "桌面鼠标操作与VR映射",
        "question": (
            "请说明桌面端具体用鼠标怎样操作哪些对象，并写清每项桌面操作对应的VR操作；"
            "例如单击选择对象、拖动改变位置，对应VR射线选择与手柄抓取。"
            "若拖动只允许改变距离，请明确固定轴/中点、自由度、吸附步长及越界反馈；不要引入额外自变量。"
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
        "field": "initial_reset_state",
        "stage": Stage.CONCEPTUAL_OR_VR_SETUP,
        "label": "Initial与Reset确定状态",
        "question": (
            "请给出场景首次打开和按下 Reset 后必须恢复的完整状态：对象配置、每个可调参数的默认数值与单位、"
            "离散选项、已采用的测量对象与显示状态，以及 Reset 后是否清除已保存数据；"
            "只有明确使用探针或曲线时才填写对应状态。"
            "若有多情形记录，请分别定义基准加载、Restore和Reset，避免基准加载清空之前的比较证据。"
        ),
    },
    {
        "field": "parameter_specifications",
        "stage": Stage.VARIABLES_AND_CONDITIONS,
        "label": "公式自变量可调契约",
        "question": (
            "请逐项列出所选公式中作为实验自变量的输入，并确认全部可在桌面与VR中调节。"
            "每个连续量必须给出控件、最小值、最大值、默认值、单位和步长；离散量必须给出默认选项和全部允许选项。"
            "如果使用近/中/远等分组，还必须给出每组的准确数值或边界。"
        ),
    },
    {
        "field": "model_constants_and_media",
        "stage": Stage.VARIABLES_AND_CONDITIONS,
        "label": "模型常量、介质与固定输入",
        "question": (
            "请逐项给出公式中不作为自变量调节的常量、介质参数和控制量的准确数值、单位及固定理由；"
            "介电常数或磁导率等材料量需明确采用真空常量、相对参数乘以真空常量，还是直接使用可调绝对值。"
            "带符号的源要写明每个对象的符号约定；只有绝对值和同号/异号标签不足以确定初态。"
        ),
    },
    {
        "field": "measurement_specifications",
        "stage": Stage.EXPECTED_DATA_VISUALIZATION,
        "label": "指标与空间测量定义",
        "question": (
            "请定义最终界面中的每个数值、纵轴指标和空间测量读数：给出物理量或指标名称、计算公式/算法、"
            "单位（无量纲也需注明）、采样位置或区间、刷新时机；并明确空间探针显示矢量分量、大小、方向、"
            "相位或本实验需要的其他数据。不适用的项目需明确写出不适用及原因。"
        ),
    },
    {
        "field": "numerical_model_specifications",
        "stage": Stage.EXPECTED_DATA_VISUALIZATION,
        "label": "数值算法与边界契约",
        "question": (
            "请明确所选公式怎样计算和显示：直接代数求值或具体数值算法、计算域及坐标/单位、"
            "采样点或网格、步长、误差/收敛容差、最大迭代或积分步数、奇点/边界/零场处理及刷新时机。"
            "若只需直接公式且不积分，请明确无需迭代及采样方式；若显示场线或轨迹，仍需给出积分方法、"
            "种子位置/数量、步长和终止条件。这些数值需先在设计中确认，再写入最终PDF。"
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
    {
        "field": IMPLEMENTATION_DEFAULTS_FIELD,
        "stage": Stage.DESIGN_VALUE_AND_LIMITATIONS,
        "label": "Builder默认界面、流程与Unity实现契约",
        "question": (
            "请审阅系统根据当前实验生成的默认界面、完整学生流程、Unity状态机、事件链、组件职责、"
            "测量策略、日志和帮助方案。回复“批准默认方案”即可采用；如需修改，请给出完整替代契约。"
        ),
        "default_proposal": True,
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
    explicitly_cleared = {
        str(field)
        for field in emvr.get("explicitly_cleared_fields", [])
        if str(field) in BUILDER_REQUIREMENT_FIELDS
    } if isinstance(emvr.get("explicitly_cleared_fields", []), list) else set()
    stage_cleared = stage_state.get("explicitly_cleared_fields", [])
    if isinstance(stage_cleared, list):
        explicitly_cleared.update(
            str(field)
            for field in stage_cleared
            if str(field) in BUILDER_REQUIREMENT_FIELDS
        )
    values = {
        # EMVR field_state is the latest field-level source of truth.  A
        # student may revise an earlier Builder item from a later stage; the
        # old stage snapshot must never override that newer correction.
        field: (
            ""
            if field in explicitly_cleared
            else _text(field_state.get(field) or stage_state.get(field))
        )
        for field in BUILDER_REQUIREMENT_FIELDS
    }
    if not implementation_defaults_approval_valid(session):
        values[IMPLEMENTATION_DEFAULTS_FIELD] = ""
    # Legacy storage key remains readable for old sessions. Machine-specific
    # locations are runtime context, never a student requirement or PDF fact.
    if session.interaction_state is InteractionState.EMVR_DIRECT:
        values["builder_workspace_absolute_path"] = PACK_NAME
    return values


def builder_requirement_default_proposal(
    session: DesignSession,
    field: str,
) -> dict[str, Any] | None:
    """Return a complete deterministic proposal for a defaulted requirement."""

    if field != IMPLEMENTATION_DEFAULTS_FIELD:
        return None
    contract = build_implementation_defaults(session)
    return {
        "contract": contract,
        "value": format_implementation_defaults(contract),
        "option_id": "approve-builder-implementation-defaults",
    }


def _field_valid(field: str, value: str) -> bool:
    if not is_resolved_design_value(value):
        return False
    if field == "lab_id":
        return LAB_ID_PATTERN.fullmatch(value) is not None
    if field == "builder_workspace_absolute_path":
        return value == PACK_NAME or bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", value))
    if field == "parameter_specifications":
        # This is content validation, not conversational intent matching: a
        # continuous parameter needs at least one numeric boundary and a unit;
        # a categorical parameter must explicitly identify itself as discrete.
        has_numeric_boundary = re.search(r"\d", value) is not None
        has_unit = re.search(
            r"(?:m|cm|mm|km|Hz|kHz|MHz|GHz|V|mV|A|mA|C|μC|uC|F|H|Ω|ohm|S|W|T|Wb|rad|°|deg|s|ms|μs|ns|"
            r"米|厘米|毫米|千米|秒|毫秒|微秒|纳秒|赫兹|伏特?|安培?|库仑|法拉|亨利|欧姆|特斯拉|韦伯|弧度|度|无量纲)",
            value,
            flags=re.IGNORECASE,
        ) is not None
        explicitly_discrete = re.search(r"(?:离散|选项)", value) is not None
        has_default = re.search(r"(?:默认|初始|reset)", value, flags=re.IGNORECASE) is not None
        has_step_or_options = re.search(
            r"(?:步长|增量|离散|允许选项|全部选项)", value
        ) is not None
        adjustable = re.search(r"(?:可调|调节|滑块|旋钮|输入框|拖动)", value) is not None
        return (
            has_default
            and has_step_or_options
            and adjustable
            and (explicitly_discrete or (has_numeric_boundary and has_unit))
        )
    if field == "model_constants_and_media":
        has_number = re.search(r"\d", value) is not None
        has_unit_or_dimensionless = re.search(
            r"(?:F/m|H/m|C|V|A|Ω|ohm|S/m|T|Hz|rad/s|m/s|kg|无量纲|真空常量|epsilon_0|mu_0|ε₀|μ₀)",
            value,
            flags=re.IGNORECASE,
        ) is not None
        has_role = re.search(r"(?:固定|常量|控制量|可调|介质)", value) is not None
        return has_number and has_unit_or_dimensionless and has_role
    if field == "measurement_specifications":
        has_definition = re.search(r"(?:计算|定义|公式|算法|不适用)", value) is not None
        has_unit = re.search(r"(?:单位|无量纲|V/m|A/m|T|W/m|Hz|rad|度)", value) is not None
        has_readout = re.search(r"(?:探针|读数|测量|采样)", value) is not None
        return has_definition and has_unit and has_readout
    if field == "numerical_model_specifications":
        return all(re.search(pattern, value, re.IGNORECASE) for pattern in (
            r"直接|代数|算法|Euler|RK4|Runge|积分|有限差分|有限元",
            r"采样|网格|计算域|空间域",
            r"边界|奇点|零场|无效|NaN",
            r"无需迭代|不迭代|最大.*(?:步|迭代)|上限|终止",
            r"容差|误差|精度",
        ))
    if field == "initial_reset_state":
        has_initial = re.search(r"(?:Initial|初始|首次打开)", value, flags=re.IGNORECASE) is not None
        has_reset = re.search(r"(?:Reset|重置)", value, flags=re.IGNORECASE) is not None
        has_concrete_state = re.search(r"(?:\d|同种|异种|同号|异号|开|关|空|清除)", value) is not None
        return has_initial and has_reset and has_concrete_state
    if field == IMPLEMENTATION_DEFAULTS_FIELD:
        required_markers = (
            ("Start", "开始页"),
            ("Lab", "实验页"),
            ("Back", "返回"),
            ("Capture", "保存快照"),
            ("Restore", "恢复快照"),
            ("Reset", "重置"),
            ("Unity",),
            ("事件", "event"),
            ("帮助", "help"),
            ("English", "中文", "language"),
        )
        lowered = value.casefold()
        return all(
            any(marker.casefold() in lowered for marker in alternatives)
            for alternatives in required_markers
        )
    return True


def _uses_named_distance_bands(session: DesignSession) -> bool:
    context = _active_requirement_context(session)
    for match in re.finditer(r"([近中远])\s*[/、，,]\s*([近中远])\s*[/、，,]\s*([近中远])", context):
        if set(match.groups()) == {"近", "中", "远"}:
            return True
    return all(re.search(rf"{label}(?:距离|区|场)", context) for label in ("近", "中", "远"))


def _active_requirement_context(session: DesignSession) -> str:
    """Do not let audit history or a superseded default create new blockers."""

    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    fields = emvr.get("field_state", {})
    fields = fields if isinstance(fields, dict) else {}
    stage_state = session.design_context.get("stage_design_state", {})
    stage_state = stage_state if isinstance(stage_state, dict) else {}
    cleared = set(emvr.get("explicitly_cleared_fields", []) or []) | set(
        stage_state.get("explicitly_cleared_fields", []) or []
    )
    relevant = {
        "procedure_steps", "comparison_cases", "limiting_cases", "object_constraints",
        "parameter_specifications", "model_constants_and_media", "measurement_specifications",
        "expected_results", "acceptance_criteria", "report_questions",
    }
    current = {
        field: _text(fields.get(field) or stage_state.get(field))
        for field in relevant if field not in cleared
    }
    stored_values = []
    for output in session.stage_outputs.values():
        payload = output.get("stage_payload", {}) if isinstance(output, dict) else {}
        if isinstance(payload, dict):
            stored_values.extend(
                _text(value) for field, value in payload.items()
                if field in relevant and field not in cleared and not current.get(field)
            )
    return "；".join([*current.values(), *stored_values])


def _defines_named_distance_bands(value: str) -> bool:
    combined = re.search(r"近\s*[/、]\s*中\s*[/、]\s*远", value) is not None
    separate = all(
        re.search(rf"{label}[^。；;]{{0,24}}\d", value) is not None
        for label in ("近", "中", "远")
    )
    return bool(
        separate
        or (
            combined
            and any(
                re.search(r"近\s*[/、]\s*中\s*[/、]\s*远", clause)
                and len(re.findall(r"[-+]?\d+(?:\.\d+)?", clause)) >= 3
                for clause in re.split(r"[。；;\n]", value)
            )
        )
    )


def _cross_field_validation_error(
    session: DesignSession,
    field: str,
    values: dict[str, str],
) -> str | None:
    if field != "builder_workspace_absolute_path":
        try:
            content, _ = embed_supplied_references(
                values.get(field, ""), session.design_context.get("builder_reference_material", [])
            )
            validate_portable_content(content, field)
        except ValueError as exc:
            return "这项交付内容引用越界或参考副本不完整：" + str(exc) + "。请将必要内容复制到本字段，或提供带内容的内嵌参考；不要只保留包外路径。"
    if field == "numerical_model_specifications":
        numerical = values.get(field, "")
        integrates = bool(re.search(r"RK4|龙格", numerical, re.I)) or any(
            re.search(r"积分|trajectory", clause, re.I)
            and not re.search(r"无需|不(?:进行|需要|使用|做)|无积分", clause)
            for clause in re.split(r"[。；;\n]", numerical)
        )
        if integrates and not numerical_tolerance_defined(numerical):
            return "数值积分给出了步长，但没有可检验的误差/收敛容差；步长和最大步数不能代替精度验收值。"
        from .numerical_contract import biot_numerical_gap
        gap = biot_numerical_gap(numerical)
        if gap:
            return '数值算法还需明确：' + gap + '。'
    if field == "model_constants_and_media":
        constants = values.get(field, "")
        # Discrete geometry controls still need numerical source geometry.
        # Ask for the first missing property, retaining all other supplied data.
        choices = values.get('parameter_specifications', '')
        from .numerical_contract import geometry_gap
        gap = geometry_gap(choices, constants + '；' + values.get('numerical_model_specifications', ''))
        if gap:
            return f'载流路径选项中尚未定义{gap}；固定对象也需要有效几何输入（长度为正且带单位，匝数为正整数），不能由Builder猜测。'
        if "绝对值" in constants and re.search(r"两(?:个|个点)?电荷|两源", constants):
            signed_context = "；".join(values.get(key, "") for key in (
                "model_constants_and_media", "parameter_specifications", "initial_reset_state"
            ))
            named_signs = re.findall(
                r"(Q[_ ]?[12AB]|电荷\s*[AB甲乙]|\b[AB])\s*(?:=|：|为|固定为?)?\s*[+−±-]\s*\d",
                signed_context, re.I,
            )
            verbal_convention = re.search(r"(?:A|Q1|第一|一个)[^。；;]{0,20}正[^。；;]{0,50}(?:B|Q2|第二|另一)[^。；;]{0,25}(?:负|同号|异号)", signed_context)
            names = {re.sub(r"电荷|Q|[_\s]", "", name.upper()).translate(str.maketrans({"1": "A", "2": "B", "甲": "A", "乙": "B"})) for name in named_signs}
            if len(names) < 2 and not verbal_convention:
                return "两源只给出了电荷量绝对值；请补充每个源的带符号初值及同号/异号切换约定，不能由Builder猜测。"
    if field in {"expected_results", "acceptance_criteria"}:
        numerical = values.get('numerical_model_specifications', '')
        fixed_seeds = re.search(r'(?:固定[^。；;]{0,15}种子|种子(?:点)?\s*\d+|\d+\s*个?种子)', numerical)
        for clause in re.split(r"[。；;\n]", values.get(field, "")):
            if (fixed_seeds and re.search(r'毕奥|Biot|biot_savart', numerical, re.I)
                    and re.search(r'场线[^。；;]{0,20}(?:更密|最密|密度[^。；;]{0,8}增加|范围[^。；;]{0,8}(?:扩大|最大))', clause)
                    and not re.search(r'不|不能|不得|无需|并非', clause)):
                return '固定种子下，增大电流不能作为场线条数、密度或环绕范围必然增加的验收条件；请用场强映射或方向、形态比较定义结果。'
            if "场线" in clause and re.search(r"空白[^。；;]{0,40}(?:显著扩大|必然扩大|一定扩大)", clause) and not re.search(r"不|不能|不得|无需", clause):
                return "场线绘制的空白不能作为零场区域或固定扩大的硬判据；请按公式、统一视图和实际形态定义验收。"
    if field == "parameter_specifications" and _uses_named_distance_bands(session):
        if not _defines_named_distance_bands(values.get(field, "")):
            return "实验流程使用了近/中/远分组，但参数契约没有分别给出三组的准确数值或边界。"
    if field == "model_constants_and_media":
        context = _active_requirement_context(session)
        if "排除半径" in context and re.search(
            r"排除半径[^。；;]{0,30}\d", values.get(field, "")
        ) is None:
            return "模型使用了源附近排除半径，但固定输入契约没有给出排除半径的准确数值和单位。"
    if field == "initial_reset_state" and measurement_disables_probe(
        values.get("measurement_specifications", "")
    ):
        if any(re.search(
            r"探针[^。；;]{0,40}(?:位于|置于|放在|在|位置|坐标|初始)",
            clause,
        ) and not re.search(r"不适用|不设置|不使用|取消|无需|无探针", clause)
            for clause in re.split(r"[。；;]", values.get(field, ""))):
            return "测量契约明确不使用空间探针，但 Initial/Reset 仍保留探针位置；请删除旧探针状态。"
    return None


def numerical_tolerance_defined(value: str) -> bool:
    """Recognize numeric error bounds, including percentages and comparisons."""
    number = r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    return bool(re.search(
        rf"(?:容差|误差(?:上限|阈值)|收敛(?:阈值|精度)|tolerance)[^。；;\n]{{0,45}}{number}|"
        rf"(?:相对误差|绝对误差|位置偏差|偏差|误差)\s*(?:小于|不超过|低于|<=|<|≤)\s*{number}", value, re.I
    ))


def _requirement_valid(
    session: DesignSession,
    field: str,
    value: str,
    values: dict[str, str],
) -> bool:
    return _field_valid(field, value) and _cross_field_validation_error(
        session, field, values
    ) is None


def builder_requirement_value_is_valid(field: str, value: Any) -> bool:
    """Validate one explicitly requested Builder field for direct submission."""

    return field in BUILDER_REQUIREMENT_FIELDS and _field_valid(field, _text(value))


def _validation_error(field: str, value: str) -> str | None:
    if not is_resolved_design_value(value) or _field_valid(field, value):
        return None
    shown = value[:120]
    if field == "lab_id":
        return (
            f"当前输入“{shown}”不符合 Builder ID 格式：必须以小写字母开头，"
            "且只能包含小写字母、数字和下划线（长度 3–64 个字符）。"
        )
    if field == "parameter_specifications":
        return (
            f"当前输入“{shown}”还不能形成可执行公式自变量契约：每个自变量都需明确可调控件和默认值；"
            "连续参数还需包含数值范围、单位和步长，离散参数需列出全部允许选项。"
        )
    if field == "builder_workspace_absolute_path":
        return "BuilderPack由实际运行电脑定位，不需要填写机器绝对路径。"
    if field == "model_constants_and_media":
        return f"当前输入“{shown}”仍缺少常量/介质的准确数值、单位或固定/可调角色。"
    if field == "measurement_specifications":
        return f"当前输入“{shown}”仍缺少指标计算方法、单位或空间探针/采样读数定义。"
    if field == "numerical_model_specifications":
        return "数值契约仍缺少算法、采样域、误差/精度、边界处理或有界终止条件；请补充缺少部分。"
    if field == "initial_reset_state":
        return f"当前输入“{shown}”仍未同时定义 Initial 与 Reset 的具体对象和参数状态。"
    return None


def missing_builder_requirements(
    session: DesignSession,
    *,
    stage: Stage | None = None,
) -> list[dict[str, Any]]:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return []
    values = builder_requirement_values(session)
    missing: list[dict[str, Any]] = []
    for spec in BUILDER_REQUIREMENT_SPECS:
        field = str(spec["field"])
        value = values.get(field, "")
        if (stage is not None and spec["stage"] is not stage) or _requirement_valid(
            session, field, value, values
        ):
            continue
        item = deepcopy(spec)
        error = _cross_field_validation_error(session, field, values) or _validation_error(
            field, value
        )
        if field == IMPLEMENTATION_DEFAULTS_FIELD:
            proposal = builder_requirement_default_proposal(session, field)
            if proposal is not None:
                item["default_contract"] = proposal["contract"]
                item["default_value"] = proposal["value"]
                item["default_option_id"] = proposal["option_id"]
            stage_state = session.design_context.get("stage_design_state", {})
            if (
                isinstance(stage_state, dict)
                and _text(stage_state.get(field))
                and not implementation_defaults_approval_valid(session)
            ):
                error = "实验内容已更新，先前批准的默认实现契约已失效；下面已按最新设计重新生成。"
        if error:
            item["validation_error"] = error
            # Keep the visible task and pending field about the same gap.
            # Repeating the full initial questionnaire hides what is missing.
            if field != IMPLEMENTATION_DEFAULTS_FIELD:
                item["question"] = error + " 已有内容保留；只需补充或修正这一项，也可以先索取参考。"
        missing.append(item)
    return missing


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
            "complete": _requirement_valid(
                session,
                str(spec["field"]),
                values.get(str(spec["field"]), ""),
                values,
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
