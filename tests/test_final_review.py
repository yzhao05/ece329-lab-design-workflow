"""Guided export requires saved feedback, independently of model availability."""
import base64
import io
import json
import os
from types import SimpleNamespace

import pdfplumber
import pytest
from PIL import Image

from ece329_workflow.experience import ModelExperienceExtractor
from ece329_workflow.feedback_images import validate_images
from ece329_workflow.models import InteractionState, WorkflowStatus
from ece329_workflow.reporting import render_design_report_pdf
from tests.test_feedback_pipeline import pipeline, submit, extraction_response
from tests.test_security_and_store import call_api


def screenshot(role='problem', format='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(buffer, format=format)
    return {'role': role, 'data_url': 'data:image/' + format.lower() + ';base64,' + base64.b64encode(buffer.getvalue()).decode()}


def completed(p):
    s = p.engine.store.get(p.session.design_id)
    s.interaction_state = InteractionState.GUIDED_DESIGN
    s.status = WorkflowStatus.COMPLETE
    s.current_stage_index = 12
    s.design_context['synthesis'] = {'student_summary': '我比较电流变化与磁场强度之间的关系。'}
    p.engine.store.save(s)
    return s


def download(p, suffix='pdf', authenticated=True):
    captured = {}
    def start(status, headers):
        captured.update(status=status, headers=dict(headers))
    body = b''.join(p.api({'REQUEST_METHOD': 'GET',
        'PATH_INFO': f'/v1/designs/{p.session.design_id}/guided-summary.{suffix}',
        'QUERY_STRING': '', 'wsgi.input': io.BytesIO(), 'REMOTE_ADDR': '127.0.0.1',
        'HTTP_AUTHORIZATION': 'Bearer owner-token' if authenticated else ''}, start))
    return captured['status'], captured['headers'], body


def test_gate_requires_current_completed_design_and_not_successful_analysis(pipeline):
    p = pipeline
    submit(p, category='final_review')  # Earlier feedback cannot authorize a later completion.
    s = completed(p)
    assert download(p)[0].startswith('409')
    assert download(p, 'txt')[0].startswith('409')
    assert download(p, authenticated=False)[0].startswith('401')
    submit(p, category='final_review', revision=1, request_id='final-review-old-001')
    assert download(p)[0].startswith('409')
    status, _, receipt = submit(p, category='final_review', request_id='final-review-ready-001')
    assert status.startswith('201')
    # Saved feedback is sufficient, even when no extraction model is available.
    assert p.repo.has_final_review(s.design_id, s.revision)
    status, headers, pdf = download(p)
    assert status.startswith('200') and headers['Content-Type'] == 'application/pdf'
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        text = '\n'.join(page.extract_text() or '' for page in document.pages)
    assert '我比较电流变化' in text
    assert '总结 PDF' in text
    assert not p.repo.has_final_review('another-design', s.revision)
    s = p.engine.store.get(s.design_id)
    s.revision += 1
    p.engine.store.save(s)
    assert download(p)[0].startswith('200')  # A read-only conversation does not change the reviewed content.
    s = p.engine.store.get(s.design_id)
    s.design_context['synthesis']['student_summary'] += '现在改为比较半径。'
    s.revision += 1
    p.engine.store.save(s)
    assert download(p)[0].startswith('409')


@pytest.mark.parametrize('format', ['JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF'])
def test_screenshot_formats_are_decoded_and_normalized(format):
    result = validate_images([screenshot(format=format)], 'final_review', False)
    assert result[0]['data_url'].startswith('data:image/jpeg;base64,')


@pytest.mark.parametrize('images,category,problem', [
    ([{'role': [], 'data_url': ''}], 'final_review', False),
    ([{'role': 'problem', 'data_url': 'data:image/png;base64,garbage'}], 'final_review', False),
    ([screenshot()], 'other', False),
    ([screenshot()], 'final_review', True),
    ([screenshot(), screenshot()], 'final_review', False),
    ([], 'final_review', 'true'),
])
def test_invalid_image_evidence_is_rejected(images, category, problem):
    with pytest.raises(ValueError):
        validate_images(images, category, problem)


def test_problem_requires_all_context_images_and_maintainer_can_view_them(pipeline):
    p = pipeline
    completed(p)
    assert submit(p, category='final_review', has_problem=True)[0].startswith('400')
    images = [screenshot(role) for role in ('problem', 'before', 'after')]
    status, _, receipt = submit(p, category='final_review', attachments=images, has_problem=True)
    assert status.startswith('201')
    detail = call_api(p.api, 'GET', f"/v1/feedback/tickets/{receipt['id']}", request_headers=p.admin)[2]
    assert [i['role'] for i in detail['evidence']['attachments']] == ['problem', 'before', 'after']
    assert 'attachments' not in p.repo.tickets(p.session.design_id)[0]
    assert submit(p, category='final_review', attachments=images, has_problem=True)[2]['id'] == receipt['id']
    assert submit(p, category='final_review', attachments=[], has_problem=False)[0].startswith('409')


def test_large_image_submission_has_its_own_bounded_request_limit(pipeline):
    buffer = io.BytesIO()
    Image.frombytes('RGB', (300, 300), os.urandom(300 * 300 * 3)).save(buffer, format='PNG')
    image = {'role': 'problem', 'data_url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}
    assert len(image['data_url']) > pipeline.api.settings.max_body_bytes
    assert submit(pipeline, category='final_review', attachments=[image])[0].startswith('201')
    assert call_api(pipeline.api, 'POST', '/v1/designs', {'idea': image['data_url']})[0].startswith('413')


def test_screenshots_and_gate_survive_database_reopen(pipeline):
    from ece329_workflow.experience import ExperienceStore
    from tests.test_security_and_store import workspace_temp_path, remove_sqlite_files
    path = workspace_temp_path('.sqlite')
    s = completed(pipeline)
    try:
        store = ExperienceStore(path)
        receipt, _ = store.submit(s, {'message': '整体体验顺畅', 'category': 'final_review',
            'request_id': 'persistent-final-review', 'attachments': [screenshot()]})
        store.close()
        store = ExperienceStore(path)
        assert store.has_final_review(s.design_id, s.revision)
        assert store.feedback_detail(receipt['id'])['evidence']['attachments'][0]['role'] == 'problem'
        store.close()
    finally:
        remove_sqlite_files(path)


@pytest.mark.parametrize('model', ['gpt-5.4-mini', 'deepseek-flash'])
def test_both_model_passes_receive_images_or_explicit_text_only_limitation(model):
    calls = []
    def create(body):
        calls.append(body)
        return extraction_response(body)
    extractor = ModelExperienceExtractor(SimpleNamespace(model=model, reasoning_effort='low',
        transport=SimpleNamespace(create=create)))
    extractor.extract({'message': '流程体验反馈', 'category': 'final_review',
                       'attachments': [screenshot()], 'evidence': {}})
    assert len(calls) == 2
    for call in calls:
        content = call['input'][0]['content']
        text = content[0]['text']
        assert 'base64' not in text
        if model.startswith('deepseek'):
            assert len(content) == 1
            assert 'cannot inspect screenshots' in text
        else:
            assert any(block['type'] == 'input_image' for block in content)
            assert 'attachment:0' in text


def test_long_guided_report_has_all_content_and_bounded_page_text(pipeline):
    p = pipeline
    s = completed(p)
    s.design_context['synthesis']['student_summary'] = 'B=μ₀I/(2R)；距离 r²。\n' + ('实验步骤与结果解释。' * 800) + '总结结束标记'
    p.engine.store.save(s)
    report = p.engine.build_guided_summary_report(s.design_id)
    assert len(report['sections']) == 7
    data = render_design_report_pdf(report)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        assert '总结结束标记' in text
        assert '0' in text and '2' in text
        assert len(pdf.pages) > 2
        for page in pdf.pages:
            assert all(0 <= ch['x0'] < ch['x1'] <= page.width + .1 for ch in page.chars)
            assert all(0 <= ch['top'] < ch['bottom'] <= page.height + .1 for ch in page.chars)


def test_legacy_review_without_fingerprint_only_unlocks_original_revision(pipeline):
    from ece329_workflow.experience import final_review_content_fingerprint
    p = pipeline
    s = completed(p)
    _, _, receipt = submit(p, category='final_review')
    with p.repo.connection() as db:
        row = db.execute('SELECT payload FROM feedback_tickets WHERE id=?', (receipt['id'],)).fetchone()
        payload = json.loads(row['payload'])
        payload.pop('review_content_fingerprint')
        db.execute('UPDATE feedback_tickets SET payload=? WHERE id=?', (json.dumps(payload), receipt['id']))
    stamp = final_review_content_fingerprint(s)
    assert p.repo.has_final_review(s.design_id, s.revision, stamp)
    assert not p.repo.has_final_review(s.design_id, s.revision + 1, stamp)


def test_review_content_stamp_tracks_design_changes_but_not_revision(pipeline):
    from ece329_workflow.experience import final_review_content_fingerprint
    s = completed(pipeline)
    stamp = final_review_content_fingerprint(s)
    s.revision += 1
    assert final_review_content_fingerprint(s) == stamp
    s.design_context.setdefault('idea', {})['original'] = '现在研究电磁感应'
    assert final_review_content_fingerprint(s) != stamp
