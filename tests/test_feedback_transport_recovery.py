"""Real transport error mapping must drive the bounded provider fallback."""
from contextlib import contextmanager
from http.client import IncompleteRead, RemoteDisconnected
from io import BytesIO
from urllib.error import HTTPError

import pytest

from ece329_workflow.experience import ExperienceStore
from ece329_workflow.openai_generator import OpenAIResponsesHTTPTransport, ModelHTTPError, ModelOutputError
from tests.test_feedback_pipeline import pipeline, submit, candidate, review_note
from tests.test_feedback_failover import configure
from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files


@pytest.mark.parametrize('error', [ConnectionResetError('private host'), RemoteDisconnected('private host'),
                                  IncompleteRead(b'private response'), OSError('private network failure')])
@pytest.mark.parametrize('during_read', [False, True])
def test_interrupted_network_uses_backup_and_preserves_attempt_budget(pipeline, monkeypatch, error, during_read):
    p = pipeline
    calls, generator = configure(p, None)
    class BrokenResponse(BytesIO):
        def read(self, *args):
            raise error
    def urlopen(*args, **kwargs):
        if during_read:
            return BrokenResponse()
        raise error
    monkeypatch.setattr('ece329_workflow.openai_generator.urlopen', urlopen)
    generator.transport.providers['openai'] = OpenAIResponsesHTTPTransport('test-only')
    ticket = submit(p)[2]
    p.service.run_once()
    detail = p.repo.feedback_detail(ticket['id'])
    assert detail['status'] == 'candidate' and detail['attempts'] == 2
    assert detail['evidence']['analysis_attempts'][0]['code'] == 'model_connection_error'
    assert [provider for provider, _ in calls] == ['deepseek', 'deepseek']
    assert 'private' not in str(detail)


def test_http_error_status_survives_broken_error_body_and_closes_response(monkeypatch):
    class BrokenBody(BytesIO):
        def read(self, *args):
            raise IncompleteRead(b'private')
    body = BrokenBody()
    error = HTTPError('https://test.invalid', 503, 'private', {}, body)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr('ece329_workflow.openai_generator.urlopen', fail)
    with pytest.raises(ModelHTTPError) as caught:
        OpenAIResponsesHTTPTransport('test-only').create({})
    assert caught.value.status_code == 503 and caught.value.error_code is None
    assert body.closed


def test_invalid_utf8_is_an_output_error_instead_of_an_internal_failure(monkeypatch):
    monkeypatch.setattr('ece329_workflow.openai_generator.urlopen', lambda *a, **kw: BytesIO(b'\xff'))
    with pytest.raises(ModelOutputError):
        OpenAIResponsesHTTPTransport('test-only').create({})


def test_inbox_counts_and_rows_share_snapshot_during_another_workers_write(pipeline, monkeypatch):
    path = workspace_temp_path('.sqlite')
    first = second = None
    try:
        first, second = ExperienceStore(path), ExperienceStore(path)
        with first.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
        first.submit(pipeline.session, {'message':'first', 'request_id':'snapshot-first-001'})
        original = first.connection
        inserted = False
        @contextmanager
        def connection():
            nonlocal inserted
            with original() as db:
                def concurrent_write(sql):
                    nonlocal inserted
                    if sql.startswith('SELECT t.*') and not inserted:
                        inserted = True
                        second.submit(pipeline.session, {'message':'second', 'request_id':'snapshot-second-002'})
                db.set_trace_callback(concurrent_write)
                yield db
        monkeypatch.setattr(first, 'connection', connection)
        inbox = first.feedback_inbox()
        assert inserted and inbox['total'] == len(inbox['feedback']) == 1
        next_page = first.feedback_inbox()
        assert next_page['total'] == len(next_page['feedback']) == 2
    finally:
        if first: first.close()
        if second: second.close()
        remove_sqlite_files(path)


@pytest.mark.parametrize('reader', ['experiences', 'feedback_detail'])
def test_review_evidence_and_versions_share_snapshot_during_concurrent_approval(pipeline, monkeypatch, reader):
    path = workspace_temp_path('.sqlite')
    first = second = None
    try:
        first, second = ExperienceStore(path), ExperienceStore(path)
        with first.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
        ticket, _ = first.submit(pipeline.session, {'message': 'snapshot review', 'request_id': 'review-snapshot-001'})
        first.finish(first.claim(), candidate())
        experience = first.experiences()[0]
        original = first.connection
        approved = False
        @contextmanager
        def connection():
            nonlocal approved
            with original() as db:
                def concurrent_write(sql):
                    nonlocal approved
                    trigger = ('SELECT version,decision,note,content,created' if reader == 'experiences'
                               else 'SELECT id,status FROM learned_experiences')
                    if sql.startswith(trigger) and not approved:
                        approved = True
                        second.review(experience['id'], 'approve', experience['version'], review_note(),
                                      content=candidate(summary='已核对的新经验摘要'), scope='session')
                db.set_trace_callback(concurrent_write)
                yield db
        monkeypatch.setattr(first, 'connection', connection)
        if reader == 'experiences':
            old = first.experiences()[0]
            assert approved and old['version'] == experience['version']
            assert old['reviews'] == [] and old['content'] == experience['content']
            new = first.experiences()[0]
            assert new['version'] == experience['version'] + 1 and len(new['reviews']) == 1
        else:
            old = first.feedback_detail(ticket['id'])
            assert approved and old['experience']['status'] == 'candidate'
            assert old['evidence']['scope'] == 'global'
            new = first.feedback_detail(ticket['id'])
            assert new['experience']['status'] == 'active' and new['evidence']['scope'] == 'session'
    finally:
        if first: first.close()
        if second: second.close()
        remove_sqlite_files(path)
