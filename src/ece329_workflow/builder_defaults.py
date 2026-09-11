from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .models import DesignSession
from .unity_blueprint import CONSTRUCTION_LABELS, construction_defaults
from .physics_blueprint import selected_physics_guidance


IMPLEMENTATION_DEFAULTS_FIELD = "implementation_defaults"
IMPLEMENTATION_DEFAULTS_VERSION = "builder-ui-flow-v7"
_IMPLEMENTATION_INPUT_FIELDS = frozenset(
    {
        "lab_title",
        "lab_id",
        "desktop_interaction_plan",
        "room_spatial_requirements",
        "hidden_object_lifecycle",
        "parameter_specifications",
        "model_constants_and_media",
        "measurement_specifications",
        "numerical_model_specifications",
        "initial_reset_state",
        "independent_variable",
        "observations",
        "controlled_conditions",
        "procedure_steps",
        "visualization_plan",
        "trend_annotation",
        "unity_update_event",
        "unity_objects",
        "interactions",
        "expected_results",
        "acceptance_criteria",
        "limitations",
    }
)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(part for item in value if (part := _text(item)))
    if isinstance(value, dict):
        return "；".join(part for item in value.values() if (part := _text(item)))
    return str(value).strip() if value is not None else ""


def _latest_value(session: DesignSession, field: str) -> str:
    stage_state = session.design_context.get("stage_design_state", {})
    stage_state = stage_state if isinstance(stage_state, dict) else {}
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    field_state = emvr.get("field_state", {})
    field_state = field_state if isinstance(field_state, dict) else {}
    emvr_cleared = emvr.get("explicitly_cleared_fields", [])
    if isinstance(emvr_cleared, list) and field in emvr_cleared:
        return ""
    stage_cleared = stage_state.get("explicitly_cleared_fields", [])
    if isinstance(stage_cleared, list) and field in stage_cleared:
        return ""
    return _text(field_state.get(field) or stage_state.get(field))


def measurement_disables_probe(value: Any) -> bool:
    text = _text(value)
    return bool(
        re.search(
            r"(?:不设置|不使用|无需|取消|没有|无)(?:可移动)?(?:空间)?探针|"
            r"(?:空间)?探针[^。；]{0,30}(?:不适用|取消|不显示)",
            text,
        )
    )


def measurement_is_qualitative_only(value: Any) -> bool:
    text = _text(value)
    return bool(
        re.search(r"(?:仅(?:作|做|进行)?定性|只(?:作|做)定性|不涉及数值计算|无定量(?:读数|指标))", text)
        or (re.search(r"定性(?:描述|判断|形态指标|指标|分类)", text) and not re.search(r"(?:读数|显示|测量)[^。；]{0,45}(?:Bz|Bx|By|分量|大小|数值)", text, re.I))
    )


def measurement_disables_chart(value: Any) -> bool:
    return measurement_is_qualitative_only(value) or bool(re.search(
        r"不(?:生成|设置|使用|显示)(?:数值|定量|理论)?曲线|无(?:数值|定量|理论)?曲线", _text(value)
    ))


def project_derived_contract_text(session: DesignSession, value: Any) -> Any:
    """Refresh generated copies without rewriting the user's canonical fields.

    Baseline paragraphs used to embed a complete Initial/Reset snapshot.
    Refer to its owning contract instead, so later corrections cannot leave
    an obsolete copy in procedure, theory or implementation output.
    """
    if isinstance(value, list):
        return [project_derived_contract_text(session, item) for item in value]
    if isinstance(value, dict):
        return {key: project_derived_contract_text(session, item) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    measurements = _latest_value(session, "measurement_specifications")
    text = value
    text = re.sub(
        r"在相同[^。；;\n]+设置下并排比较([^。；;\n]+)，标出共同点和差异",
        r"并排比较\1；只改变待比较条件，其他输入及观察方式保持一致，标出共同点和差异",
        text,
    )
    baseline_prefixes = (
        "每轮比较前恢复已确认的初始状态：", "每轮比较前恢复同一快照：",
        "使用最新 Initial 契约的参数与对象配置作为比较基准：",
    )
    if text.startswith(baseline_prefixes) and _latest_value(session, "initial_reset_state"):
        return (
            "使用最新 Initial 契约的参数与对象配置作为比较基准："
            + project_derived_contract_text(session, _latest_value(session, "initial_reset_state"))
            + " 保持已确认控制条件一致。"
            "比较过程中保留已保存快照，只有显式 Reset 才按 Reset 契约清理记录。"
        )
    # Replace complete embedded snapshots using their stored field identity,
    # before any display-policy transformations alter the matching text.
    previous_references = []
    state = session.design_context.get("stage_design_state", {})
    if isinstance(state, dict):
        previous_references.append(state.get("reference_condition"))
    for output in session.stage_outputs.values():
        payload = output.get("stage_payload", {}) if isinstance(output, dict) else {}
        if isinstance(payload, dict):
            previous_references.append(payload.get("reference_condition"))
    for previous in previous_references:
        if (_latest_value(session, "initial_reset_state") and isinstance(previous, str)
                and previous.startswith(baseline_prefixes) and previous in text):
            text = text.replace(previous, project_derived_contract_text(session, previous))
    if measurement_disables_probe(measurements):
        text = text.replace("相机或探针设置", "相机设置")
        text = re.sub(r"(?:空间)?探针(?:初始)?(?:位于|置于|放在|在)[^。；;\n]*(?:[。；;]|$)", "", text)
    if measurement_disables_chart(measurements):
        for old, new in (
            ("对比曲线同步重算", "定性比较面板同步刷新"),
            ("曲线和场图", "场图"),
            ("曲线与场图", "场图"),
            ("刷新数值、曲线和空间场", "刷新参数读数、定性比较和空间场"),
            ("RefreshFieldLinesAndPlot", "RefreshFieldLinesAndResults"),
        ):
            text = text.replace(old, new)
    return text


def _procedure_steps(session: DesignSession) -> list[str]:
    from .procedure_contract import current_procedure
    return [
        _text(project_derived_contract_text(session, item))
        for item in current_procedure(session)
    ]


def project_numerical_controls(session: DesignSession, value: Any) -> Any:
    """Numerical controls refer to their owning contract instead of stale copies."""
    if not _latest_value(session, 'numerical_model_specifications'):
        return value
    if isinstance(value, list):
        return [project_numerical_controls(session, item) for item in value]
    if not isinstance(value, str):
        return value
    from .numerical_contract import NUMBER, LENGTH_UNIT
    replacements = (
        (rf'{NUMBER}\s*(?:源段|段直线近似)', '源离散（按最新数值契约固定）'),
        (rf'(?:源分段数|源路径段数)\s*[:：=]?\s*{NUMBER}', '源离散（按最新数值契约固定）'),
        (rf'{NUMBER}\s*个?(?:场线)?种子', '种子（按最新数值契约固定）'),
        (rf'(?:场线)?步长\s*[:：=]?\s*{NUMBER}\s*{LENGTH_UNIT}', '积分步长（按最新数值契约固定）'),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value)
    return value


def build_implementation_defaults(session: DesignSession) -> dict[str, Any]:
    """Build the deterministic Unity/UI contract shown before final export."""

    lab_id = _latest_value(session, "lab_id") or "confirmed_lab_id"
    title = _latest_value(session, "lab_title") or "已确认实验"
    measurements = _text(
        _latest_value(session, "measurement_specifications"),
    ) or "显示已确认的物理输出、单位、采样位置与有效性状态"
    initial_reset = _text(
        _latest_value(session, "initial_reset_state"),
    ) or "Initial 与 Reset 恢复全部已确认默认值并清除临时记录"
    desktop_xr = _text(
        _latest_value(session, "desktop_interaction_plan"),
    ) or "桌面鼠标操作与 XR 射线/抓取逐项对应"
    hidden_lifecycle = _latest_value(session, "hidden_object_lifecycle") or "无"
    steps = _procedure_steps(session)
    step_text = "；".join(
        f"S{index} {step}" for index, step in enumerate(steps, start=1)
    ) or "按基准、单变量操作、观察、记录、比较、解释的顺序执行"
    probe_mode = (
        "不创建空间探针对象；测量面板明确标注不适用及原因"
        if measurement_disables_probe(measurements)
        else "创建测量/探针对象，并按测量契约显示读数、单位、位置与方向"
    )
    chart_mode = (
        "不生成伪定量纵轴曲线；使用统一视图、快照和文字判据完成定性比较"
        if measurement_is_qualitative_only(measurements)
        else "不生成曲线；按测量契约保留数值读数、单位、方向与比较快照"
        if measurement_disables_chart(measurements)
        else "按测量契约生成带单位坐标轴、固定图例和统一量程的结果图"
    )
    return {
        "contract_version": IMPLEMENTATION_DEFAULTS_VERSION,
        "scene": (
            f"实验“{title}”输出到 Assets/Scenes/{lab_id}.unity；场景层级固定为 "
            f"Environment、XR、Experiment、Systems、UI。初始隐藏对象和触发要求：{hidden_lifecycle}。"
        ),
        "navigation": (
            "采用 Start -> Lab -> Back 页面流；Lab 页面固定提供 Capture、Restore、Reset。"
            "Back 只返回 Start，Restore 恢复最近一次 Capture；Reset 的记录清理行为严格采用本契约 Initial/Reset 字段。"
        ),
        "ui_regions": (
            "Game View 固定保留 Instruction、Experiment、Parameters、Status、Results 五个区域；"
            "Instruction 提供当前任务；Experiment 显示实验对象；Parameters 按参数契约提供带单位的控件；"
            "Status 显示计算有效性与操作反馈；Results 按测量契约显示结果和比较快照。"
        ),
        "experiment_flow": step_text,
        "state_machine": (
            "START 经 Start 加载 Initial 到 READY；READY/VALID/CAPTURED/COMPARING 的参数操作进入 DIRTY，"
            "验证通过后 CALCULATING -> VALID，失败进入 INVALID；当前模型有效的 VALID/CAPTURED/COMPARING "
            "可 Capture -> CAPTURED。"
            "有足够有效快照才进入 COMPARING，全部步骤及验收条件通过才 COMPLETE。"
            "INVALID 经修正参数重新验证或 Reset 恢复基准；禁止自动重试。Reset 从任意 Lab 状态回 READY，"
            "随后只重算一次到 VALID；Back 回 START 并取消计算。"
        ),
        "event_pipeline": (
            "桌面或 XR 输入 -> ValidateInput -> UpdateExperimentState -> RecalculateModel -> "
            "RefreshVisualization -> RefreshReadouts -> EnableCapture；输入不自动创建快照，"
            "只有显式 Capture 点击创建一次快照；重复 event_id 幂等，UI 回填不触发新输入。"
        ),
        "unity_components": (
            "LabFlowController 负责页面和实验状态；ParameterController 负责控件、单位和范围；"
            "PhysicsModel 只执行已确认公式；VisualizationController 只读取模型输出；"
            "MeasurementController 执行采样；SnapshotStore 适配 Common 状态存储；"
            "ResetController 恢复已确认状态；DesktopXRAdapter 统一鼠标、模拟器与真机事件。"
        ),
        "measurement_and_chart_policy": f"{probe_mode}；{chart_mode}。",
        "initial_reset": project_derived_contract_text(session, initial_reset),
        "logging_help_language": (
            "界面语言默认 English；状态栏记录参数变更、计算完成、Capture、Restore、Reset 与无效条件；"
            "键鼠和 XR 关键操作提供上下文帮助，错误提示必须说明原因及恢复动作。"
        ),
        "desktop_xr_mapping": desktop_xr,
        **construction_defaults(lab_id),
        "selected_physics_implementation": selected_physics_guidance(session),
        "numerical_model": _latest_value(session, "numerical_model_specifications"),
    }


def format_implementation_defaults(contract: dict[str, Any]) -> str:
    labels = (
        ("scene", "场景与层级"),
        ("navigation", "界面导航与固定按钮"),
        ("ui_regions", "Game View区域"),
        ("experiment_flow", "学生实验步骤"),
        ("state_machine", "实验状态机"),
        ("event_pipeline", "Unity事件链"),
        ("unity_components", "Unity组件职责"),
        ("measurement_and_chart_policy", "测量与曲线策略"),
        ("initial_reset", "Initial/Reset"),
        ("logging_help_language", "日志、帮助与语言"),
        ("desktop_xr_mapping", "桌面与XR映射"),
        *CONSTRUCTION_LABELS,
        ("selected_physics_implementation", "所选公式的计算实现与独立基准"),
        ("numerical_model", "本实验数值算法与边界"),
    )
    return "\n".join(
        f"{index}. {label}：{_text(contract.get(key))}"
        for index, (key, label) in enumerate(labels, start=1)
    )


def implementation_defaults_fingerprint(session: DesignSession) -> str:
    """Hash only design inputs that can change the generated implementation."""

    stage_state = session.design_context.get("stage_design_state", {})
    stage_state = stage_state if isinstance(stage_state, dict) else {}
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    field_state = emvr.get("field_state", {})
    field_state = field_state if isinstance(field_state, dict) else {}
    material = {
        # Bind approval to the exact deterministic contract shown to the user.
        # This also covers legacy procedure steps stored only in stage_outputs.
        "generated_contract": build_implementation_defaults(session),
        "stage_state": {
            key: deepcopy(value)
            for key, value in stage_state.items()
            if key in _IMPLEMENTATION_INPUT_FIELDS
        },
        "field_state": {
            key: deepcopy(value)
            for key, value in field_state.items()
            if key in _IMPLEMENTATION_INPUT_FIELDS
        },
        "formula_selection": deepcopy(
            (emvr.get("formula_flow") or {}).get("formula_selection", {})
            if isinstance(emvr.get("formula_flow"), dict) else {}
        ),
        "selected_primary_formula_ids": deepcopy(emvr.get("selected_primary_formula_ids", [])),
        "selected_supporting_formula_ids": deepcopy(emvr.get("selected_supporting_formula_ids", [])),
        "cleared_fields": deepcopy(emvr.get("explicitly_cleared_fields", [])),
        "stage_cleared_fields": deepcopy(stage_state.get("explicitly_cleared_fields", [])),
        "authoritative_experiment_brief": deepcopy(
            emvr.get("authoritative_experiment_brief", {})
        ),
        "embedded_reference_material": deepcopy(session.design_context.get("builder_reference_material", [])),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def implementation_defaults_approval_valid(session: DesignSession) -> bool:
    emvr = session.design_context.get("emvr_design", {})
    if not isinstance(emvr, dict):
        return False
    approval = emvr.get("implementation_defaults_approval", {})
    return bool(
        isinstance(approval, dict)
        and approval.get("fingerprint") == implementation_defaults_fingerprint(session)
        and approval.get("contract_version") == IMPLEMENTATION_DEFAULTS_VERSION
        and approval.get("approved_value") == _latest_value(session, IMPLEMENTATION_DEFAULTS_FIELD)
    )


def record_implementation_defaults_approval(
    session: DesignSession,
    *,
    source: str,
) -> None:
    emvr = session.design_context.setdefault("emvr_design", {})
    if not isinstance(emvr, dict):
        emvr = {}
        session.design_context["emvr_design"] = emvr
    emvr["implementation_defaults_approval"] = {
        "fingerprint": implementation_defaults_fingerprint(session),
        "contract_version": IMPLEMENTATION_DEFAULTS_VERSION,
        "source": source,
        "approved_value": _latest_value(session, IMPLEMENTATION_DEFAULTS_FIELD),
    }
