from copy import deepcopy
from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.experience import ExperienceStore, FeedbackService
from ece329_workflow.feedback import guidance
from ece329_workflow.model_routing import ModelRouter, RoutedGenerator
from ece329_workflow.models import DesignSession, Stage, InteractionState, SessionConflict, StepOutput
from ece329_workflow.openai_generator import OpenAIStageGenerator
from ece329_workflow.security import APISettings
from ece329_workflow.store import SQLiteSessionStore
from tests.test_feedback_pipeline import candidate
from tests.test_model_selection import make_engine, add_session, ModelTransport, QUESTION
from tests.test_security_and_store import call_api, workspace_temp_path, remove_sqlite_files


@pytest.fixture
def research():
    engine, transport = make_engine()
    session = add_session(engine)
    repo = ExperienceStore(project_id='course-a')
    service = FeedbackService(repo, SimpleNamespace(extract=lambda _: candidate()), background=False)
    api = WorkflowAPI(engine, APISettings(project_id='course-a', feedback_admin_token='maintainer', rate_limit_requests=500), feedback_service=service)
    yield SimpleNamespace(engine=engine, transport=transport, session=session, repo=repo, service=service, api=api,
                          auth={'Authorization':'Bearer model-owner'})
    repo.close()


def test_registry_policy_and_explicit_override_precedence():
    engine, _ = make_engine()
    router = ModelRouter(engine.generator, {})
    assert len(router.policy) == len(Stage) == 13
    assert router.resolve(Stage.THEORETICAL_FRAMEWORK.value, router.defaults())['profile'] == 'reasoning'
    config = router.validate({'strategy':'custom', 'profile':'balanced',
                              'stage_overrides': {Stage.THEORETICAL_FRAMEWORK.value:'fast'}})
    assert router.resolve(Stage.THEORETICAL_FRAMEWORK.value, config)['profile'] == 'fast'
    config['model_override'] = 'gpt-5.5'
    assert router.resolve(Stage.THEORETICAL_FRAMEWORK.value, config)['model'] == 'gpt-5.5'
    registry = deepcopy(router.registry); registry['fast'] = {'model':'gpt-5.6-luna','reasoning':'low'}
    custom = ModelRouter(engine.generator, {'ECE329_MODEL_REGISTRY': json.dumps(registry),
                                         'ECE329_STAGE_POLICY':json.dumps({Stage.THEORETICAL_FRAMEWORK.value:'fast'})})
    assert custom.resolve(Stage.THEORETICAL_FRAMEWORK.value, custom.defaults())['model'] == 'gpt-5.6-luna'


@pytest.mark.parametrize('config', [[], False, {'strategy':'bogus'}, {'experience_enabled':'false'},
    {'strategy':'custom','stage_overrides':{'stage_99':'fast'}}, {'strategy':'custom','stage_overrides':{Stage.HYPOTHESIS.value:'validator'}},
    {'model_override':'gpt-5.5'}, {'validator_enabled':False}])
def test_invalid_config_never_mutates_or_calls_model(research, config):
    before = research.engine.store.get(research.session.design_id)
    result = call_api(research.api, 'PATCH', f'/v1/designs/{before.design_id}/model-config', {'config':config,'version':0}, request_headers=research.auth)
    assert result[0].startswith('400')
    assert research.engine.store.get(before.design_id) == before
    assert not research.transport.requests


def test_config_api_is_owner_scoped_versioned_and_does_not_advance(research):
    path = f'/v1/designs/{research.session.design_id}/model-config'
    assert call_api(research.api,'GET',path)[0].startswith('401')
    assert call_api(research.api,'GET','/v1/config')[2]['local_validator'] == 'always_enabled'
    before = research.engine.store.get(research.session.design_id)
    body = {'config':{'strategy':'quality','experience_enabled':False},'version':0}
    status, _, result = call_api(research.api,'PATCH',path,body,request_headers=research.auth)
    assert status.startswith('200') and result['version'] == 1
    assert call_api(research.api,'PATCH',path,body,request_headers=research.auth)[0].startswith('409')
    after = research.engine.store.get(before.design_id)
    assert after.current_stage == before.current_stage and after.history == before.history and after.revision == before.revision
    response = research.engine.process_turn(before.design_id, {'message':QUESTION,'turn_id':'routing-profile-001'})
    assert response['selected_model'] == 'gpt-5.6-sol'
    assert all(p['model']=='gpt-5.6-sol' and p['reasoning']['effort']=='high' for p in research.transport.requests)


def test_each_stage_resolves_again_and_clears_only_remote_chain():
    class StageGenerator(OpenAIStageGenerator):
        def generate(self, session, message):
            self.transport.create({'model':self.model,'reasoning':{'effort':self.reasoning_effort}})
            session.model_context['openai_previous_response_id'] = 'response-' + self.model
            return StepOutput('test')
    requests=[]
    base=StageGenerator(transport=SimpleNamespace(create=lambda p: requests.append(p) or {}))
    router=ModelRouter(base,{})
    session=DesignSession('stages',InteractionState.EMVR_DIRECT)
    generator=RoutedGenerator(base,router,router.defaults(),session.current_stage.value)
    generator.generate(session,'first')
    session.current_stage_index=list(Stage).index(Stage.THEORETICAL_FRAMEWORK)
    generator.generate(session,'second')
    assert [p['model'] for p in requests] == ['gpt-5.4-mini','gpt-5.6-sol']
    assert session.model_context['selected_model']=='gpt-5.6-sol'


def test_real_usage_telemetry_no_duplicates_and_feedback_join(research):
    original = research.transport.create
    def measured(payload):
        return {**original(payload), 'usage':{'input_tokens':101,'output_tokens':23,'input_tokens_details':{'cached_tokens':7}}}
    research.transport.create = measured
    request={'message':QUESTION,'turn_id':'measured-turn-001','model_config':{'strategy':'fast'}}
    response=research.engine.process_turn(research.session.design_id,request)
    assert research.engine.process_turn(research.session.design_id,request)==response
    rows=research.engine.telemetry_records(research.session.design_id)
    assert len(rows)==1 and rows[0]['id']==response['telemetry_id']
    assert rows[0]['input_tokens']==101*len(rows[0]['calls'])
    assert rows[0]['output_tokens']==23*len(rows[0]['calls'])
    assert rows[0]['latency_ms']>=0 and all(s['validator_pass'] for s in rows[0]['stages'])
    assert QUESTION not in json.dumps(rows,ensure_ascii=False) and 'model-owner' not in json.dumps(rows)
    body={'message':'这一步需要更明确的操作说明','category':'other','request_id':'feedback-measured-001',
          'telemetry_id':response['telemetry_id'],'scope':'session','stage':response['handled_stage'],'revision':response['revision']}
    status,_,receipt=call_api(research.api,'POST',f'/v1/designs/{research.session.design_id}/feedback',body,request_headers=research.auth)
    assert status.startswith('201'),receipt
    assert research.engine.telemetry_records(research.session.design_id)[0]['user_feedback'][0]['id']==receipt['id']
    other=add_session(research.engine,design_id='another')
    with pytest.raises(ValueError,match='does not belong'):
        research.repo.submit(other,{**body,'revision':other.revision})
    assert call_api(research.api,'GET',f'/v1/designs/{other.design_id}/telemetry')[0].startswith('401')


def test_unreported_usage_is_unknown_and_switch_config_changes_fingerprint(research):
    request={'message':QUESTION,'turn_id':'config-frozen-001','model_config':{'strategy':'fast'}}
    research.engine.process_turn(research.session.design_id,request)
    assert research.engine.telemetry_records(research.session.design_id)[0]['input_tokens'] is None
    with pytest.raises(SessionConflict):
        research.engine.process_turn(research.session.design_id,{**request,'model_config':{'strategy':'quality'}})


def approve(repo, service, session, scope, request_id):
    ticket,_=repo.submit(session,{'message':'已经回答但重复询问','category':'answered_pending','request_id':request_id,'scope':scope})
    service.run_once()
    item=next(r for r in repo.experiences('candidate') if r['ticket_id']==ticket['id'])
    repo.review(item['id'],'approve',1,'已在同类型会话验证通过')
    return item['id']


def test_session_project_global_scope_and_soft_delete_audit(research):
    a=research.session
    session_id=approve(research.repo,research.service,a,'session','scope-session-001')
    project_id=approve(research.repo,research.service,a,'project','scope-project-001')
    global_id=approve(research.repo,research.service,a,'global','scope-global-001')
    def retrieve(design,project):
        return research.repo.retrieve(a.interaction_state.value,a.current_stage.value,'已经回答',design_id=design,project_id=project)
    assert len(retrieve(a.design_id,'course-a'))==3
    assert {r['scope'] for r in retrieve('other','course-a')}=={'project','global'}
    assert {r['scope'] for r in retrieve('other','course-b')}=={'global'}
    research.repo.review(global_id,'delete',2,'确认错误经验，应停止引用')
    assert retrieve('other','course-b')==[]
    deleted=research.repo.experiences('deleted')[0]
    assert deleted['reviews'][-1]['decision']=='delete'


def test_experience_off_removes_prompt_advice_without_disabling_validation(research):
    approve(research.repo,research.service,research.session,'global','experience-off-001')
    session=research.engine.store.get(research.session.design_id)
    session.model_context['model_config']={'experience_enabled':False}
    research.engine._attach_experiences(session,'已经回答')
    assert session.turn_context['experience_rules']==[]
    assert guidance(session,'已经回答')['rules']==[]
    assert research.engine.model_configuration()['local_validator']=='always_enabled'


def test_scoped_experiences_and_telemetry_survive_restart():
    path=workspace_temp_path('.sqlite')
    try:
        engine,_=make_engine(store=SQLiteSessionStore(path)); session=add_session(engine)
        repo=ExperienceStore(path,project_id='persistent')
        engine.experience_store=repo
        engine.process_turn(session.design_id,{'message':QUESTION,'model_config':{'strategy':'quality'}})
        repo.close()
        restarted,_=make_engine(store=SQLiteSessionStore(path)); repo=ExperienceStore(path,project_id='persistent')
        restarted.experience_store=repo
        assert restarted.model_configuration(session.design_id)['config']['strategy']=='quality'
        assert len(restarted.telemetry_records(session.design_id))==1
        restarted.store.delete(session.design_id)
        assert repo.telemetry(session.design_id)==[]
        repo.close()
    finally:
        remove_sqlite_files(path)


def test_adaptive_validation_retry_is_bounded_and_never_overrides_explicit_model():
    from ece329_workflow.openai_generator import ModelOutputError
    calls=[]
    class Rejecting(OpenAIStageGenerator):
        def generate(self, session, message):
            calls.append(self.model)
            raise ModelOutputError('fixture rejection')
    base=Rejecting(transport=SimpleNamespace(create=lambda _: {}))
    router=ModelRouter(base,{})
    config=router.validate({'strategy':'fast','adaptive_enabled':True})
    session=DesignSession('adaptive',InteractionState.EMVR_DIRECT)
    runner=RoutedGenerator(base,router,config,session.current_stage.value)
    with pytest.raises(ModelOutputError):runner.generate(session,'try')
    assert calls==['gpt-5.4-mini','gpt-5.6-sol']
    calls.clear()
    config=router.validate({'strategy':'custom','model_override':'gpt-5.5','adaptive_enabled':True})
    with pytest.raises(ModelOutputError):RoutedGenerator(base,router,config,session.current_stage.value).generate(session,'try')
    assert calls==['gpt-5.5']
    router.failure_counts[session.current_stage.value]=2
    assert router.resolve(session.current_stage.value,router.validate({'strategy':'fast','adaptive_enabled':True}))['profile']=='balanced'
    assert router.resolve(session.current_stage.value,router.validate({'strategy':'fast'}))['profile']=='fast'


def test_research_summary_preserves_unknown_usage_and_group_boundaries():
    from tools.export_workflow_telemetry import summarize
    records=[{'mode':'EMVR_DIRECT','initial_stage':'HYPOTHESIS','model_config':{'strategy':'fast','experience_enabled':enabled},
              'latency_ms':20,'status':'completed','input_tokens':None,'output_tokens':None,
              'stages':[{'validator_pass':True}]} for enabled in (True,False)]
    groups=summarize(records)
    assert len(groups)==2 and all(row['unknown_usage_turns']==1 for row in groups)
    assert all(row['validator_pass_rate']==1 for row in groups)


def test_failed_model_calls_record_sanitized_diagnostics(research):
    from ece329_workflow.openai_generator import ModelHTTPError
    def fail(_): raise ModelHTTPError(503,'secret-provider-body')
    research.transport.create=fail
    with pytest.raises(ModelHTTPError):
        research.engine.process_turn(research.session.design_id,{'message':QUESTION,'turn_id':'failed-metrics-001'})
    row=research.engine.telemetry_records(research.session.design_id)[0]
    assert row['status']=='failed' and row['error_type']=='ModelHTTPError'
    assert row['calls'] and all(c['error_type']=='ModelHTTPError' for c in row['calls'])
    assert all(s['validator_pass'] is None for s in row['stages'])
    assert 'secret-provider-body' not in json.dumps(row)


def test_disabling_experience_resets_remote_context_only_once(research):
    session=research.engine.store.get(research.session.design_id)
    session.model_context['model_config']={'experience_enabled':False}
    research.engine._attach_experiences(session,QUESTION)
    assert 'openai_previous_response_id' not in session.model_context
    session.model_context['openai_previous_response_id']='new-chain-without-experience'
    research.engine._attach_experiences(session,QUESTION)
    assert session.model_context['openai_previous_response_id']=='new-chain-without-experience'


def test_feedback_on_old_answer_contains_server_matched_target_not_only_recent_turns(research):
    session=research.session
    session.revision=8
    session.history=[{'revision': n, 'handled_stage':session.current_stage.value,
                      'user_message':f'user-{n}', 'output':{'assistant_message':f'answer-{n}'}} for n in range(1,9)]
    ticket,_=research.repo.submit(session, {'message':'早先回答需要改进','category':'other','request_id':'historic-target-001',
                                           'revision':2,'stage':session.current_stage.value,'scope':'session'})
    research.service.run_once()
    evidence=next(r for r in research.repo.experiences('candidate') if r['ticket_id']==ticket['id'])['evidence']['evidence']
    assert evidence['reported_turn']['assistant']=='answer-2'
    assert all(r['revision']>=5 for r in evidence['recent_turns'])
    from ece329_workflow.experience import evidence_snapshot
    assert evidence_snapshot(session,2,Stage.HYPOTHESIS.value)['reported_turn'] is None


def test_measurements_include_only_rules_present_in_actual_payload(research):
    from ece329_workflow.telemetry import CURRENT_TRACE, TurnTrace, ObservedTransport
    from ece329_workflow.models import TurnRequest
    session=research.session
    session.turn_context['experience_rules']=[{'id':'EXP-INCLUDED'},{'id':'EXP-EXCLUDED'}]
    trace=TurnTrace(session,TurnRequest(message='test'))
    token=CURRENT_TRACE.set(trace)
    try:
        transport=ObservedTransport(SimpleNamespace(create=lambda _: {}),session,{'profile':'fast'})
        transport.create({'model':'test','input':'Rule EXP-INCLUDED'})
    finally:
        CURRENT_TRACE.reset(token)
    assert trace.data['calls'][0]['experience_rule_ids']==['EXP-INCLUDED']


def test_review_retains_scope_and_rule_changes_for_each_version(research):
    ticket,_=research.repo.submit(research.session,{'message':'步骤需要改进','category':'other',
        'request_id':'review-version-001','scope':'session'})
    research.service.run_once()
    item=next(r for r in research.repo.experiences('candidate') if r['ticket_id']==ticket['id'])
    edited={**item['content'],'recommendation':'在推进前先回应当前尚未完成的请求。'}
    research.repo.review(item['id'],'approve',1,'已验证适用于当前课程项目',content=edited,scope='project')
    audit=research.repo.experiences('active')[0]['reviews'][0]['content']
    assert audit['previous']=={'scope':'session','rule':item['content']}
    assert audit['current']=={'scope':'project','rule':edited}
