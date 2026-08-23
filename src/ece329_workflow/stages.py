from __future__ import annotations

from dataclasses import dataclass

from .models import InteractionState, Stage


@dataclass(frozen=True, slots=True)
class StageDefinition:
    number: int
    stage: Stage
    title_zh: str
    goal_zh: str
    guided_rule: str
    emvr_rule: str


@dataclass(frozen=True, slots=True)
class IdeaDevelopmentFacet:
    facet_id: str
    stage: Stage
    title_zh: str


IDEA_DEVELOPMENT_STAGES: tuple[Stage, ...] = (
    Stage.IDEA_BRAINSTORMING,
    Stage.COURSE_MAPPING_AND_DIRECTION,
    Stage.LEARNING_OBJECTIVES,
    Stage.RESEARCH_QUESTION,
    Stage.THEORETICAL_FRAMEWORK,
    Stage.HYPOTHESIS,
    Stage.CONCEPTUAL_OR_VR_SETUP,
)
IDEA_DEVELOPMENT_TITLE = "实验想法完善"
PUBLIC_STAGE_COUNT = 1 + len(Stage) - len(IDEA_DEVELOPMENT_STAGES)

IDEA_DEVELOPMENT_FACETS: tuple[IdeaDevelopmentFacet, ...] = (
    IdeaDevelopmentFacet("direction_outline", Stage.IDEA_BRAINSTORMING, "实验现象与大纲雏形"),
    IdeaDevelopmentFacet("course_mapping", Stage.COURSE_MAPPING_AND_DIRECTION, "课程映射"),
    IdeaDevelopmentFacet("learning_objective", Stage.LEARNING_OBJECTIVES, "学习目标"),
    IdeaDevelopmentFacet("research_question", Stage.RESEARCH_QUESTION, "研究问题"),
    IdeaDevelopmentFacet("theoretical_framework", Stage.THEORETICAL_FRAMEWORK, "理论依据"),
    IdeaDevelopmentFacet("hypothesis", Stage.HYPOTHESIS, "假设与预期趋势"),
    IdeaDevelopmentFacet("conceptual_structure", Stage.CONCEPTUAL_OR_VR_SETUP, "概念实验结构"),
)


STAGE_DEFINITIONS: tuple[StageDefinition, ...] = (
    StageDefinition(
        1,
        Stage.IDEA_BRAINSTORMING,
        "想法探索与大纲雏形",
        "帮助学生发散、比较和完善想法，并形成可按缺口继续补充的实验大纲雏形。",
        "先询问学生想研究主题与哪类现象或概念的关系，并用课程资料支持的例子帮助发散；收敛时整理核心现象、课程关系、基本对照和观察重点，形成实验大纲雏形；不提前确定具体变量、公式、研究问题或实验结构；每次只给学生一个任务。",
        "直接整理用户原始想法、目标现象、可用的VR交互机会和必要假设。",
    ),
    StageDefinition(
        2,
        Stage.COURSE_MAPPING_AND_DIRECTION,
        "课程映射",
        "展示已经确定的实验方向与ECE329知识点之间的联系。",
        "承接想法探索中已经确定的方向，由助手展示主要课程支点、辅助关系和映射理由；不得重新列出候选方向让学生选择，只请学生检查是否准确或指出遗漏。",
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

_GUIDED_STAGE_TITLES: dict[Stage, str] = {
    Stage.CONCEPTUAL_OR_VR_SETUP: "概念实验结构",
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "学生总结",
}

_EMVR_STAGE_TITLES: dict[Stage, str] = {
    Stage.CONCEPTUAL_OR_VR_SETUP: "Unity VR模拟实验设计",
    Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "EMVR方案汇总",
}


def stage_title(
    stage: Stage,
    interaction_state: InteractionState = InteractionState.GUIDED_DESIGN,
) -> str:
    if interaction_state is InteractionState.EMVR_DIRECT:
        return _EMVR_STAGE_TITLES.get(stage, STAGES_BY_ID[stage].title_zh)
    return _GUIDED_STAGE_TITLES.get(stage, STAGES_BY_ID[stage].title_zh)


def public_stage_catalog(
    interaction_state: InteractionState = InteractionState.GUIDED_DESIGN,
) -> list[dict[str, object]]:
    return [
        {
            "number": item.number,
            "id": item.stage.value,
            "title": stage_title(item.stage, interaction_state),
            "goal": item.goal_zh,
            **stage_group_metadata(item.stage, interaction_state),
        }
        for item in STAGE_DEFINITIONS
    ]


def stage_group_metadata(
    stage: Stage,
    interaction_state: InteractionState = InteractionState.GUIDED_DESIGN,
) -> dict[str, object]:
    definition = STAGES_BY_ID[stage]
    if stage in IDEA_DEVELOPMENT_STAGES:
        facet = IDEA_DEVELOPMENT_FACETS[IDEA_DEVELOPMENT_STAGES.index(stage)]
        return {
            "workflow_stage_number": 1,
            "workflow_stage_count": PUBLIC_STAGE_COUNT,
            "workflow_stage_title": IDEA_DEVELOPMENT_TITLE,
            "substep_number": None,
            "substep_count": None,
            "substep_title": None,
            "idea_component_id": facet.facet_id,
            "idea_component_title": facet.title_zh,
        }
    return {
        "workflow_stage_number": definition.number - len(IDEA_DEVELOPMENT_STAGES) + 1,
        "workflow_stage_count": PUBLIC_STAGE_COUNT,
        "workflow_stage_title": stage_title(stage, interaction_state),
        "substep_number": None,
        "substep_count": None,
        "substep_title": None,
        "idea_component_id": None,
        "idea_component_title": None,
    }
