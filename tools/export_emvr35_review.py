"""Complete synthetic Biot-Savart/RK4 handoff; no user approval or Unity run implied."""
from math import cos, sin, pi, sqrt
from pathlib import Path
import json

from ece329_workflow.builder_defaults import build_implementation_defaults, format_implementation_defaults, record_implementation_defaults_approval
from ece329_workflow.builder_input import build_builder_gate1_input
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import UserIntent, resolved_intent
from ece329_workflow.emvr_formula_flow import handle_emvr_formula_turn, recover_topic_analysis_from_knowledge
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage
from tools.export_emvr34_regression import VALUES as SHARED_VALUES, formula_action


MU = 1.25663706212e-6
RADIUS = 0.5
TOPIC = '闭合圆环稳恒电流的磁场：用毕奥萨伐尔积分与RK4观察电流大小和正负方向'
NUMERICAL = (
    '物理右手SI坐标，圆环r(u)=(0.5*cos(u),0.5*sin(u),0) m，u从0到2*pi，正I沿u递增，闭合源无需另加回流线。'
    '固定512段直线近似，dl为相邻节点差，在每段中点累加mu_0*I/(4*pi)*dl cross (r-mid)/|r-mid|^3。'
    '显示与采样域[-1.5,1.5]^3 m，所有源距按到线段的最短距离检查。固定中心读数(0,0,0) m。'
    '20个种子：phi_k=2*pi*(k+0.5)/20，k=0..19，seed_k=(0.5+0.08*cos(phi_k),0,0.08*sin(phi_k)) m。'
    '每个种子仅沿+B/|B|方向用RK4积分，固定物理弧长步长0.02 m，每条最多5000步；轨迹有方向箭头。'
    '每个RK4中间采样均检查距源0.03 m排除半径、出界、NaN/Infinity与|B|<1e-12 T，命中则终止当前线且记录原因。'
    '闭合判据：已走弧长至少0.2 m，距起点小于0.01 m，单位切向点积大于0.99；未满足不能称闭合。'
    'I=0时输出零场并隐藏箭头，不执行方向归一化或生成场线；仍是有效物理状态。'
    '验收用1024源段重算；同一有效采样点B误差容差max(1e-9 T,1%*|B_ref|)。'
    '验收轨迹以0.01 m步长、每条最多10000步复算一次，对共同有效弧长插值比较，位置偏差小于0.001 m；'
    '两条线终止种类不一致则报告精度不足。达到步数上限标截断，超差不自动继续减半。'
    '预算分别计入512/1024源段、20种子、RK4四次场求值、主轨迹与一次精度复算；每帧4096源段贡献或4 ms，保留游标，检查取消。'
    '参数变化或Reset递增revision并取消旧任务，只提交当前revision；运行时主轨迹完成再开放Capture，精度复算只按明确验收操作执行。'
    '独立解析基准：圆环轴上Bz=mu_0*I*R^2/(2*(R^2+z^2)^(3/2))，Bx=By=0；'
    'R=0.5 m、I=1 A、z=0时Bz=1.25663706212e-6 T，负I反号，I=0为0。'
)
VALUES = {
    **SHARED_VALUES,
    'lab_title':'Circular Current Magnetic Field - Synthetic Review',
    'lab_id':'ece329_biot_savart_review',
    'desktop_interaction_plan':'鼠标或XR射线操作同一有向电流滑块及Capture、Restore、Reset、Back，固定圆环不拖动；两端共用参数处理器。',
    'initial_reset_state':'Initial电流1 A、固定闭合圆环与20种子，中心读数和场线按数值契约重算；快照为空。Reset恢复1 A并清除领域快照列表，取消旧revision；Restore仅恢复最近快照中的输入，不清除记录，完整导入后重算一次，不新增快照。',
    'parameter_specifications':'唯一自变量有向电流I，通过鼠标和XR滑块可调，最小-2 A、最大2 A、默认1 A、步长0.1 A；四组比较为1 A、2 A、-1 A、0 A。圆环位置、方向、半径与种子不作为控件。',
    'model_constants_and_media':'真空mu_0=1.25663706212e-6 H/m；R=0.5 m；圆环位于物理xy平面且中心为原点，正I从+z看逆时针。数值排除半径0.03 m；20个确定种子，位置公式见数值契约。导线可视半径0.01 m仅作显示，不能充当排除半径或进入公式。固定色标0..2e-5 T，超限标裁切。',
    'measurement_specifications':'固定中心采样显示Bx、By、Bz、|B|，单位T；场线方向和环绕性通过RK4轨迹与箭头定性观察，不能将密度当强度。颜色按|B|和固定色标映射。电流变化后刷新；空间探针不适用，固定中心无需移动探针；相位与频率不适用。只用快照比较，不生成定量曲线。',
    'numerical_model_specifications':NUMERICAL,
    'independent_variable':'带符号圆环电流I，单位A',
    'observations':'中心Bz与|B|、磁场线方向和环绕性',
    'controlled_conditions':'真空磁导率、圆环几何、512源段、种子与步长、色标和观察视角固定',
    'research_hypothesis':'固定圆环路径时B与I线性相关；电流反向使B反向，正比例变大不会改变归一化场方向。',
    'expected_trend':'中心Bz随I线性变化；正比例电流变化保持非零场线几何，反向改变箭头和积分方向，0 A没有方向箭头。',
    'limiting_cases':'-2 A、0 A、2 A；源排除区、计算域边界和最大积分步数；不把截断或种子稀疏造成的空白称为物理零场。',
    'physical_mechanism':'沿闭合圆环累计毕奥萨伐尔磁场，再用归一化场方向积分空间场线，稳恒场不添加感应电场。',
    'simulation_inputs':'滑块I，固定mu_0与有向源节点，数值契约的采样点、种子、步长、排除半径和容差。',
    'calculated_outputs':'B矢量T、中心读数、物理弧长轨迹与终止原因、有效性。',
    'physics_layer':'纯物理坐标逐段计算B；场线按RK4有界积分，不使用显示尺寸计算物理距离。',
    'visualization_layer':'固定圆环、20种子的磁场线与方向箭头、|B|颜色；中心数值面板和快照，不生成曲线。',
    'measurement_interface':'中心B分量和大小以T显示；Results列出实际输入、计算状态、轨迹终止原因和快照。',
    'unity_objects':'固定闭合圆环、磁场线与方向箭头、电流滑块、中心结果面板、快照控制按钮',
    'reference_condition':'I=1 A，中心Bz=1.25663706212e-6 T，固定其余条件。',
    'procedure_steps':[
        'Start加载1 A基准，等待计算完成，核对中心Bz并Capture保存基准。',
        '把电流设为2 A，等待计算完成，Capture保存中心读数和场线显示。',
        '把电流设为-1 A，等待计算完成，Capture保存方向反转结果。',
        '把电流设为0 A，核对零场与箭头隐藏，Capture保存零场状态。',
        '并排比较四组兼容快照，解释带符号线性关系及场线显示局限。',
        'Restore恢复最近快照，核对完整输入和结果一致，不要Capture。',
        'Reset恢复1 A并清除记录，核对Restore已禁用；Back返回Start。',
    ],
    'comparison_logic':'仅改变I，对1、2、-1、0 A分别比较中心读数和整体场线方向，固定几何、种子、步长、色标。',
    'visualization_plan':'20种子RK4场线、中心T单位读数与四组快照；无定量曲线。',
    'trend_annotation':'固定种子时场线数量不表示场强，比较中心数值和统一颜色；截断只描述有限绘图域。',
    'unity_update_event':'滑块事件递增revision并触发有界计算；完成后刷新，只有显式Capture才保存领域记录。',
    'expected_results':'1 A时中心Bz=1.25663706212e-6 T；2 A为2.51327412424e-6 T；-1 A为-1.25663706212e-6 T；0 A为0 T。固定正比例电流下不要求场线数量增加。',
    'acceptance_criteria':'完成四组快照、Restore和Reset；中心解析基准满足1e-9 T绝对容差，源段与轨迹复算满足数值契约；方向正确，截断标记完整，Reset后旧快照不能恢复。',
    'report_questions':'用毕奥萨伐尔定律解释电流翻倍与反向；为什么固定种子线条数量不能代表场强；如何区分源排除区、域截断和零场。',
    'result_interpretation':'以中心解析基准和快照解释B与带符号I的线性关系，区分场线方向、采样策略与强度。',
    'if_prediction_supported':'独立基准和复算均通过时支持所选薄圆环稳恒磁场模型。',
    'limitations':'真空薄导线、稳恒闭合电流；不包含感应、磁滞或导线内部场；有限绘图域与排除区限制可显示范围。',
    'conceptual_feasibility':'闭合圆环源和有限种子数可按有界工作单元实现桌面与XR等效操作。',
    'teaching_value':'建立有向电流、毕奥萨伐尔矢量积分与磁场线的联系。',
    'vr_added_value':'从圆环平面两侧观察电流绕行方向与磁场箭头的空间对应。',
}


def circle_field(z: float, segments: int = 512) -> tuple[float, float, float]:
    """Independent finite CPU check of the numerical contract, for I=1 A."""
    result = [0.0, 0.0, 0.0]
    for k in range(segments):
        a, b = 2*pi*k/segments, 2*pi*(k+1)/segments
        p, q = (RADIUS*cos(a), RADIUS*sin(a)), (RADIUS*cos(b), RADIUS*sin(b))
        dx, dy = q[0]-p[0], q[1]-p[1]
        rx, ry = -(p[0]+q[0])/2, -(p[1]+q[1])/2
        scale = MU/(4*pi)/(rx*rx+ry*ry+z*z)**1.5
        for j, value in enumerate((dy*z, -dx*z, dx*ry-dy*rx)):
            result[j] += scale*value
    return tuple(result)


def completed_ring_engine():
    s = DesignSession(design_id='emvr35_biot_savart_review', interaction_state=InteractionState.EMVR_DIRECT)
    analysis = recover_topic_analysis_from_knowledge(TOPIC)
    handle_emvr_formula_turn(s, TOPIC, formula_action('SET_EMVR_TOPIC', analysis))
    out,_ = handle_emvr_formula_turn(s, '采用闭合圆环的毕奥萨伐尔积分', formula_action('SELECT_EMVR_FORMULAS', {
        'primary_profile_ids':['FD12_MAGNETIC_SOURCE_FIELD'], 'primary_formula_ids':['biot_savart'],
        'supporting_formula_ids':[], 'objects':['固定闭合圆环'], 'changed_quantities':['有向电流I'],
        'observed_quantities':['中心B与场线方向'], 'operations':VALUES['procedure_steps'],
        'comparison_cases':['1 A','2 A','-1 A','0 A'], 'boundary_conditions':[VALUES['limitations']]}))
    out,_ = handle_emvr_formula_turn(s, '组合', resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), selected_option_id='emvr-composition:combined')
    handle_emvr_formula_turn(s, '采用方法', resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), selected_option_id=out.stage_payload['experiment_methods'][0]['option_id'])
    _,locked = handle_emvr_formula_turn(s, '确认方向', resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), complete_stage=True)
    assert locked
    apply_stage_field_updates(s,[{'field':key,'operation':'REPLACE','value':value} for key,value in VALUES.items()],stage=Stage.DESIGN_VALUE_AND_LIMITATIONS)
    contract = format_implementation_defaults(build_implementation_defaults(s))
    apply_stage_field_updates(s,[{'field':'implementation_defaults','operation':'REPLACE','value':contract}],stage=Stage.DESIGN_VALUE_AND_LIMITATIONS)
    record_implementation_defaults_approval(s,source='SYNTHETIC_REVIEW')
    g=RuleBasedStageGenerator()
    for index,stage in enumerate(Stage):
        s.current_stage_index=index
        s.stage_outputs[stage.value]=g.generate(s,'合成回归字段已提供').to_dict()
    e=WorkflowEngine(generator=g);e.store.save(s)
    result=e.process_turn(s.design_id,{'message':'继续','complete_stage':True})
    assert result['workflow_status']=='complete',result.get('completion_error') or result['assistant_message'][:1200]
    return e,e.store.get(s.design_id)


def main():
    for z in (0.0,0.5):
        actual=circle_field(z)
        expected=MU*RADIUS**2/(2*(RADIUS**2+z*z)**1.5)
        assert abs(actual[2]-expected)<1e-9 and max(abs(actual[0]),abs(actual[1]))<1e-12
    e,s=completed_ring_engine()
    out=Path('output/pdf');out.mkdir(parents=True,exist_ok=True)
    target=out/'emvr35-biot-savart-builder-review.pdf'
    target.write_bytes(e.render_builder_input_pdf(s.design_id))
    scratch=Path('build/emvr35-review');scratch.mkdir(parents=True,exist_ok=True)
    (scratch/'builder-input.json').write_text(json.dumps(build_builder_gate1_input(s),ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{target}: synthetic handoff and independent axial checks passed; no Unity execution.')


if __name__=='__main__':
    main()
