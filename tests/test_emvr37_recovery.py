"""Replay the distinct failures in emvr37 against real EMVR state updates."""
from copy import deepcopy
import pytest

from ece329_workflow.models import Stage
from ece329_workflow.emvr_design import apply_emvr_field_updates, merge_emvr_structured_requirements
from ece329_workflow.emvr_catalog import selection, recover_catalog_edits
from ece329_workflow.dialogue_state import current_pending_action, resolved_intent, UserIntent
from ece329_workflow.generator import RuleBasedStageGenerator, _emvr_reference_output
from ece329_workflow.reporting import effective_emvr_stage_payload
from ece329_workflow.knowledge_base import KNOWLEDGE
from tests.test_emvr36_dialogue import prepare
from tools.export_emvr34_regression import completed_magnetic_engine


@pytest.fixture(scope='module')
def base():
    return completed_magnetic_engine()[1]


def setup(base, stage=Stage.COURSE_MAPPING_AND_DIRECTION):
    e,s=prepare(base,stage)
    emvr=s.design_context['emvr_design']
    apply_emvr_field_updates(emvr,{'field_updates':[
        {'field_id':k,'operation':'REPLACE','value':v} for k,v in {
            'primary_formula_ids':['biot_savart','ampere_integral'],
            'supporting_formula_ids':['ampere_magnetostatic'],
            'course_relationship':'稳恒电流产生的磁场',
            'changed_quantities':['电流大小与方向','载流路径形状'],
            'observed_quantities':['磁场线方向和环绕性'],
        }.items()]})
    s.design_context['design_state']['course_relationship']='稳恒电流产生的磁场'
    # Legacy UI displayed all three formulas as core equations.
    s.stage_outputs[Stage.THEORETICAL_FRAMEWORK.value]['stage_payload']['core_equations']=[
        deepcopy(f) for fid in ['biot_savart','ampere_integral','ampere_magnetostatic']
        for f in KNOWLEDGE.formulas if f['id']==fid]
    s.history=[]
    e.store.save(s)
    return e,s


def test_mapping_headline_sources_and_chat_use_one_projection(base):
    e,s=setup(base)
    r=e.process_turn(s.design_id,{'message':'主要课程主题和课程讲义依据对不上，两者不是同一主题'})
    stored=e.store.get(s.design_id)
    p=effective_emvr_stage_payload(stored,Stage.COURSE_MAPPING_AND_DIRECTION)
    assert p['primary_topic']=="Magnetic force and fields and Ampere's law"
    assert [x['lecture'] for x in p['course_references']]==[12]
    assert 'Lecture 1｜' not in r['assistant_message']
    assert '物理模型、Unity交互还是展示方式' not in r['assistant_message']
    assert 'primary_course_concept_id' not in r['assistant_message']
    assert 'lecture_12' not in r['assistant_message']


def test_named_topic_edit_does_not_change_course_relationship(base):
    e,s=setup(base)
    class WrongBinding(RuleBasedStageGenerator):
        def resolve_intent(self,*args):
            return resolved_intent(UserIntent.MODIFY_PREVIOUS_PROPOSAL,actions_authoritative=True,dialogue_acts=[
                {'type':'MODIFY_DESIGN_FIELD','target':'course_relationship','operation':'REPLACE',
                 'content':"Magnetic force and fields and Ampere's law",'confidence':1.0}])
    e.generator=WrongBinding()
    e.process_turn(s.design_id,{'message':"不要删掉主要课程主题，改为与讲义依据一致的Magnetic force and fields and Ampere's law就行了"})
    stored=e.store.get(s.design_id)
    assert merge_emvr_structured_requirements(stored.design_context['emvr_design'])['course_relationship']=='稳恒电流产生的磁场'


def test_relationship_and_reference_limit_are_both_applied(base):
    e,s=setup(base)
    r=e.process_turn(s.design_id,{'message':'‘课程关系’改回‘稳恒电流产生的磁场’；讲义依据只保留Lecture 12一条'})
    emvr=e.store.get(s.design_id).design_context['emvr_design']
    assert emvr['field_state']['course_reference_ids']==['lecture_12']
    assert emvr['field_state']['course_relationship']=='稳恒电流产生的磁场'


def test_object_and_path_options_are_both_preserved(base):
    e,s=setup(base,Stage.LEARNING_OBJECTIVES)
    e.process_turn(s.design_id,{'message':'研究对象就写‘载流导线’，路径形状保留直导线、圆环、螺线管三种。'})
    fields=e.store.get(s.design_id).design_context['emvr_design']['field_state']
    assert fields['research_object']=='载流导线'
    assert fields['path_shape_options']==['直导线','圆环','螺线管']


def question_pending(s):
    s.model_context['dialogue_state']={'pending_action':{
        'type':'ANSWER_EMVR_STAGE_QUESTION','interaction_state':'EMVR_DIRECT',
        'subject':'research_question','answer_fields':['research_question'],'question':'请补齐研究问题。'}}


@pytest.mark.parametrize('message',['给我个参考','你的参考和我们现在的问题无关，你刚刚问的是关于补齐研究问题的'])
def test_reference_is_research_question_and_can_be_adopted(base,message):
    e,s=setup(base,Stage.RESEARCH_QUESTION)
    question_pending(s);e.store.save(s)
    before=deepcopy(s.design_context['emvr_design']['field_state'])
    r=e.process_turn(s.design_id,{'message':message})
    stored=e.store.get(s.design_id)
    assert stored.design_context['emvr_design']['field_state']==before
    assert '基准状态：' not in r['assistant_message']
    assert '电流大小与方向' in r['assistant_message'] and '什么差异？' in r['assistant_message']
    assert current_pending_action(stored)['candidate_binding_authorized'] is True
    assert r['stage_payload']['reference_draft']['field']=='research_question'
    e.process_turn(s.design_id,{'message':'采用'})
    assert '什么差异？' in merge_emvr_structured_requirements(e.store.get(s.design_id).design_context['emvr_design'])['research_question']


def test_core_and_support_are_separate_in_projection(base):
    _,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    p=effective_emvr_stage_payload(s,Stage.THEORETICAL_FRAMEWORK)
    assert [f['id'] for f in p['core_equations']]==['biot_savart','ampere_integral']
    assert [f['id'] for f in p['supporting_equations']]==['ampere_magnetostatic']


@pytest.mark.parametrize('message',[
    '可以，第三条放到辅助公式，核心公式保留前两条',
    '去掉核心公式里的第三条，把它放在支撑公式里，或是直接删掉',
])
def test_move_legacy_third_formula_keeps_theory_and_all_views(base,message):
    e,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    original=s.design_context['design_state'].get('theoretical_framework')
    e.process_turn(s.design_id,{'message':message})
    stored=e.store.get(s.design_id);emvr=stored.design_context['emvr_design']
    assert selection(emvr,'primary')==['biot_savart','ampere_integral']
    assert selection(emvr,'supporting')==['ampere_magnetostatic']
    assert emvr['authoritative_experiment_brief']['primary_formula_ids']==selection(emvr,'primary')
    assert stored.design_context['design_state'].get('theoretical_framework')==original


def test_delete_formula_is_not_literal_theory_text(base):
    e,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    e.process_turn(s.design_id,{'message':'删除核心公式的第三条'})
    stored=e.store.get(s.design_id);emvr=stored.design_context['emvr_design']
    assert selection(emvr,'supporting')==[]
    assert '删除核心公式' not in str(stored.design_context['design_state'].get('theoretical_framework'))
    assert 'ampere_magnetostatic' not in emvr['formula_flow']['experiment_brief']['supporting_formula_ids']
    assert 'Magnetostatic field equations' not in str(emvr['formula_flow']['experiment_methods'])


def test_explicit_core_list_does_not_overwrite_theory(base):
    e,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    original=s.design_context['design_state'].get('theoretical_framework')
    e.process_turn(s.design_id,{'message':"不要修改理论依据，把核心公式改为：1. Biot-Savart law：dB = mu_0 I dl x r_hat /(4*pi*r^2) 2. Ampere law - integral form：∮_C H dot dl = I_C"})
    stored=e.store.get(s.design_id)
    assert selection(stored.design_context['emvr_design'],'primary')==['biot_savart','ampere_integral']
    assert stored.design_context['design_state'].get('theoretical_framework')==original


def test_question_about_formula_role_does_not_mutate(base):
    _,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    assert not recover_catalog_edits(s,'核心公式的第三条能算作我们的核心公式吗？')


def test_magnetostatics_default_does_not_add_wavefront(base):
    _,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    r=RuleBasedStageGenerator().generate(s,'继续')
    assert '波前' not in str(r.stage_payload.get('visual_only_elements'))


def test_invalid_formula_index_cannot_be_written_as_theory(base):
    e,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    original=deepcopy(s.design_context['design_state'])
    r=e.process_turn(s.design_id,{'message':'删除核心公式的第九条','complete_stage':True})
    stored=e.store.get(s.design_id).design_context['design_state']
    assert {k:v for k,v in stored.items() if k!='pending_action'}=={k:v for k,v in original.items() if k!='pending_action'}
    assert '未能定位' in r['assistant_message']
    assert e.store.get(s.design_id).current_stage is Stage.THEORETICAL_FRAMEWORK


def test_method_generation_and_review_do_not_expand_student_scope():
    from ece329_workflow.emvr_formula_flow import _generate_experiment_methods, _format_selected_method_designs
    flow={'formula_selection':{'primary_profile_ids':['FD12_MAGNETIC_SOURCE_FIELD'],
          'primary_formula_ids':['biot_savart','ampere_integral'], 'supporting_formula_ids':[],
          'changed_quantities':['电流大小与方向','载流路径形状'], 'observed_quantities':['磁场线方向和环绕性']}}
    methods=_generate_experiment_methods(flow,strategy='COMBINED')
    assert methods
    assert '磁导率' not in str([m['changed_quantities'] for m in methods])
    flow['experiment_methods']=methods
    flow['experiment_brief']={'changed_quantities':['电流方向'],'observed_quantities':['磁场线方向']}
    text=_format_selected_method_designs(flow,[methods[0]['method_id']])
    assert '主动变化：电流方向' in text and '载流路径形状' not in text


def test_invalid_catalog_identifier_is_rejected_without_mutation():
    state={'field_state':{'course_reference_ids':['lecture_12']}}
    apply_emvr_field_updates(state,{'field_updates':[{'field_id':'course_reference_ids','operation':'REPLACE','value':['lecture_99']}]})
    assert state['field_state']['course_reference_ids']==['lecture_12']


def test_two_formula_roles_and_requested_order_are_preserved(base):
    _,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    edits=recover_catalog_edits(s,'核心公式改为：Ampere law - integral form、Biot-Savart law；辅助公式改为：Magnetostatic field equations')
    assert edits['primary_formula_ids']['value']==['ampere_integral','biot_savart']
    assert edits['supporting_formula_ids']['value']==['ampere_magnetostatic']


def test_online_reference_for_wrong_field_is_replaced(base):
    from ece329_workflow.models import StepOutput
    e,s=setup(base,Stage.RESEARCH_QUESTION)
    question_pending(s);e.store.save(s)
    class WrongReference(RuleBasedStageGenerator):
        def generate(self,session,message):
            return StepOutput(assistant_message='基准状态：先保存当前快照。操作与记录：逐项调节参数并保存。',
                stage_payload={'reference_draft':{'field':'procedure_steps','value':'先保存快照，再调节电流。'}})
    e.generator=WrongReference()
    r=e.process_turn(s.design_id,{'message':'给我个参考'})
    assert '基准状态' not in r['assistant_message']
    assert r['stage_payload']['reference_draft']['field']=='research_question'


def test_inapplicable_wavefront_edit_keeps_field_lines(base):
    e,s=setup(base,Stage.THEORETICAL_FRAMEWORK)
    s.design_context['emvr_design']['field_state']['visualization_requirements']=['方向箭头','波前或场线动画','颜色强度映射']
    e.store.save(s)
    e.process_turn(s.design_id,{'message':'教学可视化中的“波前”不适用于本实验'})
    values=merge_emvr_structured_requirements(e.store.get(s.design_id).design_context['emvr_design'])['visualization_requirements']
    assert '波前' not in str(values) and '场线动画' in str(values) and '方向箭头' in str(values)


def test_reference_feedback_does_not_swallow_a_named_edit(base):
    e,s=setup(base,Stage.RESEARCH_QUESTION)
    question_pending(s);e.store.save(s)
    e.process_turn(s.design_id,{'message':'你的参考无关，研究问题改为“电流反向后磁场线方向如何变化？”'})
    value=merge_emvr_structured_requirements(e.store.get(s.design_id).design_context['emvr_design'])['research_question']
    assert value=='电流反向后磁场线方向如何变化？'
