"""Explicit alternative-provider retries and actionable output diagnostics."""
import json
from types import SimpleNamespace

import pytest

from ece329_workflow.experience import ModelExperienceExtractor
from ece329_workflow.models import InteractionState
from ece329_workflow.openai_generator import ModelOutputError, ModelConfigurationError, ModelConnectionError
from tests.test_feedback_pipeline import pipeline, submit, extraction_response, extraction_draft
from tests.test_feedback_failover import configure
from tests.test_security_and_store import call_api


@pytest.mark.parametrize('mode', list(InteractionState))
def test_user_can_retry_invalid_output_with_alternative_provider_without_changing_design(pipeline, mode):
    p = pipeline
    p.session.interaction_state = mode
    p.engine.store.save(p.session)
    calls, generator = configure(p, ModelOutputError('private'))
    ticket = submit(p)[2]
    p.service.run_once()
    before = p.engine.store.get(p.session.design_id)
    base = f'/v1/designs/{p.session.design_id}/feedback'
    data = call_api(p.api, 'GET', base, request_headers=p.auth)[2]
    assert [m['provider'] for m in data['analysis_options']['models']] == ['openai', 'deepseek']
    assert data['feedback'][0]['last_analysis']['provider'] == 'openai'
    path = base + '/' + ticket['id'] + '/retry'
    assert call_api(p.api, 'POST', path, {'model':'deepseek-flash'})[0].startswith('401')
    assert call_api(p.api, 'POST', path, {'model':'deepseek-flash'}, request_headers=p.auth)[0].startswith('202')
    assert call_api(p.api, 'POST', path, {'model':'deepseek-flash'}, request_headers=p.auth)[0].startswith('400')
    p.service.run_once()
    result = p.repo.feedback_detail(ticket['id'])
    assert result['status'] == 'candidate' and result['attempts'] == 2
    assert result['last_analysis']['model'] == 'deepseek-flash'
    assert [provider for provider, _ in calls] == ['openai', 'deepseek', 'deepseek']
    assert generator.model == 'gpt-5.4-mini' and p.engine.store.get(p.session.design_id) == before


@pytest.mark.parametrize('body', [{'model':'unknown-model'}, {'model':None}, {'model':[]}, {'provider':'deepseek'}, {'model':'deepseek-flash','key':'private'}])
def test_invalid_manual_selection_does_not_queue_or_consume_attempts(pipeline, body):
    p = pipeline
    configure(p, ModelOutputError('bad'))
    ticket = submit(p)[2];p.service.run_once()
    path = f"/v1/designs/{p.session.design_id}/feedback/{ticket['id']}/retry"
    assert call_api(p.api, 'POST', path, body, request_headers=p.auth)[0].startswith('400')
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'failed' and row['attempts'] == 1


def test_explicit_selection_does_not_fall_back_to_the_provider_user_switched_away_from(pipeline):
    p = pipeline
    calls, _ = configure(p, ModelOutputError('bad'), secondary_failure=ModelConnectionError('offline'))
    ticket = submit(p)[2];p.service.run_once()
    p.service.retry(p.session.design_id, ticket['id'], {'model':'deepseek-flash'})
    p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['attempts'] == 2 and row['status'] == 'failed' and len(calls) == 2
    p.service.retry(p.session.design_id, ticket['id'], {})
    assert p.repo.feedback_detail(ticket['id'])['evidence']['analysis_model'] is None


def test_disabled_provider_is_revalidated_after_queueing(pipeline):
    p = pipeline
    calls, generator = configure(p, ModelOutputError('bad'))
    ticket = submit(p)[2];p.service.run_once()
    p.service.retry(p.session.design_id, ticket['id'], {'model':'deepseek-flash'})
    generator.transport.providers['deepseek'] = None
    p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert len(calls) == 1 and row['status'] == 'failed'
    assert row['last_analysis']['code'] == 'model_configuration_error'


def test_queued_selection_survives_changes_to_provider_representatives(pipeline):
    p = pipeline
    calls, generator = configure(p, ModelOutputError('bad'))
    ticket = submit(p)[2]; p.service.run_once()
    p.service.retry(p.session.design_id, ticket['id'], {'model': 'deepseek-flash'})
    # A restart may change the default/ordering without disabling the queued model.
    generator.model = 'deepseek-v4-pro'
    generator.allowed_models = ('deepseek-v4-pro', 'gpt-5.4-mini', 'deepseek-flash')
    assert 'deepseek-flash' not in p.service.extractor.models()
    assert p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'candidate' and row['attempts'] == 2
    assert [body['model'] for _, body in calls[1:]] == ['deepseek-flash', 'deepseek-flash']
    assert not p.service.run_once()


@pytest.mark.parametrize('remove', ['allowlist', 'provider'])
def test_unavailable_primary_is_not_advertised_or_called(pipeline, remove):
    p = pipeline
    calls, generator = configure(p, ModelOutputError('must not call primary'))
    if remove == 'allowlist':
        generator.allowed_models = ('deepseek-flash', 'deepseek-v4-pro')
    else:
        generator.transport.providers['openai'] = None
    assert [m['id'] for m in p.service.analysis_options()['models']] == ['deepseek-flash']
    ticket = submit(p)[2]; p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'candidate' and row['attempts'] == 1
    assert [provider for provider, _ in calls] == ['deepseek', 'deepseek']


def test_no_configured_models_fails_once_instead_of_recording_no_learning_or_looping(pipeline):
    p = pipeline
    calls, generator = configure(p, ModelOutputError('bad'))
    generator.allowed_models = ()
    assert p.service.analysis_options()['models'] == []
    ticket = submit(p)[2]
    assert p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'failed' and row['attempts'] == 1
    assert row['last_analysis']['code'] == 'model_configuration_error'
    assert calls == [] and not p.service.run_once()


def test_disallowed_queued_model_is_not_replaced_with_another_model(pipeline):
    p = pipeline
    calls, generator = configure(p, ModelOutputError('bad'))
    ticket = submit(p)[2]; p.service.run_once()
    p.service.retry(p.session.design_id, ticket['id'], {'model': 'deepseek-flash'})
    generator.allowed_models = ('gpt-5.4-mini', 'deepseek-v4-pro')
    p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['status'] == 'failed' and row['attempts'] == 2
    assert row['last_analysis']['code'] == 'model_configuration_error'
    assert len(calls) == 1 and not p.service.run_once()


@pytest.mark.parametrize('kind,reason', [('truncated','output_limit'),('json','invalid_json'),
                                       ('schema','schema_validation'),('reference','evidence_reference')])
def test_output_failures_explain_why_without_leaking_content(pipeline, kind, reason):
    p = pipeline
    def create(body):
        if kind == 'truncated':
            return {'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}, 'output_text':'private'}
        if kind == 'json': return {'output_text':'private'}
        draft = extraction_draft()
        if kind == 'schema': draft['candidate']['trigger'] = 'private' * 200
        if kind == 'reference': draft['diagnosis']['facts'] = [{'evidence_ref':'turn:999','observation':'private'}]
        return {'output_text':json.dumps(draft)}
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='test',transport=SimpleNamespace(create=create)), {})
    ticket = submit(p)[2];p.service.run_once()
    row = p.repo.feedback_detail(ticket['id'])
    assert row['last_analysis']['reason'] == reason and row['last_analysis']['phase'] == 'draft'
    assert row['attempts'] == 1 and row['status'] == 'failed'
    assert 'private' not in json.dumps(row)


def test_feedback_budget_and_reasoning_are_independent_and_configurable():
    requests = []
    def create(body):
        requests.append(body)
        return extraction_response(body)
    generator = SimpleNamespace(model='test',reasoning_effort='high',transport=SimpleNamespace(create=create))
    extractor = ModelExperienceExtractor(generator, {})
    extractor.extract({'evidence':{'current_state':{'stage':'test'}}})
    assert [r['max_output_tokens'] for r in requests] == [8192,4096]
    assert all(r['reasoning']['effort'] == 'low' for r in requests)
    schema = requests[0]['text']['format']['schema']
    assert schema['properties']['candidate']['properties']['trigger']['maxLength'] == 140
    assert schema['properties']['diagnosis']['properties']['facts']['items']['properties']['evidence_ref']['enum'] == ['current_state']
    requests.clear()
    ModelExperienceExtractor(generator, {'ECE329_FEEDBACK_REASONING_EFFORT':'none',
        'ECE329_FEEDBACK_MAX_OUTPUT_TOKENS':'12000','ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS':'5000'}).extract({})
    assert [r['max_output_tokens'] for r in requests] == [12000,5000]
    assert all(r['reasoning']['effort'] == 'none' for r in requests)


def test_check_phase_validation_failure_is_not_reported_as_a_draft_failure(pipeline):
    p = pipeline
    calls = []
    def create(body):
        calls.append(body)
        return {'output_text':'{"issues": 123}'} if len(calls) == 2 else extraction_response(body)
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='test',transport=SimpleNamespace(create=create)), {})
    ticket = submit(p)[2];p.service.run_once()
    last = p.repo.feedback_detail(ticket['id'])['last_analysis']
    assert last['phase'] == 'check' and last['reason'] == 'schema_validation' and len(calls) == 2


def test_deepseek_length_finish_reports_output_limit(pipeline):
    from ece329_workflow.provider_transport import SchemaCheckedResponse
    p = pipeline
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='deepseek-flash',transport=SimpleNamespace(
        create=lambda _: SchemaCheckedResponse('', {}, 'length', {}))), {})
    ticket = submit(p)[2];p.service.run_once()
    assert p.repo.feedback_detail(ticket['id'])['last_analysis']['reason'] == 'output_limit'


@pytest.mark.parametrize('env', [{'ECE329_FEEDBACK_REASONING_EFFORT':'unknown'},
                               {'ECE329_FEEDBACK_MAX_OUTPUT_TOKENS':'0'},
                               {'ECE329_FEEDBACK_CHECK_MAX_OUTPUT_TOKENS':'999999'}])
def test_invalid_feedback_configuration_fails_early(env):
    with pytest.raises(ModelConfigurationError):
        ModelExperienceExtractor(None, env)
