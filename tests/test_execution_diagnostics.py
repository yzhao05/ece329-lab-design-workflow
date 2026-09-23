"""Real observers, deterministic generators; no external model calls."""
from copy import deepcopy
import json

import pytest

from ece329_workflow import execution_diagnostics as diagnostics
from ece329_workflow.dialogue_state import (
    validate_resolved_intent, record_pending_clarification, recover_repeated_pending_answer,
    clarification_output, UserIntent, resolved_intent,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.experience import evidence_snapshot
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage, TurnRequest, StageCompletionError
from ece329_workflow.telemetry import CURRENT_TRACE, TurnTrace


@pytest.fixture(params=list(InteractionState))
def observed(request):
    session = DesignSession(design_id='diagnostic-test', interaction_state=request.param,
        current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS))
    pending = {'action_id': 'pending-1', 'status': 'PENDING',
        'type': 'ANSWER_EMVR_STAGE_QUESTION' if request.param is InteractionState.EMVR_DIRECT else 'ANSWER_STAGE_QUESTION',
        'interaction_state': request.param.value, 'stage': session.current_stage.value,
        'subject': session.current_stage.value,
        'answer_fields': ['independent_variable', 'observations'],
        'allowed_intents': [intent.value for intent in UserIntent]}
    session.model_context['dialogue_state'] = {'pending_action': pending}
    trace = TurnTrace(session, TurnRequest(message='distance and field lines', turn_id='diagnostic-turn'))
    token = CURRENT_TRACE.set(trace)
    try:
        yield session, pending, trace
    finally:
        CURRENT_TRACE.reset(token)


def events(trace, step):
    return [event for event in trace.data['execution_diagnostic']['events'] if event['step'] == step]


def test_validation_keeps_original_intent_and_exact_rejection(observed):
    session, pending, trace = observed
    raw = {'intent': 'ANSWER_CURRENT_QUESTION', 'confidence': .99, 'advance_requested': True,
        'actions_authoritative': True, 'dialogue_acts': [
            {'type': 'MODIFY_STAGE_FIELD', 'target': 'nonexistent', 'content': 'answer', 'confidence': .99}]}
    original = deepcopy(raw)
    result = validate_resolved_intent(raw, pending)
    assert raw == original
    assert result['intent'] == 'UNCLEAR'
    validation = events(trace, 'semantic_validation')[-1]
    assert validation['before']['intent'] == 'ANSWER_CURRENT_QUESTION'
    assert validation['before']['advance_requested'] is True
    assert validation['after']['intent'] == 'UNCLEAR'
    rejection = events(trace, 'action_validation')[0]
    assert rejection['field_path'] == 'dialogue_acts[0]'
    assert rejection['rejected_action']['target'] == 'nonexistent'
    assert rejection['rule'] == '阶段字段或操作无效'


def test_candidate_failure_is_observed_without_inventing_missing_fields(observed):
    session, pending, trace = observed
    for text in ('vary distance; observe field lines', 'fix both charges at 1 nC'):
        pending = record_pending_clarification(session, text, allow_exact_field_binding=False)
    assert recover_repeated_pending_answer(resolved_intent(UserIntent.ADVANCE_STAGE, confidence=.99), pending, 'continue') is None
    clarification_output(pending, session.interaction_state)
    assert events(trace, 'candidate_lifecycle')[0]['before']['candidate_id'] is None
    assert events(trace, 'candidate_lifecycle')[0]['after']['candidate_id']
    assert events(trace, 'candidate_lifecycle')[-1]['after']['candidate_turn_count'] == 2
    assert events(trace, 'recovery')[-1]['status'] == 'blocked'
    assert events(trace, 'reply')[-1]['template_id'] == 'clarification_output'
    assert 'missing_fields' not in json.dumps(trace.data['execution_diagnostic'])


def test_stage_check_and_backend_exception_are_distinct(observed):
    session, pending, trace = observed
    @diagnostics.observe_completion
    def check(s, stage):
        raise StageCompletionError('specific required field')
    with pytest.raises(StageCompletionError):
        check(session, session.current_stage)
    assert events(trace, 'stage_check')[-1]['check_message'] == 'specific required field'
    @diagnostics.observe_completion
    def broken(s, stage):
        raise KeyError('sensitive backend contents')
    with pytest.raises(KeyError):
        broken(session, session.current_stage)
    assert events(trace, 'stage_check')[-1]['code'] == 'backend_exception'
    assert 'sensitive backend contents' not in json.dumps(trace.data)


def test_private_storage_matches_reported_revision_not_current_state(observed):
    session, pending, trace = observed
    session.revision = 1
    session.history.append({'revision': 1, 'handled_stage': session.current_stage.value,
        'interaction_state': session.interaction_state.value, 'user_message': 'answer',
        'resolved_intent': {'intent': 'UNCLEAR'}, 'output': {'assistant_message': 'clarify'}})
    diagnostics.record('semantic_validation', 'blocked', before={'intent': 'ANSWER_CURRENT_QUESTION'}, after={'intent': 'UNCLEAR'})
    diagnostics.capture(session, {'revision': 1, 'assistant_message': 'clarify'})
    public = json.dumps(session.to_dict(include_history=True))
    assert 'execution_diagnostic' not in public
    saved = evidence_snapshot(session, 1)['reported_turn']['execution_diagnostic']
    assert saved['correlation']['revision'] == 1
    assert saved['correlation']['turn_id'] == 'diagnostic-turn'
    assert next(e for e in saved['events'] if e['step'] == 'stage_check')['status'] == 'not_executed'
    session.revision = 2
    session.history.append({'revision': 2, 'handled_stage': session.current_stage.value, 'output': {}})
    assert evidence_snapshot(session, 2)['reported_turn']['execution_diagnostic'] is None
    assert evidence_snapshot(session, 1)['reported_turn']['execution_diagnostic'] == saved


def test_diagnostics_are_bounded_and_secrets_are_redacted(observed):
    session, pending, trace = observed
    for _ in range(150):
        diagnostics.record('test', 'success', api_key='private', headers={'Authorization': 'private'},
            text='Bearer abc123 sk-test-secret password=hunter2', reasoning_content='hidden')
    result = trace.data['execution_diagnostic']
    encoded = json.dumps(result)
    assert result['truncated']
    assert len(result['events']) == diagnostics.MAX_EVENTS
    for forbidden in ('private', 'abc123', 'sk-test-secret', 'hunter2', 'hidden'):
        assert forbidden not in encoded


def test_model_envelope_survives_encoded_action_parse_failure(observed):
    session, pending, trace = observed
    from ece329_workflow.openai_generator import _parse_intent_response, ModelOutputError
    response = {'output_text': json.dumps({'intent': 'ADVANCE_STAGE', 'confidence': .95,
                                         'dialogue_acts_json': '{invalid'})}
    with pytest.raises(ModelOutputError):
        _parse_intent_response(response)
    assert events(trace, 'model_envelope')[-1]['result']['intent'] == 'ADVANCE_STAGE'
    assert events(trace, 'model_parse')[-1]['field_path'] == 'dialogue_acts_json'
    assert events(trace, 'model_parse')[-1]['code'] == 'invalid_json'


def test_field_results_distinguish_rejected_saved_and_duplicate(observed):
    session, pending, trace = observed
    from ece329_workflow.dialogue_acts import apply_stage_field_updates
    update = {'field': 'observations', 'operation': 'REPLACE', 'value': 'field lines'}
    apply_stage_field_updates(session, [{'field': 'not_a_field', 'value': 'ignored'}, update], stage=session.current_stage)
    apply_stage_field_updates(session, [update], stage=session.current_stage)
    rows = events(trace, 'field_write')
    assert any(e['code'] == 'invalid_field_or_operation' and e['status'] == 'blocked' for e in rows)
    assert any(e['code'] == 'applied_to_memory' and e['changed'] for e in rows)
    assert any(e['code'] == 'already_saved' and e['changed'] is False for e in rows)


def test_submission_context_records_omission_not_prompt_content(observed):
    session, pending, trace = observed
    trace.execution_context_keys = {'candidate', 'saved_answers'}
    diagnostics.observe_submitted_context({'text': {'format': {'name': 'ece329_context_intent'}},
        'input': [{'content': [{'type': 'input_text', 'text': json.dumps({
            'user_message': 'private user text', 'carried_context': {'saved_answers': 'private answer'}})}]}]})
    context = events(trace, 'context')[-1]
    assert context['omitted_context_keys'] == ['candidate']
    assert 'private' not in json.dumps(trace.data)


def test_failed_design_save_keeps_correlated_diagnostic_in_server_telemetry(monkeypatch, caplog):
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    created = engine.create_design('electrostatic experiment', interaction_state=InteractionState.EMVR_DIRECT)
    def fail_save(*args, **kwargs):
        raise OSError('database unavailable')
    monkeypatch.setattr(engine.store, 'save', fail_save)
    with pytest.raises(OSError):
        engine.process_turn(created['design_id'], {'message': 'compare electric fields', 'turn_id': 'failed-save'})
    row = engine.telemetry_records(created['design_id'])[0]
    assert row['status'] == 'failed'
    assert row['execution_diagnostic']['correlation']['turn_id'] == 'failed-save'
    assert row['id'] in caplog.text


def test_extractor_budget_deduplicates_diagnostics_without_modifying_evidence():
    diagnostic = {'version': 1, 'events': [{'sequence': i, 'status': 'blocked', 'rule': 'x' * 1000} for i in range(40)]}
    evidence = {'reported_turn': {'revision': 3, 'execution_diagnostic': diagnostic},
                'event_chain': [{'revision': 3, 'execution_diagnostic': diagnostic}], 'recent_turns': []}
    original = deepcopy(evidence)
    compact = diagnostics.extraction_evidence(evidence)
    assert compact['reported_turn']['execution_diagnostic']['model_input_omitted_events'] > 0
    assert compact['event_chain'][0]['execution_diagnostic'] == {'same_turn_reference': 3}
    assert evidence == original


@pytest.mark.parametrize('mode', list(InteractionState))
def test_engine_persists_diagnostics_without_changing_public_contract(mode):
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    created = engine.create_design('electrostatic experiment', interaction_state=mode)
    result = engine.process_turn(created['design_id'], {'message': 'compare electric fields', 'turn_id': 'audit-turn-1'})
    session = engine.store.get(created['design_id'])
    records = session.model_context['execution_diagnostics']
    assert records[-1]['correlation']['revision'] == result['revision']
    assert records[-1]['correlation']['mode'] == mode.value
    assert 'execution_diagnostic' not in json.dumps(result)
    assert 'execution_diagnostic' not in json.dumps(engine.get_design(created['design_id'], include_history=True))
    before = len(records)
    engine.process_turn(created['design_id'], {'message': 'compare electric fields', 'turn_id': 'audit-turn-1'})
    assert len(engine.store.get(created['design_id']).model_context['execution_diagnostics']) == before


@pytest.mark.parametrize('pending', ['invalid', [1], 7])
def test_invalid_recovery_context_does_not_break_safe_rejection(observed, pending):
    assert recover_repeated_pending_answer({}, pending, 'continue') is None
    assert events(observed[2], 'recovery')[-1]['status'] == 'blocked'


@pytest.mark.parametrize('transition', ['backward_normalization', 'completion'])
def test_diagnostic_progress_distinguishes_normalization_and_completion(observed, transition):
    session, _, trace = observed
    from ece329_workflow.models import WorkflowStatus
    if transition == 'backward_normalization':
        session.current_stage_index = 0
    else:
        session.status = WorkflowStatus.COMPLETE
    diagnostics.capture(session, {'revision': 1, 'assistant_message': 'done'})
    result = trace.data['execution_diagnostic']['advance']
    assert result['status'] == ('success' if transition == 'completion' else 'not_advanced')


def test_extraction_budget_includes_metadata_not_only_events():
    diagnostic = {'version': 1, 'events': [], 'final_intent': {'content': 'x' * 25000},
                  'pending_after': {'proposal': 'x' * 25000}, 'experience_execution': ['x' * 2000] * 24}
    evidence = {'event_chain': [{'revision': i, 'execution_diagnostic': diagnostic} for i in range(7)]}
    result = diagnostics.extraction_evidence(evidence)
    assert sum(len(json.dumps(row['execution_diagnostic'], ensure_ascii=False))
               for row in result['event_chain']) <= 18000
    assert any(row['execution_diagnostic'].get('model_input_truncated') for row in result['event_chain'])
    assert evidence['event_chain'][0]['execution_diagnostic'] == diagnostic
    from ece329_workflow.feedback_diagnostics import evidence_was_truncated
    assert evidence_was_truncated(result)


def test_missing_revision_is_not_a_duplicate_turn():
    result = diagnostics.extraction_evidence({'recent_turns': [
        {'execution_diagnostic': {'version': 1, 'events': [{'step': 'reply', 'status': 'success'}]}},
        {'execution_diagnostic': {'version': 1, 'events': [{'step': 'reply', 'status': 'failed'}]}},
    ]})
    assert [row['execution_diagnostic']['events'][0]['status'] for row in result['recent_turns']] == ['success', 'failed']
