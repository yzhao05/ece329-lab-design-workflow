"""Cross-stage attribution, review mutation boundaries and finite job recovery."""
from copy import deepcopy
import json

import pytest

from ece329_workflow.experience import ExperienceStore, FeedbackService
from ece329_workflow.models import DesignSession, InteractionState, Stage
from tests.test_feedback_pipeline import pipeline, submit, extract_one, review, review_note, candidate


def test_general_feedback_after_transition_uses_the_answered_stage_and_mode(pipeline):
    p = pipeline
    session = p.engine.store.get(p.session.design_id)
    session.current_stage_index = 1
    session.interaction_state = InteractionState.EMVR_DIRECT
    session.history[-1].update(handled_stage=Stage.IDEA_BRAINSTORMING.value,
                               interaction_state=InteractionState.GUIDED_DESIGN.value)
    p.engine.store.save(session)
    receipt = submit(p)[2]
    p.service.run_once()
    assert receipt['stage'] == Stage.IDEA_BRAINSTORMING.value
    evidence = p.seen[-1]['evidence']
    assert evidence['stage'] == session.current_stage.value
    assert evidence['mode'] == 'EMVR_DIRECT'
    assert evidence['reported_mode'] == 'GUIDED_DESIGN'
    assert evidence['reported_turn']['revision'] == session.revision


def test_explicit_mismatched_stage_is_not_silently_rebound(pipeline):
    submit(pipeline, stage='HYPOTHESIS')
    pipeline.service.run_once()
    evidence = pipeline.seen[-1]['evidence']
    assert evidence['reported_turn'] is None and evidence['event_chain'] == []


def test_correction_retrieval_uses_historical_mode(pipeline):
    p = pipeline
    item = extract_one(p)
    assert review(p, item, content=candidate(modes=['GUIDED_DESIGN'], summary='承接用户对已展示内容的确认。'),
                  note=review_note(corrected='承接用户对已展示内容的确认。'))[0].startswith('200')
    payload = deepcopy(p.seen[0])
    payload['evidence'].update(mode='EMVR_DIRECT', reported_mode='GUIDED_DESIGN')
    assert len(p.repo.correction_examples(payload)) == 1
    payload['evidence'].update(mode='GUIDED_DESIGN', reported_mode='EMVR_DIRECT')
    assert p.repo.correction_examples(payload) == []


@pytest.mark.parametrize('decision', ['reject', 'disable', 'delete'])
@pytest.mark.parametrize('change', [{'scope': 'session'}, {'content': candidate(summary='不应写入的内容')}])
def test_nonapproval_cannot_change_rule_or_scope(pipeline, decision, change):
    item = extract_one(pipeline)
    if decision == 'disable':
        review(pipeline, item)
        item = pipeline.repo.experiences('active')[0]
    before = deepcopy(item)
    assert review(pipeline, item, decision, **change)[0].startswith('400')
    assert pipeline.repo.experiences()[0] == before


def test_all_approved_field_edits_teach_even_when_selected_field_is_unchanged(pipeline):
    p = pipeline
    item = extract_one(p)
    edited = candidate(trigger='上一轮邀请确认后用户表示继续。', recommendation='记录确认，推进下一待办；遇到真实阻塞时说明。')
    assert review(p, item, content=edited)[0].startswith('200')
    examples = p.repo.correction_examples(p.seen[0])
    assert len(examples) == 1
    assert {change['field'] for change in examples[0]['changes']} == {'trigger', 'recommendation'}
    assert examples[0]['opinion'] == review_note()['opinion']
    active = p.repo.experiences('active')[0]
    review(p, active, 'disable')
    disabled = p.repo.experiences('disabled')[0]
    # Reverting a field must not resurrect its older correction example.
    review(p, disabled, content=candidate(recommendation=edited['recommendation']), note=review_note(disabled['content']))
    examples = p.repo.correction_examples(p.seen[0])
    assert all(change['corrected'] != edited['trigger'] for entry in examples for change in entry['changes'])


def test_exhausted_queued_job_is_terminal_instead_of_polling_forever(pipeline):
    p = pipeline
    submit(p)
    with p.repo.connection() as db:
        db.execute("UPDATE feedback_tickets SET status='queued',attempts=10")
    assert p.repo.has_work()
    p.service._run()
    assert not p.repo.has_work()
    ticket = p.repo.tickets(p.session.design_id)[0]
    assert ticket['status'] == 'failed' and not ticket['can_retry']
    assert p.seen == []


def test_legacy_basis_review_can_still_teach_from_audit(pipeline):
    p = pipeline
    item = extract_one(p)
    review(p, item, content=candidate(summary='核对确认对象并继续对应事项。'), note=review_note(corrected='核对确认对象并继续对应事项。'))
    with p.repo.connection() as db:
        row = db.execute('SELECT note FROM experience_reviews').fetchone()
        note = json.loads(row['note']); note['basis'] = note.pop('opinion')
        db.execute('UPDATE experience_reviews SET note=?', (json.dumps(note),))
    assert p.repo.correction_examples(p.seen[0])[0]['opinion'] == review_note()['opinion']
