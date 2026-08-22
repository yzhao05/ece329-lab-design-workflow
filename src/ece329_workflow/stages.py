from __future__ import annotations

from dataclasses import dataclass

from .models import Stage


@dataclass(frozen=True, slots=True)
class StageDefinition:
    number: int
    stage: Stage
    title_zh: str
    goal_zh: str
    guided_rule: str
    emvr_rule: str


STAGE_DEFINITIONS: tuple[StageDefinition, ...] = (
    StageDefinition(
        1,
        Stage.IDEA_BRAINSTORMING,
        "实验想法探索与完善",
        "帮助学生发散、比较和完善自己的实验想法。",
        "停留在本阶段循环探索；每次只进行一种 brainstorming 活动，并只给学生一个任务。",
        "直接整理用户原始想法、目标现象、可用的VR交互机会和必要假设。",
    ),
    StageDefinition(
        2,
        Stage.COURSE_MAPPING_AND_DIRECTION,
        "ECE329课程映射与实验方向",
        "将想法关联到ECE329知识点并收敛为一个方向。",
        "最多给三个方向，让学生自己选择或修改；不要替学生决定。",
        "直接选择兼顾课程相关性、理论清晰度和VR表现力的方向。",
    ),
    StageDefinition(
        3,
        Stage.LEARNING_OBJECTIVES,
        "学习目标",
        "确定概念、计算和分析层面的学习目标。",
        "一次只引导学生确定一种学习目标。",
        "直接生成课程目标、计算目标、分析目标、交互目标和观察目标。",
    ),
    StageDefinition(
        4,
        Stage.RESEARCH_QUESTION,
        "研究问题",
        "形成一个范围有限、能够由设计回答的主要问题。",
        "一次只帮助学生判断一个核心量，不直接替学生写完整答案。",
        "直接生成适合Unity中调整参数和观察结果的研究问题。",
    ),
    StageDefinition(
        5,
        Stage.THEORETICAL_FRAMEWORK,
        "理论框架",
        "建立研究问题与ECE329理论的联系。",
        "一次只引导一个理论判断，例如公式、物理机制或假设。",
        "直接区分输入量、计算输出和仅用于教学的视觉元素。",
    ),
    StageDefinition(
        6,
        Stage.HYPOTHESIS,
        "假设与预期趋势",
        "依据理论预测变量变化趋势。",
        "让学生先判断一种趋势，再对推理提供反馈。",
        "直接生成假设、极端条件及Unity中的反馈方式。",
    ),
    StageDefinition(
        7,
        Stage.CONCEPTUAL_OR_VR_SETUP,
        "概念实验结构／Unity VR模拟设计",
        "组织实验模块；EMVR下完善Unity模拟、交互、计算和可视化设计。",
        "一次只引导一个实验模块判断，不直接生成完整装置。",
        "保留用户原始场景条件但不定义VR场景；不设计可访问性与舒适性；直接完善任务、Unity对象、交互、物理层、可视化、界面和内部状态。",
    ),
    StageDefinition(
        8,
        Stage.VARIABLES_AND_CONDITIONS,
        "变量与条件",
        "确定自变量、因变量、控制变量、基准与干扰因素。",
        "一次只处理一种变量。",
        "直接补全变量表以及变量在Unity中的控制和显示映射。",
    ),
    StageDefinition(
        9,
        Stage.CONCEPTUAL_PROCEDURE,
        "概念实验流程",
        "组织基准、参数改变、观察、记录和比较的逻辑。",
        "一次只引导一个流程单元。",
        "直接形成VR用户任务流，但不生成Unity代码。",
    ),
    StageDefinition(
        10,
        Stage.EXPECTED_DATA_VISUALIZATION,
        "预期数据可视化窗口",
        "在对话中提供理论预测可视化供学生参考。",
        "生成窗口后只提一个观察问题，不立即解释结论。",
        "直接生成带趋势标注的窗口和Unity联动建议。",
    ),
    StageDefinition(
        11,
        Stage.RESULT_INTERPRETATION,
        "可能结果及解释",
        "讨论不同结果的物理含义。",
        "每次只给出一种结果情形让学生解释。",
        "直接生成支持、相反、不明显、无结论和模型超限等解释。",
    ),
    StageDefinition(
        12,
        Stage.DESIGN_VALUE_AND_LIMITATIONS,
        "设计价值、可行性与局限性",
        "综合评价课程价值、可行性、理论局限、创新和VR附加价值。",
        "一次只从一个角度引导学生反思。",
        "直接评价设计，并指出可以简化或扩展的部分。",
    ),
    StageDefinition(
        13,
        Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        "学生总结／EMVR最终方案",
        "引导学生自己总结；EMVR状态下生成最终设计。",
        "绝不替学生生成最终方案；一次只让学生总结一个部分，并对学生草稿给一个关键反馈。",
        "直接生成完整的Unity VR模拟实验设计方案。",
    ),
)


STAGES_BY_ID = {definition.stage: definition for definition in STAGE_DEFINITIONS}


def public_stage_catalog() -> list[dict[str, object]]:
    return [
        {
            "number": item.number,
            "id": item.stage.value,
            "title": item.title_zh,
            "goal": item.goal_zh,
        }
        for item in STAGE_DEFINITIONS
    ]
