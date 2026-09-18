"""Feedback provider failover shares a persisted, finite analysis budget."""
import json
from types import SimpleNamespace

import pytest

from ece329_workflow.experience import MAX_ATTEMPTS, ModelExperienceExtractor
from ece329_workflow.models import InteractionState
from ece329_workflow.openai_generator import (ModelConnectionError, ModelHTTPError,
                                             ModelOutputError, ModelTimeoutError)
from ece329_workflow.provider_transport import ProviderResponsesTransport
from tests.test_feedback_pipeline import pipeline, submit, extraction_response


def configure(p, failure, *, primary='gpt-5.4-mini', backup=True, secondary_failure=None):
    calls = []
    def create(provider, body):
        calls.append((provider, body))
        error = failure if provider == ('deepseek' if primary.startswith('deepseek') else 'openai') else secondary_failure
        if error:
            raise error
        return extraction_response(body)
    transports = {name: SimpleNamespace(create=lambda body, provider=name: create(provider, body),
                                         _timeout_seconds=200) for name in ('openai', 'deepseek')}
    if not backup:
        transports['deepseek' if primary.startswith('gpt') else 'openai'] = None
    generator = SimpleNamespace(model=primary, reasoning_effort='medium',
        allowed_models=('gpt-5.4-mini', 'deepseek-flash', 'deepseek-v4-pro'),
        transport=ProviderResponsesTransport(**transports))
    p.service.extractor = ModelExperienceExtractor(generator)
    return calls, generator


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('primary', ['gpt-5.4-mini', 'deepseek-flash'])
def test_connection_failure_uses_other_configured_provider(pipeline, mode, primary):
    p = pipeline
    p.session.interaction_state = mode
    p.engine.store.save(p.session)
    calls, generator = configure(p, ModelConnectionError('private-key-and-body'), primary=primary)
    ticket = submit(p)[2]
    assert p.service.run_once()
    assert not p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'candidate' and row['attempts'] == 2 and row['max_attempts'] == 10
    history = row['evidence']['analysis_attempts']
    assert [h['status'] for h in history] == ['failed', 'completed']
    assert history[0]['code'] == 'model_connection_error' and history[0]['phase'] == 'draft'
    assert len(calls) == 3 and calls[0][0] != calls[1][0] == calls[2][0]
    assert 'private-key-and-body' not in json.dumps(row)
    assert generator.model == primary  # Never change the shared dialogue generator.
    assert p.service.extractor.lease_seconds() == 460
    for provider, body in calls:
        assert ('_workflow_output_cap' in body) == (provider == 'deepseek')


@pytest.mark.parametrize('error', [ModelTimeoutError('secret'), ModelHTTPError(401, 'secret'),
                                  ModelHTTPError(429), ModelHTTPError(503)])
def test_api_failure_can_fall_back(pipeline, error):
    p = pipeline
    configure(p, error)
    ticket = submit(p)[2]
    p.service.run_once()
    detail = p.repo.feedback_detail(ticket['id'])
    assert detail['status'] == 'candidate'
    assert detail['evidence']['analysis_attempts'][0]['code'] == error.diagnostic_code
    assert 'secret' not in json.dumps(detail)


@pytest.mark.parametrize('error', [ModelOutputError('private'), ValueError('private')])
def test_invalid_output_is_diagnosable_and_does_not_trigger_provider_loop(pipeline, error):
    p = pipeline
    calls, _ = configure(p, error)
    ticket = submit(p)[2]
    p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert len(calls) == 1 and row['status'] == 'failed' and row['can_retry']
    assert 'model_output_invalid' in row['error']


def test_no_unconfigured_or_disallowed_backup(pipeline):
    p = pipeline
    calls, generator = configure(p, ModelConnectionError('offline'), backup=False)
    submit(p)
    p.service.run_once()
    assert len(calls) == 1
    generator.transport.providers['deepseek'] = SimpleNamespace(create=extraction_response)
    generator.allowed_models = ('gpt-5.4-mini',)
    assert p.service.extractor.models() == ['gpt-5.4-mini']


def test_ten_attempts_total_including_automatic_backup_and_manual_retry(pipeline):
    p = pipeline
    calls, _ = configure(p, ModelHTTPError(503), secondary_failure=ModelHTTPError(429))
    ticket = submit(p)[2]
    for expected in range(2, MAX_ATTEMPTS + 1, 2):
        p.service.run_once()
        assert not p.service.run_once()
        row = p.repo.feedback_detail(ticket['id'])
        assert row['attempts'] == expected and row['status'] == 'failed'
        if row['can_retry']:
            p.repo.retry(p.session.design_id, ticket['id'])
    assert len(calls) == 10 and not row['can_retry']
    assert len(row['evidence']['analysis_attempts']) == 10
    with pytest.raises(ValueError):
        p.repo.retry(p.session.design_id, ticket['id'])


def test_last_attempt_cannot_start_backup(pipeline):
    p = pipeline
    calls, _ = configure(p, ModelConnectionError('offline'))
    ticket = submit(p)[2]
    with p.repo.connection() as db:
        db.execute('UPDATE feedback_tickets SET attempts=9')
    p.service.run_once()
    assert len(calls) == 1
    assert not p.repo.feedback_detail(ticket['id'])['can_retry']


def test_old_three_attempt_limit_records_gain_remaining_budget(pipeline):
    p = pipeline
    ticket = submit(p)[2]
    with p.repo.connection() as db:
        db.execute("UPDATE feedback_tickets SET attempts=3,status='failed'")
    assert p.repo.feedback_detail(ticket['id'])['can_retry']
    p.repo.retry(p.session.design_id, ticket['id'])
    p.service.run_once()
    assert p.repo.feedback_detail(ticket['id'])['attempts'] == 4


def test_deleted_or_reclaimed_job_cannot_start_backup_or_store_diagnostics(pipeline):
    p = pipeline
    ticket = submit(p)[2]
    old = p.repo.claim(lease_seconds=-1)
    new = p.repo.claim()
    assert not p.repo.record_attempt(old, {'status': 'failed'})
    assert not p.repo.reserve_fallback(old, 300)
    p.repo.delete_design(p.session.design_id)
    assert not p.repo.record_attempt(new, {'status': 'failed'})
    assert not p.repo.reserve_fallback(new, 300)


def test_critic_connection_failure_restarts_analysis_on_backup(pipeline):
    p = pipeline
    calls, generator = configure(p, None)
    def primary(body):
        calls.append(('openai', body))
        if body['text']['format']['name'].endswith('_check'):
            raise ModelTimeoutError('private')
        return extraction_response(body)
    generator.transport.providers['openai'].create = primary
    ticket = submit(p)[2]
    p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert len(calls) == 4 and row['status'] == 'candidate' and row['attempts'] == 2
    assert row['evidence']['analysis_attempts'][0]['phase'] == 'check'


def test_failed_evidence_check_is_not_retried_on_a_more_agreeable_model(pipeline):
    p = pipeline
    calls, generator = configure(p, None)
    def primary(body):
        calls.append(('openai', body))
        response = extraction_response(body)
        if body['text']['format']['name'].endswith('_check'):
            content = json.loads(response['output_text'])
            content['evidence_supported'] = False
            response['output_text'] = json.dumps(content)
        return response
    generator.transport.providers['openai'].create = primary
    ticket = submit(p)[2]
    p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert len(calls) == 2 and row['status'] == 'no_learning' and row['attempts'] == 1
    assert not row['can_retry'] and p.repo.experiences() == []
