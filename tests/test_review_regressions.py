"""Cross-worker persistence and retrieval regressions from the project review."""
import json

import pytest

from ece329_workflow.experience import ExperienceStore
from ece329_workflow.models import DesignSession, InteractionState, SessionConflict
from ece329_workflow.store import InMemorySessionStore, SQLiteSessionStore
from tests.test_feedback_pipeline import candidate
from tests.test_model_selection import make_engine, add_session, QUESTION
from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files


@pytest.fixture(params=['memory', 'sqlite'])
def stores(request):
    if request.param == 'memory':
        first = InMemorySessionStore()
        yield first, first
    else:
        path = workspace_temp_path('.sqlite3')
        try:
            yield SQLiteSessionStore(path), SQLiteSessionStore(path)
        finally:
            remove_sqlite_files(path)


@pytest.mark.parametrize('mode', list(InteractionState))
def test_same_revision_updates_cannot_overwrite_another_worker(stores, mode):
    first, second = stores
    first.save(DesignSession('race', mode))
    pending_turn = first.get('race')
    pending_config = first.get('race')
    fresh = second.get('race')
    fresh.model_context['model_config_version'] = 1
    fresh.model_context['model_config'] = {'experience_enabled': False}
    second.save(fresh, expected_revision=0)
    pending_turn.revision += 1
    for stale in (pending_turn, pending_config):
        with pytest.raises(SessionConflict):
            first.save(stale, expected_revision=0)
    assert second.get('race').model_context == fresh.model_context
    # Reload and retry succeeds; tokens stay private, including after restart.
    reloaded = first.get('race')
    reloaded.revision += 1
    first.save(reloaded, expected_revision=0)
    reloaded.revision += 1
    first.save(reloaded, expected_revision=1)
    assert second.get('race').revision == 2
    assert '_store_snapshot' not in reloaded.to_dict(include_history=True)


@pytest.mark.parametrize('mode', list(InteractionState))
def test_model_change_during_generation_conflicts_then_retry_keeps_new_config(stores, mode):
    first, second = stores
    engine, transport = make_engine(store=first)
    other, _ = make_engine(store=second)
    session = add_session(engine, mode)
    real_create = transport.create
    updates = []

    def change_during_call(payload):
        if not updates:
            config = other.model_configuration(session.design_id)
            config['config'].update(experience_enabled=False, model_override='gpt-5.5', strategy='custom')
            updates.append(other.update_model_configuration(session.design_id, config['config'], config['version']))
        return real_create(payload)

    transport.create = change_during_call
    turn = {'message': QUESTION, 'turn_id': 'concurrent-config-turn'}
    with pytest.raises(SessionConflict):
        engine.process_turn(session.design_id, turn)
    assert other.model_configuration(session.design_id)['config']['experience_enabled'] is False
    # One explicit retry after reload, rather than an automatic retry loop.
    result = engine.process_turn(session.design_id, turn)
    assert result['selected_model'] == 'gpt-5.5'
    assert result['model_config']['experience_enabled'] is False
    assert result['revision'] == session.revision + 1


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('unrelated', ['mode', 'stage', 'keywords'])
def test_newer_unrelated_experiences_do_not_hide_relevant_rule(mode, unrelated):
    repo = ExperienceStore()
    try:
        with repo.connection() as db:
            db.execute("INSERT INTO feedback_tickets(id,design_id,request_id,request_hash,payload,status,created) VALUES(?,?,?,?,?,?,?)",
                       ('ticket', 'source', 'request', 'hash', json.dumps({'scope': 'global'}), 'active', 1))
            for index in range(502):
                rule = candidate()
                if index:
                    if unrelated == 'mode':
                        rule['modes'] = [m.value for m in InteractionState if m != mode]
                    elif unrelated == 'stage':
                        rule['stages'] = ['HYPOTHESIS']
                    else:
                        rule['keywords'] = ['unrelated-keyword']
                db.execute('INSERT INTO learned_experiences(id,ticket_id,digest,content,status,updated) VALUES(?,?,?,?,?,?)',
                           (str(index), 'ticket', str(index), json.dumps(rule), 'active', index))
        found = repo.retrieve(mode.value, 'IDEA_BRAINSTORMING', '已经回答')
        assert [r['id'] for r in found] == ['EXP-0']
    finally:
        repo.close()
