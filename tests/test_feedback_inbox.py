"""A saved feedback submission must remain visible without a learned experience."""
from types import SimpleNamespace

import pytest

from tests.test_feedback_pipeline import pipeline, submit, candidate, extraction_draft, extraction_check
from tests.test_security_and_store import call_api, workspace_temp_path, remove_sqlite_files
from ece329_workflow.experience import ExperienceStore


def admin_get(p, url):
    path, _, query = url.partition('?')
    def app(environ, start_response):
        environ['QUERY_STRING'] = query
        return p.api(environ, start_response)
    return call_api(app, 'GET', path, request_headers=p.admin)


def inbox(p, query=''):
    return admin_get(p, '/v1/feedback/tickets' + query)


def test_two_submissions_are_visible_before_extraction(pipeline):
    p = pipeline
    submit(p); submit(p, request_id='second-feedback-001')
    assert p.repo.experiences('candidate') == []
    status, _, body = inbox(p)
    assert status.startswith('200') and body['total'] == 2
    assert len(body['feedback']) == 2 and body['counts'] == {'queued': 2}
    assert all(item['experience'] is None for item in body['feedback'])
    assert 'evidence' not in body['feedback'][0]  # Large evidence is loaded on demand.


@pytest.mark.parametrize('state', ['running', 'failed', 'no_learning', 'duplicate'])
def test_no_candidate_does_not_hide_feedback(pipeline, state):
    p = pipeline
    submit(p)
    if state == 'running':
        p.repo.claim()
    elif state == 'failed':
        p.repo.finish(p.repo.claim(), error='分析未完成')
    elif state == 'no_learning':
        p.repo.finish(p.repo.claim(), {**candidate(useful=False), 'analysis': {
            'diagnosis': extraction_draft()['diagnosis'], 'model_check': extraction_check(evidence_supported=False),
            'validation_status': 'not_replayed'}})
    else:
        p.service.run_once()
        submit(p, request_id='duplicate-feedback-001')
        p.service.run_once()
    body = inbox(p, '?status=' + state)[2]
    assert body['filtered_total'] == 1
    record = body['feedback'][0]
    assert record['status'] == state
    detail = call_api(p.api, 'GET', f"/v1/feedback/tickets/{record['id']}", request_headers=p.admin)[2]
    assert detail['evidence']['message'] == record['message']
    if state == 'no_learning':
        assert not detail['evidence']['extraction_candidate']['useful']
        assert not detail['evidence']['extraction_analysis']['model_check']['evidence_supported']
        assert p.repo.experiences() == []
    if state == 'duplicate':
        assert record['experience']['id'] == p.repo.experiences()[0]['id']


def test_inbox_details_and_retry_require_maintainer_auth(pipeline):
    p = pipeline
    ticket = submit(p)[2]
    for path, method in [('/v1/feedback/tickets','GET'),
                         (f"/v1/feedback/tickets/{ticket['id']}",'GET'),
                         (f"/v1/feedback/tickets/{ticket['id']}/retry",'POST')]:
        assert call_api(p.api, method, path, request_headers=p.auth)[0].startswith('401')
        assert call_api(p.api, method, path)[0].startswith('401')


def test_maintainer_retry_is_bounded_and_does_not_advance_design(pipeline):
    p = pipeline
    p.service.extractor = SimpleNamespace(extract=lambda _: (_ for _ in ()).throw(ValueError('private')))
    ticket = submit(p)[2]
    before = p.engine.store.get(p.session.design_id)
    for attempt in range(1, 11):
        p.service.run_once()
        body = inbox(p)[2]
        assert body['feedback'][0]['attempts'] == attempt
        status = call_api(p.api, 'POST', f"/v1/feedback/tickets/{ticket['id']}/retry", {}, request_headers=p.admin)[0]
        assert status.startswith('202' if attempt < 10 else '400')
    assert p.engine.store.get(p.session.design_id) == before


def test_exact_related_experience_lookup_and_pagination(pipeline):
    p = pipeline
    for i in range(52):
        submit(p, request_id=f'feedback-page-{i:03}')
    body = inbox(p)[2]
    assert body['total'] == 52 and len(body['feedback']) == 50
    assert len(inbox(p, '?offset=50')[2]['feedback']) == 2
    assert inbox(p, '?status=bad')[0].startswith('400')
    assert inbox(p, '?offset=-1')[0].startswith('400')
    p.service.run_once()
    item = p.repo.experiences()[0]
    result = admin_get(p, f"/v1/feedback/experiences?experience_id={item['id']}")[2]
    assert len(result['experiences']) == 1 and result['experiences'][0]['id'] == item['id']
    assert admin_get(p, '/v1/feedback/experiences?experience_id=bad')[0].startswith('400')


def test_existing_saved_tickets_are_visible_after_store_restart(pipeline):
    path = workspace_temp_path('.sqlite')
    try:
        first = ExperienceStore(path)
        first.submit(pipeline.session, {'message':'已提交但没有候选', 'request_id':'old-saved-ticket-001'})
        first.finish(first.claim(), candidate(useful=False)); first.close()
        second = ExperienceStore(path)
        assert second.feedback_inbox()['total'] == 1
        assert second.feedback_inbox()['feedback'][0]['status'] == 'no_learning'
        second.close()
    finally:
        remove_sqlite_files(path)
