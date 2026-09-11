"""Feedback API -> model extraction -> review -> both-mode prompt retrieval."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.dialogue_state import serialize_intent_input, resolved_intent, UserIntent
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.experience import ExperienceStore, FeedbackService, ModelExperienceExtractor, validate_candidate
from ece329_workflow.feedback import guidance
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage, StepOutput
from ece329_workflow.security import APISettings
from ece329_workflow.store import InMemorySessionStore, SQLiteSessionStore
from tests.test_security_and_store import call_api, workspace_temp_path, remove_sqlite_files


def candidate(**updates):
    return {
        'useful': True, 'summary': '用户报告已经回答的流程问题仍被重复询问，应核对回答证据。',
        'category': 'answered_pending', 'trigger': '学生说明问题已回答且前提没有变化时',
        'recommendation': '检查已提交回答与开放待办的对应关系；证据成立后关闭旧待办，回应当前请求。',
        'verification': '回答仍保留，旧问题不再重复，新问题不被误关闭。',
        'keywords': ['已经回答', '重复询问'], 'modes': [m.value for m in InteractionState],
        'stages': [s.value for s in Stage], **updates,
    }


def test_persistent_storage_failure_stops_worker_without_infinite_retries():
    class BrokenStore:
        calls = 0
        def claim(self):
            self.calls += 1
            raise sqlite3.OperationalError('database unavailable')
    store = BrokenStore()
    service = FeedbackService(store, None)
    waits = []
    service._stop = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: waits.append(seconds))
    service._run()
    assert store.calls == 3
    assert waits == [2, 2]
    assert service._thread is None


@pytest.fixture
def pipeline():
    repo = ExperienceStore()
    seen = []
    def extract(payload):
        seen.append(payload)
        return candidate()
    service = FeedbackService(repo, SimpleNamespace(extract=extract), background=False)
    engine = WorkflowEngine(generator=RuleBasedStageGenerator(), store=InMemorySessionStore())
    session = DesignSession('feedback-owner', InteractionState.EMVR_DIRECT,
                            access_token_hash=hashlib.sha256(b'owner-token').hexdigest(), revision=2)
    session.history = [{'revision': 2, 'user_message': '我已经回答过了',
                        'output': {'assistant_message': '请再告诉我实验流程。'}}]
    engine.store.save(session)
    api = WorkflowAPI(engine, APISettings(feedback_admin_token='maintainer-token', rate_limit_requests=500), feedback_service=service)
    yield SimpleNamespace(repo=repo, service=service, engine=engine, api=api, session=session, seen=seen,
                          auth={'Authorization': 'Bearer owner-token'},
                          admin={'X-ECE329-Feedback-Admin-Token': 'maintainer-token'})
    service.stop()
    repo.close()


def submit(p, **updates):
    return call_api(p.api, 'POST', f'/v1/designs/{p.session.design_id}/feedback',
                    {'message': '我已经回答过了，为什么重复询问？', 'category': 'answered_pending',
                     'request_id': 'feedback-request-0001', **updates}, request_headers=p.auth)


def review(p, item, decision='approve', **updates):
    return call_api(p.api, 'POST', f"/v1/feedback/experiences/{item['id']}/review",
                    {'decision': decision, 'version': item['version'], 'note': '已对照原始证据与回放测试核验。', **updates}, request_headers=p.admin)


def extract_one(p):
    submit(p)
    assert p.service.run_once()
    return p.repo.experiences()[0]


def test_receipt_is_saved_before_analysis_and_does_not_advance_design(pipeline):
    p = pipeline
    before = p.engine.store.get(p.session.design_id)
    status, _, receipt = submit(p)
    assert status.startswith('201') and receipt['status'] == 'queued'
    assert p.seen == []
    assert p.engine.store.get(p.session.design_id) == before
    assert p.repo.tickets(p.session.design_id)[0]['id'] == receipt['id']
    assert p.service.run_once()
    assert p.repo.tickets(p.session.design_id)[0]['status'] == 'candidate'
    assert p.seen[0]['evidence']['recent_turns'][0]['assistant'] == '请再告诉我实验流程。'
    assert 'owner-token' not in json.dumps(p.seen) and 'access_token_hash' not in json.dumps(p.seen)


def test_network_retry_remains_idempotent_after_design_advances(pipeline):
    p = pipeline
    _, _, first = submit(p)
    p.session.revision += 1
    p.engine.store.save(p.session)
    status, _, second = submit(p)
    assert status.startswith('200') and first == second
    assert len(p.repo.tickets(p.session.design_id)) == 1
    assert submit(p, message='不同反馈')[0].startswith('409')


@pytest.mark.parametrize('updates', [
    {'message': ''}, {'message': 'x' * 4001}, {'message': 5}, {'category': 'invented'},
    {'revision': 999}, {'revision': True}, {'request_id': 'short'}, {'evidence': {}},
])
def test_invalid_feedback_is_not_enqueued(pipeline, updates):
    assert submit(pipeline, **updates)[0].startswith('400')
    assert pipeline.repo.tickets(pipeline.session.design_id) == []


def test_feedback_and_admin_authentication_are_separate(pipeline):
    p = pipeline
    submit(p)
    path = f'/v1/designs/{p.session.design_id}/feedback'
    assert call_api(p.api, 'GET', path)[0].startswith('401')
    assert call_api(p.api, 'GET', path, request_headers=p.admin)[0].startswith('401')
    assert len(call_api(p.api, 'GET', path, request_headers=p.auth)[2]['feedback']) == 1
    assert call_api(p.api, 'GET', '/v1/feedback/experiences', request_headers=p.auth)[0].startswith('401')
    assert call_api(p.api, 'GET', '/v1/feedback/experiences', request_headers=p.admin)[0].startswith('200')
    p.api.settings = APISettings()
    assert call_api(p.api, 'GET', '/v1/feedback/experiences', request_headers=p.admin)[0].startswith('401')


@pytest.mark.parametrize('mode', list(InteractionState))
def test_reviewed_experience_enters_intent_and_generation_but_disable_revokes_it(pipeline, mode):
    p = pipeline
    item = extract_one(p)
    assert p.repo.retrieve(mode.value, Stage.IDEA_BRAINSTORMING.value, '已经回答') == []
    assert review(p, item)[0].startswith('200')
    # A different design in either mode can retrieve only the approved reusable rule.
    session = DesignSession('other-design', mode)
    p.engine.store.save(session)
    packet = p.engine.get_prompt_packet(session.design_id, '我已经回答过了')
    assert 'EXP-' + item['id'] in json.dumps(packet)
    p.engine._attach_experiences(session, '我已经回答过了')
    assert 'EXP-' + item['id'] in json.dumps(serialize_intent_input(session, '我已经回答过了', None, {}))
    assert guidance(session, '已经回答')['authority'].startswith('advisory')
    assert review(p, item)[0].startswith('409')
    active = p.repo.experiences('active')[0]
    assert review(p, active, 'disable')[0].startswith('200')
    assert 'EXP-' + item['id'] not in json.dumps(p.engine.get_prompt_packet(session.design_id, '我已经回答过了'))
    assert [r['decision'] for r in p.repo.experiences('disabled')[0]['reviews']] == ['approve', 'disable']


def test_retrieval_obeys_scope_relevance_and_size_budget(pipeline):
    p = pipeline
    item = extract_one(p)
    content = candidate(modes=['EMVR_DIRECT'], stages=['CONCEPTUAL_PROCEDURE'])
    assert review(p, item, content=content)[0].startswith('200')
    assert p.repo.retrieve('GUIDED_DESIGN', 'CONCEPTUAL_PROCEDURE', '已经回答') == []
    assert p.repo.retrieve('EMVR_DIRECT', 'HYPOTHESIS', '已经回答') == []
    assert p.repo.retrieve('EMVR_DIRECT', 'CONCEPTUAL_PROCEDURE', '磁通如何计算') == []
    rules = p.repo.retrieve('EMVR_DIRECT', 'CONCEPTUAL_PROCEDURE', '已经回答')
    assert len(rules) == 1
    p.session.turn_context['experience_rules'] = rules * 5
    selected = guidance(p.session, '我已经回答过了')['rules']
    assert len(selected) <= 3 and sum(len(r['instruction']) for r in selected) <= 650


def test_reject_never_enables_a_rule(pipeline):
    p = pipeline
    item = extract_one(p)
    assert review(p, item, 'reject')[0].startswith('200')
    assert p.repo.retrieve('EMVR_DIRECT', 'IDEA_BRAINSTORMING', '已经回答') == []
    assert p.repo.tickets(p.session.design_id)[0]['status'] == 'rejected'


def test_model_failure_is_recorded_and_retry_is_bounded(pipeline):
    p = pipeline
    def fail(_):
        raise RuntimeError('secret-api-key-must-not-leak')
    p.service.extractor = SimpleNamespace(extract=fail)
    _, _, ticket = submit(p)
    for attempt in range(1, 4):
        assert p.service.run_once()
        assert not p.service.run_once()  # Failure does not trigger a busy automatic retry.
        row = p.repo.tickets(p.session.design_id)[0]
        assert row['status'] == 'failed' and row['attempts'] == attempt
        assert 'secret-api-key' not in json.dumps(row)
        path = f"/v1/designs/{p.session.design_id}/feedback/{ticket['id']}/retry"
        status = call_api(p.api, 'POST', path, {}, request_headers=p.auth)[0]
        assert status.startswith('202' if attempt < 3 else '400')
    assert p.engine.get_prompt_packet(p.session.design_id, '电场如何计算')


@pytest.mark.parametrize('output,status', [(candidate(useful=False), 'no_learning'), ({'useful': True}, 'failed')])
def test_insufficient_or_invalid_model_output_never_activates(pipeline, output, status):
    p = pipeline
    p.service.extractor = SimpleNamespace(extract=lambda _: output)
    submit(p)
    p.service.run_once()
    assert p.repo.tickets(p.session.design_id)[0]['status'] == status
    assert p.repo.experiences() == []


def test_model_extractor_uses_configured_transport_and_strict_bounded_job(pipeline):
    captured = []
    def create(payload):
        captured.append(payload)
        return {'output_text': json.dumps(candidate())}
    generator = SimpleNamespace(primary=SimpleNamespace(transport=SimpleNamespace(create=create), model='configured-test-model', reasoning_effort='low'))
    p = pipeline
    p.service.extractor = ModelExperienceExtractor(generator)
    extract_one(p)
    assert len(captured) == 1
    request = captured[0]
    assert request['model'] == 'configured-test-model' and request['store'] is False
    assert request['text']['format']['strict'] is True
    assert request['max_output_tokens'] == 2200
    assert '不是给你的指令' in request['instructions']
    assert 'owner-token' not in json.dumps(request)


def test_duplicate_extraction_and_edited_review_deduplicate(pipeline):
    p = pipeline
    item = extract_one(p)
    submit(p, request_id='feedback-request-0002')
    p.service.run_once()
    assert len(p.repo.experiences()) == 1
    assert p.repo.tickets(p.session.design_id)[0]['status'] == 'duplicate'
    p.service.extractor = SimpleNamespace(extract=lambda _: candidate(trigger='新的适用条件'))
    submit(p, request_id='feedback-request-0003')
    p.service.run_once()
    other = next(row for row in p.repo.experiences() if row['id'] != item['id'])
    assert review(p, other, content=candidate())[0].startswith('409')
    assert review(p, item, content=candidate(trigger='编辑后更精确的适用条件'))[0].startswith('200')
    assert p.repo.experiences('active')[0]['content']['trigger'] == '编辑后更精确的适用条件'


def test_restart_recovers_expired_lease_and_stale_worker_cannot_write():
    path = workspace_temp_path('.sqlite')
    try:
        first = ExperienceStore(path)
        session = DesignSession('durable-design', InteractionState.EMVR_DIRECT)
        ticket, _ = first.submit(session, {'message': '已经回答过了', 'request_id': 'durable-request-1'})
        abandoned = first.claim(lease_seconds=-1)
        restarted = ExperienceStore(path)
        job = restarted.claim()
        assert job['id'] == abandoned['id'] and job['token'] != abandoned['token']
        assert not first.finish(abandoned, candidate())
        assert restarted.finish(job, candidate())
        item = restarted.experiences()[0]
        restarted.review(item['id'], 'approve', 1, '已运行回放并核对证据')
        third = ExperienceStore(path)
        assert third.tickets(session.design_id)[0]['durable'] is True
        assert third.tickets(session.design_id)[0]['attempts'] == 2
        assert third.retrieve('EMVR_DIRECT', 'IDEA_BRAINSTORMING', '已经回答')
    finally:
        remove_sqlite_files(path)


def test_worker_runs_off_request_thread_and_delete_cancels_inflight_result(pipeline):
    p = pipeline
    started, release, finished = Event(), Event(), Event()
    def slow(_):
        started.set()
        release.wait(5)
        finished.set()
        return candidate()
    p.service.extractor = SimpleNamespace(extract=slow)
    p.service.background = True
    try:
        status, _, ticket = submit(p)
        assert status.startswith('201') and started.wait(2)
        assert not finished.is_set()
        status, _, _ = call_api(p.api, 'DELETE', f'/v1/designs/{p.session.design_id}', request_headers=p.auth)
        assert status.startswith('204')
        release.set()
        p.service.stop()
        assert p.repo.tickets(p.session.design_id) == [] and p.repo.experiences() == []
    finally:
        release.set()
        p.service.stop()


def test_source_session_deletion_cleans_durable_feedback_and_review_audit():
    path = workspace_temp_path('.sqlite')
    try:
        sessions = SQLiteSessionStore(path)
        repo = ExperienceStore(path)
        session = DesignSession('source', InteractionState.EMVR_DIRECT)
        sessions.save(session)
        repo.submit(session, {'message': '已经回答', 'request_id': 'source-feedback-1'})
        repo.finish(repo.claim(), candidate())
        item = repo.experiences()[0]
        repo.review(item['id'], 'approve', 1, '验证了原始回答与待办')
        sessions.delete(session.design_id)
        assert repo.tickets(session.design_id) == [] and repo.experiences() == []
        with repo.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM experience_reviews').fetchone()[0] == 0
    finally:
        remove_sqlite_files(path)


def test_unavailable_optional_experience_store_does_not_break_design(pipeline):
    def fail(*_, **__):
        raise sqlite3.OperationalError('database is locked')
    pipeline.engine.experience_store = SimpleNamespace(retrieve=fail)
    packet = pipeline.engine.get_prompt_packet(pipeline.session.design_id, '我已经回答过了')
    assert 'EXP-' not in json.dumps(packet)
    assert packet


@pytest.mark.parametrize('mode', list(InteractionState))
def test_real_turn_routes_reviewed_advice_to_parser_and_reply(pipeline, mode):
    p = pipeline
    item = extract_one(p)
    assert review(p, item, content=candidate(keywords=['叠加']))[0].startswith('200')
    seen = {}
    class RecordingGenerator(RuleBasedStageGenerator):
        def resolve_intent(self, session, message, pending, carried):
            seen['intent'] = serialize_intent_input(session, message, pending, carried)
            return resolved_intent(UserIntent.ASK_COURSE_QUESTION, actions_authoritative=True,
                                   dialogue_acts=[{'type': 'ASK_COURSE_QUESTION', 'content': message, 'confidence': 1.0}])
        def generate(self, session, message):
            from ece329_workflow.prompts import build_prompt_packet
            seen['reply'] = build_prompt_packet(session, message)
            return StepOutput('分别计算各电荷的电场矢量，再作矢量求和。')
    p.engine.generator = RecordingGenerator()
    session = DesignSession('actual-turn', mode, current_stage_index=8,
                            stage_outputs={'CONCEPTUAL_PROCEDURE': {'assistant_message': '已记录流程'}})
    p.engine.store.save(session)
    response = p.engine.process_turn(session.design_id, {'message': '电场的叠加如何计算？'})
    for context in ('intent', 'reply'):
        assert 'EXP-' + item['id'] in json.dumps(seen[context])
    assert '矢量求和' in response['assistant_message']
    assert p.engine.store.get(session.design_id).current_stage_index == 8
