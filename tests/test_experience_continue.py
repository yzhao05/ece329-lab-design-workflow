"""Approved rules reach paraphrased turns; explicit no-edit controls do not replay stale edits."""
import json
import pytest
from ece329_workflow.dialogue_acts import keeps_current_design, apply_stage_field_updates
from ece329_workflow.dialogue_state import UserIntent, resolved_intent, current_pending_action, serialize_intent_input
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.openai_generator import OpenAIStageGenerator
from tests.test_openai_generator import FakeTransport
from tests.test_feedback_pipeline import pipeline, extract_one, review, candidate

MESSAGE = "do not modify anything, go ahead"

def controls(message=MESSAGE):
    return [{'type':'CONTROL','target':target,'operation':'MERGE','content':None,
             'confidence':0.99,'source_text':message,'source_start':0,'source_end':len(message)}
            for target in ['KEEP_CURRENT','ADVANCE']]

def pending(mode):
    return {'action_id':'previous-confirmation','type':'CONFIRM_STAGE_OR_MODIFY',
            'interaction_state':mode.value,'stage':'IDEA_BRAINSTORMING','subject':'IDEA_BRAINSTORMING',
            'question':'核对当前实验边界，准确时继续。','proposal':{'stage':'IDEA_BRAINSTORMING'},
            'allowed_intents':[i.value for i in UserIntent],'status':'PENDING',
            'candidate_answer':'旧的未执行修订，不得写入当前实验对象或其他字段',
            'candidate_binding_authorized':False,'candidate_resolution':'MODIFY_PREVIOUS_PROPOSAL',
            'candidate_turns':['旧的未执行修订，不得写入当前实验对象或其他字段'],'repeat_count':4}

@pytest.mark.parametrize('mode', list(InteractionState))
def test_approved_rule_reaches_english_turn_without_keyword_match(pipeline, mode):
    p=pipeline;item=extract_one(p)
    assert review(p,item)[0].startswith('200')
    session=DesignSession('paraphrase-rule',mode)
    session.model_context['dialogue_state']={'pending_action':pending(mode)}
    p.engine._attach_experiences(session,MESSAGE)
    rules=session.turn_context['experience_rules']
    assert len(rules)==1 and rules[0]['match_basis']=='workflow_state'
    assert 'EXP-'+item['id'] in serialize_intent_input(session,MESSAGE,None,{})
    session.model_context['model_config']={'experience_enabled':False}
    p.engine._attach_experiences(session,MESSAGE)
    assert session.turn_context['experience_rules']==[]
    session.model_context['model_config']={'experience_enabled':True}
    active=p.repo.experiences('active')[0]
    assert review(p,active,'stop')[0].startswith('200')
    p.engine._attach_experiences(session,MESSAGE)
    assert session.turn_context['experience_rules']==[]

@pytest.mark.parametrize('mode',list(InteractionState))
@pytest.mark.parametrize('message',[MESSAGE,'不再执行前面的修改，保留当前设计继续。'])
def test_resolver_preserves_current_request_instead_of_reparsing_old_candidate(mode,message):
    acts=controls(message)
    transport=FakeTransport(output={'intent':'ADVANCE_STAGE','target':'IDEA_BRAINSTORMING',
        'resolved_value_json':None,'semantic_updates_json':None,
        'dialogue_acts_json':json.dumps(acts),'confidence':0.99})
    generator=OpenAIStageGenerator(transport=transport)
    session=DesignSession('keep-current-model',mode)
    result=generator.resolve_intent(session,message,pending(mode),{})
    assert result['intent']=='ADVANCE_STAGE' and result['source']=='SEMANTIC_KEEP_CURRENT'
    assert len(transport.requests)==1
    assert result['semantic_updates']['control_actions']==['KEEP_CURRENT','ADVANCE']

@pytest.mark.parametrize('mode',list(InteractionState))
def test_compact_recovery_can_keep_current_without_reparsing_stale_candidate(mode):
    rich={'intent':'UNCLEAR','target':None,'resolved_value_json':None,
          'semantic_updates_json':None,'confidence':0.5,
          'dialogue_acts_json':json.dumps([{'type':'UNRESOLVED','target':'',
              'content':MESSAGE,'source_text':MESSAGE,'confidence':0.5}])}
    acts=controls()
    for act,source in zip(acts,['do not modify anything','go ahead']):
        act.update(source_text=source,source_start=MESSAGE.index(source),
                   source_end=MESSAGE.index(source)+len(source))
    transport=FakeTransport(outputs=[rich,{'actions':acts}])
    result=OpenAIStageGenerator(transport=transport).resolve_intent(
        DesignSession('compact-keep-current',mode),MESSAGE,pending(mode),{})
    assert result['source']=='SEMANTIC_KEEP_CURRENT'
    assert len(transport.requests)==2
    assert 'KEEP_CURRENT' in transport.requests[1]['instructions']

class KeepGenerator(RuleBasedStageGenerator):
    def resolve_intent(self,session,message,pending_action,carried_context):
        return resolved_intent(UserIntent.ADVANCE_STAGE,dialogue_acts=controls(message),
            actions_authoritative=True,source='SEMANTIC_KEEP_CURRENT')

@pytest.mark.parametrize('mode',list(InteractionState))
def test_keep_current_clears_only_uncommitted_candidate_and_asks_next_needed_question(mode):
    session=DesignSession('keep-current-workflow',mode)
    session.design_context={'topic':'平面 TEM 波', 'student_confirmed_note':'介质块和移动探针'}
    session.history=[{'revision':1, 'user_message':'旧的未执行修订，不得写入当前实验对象或其他字段'}]
    session.model_context['dialogue_state']={'pending_action':pending(mode)}
    engine=WorkflowEngine(generator=KeepGenerator())
    engine.store.save(session)
    output=engine.process_turn(session.design_id,{'message':MESSAGE})
    stored=engine.store.get(session.design_id)
    assert '请把各栏目与最终内容对应写出' not in output['assistant_message']
    assert '请直接重试这项修改' not in output['assistant_message']
    assert '旧的未执行修订' not in json.dumps(stored.design_context,ensure_ascii=False)
    assert stored.design_context['topic']=='平面 TEM 波'
    assert stored.design_context['student_confirmed_note']=='介质块和移动探针'
    assert stored.history[0]['user_message']=='旧的未执行修订，不得写入当前实验对象或其他字段'
    assert not (current_pending_action(stored) or {}).get('candidate_answer')
    assert stored.current_stage==Stage.IDEA_BRAINSTORMING  # Missing design data is not skipped.
    assert output['assistant_message'].strip()

def test_complete_guided_stage_advances_after_declining_uncommitted_candidate():
    stage=Stage.VARIABLES_AND_CONDITIONS
    mode=InteractionState.GUIDED_DESIGN
    session=DesignSession('complete-keep-current',mode,current_stage_index=list(Stage).index(stage))
    values={'independent_variable':'距离','observations':'场线和通量','controlled_conditions':'电荷量和观察方式'}
    apply_stage_field_updates(session,[{'field':k,'operation':'REPLACE','value':v}
                                      for k,v in values.items()],stage=stage)
    action=pending(mode)
    action.update(stage=stage.value,subject=stage.value,proposal={'stage':stage.value})
    session.model_context['dialogue_state']={'pending_action':action}
    session.stage_outputs[stage.value]={'assistant_message':'确认后继续','stage_payload':values}
    engine=WorkflowEngine(generator=KeepGenerator())
    engine.store.save(session)
    output=engine.process_turn(session.design_id,{'message':MESSAGE})
    assert output['current_stage']==Stage.CONCEPTUAL_PROCEDURE.value
    assert not (current_pending_action(engine.store.get(session.design_id)) or {}).get('candidate_answer')

@pytest.mark.parametrize('scope',['session','project','global'])
def test_scope_fallback_keeps_mode_stage_and_owner_boundaries(pipeline,scope):
    p=pipeline
    item=extract_one(p)
    content=candidate(modes=['EMVR_DIRECT'],stages=['IDEA_BRAINSTORMING'])
    assert review(p,item,content=content,scope=scope)[0].startswith('200')
    kwargs={'design_id':p.session.design_id,'project_id':p.seen[0].get('project_id'),
            'allow_scope_fallback':True}
    assert len(p.repo.retrieve('EMVR_DIRECT','IDEA_BRAINSTORMING',MESSAGE,**kwargs))==1
    assert not p.repo.retrieve('GUIDED_DESIGN','IDEA_BRAINSTORMING',MESSAGE,**kwargs)
    assert not p.repo.retrieve('EMVR_DIRECT','HYPOTHESIS',MESSAGE,**kwargs)
    kwargs.update(design_id='other-design',project_id='other-project')
    assert len(p.repo.retrieve('EMVR_DIRECT','IDEA_BRAINSTORMING',MESSAGE,**kwargs))==(1 if scope=='global' else 0)

@pytest.mark.parametrize('kind',['missing_source','uncovered_edit','low_confidence','invented_span','write_act','advance_only'])
def test_keep_current_requires_complete_grounded_semantic_decision(kind):
    acts=controls();message=MESSAGE
    if kind=='missing_source':acts[0].pop('source_text')
    if kind=='uncovered_edit':message += '; replace the probe with a coil'
    if kind=='low_confidence':acts[0]['confidence']=0.3
    if kind=='invented_span':acts[0]['source_end']=4
    if kind=='write_act':acts.append({'type':'MODIFY_DESIGN_FIELD','target':'research_object','content':'coil'})
    if kind=='advance_only':acts=acts[1:]
    assert not keeps_current_design(acts,message)
