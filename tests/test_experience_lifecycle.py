"""Three experience states, independent feedback outcomes and legacy migration."""
import json

import pytest

from ece329_workflow.experience import ExperienceStore
from ece329_workflow.models import DesignSession, InteractionState, SessionConflict
from tests.test_feedback_pipeline import pipeline, extract_one, review, candidate, submit
from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files


def note(reason):
    return {'original': '', 'corrected': '', 'opinion': reason}


@pytest.mark.parametrize('mode', list(InteractionState))
def test_stop_edit_reenable_and_stop_again_preserve_history_and_feedback(pipeline, mode):
    p = pipeline
    item = extract_one(p)
    assert review(p, item, 'stop', note=note('总结不准确'))[2]['status'] == 'stopped'
    stopped = p.repo.experiences('stopped')[0]
    assert p.repo.retrieve(mode.value, 'IDEA_BRAINSTORMING', '已经回答') == []
    edited = candidate(trigger='核对上下文，用户确已回答当前问题时。')
    assert review(p, stopped, content=edited, note=note('修订后批准'))[2]['status'] == 'active'
    active = p.repo.experiences('active')[0]
    assert active['content'] == edited
    assert p.repo.retrieve(mode.value, 'IDEA_BRAINSTORMING', '已经回答')
    assert review(p, active, 'stop', note=note('被新经验替代'))[2]['status'] == 'stopped'
    assert p.repo.retrieve(mode.value, 'IDEA_BRAINSTORMING', '已经回答') == []
    stopped = p.repo.experiences('stopped')[0]
    assert [r['decision'] for r in stopped['reviews']] == ['stop','approve','stop']
    assert [json.loads(r['note'])['opinion'] for r in stopped['reviews']] == ['总结不准确','修订后批准','被新经验替代']
    assert p.repo.correction_examples(p.seen[0]) == []
    ticket = p.repo.tickets(p.session.design_id)[0]
    assert ticket['status'] == 'candidate'
    assert ticket['experience'] == {'id': item['id'], 'status': 'stopped'}
    assert not ticket['can_retry']
    inbox = p.repo.feedback_inbox()
    assert inbox['counts'] == {'candidate': 1}
    assert inbox['feedback'][0]['experience'] == ticket['experience']
    assert p.repo.feedback_detail(ticket['id'])['experience'] == ticket['experience']
    assert review(p, active, 'stop', note=note('重复提交'))[0].startswith('409')
    assert review(p, stopped, 'stop', note=note('再次停止'))[0].startswith('400')


@pytest.mark.parametrize('legacy,decision', [('rejected','reject'), ('disabled','disable'), ('deleted','delete')])
def test_upgrade_keeps_legacy_reason_and_allows_reenable_once(legacy, decision):
    path = workspace_temp_path('.sqlite')
    try:
        repo = ExperienceStore(path)
        repo.submit(DesignSession('legacy-design',InteractionState.EMVR_DIRECT),
                    {'message':'已经回答','request_id':'legacy-review-001'})
        repo.finish(repo.claim(),candidate())
        item = repo.experiences()[0]
        repo.review(item['id'],decision,1,note('旧的具体原因')) if decision != 'disable' else repo.review(item['id'],'approve',1,note('先启用'))
        if decision == 'disable':
            repo.review(item['id'],decision,2,note('旧的具体原因'))
        with repo.connection() as db:
            db.execute('UPDATE learned_experiences SET status=?',(legacy,))
            db.execute('UPDATE feedback_tickets SET status=?',(legacy,))
        before = repo.experiences()[0]
        repo.close()
        repo = ExperienceStore(path)
        migrated = repo.experiences('stopped')[0]
        assert migrated['content'] == before['content']
        assert migrated['reviews'] == before['reviews']
        assert migrated['review_note'] == before['review_note']
        assert migrated['version'] == before['version'] + 1
        assert repo.tickets('legacy-design')[0]['status'] == 'candidate'
        with pytest.raises(SessionConflict):
            repo.review(item['id'],'approve',before['version'],note('旧页面批准'))
        repo.close()
        repo = ExperienceStore(path)
        assert repo.experiences('stopped')[0]['version'] == migrated['version']
        assert repo.review(item['id'],'approve',migrated['version'],note('现在批准'))['status'] == 'active'
        assert repo.experiences('active')[0]['reviews'][-2]['decision'] == decision
        repo.close()
    finally:
        remove_sqlite_files(path)


def test_duplicate_feedback_tracks_stopped_experience_without_reactivating(pipeline):
    p = pipeline
    item = extract_one(p)
    review(p,item,'stop',note=note('暂时停用'))
    submit(p,request_id='duplicate-stopped-001');p.service.run_once()
    records = p.repo.tickets(p.session.design_id)
    assert {r['status'] for r in records} == {'candidate','duplicate'}
    assert all(r['experience']['status'] == 'stopped' for r in records)
    assert len(p.repo.experiences()) == 1
    stopped = p.repo.experiences('stopped')[0]
    review(p,stopped,note=note('批准'))
    assert all(r['experience']['status'] == 'active' for r in p.repo.tickets(p.session.design_id))
    assert p.repo.feedback_inbox()['counts'] == {'candidate':1,'duplicate':1}


@pytest.mark.parametrize('change', [{'content':candidate(summary='不可在停止时偷偷修改')},{'scope':'session'}])
def test_stop_does_not_modify_rule_or_scope(pipeline, change):
    item = extract_one(pipeline)
    assert review(pipeline,item,'stop',note=note('暂时停止'),**change)[0].startswith('400')
    assert pipeline.repo.experiences()[0] == item
