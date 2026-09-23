from copy import deepcopy
from types import SimpleNamespace
import json
import pytest

from ece329_workflow.experience import validate_candidate, fingerprint
from ece329_workflow.experience_rules import contract, RuleRun, capture_replay, digest, business_state
from ece329_workflow.experience_replay import fixture, semantic, compare, verify, restore
from ece329_workflow.experience_admin import replay_draft, restore_draft, generate_draft
from ece329_workflow.models import InteractionState, SessionConflict, Stage
from ece329_workflow.feedback import guidance
from tests.test_feedback_pipeline import pipeline, candidate, extract_one, review, review_note
from tests.test_security_and_store import call_api
from tests.test_openai_generator import FakeTransport


def packet(content=None, version=1):
    content=content or candidate(execution=contract())
    return {'id':'EXP-'+'a'*32,'version':version,'scope':'global','rule':content,'execution':content['execution']}


@pytest.mark.parametrize('change',[
    lambda c:c['actions'].append({'name':'execute_sql','parameters':{'sql':'DELETE'}}),
    lambda c:c.update(max_per_turn=99),lambda c:c.update(exceptions=[]),
    lambda c:c.update(preconditions=[]),lambda c:c.update(assertions=[]),lambda c:c.update(version=True)])
def test_unknown_actions_or_weakened_contracts_fail_closed(change):
    value=contract();change(value)
    with pytest.raises(ValueError):validate_candidate(candidate(execution=value))


def test_legacy_candidates_are_not_promoted():
    assert 'execution' not in validate_candidate(candidate())


@pytest.mark.parametrize('mode',list(InteractionState))
@pytest.mark.parametrize('message',['保留当前设计，不要改，继续','do not modify anything, go ahead'])
def test_rule_closes_candidate_and_verifies_actual_workflow(mode,message):
    result=compare(fixture(mode),message,semantic(message,'positive'),packet(),True)
    assert result['passed'],result
    assert result['kind']=='isolated_simulated_replay'
    after=result['with_rule']
    assert after['reply'].strip()
    assert not after['state']['pending'].get('candidate_answer')
    assert any(e['code']=='execution_verified' for e in after['events'])


@pytest.mark.parametrize('kind',['bare','accept','edit','ambiguous'])
def test_negative_intents_do_not_execute_rule(kind):
    result=compare(fixture(InteractionState.EMVR_DIRECT),'example',semantic('example',kind),packet(),False)
    assert result['passed']


def test_complete_contract_not_truncated_by_old_instruction_budget(pipeline,monkeypatch):
    p=pipeline;item=extract_one(p)
    content=candidate(execution=contract())
    # Seed an already-reviewed row to test selection independently of approval.
    with p.repo.connection() as db:
        db.execute("UPDATE learned_experiences SET status='active',content=? WHERE id=?",(json.dumps(content),item['id']))
    session=fixture(InteractionState.GUIDED_DESIGN)
    rows,decisions=p.repo.select_rules(session,'please retain the saved design and proceed')
    assert len(rows)==1
    session.turn_context['experience_rules']=rows
    assert guidance(session,'anything')['rules'][0]['execution']==contract()
    monkeypatch.setenv('ECE329_EXPERIENCE_TOKEN_BUDGET','1')
    rows,decisions=p.repo.select_rules(session,'继续')
    assert rows==[] and any(d['reason']=='budget_excluded' for d in decisions)


def test_no_scope_only_fallback_for_unrelated_legacy_rules(pipeline):
    p=pipeline;item=extract_one(p);review(p,item)
    session=fixture(InteractionState.EMVR_DIRECT)
    session.model_context['dialogue_state']['pending_action']={}
    rows,decisions=p.repo.select_rules(session,'unrelated question')
    assert rows==[] and any(d['reason']=='not_relevant' for d in decisions)


@pytest.mark.parametrize('mode', list(InteractionState))
def test_conflicts_version_changes_and_same_state_do_not_loop(mode):
    session=fixture(mode);message='Keep the saved design and proceed'
    r=packet();other=deepcopy(r);other['id']='EXP-'+'b'*32
    other['execution']['actions'][-1]['parameters']={'style':'task_only'}
    run=RuleRun(session,message);run.select([r,other],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    assert run.blocked and session.model_context['dialogue_state']['pending_action']['candidate_answer']
    run=RuleRun(session,message,store=SimpleNamespace(rule_is_current=lambda r:False));run.select([r],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    assert run.blocked
    session.model_context['experience_execution_guard']={'key':digest((r['id'],r['version'],digest(business_state(session))))}
    run=RuleRun(session,message);run.select([r],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    assert run.blocked and run.events[-1]['code']=='no_progress_blocked'


def test_replay_is_version_and_content_bound_and_does_not_modify_design(pipeline):
    p=pipeline;item=extract_one(p);before=deepcopy(p.engine.store.get(p.session.design_id))
    value=candidate(execution=contract())
    assert review(p,item,content=value)[0].startswith('400')
    result=replay_draft(p.repo,item['id'],{'version':1,'content':value,'scope':'global'})
    assert result['report']['status']=='passed'
    assert result['report']['historical']['status']=='unavailable'
    assert result['report']['live_model_test']=='not_run'
    assert p.engine.store.get(p.session.design_id)==before
    edited=deepcopy(value);edited['summary']='另一版摘要'
    assert review(p,item,content=edited,validation_id=result['id'])[0].startswith('400')
    assert review(p,item,content=value,validation_id=result['id'],scope='session')[0].startswith('400')
    assert review(p,item,content=value,validation_id=result['id'])[0].startswith('200')
    assert review(p,item,content=value,validation_id=result['id'])[0].startswith('409')
    active=p.repo.experiences('active')[0]
    assert active['reviews'][-1]['content']['source']['validation_id']==result['id']
    assert review(p,active,'stop')[0].startswith('200')
    assert not p.repo.rule_is_current({'id':'EXP-'+item['id'],'version':2})


def test_old_verifier_report_cannot_authorize_a_new_approval(pipeline):
    p=pipeline;item=extract_one(p);value=candidate(execution=contract())
    result=replay_draft(p.repo,item['id'],{'version':1,'content':value,'scope':'global'})
    with p.repo.connection() as db:
        report=deepcopy(result['report']);report.pop('verifier_version')
        db.execute('UPDATE experience_validations SET report=? WHERE id=?',(json.dumps(report),result['id']))
    assert review(p,item,content=value,validation_id=result['id'])[0].startswith('400')
    assert p.repo.experiences()[0]['version']==1


def test_restore_creates_unapproved_draft_and_keeps_live_version(pipeline):
    p=pipeline;item=extract_one(p);review(p,item)
    active=p.repo.experiences('active')[0]
    result=restore_draft(p.repo,item['id'],{'version':active['version'],'restore_version':1,'note':review_note()})
    assert result['status']=='draft_not_approved'
    assert result['source']['restored_version']==1
    assert p.repo.experiences('active')[0]['version']==active['version']


def test_revision_draft_uses_existing_provider_once_and_records_usage(pipeline):
    p=pipeline;item=extract_one(p)
    transport=FakeTransport(output={'candidate_json':json.dumps(candidate(execution=contract())), 'unsupported_actions':[]})
    p.service.extractor=SimpleNamespace(generator=SimpleNamespace(transport=transport),models=lambda:['deepseek-flash'])
    result=generate_draft(p.service,item['id'],{'version':1,'note':review_note(),'scope':'session'})
    assert len(transport.requests)==1
    assert result['content']['execution']==contract()
    assert result['scope']=='session'
    assert p.repo.experiences()[0]['status']=='candidate'
    with p.repo.connection() as db:
        row=db.execute("SELECT record FROM usage_runs WHERE ticket_id=? AND json_extract(record,'$.purpose')='rule_revision'",(item['ticket_id'],)).fetchone()
        assert row and json.loads(row['record'])['purpose']=='rule_revision'
        record=json.loads(row['record'])
        assert record['stage_basis']=='feedback_target'
        assert record['stage']==item['evidence']['reported_stage']
        db.execute('UPDATE feedback_tickets SET attempts=2 WHERE id=?',(item['ticket_id'],))
        totals=p.repo._ticket_usage(db,item['ticket_id'])
        assert totals['run_count']==2
        assert totals['complete'] is False
        assert totals['total_tokens'] is None


def test_snapshot_never_uses_submission_state_and_omits_sensitive_context():
    session=fixture(InteractionState.GUIDED_DESIGN)
    snapshot=capture_replay(session)
    assert restore(snapshot).history==session.history
    assert restore({'complete':False}) is None
    session.design_context['api_key']='must-not-persist'
    snapshot=capture_replay(session)
    assert snapshot['complete'] is False and 'must-not-persist' not in json.dumps(snapshot)


def test_authoring_endpoints_require_admin_token(pipeline):
    p=pipeline;item=extract_one(p)
    for action in ['draft','replay','restore','configure','evaluate']:
        assert call_api(p.api,'POST',f"/v1/feedback/experiences/{item['id']}/{action}",{'version':1})[0].startswith('401')
    assert call_api(p.api,'GET','/v1/feedback/executions')[0].startswith('401')


def test_concurrent_approvals_cannot_overwrite_newer_version(pipeline):
    from concurrent.futures import ThreadPoolExecutor
    p=pipeline;item=extract_one(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:review(p,item)[0].split()[0],range(2)))
    assert sorted(statuses)==['200','409']
    assert p.repo.experiences('active')[0]['version']==2


def test_candidate_limit_scope_and_stop_reasons_are_auditable(pipeline,monkeypatch):
    p=pipeline;item=extract_one(p);review(p,item,scope='session')
    session=fixture(InteractionState.EMVR_DIRECT)
    rows,decisions=p.repo.select_rules(session,'already answered')
    assert not rows and any(d['reason']=='scope_mismatch' for d in decisions)
    session.design_id=p.session.design_id
    rows,_=p.repo.select_rules(session,'already answered');assert len(rows)==1
    active=p.repo.experiences('active')[0];review(p,active,'stop')
    rows,decisions=p.repo.select_rules(session,'already answered')
    assert not rows and any(d['reason']=='not_active' for d in decisions)


def test_validation_failure_does_not_retry_or_delete_history():
    session=fixture(InteractionState.GUIDED_DESIGN);message='keep saved design and proceed'
    run=RuleRun(session,message);run.select([packet()],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    run.hook('before_pending',session)
    assert sum(e['code']=='candidate_declined' for e in run.events)==1
    session.design_context['confirmed_note']='corrupted'
    with pytest.raises(ValueError,match='postcondition'):
        run.hook('before_reply',session,response={'assistant_message':'','revision':1})
    assert run.events[-1]['code']=='execution_failed'
    assert run.repairs<=1


def test_historical_replay_uses_recorded_before_state_not_current_state():
    session=fixture(InteractionState.EMVR_DIRECT);message='keep the saved design and proceed'
    evidence={'current_state':{'stage':'WRONG_SUBMISSION_STAGE'},
              'replay':{'state_before':capture_replay(session),'intent':semantic(message,'positive'),'message':message}}
    result=verify(candidate(execution=contract()),'a'*32,1,evidence)
    assert result['status']=='passed'
    assert result['historical']['status']=='completed'
    assert result['historical']['result']['with_rule']['state']['stage']=='IDEA_BRAINSTORMING'
    complete=[c for c in result['cases'] if c.get('scenario')=='complete_stage']
    assert len(complete)==4 and all(c['passed'] for c in complete)
    assert len(result['cases'])==264
    assert result['historical']['result']['kind']=='isolated_recorded_response_replay'
    assert all(e['kind']=='isolated_recorded_response_replay' for e in
        result['historical']['result']['with_rule']['events'] if 'kind' in e)


@pytest.mark.parametrize('mode',list(InteractionState))
def test_reply_repair_removes_exact_obsolete_question_without_rewriting_history(mode):
    session=fixture(mode);message='keep saved design and continue'
    previous=deepcopy(session.history)
    old=session.model_context['dialogue_state']['pending_action']['question']
    run=RuleRun(session,message);run.select([packet()],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    response={'assistant_message':old,'revision':1}
    run.hook('before_reply',session,response=response)
    assert old not in response['assistant_message']
    assert response['student_task'] in response['assistant_message']
    assert session.history==previous and run.repairs==1
    assert run.events[-1]['code']=='execution_verified'


@pytest.mark.parametrize('field,value',[
    ('mode',{}),('status',[]),('stage_index',-1),('stage_index',99),
    ('revision',True),('history',[None]),('model_context',[])])
def test_invalid_old_snapshots_are_unavailable_not_backend_errors(field,value):
    snapshot=capture_replay(fixture(InteractionState.GUIDED_DESIGN))
    snapshot[field]=value
    assert restore(snapshot) is None


def test_historical_keep_without_candidate_is_not_a_failed_rule():
    session=fixture(InteractionState.EMVR_DIRECT)
    session.model_context['dialogue_state']['pending_action'].pop('candidate_answer')
    message='保留当前设计并继续'
    result=verify(candidate(execution=contract()),'a'*32,1,
        {'replay':{'state_before':capture_replay(session),'intent':semantic(message,'positive'),'message':message}})
    assert result['historical']['result']['expected_applicable'] is False
    assert result['status']=='passed'


def test_stage_restricted_rule_has_positive_replay_coverage():
    result=verify(candidate(execution=contract(),stages=[Stage.RESEARCH_QUESTION.value]),'a'*32,1)
    assert result['status']=='passed'
    positives=[c for c in result['cases'] if c['expected_applicable']]
    assert len(positives)==4 and all(c['passed'] for c in positives)


@pytest.mark.parametrize('field',['mode','stage'])
def test_scope_is_rechecked_after_intent_changes_context(field):
    session=fixture(InteractionState.GUIDED_DESIGN);message='keep current and continue'
    rule=packet(candidate(execution=contract(),modes=[InteractionState.GUIDED_DESIGN.value],stages=[Stage.IDEA_BRAINSTORMING.value]))
    run=RuleRun(session,message);run.select([rule],[])
    if field=='mode': session.interaction_state=InteractionState.EMVR_DIRECT
    else: session.current_stage_index=list(Stage).index(Stage.RESEARCH_QUESTION)
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    assert not run.applied
    assert session.model_context['dialogue_state']['pending_action']['candidate_answer']
    assert run.events[-1]['code']==field+'_mismatch'


@pytest.mark.parametrize('mode',list(InteractionState))
def test_repaired_question_is_synchronized_to_reply_history_and_stage_output(mode):
    session=fixture(mode);message='keep current and continue';run=RuleRun(session,message)
    run.select([packet()],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    output={'assistant_message':'','student_task':None,'stage_payload':{'pending_action':{'question':'obsolete'}}}
    session.history.append({'revision':1,'output':deepcopy(output)})
    session.stage_outputs[session.current_stage.value]={'revision':1,**deepcopy(output)}
    response={'revision':1,'handled_stage':session.current_stage.value,**deepcopy(output)}
    run.hook('before_reply',session,response=response)
    expected=response['stage_payload']['pending_action']
    assert expected['question']==response['student_task']
    assert session.history[-1]['output']['stage_payload']['pending_action']==expected
    assert session.stage_outputs[session.current_stage.value]['stage_payload']['pending_action']==expected
    assert session.history[0]==run.before.history[0]
