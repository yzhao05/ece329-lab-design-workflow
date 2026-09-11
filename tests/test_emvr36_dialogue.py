"""Feedback emvr36: whole-turn edits, historical labels and useful replies."""
from copy import deepcopy

import pytest

from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import resolved_intent, UserIntent, current_pending_action
from ece329_workflow.emvr_design import apply_emvr_field_updates, merge_emvr_structured_requirements
from ece329_workflow.engine import WorkflowEngine, _reconcile_explicit_emvr_edits
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import Stage, WorkflowStatus, StepOutput
from ece329_workflow.reporting import effective_emvr_stage_payload
from tools.export_emvr34_regression import completed_magnetic_engine


@pytest.fixture(scope='module')
def complete():
    return completed_magnetic_engine()[1]


def prepare(complete, stage=Stage.LEARNING_OBJECTIVES):
    s = deepcopy(complete)
    s.status = WorkflowStatus.ACTIVE
    s.current_stage_index = list(Stage).index(stage)
    s.model_context['dialogue_state'] = {'pending_action': {
        'type':'CONFIRM_STAGE_OR_MODIFY', 'interaction_state':'EMVR_DIRECT',
        'subject':stage.value, 'question':'确认当前草稿吗？', 'advance_on_accept':True,
        'allowed_intents':[intent.value for intent in UserIntent],
    }}
    e = WorkflowEngine(generator=RuleBasedStageGenerator())
    e.store.save(s)
    return e,s


def fields(s):
    return merge_emvr_structured_requirements(s.design_context['emvr_design'])


def test_move_then_replace_uses_old_source_and_writes_both_fields(complete):
    e,s = prepare(complete)
    old = fields(s)['research_object']
    r = e.process_turn(s.design_id, {'message':'核心操作没有针对性，把目前研究对象的内容移到核心操作那里去，把研究对象换为更简洁的“载流导线”'})
    stored = e.store.get(s.design_id)
    assert fields(stored)['research_object'] == '载流导线'
    assert old in '；'.join(fields(stored)['required_behaviors'])
    assert stored.current_stage is Stage.LEARNING_OBJECTIVES
    assert '核心操作' in r['assistant_message'] or '交互' in r['assistant_message']


def test_partial_model_batch_cannot_suppress_explicit_other_edits(complete):
    _,s = prepare(complete)
    pending = current_pending_action(s)
    intent = resolved_intent(UserIntent.MODIFY_PREVIOUS_PROPOSAL, dialogue_acts=[
        {'type':'MODIFY_EMVR_FIELD','target':'research_object','operation':'REPLACE','content':'载流导线','confidence':1.0}
    ], actions_authoritative=True)
    message = '把目前研究对象的内容移到核心操作那里去，把研究对象换为更简洁的“载流导线”'
    result = _reconcile_explicit_emvr_edits(s, message, pending, intent)
    assert {a['target'] for a in result['dialogue_acts']} >= {'required_behaviors','research_object'}


def test_wrong_current_stage_binding_is_corrected_in_actual_engine_turn(complete):
    e,s = prepare(complete)
    before = fields(s)
    text = '调节电流并保存快照'
    class MisbindingGenerator(RuleBasedStageGenerator):
        calls = 0
        def resolve_intent(self, session, message, pending, context):
            self.calls += 1
            return resolved_intent(UserIntent.MODIFY_PREVIOUS_PROPOSAL, dialogue_acts=[
                {'type':'MODIFY_EMVR_FIELD','target':'vr_interaction_objective','operation':'REPLACE','content':text,'confidence':1.0}
            ],actions_authoritative=True)
    e.generator = MisbindingGenerator()
    e.process_turn(s.design_id,{'message':f'之前的可用交互改为“{text}”'})
    after = fields(e.store.get(s.design_id))
    assert e.generator.calls == 1
    assert after['required_behaviors'] == [text]
    assert after.get('vr_interaction_objective') == before.get('vr_interaction_objective')


def test_negated_quoted_edit_is_not_recovered_as_an_instruction():
    from ece329_workflow.emvr_design import recover_explicit_emvr_edits
    assert not recover_explicit_emvr_edits('不要把可用交互改为“拖动线圈”')
    assert not recover_explicit_emvr_edits('不要把研究问题改为“只比较电流大小”')


def test_reference_uses_public_formula_names_without_changing_ids(complete):
    e,s = prepare(complete)
    class ReferenceGenerator(RuleBasedStageGenerator):
        def resolve_intent(self, session, message, pending, context):
            return resolved_intent(UserIntent.REQUEST_MORE_EXAMPLES, target='conceptual_objective', confidence=1.0,
                                   source='SEMANTIC_TEST', dialogue_acts=[{'type':'REQUEST_REFERENCE','target':'conceptual_objective',
                                   'operation':'REPLACE','content':message,'confidence':1.0}], actions_authoritative=True)
        def generate(self, session, message):
            return StepOutput(assistant_message='参考 FD12_MAGNETIC_SOURCE_FIELD，先比较稳恒电流产生的磁场方向。',
                              stage_payload={'reference_examples':['固定路径，比较不同电流。']})
    e.generator = ReferenceGenerator()
    r = e.process_turn(s.design_id, {'message':'给我一个参考'})
    assert 'FD12_' not in r['assistant_message']
    assert '稳恒电流产生的磁场' in r['assistant_message']
    assert fields(e.store.get(s.design_id))['research_object'] == fields(s)['research_object']


def test_cross_stage_interaction_edit_does_not_change_learning_objectives(complete):
    e,s = prepare(complete)
    before = fields(s)
    interaction = '调节电流大小与方向；切换载流路径形状；保存快照并比较不同配置'
    e.process_turn(s.design_id, {'message':f'之前的可用交互改为“{interaction}”'})
    after = fields(e.store.get(s.design_id))
    assert interaction in '；'.join(after['required_behaviors'])
    for field in ('conceptual_objective','vr_interaction_objective','learning_objectives'):
        assert after.get(field) == before.get(field)
    payload = effective_emvr_stage_payload(e.store.get(s.design_id), Stage.IDEA_BRAINSTORMING)
    assert interaction in str(payload['possible_vr_interactions'])


def test_abstract_feedback_produces_question_instead_of_saving_instruction(complete):
    e,s = prepare(complete, Stage.RESEARCH_QUESTION)
    e.process_turn(s.design_id, {'message':'条件端和响应端准确，但问题主线还需要明确比较关系'})
    question = fields(e.store.get(s.design_id))['research_question']
    assert '需要明确比较关系' not in question
    assert '电流' in question and ('比较' in question or '变化' in question)


def test_visual_deletion_propagates_to_old_and_later_report_sections(complete):
    e,s = prepare(complete, Stage.THEORETICAL_FRAMEWORK)
    apply_emvr_field_updates(s.design_context['emvr_design'], {'field_updates':[
        {'field_id':'visualization_requirements','operation':'REPLACE','value':['方向箭头','波前或场线动画','颜色强度映射']}
    ]})
    e.store.save(s)
    e.process_turn(s.design_id, {'message':'教学可视化中的‘波前’不适用于稳恒电流磁场，去掉'})
    stored = e.store.get(s.design_id)
    assert '波前' not in str(fields(stored)['visualization_requirements'])
    for stage,field in ((Stage.THEORETICAL_FRAMEWORK,'visual_only_elements'),
                        (Stage.CONCEPTUAL_OR_VR_SETUP,'visualization_layer'),
                        (Stage.EXPECTED_DATA_VISUALIZATION,'student_visualization_requirements')):
        payload = effective_emvr_stage_payload(stored,stage)
        assert '场线动画' in str(payload[field])
        assert '波前' not in str(payload[field])


def test_stage_interaction_write_updates_canonical_early_report(complete):
    _,s = prepare(complete)
    apply_stage_field_updates(s,[{'field':'interactions','operation':'REPLACE','value':'保存四组快照并比较方向'}],stage=Stage.LEARNING_OBJECTIVES)
    assert '保存四组快照并比较方向' in str(fields(s)['required_behaviors'])


def test_report_question_list_is_an_answer_without_second_adoption(complete):
    e,s = prepare(complete, Stage.RESULT_INTERPRETATION)
    s.model_context['dialogue_state']['pending_action'] = {
        'type':'ANSWER_EMVR_STAGE_QUESTION','interaction_state':'EMVR_DIRECT',
        'subject':'report_questions','answer_fields':['report_questions'],
        'question':'请给出实验报告问题。','advance_on_accept':False,
    }
    e.store.save(s)
    r = e.process_turn(s.design_id, {'message':'1，电流增大时场强如何变化？ 2，电流方向反转时磁场方向如何变化？ 3，如何解释对比快照？'})
    assert len(fields(e.store.get(s.design_id))['report_questions']) == 3
    assert '候选内容保留' not in r['assistant_message']


def test_each_user_question_is_answered_independently_and_partial_failure_stays_pending(complete):
    from ece329_workflow.engine import _generate_question_response
    _,s = prepare(complete)
    before = deepcopy(s.design_context)
    class Answerer:
        def __init__(self): self.calls = []
        def generate(self, session, message):
            self.calls.append(message)
            if '反转' in message:
                return StepOutput(assistant_message='暂时无法回答。',stage_payload={'request_response_incomplete':True})
            return StepOutput(assistant_message='在固定几何下磁场与电流成正比。',stage_payload={})
    generator = Answerer()
    r = _generate_question_response(generator,s,{},['电流增大时呢？方向反转时呢？'])
    assert generator.calls == ['电流增大时呢？','方向反转时呢？']
    assert r.stage_payload['pending_student_questions'] == ['方向反转时呢？']
    assert 'answered_student_questions' not in r.stage_payload
    assert s.design_context == before


def test_same_default_contract_is_not_reprinted_but_changed_contract_is(complete):
    from ece329_workflow.engine import _default_proposal_text
    _,s = prepare(complete)
    s.history.append({'output':{'stage_payload':{'pending_action':{'default_proposal_value':'完整方案ABC'}}}})
    assert '完整方案ABC' not in _default_proposal_text(s, '完整方案ABC')
    assert '完整方案XYZ' in _default_proposal_text(s, '完整方案XYZ')


def test_geometry_followups_fill_one_gap_without_losing_prior_constants(complete):
    from ece329_workflow.builder_requirements import missing_builder_requirements, builder_requirement_values
    e,s = prepare(complete, Stage.VARIABLES_AND_CONDITIONS)
    apply_stage_field_updates(s,[
        {'field':'parameter_specifications','operation':'REPLACE','value':'电流滑块0.5~5A，默认1A，步长0.1A；路径默认直导线，选项直导线、圆环、螺线管。'},
        {'field':'model_constants_and_media','operation':'REPLACE','value':'真空mu_0=1.25663706212e-6 H/m，电流正向沿导线起点到终点，位置固定。'},
    ],stage=Stage.VARIABLES_AND_CONDITIONS)
    for label, answer in [('直导线长度','2 m'),('圆环半径','0.5 m'),('螺线管半径','0.2 m'),('螺线管长度','1 m'),('螺线管匝数','100')]:
        missing = next(x for x in missing_builder_requirements(s) if x['field']=='model_constants_and_media')
        assert label in missing['question']
        s.model_context['dialogue_state']['pending_action'] = {
            'type':'ANSWER_EMVR_STAGE_QUESTION','interaction_state':'EMVR_DIRECT',
            'subject':'model_constants_and_media','answer_fields':['model_constants_and_media'],
            'question':missing['question'], 'advance_on_accept':False,
        }
        e.store.save(s)
        e.process_turn(s.design_id,{'message':answer})
        s = e.store.get(s.design_id)
    assert not any(x['field']=='model_constants_and_media' for x in missing_builder_requirements(s))
    assert '1.25663706212e-6' in builder_requirement_values(s)['model_constants_and_media']


def test_fixed_magnetic_seeds_cannot_pass_density_growth_as_acceptance(complete):
    from ece329_workflow.builder_requirements import _cross_field_validation_error
    _,s = prepare(complete)
    values = {'numerical_model_specifications':'毕奥萨伐尔积分，RK4，场线种子点20个。',
              'acceptance_criteria':'电流增大时磁场线密度增加。'}
    assert '固定种子' in _cross_field_validation_error(s,'acceptance_criteria',values)
    values['acceptance_criteria'] = '固定种子下不能要求场线密度增加，应比较方向和统一色标。'
    assert _cross_field_validation_error(s,'acceptance_criteria',values) is None


def test_geometry_validation_also_applies_to_a_subset_of_shapes(complete):
    from ece329_workflow.builder_requirements import _cross_field_validation_error
    _,s = prepare(complete)
    values = {'parameter_specifications':'路径选项圆环、螺线管，默认圆环。',
              'model_constants_and_media':'真空磁导率4pi*1e-7 H/m，位置固定。'}
    assert '圆环半径' in _cross_field_validation_error(s,'model_constants_and_media',values)
    values['model_constants_and_media'] += '圆环半径0.5m；螺线管半径0.2m、长度1m、匝数100。'
    assert _cross_field_validation_error(s,'model_constants_and_media',values) is None
