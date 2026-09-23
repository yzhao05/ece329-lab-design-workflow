"""Accounting uses actual provider tokens and active time, without fee estimates."""
import json
from types import SimpleNamespace

import pytest

from ece329_workflow.usage import read_usage, summarize
from ece329_workflow.usage import breakdown
from ece329_workflow.experience import ExperienceStore, ModelExperienceExtractor
from ece329_workflow.models import InteractionState
from tests.test_routing_and_telemetry import research
from tests.test_feedback_pipeline import pipeline, submit, extraction_response
from tests.test_model_selection import QUESTION
from tests.test_security_and_store import call_api, workspace_temp_path


@pytest.mark.parametrize('reported_mode', list(InteractionState))
def test_feedback_usage_belongs_to_reported_mode_not_submission_mode(pipeline, reported_mode):
    p = pipeline
    ticket = submit(p)[2]
    current = next(mode for mode in InteractionState if mode != reported_mode)
    with p.repo.connection() as db:
        payload = json.loads(db.execute('SELECT payload FROM feedback_tickets WHERE id=?', (ticket['id'],)).fetchone()[0])
        payload['evidence'].update(reported_mode=reported_mode.value, mode=current.value)
        db.execute('UPDATE feedback_tickets SET payload=? WHERE id=?', (json.dumps(payload), ticket['id']))
    assert p.service.run_once()
    with p.repo.connection() as db:
        rows = db.execute('SELECT record FROM usage_runs WHERE ticket_id=?', (ticket['id'],)).fetchall()
    assert rows and all(json.loads(row['record'])['mode'] == reported_mode.value for row in rows)
    assert not p.service.run_once()


def test_provider_specific_usage_and_reasoning_tokens_are_not_mixed():
    openai = {'provider':'openai','model_id':'shared-model'}
    deepseek = {'provider':'deepseek','model_id':'shared-model'}
    read_usage({'model':'shared-snapshot','usage':{'input_tokens':1000,'output_tokens':100,
        'input_tokens_details':{'cached_tokens':200},'output_tokens_details':{'reasoning_tokens':30}}},openai)
    read_usage({'model':'shared-model','usage':{'prompt_tokens':1000,'completion_tokens':100,
        'prompt_cache_hit_tokens':200,'completion_tokens_details':{'reasoning_tokens':30}}},deepseek)
    assert openai['reported_model'] == 'shared-snapshot'
    assert deepseek['reasoning_tokens'] == 30
    assert summarize([openai,deepseek])['total_tokens'] == 2200  # Reasoning is already included.
    assert deepseek['usage_format'] == 'deepseek_chat'


def test_cross_stage_calls_split_without_counting_local_work_twice():
    calls = []
    for stage, model, ms, role in [('IDEA_BRAINSTORMING','gpt-5.4-mini',200,'intent_resolver'),
        ('RESEARCH_QUESTION','gpt-5.5',500,'design_agent')]:
        calls.append({**measured_call(model), 'stage':stage,'provider':'openai','latency_ms':ms,'agent':role})
    rows, stages = breakdown([{'id':'r1','initial_stage':'IDEA_BRAINSTORMING','calls':calls,'latency_ms':1000}])
    assert len(rows) == 2 and {r['model_id'] for r in rows} == {'gpt-5.4-mini','gpt-5.5'}
    assert sum(r['usage']['input_tokens'] for r in stages) == 2000
    assert sum(r['usage']['active_ms'] for r in stages) == 1000
    assert next(r for r in stages if r['stage']=='UNATTRIBUTED_PROCESSING')['usage']['active_ms'] == 300
    assert sum(r['usage']['active_ms'] for r in rows) == 700


def test_feedback_stage_and_agent_roles_are_exposed(pipeline):
    p = pipeline
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='test',transport=SimpleNamespace(create=extraction_response)), {})
    ticket=submit(p)[2];p.service.run_once()
    rows=p.repo.feedback_detail(ticket['id'])['usage']['breakdown']
    assert {r['agent'] for r in rows} == {'experience_extractor','experience_checker'}
    assert {r['stage'] for r in rows} == {p.session.current_stage.value}


def test_current_stage_comes_from_saved_design_not_feedback_or_translation(pipeline):
    from ece329_workflow.models import WorkflowStatus
    p = pipeline
    p.repo.register_usage_design(p.session.design_id, p.session.interaction_state.value)
    p.repo.record_telemetry({'id':'failed-turn','design_id':p.session.design_id,'created':1,
        'latency_ms':1000,'calls':[], 'initial_stage':'VARIABLES_AND_CONDITIONS',
        'final_stage':'CONCEPTUAL_PROCEDURE','handled_stage':'VARIABLES_AND_CONDITIONS',
        'status':'failed','workflow_status':'active'})
    p.session.current_stage_index=12; p.session.status=WorkflowStatus.COMPLETE;p.engine.store.save(p.session)
    ticket=submit(p)[2];p.service.run_once()
    item=call_api(p.api,'GET','/v1/feedback/usage',request_headers={'X-ECE329-Feedback-Admin-Token':'maintainer-token'})[2]['designs'][0]
    assert item['current_state']['stage'] == 'STUDENT_SYNTHESIS_OR_EMVR_OUTPUT'
    assert item['current_state']['status'] == 'complete'
    assert item['usage']['last_dialogue']['status'] == 'failed'
    assert p.repo.feedback_detail(ticket['id'])['status'] == 'candidate'


def measured_call(model='test'):
    call = {'model_id': model}
    read_usage({'usage': {'input_tokens': 1000, 'output_tokens': 100,
                         'input_tokens_details': {'cached_tokens': 200}}}, call)
    return call


def test_cached_tokens_are_not_double_counted_and_missing_usage_stays_unknown():
    call = measured_call()
    assert summarize([call])['total_tokens'] == 1100
    unknown = {'model_id': 'unknown'}
    read_usage({}, unknown)
    total = summarize([call, unknown])
    assert total['input_tokens'] is None and total['known_input_tokens'] == 1000
    assert total['missing_usage_calls'] == 1
    assert total['cached_input_tokens'] is None and total['known_cached_input_tokens'] == 200


@pytest.mark.parametrize('mode', list(InteractionState))
def test_reply_timing_excludes_idle_and_cached_replays_do_not_charge(research, monkeypatch, mode):
    p = research
    p.session.interaction_state = mode; p.engine.store.save(p.session)
    p.repo.register_usage_design(p.session.design_id, mode.value)
    clock = [0.0]
    monkeypatch.setattr('ece329_workflow.telemetry.perf_counter', lambda: clock[0])
    original = p.transport.create
    def create(body):
        clock[0] += 2
        response = original(body)
        response['usage'] = {'input_tokens': 1000, 'output_tokens': 100, 'input_tokens_details': {'cached_tokens': 200}}
        return response
    p.transport.create = create
    request = {'message': QUESTION, 'turn_id': 'usage-turn-001', 'model': 'gpt-5.4-mini'}
    first = p.engine.process_turn(p.session.design_id, request)
    count = len(p.transport.requests)
    assert first['timing']['reply_ms'] == first['timing']['active_ms'] == count * 2000
    clock[0] += 3600  # User leaves the page for an hour.
    assert p.engine.process_turn(p.session.design_id, request) == first
    assert len(p.transport.requests) == count
    second = p.engine.process_turn(p.session.design_id, {**request, 'turn_id': 'usage-turn-002'})
    assert second['timing']['active_ms'] == len(p.transport.requests) * 2000
    snapshot = p.engine.get_design(p.session.design_id)
    assert snapshot['timing']['active_ms'] == second['timing']['active_ms']
    assert len(snapshot['reply_timings']) == 2
    stats = p.repo.usage_inbox()['designs'][0]['usage']
    assert stats['input_tokens'] == len(p.transport.requests) * 1000
    assert stats['output_tokens'] == len(p.transport.requests) * 100
    assert_no_fees(stats)


def test_feedback_invalid_json_and_manual_retry_both_count(pipeline):
    p = pipeline
    calls = []
    def create(body):
        calls.append(body)
        response = {'output_text': 'invalid'} if len(calls) == 1 else extraction_response(body)
        response['usage'] = {'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':200}}
        return response
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='test',transport=SimpleNamespace(create=create)), {})
    ticket = submit(p)[2];p.service.run_once()
    first = p.repo.feedback_detail(ticket['id'])
    assert first['status'] == 'failed' and first['usage']['total_tokens'] == 1100
    p.service.retry(p.session.design_id,ticket['id'],{});p.service.run_once()
    final = p.repo.feedback_detail(ticket['id'])
    assert final['status'] == 'candidate' and final['usage']['run_count'] == 2
    assert final['usage']['call_count'] == 3 and final['usage']['total_tokens'] == 3300
    assert_no_fees(final['usage'])
    assert p.repo.experiences()[0]['usage'] == final['usage']
    stats = p.repo.usage_inbox()['designs'][0]['usage']
    assert stats['dialogue']['active_ms'] == 0 and stats['feedback']['call_count'] == 3
    assert not p.service.run_once()


def test_usage_ledger_survives_restart_and_deduplicates_records():
    path = workspace_temp_path('usage.sqlite')
    repo = ExperienceStore(path)
    repo.register_usage_design('design-a','EMVR_DIRECT')
    record = {'id':'run-one','design_id':'design-a','created':1,'mode':'EMVR_DIRECT',
              'calls':[measured_call()], 'latency_ms':1234,'revision':1}
    repo.record_telemetry(record);repo.record_telemetry(record);repo.close()
    restored = ExperienceStore(path)
    stats = restored.usage_inbox()['designs'][0]
    assert stats['complete'] == 1 and stats['usage']['call_count'] == 1
    assert stats['usage']['active_ms'] == 1234
    assert stats['usage']['total_tokens'] == 1100
    assert_no_fees(stats['usage'])
    restored.delete_design('design-a')
    assert restored.usage_inbox()['total'] == 0
    restored.close()


def test_maintainer_usage_list_is_private_and_paginates(pipeline):
    p = pipeline
    for i in range(53): p.repo.register_usage_design(f'design-{i}', 'EMVR_DIRECT')
    route = '/v1/feedback/usage'
    assert call_api(p.api,'GET',route)[0].startswith('401')
    assert call_api(p.api,'GET',route,request_headers=p.auth)[0].startswith('401')
    response = call_api(p.api,'GET',route,request_headers={'X-ECE329-Feedback-Admin-Token':'maintainer-token'})[2]
    assert response['total'] == 53 and len(response['designs']) == 50
    assert len(p.repo.usage_inbox(50)['designs']) == 3
    with pytest.raises(ValueError): p.repo.usage_inbox(-1)


def test_design_translation_is_metered_but_cache_hits_are_not_charged(research):
    p = research
    p.repo.register_usage_design(p.session.design_id, p.session.interaction_state.value)
    original = p.transport.create
    def create(body):
        response = original(body)
        response['usage'] = {'input_tokens': 20, 'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 0}}
        return response
    p.transport.create = create
    body = {'texts': ['阶段'], 'language': 'en', 'design_id': p.session.design_id}
    assert call_api(p.api,'POST','/v1/localization',body,request_headers=p.auth)[0].startswith('200')
    assert call_api(p.api,'POST','/v1/localization',body,request_headers=p.auth)[0].startswith('200')
    stats = p.repo.design_usage(p.session.design_id)
    assert stats['translation']['call_count'] == 1 and stats['translation']['total_tokens'] == 30
    assert stats['dialogue']['active_ms'] == 0


def test_legacy_feedback_with_missing_attempt_usage_remains_incomplete(pipeline):
    p = pipeline
    ticket = submit(p)[2]
    job = p.repo.claim()
    p.repo.finish(job,error='old unmetered failure')
    stats = p.repo.feedback_detail(ticket['id'])['usage']
    assert stats['complete'] is False and stats['total_tokens'] is None
    assert_no_fees(stats)


def test_deleted_design_cannot_be_resurrected_by_late_usage():
    from ece329_workflow.store import SQLiteSessionStore
    from ece329_workflow.models import DesignSession
    from ece329_workflow.usage import measure_translation, CURRENT_USAGE
    path = workspace_temp_path('deleted-usage.sqlite')
    sessions = SQLiteSessionStore(path)
    session = DesignSession('delete-usage', InteractionState.EMVR_DIRECT)
    sessions.save(session)
    repo = ExperienceStore(path)
    repo.register_usage_design(session.design_id, session.interaction_state.value)
    with measure_translation(repo, session.design_id):
        CURRENT_USAGE.get()[0].append(measured_call())
        sessions.delete(session.design_id)
    assert repo.usage_inbox()['total'] == 0
    with repo.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM usage_runs').fetchone()[0] == 0
    repo.close()


@pytest.mark.parametrize('mode', list(InteractionState))
def test_new_design_is_registered_without_charging_idempotent_creation(pipeline, mode):
    p = pipeline
    body={'idea':'我想研究磁场', 'interaction_state':mode.value}
    headers={'Idempotency-Key':'new-design-usage-001'}
    status,_,response = call_api(p.api,'POST','/v1/designs',body,request_headers=headers)
    assert status.startswith('201')
    assert response['timing']['reply_ms'] >= 0
    assert call_api(p.api,'POST','/v1/designs',body,request_headers=headers)[2] == response
    stats = p.repo.usage_inbox()
    assert stats['total'] == 1 and stats['designs'][0]['complete'] == 1
    assert stats['designs'][0]['usage']['run_count'] == 1
    view = call_api(p.api,'GET','/v1/feedback/usage',request_headers={'X-ECE329-Feedback-Admin-Token':'maintainer-token'})[2]['designs'][0]
    assert view['current_state']['stage'] == response['current_stage']
    assert view['current_state']['status'] == 'active'
    assert view['usage']['last_dialogue']['stage'] == response['current_stage']


FEE_FIELDS = {'estimated_cost_usd', 'known_cost_usd', 'unpriced_calls', 'currency',
              'pricing_key', 'price_rates_usd_per_million'}


def assert_no_fees(value):
    if isinstance(value, dict):
        assert not FEE_FIELDS.intersection(value)
        for item in value.values():
            assert_no_fees(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_fees(item)


def test_legacy_pricing_fields_are_ignored_by_usage_summaries(monkeypatch):
    # Old deployment settings and persisted pricing must not affect token accounting.
    monkeypatch.setenv('ECE329_MODEL_PRICING_JSON', 'invalid obsolete configuration')
    repo = ExperienceStore()
    try:
        repo.register_usage_design('legacy', 'GUIDED_DESIGN')
        call = {**measured_call(), 'estimated_cost_usd': 0.0025,
                'pricing_key': 'test', 'price_rates_usd_per_million': {'input': 2}}
        repo.record_telemetry({'id': 'legacy-run', 'design_id': 'legacy', 'created': 1,
                              'calls': [call], 'latency_ms': 100})
        stats = repo.usage_inbox()['designs'][0]['usage']
        assert stats['total_tokens'] == 1100
        assert stats['active_ms'] == 100
        assert_no_fees(stats)
    finally:
        repo.close()


def test_new_measured_call_has_no_pricing_fields():
    from ece329_workflow.usage import UsageTransport
    calls = []
    transport = SimpleNamespace(create=lambda payload: {'usage': {'input_tokens': 10, 'output_tokens': 5}})
    UsageTransport(transport, calls).create({'model': 'deepseek-flash'})
    assert calls[0]['input_tokens'] == 10 and calls[0]['output_tokens'] == 5
    assert calls[0]['provider'] == 'deepseek'
    assert_no_fees(calls)
