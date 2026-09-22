from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from ece329_workflow.experience_rules import contract, RuleRun, execution_identity
from ece329_workflow.experience_actions import configurable_contract, validate_configurable, effective_conditions
from ece329_workflow.experience_replay import coverage, compare, fixture, semantic
from ece329_workflow.experience_evaluation import evaluate
from ece329_workflow.experience_admin import configure_draft, execution_records
from ece329_workflow.models import InteractionState
from tests.test_executable_experience import packet
from tests.test_feedback_pipeline import pipeline, extract_one, candidate, review_note


def test_empty_or_negative_only_coverage_is_never_sufficient():
    content=candidate()
    assert not coverage(content,[])['sufficient']
    cases=[{'mode':m,'initial_stage':s,'expected_applicable':False,'actually_executed':False,'passed':True}
           for m in content['modes'] for s in content['stages'] for _ in range(8)]
    result=coverage(content,cases)
    assert not result['sufficient'] and result['positive_executed']==0
    assert len(result['uncovered'])==26


def test_editorial_examples_do_not_conflict_and_all_sources_are_retained():
    session=fixture(InteractionState.GUIDED_DESIGN);message='keep the saved design and continue'
    one=packet();two=deepcopy(one);two['id']='EXP-'+'b'*32
    two['execution']['examples'][0]['input']='另一段正例'
    assert execution_identity(one['execution'])==execution_identity(two['execution'])
    run=RuleRun(session,message);run.select([one,two],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    run.hook('before_reply',session,response={'assistant_message':'重新把所有旧修改逐项对应到栏目。','revision':1})
    assert not run.blocked and len(run.sources)==2
    assert run.events[-1]['sources']==run.sources
    assert sum(e['code']=='candidate_declined' for e in run.events)==1
    equivalent=configurable_contract()
    equivalent['actions']=[a for a in equivalent['actions'] if a['name']!='ask_missing_fields']
    assert execution_identity(contract())==execution_identity(equivalent)


@pytest.mark.parametrize('mode',list(InteractionState))
def test_configured_reply_replaces_unverifiable_old_demands_and_changes_behavior(mode):
    execution=configurable_contract();execution['actions'][-1]['parameters']['style']='task_only'
    session=fixture(mode);message='do not modify anything, go ahead'
    result=compare(session,message,semantic(message,'positive'),packet(candidate(execution=execution)),True)
    assert result['passed'] and result['changed']
    assert result['with_rule']['reply']==result['with_rule']['state']['pending']['question']
    assert '栏目对应关系' not in result['with_rule']['reply']


def test_structured_conditions_are_enforced_and_unsupported_fields_rejected():
    value=configurable_contract();value['conditions']['min_repeat_count']=4
    session=fixture(InteractionState.EMVR_DIRECT);run=RuleRun(session,'keep current and continue')
    run.select([packet(candidate(execution=value))],[])
    run.hook('after_intent',session,semantic('keep current and continue','positive'))
    assert not run.applied and run.events[-1]['code']=='condition_not_matched'
    value['conditions']['only_if_secret']=True
    with pytest.raises(ValueError,match='unsupported_rule_condition'):validate_configurable(value)


def test_configure_draft_preserves_custom_examples_and_only_saves_a_draft(pipeline):
    p=pipeline;item=extract_one(p);value=candidate(execution=contract())
    result=configure_draft(p.repo,item['id'],{'version':1,'note':review_note(),'content':value,
        'conditions':{'pending_types':['CONFIRM_STAGE_OR_MODIFY'],'min_repeat_count':2},'reply_style':'task_only'})
    assert result['content']['execution']['version']==2
    assert result['content']['execution']['examples']==value['execution']['examples']
    assert p.repo.experiences()[0]['version']==1
    assert effective_conditions(result['content'])['additional_checks']['min_repeat_count']==2


class EvalTransport:
    def __init__(self,fail=False):self.calls=[];self.fail=fail
    def create(self,payload):
        self.calls.append(payload)
        if self.fail:raise TimeoutError('secret-key-must-not-be-stored')
        inputs=json.loads(payload['input'][0]['content'][0]['text'])
        rows=[]
        for row in inputs:
            # A deterministic mock, not a claim of live semantic quality.
            positive=row['message'] in ['不要修改，保留当前设计并继续','do not modify anything, go ahead']
            rows.append({'id':row['id'],'source_text':row['message'],'confidence':0.99,
                'keep_current':positive,'advance':positive,'accept_candidate':False,'new_edit':False,'ambiguous':not positive})
        return {'output_text':json.dumps({'cases':rows}),'usage':{'input_tokens':80,'output_tokens':60}}


def test_explicit_evaluation_includes_custom_examples_is_metered_and_never_retries(pipeline):
    p=pipeline;item=extract_one(p);transport=EvalTransport()
    p.service.extractor=SimpleNamespace(generator=SimpleNamespace(transport=transport),models=lambda:['deepseek-flash'])
    value=candidate(execution=configurable_contract())
    value['execution']['examples'].append({'input':'custom unclear request','applies':False})
    body={'version':1,'content':value,'confirm_model_evaluation':True}
    with pytest.raises(ValueError,match='explicit_model'):evaluate(p.service,item['id'],{**body,'confirm_model_evaluation':False})
    assert not transport.calls
    result=evaluate(p.service,item['id'],body)
    assert len(transport.calls)==1 and result['report']['semantic_status']=='passed'
    assert result['report']['full_flow_status']=='unavailable'
    assert result['report']['usage']['total_tokens']==140
    assert sum(c['input']=='custom unclear request' for c in result['report']['cases'])==2
    assert 'expected' not in transport.calls[0]['input'][0]['content'][0]['text']
    transport.fail=True
    for _ in range(2):
        failed=evaluate(p.service,item['id'],body)
        assert failed['report']['status']=='failed'
        assert 'secret-key' not in json.dumps(failed)
    with pytest.raises(ValueError,match='evaluation_attempt_limit'):evaluate(p.service,item['id'],body)
    assert len(transport.calls)==3
    with p.repo.connection() as db:
        assert db.execute('SELECT attempts FROM feedback_tickets WHERE id=?',(item['ticket_id'],)).fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM experience_evaluations').fetchone()[0]==3
        db.execute('UPDATE feedback_tickets SET attempts=2 WHERE id=?',(item['ticket_id'],))
        assert p.repo._ticket_usage(db,item['ticket_id'])['complete'] is False


def test_statistics_never_count_retrieval_as_verified_execution(pipeline):
    p=pipeline;item=extract_one(p)
    with p.repo.connection() as db:
        for i,code in enumerate(['selected','execution_verified','execution_failed','condition_not_matched']):
            record={'experience_execution':[{'rule_id':'EXP-'+item['id'],'version':1,'mode':'EMVR_DIRECT','code':code}]}
            db.execute('INSERT INTO workflow_telemetry VALUES(?,?,?,?)',(str(i),'d',i,json.dumps(record)))
    stats=execution_records(p.repo)['statistics']['rules'][0]
    assert stats['verified_rate']==0.5
    assert stats['counts']['selected']==1


def test_conflicting_rules_preserve_explicit_user_intent_in_the_real_engine(pipeline):
    from ece329_workflow.generator import RuleBasedStageGenerator
    from ece329_workflow.engine import WorkflowEngine
    class Generator(RuleBasedStageGenerator):
        def resolve_intent(self,s,message,*args):return semantic(message,'positive')
    p=pipeline;one=packet(candidate(execution=configurable_contract()));two=deepcopy(one)
    two['id']='EXP-'+'b'*32;two['execution']['actions'][-1]['parameters']['style']='task_only'
    p.repo.select_rules=lambda *args:([one,two],[])
    engine=WorkflowEngine(generator=Generator());engine.experience_store=p.repo
    s=fixture(InteractionState.GUIDED_DESIGN);engine.store.save(s)
    result=engine.process_turn(s.design_id,{'message':'keep saved design and continue'})
    saved=engine.store.get(s.design_id)
    assert not saved.model_context['dialogue_state']['pending_action'].get('candidate_answer')
    assert '栏目对应关系' not in result['assistant_message']
    events=engine.telemetry_records(s.design_id)[0]['experience_execution']
    assert any(e['code']=='conflict_blocked' for e in events)
    assert any(e['code']=='base_workflow_preserved' and e['intent']=='ADVANCE_STAGE' for e in events)
    assert not any(e['code']=='candidate_declined' for e in events)


def test_approval_rejects_missing_coverage_unmapped_conditions_and_missing_ack(pipeline):
    from ece329_workflow.experience import fingerprint
    from ece329_workflow.experience_replay import VERIFIER_VERSION
    from tests.test_feedback_pipeline import review
    p=pipeline;item=extract_one(p);value=candidate(execution=configurable_contract())
    with p.repo.connection() as db:
        db.execute('INSERT INTO experience_validations VALUES(?,?,?,?,?,?)',('no-coverage',item['id'],1,
          fingerprint({'content':value,'scope':'global'}),json.dumps({'status':'passed','verifier_version':VERIFIER_VERSION,'coverage':{'sufficient':False}}),0))
    assert review(p,item,content=value,validation_id='no-coverage')[0].startswith('400')
    assert review(p,item,content=value,confirmed_conditions=None)[0].startswith('400')
    value['execution']['unmapped_conditions']=['只在尚未支持的特殊条件下执行']
    response=review(p,item,content=value)
    assert response[0].startswith('400')
    assert p.repo.experiences()[0]['version']==1


def test_configurable_condition_negative_cases_are_replayed():
    from ece329_workflow.experience_replay import verify
    from ece329_workflow.models import Stage
    execution=configurable_contract();execution['conditions']={'min_repeat_count':4,'pending_types':['CONFIRM_STAGE_OR_MODIFY']}
    result=verify(candidate(execution=execution,modes=['EMVR_DIRECT'],stages=[Stage.IDEA_BRAINSTORMING.value]),'a'*32,1)
    assert result['status']=='passed'
    negatives=[c for c in result['cases'] if c.get('scenario')=='condition_negative']
    assert len(negatives)==2 and all(c['passed'] and not c['actually_executed'] for c in negatives)


def test_partial_structured_configuration_preserves_existing_restrictions(pipeline):
    p=pipeline;item=extract_one(p);execution=configurable_contract()
    execution['conditions']={'min_repeat_count':4,'pending_types':['CONFIRM_STAGE_OR_MODIFY','ANSWER_STAGE_QUESTION']}
    execution['unmapped_conditions']=['尚未实现的附加限制']
    execution['actions']=[a for a in execution['actions'] if a['name']!='ask_missing_fields']
    result=configure_draft(p.repo,item['id'],{'version':1,'note':review_note(),
        'content':candidate(execution=execution),'reply_style':'task_only'})
    actual=result['content']['execution']
    assert actual['conditions']==execution['conditions']
    assert actual['unmapped_conditions']==execution['unmapped_conditions']
    assert any(a['name']=='ask_missing_fields' for a in actual['actions'])  # Explicit in new drafts; required in legacy execution too.
    assert actual['actions'][-1]['parameters']=={'style':'task_only'}


def test_coalesced_rule_statistics_attribute_verification_to_every_source(pipeline):
    p=pipeline;item=extract_one(p)
    sources=[{'id':'EXP-'+item['id'],'version':1},{'id':'EXP-'+'b'*32,'version':3}]
    events=[{'rule_id':sources[0]['id'],'version':1,'mode':'GUIDED_DESIGN',
             'step':'verification','code':'execution_verified','sources':sources}]*2
    with p.repo.connection() as db:
        db.execute('INSERT INTO workflow_telemetry VALUES(?,?,?,?)',('merged','d',1,json.dumps({'experience_execution':events})))
    stats=execution_records(p.repo)['statistics']['rules']
    assert len(stats)==2
    assert all(s['counts']['execution_verified']==1 and s['verified_rate']==1 for s in stats)


@pytest.mark.parametrize('mode',list(InteractionState))
def test_model_evaluation_cannot_hide_flow_errors_or_mislabel_boundary_cases(pipeline,monkeypatch,mode):
    from ece329_workflow.experience_rules import capture_replay
    p=pipeline;item=extract_one(p);transport=EvalTransport()
    p.service.extractor=SimpleNamespace(generator=SimpleNamespace(transport=transport),models=lambda:['deepseek-flash'])
    with p.repo.connection() as db:
        payload=json.loads(db.execute('SELECT payload FROM feedback_tickets WHERE id=?',(item['ticket_id'],)).fetchone()[0])
        payload['evidence']['replay']={'state_before':capture_replay(fixture(mode)),'message':'maybe'}
        db.execute('UPDATE feedback_tickets SET payload=? WHERE id=?',(json.dumps(payload),item['ticket_id']))
    def failed_flow(s,message,intent,rule,expected,**kwargs):
        return {'passed':False,'expected_applicable':expected,'error':'simulated_backend_failure'}
    monkeypatch.setattr('ece329_workflow.experience_evaluation.compare',failed_flow)
    result=evaluate(p.service,item['id'],{'version':1,'content':candidate(execution=configurable_contract(),modes=[mode.value]),'confirm_model_evaluation':True})
    report=result['report']
    assert report['semantic_status']=='passed'
    assert report['controlled_flow_status']=='failed'
    assert report['full_flow_status']=='failed'  # A broken replay is not merely "not applicable".
    assert sum(c['origin']=='boundary' for c in report['cases'])==8
