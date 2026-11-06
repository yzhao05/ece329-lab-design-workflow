"""Lifecycle and deployment regressions found during iterative project review."""
from types import SimpleNamespace
import sys
import pytest

from ece329_workflow import harness_runtime
from ece329_workflow.artifact_harness import review
from ece329_workflow.experience import ExperienceStore
from ece329_workflow.models import DesignSession, InteractionState
from ece329_workflow.openai_generator import OpenAIStageGenerator
from ece329_workflow.store import SQLiteSessionStore
from ece329_workflow.workflow_harness import WorkflowHarnessTransport
from tests.test_workflow_harness import Provider, LocalRuntime, payload
from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files


@pytest.fixture
def durable():
    path = workspace_temp_path('review-lifecycle.sqlite3')
    sessions = SQLiteSessionStore(path)
    store = ExperienceStore(path)
    try:
        yield sessions, store
    finally:
        store.close()
        remove_sqlite_files(path)


@pytest.mark.parametrize('mode', list(InteractionState))
def test_late_diagnostics_and_usage_registration_do_not_restore_deleted_design(durable, mode):
    sessions, store = durable
    session = DesignSession('deleted-diagnostic', mode)
    sessions.save(session)
    sessions.delete(session.design_id)
    store.record_telemetry({'id': 'late-turn', 'design_id': session.design_id, 'created': 1,
                            'mode': mode.value, 'calls': [], 'latency_ms': 1})
    store.register_usage_design(session.design_id, mode.value)
    with store.connection() as db:
        for table in ('workflow_telemetry', 'usage_designs', 'usage_runs'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('delete_during_call', [False, True])
def test_report_review_respects_design_deletion(durable, mode, delete_during_call):
    sessions, store = durable
    session = DesignSession('deleted-report', mode)
    sessions.save(session)
    class DeletingProvider(Provider):
        def create(self, request):
            if delete_during_call: sessions.delete(session.design_id)
            return super().create(request)
    provider = DeletingProvider()
    engine = SimpleNamespace(experience_store=store,
        generator=OpenAIStageGenerator(WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime)))
    if not delete_during_call: sessions.delete(session.design_id)
    review(engine, session, {'text': 'Read-only report'}, kind='report')
    assert len(provider.payloads) == int(delete_during_call)
    with store.connection() as db:
        for table in ('artifact_harness_reviews', 'usage_designs', 'usage_runs'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.parametrize('explicit_cap,expected', [(None, 24000), (16000, 16000)])
def test_harness_preserves_declared_deepseek_budget(explicit_cap, expected):
    provider = Provider()
    request = payload('ece329_stage_output')
    request['max_output_tokens'] = 24000
    if explicit_cap is not None: request['_workflow_output_cap'] = explicit_cap
    WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime).create(request)
    assert provider.payloads[0]['_workflow_output_cap'] == expected


@pytest.mark.parametrize('failure', ['version', 'missing_binary', 'bad_manifest'])
def test_native_runtime_preflight_checks_matching_binary_and_falls_back_once(monkeypatch, failure):
    monkeypatch.setattr(harness_runtime, 'version', lambda name:
        'wrong-version' if failure == 'version' and name == 'deepseek-harness-runtime-bin' else harness_runtime.SDK_VERSION)
    def resolve(mode=None):
        assert mode == 'exe'  # Match the worker's filtered environment.
        if failure == 'missing_binary': raise FileNotFoundError('missing binary')
        if failure == 'bad_manifest': raise ValueError('invalid manifest')
        return ('unused.exe',)
    monkeypatch.setitem(sys.modules, 'deepseek_harness_runtime', SimpleNamespace(resolve_bundled_launch_args=resolve))
    assert not harness_runtime.available()
    provider = Provider()
    result = WorkflowHarnessTransport(provider).create(payload('ece329_stage_output'))
    assert len(provider.payloads) == 1
    assert result['_workflow_harness'] == {'task': 'stage', 'status': 'bypassed', 'reason': 'sdk_unavailable'}
