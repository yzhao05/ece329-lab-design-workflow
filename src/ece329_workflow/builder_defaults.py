from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .models import DesignSession, Stage
from .unity_blueprint import CONSTRUCTION_LABELS, construction_defaults


IMPLEMENTATION_DEFAULTS_FIELD = "implementation_defaults"
IMPLEMENTATION_DEFAULTS_VERSION = "builder-ui-flow-v2"
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
        re.search(r"(?:定性(?:描述|判断)|不涉及数值计算|无定量纵轴|不生成(?:定量)?曲线)", text)
    )


def _compact(value: Any, fallback: str, *, limit: int = 520) -> str:
    text = " ".join(_text(value).split())
    return (text[: limit - 1] + "…") if len(text) > limit else (text or fallback)


def _procedure_steps(session: DesignSession) -> list[str]:
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    field_state = emvr.get("field_state", {})
    field_state = field_state if isinstance(field_state, dict) else {}
    raw = field_state.get("procedure_steps")
    stage_state = session.design_context.get("stage_design_state", {})
    stage_state = stage_state if isinstance(stage_state, dict) else {}
    if any(
        "procedure_steps" in state.get("explicitly_cleared_fields", [])
        for state in (emvr, stage_state)
    ):
        return []
    if not raw and stage_state.get("procedure_steps"):
        raw = [stage_state["procedure_steps"]]
    if not isinstance(raw, list) or not raw:
        stored = session.stage_outputs.get(Stage.CONCEPTUAL_PROCEDURE.value, {})
        payload = stored.get("stage_payload", {}) if isinstance(stored, dict) else {}
        raw = payload.get("procedure_steps", []) if isinstance(payload, dict) else []
    return [
        _text(item)
        for item in raw
        if _text(item)
    ] if isinstance(raw, list) else []


def build_implementation_defaults(session: DesignSession) -> dict[str, Any]:
    """Build the deterministic Unity/UI contract shown before final export."""

    lab_id = _latest_value(session, "lab_id") or "confirmed_lab_id"
    title = _latest_value(session, "lab_title") or "已确认实验"
    parameters = _text(
        _latest_value(session, "parameter_specifications"),
    ) or "使用已确认自变量的控件、范围、单位、默认值与步长"
    measurements = _text(
        _latest_value(session, "measurement_specifications"),
    ) or "显示已确认的物理输出、单位、采样位置与有效性状态"
    initial_reset = _text(
        _latest_value(session, "initial_reset_state"),
    ) or "Initial 与 Reset 恢复全部已确认默认值并清除临时记录"
    desktop_xr = _text(
        _latest_value(session, "desktop_interaction_plan"),
    ) or "桌面鼠标操作与 XR 射线/抓取逐项对应"
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
        else "按测量契约生成带单位坐标轴、固定图例和统一量程的结果图"
    )
    return {
        "contract_version": IMPLEMENTATION_DEFAULTS_VERSION,
        "scene": (
            f"实验“{title}”输出到 Assets/Scenes/{lab_id}.unity；场景层级固定为 "
            "Environment、XR、Experiment、Systems、UI，不依赖隐藏模板启动实验。"
        ),
        "navigation": (
            "采用 Start -> Lab -> Back 页面流；Lab 页面固定提供 Capture、Restore、Reset。"
            "Back 只返回 Start，Reset 恢复基准并清除临时快照，Restore 恢复最近一次 Capture。"
        ),
        "ui_regions": (
            "Game View 固定保留 Instruction、Experiment、Parameters、Status、Results 五个区域；"
            f"Parameters 显示：{parameters}；Results 显示：{measurements}。"
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
        "initial_reset": initial_reset,
        "logging_help_language": (
            "界面语言默认 English；状态栏记录参数变更、计算完成、Capture、Restore、Reset 与无效条件；"
            "键鼠和 XR 关键操作提供上下文帮助，错误提示必须说明原因及恢复动作。"
        ),
        "desktop_xr_mapping": desktop_xr,
        **construction_defaults(lab_id),
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
