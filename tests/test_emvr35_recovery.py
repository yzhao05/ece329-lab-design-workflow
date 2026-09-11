"""emvr35: indexed procedure questions, final repair and useful references."""
from copy import deepcopy

import pytest

from ece329_workflow.builder_defaults import record_implementation_defaults_approval, build_implementation_defaults, format_implementation_defaults
from ece329_workflow.builder_references import builder_field_reference
from ece329_workflow.builder_requirements import missing_builder_requirements, numerical_tolerance_defined
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import current_pending_action
from ece329_workflow.emvr_design import normalize_emvr_design_update, merge_emvr_structured_requirements
from ece329_workflow.engine import WorkflowEngine, _completion_source_fingerprint, _explicit_emvr_contract_request
from ece329_workflow.generator import RuleBasedStageGenerator, _emvr_reference_output
from ece329_workflow.models import Stage, WorkflowStatus
from ece329_workflow.procedure_contract import procedure_steps, current_procedure
from ece329_workflow.reporting import _readable_report_value
from ece329_workflow.reporting import effective_emvr_stage_payload, _effective_emvr_visualization
from ece329_workflow.builder_defaults import measurement_is_qualitative_only, implementation_defaults_approval_valid
from tools.export_emvr34_regression import completed_magnetic_engine


STEPS = ["核对参数规格：电流0.5~5A、默认1A、步长0.1A。", "加载并记录1A基准。",
         "每次只改变一个自变量。", "等待计算刷新后观察磁场方向。", "保存各比较情形快照。",
         "并排比较快照的方向与环绕性。", "依据毕奥萨伐尔定律解释差异，记录模型边界。"]


@pytest.fixture(scope='module')
def complete():
    return completed_magnetic_engine()[1]


def prepare(complete, broken=False, final=False):
    s=deepcopy(complete)
    s.status=WorkflowStatus.ACTIVE
    s.current_stage_index=list(Stage).index(Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT if final else Stage.CONCEPTUAL_PROCEDURE)
    apply_stage_field_updates(s,[{'field':'procedure_steps','operation':'REPLACE','value':STEPS[-1:] if broken else STEPS}],
                              stage=Stage.CONCEPTUAL_PROCEDURE)
    s.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value]['stage_payload']['procedure_steps']=STEPS[-1:] if broken else STEPS
    s.history.append({'stage':Stage.CONCEPTUAL_PROCEDURE.value,'output':{'stage_payload':{'procedure_steps':STEPS}}})
    s.model_context['dialogue_state']={'pending_action':None}
    # Isolate procedure completeness from the legitimately invalidated defaults.
    contract=format_implementation_defaults(build_implementation_defaults(s))
    apply_stage_field_updates(s,[{'field':'implementation_defaults','operation':'REPLACE','value':contract}],stage=Stage.DESIGN_VALUE_AND_LIMITATIONS)
    record_implementation_defaults_approval(s,source='TEST')
    e=WorkflowEngine(generator=RuleBasedStageGenerator());e.store.save(s)
    return e,s


def test_show_seventh_step_does_not_replace_list_or_advance_with_stale_button(complete):
    e,s=prepare(complete)
    response=e.process_turn(s.design_id,{'message':'明确写出流程中第七条的内容','complete_stage':True,'selected_option_id':'stale-continue'})
    stored=e.store.get(s.design_id)
    assert current_procedure(stored)==STEPS
    assert stored.current_stage is Stage.CONCEPTUAL_PROCEDURE
    assert STEPS[-1] in response['assistant_message']


def test_indexed_edit_keeps_other_steps(complete):
    e,s=prepare(complete)
    response=e.process_turn(s.design_id,{'message':'把流程第七条改为：用公式解释方向变化并核对Reset。'})
    steps=current_procedure(e.store.get(s.design_id))
    assert steps[:-1]==STEPS[:-1]
    assert steps[-1]=='用公式解释方向变化并核对Reset。'


def test_short_indexed_edit_and_questions_do_not_become_full_replacements(complete):
    e,s=prepare(complete)
    e.process_turn(s.design_id,{'message':'把第七条改为：解释电流反向后的磁场方向。'})
    assert current_procedure(e.store.get(s.design_id))[:-1]==STEPS[:-1]
    pending={'subject':'procedure_steps','answer_fields':['procedure_steps']}
    assert _explicit_emvr_contract_request(s,'为什么只剩一步。是不是丢失了。',pending) is None


def test_repeated_procedure_actions_keep_their_order():
    steps=['加载基准。','保存快照。','改变电流。','保存快照。','并排比较。']
    assert normalize_emvr_design_update({'procedure_steps':steps})['procedure_steps']==steps
    merged=merge_emvr_structured_requirements({'field_state':{'procedure_steps':steps}})
    assert merged['procedure_steps']==steps


def test_final_missing_prior_stage_is_a_writable_repair_not_artifact_blocker(complete):
    e,s=prepare(complete,broken=True,final=True)
    r=e.process_turn(s.design_id,{'message':'继续','complete_stage':True})
    pending=current_pending_action(e.store.get(s.design_id))
    assert pending['answer_fields']==['procedure_steps']
    assert 'artifact_blocker' not in r['stage_payload']


@pytest.mark.parametrize('style',['sentences','numbered','s_ids'])
def test_old_stuck_session_accepts_complete_replacement_and_reaches_export(complete,style):
    e,s=prepare(complete,broken=True,final=True)
    s.model_context['artifact_blocker']={'fingerprint':_completion_source_fingerprint(s),'error':'EMVR报告仍缺少：完整实验流程','message':'旧故障'}
    e.store.save(s)
    if style=='sentences': msg='不采用，使用下面这版作为实验流程： '+' '.join(STEPS)
    elif style=='numbered': msg='实验流程分为7步： '+' '.join(f'{i}，{v}' for i,v in enumerate(STEPS,1))
    else: msg='学生实验步骤：'+'；'.join(f'S{i} {v}' for i,v in enumerate(STEPS,1))
    r=e.process_turn(s.design_id,{'message':msg})
    stored=e.store.get(s.design_id)
    assert current_procedure(stored)==STEPS
    assert '没有可靠' not in r['assistant_message']
    # Explicitly approve the regenerated construction document, then finish.
    for _ in range(5):
        if r.get('workflow_status')=='complete':break
        pending=current_pending_action(e.store.get(s.design_id)) or {}
        r=e.process_turn(s.design_id,{'message':'批准默认方案' if pending.get('subject')=='implementation_defaults' else '继续','complete_stage':True})
    assert r['workflow_status']=='complete',r['assistant_message']
    assert r['builder_input_ready']


def test_history_question_is_read_only_and_explicit_restore_is_possible(complete):
    e,s=prepare(complete,broken=True,final=True)
    r=e.process_turn(s.design_id,{'message':'阶段3的时候已经确认过实验流程'})
    assert STEPS[0] in r['assistant_message']
    assert current_procedure(e.store.get(s.design_id))==STEPS[-1:]
    e.process_turn(s.design_id,{'message':'恢复之前的实验流程'})
    assert current_procedure(e.store.get(s.design_id))==STEPS


def test_reference_targets_missing_flow_and_never_recommends_unbound_complaint(complete):
    e,s=prepare(complete,broken=True,final=True)
    e.process_turn(s.design_id,{'message':'继续','complete_stage':True})
    r=e.process_turn(s.design_id,{'message':'给我个参考'})
    draft=r['stage_payload']['reference_draft']
    assert draft['field']=='procedure_steps'
    assert len(procedure_steps(draft['value']))==7
    assert current_procedure(e.store.get(s.design_id))==STEPS[-1:]
    r=e.process_turn(s.design_id,{'message':'你现在在问什么问题，明确告诉我'})
    assert '流程' in r['assistant_message']
    assert '没有改动' not in r['assistant_message']


def test_procedure_reference_can_be_explicitly_adopted(complete):
    e,s=prepare(complete,broken=True,final=True)
    e.process_turn(s.design_id,{'message':'继续','complete_stage':True})
    e.process_turn(s.design_id,{'message':'给我个参考'})
    e.process_turn(s.design_id,{'message':'采用'})
    assert current_procedure(e.store.get(s.design_id))==STEPS


def test_missing_history_does_not_claim_a_successful_restore(complete):
    e,s=prepare(complete,broken=True,final=True)
    s.history=[]
    e.store.save(s)
    r=e.process_turn(s.design_id,{'message':'恢复之前的实验流程'})
    assert '没有完整流程' in r['assistant_message']
    assert current_procedure(e.store.get(s.design_id))==STEPS[-1:]


def test_previous_default_contract_requires_review_after_policy_upgrade(complete):
    s=deepcopy(complete)
    s.design_context['emvr_design']['implementation_defaults_approval']['contract_version']='builder-ui-flow-v5'
    assert not implementation_defaults_approval_valid(s)
    e=WorkflowEngine(generator=RuleBasedStageGenerator());e.store.save(s)
    r=e.process_turn(s.design_id,{'message':'继续','complete_stage':True})
    assert r['workflow_status']=='active'
    assert current_pending_action(e.store.get(s.design_id))['default_proposal_value']
    r=e.process_turn(s.design_id,{'message':'批准默认方案','complete_stage':True})
    assert r['workflow_status']=='complete' and r['builder_input_ready']


def test_json_encoded_lists_and_decimal_results_are_not_corrupted():
    emvr={'field_state':{'changed_quantities':['["电流大小与方向","载流路径形状"]']}}
    assert merge_emvr_structured_requirements(emvr)['changed_quantities']==['电流大小与方向','载流路径形状']
    text=_readable_report_value('expected_results',['0.5A：基准。','1.2A：比较。','5A：上限。'])
    assert '0.5A' in text and '1.2A' in text


def test_percentage_tolerance_supplement_is_merged_without_erasing_model(complete):
    e,s=prepare(complete)
    s.current_stage_index=list(Stage).index(Stage.EXPECTED_DATA_VISUALIZATION)
    initial='毕奥萨伐尔数值积分，源路径分段512段，按中点求积；场线RK4；计算域[-1.5,1.5] m；20个种子坐标为(0.6*cos(2*pi*k/20),0,0.6*sin(2*pi*k/20)) m，k=0..19，最大5000步；边界和奇点终止；零场不归一化。'
    apply_stage_field_updates(s,[{'field':'numerical_model_specifications','operation':'REPLACE','value':initial}],stage=s.current_stage)
    s.model_context['dialogue_state']={'pending_action':{'type':'ANSWER_EMVR_STAGE_QUESTION','subject':'numerical_model_specifications','answer_fields':['numerical_model_specifications'],'question':'请补充误差容差。'}}
    e.store.save(s)
    supplement='补充误差/收敛容差：场线RK4固定步长0.02米，验收时用步长减半0.01米重算，同一场线位置偏差小于1%即视为收敛；毕奥-萨伐尔电流元离散长度0.01米，与0.005米对比，磁场强度相对误差小于1%。'
    assert numerical_tolerance_defined(supplement)
    e.process_turn(s.design_id,{'message':supplement})
    stored=e.store.get(s.design_id)
    value=stored.design_context['stage_design_state']['numerical_model_specifications']
    assert initial in value and '小于1%' in value
    assert 'numerical_model_specifications' not in {x['field'] for x in missing_builder_requirements(stored)}


@pytest.mark.parametrize('field',['measurement_specifications','numerical_model_specifications','expected_results'])
def test_magnetic_reference_contains_actual_help_instead_of_question_repetition(complete,field):
    s=deepcopy(complete)
    s.design_context['emvr_design']['selected_primary_formula_ids']=['biot_savart']
    reference=builder_field_reference(s,field)
    assert '建议' in reference['value']
    assert 'ε₀' not in reference['value'] and '库仑' not in reference['value']
    if field=='numerical_model_specifications': assert '1%' in reference['value'] and '0.005 m' in reference['value']
    if field=='expected_results': assert '不会改变' in reference['value']


def test_qualitative_magnetic_shape_has_no_numeric_axes_or_curve_requirement(complete):
    s=deepcopy(complete)
    measurement=('电流大小：显示当前电流值，单位A，范围0.5~5A；'
                 '磁场线方向和环绕性：定性形态指标，由毕奥萨伐尔积分生成场线，通过方向箭头显示，无量纲；'
                 '空间探针：不设置可移动探针，不需要单点读数。')
    apply_stage_field_updates(s,[{'field':'measurement_specifications','operation':'REPLACE','value':measurement}],
                              stage=Stage.EXPECTED_DATA_VISUALIZATION)
    assert measurement_is_qualitative_only(measurement)
    s.design_context['stage_design_state'].pop('physics_layer',None)
    s.stage_outputs[Stage.CONCEPTUAL_OR_VR_SETUP.value]['stage_payload'].update(
        physics_layer={'real_time_updates':['数值','曲线','场表现']},measurement_interface=['比较曲线'])
    payload=effective_emvr_stage_payload(s,Stage.CONCEPTUAL_OR_VR_SETUP)
    assert '曲线' not in payload['physics_layer']['real_time_updates']
    assert '不生成数值纵轴' in str(payload['measurement_interface'])
    visualization=_effective_emvr_visualization(s,{'visualization':{'x_axis':{'label':'I'},'y_axis':{'label':'形态'}}})
    assert 'x_axis' not in visualization and 'y_axis' not in visualization
    assert '快照' in build_implementation_defaults(s)['measurement_and_chart_policy']
    assert not measurement_is_qualitative_only('显示Bz分量，单位T；补充定性形态指标。')
