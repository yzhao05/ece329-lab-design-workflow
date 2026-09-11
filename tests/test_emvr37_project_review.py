"""Cross-layer regressions found during review of the emvr37 changes."""
from copy import deepcopy

import pytest

from ece329_workflow.builder_input import build_builder_gate1_input, validate_builder_gate1_input
from ece329_workflow.builder_references import builder_field_reference
from ece329_workflow.builder_requirements import builder_requirement_values, numerical_tolerance_defined
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.emvr_catalog import project_catalog
from ece329_workflow.emvr_design import apply_emvr_field_updates
from ece329_workflow.models import Stage, StepOutput
from ece329_workflow.numerical_contract import biot_numerical_gap, uses_biot_model
from ece329_workflow.numerical_updates import merge_numerical_contract
from ece329_workflow.unity_blueprint import construction_defaults, ROOM_PREFAB_SOURCES, ROOM_LIGHTING_SOURCE
from tests.test_emvr37_numeric_recovery import numeric_session, INITIAL, SEGMENTS, SEEDS, ZERO, FIELD
from tools.export_emvr35_review import completed_ring_engine
from tests.test_builder_portability import portable_workspace, make_pack


@pytest.fixture(scope='module')
def base():
    return completed_ring_engine()[1]


def test_numeric_correction_replaces_value_keeps_domain_and_refinement():
    old = '毕奥积分，计算域[-1.5,1.5]^3 m；场线步长0.02m，最大步数5000；验收复算场线步长0.01m，最大步数10000。'
    new = merge_numerical_contract(old, '场线步长改为0.03m；最大步数6000')
    assert '场线步长0.03m' in new and '最大步数6000' in new
    assert '0.02m' not in new and '5000' not in new
    assert '计算域[-1.5,1.5]^3 m' in new
    assert '验收复算场线步长0.01m，最大步数10000' in new


def test_numerical_correction_can_revert_to_an_earlier_value(base):
    e,s = numeric_session(base)
    for value in ('0.03米','0.04米','0.03米'):
        apply_stage_field_updates(s, [{'field':FIELD,'operation':'MERGE','value':'步长'+value}], stage=s.current_stage)
    result = builder_requirement_values(s)[FIELD]
    assert '步长0.03米' in result and '0.04米' not in result
    assert '0.02米' not in result and '边长3米' in result


def test_named_source_count_correction_retains_other_shapes():
    new = merge_numerical_contract('直导线200段；圆环200段；螺线管400段；采用中点求积。', '直导线300段')
    assert '直导线300段' in new and '直导线200段' not in new
    assert '圆环200段' in new and '螺线管400段' in new and '中点' in new


def test_extra_explanation_survives_scalar_correction():
    result = merge_numerical_contract('场线步长0.02m；RK4', '场线步长0.03m；奇点时停止并记录原因')
    assert '0.02m' not in result and '奇点时停止并记录原因' in result
    assert merge_numerical_contract(result,'场线步长0.03m；奇点时停止并记录原因') == result


@pytest.mark.parametrize('text', ['误差-1e-5','容差0','容差1e999','误差：固定步长0.01m','误差未知，最大5000步'])
def test_invalid_tolerance_is_not_hidden_by_another_number(text):
    assert not numerical_tolerance_defined(text)


def test_one_positive_source_cannot_hide_a_negative_source():
    assert biot_numerical_gap(INITIAL + '直导线200段；圆环-200段；每段中点。' + SEEDS + ZERO)


def test_direct_export_checks_tolerance_too(base):
    payload = build_builder_gate1_input(base)
    payload['physics']['numerical_model'] = INITIAL.replace('误差/收敛容差1e-5。','') + SEGMENTS + SEEDS + ZERO
    with pytest.raises(ValueError,match='容差'):
        validate_builder_gate1_input(payload)


def test_export_rejects_algorithm_from_another_formula(base):
    payload = build_builder_gate1_input(base)
    payload['physics']['numerical_model'] = '库仑场直接计算；计算域3m，误差1e-5，边界外终止，无需迭代。'
    with pytest.raises(ValueError,match='毕奥'):
        validate_builder_gate1_input(payload)


def test_legacy_formula_alias_cannot_resurrect_electrostatic_reference(base):
    s = deepcopy(base)
    emvr = s.design_context['emvr_design']
    emvr['formula_flow']['formula_selection']['primary'] = ['coulomb_point_charge']
    result = builder_field_reference(s, FIELD)
    assert '库仑' not in result['value'] and uses_biot_model(result['value'])
    apply_emvr_field_updates(emvr, {'field_updates':[{'field_id':'primary_formula_ids','operation':'REPLACE','value':['biot_savart']}]})
    assert emvr['formula_flow']['formula_selection']['primary'] == ['biot_savart']


def test_clear_reference_removes_stale_displayed_sources(base):
    s = deepcopy(base)
    apply_emvr_field_updates(s.design_context['emvr_design'], {'field_updates':[{'field_id':'course_reference_ids','operation':'CLEAR','value':[]}]})
    payload = {'primary_topic':'stale topic','course_references':[{'concept_id':'lecture_01'}]}
    project_catalog(s, Stage.COURSE_MAPPING_AND_DIRECTION, payload)
    assert payload['course_references'] == [] and payload['primary_topic'] == ''


def test_incompatible_online_reference_cannot_be_adopted(base):
    from ece329_workflow.generator import RuleBasedStageGenerator
    e,s = numeric_session(base)
    class WrongPhysics(RuleBasedStageGenerator):
        def generate(self,session,message):
            return StepOutput(assistant_message='库仑计算方案', stage_payload={'reference_draft':{'field':FIELD,
                'value':'库仑场直接计算；计算域3m，误差1e-5，边界外终止，无需迭代。'}})
    e.generator = WrongPhysics()
    result = e.process_turn(s.design_id, {'message':'给我个完整的参考'})
    assert '库仑' not in result['stage_payload']['reference_draft']['value']


def test_pdf_input_has_complete_room_and_lighting_contract(base):
    payload = build_builder_gate1_input(base)
    env = {row['key']:row['value'] for row in payload['environment']}
    assert all(path in str(env['environment.room_shell_source']) for path in ROOM_PREFAB_SOURCES)
    assert env['environment.lighting_recipe'] == ROOM_LIGHTING_SOURCE
    assert 'Part 02 x2' in env['environment.room_module_counts']
    blueprint = construction_defaults('test_lab')
    assert '当前场景GI数据' in blueprint['room_assembly_and_lighting']
    assert '不新增Directional Light' in blueprint['room_assembly_and_lighting']
    assert 'create_blind_run_pack.ps1' in blueprint['bootstrap_and_references']
    semantics = {row['key']:row for row in payload['value_semantics']}
    assert semantics['value.numeric_morphology_axis']['status'] == 'not-applicable'
    assert '固定采样位置和读数仍按测量契约实现' in semantics['value.spatial_probe']['value']


def test_reference_preserves_confirmed_exclusion_radius(base):
    _,s = numeric_session(base)
    result = builder_field_reference(s,FIELD)
    assert '0.05 m' not in result['value']
    assert '距源不大于0.03 m' in result['value']
    assert '待确认建议' not in result['value'] and '采用后替换' not in result['value']


def test_control_rows_do_not_repeat_superseded_numerical_values(base):
    from ece329_workflow.reporting import effective_emvr_stage_payload
    s = deepcopy(base)
    original = s.design_context['stage_design_state']['controlled_conditions']
    assert '512源段' in original
    payload = effective_emvr_stage_payload(s,Stage.VARIABLES_AND_CONDITIONS)
    assert '512源段' not in str(payload['controlled_variables'])
    assert '最新数值契约' in str(payload['controlled_variables'])
    assert s.design_context['stage_design_state']['controlled_conditions'] == original
    exported = build_builder_gate1_input(s)
    row = next(x for x in exported['design_definition'] if x['key']=='controlled_variables')
    assert '512源段' not in row['value']


def test_discovery_prefers_marked_child_and_never_silently_falls_back(portable_workspace):
    import json
    import shutil
    from ece329_workflow.builder_portability import resolve_local_builder_pack, PACK_NAME
    master = make_pack(portable_workspace)
    child = master/'RebuildWorkspaces/EMVR_Blind_Rebuild_test_lab'
    shutil.copytree(master, child, ignore=shutil.ignore_patterns('RebuildWorkspaces'))
    marker = {'lab_id':'test_lab', 'workflow_mode':'blind-rebuild',
              'required_master_directory_name':PACK_NAME,'required_directory_name':child.name}
    (child/'.emvr-workspace.json').write_text(json.dumps(marker),encoding='utf-8')
    assert resolve_local_builder_pack(child/'UnityProject') == child
    (child/'.emvr-workspace.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='不自动退回主包'):
        resolve_local_builder_pack(child/'UnityProject')
