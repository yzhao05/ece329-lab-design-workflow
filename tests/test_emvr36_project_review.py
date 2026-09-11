"""Review regressions across request routing and Builder numerical handoff."""
from copy import deepcopy
import pytest

from ece329_workflow.dialogue_state import resolved_intent, UserIntent, current_pending_action
from ece329_workflow.emvr_design import recover_explicit_emvr_edits
from ece329_workflow.engine import _reconcile_explicit_emvr_edits, _explicit_emvr_contract_request
from ece329_workflow.builder_requirements import _cross_field_validation_error
from tests.test_emvr36_dialogue import prepare
from tools.export_emvr34_regression import completed_magnetic_engine


@pytest.fixture(scope='module')
def complete():
    return completed_magnetic_engine()[1]


def test_edit_must_preserve_reference_request_for_same_field(complete):
    _,s=prepare(complete)
    raw=resolved_intent(UserIntent.MODIFY_PREVIOUS_PROPOSAL,confidence=1.0,actions_authoritative=True,
        dialogue_acts=[{'type':'REQUEST_REFERENCE','target':'research_question','content':'修改后给个参考','confidence':1.0}])
    r=_reconcile_explicit_emvr_edits(s,'研究问题改为“比较电流反转后的方向”，修改后给个参考',current_pending_action(s),raw)
    assert any(a['type']=='REQUEST_REFERENCE' for a in r['dialogue_acts'])


@pytest.mark.parametrize('text',[
    '请问可用交互改为“拖动线圈”合适吗？',
    '请问研究问题改为“比较电流反向后的变化”合适吗？',
    '教学可视化中的“波前”不要直接删除。',
])
def test_discussion_or_negated_deletion_is_not_an_edit(text):
    assert not recover_explicit_emvr_edits(text,{'visualization_requirements':['波前','箭头']})


def test_two_explicit_replacements_use_final_value():
    r=recover_explicit_emvr_edits('研究对象改为“圆环”；研究对象改为“螺线管”')
    assert r['research_object']['value']=='螺线管'


@pytest.mark.parametrize('constant',['圆环半径0m','圆环半径-0.5m','圆环半径0.5','圆环半径2A','圆环半径（米）2A'])
def test_invalid_geometry_is_not_builder_ready(complete,constant):
    _,s=prepare(complete)
    values={'parameter_specifications':'路径选项圆环，默认圆环。','model_constants_and_media':constant}
    assert _cross_field_validation_error(s,'model_constants_and_media',values)


def test_equivalent_endpoints_do_not_define_a_wire():
    from ece329_workflow.numerical_contract import geometry_gap
    assert geometry_gap('路径选项直导线','直导线端点(0,0,0) m 和 (0.0,0,0) m')


def test_bad_short_geometry_answer_does_not_write_zero(complete):
    _,s=prepare(complete)
    pending={'type':'ANSWER_EMVR_STAGE_QUESTION','interaction_state':'EMVR_DIRECT','subject':'model_constants_and_media',
             'answer_fields':['model_constants_and_media'],'question':'载流路径选项中尚未定义圆环半径（米）；固定对象也需要明确几何输入。'}
    intent=_explicit_emvr_contract_request(s,'0',pending)
    assert not intent.get('dialogue_acts')


def test_count_only_biot_contract_is_not_implementable(complete):
    _,s=prepare(complete)
    numerical='毕奥萨伐尔直接积分，RK4；计算域3m，种子20个，步长0.02m，容差1e-5，最大5000步，排除0.05m，超界停止。'
    assert _cross_field_validation_error(s,'numerical_model_specifications',{'numerical_model_specifications':numerical})


def test_pdf_validation_also_rejects_missing_source_discretization():
    from tools.export_emvr35_review import completed_ring_engine
    from ece329_workflow.builder_input import build_builder_gate1_input, validate_builder_gate1_input
    _,s=completed_ring_engine()
    payload=build_builder_gate1_input(s)
    payload['physics']['numerical_model']='毕奥萨伐尔积分，RK4；20种子，步长0.02m，容差1e-5，最多5000步。'
    with pytest.raises(ValueError,match='not reproducible'):
        validate_builder_gate1_input(payload)


def test_builder_method_uses_final_scope_and_preserves_unit_vectors():
    from tools.export_emvr35_review import completed_ring_engine
    from ece329_workflow.builder_input import build_builder_gate1_input
    from ece329_workflow.reporting import _formula_expression_for_report
    _,s=completed_ring_engine()
    payload=build_builder_gate1_input(s)
    formula=payload['formula_driven_experiment']
    summary=formula['selected_methods'][0]['process_summary']
    assert 'Student tasks' in summary and '中心B与场线方向' in summary
    assert '磁场环流' not in summary
    assert 'r_hat' in formula['formula_contracts'][0]['expression']
    assert _formula_expression_for_report('x̂ + ŷ + ẑ')=='x_hat + y_hat + z_hat'


def test_question_batch_is_bounded_and_can_resume(complete):
    from ece329_workflow.engine import _generate_question_response
    from ece329_workflow.models import StepOutput, TurnRequest
    e,s=prepare(complete)
    class Answerer:
        def __init__(self): self.calls=0
        def generate(self,session,message):
            self.calls+=1
            return StepOutput(assistant_message='已按当前课程关系解释。')
    generator=Answerer()
    questions=[f'第{i}种比较为什么成立？' for i in range(20)]
    original={'keep':'outer context'}
    s.turn_context=deepcopy(original)
    result=_generate_question_response(generator,s,{},questions)
    assert generator.calls==16
    assert result.stage_payload['pending_student_questions']==questions[16:]
    assert s.turn_context==original
    s.history.append({'output':result.to_dict()})
    intent,_=e._resolve_turn_intent(s,TurnRequest(message='继续回答剩余问题'),'继续回答剩余问题')
    assert intent['semantic_updates']['student_questions']==questions[16:]
    followup=_generate_question_response(generator,s,{},intent['semantic_updates']['student_questions'])
    assert followup.stage_payload['answered_student_questions']==questions[16:]
    assert generator.calls==20


def test_legacy_unresolved_item_survives_explicit_edit(complete):
    _,s=prepare(complete)
    intent=resolved_intent(UserIntent.MODIFY_PREVIOUS_PROPOSAL,confidence=1.0,
        semantic_updates={'unresolved_content':['还要加一个未定义的特殊效果'],'control_actions':['REQUEST_SUMMARY']})
    r=_reconcile_explicit_emvr_edits(s,'研究对象改为“圆环”，还要加一个未定义的特殊效果',current_pending_action(s),intent)
    assert r['semantic_updates']['unresolved_content']
    assert 'REQUEST_SUMMARY' in r['semantic_updates']['control_actions']
