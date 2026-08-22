from __future__ import annotations

from typing import Any, Protocol

from .guardrails import (
    BREADTH_EXPLORATION,
    COURSE_CONTENT,
    DEPTH_EXPANSION,
    INTEREST_DESCRIPTION,
    OUT_OF_SCOPE,
    UNREASONABLE_REQUEST,
    classify_stage_one_input,
    course_example_options,
    is_no_direction_request,
)
from .knowledge_base import KNOWLEDGE
from .models import DesignSession, InteractionState, Stage, StepOutput


class StageGenerator(Protocol):
    def generate(self, session: DesignSession, user_message: str) -> StepOutput: ...


def _idea(session: DesignSession, user_message: str) -> str:
    idea_context = session.design_context.get("idea", {})
    if isinstance(idea_context, dict):
        for key in ("current_focus", "current_summary", "main_direction"):
            value = idea_context.get(key)
            if value:
                return str(value)
    control_messages = {"继续", "进入下一阶段", "确认本阶段并进入下一阶段"}
    if (
        session.current_stage is Stage.IDEA_BRAINSTORMING
        and user_message.strip()
        and user_message.strip() not in control_messages
    ):
        return user_message.strip()
    if isinstance(idea_context, dict) and idea_context.get("original"):
        return str(idea_context["original"])
    for item in reversed(session.history):
        if item.get("user_message"):
            return str(item["user_message"])
    return "尚未明确的ECE329实验想法"


def _topic_options(text: str) -> list[dict[str, Any]]:
    return KNOWLEDGE.brainstorm_options(text)


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


def _scene_components(direction: str, index: int) -> tuple[str, str, str, str]:
    if "散度" in direction or "通量" in direction:
        return (
            "用透明曲面包住看不见的源",
            "想象一个带电分布周围悬浮着许多场箭头，再用大小和形状不同的透明闭合面"
            "把它包住。你既能贴近每一点观察场向外发散的程度，也能退后看穿过整个曲面"
            "的场线总效果；当曲面被拉长、压扁，却仍包住同样的源时，两种观察会怎样呼应？",
            "局部看到的源强特征，怎样共同组成闭合面上的整体效果？",
            "可以把闭合面做成偏心、凹陷或穿过材料界面的形状，作为超出标准例题的联想。",
        )
    if "旋度" in direction or "环流" in direction:
        return (
            "沿一圈小路追踪场的转向",
            "想象在空间中放入许多大小不同的透明闭合路径，并沿每条路径逐段观察场箭头"
            "是顺着前进方向、逆着前进方向，还是近乎垂直。局部看似轻微的旋转趋势，"
            "沿整圈累积后会不会显现出明显差别？当路径移到另一个区域时，画面又如何改变？",
            "你更想解释局部的旋转感，还是它沿整条闭合路径累积后的整体差异？",
            "可以让路径变形、偏离对称中心或同时环绕多个来源，看看直觉是否仍成立。",
        )
    if "驻波" in direction or "共振" in direction:
        return (
            "节点与波腹沿线路浮现",
            "想象一列波沿着传输线前进，又有一列波从末端返回；把两者同时冻结在空间中，"
            "有些位置几乎不动，有些位置反复达到很强的响应。若末端状态或线路尺度换一种"
            "情形，这些节点、波腹与重复图样会整体移动，还是以另一种方式重排？",
            "这种空间图样最让你想追问的是节点的位置、图样的重复性，还是共振的形成？",
            "可以把均匀线路改成带转折或分段结构，设想原有驻波图样会如何被重新塑造。",
        )
    if "匹配" in direction or "功率反射" in direction:
        return (
            "让末端从像墙一样反弹到平顺接续",
            "想象同一个波包连续遇到几种不同的线路末端：有的像硬墙一样把明显的波送回来，"
            "有的只留下很弱的回波，有的则让能量继续向前。把这些画面并排比较时，末端状态、"
            "反射强弱与能量传递之间会呈现怎样的联系？",
            "你最想理解的是回波为什么变弱，还是能量为什么能更顺利地继续传递？",
            "可以设想用多段渐变结构代替单一末端，观察反射是否会分散成更复杂的图样。",
        )
    if "反射" in direction or "暂态" in direction:
        return (
            "追着一个脉冲看它到达边界之后",
            "想象一个短脉冲沿传输线向前奔跑，到达末端后出现返回的波，并在途中与后续入射"
            "部分相遇。若把不同末端状态的时间画面并排播放，你会看到返回波的方向、形状和"
            "到达时刻怎样改变原来的信号图样？",
            "你更想追问边界为什么产生回波，还是来回传播怎样形成完整的暂态过程？",
            "可以加入第二个不连续处或支路，让同一个脉冲经历多次往返，形成更复杂的启发性画面。",
        )
    if "偏振" in direction or "正交" in direction:
        return (
            "看场箭头的尖端在空间中画轨迹",
            "想象固定在空间一点观察电场箭头：两个正交方向的分量一边振荡，一边保持某种"
            "相对节奏，箭头尖端可能画出直线、椭圆或旋转轨迹。把观察点沿传播方向移动时，"
            "这种轨迹与波的空间变化会怎样联系起来？",
            "哪种分量关系最能帮助你解释偏振轨迹为什么改变？",
            "可以让波经过一个倾斜结构或多层界面，设想输出轨迹出现怎样的新变化。",
        )
    if "磁通" in direction or "感应" in direction:
        return (
            "让穿过回路的磁场图样动起来",
            "想象一个线圈附近的磁场随时间增强、减弱，或让线圈与磁场来源发生相对运动。"
            "把穿过回路的磁场图样与回路中出现的电响应同时显示，你会看到变化的快慢、"
            "方向与响应方向之间产生怎样的对应？",
            "你最想解释的是磁场本身的变化，还是回路为何对这种变化作出响应？",
            "可以把单个回路换成不同形状或相互靠近的多个回路，比较感应图样是否仍直观。",
        )
    if "衰减" in direction or "穿透" in direction or "屏蔽" in direction:
        return (
            "跟随场进入材料并逐渐消退",
            "想象一列电磁波碰到一块材料：一部分在界面返回，另一部分进入内部，但颜色与"
            "箭头长度随深度逐渐变化。把不同材料或不同激励情形并排放置时，界面附近和材料"
            "深处的空间图样会出现怎样的反差？",
            "你更想解释界面处的分流，还是进入材料后的衰减与穿透？",
            "可以把材料做成薄层、弯曲外壳或带接缝结构，想象屏蔽与泄漏会形成什么画面。",
        )
    if "介质" in direction or "极化" in direction or "材料" in direction:
        return (
            "把同一物体换成不同材料",
            "想象两个外形完全相同的物体被放进同一外加场，其中一个表现得像导体，另一个"
            "是介质。把场线、等势面或材料内部的响应并排显示，物体内外的场会怎样重新分布，"
            "界面两侧又会出现怎样的方向反差？",
            "你最想理解材料内部的响应，还是材料界面怎样改变周围空间的场？",
            "可以给介质做分层、开孔或包覆结构，构造一个课堂公式未直接给出答案的设想。",
        )
    if "边界" in direction or "电势" in direction:
        return (
            "把平滑边界慢慢捏成尖角和窄缝",
            "想象一个规则导体边界逐渐被拉出尖角、凹槽或窄缝，同时另一个带电物体缓慢靠近。"
            "原本均匀或对称的场线和等势面会从哪里先变形，哪些位置会出现明显的聚集或疏散？",
            "哪一种边界形状最能触发你对场分布变化的直觉或疑问？",
            "可以把尖角放进介质外壳或带开口的金属结构中，把几何与材料图景组合起来。",
        )
    if any(keyword in direction for keyword in ("电荷", "电流分布", "场形状", "空间场")):
        return (
            "让两个场源从远处慢慢靠近",
            "想象先分别观察两个不同形状或方向的场源，再把它们逐渐移近。每个来源单独存在时"
            "清晰的对称性会怎样被另一个来源打破；空间中的场线、箭头方向和强弱区域会在哪里"
            "合并、偏转、抵消或形成新的结构？",
            "你最想追踪的是对称性被打破的过程，还是强场与弱场区域如何重新出现？",
            "可以把球形或线形来源换成带尖端、偏心或多部分结构，作为更有画面感的延伸。",
        )
    generic_frames = (
        (
            "让几何关系变得可见",
            "想象把两个相关对象从相距很远慢慢移到彼此附近，并从多个方向观察场或波的"
            "空间图样。原先规则的结构会在哪里先弯曲、聚集、抵消或重新排列？",
            "哪一种空间变化最违背你的第一直觉？",
            "可以把规则外形换成带尖角、弯折或窄缝的结构，作为进一步联想。",
        ),
        (
            "让材料与边界形成反差",
            "想象保持整体轮廓相近，却改变一个区域的材料或边界状态。从一侧走到另一侧时，"
            "场的方向、幅度或传播图样会呈现怎样的反差？",
            "哪一处边界变化最值得继续解释？",
            "可以设想分层材料或不完全封闭的边界，看看是否出现新的空间特征。",
        ),
        (
            "让多个来源在空间中相遇",
            "想象同时存在两个来源或两条传播路径，并改变它们的相对位置和朝向。在空间中"
            "移动观察点，哪里会出现增强、减弱、节点或方向突变？",
            "哪一种相互作用最能成为你自己的探索主线？",
            "可以加入第三个对象或不对称扰动，观察原有图样是否仍保持直观对称性。",
        ),
    )
    return generic_frames[index % len(generic_frames)]


def build_exploration_scenes(
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn catalog-grounded relationships into vivid but clearly scoped scenes."""

    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    scenes: list[dict[str, Any]] = []
    for index, option in enumerate(options):
        direction = str(option.get("direction") or "ECE329课程关系").strip()
        focus = _clean_focus_text(option.get("focus"))
        title, physical_frame, thinking_prompt, extension = _scene_components(
            direction,
            index,
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
    return "\n\n".join(blocks)


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
        options = course_example_options()
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
        options = _topic_options(idea)

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
                options = _topic_options(retrieval_text)
            no_direction = is_no_direction_request(user_message)
            if input_kind != COURSE_CONTENT:
                options = course_example_options()
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
                    "暂时没有具体方向也没关系。我们可以先从ECE329课上学习的"
                    "电磁场、电磁波和传输线中寻找你感兴趣的关系。"
                )
            elif input_kind == OUT_OF_SCOPE:
                introduction = (
                    "你提出的主题不属于ECE329课程的内容范围，"
                    "因此不适合作为这门课实验设计的核心。"
                    "ECE329主要学习电磁场、电磁波和传输线，你可以先参考下面三个例子。"
                )
            elif selected_option is not None:
                selected_direction = str(
                    selected_option.get("direction")
                    or selected_option.get("focus")
                    or "上一轮所选方向"
                )
                introduction = (
                    f"你已经把方向收到了“{selected_direction}”。我先不继续给你新的"
                    "选项，因为同一个方向对不同学生可能意味着完全不同的兴趣。你可以"
                    "描述让你注意到它的现象、你觉得最值得解释的联系，或者目前仍感到"
                    "疑惑的地方；不需要写成正式的实验问题。"
                )
            elif brainstorm_phase == INTEREST_DESCRIPTION:
                introduction = (
                    f"现在我们暂时把“{selected_focus or current_focus}”作为感兴趣的方向。"
                    "接下来先由你赋予这个方向更具体的含义，而不是继续从一组答案中选择。"
                    "你可以结合观察到的现象、直觉上的矛盾，或希望学生真正看懂的物理联系"
                    "来描述。"
                )
            elif brainstorm_phase == DEPTH_EXPANSION:
                related_directions = [
                    str(item.get("direction") or "").strip()
                    for item in deepening_connections[:3]
                    if str(item.get("direction") or "").strip()
                    and str(item.get("direction") or "").strip() != selected_focus
                ]
                connection_text = "、".join(related_directions[:2])
                related_sentence = (
                    f"同时，{connection_text}提供了与这条主线相邻的观察角度。"
                    if connection_text
                    else "它还可以和同一课程板块中的边界行为与空间分布联系起来。"
                )
                introduction = (
                    f"你刚才把关注点描述为：“{interest_description or user_message.strip()}”"
                    f"。从ECE329的概念联系看，可以把“{selected_focus or topic_anchor}”"
                    "作为这段想法的主线：重点不只是看到某个结果，而是理解不同场或波的"
                    "成分、边界行为与最终空间图样之间为什么会产生联系。"
                    f"{related_sentence}这样形成的内容已经比一个宽泛主题更深入，同时仍把"
                    "变量、公式和实验装置留给后续阶段。"
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
                    "请用自己的话描述：这个方向中什么现象或物理联系最吸引你，"
                    "以及你最希望进一步弄清什么？"
                )
            elif brainstorm_phase == DEPTH_EXPANSION and input_kind == COURSE_CONTENT:
                closing_task = (
                    "请继续用自己的话补充或修正这段理解；如果它已经准确表达你的想法，"
                    "也可以确认当前方向并进入下一阶段。"
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
                    "interest_description": interest_description,
                    "alternative_ideas": alternatives,
                    "exploration_scenes": exploration_scenes,
                    "deepening_connections": deepening_connections,
                    "course_source": KNOWLEDGE.source_reference,
                    "reference_sources": KNOWLEDGE.source_references,
                    "source_policy": KNOWLEDGE.supplemental_data["policy"][
                        "course_scope_rule"
                    ],
                    "ready_for_next_stage": ready_for_next_stage,
                },
                student_task=closing_task,
            )
        if stage is Stage.COURSE_MAPPING_AND_DIRECTION:
            topics = _course_topics(idea)
            return StepOutput(
                assistant_message="当前只判断这个想法与哪一类ECE329内容联系最紧密。",
                stage_payload={
                    "candidate_course_directions": topics,
                    "course_references": _course_references(idea),
                    "idea_reference": idea,
                },
                student_task="你希望把哪一个课程方向作为实验的主要理论核心？",
            )
        if stage is Stage.LEARNING_OBJECTIVES:
            return StepOutput(
                assistant_message="先确定学习目标的重点类型，不同时写完整目标列表。",
                stage_payload={"objective_types": ["概念理解", "定量计算", "结果解释"]},
                student_task="你最希望学生通过这个实验获得哪一种能力？",
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
                assistant_message="变量设计从自变量开始，本轮不同时填写其他变量。",
                stage_payload={"variable_type": "independent_variable"},
                student_task="请写出你准备主动改变的一个量，并说明它的合理范围。",
            )
        if stage is Stage.CONCEPTUAL_PROCEDURE:
            return StepOutput(
                assistant_message="流程设计先建立比较所需的基准条件。",
                stage_payload={"procedure_unit": "reference_condition"},
                student_task="在改变主要变量前，你会先建立什么基准状态？",
            )
        if stage is Stage.EXPECTED_DATA_VISUALIZATION:
            visualization = _visualization(idea, emvr=False)
            return StepOutput(
                assistant_message="这是预期数据窗口的结构草图；它只表示理论预测，不是实测数据。",
                stage_payload={"observation_focus": "relationship_shape"},
                student_task="根据你的假设，你认为理论曲线最可能呈现什么形状？",
                visualization=visualization,
            )
        if stage is Stage.RESULT_INTERPRETATION:
            return StepOutput(
                assistant_message="本轮只考虑一种与预测不一致的情况。",
                stage_payload={"result_case": "no_clear_change"},
                student_task="如果因变量没有随自变量明显变化，你认为最值得先检查哪一个理论假设？",
            )
        if stage is Stage.DESIGN_VALUE_AND_LIMITATIONS:
            return StepOutput(
                assistant_message="先从理论局限这一个角度反思设计。",
                stage_payload={"review_dimension": "model_limitation"},
                student_task="你的设计依赖的哪个理想化假设最可能限制结论？",
            )
        return StepOutput(
            assistant_message="最后由你自己完成总结；我会逐部分检查，不会替你生成整份方案。",
            stage_payload={
                "current_summary_section": "实验想法与设计动机",
                "final_proposal_generated": False,
            },
            student_task="请先用两到三句话总结实验想研究什么，以及为什么值得研究。",
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
