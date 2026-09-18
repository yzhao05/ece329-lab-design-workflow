"""Regression coverage for evidence, bounded checking and correction feedback."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from ece329_workflow.experience import ExperienceStore, FeedbackService, ModelExperienceExtractor, evidence_snapshot
from ece329_workflow.models import DesignSession, InteractionState, SessionConflict
from ece329_workflow.experience_learning import workflow_evidence_state, validate_review_note
from ece329_workflow.openai_generator import ModelOutputError
from tests.test_feedback_pipeline import (candidate, extraction_draft, extraction_check, review_note,
                                         pipeline, extract_one, review, submit)
from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files


def test_targeted_evidence_includes_neighbors_not_just_latest_turns():
    session = DesignSession('history', InteractionState.EMVR_DIRECT, revision=10)
    session.history = [{'revision': i, 'handled_stage': session.current_stage.value,
                        'user_message': '继续下一步', 'output': {'assistant_message': f'回复{i}'},
                        'feedback_state_before': {'stage': 'before', 'pending_excerpt': 'confirm'},
                        'feedback_state_after': {'stage': 'after'}} for i in range(1, 11)]
    data = evidence_snapshot(session, 3, session.current_stage.value)
    assert [r['revision'] for r in data['event_chain']] == [2, 3, 4]
    assert [r['position'] for r in data['event_chain']] == ['before', 'reported', 'after']
    assert data['event_chain'][1]['state_before']['pending_excerpt'] == 'confirm'
    assert data['recent_turns'][0]['revision'] == 7
    data['event_chain'][0]['state_before']['stage'] = 'changed'
    assert session.history[1]['feedback_state_before']['stage'] == 'before'
    assert evidence_snapshot(session, 0)['event_chain'] == []
    del session.history[2]['feedback_state_before']
    assert evidence_snapshot(session, 3)['event_chain'][1]['state_before'] is None


@pytest.mark.parametrize('mode', list(InteractionState))
def test_real_turn_captures_states_in_both_modes(pipeline, mode):
    p = pipeline
    session = p.engine.store.get(p.session.design_id)
    session.interaction_state = mode
    p.engine.store.save(session)
    result = p.engine.process_turn(session.design_id, {'message': '你现在在问什么问题？', 'turn_id': 'state-evidence-001'})
    saved = p.engine.store.get(session.design_id)
    row = saved.history[-1]
    assert row['feedback_state_before']['revision'] == session.revision
    assert row['feedback_state_after']['revision'] == result['revision']
    count = len(saved.history)
    p.engine.process_turn(session.design_id, {'message': '你现在在问什么问题？', 'turn_id': 'state-evidence-001'})
    assert len(p.engine.store.get(session.design_id).history) == count
    packet = p.engine.get_prompt_packet(session.design_id, '继续')
    assert 'feedback_state_before' not in json.dumps(packet)
    assert 'feedback_state_after' not in json.dumps(packet)


@pytest.mark.parametrize('failed', [None, 'evidence_supported', 'positive_case_passes', 'negative_case_passes'])
def test_two_bounded_calls_and_failed_checks_cannot_learn(pipeline, failed):
    p = pipeline
    requests = []
    def create(request):
        requests.append(deepcopy(request))
        value = extraction_draft() if len(requests) == 1 else extraction_check(**({failed: False} if failed else {}))
        return {'output_text': json.dumps(value)}
    p.service.extractor = ModelExperienceExtractor(SimpleNamespace(model='test', transport=SimpleNamespace(create=create)))
    submit(p)
    assert p.service.run_once()
    assert not p.service.run_once()
    assert len(requests) == 2
    assert 'event_chain' in requests[0]['input'][0]['content'][0]['text']
    assert p.repo.tickets(p.session.design_id)[0]['status'] == ('no_learning' if failed else 'candidate')
    with p.repo.connection() as db:
        saved = json.loads(db.execute('SELECT payload FROM feedback_tickets').fetchone()[0])
    assert saved['extraction_analysis']['validation_status'] == 'not_replayed'


def test_invented_fact_reference_fails_without_repair_loop():
    calls = []
    def create(request):
        calls.append(request)
        draft = extraction_draft()
        draft['diagnosis']['facts'] = [{'evidence_ref': 'turn:999', 'observation': '未知事实'}]
        return {'output_text': json.dumps(draft)}
    extractor = ModelExperienceExtractor(SimpleNamespace(model='test', transport=SimpleNamespace(create=create)))
    with pytest.raises(ModelOutputError) as error:
        extractor.extract({'evidence': {}})
    assert error.value.feedback_reason == 'evidence_reference'
    assert len(calls) == 1


def test_api_enforces_pair_applies_correction_and_preserves_audit(pipeline):
    p = pipeline
    item = extract_one(p)
    assert review(p, item, note='旧格式不能产生结构化修订')[0].startswith('400')
    assert review(p, item, note=review_note(corrected=''))[0].startswith('400')
    assert review(p, item, note=review_note(original='并不是原文'))[0].startswith('409')
    corrected = '上一轮邀请用户确认继续时，应承接“继续下一步”并推进对应待办。'
    assert review(p, item, note=review_note(corrected=corrected))[0].startswith('200')
    active = p.repo.experiences('active')[0]
    assert active['content']['summary'] == corrected
    audit = active['reviews'][0]
    assert audit['content']['previous']['rule'] == item['content']
    assert json.loads(audit['note'])['corrected'] == corrected
    assert json.loads(audit['note'])['opinion'] == review_note()['opinion']
    assert audit['content']['current']['rule']['summary'] == corrected
    assert review(p, item, note=review_note(corrected=corrected))[0].startswith('409')


@pytest.mark.parametrize('mode', list(InteractionState))
def test_human_correction_reaches_next_extraction_and_disable_revokes(pipeline, mode):
    p = pipeline
    item = extract_one(p)
    assert review(p, item, note=review_note(corrected='先检查上下文中的确认邀请，不能把未推进误判为缺少用户回答。'))[0].startswith('200')
    session = p.engine.store.get(p.session.design_id)
    session.interaction_state = mode
    p.engine.store.save(session)
    submit(p, request_id='next-learning-001')
    p.service.run_once()
    examples = p.seen[-1]['reviewed_corrections']
    assert len(examples) == 1 and examples[0]['original'] == item['content']['summary']
    assert examples[0]['opinion'] == review_note()['opinion']
    assert 'scope_design_id' not in json.dumps(examples)
    active = p.repo.experiences('active')[0]
    assert review(p, active, 'disable')[0].startswith('200')
    submit(p, request_id='next-learning-002')
    p.service.run_once()
    assert p.seen[-1]['reviewed_corrections'] == []
    disabled = p.repo.experiences('disabled')[0]
    assert review(p, disabled)[0].startswith('200')
    assert len(p.repo.correction_examples(p.seen[-1])) == 1


@pytest.mark.parametrize('scope', ['session', 'project', 'global'])
def test_correction_examples_respect_scope_mode_stage_and_relevance(pipeline, scope):
    p = pipeline
    item = extract_one(p)
    content = candidate(modes=['EMVR_DIRECT'], stages=['IDEA_BRAINSTORMING'])
    assert review(p, item, content=content, scope=scope,
                  note=review_note(corrected='承接已展示内容的确认，不假定未填写信息。'))[0].startswith('200')
    payload = deepcopy(p.seen[0])
    assert len(p.repo.correction_examples(payload)) == 1
    payload['scope_design_id'] = 'other-design'
    payload['project_id'] = 'other-project'
    assert len(p.repo.correction_examples(payload)) == (1 if scope == 'global' else 0)
    payload = deepcopy(p.seen[0]); payload['evidence']['mode'] = 'GUIDED_DESIGN'
    assert p.repo.correction_examples(payload) == []
    payload = deepcopy(p.seen[0]); payload['reported_stage'] = 'HYPOTHESIS'
    assert p.repo.correction_examples(payload) == []
    payload = deepcopy(p.seen[0]); payload.update(category='other', message='不相关', topic='')
    assert p.repo.correction_examples(payload) == []


def test_old_notes_remain_readable_but_never_become_learning_examples(pipeline):
    p = pipeline
    item = extract_one(p)
    review(p, item)
    with p.repo.connection() as db:
        db.execute('UPDATE experience_reviews SET note=?', ('旧审阅理由',))
    assert p.repo.experiences('active')[0]['reviews'][0]['note'] == '旧审阅理由'
    assert p.repo.correction_examples(p.seen[0]) == []


def test_corrections_survive_restart_and_deleting_source_removes_examples():
    path = workspace_temp_path('.sqlite')
    try:
        repo = ExperienceStore(path)
        session = DesignSession('durable-correction', InteractionState.EMVR_DIRECT)
        repo.submit(session, {'message': '已经回答', 'category': 'answered_pending', 'request_id': 'correction-durable-001'})
        job = repo.claim()
        repo.finish(job, candidate())
        item = repo.experiences()[0]
        repo.review(item['id'], 'approve', 1, review_note(corrected='承接上一轮已展示的确认事项。'))
        repo.close()
        restarted = ExperienceStore(path)
        assert len(restarted.correction_examples(job['payload'])) == 1
        restarted.delete_design(session.design_id)
        assert restarted.correction_examples(job['payload']) == []
        restarted.close()
    finally:
        remove_sqlite_files(path)


def test_evidence_capture_is_read_only_and_tolerates_unmigrated_state():
    session = DesignSession('legacy', InteractionState.EMVR_DIRECT)
    session.design_context['design_state'] = None
    session.model_context['dialogue_state'] = None
    original = deepcopy(session)
    state = workflow_evidence_state(session)
    assert session == original
    assert state['pending_excerpt'] == 'null'


def test_unchanged_and_rejected_reviews_do_not_teach_extractor(pipeline):
    item = extract_one(pipeline)
    review(pipeline, item, 'reject', note=review_note(corrected='应先核实证据再总结经验。'))
    assert pipeline.repo.correction_examples(pipeline.seen[0]) == []


def test_legacy_structured_basis_is_normalized_without_losing_text():
    old = review_note()
    old['basis'] = old.pop('opinion')
    assert validate_review_note(old) == review_note()
    assert 'basis' in old  # Do not mutate stored legacy audit data.
    with pytest.raises(ValueError):
        validate_review_note({**old, 'opinion': '两个字段含义冲突'})


def test_opinion_is_advice_not_an_automatic_status_command(pipeline):
    item = extract_one(pipeline)
    opinion = '暂不采用，需先限定范围并核对事实。'
    assert review(pipeline, item, 'reject', note=review_note(opinion=opinion))[0].startswith('200')
    assert pipeline.repo.experiences('rejected')[0]['status'] == 'rejected'
    assert json.loads(pipeline.repo.experiences('rejected')[0]['reviews'][0]['note'])['opinion'] == opinion
