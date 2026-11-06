from copy import deepcopy
import json
import os
from types import SimpleNamespace
import pytest

from ece329_workflow.experience_harness import generate, evidence_for, _parse
from ece329_workflow.harness_runtime import Runtime
from ece329_workflow.models import InteractionState, SessionConflict
from tests.test_feedback_pipeline import pipeline, extract_one, candidate, review_note
from tests.test_security_and_store import call_api


def response(content=None, **extra):
    value=dict(candidate_json=json.dumps(content or candidate()), observations=[
        {'evidence_ref':'user_feedback','description':'The user reports repeated questioning.'}],
        hypotheses=['The cause is not established.'], unknowns=['Historical state is incomplete.'],
        unsupported_actions=[])
    value.update(extra)
    return json.dumps(value)


def setup(p):
    item=extract_one(p)
    p.service.extractor=SimpleNamespace(models=lambda:['deepseek-flash'], generator=SimpleNamespace(transport=object()))
    body={'version':item['version'],'request_id':'harness-test-request-0001','confirm_harness':True,
          'content':item['content'],'scope':'global','note':review_note()}
    return item,body


def fake(outputs):
    class Fake:
        requests=0
        def __init__(self,*args): pass
        def run(self,prompt):
            Fake.requests+=1
            value=outputs[min(Fake.requests-1,len(outputs)-1)]
            if callable(value): return value()
            return value
    return Fake


@pytest.mark.parametrize('mode',list(InteractionState))
def test_draft_is_isolated_versioned_and_idempotent(pipeline,mode):
    p=pipeline;p.session.interaction_state=mode;p.engine.store.save(p.session)
    item,body=setup(p);before=deepcopy(p.engine.store.get(p.session.design_id).design_context)
    runner=fake([response()]);result=generate(p.service,item['id'],body,runner)
    assert result['status']=='completed'
    assert result['draft']['status']=='draft_not_approved'
    assert result['replay']['status']=='not_applicable'
    assert result['semantic_status']==result['full_flow_status']=='not_tested'
    assert p.repo.experiences()[0]['status']=='candidate'
    assert p.repo.experiences()[0]['version']==1
    assert p.engine.store.get(p.session.design_id).design_context==before
    assert generate(p.service,item['id'],body,runner)==result
    assert runner.requests==1
    assert p.repo.experiences()[0]['harness_runs'][0]['run_id']==result['run_id']
    with pytest.raises(SessionConflict): generate(p.service,item['id'],dict(body,scope='session'),runner)


def test_schema_repair_and_loop_bound(pipeline):
    p=pipeline;item,body=setup(p);runner=fake(['{}','{}'])
    result=generate(p.service,item['id'],body,runner)
    assert result['status']=='failed' and runner.requests==2
    assert p.repo.experiences()[0]['content']==item['content']
    assert generate(p.service,item['id'],body,runner)==result
    assert runner.requests==2


def test_backend_bug_does_not_retry_model_as_validation_failure(pipeline, monkeypatch):
    item, body = setup(pipeline)
    runner = fake([response()])
    def broken(*args): raise KeyError('internal state missing')
    monkeypatch.setattr('ece329_workflow.experience_harness._replay', broken)
    result = generate(pipeline.service, item['id'], body, runner)
    assert result['status'] == 'failed' and result['code'] == 'harness_backend_error'
    assert runner.requests == 1


def test_invalid_reference_is_repaired_only_once(pipeline):
    p=pipeline;item,body=setup(p)
    runner=fake([response(observations=[{'evidence_ref':'invented','description':'Fake'}]),response()])
    result=generate(p.service,item['id'],body,runner)
    assert result['status']=='completed' and runner.requests==2
    assert result['steps'][2]['step']=='validate_draft'


def test_per_version_limit_and_concurrent_version_change(pipeline):
    p=pipeline;item,body=setup(p);runner=fake([response()])
    for index in range(3):
        assert generate(p.service,item['id'],dict(body,request_id=f'harness-request-{index:04d}'),runner)['status']=='completed'
    with pytest.raises(ValueError,match='harness_attempt_limit'):
        generate(p.service,item['id'],dict(body,request_id='harness-request-0004'),runner)
    assert runner.requests==3


def test_concurrent_approval_cannot_be_overwritten(pipeline):
    p=pipeline;item,body=setup(p)
    def change():
        p.repo.review(item['id'],'stop',1,review_note())
        return response()
    result=generate(p.service,item['id'],body,fake([change]))
    assert result['code']=='harness_version_changed'
    assert p.repo.experiences()[0]['status']=='stopped'
    assert 'draft' not in result


def test_evidence_redacts_secrets_and_separates_submission(pipeline):
    item,body=setup(pipeline)
    item['evidence']['message']='password=secret123 bearer abc sk-12345'
    item['evidence']['evidence']['event_chain']=[{'state_before':{'api_key':'sensitive'},'assistant':''}]
    view=evidence_for(item);text=json.dumps(view)
    assert 'secret123' not in text and 'sensitive' not in text and 'sk-12345' not in text
    assert 'submission_state' in view['records']
    assert view['records']['event_chain:0']['assistant']==''


def test_api_requires_admin_and_explicit_trigger(pipeline):
    p=pipeline;item,body=setup(p);path=f"/v1/feedback/experiences/{item['id']}/harness"
    assert call_api(p.api,'POST',path,body)[0].startswith('401')
    status,_,_=call_api(p.api,'POST',path,dict(body,confirm_harness=False),request_headers=p.admin)
    assert status.startswith('400')


def test_model_bridge_rejects_tools_and_request_budget():
    from ece329_workflow.openai_generator import OpenAIResponsesHTTPTransport
    class Transport:
        def create(self,payload): return {'output_text':'{}','usage':{'input_tokens':10,'output_tokens':2}}
    calls=[];bridge=Runtime(Transport(),'deepseek-flash',{},calls)
    with pytest.raises(ValueError,match='tools_forbidden'):
        bridge.complete({'messages':[{'role':'user','content':'test'}],'tools':[{}]})
    for _ in range(2): assert bridge.complete({'messages':[{'role':'user','content':'test'}]})=='{}'
    with pytest.raises(ValueError,match='budget_exceeded'):
        bridge.complete({'messages':[{'role':'user','content':'test'}]})
    assert len(calls)==2 and calls[0]['output_tokens']==2


def test_executable_replay_is_not_model_verification(pipeline):
    from ece329_workflow.experience_actions import configurable_contract
    p=pipeline;item,body=setup(p)
    value=candidate(execution=configurable_contract(),stages=['IDEA_BRAINSTORMING'])
    runner=fake([response(value)])
    result=generate(p.service,item['id'],body,runner)
    assert result['status']=='completed'
    assert result['replay']['kind']=='isolated_simulated_replay'
    assert result['replay']['coverage']['positive_executed']>0
    assert result['replay']['layers']['semantics']=='not_tested'
    assert 'validation_id' not in result['draft']


def test_expired_run_is_not_restarted(pipeline):
    p=pipeline;item,body=setup(p);runner=fake([response()])
    first=generate(p.service,item['id'],body,runner)
    with p.repo.connection() as db:
        db.execute("UPDATE experience_harness_runs SET record=json_set(record,'$.status','running'),lease_until=1 WHERE id=?",(first['run_id'],))
    assert p.repo.experiences()[0]['harness_runs'][0]['code']=='harness_interrupted'
    assert generate(p.service,item['id'],body,runner)['code']=='harness_interrupted'
    assert runner.requests==1


def test_factory_failure_is_recorded_without_stuck_lease(pipeline):
    p=pipeline;item,body=setup(p)
    def broken(*_): raise RuntimeError('secret runtime error')
    result=generate(p.service,item['id'],body,broken)
    assert result['status']=='failed' and 'secret runtime error' not in json.dumps(result)
    with p.repo.connection() as db:
        assert db.execute('SELECT lease_until FROM experience_harness_runs').fetchone()[0]==0


@pytest.mark.parametrize('model',['deepseek-flash','gpt-5.4-mini'])
def test_bridge_uses_provider_specific_parameters(model):
    captured=[]
    class Transport:
        def create(self,payload): captured.append(payload);return {'output_text':'{}'}
    r=Runtime(Transport(),model,{},[])
    r.complete({'messages':[{'role':'user','content':'test'}]})
    assert ('_workflow_output_cap' in captured[0])==(model=='deepseek-flash')


@pytest.mark.skipif(os.getenv('ECE329_TEST_HARNESS_RUNTIME')!='1',reason='Explicit local runtime test; no paid API calls')
@pytest.mark.parametrize('mode',list(InteractionState))
def test_real_sdk_with_fake_provider_end_to_end(pipeline,mode):
    p=pipeline;p.session.interaction_state=mode;p.engine.store.save(p.session)
    item,body=setup(p)
    class Provider:
        def create(self,payload):
            assert payload['text']['format']['name']=='feedback_harness_revision'
            return {'output_text':response(), 'usage':{'input_tokens':101,'output_tokens':202}}
    p.service.extractor.generator.transport=Provider()
    result=generate(p.service,item['id'],body)
    assert result['status']=='completed',result
    assert result['usage']['input_tokens']==101
    assert result['usage']['output_tokens']==202
    assert result['draft']['source']['kind']=='harness_revision'
