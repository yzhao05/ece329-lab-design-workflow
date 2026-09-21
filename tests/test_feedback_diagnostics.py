"""Diagnostics preserve evidence, distinguish failures, and never add model calls."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from ece329_workflow.experience import ModelExperienceExtractor
from ece329_workflow.feedback_diagnostics import diagnostic_for, transport_metadata, TransportResponse
from ece329_workflow.openai_generator import ModelHTTPError, ModelTimeoutError
from ece329_workflow.models import InteractionState
from tests.test_feedback_pipeline import pipeline, submit, extraction_draft, extraction_check
from tests.test_feedback_failover import configure


def run_response(p, draft, check=None):
    calls = []
    def create(body):
        calls.append(body)
        response = TransportResponse(draft if len(calls) == 1 else {'output_text': json.dumps(check or extraction_check())})
        response.transport_metadata = {'http_status': 200, 'request_id': 'req_test_1234'}
        return response
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='gpt-5.4-mini',
        transport=SimpleNamespace(create=create)), {'ECE329_FEEDBACK_MODEL': 'gpt-5.4-mini'})
    ticket = submit(p)[2]
    p.service.run_once()
    return p.repo.feedback_detail(ticket['id']), calls


@pytest.mark.parametrize('kind,path,reason', [
    ('long', 'candidate.trigger', 'max_length'),
    ('missing', 'candidate.trigger', 'required'),
    ('type', 'candidate.trigger', 'type'),
    ('reference', 'diagnosis.facts', 'evidence_reference'),
])
def test_precise_validation_failure_is_persisted_without_output_content(pipeline, kind, path, reason):
    draft = extraction_draft()
    if kind == 'long': draft['candidate']['trigger'] = '秘' * 168
    if kind == 'missing': del draft['candidate']['trigger']
    if kind == 'type': draft['candidate']['trigger'] = 168
    if kind == 'reference': draft['diagnosis']['facts'] = [{'evidence_ref': 'turn:999', 'observation': 'secret'}]
    row, calls = run_response(pipeline, {'output_text': json.dumps(draft)})
    last = row['last_analysis']; d = last['diagnostic']
    assert d['source'] == 'local_validation' and d['field_path'] == path and d['reason'] == reason
    assert last['phase'] == ('validate_evidence' if kind == 'reference' else 'validate_draft')
    if kind == 'long': assert d['limit'] == 140 and d['actual_length'] == 168
    assert d['request_id'] == 'req_test_1234' and d['http_status'] == 200
    assert len(calls) == row['attempts'] == 1 and not pipeline.service.run_once()
    assert last['calls'][0]['input_tokens'] is None
    assert 'secret' not in json.dumps(last) and '秘' not in json.dumps(last, ensure_ascii=False)


@pytest.mark.parametrize('response,reason', [
    ({'output_text': ''}, 'empty_output'), ({'output_text': 'private'}, 'invalid_json'),
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}, 'output_limit'),
])
def test_parse_failure_is_not_reported_as_field_validation(pipeline, response, reason):
    row, calls = run_response(pipeline, response)
    last = row['last_analysis']
    assert last['phase'] == 'parse_draft' and last['diagnostic']['source'] == 'local_parser'
    assert last['diagnostic']['reason'] == reason and len(calls) == 1
    assert last['phases'][-1] == {'phase': 'parse_draft', 'status': 'failed'}
    assert 'private' not in json.dumps(last)


@pytest.mark.parametrize('error,reason', [
    (ModelTimeoutError('private'), 'timeout'), (ModelHTTPError(429), 'rate_limited'),
    (ModelHTTPError(429, 'insufficient_quota'), 'quota_exhausted'),
    (ModelHTTPError(400, 'context_length_exceeded'), 'input_limit'),
    (TypeError('private'), 'unexpected_exception'), (KeyError('private'), 'unexpected_exception'),
    (ValueError('private'), 'unexpected_exception'),
])
def test_request_and_code_failures_have_correct_source_and_no_added_retries(pipeline, error, reason):
    calls, _ = configure(pipeline, error, backup=False)
    ticket = submit(pipeline)[2]; pipeline.service.run_once()
    row = pipeline.repo.feedback_detail(ticket['id']); last = row['last_analysis']
    assert last['diagnostic']['reason'] == reason and last['phase'] == 'request_draft'
    assert len(calls) == row['attempts'] == 1 and not pipeline.service.run_once()
    assert last['calls'][0]['input_tokens'] is None and last['calls'][0]['output_tokens'] is None
    if reason == 'unexpected_exception':
        assert last['code'] == 'internal_error' and last['diagnostic']['source'] == 'backend'
    assert 'private' not in json.dumps(row)


@pytest.mark.parametrize('kind,result', [('valid','candidate'), ('not_useful','insufficient_evidence'), ('check_failed','check_not_passed')])
def test_analysis_outcomes_complete_without_becoming_system_errors(pipeline, kind, result, monkeypatch):
    monkeypatch.setenv('ECE329_RELEASE_VERSION', 'diagnostics-test-1')
    draft = extraction_draft()
    if kind == 'not_useful': draft['candidate']['useful'] = False
    check = extraction_check(evidence_supported=kind != 'check_failed')
    row, calls = run_response(pipeline, {'output_text': json.dumps(draft)}, check)
    last = row['last_analysis']
    assert last['status'] == 'completed' and last['phase'] == 'completed'
    assert last['diagnostic']['source'] == 'analysis_result' and last['diagnostic']['code'] == result
    assert last['feedback_id'] == row['id'] and last['release_version'] == 'diagnostics-test-1'
    assert last['elapsed_ms'] >= 0 and last['design_revision'] == 2
    assert all(step['status'] == 'completed' for step in last['phases'])
    assert len(calls) == 2
    assert [c['max_output_tokens'] for c in last['calls']] == [8192, 8192]
    assert [c['reasoning_effort'] for c in last['calls']] == ['low', 'none']


@pytest.mark.parametrize('method', ['record_attempt', 'finish', 'record_feedback_usage'])
def test_persistence_failures_log_correlation_without_sensitive_error_text(pipeline, method, monkeypatch, caplog):
    calls, _ = configure(pipeline, None, backup=False)
    ticket = submit(pipeline)[2]
    def broken(*args, **kwargs): raise sqlite3.OperationalError('private-database-secret')
    monkeypatch.setattr(pipeline.repo, method, broken)
    if method == 'record_feedback_usage':
        pipeline.service.run_once()
        assert pipeline.repo.feedback_detail(ticket['id'])['last_analysis']['usage_persistence'] == 'failed'
    else:
        with pytest.raises(sqlite3.OperationalError): pipeline.service.run_once()
    assert ticket['id'] in caplog.text and pipeline.session.design_id in caplog.text
    assert 'private-database-secret' not in caplog.text and len(calls) == 2
    assert not pipeline.service.run_once()  # Existing lease prevents an immediate repeated request.


def test_provider_metadata_is_conservative_and_never_infers_quota_from_http_only():
    assert diagnostic_for(ModelHTTPError(429))['reason'] == 'rate_limited'
    assert diagnostic_for(ModelHTTPError(400, 'private-error-body'))['provider_error_code'] is None
    assert transport_metadata({'x-request-id': 'sk-secret-value'}, 200)['request_id'] is None
    assert transport_metadata({'x-request-id': 'prefix-secret-value'}, 200, 'secret')['request_id'] is None
    assert transport_metadata({'x-request-id': 'req_0123456789'}, 200)['request_id'] == 'req_0123456789'


def test_duplicate_is_an_analysis_result(pipeline):
    configure(pipeline, None, backup=False)
    submit(pipeline); pipeline.service.run_once()
    ticket = submit(pipeline, request_id='another-request-0002')[2]; pipeline.service.run_once()
    last = pipeline.repo.feedback_detail(ticket['id'])['last_analysis']
    assert last['result'] == 'duplicate' and last['diagnostic']['source'] == 'analysis_result'


@pytest.mark.parametrize('provider', ['openai', 'deepseek'])
def test_actual_transport_preserves_http_metadata_and_usage(provider, monkeypatch):
    from io import BytesIO
    from ece329_workflow.openai_generator import OpenAIResponsesHTTPTransport
    from ece329_workflow.provider_transport import DeepSeekJSONTransport
    from ece329_workflow.usage import UsageTransport, PriceBook
    raw = ({'output_text': '{}', 'usage': {'input_tokens': 20, 'output_tokens': 10}} if provider == 'openai' else
           {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10}})
    class Response(BytesIO):
        status = 200
        headers = {'x-request-id': 'req_diagnostics_123'}
    monkeypatch.setattr('ece329_workflow.openai_generator.urlopen', lambda *a, **kw: Response(json.dumps(raw).encode()))
    transport = (OpenAIResponsesHTTPTransport('test-only') if provider == 'openai' else DeepSeekJSONTransport('test-only'))
    calls = []
    UsageTransport(transport, calls, PriceBook()).create({'model': 'gpt-5.4-mini' if provider == 'openai' else 'deepseek-flash',
        'reasoning': {'effort': 'low'}, 'max_output_tokens': 8192, '_workflow_output_cap': 8192,
        'text': {'format': {'name': 'feedback_experience', 'schema': {'type': 'object', 'properties': {}}}}})
    assert calls[0]['request_id'] == 'req_diagnostics_123' and calls[0]['http_status'] == 200
    assert calls[0]['input_tokens'] == 20 and calls[0]['output_tokens'] == 10


def test_http_error_keeps_safe_code_request_id_and_no_error_body(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from ece329_workflow.openai_generator import OpenAIResponsesHTTPTransport
    error = HTTPError('https://test.invalid', 429, 'private', {'x-request-id': 'req_diagnostics_123'},
                      BytesIO(b'{"error":{"code":"insufficient_quota","message":"private secret"}}'))
    def fail(*a, **kw): raise error
    monkeypatch.setattr('ece329_workflow.openai_generator.urlopen', fail)
    with pytest.raises(ModelHTTPError) as caught: OpenAIResponsesHTTPTransport('test-only').create({})
    d = diagnostic_for(caught.value)
    assert d['http_status'] == 429 and d['provider_error_code'] == 'insufficient_quota'
    assert d['request_id'] == 'req_diagnostics_123' and 'private' not in json.dumps(d)


def test_input_excerpt_warning_uses_recorded_flags_not_text():
    from ece329_workflow.feedback_diagnostics import evidence_was_truncated
    assert evidence_was_truncated({'current_state': {'pending_excerpt_truncated': True}})
    assert evidence_was_truncated({'recent_turns': [{'truncated_fields': ['assistant']}]})
    assert not evidence_was_truncated({'reported_turn': {'assistant': 'truncated_fields: true'}})


def test_deepseek_draft_validation_retains_the_exact_field_failure(pipeline):
    from ece329_workflow.provider_transport import SchemaCheckedResponse
    draft = extraction_draft(); draft['candidate']['trigger'] = 'x' * 168
    pipeline.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='deepseek-flash', transport=SimpleNamespace(
        create=lambda _: SchemaCheckedResponse(json.dumps(draft), {}, 'stop', {}))), {})
    ticket = submit(pipeline)[2]; pipeline.service.run_once()
    last = pipeline.repo.feedback_detail(ticket['id'])['last_analysis']
    assert last['diagnostic']['field_path'] == 'candidate.trigger'
    assert last['diagnostic']['actual_length'] == 168 and last['phase'] == 'validate_draft'


@pytest.mark.parametrize('mode', list(InteractionState))
def test_retry_diagnostics_do_not_feed_back_into_model_evidence(pipeline, mode):
    p = pipeline
    p.session.interaction_state = mode
    p.engine.store.save(p.session)
    calls, generator = configure(p, ModelTimeoutError('private'), backup=False)
    ticket = submit(p)[2]; p.service.run_once()
    first_input = calls[0][1]['input'][0]['content'][0]['text']
    p.repo.retry(p.session.design_id, ticket['id'])
    p.service.run_once()
    assert calls[1][1]['input'][0]['content'][0]['text'] == first_input
    assert len(p.repo.feedback_detail(ticket['id'])['evidence']['analysis_attempts']) == 2
    assert json.loads(first_input)['evidence']['mode'] == mode.value
    assert not p.service.run_once()


@pytest.mark.parametrize('mode', list(InteractionState))
def test_null_legacy_context_is_recorded_without_an_unhandled_worker_error(pipeline, mode):
    p = pipeline; p.session.interaction_state = mode; p.engine.store.save(p.session)
    configure(p, ModelTimeoutError('private'), backup=False)
    ticket = submit(p)[2]
    with p.repo.connection() as db:
        payload = json.loads(db.execute('SELECT payload FROM feedback_tickets WHERE id=?', (ticket['id'],)).fetchone()[0])
        payload['evidence'].update(event_chain=None, recent_turns=None)
        payload['analysis_attempts'] = None
        db.execute('UPDATE feedback_tickets SET payload=? WHERE id=?', (json.dumps(payload), ticket['id']))
    assert p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'failed' and row['attempts'] == 1
    assert row['last_analysis']['diagnostic']['source'] == 'transport'
    assert row['last_analysis']['diagnostic']['reason'] == 'timeout'
    assert not p.service.run_once()


@pytest.mark.parametrize('response', [{'output': None}, {'output':[{'content': None}]}, {'output_text':42}])
def test_malformed_provider_envelopes_are_parser_errors(pipeline, response):
    row, calls = run_response(pipeline, response)
    assert row['last_analysis']['phase'] == 'parse_draft'
    assert row['last_analysis']['diagnostic']['reason'] == 'invalid_response_shape'
    assert len(calls) == 1 and row['attempts'] == 1
