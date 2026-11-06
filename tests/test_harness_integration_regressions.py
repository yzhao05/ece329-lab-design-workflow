"""Cross-module regressions found while auditing the Harness integration."""
from contextlib import contextmanager
from types import SimpleNamespace
import json
import sqlite3
import pytest

from ece329_workflow.experience import ModelExperienceExtractor
from ece329_workflow.generation_policy import GenerationPolicy
from ece329_workflow.provider_transport import ProviderResponsesTransport, provider_transport
from ece329_workflow.openai_generator import OpenAIStageGenerator, ModelConfigurationError
from ece329_workflow.workflow_harness import WorkflowHarnessTransport
from tests.test_workflow_harness import Provider, LocalRuntime
from tests.test_feedback_pipeline import pipeline


@pytest.mark.parametrize('primary', ['gpt-5.4-mini', 'deepseek-flash'])
def test_provider_capabilities_budgets_and_leases_survive_wrappers(primary):
    deepseek = SimpleNamespace(max_output_tokens=24000, http=SimpleNamespace(_timeout_seconds=180))
    base = ProviderResponsesTransport(openai=Provider(), deepseek=deepseek)
    wrapped = WorkflowHarnessTransport(base, runtime_factory=LocalRuntime)
    generator = OpenAIStageGenerator(transport=wrapped, model=primary,
                                    allowed_models=['gpt-5.4-mini', 'deepseek-flash'])
    extractor = ModelExperienceExtractor(generator, environ={})
    assert set(extractor.available_models()) == {'gpt-5.4-mini', 'deepseek-flash'}
    assert extractor.models() == ['deepseek-flash', 'gpt-5.4-mini']
    assert extractor.lease_seconds() == 420
    assert GenerationPolicy(generator, {}).budget('deepseek-flash', 'none')['max_output_tokens'] == 24000
    assert generator.transport is wrapped


def test_wrapper_cycle_fails_without_looping():
    wrapper = SimpleNamespace()
    wrapper.transport = wrapper
    with pytest.raises(ModelConfigurationError): provider_transport(wrapper)


def test_maintainer_harness_details_include_actual_host_diagnostics(pipeline):
    from ece329_workflow.experience_admin import execution_records
    diagnostic = {'version': 1, 'events': [{'step': 'stage_check', 'status': 'blocked', 'code': 'missing_fields'}]}
    pipeline.repo.record_telemetry({'id': 'harness-host-check', 'design_id': pipeline.session.design_id,
        'created': 1, 'harness': [{'status': 'completed'}], 'stages': [], 'execution_diagnostic': diagnostic})
    record = execution_records(pipeline.repo)['records'][0]
    assert record['harness'][0]['status'] == 'completed'
    assert record['host_checks']['execution_diagnostic'] == diagnostic
    assert record['host_checks']['diagnostic_status'] == 'recorded'


def test_builder_tuple_reference_resolves_exact_source(pipeline):
    from ece329_workflow.artifact_harness import review
    provider = Provider({'findings': [{'path': '/source/0/1', 'quote': 'original text',
        'code': 'language', 'explanation': 'Verify this source phrase.'}]})
    pipeline.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    result = review(pipeline.engine, pipeline.session, {'text': 'translated'}, kind='builder',
                    language='en', source=[('heading', 'original text')])
    assert result['status'] == 'reviewed'
    assert len(provider.payloads) == 1


def test_older_interrupted_review_keeps_totals_incomplete(pipeline):
    store = pipeline.repo
    design_id = pipeline.session.design_id
    with store.connection() as db:
        db.execute('INSERT OR REPLACE INTO usage_designs VALUES(?,?,?,?)', (design_id, 1, 'EMVR_DIRECT', 1))
        for index in range(12):
            record = {'created': index + 1, 'harness_review': {'status': 'running' if index == 0 else 'reviewed'}}
            db.execute('INSERT INTO artifact_harness_reviews VALUES(?,?,?)', (str(index), design_id, json.dumps(record)))
    usage = store.design_usage(design_id)
    assert len(usage['artifact_reviews']) == 10
    assert not usage['complete'] and usage['total_tokens'] is None
    assert store.design_usage(design_id, through=0)['complete']


def test_optional_review_storage_failure_does_not_call_model_or_block_export(pipeline, monkeypatch, caplog):
    from ece329_workflow.artifact_harness import review
    provider = Provider()
    pipeline.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    @contextmanager
    def unavailable():
        raise sqlite3.OperationalError('private database detail')
        yield
    monkeypatch.setattr(pipeline.repo, 'connection', unavailable)
    result = review(pipeline.engine, pipeline.session, {'text': 'source'}, kind='report')
    assert result['status'] == 'persistence_failed' and not provider.payloads
    assert pipeline.session.design_id in caplog.text and 'private database detail' not in caplog.text


def test_final_usage_save_failure_remains_incomplete_without_retry(pipeline, monkeypatch):
    from ece329_workflow.artifact_harness import review
    provider = Provider()
    pipeline.engine.generator = OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    original = pipeline.repo.connection
    count = 0
    @contextmanager
    def fail_final_save():
        nonlocal count
        count += 1
        if count == 2: raise sqlite3.OperationalError('save failed')
        with original() as db: yield db
    monkeypatch.setattr(pipeline.repo, 'connection', fail_final_save)
    result = review(pipeline.engine, pipeline.session, {'text': 'source'}, kind='report')
    assert result['status'] == 'persistence_failed'
    assert len(provider.payloads) == 1
    assert not pipeline.repo.design_usage(pipeline.session.design_id)['complete']
    cached = review(pipeline.engine, pipeline.session, {'text': 'source'}, kind='report')
    assert cached['status'] == 'running' and len(provider.payloads) == 1


@pytest.mark.parametrize('builder', [False, True])
def test_english_export_translation_meter_excludes_artifact_review(builder, monkeypatch):
    from ece329_workflow.localization import english_pdf
    from tools.export_emvr37_review import reviewed_engine
    engine, session = reviewed_engine()
    engine.experience_store = object()
    events = []
    @contextmanager
    def measured(*args):
        events.append('translation-start')
        yield
        events.append('translation-end')
    monkeypatch.setattr('ece329_workflow.usage.measure_translation', measured)
    monkeypatch.setattr('ece329_workflow.reporting.render_emvr_report_pdf', lambda *a, **k: b'%PDF')
    monkeypatch.setattr('ece329_workflow.builder_input.render_builder_review_pdf', lambda *a, **k: b'%PDF')
    monkeypatch.setattr(engine, 'review_artifact', lambda *a, **k: events.append('artifact-review'))
    translator = SimpleNamespace(english_tree=lambda value, model: value)
    assert english_pdf(engine, translator, session.design_id, builder) == b'%PDF'
    assert events == ['translation-start', 'translation-end', 'artifact-review']
