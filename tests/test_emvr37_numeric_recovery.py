"""Replay emvr37(1): additive repairs, adoptable references, clean chat text."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import subprocess

import pytest

from ece329_workflow.builder_defaults import measurement_is_qualitative_only
from ece329_workflow.builder_references import builder_field_reference
from ece329_workflow.builder_requirements import builder_requirement_values, missing_builder_requirements, numerical_tolerance_defined
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import current_pending_action
from ece329_workflow.engine import _explicit_emvr_contract_request
from ece329_workflow.models import Stage
from ece329_workflow.numerical_contract import biot_numerical_gap
from tests.test_emvr37_recovery import setup
from tools.export_emvr34_regression import completed_magnetic_engine

FIELD = 'numerical_model_specifications'
INITIAL = ('数值算法：毕奥-萨伐尔定律直接数值积分，场线积分用RK4。'
           '计算域：以导线为中心、边长3米的立方体，坐标单位米，B单位特斯拉。'
           '采样网格0.05米，场线种子点20个。步长0.02米。误差/收敛容差1e-5。'
           '最大步数5000步。奇点处理：距导线0.05米内停止计算，超出边界自动终止。'
           '刷新时机：OnSimulationParameterChanged触发重新计算。')
SEGMENTS = '源路径离散化：直导线沿长度分200段，每段用中点求值；圆环按角度分200段，每段用中点；螺线管每匝分20段，共400段，每段用中点。'
SEEDS = '种子坐标p(i,j,k)=(-1.35+0.3*i,-1.35+0.3*j,-1.35+0.3*k) m，i,j,k=0..9，按字典序遍历1000点取距源大于0.05m的前20个；不足则报告布局不兼容。'
ZERO = '边界补充：I=0为有效零场，|B|<1e-12 T不归一化并隐藏箭头。'


@pytest.fixture(scope='module')
def base():
    return completed_magnetic_engine()[1]


def pending(s, field=FIELD):
    s.model_context['dialogue_state'] = {'pending_action': {
        'type':'ANSWER_EMVR_STAGE_QUESTION', 'interaction_state':'EMVR_DIRECT',
        'subject':field, 'answer_fields':[field], 'question':'请补充当前缺项。'}}


def numeric_session(base):
    e,s = setup(base, Stage.EXPECTED_DATA_VISUALIZATION)
    apply_stage_field_updates(s, [{'field':FIELD, 'operation':'REPLACE', 'value':INITIAL}], stage=s.current_stage)
    pending(s)
    e.store.save(s)
    return e,s


@pytest.mark.parametrize('source', [
    SEGMENTS, '分段数：直导线200段，圆环200段，螺线管400段；每段取中点计算。',
    '源路径分段512段，按中点求积。', '固定512段直线近似，每段中点计算。',
    '源路径段长设为0.01m，每段中点计算。', '源路径段长0.01 m，以0.005 m复算，每段中点求积。',
])
def test_source_discretization_variants_are_understood(source):
    assert biot_numerical_gap(INITIAL + source + SEEDS + ZERO) is None


@pytest.mark.parametrize('source', [
    '直导线0段，中点求积。', '直导线-200段，中点求积。',
    '直导线0.5段，中点求积。', '源路径段长0m，中点求积。',
    '源路径段长-0.01m，中点求积。', '场线步长0.02m，中点求积。',
])
def test_non_source_steps_and_invalid_source_values_still_fail(source):
    assert '源路径' in biot_numerical_gap(INITIAL + source + SEEDS + ZERO)


def test_supplements_progress_without_erasing_answered_parts(base):
    e,s = numeric_session(base)
    for answer, next_gap in [(SEGMENTS, '种子位置'), (SEEDS, '零场'), (ZERO, None)]:
        response = e.process_turn(s.design_id, {'message':answer})
        stored = e.store.get(s.design_id)
        value = builder_requirement_values(stored)[FIELD]
        assert INITIAL in value
        assert SEGMENTS in value
        gap = biot_numerical_gap(value)
        assert (next_gap in gap) if next_gap else gap is None
        assert not response['stage_payload'].get('reference_draft')
        assert '已保存' in response['assistant_message']
        assert '下面定义理论输出' not in response['assistant_message']
        if next_gap:
            assert current_pending_action(stored)['subject'] == FIELD
    assert FIELD not in {x['field'] for x in missing_builder_requirements(stored)}


def test_repeating_a_supplement_is_idempotent(base):
    e,s = numeric_session(base)
    for _ in range(2):
        e.process_turn(s.design_id, {'message':SEGMENTS})
    value = builder_requirement_values(e.store.get(s.design_id))[FIELD]
    assert value.count(SEGMENTS) == 1
    assert INITIAL in value


def test_quadrature_without_digits_is_a_real_answer(base):
    e,s = numeric_session(base)
    e.process_turn(s.design_id, {'message':'求积规则：每段取中点计算。'})
    value = builder_requirement_values(e.store.get(s.design_id))[FIELD]
    assert INITIAL in value and '每段取中点' in value


def test_explicit_whole_replacement_remains_possible(base):
    e,s = numeric_session(base)
    new = INITIAL.replace('5000步','6000步') + SEGMENTS + SEEDS + ZERO
    e.process_turn(s.design_id, {'message':'完整替换：' + new})
    value = builder_requirement_values(e.store.get(s.design_id))[FIELD]
    assert '6000步' in value and '5000步' not in value


@pytest.mark.parametrize('field, answer', [
    ('parameter_specifications', '电流大小：滑块，0.5~5A，默认1A，步长0.1A。电流方向：切换按钮，默认正向，选项正向/反向。载流路径形状：下拉菜单，默认直导线，选项直导线、圆环、螺线管。均可在桌面和VR端调节。'),
    ('model_constants_and_media', '载流路径几何（固定）：直导线：长度2米，沿z轴放置。圆环：半径0.3米，位于xy平面。螺线管：半径0.2米，长度1米，匝数20匝。固定参数：真空磁导率 μ₀ = 4π×10⁻⁷ H/m，固定。介质真空。'),
])
def test_complete_contract_answer_is_written_without_extra_candidate_confirmation(base, field, answer):
    e,s = setup(base, Stage.VARIABLES_AND_CONDITIONS)
    pending(s,field); e.store.save(s)
    e.process_turn(s.design_id, {'message':answer})
    assert builder_requirement_values(e.store.get(s.design_id))[field] == answer


@pytest.mark.parametrize('message', ['给我个完整的参考', '源路径分200段是否合适？', '现在在问什么问题？', '不要修改，误差1e-5是否足够？', '源路径分200段够吗', '误差1e-5？'])
def test_questions_are_not_committed_as_numeric_answers(base,message):
    _,s = numeric_session(base)
    assert _explicit_emvr_contract_request(s,message,current_pending_action(s)) is None


@pytest.mark.parametrize('message', ['给我个完整的参考', '请提供一份详细参考方案', '给我个参考'])
def test_complete_reference_is_read_only_then_adoptable(base, message):
    e,s = numeric_session(base)
    before = deepcopy(s.design_context['emvr_design']['field_state'])
    before_value = builder_requirement_values(s)[FIELD]
    response = e.process_turn(s.design_id, {'message':message})
    stored = e.store.get(s.design_id)
    assert stored.design_context['emvr_design']['field_state'] == before
    assert builder_requirement_values(stored)[FIELD] == before_value
    assert response['stage_payload']['reference_draft']['field'] == FIELD
    assert current_pending_action(stored)['candidate_binding_authorized']
    candidate = builder_field_reference(stored, FIELD)
    assert candidate['adoptable'] and biot_numerical_gap(candidate['value']) is None
    assert '螺旋绕线本身不闭合' in candidate['value']
    e.process_turn(s.design_id, {'message':'采用'})
    stored = e.store.get(s.design_id)
    assert FIELD not in {x['field'] for x in missing_builder_requirements(stored)}
    assert '源路径段长设为0.01 m' in builder_requirement_values(stored)[FIELD]


def test_numeric_shortcut_does_not_swallow_other_field_edits(base):
    _,s = numeric_session(base)
    message = '源路径直导线分200段，中点求积；研究对象改为“载流导线”'
    assert _explicit_emvr_contract_request(s,message,current_pending_action(s)) is None


def test_incomplete_online_numeric_reference_is_replaced_before_adoption(base):
    from ece329_workflow.generator import RuleBasedStageGenerator
    from ece329_workflow.models import StepOutput
    e,s = numeric_session(base)
    class IncompleteReference(RuleBasedStageGenerator):
        def generate(self, session, message):
            return StepOutput(assistant_message='数值计算参考：' + INITIAL,
                              stage_payload={'reference_draft':{'field':FIELD,'value':INITIAL}})
    e.generator = IncompleteReference()
    response = e.process_turn(s.design_id, {'message':'给我个完整的参考'})
    candidate = response['stage_payload']['reference_draft']['value']
    assert biot_numerical_gap(candidate) is None
    assert builder_requirement_values(e.store.get(s.design_id))[FIELD] == INITIAL


def test_plain_error_bound_and_qualitative_wording_from_feedback():
    assert numerical_tolerance_defined('算法：毕奥-萨伐尔数值积分，场线用RK4。误差1e-5。最大步数5000步。')
    assert not numerical_tolerance_defined('误差未知，只有最大步数5000步。')
    assert measurement_is_qualitative_only('界面数值：电流大小（单位A）、电流方向、载流路径形状。纵轴指标：磁场线方向和环绕性，定性指标，由毕奥-萨伐尔定律数值积分生成场线，通过方向箭头和场线分布显示。空间探针：不设置可移动探针，不需要单点读数。')
    assert not measurement_is_qualitative_only('定性指标，同时显示Bz分量读数，单位T。')


def test_browser_composition_has_no_leading_blank_lines_or_duplicate_errors():
    node = shutil.which('node')
    bundled = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    if not node and bundled.exists():
        node = str(bundled)
    if not node:
        pytest.skip('Node is required to execute the browser composition regression')
    app = (Path(__file__).parents[1]/'docs/assets/app.js').read_text(encoding='utf-8')
    function = app[app.index('function composeAssistantText('):app.index('function deriveQuickActions(')]
    script = function + '''
const state = {mode: 'EMVR_DIRECT', stageIndex: 2};
const responses = [
 {assistant_message:' \\n\\n', student_task:'请核对参数。'},
 {assistant_message:'还需明确种子。已有内容保留。', student_task:'还需明确种子。已有内容保留。', completion_error:'还需明确种子。'},
 {assistant_message:'第一行\\n第二行', warnings:['提示内容','提示内容',' ']}
];
process.stdout.write(JSON.stringify(responses.map(composeAssistantText)));
'''
    result = subprocess.run([node,'-e',script], capture_output=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == ['请核对参数。', '还需明确种子。已有内容保留。', '第一行\n第二行\n\n提示：提示内容']
