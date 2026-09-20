import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from ece329_workflow.evidence_migrations import upgrade_payload
from ece329_workflow.experience import ExperienceStore
from ece329_workflow.models import DesignSession, InteractionState
from ece329_workflow.store import SQLiteSessionStore
from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files
from tests.test_feedback_pipeline import candidate


def old_payload():
    return {'message': '确认后没有推进', 'category': 'answered_pending', 'reported_revision': 2,
            'reported_stage': 'IDEA_BRAINSTORMING', 'scope': 'global',
            'evidence': {'source': {'revision': 2}, 'stage': 'IDEA_BRAINSTORMING',
                         'reported_turn': {'revision': 2, 'user': '继续', 'assistant': ''},
                         'event_chain': [], 'current_state': {'stage': 'CURRENT_ONLY'}},
            'analysis_attempts': [{'attempt': 1, 'status': 'completed'}],
            'attachments': [{'role': 'problem', 'data_url': 'data:image/png;base64,original'}],
            'extraction_candidate': candidate()}


def history():
    return [{'revision': revision, 'handled_stage': 'IDEA_BRAINSTORMING', 'interaction_state': 'EMVR_DIRECT',
             'user_message': '继续', 'output': {'assistant_message': '', 'warnings': ['原始提示']},
             'feedback_state_before': {'stage': f'HISTORY_{revision}'},
             'feedback_state_after': {'stage': f'HISTORY_{revision}'}}
            for revision in (1, 2, 3)]


def test_upgrade_uses_historical_states_and_excludes_post_submission_turns():
    original = old_payload()
    migrated, changed = upgrade_payload(original, history())
    assert changed and original == old_payload()
    snapshot = migrated['evidence']
    assert [row['revision'] for row in snapshot['event_chain']] == [1, 2]
    target = snapshot['event_chain'][1]
    assert target['recorded_fields']['assistant'] is True and target['assistant'] == ''
    assert target['state_before']['stage'] == 'HISTORY_2'
    assert target['warnings'] == ['原始提示']
    assert migrated['attachments'] == original['attachments']
    assert migrated['analysis_attempts'] == original['analysis_attempts']
    assert migrated['extraction_candidate'] == original['extraction_candidate']
    assert upgrade_payload(migrated, history()) == (migrated, False)


def test_missing_history_remains_unknown_and_conflicting_originals_are_preserved():
    migrated, _ = upgrade_payload(old_payload(), [])
    target = migrated['evidence']['reported_turn']
    assert 'assistant' not in target['recorded_fields']
    assert 'state_before' not in target
    assert migrated['evidence']['event_chain'] == []
    rows = history()
    rows[1]['output']['assistant_message'] = '历史记录与原摘录不一致'
    conflicted, _ = upgrade_payload(old_payload(), rows)
    assert conflicted['evidence']['reported_turn']['assistant'] == ''
    assert conflicted['evidence']['event_chain'][1]['assistant'] == ''
    assert 'assistant' not in conflicted['evidence']['event_chain'][1]['recorded_fields']
    assert 'assistant' not in conflicted['evidence']['reported_turn']['recorded_fields']
    assert 'turn:2:assistant' in conflicted['evidence']['evidence_migration']['conflicting_fields_preserved']


@pytest.mark.parametrize('mode', list(InteractionState))
def test_startup_migrates_existing_experience_evidence_once_with_backup_and_review_preservation(mode):
    path = workspace_temp_path('.sqlite')
    try:
        sessions = SQLiteSessionStore(path)
        session = DesignSession('migration-design', mode, revision=3, history=history())
        for turn in session.history:
            turn['interaction_state'] = mode.value
        sessions.save(session)
        store = ExperienceStore(path)
        receipt, _ = store.submit(session, {'message': '确认后没有推进', 'request_id': 'migration-ticket-001'})
        original = json.dumps(old_payload(), ensure_ascii=False)
        with store.connection() as db:
            db.execute('UPDATE feedback_tickets SET payload=? WHERE id=?', (original, receipt['id']))
            db.execute('INSERT INTO learned_experiences VALUES(?,?,?,?,?,?,?,?)',
                       ('experience-1', receipt['id'], 'digest', json.dumps(candidate()), 'active', 7, '人工理由', 100))
            db.execute('INSERT INTO experience_reviews VALUES(?,?,?,?,?,?)',
                       ('experience-1', 7, 'approve', '人工意见', '原始审阅内容', 100))
            rule_before = tuple(db.execute('SELECT * FROM learned_experiences').fetchone())
            review_before = tuple(db.execute('SELECT * FROM experience_reviews').fetchone())
        store.close()
        # This is the production startup path, not an explicit migration command.
        reopened = ExperienceStore(path)
        assert reopened.evidence_migration_summary['migrated'] == 1
        assert reopened.feedback_detail(receipt['id'])['evidence']['evidence']['evidence_schema_version'] == 2
        with reopened.connection() as db:
            assert tuple(db.execute('SELECT * FROM learned_experiences').fetchone()) == rule_before
            assert tuple(db.execute('SELECT * FROM experience_reviews').fetchone()) == review_before
            audit = dict(db.execute('SELECT * FROM experience_evidence_migrations').fetchone())
            assert audit['previous_payload'] == original
        reopened.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            stores = list(pool.map(lambda _: ExperienceStore(path), range(2)))
        for again in stores:
            assert again.evidence_migration_summary == {'migrated': 0, 'current': 0, 'unavailable': 0}
            with again.connection() as db:
                assert dict(db.execute('SELECT * FROM experience_evidence_migrations').fetchone()) == audit
            again.close()
        cleaned = ExperienceStore(path)
        cleaned.delete_design(session.design_id)
        with cleaned.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM experience_evidence_migrations').fetchone()[0] == 0
        cleaned.close()
    finally:
        remove_sqlite_files(path)


def test_unknown_submission_boundary_does_not_invent_neighbor_context():
    payload = old_payload()
    del payload['evidence']['source']
    migrated, _ = upgrade_payload(payload, history())
    assert migrated['evidence']['event_chain'] == []
    assert migrated['evidence']['reported_turn']['state_before']['stage'] == 'HISTORY_2'
