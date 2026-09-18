from copy import deepcopy

import pytest

from ece329_workflow.models import DesignSession, InteractionState
from ece329_workflow.unity_layout import unity_layout_snapshot
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator


def scene():
    session = DesignSession('layout-test', InteractionState.EMVR_DIRECT, revision=3)
    session.design_context['emvr_design'] = {'field_state': {
        'research_object': '电流环、电流滑块',
        'room_spatial_requirements': '电流滑块位于电流环右侧',
        'observed_quantities': ['在电流环圆心观察磁场'],
        'required_behaviors': ['调节电流滑块→圆心磁场读数更新'],
    }}
    return session


def test_partial_design_exposes_only_current_explicit_facts_without_mutation():
    session = scene(); before = deepcopy(session)
    result = unity_layout_snapshot(session)
    assert session == before
    assert result['revision'] == 3
    assert [n['label'] for n in result['nodes']] == ['电流环','电流滑块','观察位置']
    assert {r['relation'] for r in result['relations']} == {'right','center'}
    assert result['flows'] == [{'operation':'调节电流滑块','feedback':'圆心磁场读数更新'}]
    assert result['questions'] == []


def test_changes_and_clear_remove_old_geometry_instead_of_merging():
    session = scene()
    first = unity_layout_snapshot(session)
    fields = session.design_context['emvr_design']['field_state']
    fields['room_spatial_requirements'] = '电流滑块位于电流环左侧'
    session.revision += 1
    second = unity_layout_snapshot(session)
    assert 'right' not in [r['relation'] for r in second['relations']]
    assert second['revision'] > first['revision']
    session.design_context['emvr_design']['authoritative_experiment_brief'] = {'objects':['旧电流环']}
    session.design_context['emvr_design']['explicitly_cleared_fields'] = ['research_object']
    assert unity_layout_snapshot(session)['nodes'] == []
    assert unity_layout_snapshot(session)['relations'] == []


@pytest.mark.parametrize('position', ['','也许电流滑块位于电流环右侧','电流滑块不在电流环右侧',
    '建议电流滑块位于电流环右侧','电流滑块位于电流环左侧；电流滑块位于电流环右侧',
    '电流滑块位于电流环左侧；电流环位于电流滑块左侧'])
def test_unknown_proposed_negative_or_conflicting_positions_are_not_drawn(position):
    session = scene()
    session.design_context['emvr_design']['field_state']['room_spatial_requirements'] = position
    result = unity_layout_snapshot(session)
    assert all(r['relation']=='center' for r in result['relations'])
    if position:
        assert result['notes']  # Existing prose is not misreported as a missing answer.
    else:
        assert any('电流滑块' in q for q in result['questions'])


def test_formulas_do_not_create_objects_or_observation_probe():
    session = DesignSession('empty', InteractionState.EMVR_DIRECT)
    session.design_context['emvr_design'] = {'field_state': {'primary_formula_ids':['coulomb_point_charge']}}
    result = unity_layout_snapshot(session)
    assert result['nodes'] == result['relations'] == []
    assert result['questions']
    session.interaction_state = InteractionState.GUIDED_DESIGN
    assert unity_layout_snapshot(session) is None


def test_early_explicit_object_and_no_fixed_observation_are_supported():
    session = DesignSession('early', InteractionState.EMVR_DIRECT)
    session.design_context['emvr_design'] = {'formula_flow':{'topic_analysis':{'mentioned_objects':['电流环']}}}
    result = unity_layout_snapshot(session)
    assert result['nodes'][0]['label'] == '电流环'
    assert result['relations'] == []
    session.design_context['emvr_design']['field_state'] = {'observed_quantities':['不需要固定观察点']}
    assert all('在哪里观察' not in q for q in unity_layout_snapshot(session)['questions'])


def test_get_snapshot_and_turn_responses_include_revision_bound_layout():
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    session = scene();engine.store.save(session)
    result = engine.get_design(session.design_id)
    assert result['unity_layout'] == unity_layout_snapshot(session)
    created = engine.create_design('EMVR 我想研究磁场', interaction_state='EMVR_DIRECT')
    assert created['unity_layout']['design_id'] == created['design_id']
    assert created['unity_layout']['revision'] == created['revision']
    guided = engine.create_design('我想研究磁场', interaction_state='GUIDED_DESIGN')
    assert guided['unity_layout'] is None


def test_english_explicit_positions_and_observation():
    session = scene()
    session.design_context['emvr_design']['field_state'] = {
        'research_object':'Current loop;Current slider',
        'room_spatial_requirements':'Current slider is right of Current loop',
        'observed_quantities':['Observe magnetic field at the center of Current loop'],
    }
    assert {r['relation'] for r in unity_layout_snapshot(session)['relations']} == {'right','center'}


def test_clearing_objects_also_clears_causal_arrows():
    session = scene()
    session.design_context['emvr_design']['explicitly_cleared_fields'] = ['research_object']
    snapshot = unity_layout_snapshot(session)
    assert snapshot['relations'] == snapshot['flows'] == []


def test_existing_freeform_positions_do_not_repeat_the_same_question():
    session = scene()
    session.design_context['emvr_design']['field_state']['room_spatial_requirements'] = '把电流滑块安置在电流环右边的操作台上'
    snapshot = unity_layout_snapshot(session)
    assert snapshot['notes']
    assert not any('相对位置' in q for q in snapshot['questions'])
    assert snapshot['summary'][-1]['items'] == ['把电流滑块安置在电流环右边的操作台上']


@pytest.mark.parametrize('placement', [
    'A位于B左侧；B位于C左侧；C位于A左侧',
    'A位于B前方；B位于C前方；C位于A前方',
    'A位于B中心；B位于C中心；C位于A左侧',
])
def test_position_cycles_leave_diagram_blank_and_request_resolution(placement):
    session = scene()
    session.design_context['emvr_design']['field_state'] = {'research_object':'A、B、C','room_spatial_requirements':placement}
    snapshot = unity_layout_snapshot(session)
    assert snapshot['relations'] == []
    assert any('循环冲突' in q for q in snapshot['questions'])


def test_no_fixed_observer_does_not_still_draw_a_fixed_marker():
    session = scene()
    session.design_context['emvr_design']['field_state']['observed_quantities'].append('无需固定观察点')
    snapshot = unity_layout_snapshot(session)
    observer_ids = {n['id'] for n in snapshot['nodes'] if n['kind']=='observation'}
    assert all(r['source'] not in observer_ids and r['target'] not in observer_ids for r in snapshot['relations'])
    assert any('观察说明存在冲突' in q for q in snapshot['questions'])


def test_large_design_does_not_run_quadratic_pattern_expansion():
    session = scene()
    names = ['对象'+str(i) for i in range(65)]
    session.design_context['emvr_design']['field_state']['research_object'] = '、'.join(names)
    snapshot = unity_layout_snapshot(session)
    assert snapshot['relations'] == []
    assert snapshot['summary'][0]['items'] == names
    assert any('较多' in note for note in snapshot['notes'])


def test_two_observation_positions_do_not_merge_two_component_centers():
    session = scene()
    session.design_context['emvr_design']['field_state'] = {
        'research_object':'A、B', 'room_spatial_requirements':'A位于B左侧',
        'observed_quantities':['在A中心观察磁场','在B中心观察磁场'],
    }
    snapshot = unity_layout_snapshot(session)
    assert len(snapshot['relations']) == 3
    assert len({r['source'] for r in snapshot['relations'] if r['relation']=='center'}) == 2
    assert not snapshot['questions']


def test_perpendicular_positions_are_compatible_not_conflicting():
    session = scene()
    session.design_context['emvr_design']['field_state'] = {
        'research_object':'A、B', 'room_spatial_requirements':'A位于B左侧；A位于B前方',
        'observed_quantities':['无需固定观察点'],
    }
    snapshot = unity_layout_snapshot(session)
    assert {r['relation'] for r in snapshot['relations']} == {'left', 'front'}
    assert snapshot['questions'] == []


def test_english_flow_words_are_not_mistaken_for_conditional_substrings():
    session = scene()
    fields = session.design_context['emvr_design']['field_state']
    fields['research_object'] = 'slider'
    fields['required_behaviors'] = ['Shift slider -> reading changes']
    assert unity_layout_snapshot(session)['flows'] == [{'operation':'Shift slider', 'feedback':'reading changes'}]
    fields['required_behaviors'] = ['If we shift slider -> reading changes']
    assert unity_layout_snapshot(session)['flows'] == []
