"""Implementation guidance for selected course formulas, not new physics inputs.

The numerical contract owns geometry, values and tolerances. These notes expose
the extra decisions required by integral/differential relations rather than
pretending that an equation alone specifies a runnable model.
"""
from __future__ import annotations

from .models import DesignSession


FORMULA_IMPLEMENTATION_NOTES = {
    "biot_savart": (
        "在物理坐标中定义有向载流路径 r'(u)、电流正向及闭合回流路径；有限线段不能冒充无限长导线。"
        "按已确认分段与求积规则累计 I*dl×(r-r')/|r-r'|^3，并乘公式系数；"
        "采样到任意导线段的最短距离小于排除半径时标记无效。用加倍分段复算检查数值容差；"
        "可用圆环中心 B=mu*I/(2R) 或相同有限几何的解析结果作独立基准。"
    ),
    "ampere_integral": (
        "明确闭合环路、积分方向与穿过曲面的有向电流；仅在已证明对称性使切向H恒定时由环流解H。"
        "否则将环流作为对计算场的验证关系，不能用 I/周长 代替任意位置的局部场。"
    ),
    "ampere_magnetostatic": "明确稳恒电流和材料条件；旋度关系是场方程或校验关系，单凭该式不能给出唯一空间场，还需源和边界条件。",
    "long_solenoid_field": (
        "轴向单位矢量在右手物理坐标中定义；n=N/length，H=n*I*axis，B=mu*H。"
        "确认匝数密度或匝数/长度、材料磁导率、带符号电流和采样位置。"
        "直接求值无需积分；只支持足够长螺线管内部的近似均匀场。"
        "端部与外部读数标为模型范围外，不能用此式画出有限线圈回流场或断言外场严格为零。"
        "基准检查 I=0 时B=0、反向电流使B反向、固定n与mu时电流翻倍使B翻倍。"
    ),
    "current_sheet_magnetic_field": (
        "定义无限电流面、正法向和面电流矢量及两侧区域，按课程公式分别求两侧场。"
        "面上不使用未定义的单侧值；有限可视面片只代表理想无限面的一部分，不能制造有限面边缘效应。"
    ),
    "linear_magnetic_medium": "明确使用绝对mu还是mu_r*mu_0，B与H分别保留T和A/m单位；线性各向同性近似不包含饱和、磁滞或各向异性。",
    "magnetic_vector_potential": "明确电流分布、材料、规范及边界；选定积分核与求积网格后计算A，再用已确认差分/解析导数求curl(A)。用独立B结果检验，不能把A的箭头直接标成B。",
    "vector_potential_poisson": "泊松方程需要源J、区域边界、边界条件和规范。明确离散算子、求解器、残差定义、容差及最大迭代；缺任何一项就返回数值契约补全，不能无限迭代或默设零边界。",
    "faraday_differential": "明确时变B、时间函数和适用边界；curl(E)=-dB/dt本身不足以确定唯一E，还需边界/势条件或已确认对称性。只研究回路电动势时不虚构完整E空间场。",
    "faraday_generalized_emf": (
        "定义有向闭合回路、曲面法向和测量参考系；磁通Phi=integral(B dot n dA)。"
        "固定回路用已确认时间函数的解析导数，或有步长/误差/端点规则的差分计算-dPhi/dt。"
        "运动回路使用同一运动几何的总磁通导数，或计算(E+v×B)环流，不能两次累加运动项。"
        "验证固定回路恒定磁通时EMF=0、反转法向时符号反转。"
    ),
    "lorentz_force": "使用带符号电荷、同一参考系中的速度和E/B，在右手物理坐标中计算q*(E+v×B)；若模拟轨迹，另需质量、初态、时间积分及误差契约。验证纯磁力与速度垂直。",
    "inductance_definition": "明确磁通链N*Phi及有向电流；I=0时不直接计算N*Phi/I，用已确认线性模型的L或斜率。非线性材料不能默用常数L。",
    "solenoid_inductance": "确认N、截面积、长度、磁导率和长螺线管近似；分别保留N与n=N/length，不能混用。只计算已选定的L/磁通链，不自动增加电阻或瞬态实验。",
    "rl_decay": "确认无源RL自然响应、R>0、L>0、I(0)及t>=0；直接计算指数衰减无需迭代。时间域、采样间隔和Reset时间原点采用已确认契约。",
    "magnetic_energy": "明确总能量与能量密度的区别、积分区域和线性材料条件；总能量保留J，密度保留J/m^3，不能将局部密度直接当作总能量。",
}


def selected_physics_guidance(session: DesignSession) -> str:
    emvr = session.design_context.get("emvr_design", {})
    emvr = emvr if isinstance(emvr, dict) else {}
    flow = emvr.get("formula_flow", {})
    flow = flow if isinstance(flow, dict) else {}
    selection = flow.get("formula_selection", {})
    selection = selection if isinstance(selection, dict) else {}
    brief = emvr.get("authoritative_experiment_brief", {})
    brief = brief if isinstance(brief, dict) else {}
    ids = []
    for key in ("primary_formula_ids", "supporting_formula_ids"):
        ids.extend(brief.get(key) or emvr.get("selected_" + key) or selection.get(key) or [])
    notes = [
        "逐条建立所选公式ID -> 本实验使用角色（求值/约束/验算） -> SI输入及来源字段 -> 输出及采样区域 -> 独立基准的对应表。"
        "数值、几何、边界和容差以已确认字段为准；以下是实现检查要求，不批准新参数。"
        "缺少求解所必需的输入时返回对应设计字段补全，不能用可视模型尺寸或公式目录示例代填。"
    ]
    notes.extend(f"{formula_id}: {FORMULA_IMPLEMENTATION_NOTES[formula_id]}"
                 for formula_id in dict.fromkeys(ids) if formula_id in FORMULA_IMPLEMENTATION_NOTES)
    return "\n".join(notes)
