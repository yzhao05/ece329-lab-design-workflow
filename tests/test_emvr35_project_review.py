"""Cross-boundary regressions found while reviewing EMVR and Builder output."""
from copy import deepcopy

import pytest

from ece329_workflow.builder_input import _student_task_contracts
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.meta_dialogue import meta_question_kinds
from ece329_workflow.models import DesignSession, InteractionState
from ece329_workflow.procedure_contract import historical_procedure, current_procedure
from tests.test_emvr35_recovery import STEPS, prepare
from tools.export_emvr34_regression import completed_magnetic_engine


@pytest.fixture(scope='module')
def complete():
    return completed_magnetic_engine()[1]


@pytest.mark.parametrize('message', [
    '我有点乱，电流方向反过来试试。',
    '现在到哪一步了？以后不显示场线。',
    '你现在在问什么问题？实验对象应该是圆环。',
])
def test_meta_question_does_not_swallow_a_second_substantive_clause(message):
    assert not meta_question_kinds(message)


@pytest.mark.parametrize('message', ['不要恢复之前的实验流程', '不采用之前的实验流程'])
def test_negative_restore_request_does_not_restore_history(complete, message):
    e,s = prepare(complete, broken=True, final=True)
    e.process_turn(s.design_id, {'message':message})
    assert current_procedure(e.store.get(s.design_id)) == STEPS[-1:]


def test_new_topic_cannot_restore_a_previous_experiments_procedure(complete):
    e,s = prepare(complete)
    e._reset_for_new_topic(s)
    assert not historical_procedure(s)


@pytest.mark.parametrize('step', ['核对场线方向，不要Reset或Back。', '记录观察结论，不要Restore。', 'Reset清除记录，核对Restore已禁用；Back返回Start。'])
def test_negated_unity_controls_do_not_create_control_transitions(step):
    task = _student_task_contracts([step], '定性比较')[0]
    mapping = task['expected_action'].split('Unity操作映射：',1)[1]
    assert 'and Restore' not in mapping
    if '不要' in step:
        assert 'activate Reset' not in mapping and 'Back to Start' not in mapping


def test_positive_capture_in_next_clause_is_not_disabled_by_a_prior_negation():
    task = _student_task_contracts(['基准加载时不Capture，改变电流后Capture保存结果。'], 'Bz T')[0]
    assert task['exit_state'] == 'CAPTURED'


def test_comparison_precedes_interpretation_in_derived_mapping():
    task = _student_task_contracts(['并排比较快照，然后解释方向变化。'], 'Bz T')[0]
    mapping = task['expected_action'].split('Unity操作映射：',1)[1]
    assert mapping.index('compare') < mapping.index('interpretation')


def test_closed_loop_numerical_fixture_matches_independent_axial_solution():
    from tools.export_emvr35_review import MU, RADIUS, circle_field
    for z in (0.0,0.5,1.0):
        expected = MU*RADIUS**2/(2*(RADIUS**2+z*z)**1.5)
        actual = circle_field(z)
        assert max(abs(actual[0]),abs(actual[1])) < 1e-12
        assert abs(actual[2]-expected) < 1e-9
        assert abs(circle_field(z,1024)[2]-expected) < abs(actual[2]-expected)


def test_biot_builder_handoff_has_source_inputs_steps_and_adapter_contract():
    from ece329_workflow.builder_input import build_builder_gate1_input, validate_builder_gate1_input, _builder_sections
    from tools.export_emvr35_review import completed_ring_engine, VALUES
    _,s = completed_ring_engine()
    payload = build_builder_gate1_input(s)
    validate_builder_gate1_input(payload)
    assert [row['goal'] for row in payload['student_tasks']] == VALUES['procedure_steps']
    assert [row['formula_id'] for row in payload['physics']['formulas']] == ['biot_savart']
    numerical = payload['physics']['numerical_model']
    assert all(token in numerical for token in ('512','1024','phi_k','0.03','0.001','5000','10000','4 ms'))
    approved = ' '.join(row['value'] for row in payload['implementation_defaults'])
    assert all(token in approved for token in ('TryCompleteCurrentStep','TryResetObjects','CurrentSnapshotJson','run/reset','4096工作单元'))
    assert [int(heading.split('.')[0]) for heading,_ in _builder_sections(payload)] == list(range(1,22))
