"""Regressions for coexistence of curated advice and v1/v2 executable rules."""
from copy import deepcopy
import json
import pytest

from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import InteractionState
from ece329_workflow.experience_rules import contract, RuleRun, token_bound
from ece329_workflow.experience_actions import configurable_contract, effective_conditions
from ece329_workflow.experience_admin import configure_draft
from ece329_workflow.experience_replay import fixture, semantic
from ece329_workflow.feedback import guidance, bounded_guidance, curated_guidance
from tests.test_executable_experience import packet
from tests.test_feedback_pipeline import pipeline, candidate, extract_one, review_note


@pytest.mark.parametrize('mode',list(InteractionState))
@pytest.mark.parametrize('message',['不要修改，保留当前设计并继续','do not modify anything, go ahead'])
@pytest.mark.parametrize('scenario',['legacy_and_default','different_thresholds'])
def test_compatible_rules_execute_once_in_both_modes(pipeline,mode,message,scenario):
    first=contract() if scenario=='legacy_and_default' else configurable_contract()
    second=configurable_contract()
    if scenario=='different_thresholds': second['conditions']['min_repeat_count']=2
    rows=[packet(candidate(execution=first)),packet(candidate(execution=second))]
    rows[1]['id']='EXP-'+'b'*32
    original=deepcopy(rows)
    class Generator(RuleBasedStageGenerator):
        def resolve_intent(self,*args): return semantic(message,'positive')
    p=pipeline;p.repo.select_rules=lambda *args:(rows,[]);p.repo.rule_is_current=lambda *args:True
    engine=WorkflowEngine(generator=Generator());engine.experience_store=p.repo
    session=fixture(mode);engine.store.save(session)
    reply=engine.process_turn(session.design_id,{'message':message})
    events=engine.telemetry_records(session.design_id)[0]['experience_execution']
    assert not any(e['code']=='conflict_blocked' for e in events)
    assert sum(e['code']=='candidate_declined' for e in events)==1
    assert sum(e['code']=='missing_fields_asked' for e in events)==1
    verified=next(e for e in events if e['code']=='execution_verified')
    assert {s['id'] for s in verified['sources']}=={r['id'] for r in rows}
    assert '栏目对应关系' not in reply['assistant_message']
    assert engine.store.get(session.design_id).design_context['confirmed_note']==session.design_context['confirmed_note']
    assert rows==original  # Effective normalization never rewrites approved JSON.


@pytest.mark.parametrize('mode',list(InteractionState))
def test_nonmatching_rule_never_enters_merged_sources(mode):
    one=packet(candidate(execution=configurable_contract()));two=deepcopy(one)
    two['id']='EXP-'+'b'*32;two['execution']['conditions']['min_repeat_count']=4
    two['execution']['actions'][-1]['parameters']['style']='task_only'
    session=fixture(mode);message='do not modify anything, go ahead'
    run=RuleRun(session,message);run.select([one,two],[])
    run.hook('after_intent',session,semantic(message,'positive'))
    assert not run.blocked and run.sources==[{'id':one['id'],'version':one['version']}]
    assert any(e['code']=='condition_not_matched' for e in run.events)


@pytest.mark.parametrize('mode',list(InteractionState))
def test_conflicting_reply_writes_remain_blocked(mode):
    one=packet(candidate(execution=configurable_contract()));two=deepcopy(one)
    two['id']='EXP-'+'b'*32;two['execution']['actions'][-1]['parameters']['style']='task_only'
    session=fixture(mode);message='do not modify anything, go ahead'
    run=RuleRun(session,message);run.select([one,two],[])
    run.hook('after_intent',session,semantic(message,'positive'));run.hook('before_pending',session)
    assert run.blocked and not run.applied
    assert session.model_context['dialogue_state']['pending_action']['candidate_answer']


@pytest.mark.parametrize('mode',list(InteractionState))
@pytest.mark.parametrize('message',['already answered','我已经回答过了'])
def test_new_experience_keeps_builtin_guidance_with_shared_budget(pipeline,monkeypatch,mode,message):
    p=pipeline;item=extract_one(p);content=candidate(execution=configurable_contract())
    with p.repo.connection() as db:
        db.execute("UPDATE learned_experiences SET status='active',content=? WHERE id=?",(json.dumps(content),item['id']))
    session=fixture(mode)
    rows,decisions=p.repo.select_rules(session,message)
    assert len(rows)==1
    session.turn_context['experience_rules']=rows
    result=guidance(session,message)['rules']
    assert {r['id'] for r in result}=={'FB01','EXP-'+item['id']}
    assert sum(token_bound(r) for r in result)<=4096
    assert next(r for r in result if r['id'].startswith('EXP-'))['execution']==content['execution']
    # Enough for each separately, but not together: keep relevant built-in,
    # exclude the whole learned rule from BOTH prompt and executable selection.
    monkeypatch.setenv('ECE329_EXPERIENCE_TOKEN_BUDGET',str(token_bound(rows[0])))
    rows,decisions=p.repo.select_rules(session,message)
    assert not rows and any(d['rule_id'].startswith('EXP-') and d['reason']=='budget_excluded' for d in decisions)
    session.turn_context['experience_rules']=rows
    assert [r['id'] for r in guidance(session,message)['rules']]==['FB01']
    monkeypatch.setenv('ECE329_EXPERIENCE_TOKEN_BUDGET','4096')
    monkeypatch.setenv('ECE329_EXPERIENCE_MAX_CANDIDATES','1')
    rows,decisions=p.repo.select_rules(session,message)
    assert not rows and any(d['rule_id'].startswith('EXP-') and d['reason']=='candidate_limit' for d in decisions)


def test_guidance_deduplicates_and_respects_tiny_budgets():
    builtins=curated_guidance('already answered')
    rows,_=bounded_guidance(builtins*2,'already answered',{'candidates':8,'token_budget':4096})
    assert len(rows)==1
    rows,decisions=bounded_guidance(builtins,'already answered',{'candidates':8,'token_budget':1})
    assert not rows and decisions[0]['reason']=='budget_excluded'


def test_required_question_cannot_be_disabled_and_legacy_preview_is_explicit(pipeline):
    p=pipeline;item=extract_one(p)
    with pytest.raises(ValueError,match='required_missing_fields_question'):
        configure_draft(p.repo,item['id'],{'version':1,'note':review_note(),'ask_missing_fields':False})
    preview=effective_conditions(candidate(execution=contract()))
    assert preview['required_behaviors']==['ask_missing_fields_when_incomplete']
    assert any(a['name']=='ask_missing_fields' for a in preview['actions'])
