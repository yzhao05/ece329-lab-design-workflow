"""References bound to the pending contract field, separate from acceptance."""
from __future__ import annotations

from typing import Any
import re

from .builder_requirements import BUILDER_REQUIREMENT_SPECS, builder_requirement_values
from .builder_defaults import project_derived_contract_text
from .models import DesignSession


def builder_field_reference(session: DesignSession, field: str) -> dict[str, Any] | None:
    spec = next((item for item in BUILDER_REQUIREMENT_SPECS if item["field"] == field), None)
    if spec is None or field == "implementation_defaults":
        return None
    values = builder_requirement_values(session)
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    flow = emvr.get("formula_flow", {})
    selection = flow.get("formula_selection", {}) if isinstance(flow, dict) else {}
    selection = selection if isinstance(selection, dict) else {}
    formula_ids = set(emvr.get("selected_primary_formula_ids", []) or [])
    formula_ids.update(selection.get("primary", []) or [])
    formula_ids.update(selection.get("primary_formula_ids", []) or [])
    brief = emvr.get("authoritative_experiment_brief", {})
    if isinstance(brief, dict):
        formula_ids.update(brief.get("primary_formula_ids", []) or [])
    point_charge = "coulomb_point_charge" in formula_ids
    magnetic_source = "biot_savart" in formula_ids
    current = values.get(field, "")
    adoptable = True
    if field == "model_constants_and_media" and point_charge:
        # Preserve the student's fixed inputs. Only fill an absent numerical
        # protection with a visibly proposed value; never commit this here.
        value = current or (
            "固定真空介电常数 ε₀=8.8541878128e-12 F/m，介质为真空，εr=1（无量纲）；"
            "建议各点电荷量绝对值固定为1e-9 C，符号由已确认的极性配置决定；"
            "比较组保持电荷量大小、介质和场线种子数一致，以隔离自变量的影响。"
        )
        if not re.search(r"排除半径[^。；;\n]{0,30}\d", value):
            value += (
                "\n建议排除半径为0.05 m：仅用于停止靠近理想点电荷奇点的采样和场线积分，"
                "不是电荷的物理尺寸。需保证它小于最小电荷间距，种子放在排除区外。"
            )
    elif field == "numerical_model_specifications" and point_charge:
        constants = values.get("model_constants_and_media", "")
        # Refer to existing constants by ownership instead of silently choosing
        # a second exclusion radius or number of field lines.
        radius = "采用固定输入契约的排除半径" if "排除半径" in constants else "建议排除半径0.05 m"
        seeds = ("沿用固定输入契约的场线种子数" if any(x in constants for x in ("种子", "条/电荷"))
                 else "建议每个正电荷20个均匀球面种子")
        value = (
            "建议数值方案（可修改）：\n"
            "1. 在SI物理坐标中直接计算各点电荷库仑场并矢量求和，显示域建议为[-5,5]³ m。\n"
            f"2. 场线沿 E/|E| 以RK4积分；{seeds}；种子球半径取排除半径的1.2倍，"
            "过滤落入其他源排除区或显示域外的种子。\n"
            "3. 建议步长0.05 m，每条最多500步；用0.025 m复算同一种子，"
            "按相同弧长位置比较轨迹，位置偏差容差建议0.01 m；超差则标记精度不足。\n"
            f"4. {radius}；进入排除区、离开显示域、|E|<1e-9 V/m或达到步数上限时停止，"
            "遇到NaN/Infinity标为无效；步数上限终止需标记截断，不能称已收敛。\n"
            "5. 参数变更后取消旧计算并按最新状态刷新；比较组固定种子、步长、视角及色标。"
        )
    elif field == "measurement_specifications" and magnetic_source:
        value = (
            "建议测量方案（可修改）：\n"
            "1. 参数面板显示当前电流大小I（A）、电流方向和载流路径选项；读取已确认控件状态，操作后刷新。\n"
            "2. 磁场线方向取当前B矢量，环绕性用同一视角下的空间形态比较，不设置虚构的数值纵轴。"
            "场线积分参数只是绘图条件；固定种子时线条数量不表示磁场强度。\n"
            "3. 如保留强度颜色，颜色按|B|（T）与共同色标映射；不以曲线疏密代替数值计算。"
            "零场时不显示方向箭头，并标记无定义方向。\n"
            "4. 建议仅观察整体场线，不额外添加可移动探针；若已确认使用探针，则沿用已有采样位置与B读数。"
            "稳恒磁场的相位、频率不适用。比较记录保存实际输入、显示配置和有效性。"
        )
        if current:
            value = f"当前已记录：{current}\n参考补充（需选择实际采用项）：\n" + value
            adoptable = False
    elif field == "numerical_model_specifications" and magnetic_source:
        value = (
            "建议计算方案（这些数值是可修改的建议，不是既有决定）：\n"
            "1. 在右手SI坐标中按已确认有向路径分段，逐元累加毕奥-萨伐尔场；"
            "段长建议0.01 m，并以0.005 m复算；有限导线需明确回流路径，不能把有限线段当无限长导线。\n"
            "2. 场线按B/|B|用RK4积分，显示域建议[-1.5,1.5]^3 m，步长0.02 m，每条最多5000步；"
            "采用20个确定性种子，按实际路径表面外侧布置并固定保存种子坐标，比较组使用同一规则。\n"
            "3. 精度参考：固定采样位置比较两种电流元段长所得B，误差容差为max(1e-9 T, 1%*|B_ref|)；"
            "场线以0.01 m步长复算，比较同弧长位置，位置偏差容差建议0.001 m。近零场不使用相对误差除零。\n"
            "4. 建议距任意导线段0.05 m内停止积分（数值排除区，不是导线物理半径）；"
            "|B|<1e-12 T、出界、NaN/Infinity、闭合轨迹回到起点邻域或最大步数均终止。"
            "最大步数截断不等于收敛；新参数取消旧任务，不自动反复重算。\n"
            "还需根据本实验固定输入确认：各路径的有向几何与回流、实际种子位置、排除区是否与最小几何尺度兼容。"
            "已有确认值优先，不用上述建议覆盖它们。"
        )
        if current:
            value = f"当前已记录：{current}\n可用于补足缺项的参考：\n" + value
        adoptable = False
    elif field == "expected_results" and magnetic_source:
        value = (
            "建议预期结果：\n"
            "1. 固定路径几何与介质时，B与带符号电流I成正比；正比例增加电流不会改变B/|B|的方向。"
            "因此固定种子和积分规则时，不应要求场线数量或环绕范围必然增大；可用共同色标显示场强变化。\n"
            "2. 相同几何下电流反向，B方向与环绕箭头反向，场线轨迹形状保持一致。\n"
            "3. 仅比较已选路径：直导线的对称区域呈环绕形态，圆环磁场穿过环面并回绕，"
            "长螺线管内部近似沿轴向均匀；有限几何的边缘与外部场必须由实际路径计算。\n"
            "通过同一视角、种子、色标和输入快照比较上述结果；绘制疏密与空白不作为物理强度或零场的直接证据。"
        )
    elif field == "acceptance_criteria":
        value = (
            "完成条件：覆盖参数契约已定义的边界、默认状态和全部离散比较选项；"
            "每次只改变待比较条件，在相同视角与显示尺度下记录观察结果；"
            "至少保存一组有效的对照快照，并完成一次 Initial、操作、Capture、Restore、Reset 循环。\n"
            "通过条件：快照包含实际参数、配置与有效性；学生能指出至少一项可观察差异，"
            "用本实验已确认公式解释，并区分物理结果、显示编码与模型适用边界。"
            "未满足时提示缺少的证据，不把文字预测自动当成通过结果。"
        )
    elif field == "report_questions":
        value = (
            "1. 哪个条件发生变化、其他条件如何保持一致？请引用已保存快照中的实际参数。\n"
            "2. 各比较配置呈现哪些观察差异？请用已确认的核心公式解释至少一项。\n"
            "3. 哪些结果来自理论计算、哪些仅是视觉编码？模型边界或显示尺度会怎样影响判断？"
        )
    elif field == "initial_reset_state" and current:
        value = project_derived_contract_text(session, current)
    else:
        # No formula-specific numerical model is available. An honest scaffold
        # stays non-adoptable instead of masquerading as a completed contract.
        value = (f"已记录：{current}\n" if current else "") + str(spec["question"])
        adoptable = False
    return {"field": field, "label": str(spec["label"]), "value": value, "adoptable": adoptable}
