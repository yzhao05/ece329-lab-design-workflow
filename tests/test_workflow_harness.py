"""Harness integration keeps provider evidence, host checks and retry bounds."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace
import pytest

from ece329_workflow.harness_runtime import Runtime
from ece329_workflow.workflow_harness import WorkflowHarnessTransport, DIALOGUE_SCHEMAS, configure
from ece329_workflow.openai_generator import OpenAIStageGenerator, ModelServiceError, ModelOutputError
from ece329_workflow.models import InteractionState, StageCompletionError, Stage
from ece329_workflow.artifact_harness import review, bounded_fields
from ece329_workflow.usage import UsageTransport
from ece329_workflow.telemetry import CURRENT_TRACE
from tests.test_feedback_pipeline import pipeline


class Provider:
    def __init__(self, output=None):
        self.payloads = []
        self.output = output if output is not None else {'findings': []}

    def create(self, payload):
        self.payloads.append(deepcopy(payload))
        return {'id': 'provider-response-id', 'output_text': json.dumps(self.output),
                'usage': {'input_tokens': 21, 'output_tokens': 13}}


class LocalRuntime(Runtime):
    """Executor fixture only. Real SDK is tested separately below."""
    def run(self, prompt):
        return self.complete({'messages': [{'role': 'user', 'content': prompt}]})


def payload(schema, model='deepseek-flash'):
    return {'model': model, 'instructions': 'Keep scientific evidence unchanged.',
            'input': [{'role': 'user', 'content': '保留当前设计并继续 / keep this design and continue'}],
            'max_output_tokens': 700, 'reasoning': {'effort': 'none'}, 'store': False,
            'text': {'format': {'type': 'json_schema', 'name': schema, 'strict': True,
                                'schema': {'type': 'object', 'properties': {}}}}}


@pytest.mark.parametrize('schema', sorted(DIALOGUE_SCHEMAS) + ['ece329_stage_output',
    'ece329_display_translation', 'ece329_report_review', 'ece329_builder_review'])
@pytest.mark.parametrize('model', ['gpt-5.4-mini', 'deepseek-flash'])
def test_every_contract_keeps_real_usage_budget_and_host_envelope(schema, model):
    provider = Provider()
    transport = WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime)
    calls = []
    request = payload(schema, model)
    original = deepcopy(request)
    result = UsageTransport(transport, calls).create(request)
    assert len(provider.payloads) == 1
    assert result['id'] == 'provider-response-id'
    assert calls[0]['input_tokens'] == 21 and calls[0]['output_tokens'] == 13
    assert calls[0]['harness']['status'] == 'completed'
    assert provider.payloads[0]['max_output_tokens'] == 700
    assert provider.payloads[0]['text'] == request['text']
    assert request == original


def test_input_limit_bypasses_once_without_truncating_context():
    provider = Provider()
    transport = WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime)
    request = payload('ece329_context_intent')
    request['input'][0]['content'] = '完整证据' * 25000
    result = transport.create(request)
    assert result['_workflow_harness']['reason'] == 'input_budget'
    assert len(provider.payloads) == 1 and provider.payloads[0] == request


def test_failure_never_adds_retry_or_direct_paid_fallback():
    provider = Provider()
    class Broken(LocalRuntime):
        def run(self, prompt): raise ValueError('runtime failure')
    transport = WorkflowHarnessTransport(provider, runtime_factory=Broken)
    with pytest.raises(ModelServiceError): transport.create(payload('ece329_context_intent'))
    assert not provider.payloads


def test_sdk_cannot_substitute_unmeasured_output():
    class Substitution(LocalRuntime):
        def run(self, prompt):
            super().run(prompt)
            return '{"invented":true}'
    calls = []
    with pytest.raises(ModelOutputError):
        UsageTransport(WorkflowHarnessTransport(Provider(), runtime_factory=Substitution), calls).create(payload('ece329_stage_output'))
    assert calls[0]['input_tokens'] == 21 and calls[0]['output_tokens'] == 13


@pytest.mark.parametrize('mode', list(InteractionState))
def test_both_modes_keep_stage_checks_and_durable_harness_trace(pipeline, mode):
    p = pipeline
    session = p.session
    session.interaction_state = mode
    trace = SimpleNamespace(data={'harness': []})
    token = CURRENT_TRACE.set(trace)
    try:
        WorkflowHarnessTransport(Provider(), runtime_factory=LocalRuntime).create(payload('ece329_context_intent'))
        with pytest.raises(StageCompletionError): p.engine._validate_completion(session, Stage.IDEA_BRAINSTORMING)
        assert trace.data['harness'][0]['status'] == 'completed'
        assert any(x['step'] == 'stage_check' and x['status'] == 'blocked'
                   for x in trace.data['execution_diagnostic']['events'])
    finally: CURRENT_TRACE.reset(token)


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('language', ['en', 'zh'])
@pytest.mark.parametrize('kind', ['report', 'builder'])
def test_artifact_review_is_read_only_cached_and_counted(pipeline, mode, language, kind):
    p = pipeline
    p.session.interaction_state = mode
    p.engine.store.save(p.session)
    provider = Provider()
    p.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    before = deepcopy(p.engine.store.get(p.session.design_id))
    document = {'text': 'Field measurement' if language == 'en' else '场强测量'}
    result = review(p.engine, p.session, document, kind=kind, language=language)
    assert result['status'] == 'reviewed' and result['model_findings'] == 'unverified'
    assert review(p.engine, p.session, document, kind=kind, language=language) == result
    assert len(provider.payloads) == 1
    assert p.engine.store.get(p.session.design_id) == before
    usage = p.repo.design_usage(p.session.design_id)
    # This legacy fixture did not start with complete metering; do not relabel
    # its unknown historical use as zero just because this request is measured.
    assert usage['known_input_tokens'] == 21 and usage['known_output_tokens'] == 13
    assert usage['artifact_reviews'][0]['status'] == 'reviewed'
    review(p.engine, p.session, {'text': document['text'] + ' changed'}, kind=kind, language=language)
    assert len(provider.payloads) == 2


def test_artifact_invalid_reference_is_not_claimed_valid_or_retried(pipeline):
    p = pipeline
    provider = Provider({'findings': [{'path': '/document/not-recorded', 'quote': 'invented',
        'code': 'inconsistent', 'explanation': 'Unproven'}]})
    p.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    result = review(p.engine, p.session, {'text': '原始设计'}, kind='report')
    assert result['status'] == 'failed' and 'findings' not in result
    review(p.engine, p.session, {'text': '原始设计'}, kind='report')
    assert len(provider.payloads) == 1


def test_retired_model_is_recorded_without_blocking_artifact_export(pipeline):
    p = pipeline
    provider = Provider()
    p.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    p.session.model_context['selected_model'] = 'retired-model'
    result = review(p.engine, p.session, {'text': '已批准设计'}, kind='report')
    assert result['status'] == 'failed' and not provider.payloads
    assert p.repo.design_usage(p.session.design_id)['artifact_reviews'][0]['status'] == 'failed'


def test_flags_are_validated_and_offline_path_unchanged():
    provider = Provider()
    assert configure(provider, {}) is provider
    assert isinstance(configure(provider, {}, production=True), WorkflowHarnessTransport)
    assert configure(provider, {'ECE329_HARNESS_ENABLED': 'false'}, production=True) is provider
    with pytest.raises(ModelServiceError): configure(provider, {'ECE329_HARNESS_ENABLED': 'maybe'})
    with pytest.raises(ModelServiceError): configure(provider, {'ECE329_HARNESS_ENABLED': 'true', 'ECE329_HARNESS_TASKS': 'shell'})


def test_large_field_is_not_cut_or_falsely_counted_as_reviewed():
    fields, coverage = bounded_fields({'document': {'oversize': '很长的完整证据' * 10000, 'other': 'kept'}})
    assert fields == [{'value': 'kept', 'paths': ['/document/other']}]
    assert coverage['reviewed_fields'] == 1 and coverage['omitted_fields'] == 1


def test_realistic_builder_pack_receives_bounded_review(pipeline):
    from tools.export_emvr37_review import reviewed_engine
    from ece329_workflow.builder_input import build_builder_gate1_input
    _, session = reviewed_engine()
    provider = Provider()
    p = pipeline
    p.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    result = review(p.engine, p.session, build_builder_gate1_input(session), kind='builder')
    assert result['status'] in ('reviewed', 'reviewed_partial')
    assert result['coverage']['reviewed_fields'] > 20
    assert len(provider.payloads) == 1


def test_guided_pdf_hook_preserves_feedback_gate(pipeline):
    from tests.test_final_review import completed, download
    from tests.test_feedback_pipeline import submit
    p = pipeline
    completed(p)
    provider = Provider()
    p.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    assert download(p)[0].startswith('409')
    assert not provider.payloads
    submit(p, category='final_review')
    status, _, body = download(p)
    assert status.startswith('200') and body.startswith(b'%PDF')
    assert len(provider.payloads) == 1
    assert provider.payloads[0]['text']['format']['name'] == 'ece329_report_review'


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('message', ['电场的叠加如何计算？', 'How do I calculate field superposition?'])
@pytest.mark.parametrize('runtime_factory', [LocalRuntime, pytest.param(Runtime, id='real_sdk',
    marks=pytest.mark.skipif(os.getenv('ECE329_TEST_HARNESS_RUNTIME') != '1', reason='Opt-in real SDK with simulated model'))])
def test_actual_turn_recovers_invalid_intent_with_same_budget_and_idempotency(mode, message, runtime_factory):
    from tests.test_model_selection import ModelTransport, make_engine, add_session
    class FixtureProvider(ModelTransport):
        def create(self, request):
            # Decode the SDK task only in this simulated provider, so the existing
            # deterministic fixtures can exercise real host parsing and recovery.
            request = deepcopy(request)
            if isinstance(request['input'][-1]['content'], str):
                # SDK appends a runtime-directory note after the task JSON.
                task, _ = json.JSONDecoder().raw_decode(request['input'][-1]['content'])
                if isinstance(task, dict) and 'input' in task: request['input'] = task['input']
            return super().create(request)
    base, original_provider = make_engine(transport=ModelTransport(broken_intent=True))
    provider = FixtureProvider(broken_intent=True)
    wrapped, _ = make_engine(transport=WorkflowHarnessTransport(provider, runtime_factory=runtime_factory))
    left, right = add_session(base, mode), add_session(wrapped, mode)
    request = {'message': message, 'turn_id': 'harness-turn-00001'}
    baseline = base.process_turn(left.design_id, request)
    result = wrapped.process_turn(right.design_id, request)
    assert len(provider.requests) == len(original_provider.requests)
    assert any(r['text']['format']['name'] == 'ece329_compact_dialogue_acts' for r in provider.requests)
    assert result['assistant_message'] == baseline['assistant_message']
    assert wrapped.store.get(right.design_id).design_context == base.store.get(left.design_id).design_context
    trace = list(wrapped._telemetry.values())[-1]
    assert trace['harness'] and all(e['status'] == 'completed' for e in trace['harness'])
    count = len(provider.requests)
    wrapped.process_turn(right.design_id, request)
    assert len(provider.requests) == count


@pytest.mark.skipif(os.getenv('ECE329_TEST_HARNESS_RUNTIME') != '1', reason='Real local SDK, simulated upstream only')
@pytest.mark.parametrize('schema', ['ece329_context_intent', 'ece329_stage_output', 'ece329_display_translation', 'ece329_builder_review'])
def test_real_sdk_all_workflow_tasks(schema):
    provider = Provider()
    result = WorkflowHarnessTransport(provider).create(payload(schema))
    assert result['_workflow_harness']['status'] == 'completed'
    assert len(provider.payloads) == 1 and result['usage']['input_tokens'] == 21


@pytest.mark.skipif(os.getenv('ECE329_TEST_HARNESS_RUNTIME') != '1', reason='Real local SDK, simulated rate limit')
def test_real_sdk_keeps_provider_error_class_and_does_not_retry():
    from ece329_workflow.openai_generator import ModelHTTPError
    class Limited(Provider):
        def create(self, request):
            self.payloads.append(request)
            raise ModelHTTPError(429, 'rate_limit_exceeded')
    provider = Limited()
    with pytest.raises(ModelHTTPError) as caught:
        WorkflowHarnessTransport(provider).create(payload('ece329_context_intent'))
    assert caught.value.status_code == 429
    assert len(provider.payloads) == 1
