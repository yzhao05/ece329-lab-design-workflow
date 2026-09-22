"""Isolated workflow replay using simulated or recorded semantic responses.

Never resolves a provider, writes to the production store or exports artifacts.
These tests cannot prove that a live model will classify a new utterance correctly.
"""
from copy import deepcopy
from .models import DesignSession, InteractionState, Stage, WorkflowStatus
from .experience_rules import digest, ASSERTIONS

VERIFIER_VERSION = 4


def semantic(message, kind):
    from .dialogue_state import resolved_intent
    targets = {'positive': ['KEEP_CURRENT', 'ADVANCE'], 'bare': ['ADVANCE'],
               'accept': ['ACCEPT'], 'ambiguous': []}.get(kind, [])
    acts = [{'type':'CONTROL', 'target':target, 'source_text':message, 'confidence':0.99,
             'operation':'MERGE', 'content':None} for target in targets]
    if kind == 'edit':
        acts = [{'type':'UNRESOLVED','target':'research_object','source_text':message,'content':message,'confidence':0.5}]
    return resolved_intent('ADVANCE_STAGE' if targets else 'UNCLEAR', dialogue_acts=acts,
                           actions_authoritative=True, source='ISOLATED_SIMULATED_RESPONSE')


def fixture(mode):
    session = DesignSession('isolated-experience-replay', mode)
    session.design_context = {'confirmed_note': 'Saved source and observation position'}
    session.model_context['dialogue_state'] = {'pending_action': {
        'action_id':'old-candidate', 'type':'CONFIRM_STAGE_OR_MODIFY', 'status':'PENDING',
        'subject':'IDEA_BRAINSTORMING', 'stage':'IDEA_BRAINSTORMING',
        'interaction_state':mode.value, 'question':'请填写旧修改的栏目对应关系。',
        'proposal':{}, 'allowed_intents':['ADVANCE_STAGE','ACCEPT_PREVIOUS_PROPOSAL','UNCLEAR'],
        'candidate_answer':'尚未执行的旧修改', 'candidate_binding_authorized':False,
        'candidate_resolution':'MODIFY_PREVIOUS_PROPOSAL', 'repeat_count':3}}
    session.history = [{'revision':0, 'user_message':'尚未执行的旧修改', 'output':{'assistant_message':'请核对修改'}}]
    return session


def restore(snapshot):
    required = {'mode','stage_index','revision','status','completed_stages','design_context','stage_outputs','model_context','history'}
    if not isinstance(snapshot, dict) or snapshot.get('complete') is not True or not required <= set(snapshot):
        return None
    if (not isinstance(snapshot['mode'], str) or snapshot['mode'] not in {m.value for m in InteractionState} or
            not isinstance(snapshot['status'], str) or snapshot['status'] not in {s.value for s in WorkflowStatus} or
            type(snapshot['stage_index']) is not int or not 0 <= snapshot['stage_index'] < len(Stage) or
            type(snapshot['revision']) is not int or snapshot['revision'] < 0 or
            any(not isinstance(snapshot[key], dict) for key in ('design_context','stage_outputs','model_context')) or
            not isinstance(snapshot['history'], list) or any(not isinstance(r, dict) for r in snapshot['history']) or
            not isinstance(snapshot['completed_stages'], list)):
        return None
    return DesignSession('isolated-historical-replay', InteractionState(snapshot['mode']),
                         current_stage_index=snapshot['stage_index'], revision=snapshot['revision'],
                         status=WorkflowStatus(snapshot['status']), completed_stages=deepcopy(snapshot['completed_stages']),
                         design_context=deepcopy(snapshot['design_context']), stage_outputs=deepcopy(snapshot['stage_outputs']),
                         history=deepcopy(snapshot['history']),
                         model_context=deepcopy(snapshot['model_context']))


def once(session, message, intent, rule=None, *, kind='isolated_simulated_replay'):
    from .engine import WorkflowEngine
    from .generator import RuleBasedStageGenerator
    from .experience import ExperienceStore
    from .experience_rules import business_state
    class SimulatedGenerator(RuleBasedStageGenerator):
        def resolve_intent(self, *_): return deepcopy(intent)
    engine = WorkflowEngine(generator=SimulatedGenerator())
    store = ExperienceStore()
    engine.experience_store = store
    store.select_rules = lambda current,*args: ([deepcopy(rule)] if rule and
        current.interaction_state.value in rule['rule']['modes'] and current.current_stage.value in rule['rule']['stages'] else [], [])
    store.rule_is_current = lambda *args: True
    engine.store.save(deepcopy(session))
    try:
        result = engine.process_turn(session.design_id, {'message': message})
        saved = engine.store.get(session.design_id)
        record = engine.telemetry_records(session.design_id)[0]
        events = record.get('experience_execution', [])
        for event in events:
            if event.get('kind') == 'online_execution': event['kind'] = kind
        return {'state': business_state(saved), 'reply':result.get('assistant_message'),
                'events':events, 'error':None}
    except Exception as exc:
        return {'state':business_state(engine.store.get(session.design_id)), 'reply':None,
                'events':[], 'error':type(exc).__name__}
    finally:
        store.close()


def compare(session, message, intent, rule, expected, *, kind='isolated_simulated_replay'):
    before = once(session, message, intent, kind=kind)
    after = once(session, message, intent, rule, kind=kind)
    verified = next((r for r in after['events'] if r['step']=='verification'), None)
    executed = any(r['code']=='candidate_declined' for r in after['events'])
    passed = bool(verified and verified['code']=='execution_verified') if expected else not executed
    passed = passed and not before['error'] and not after['error']
    return {'mode':session.interaction_state.value, 'initial_stage':session.current_stage.value,
            'message':message, 'expected_applicable':expected, 'actually_executed':executed,
            'passed':passed, 'kind':kind, 'baseline':before, 'with_rule':after,
            'changed':digest(before['state']) != digest(after['state']) or before['reply'] != after['reply']}


def coverage(content, cases):
    declared = [(mode,stage) for mode in content['modes'] for stage in content['stages']]
    rows=[]
    for mode,stage in declared:
        matches=[c for c in cases if c['mode']==mode and c['initial_stage']==stage]
        positives=[c for c in matches if c['expected_applicable']]
        negatives=[c for c in matches if not c['expected_applicable']]
        rows.append({'mode':mode,'stage':stage,'positive_executed':sum(c['actually_executed'] for c in positives),
                     'negative_checked':len(negatives), 'positive_passed':sum(c['passed'] for c in positives),
                     'negative_passed':sum(not c['actually_executed'] and c['passed'] for c in negatives)})
    uncovered=[r for r in rows if r['positive_executed']<2 or r['negative_checked']<8]
    return {'declared':{'modes':content['modes'],'stages':content['stages']},
            'tested':{'modes':sorted({c['mode'] for c in cases}), 'stages':sorted({c['initial_stage'] for c in cases})},
            'requirements':{'positive_per_scope':2,'negative_per_scope':8}, 'by_scope':rows,
            'positive_executed':sum(r['positive_executed'] for r in rows),
            'negative_checked':sum(r['negative_checked'] for r in rows),
            'uncovered':uncovered, 'sufficient':bool(rows) and not uncovered}


def verify(content, experience_id, version, evidence=None):
    from .experience_rules import validate_contract
    from .experience_actions import fixture_conditions, matches_conditions, effective_conditions
    validate_contract(content['execution'])
    def make_fixture(mode): return fixture_conditions(fixture(mode),content['execution'])
    rule = {'id':'EXP-'+experience_id, 'version':version, 'rule':content, 'execution':content['execution'], 'scope':'global'}
    examples = [
        ('不要修改，保留当前设计并继续','positive'), ('do not modify anything, go ahead','positive'),
        ('继续','bare'), ('go ahead','bare'), ('接受之前的修改','accept'), ('accept the previous changes','accept'),
        ('不要改其他内容，但把探针换成线圈','edit'), ('keep the rest but replace the probe','edit'),
        ('可能吧','ambiguous'), ('maybe','ambiguous')]
    cases = [compare(make_fixture(mode), message, semantic(message, kind), rule, kind=='positive' and
                     mode.value in content['modes'] and Stage.IDEA_BRAINSTORMING.value in content['stages'])
             for mode in InteractionState for message,kind in examples]
    # Every declared stage must exercise an applicable rule, not pass solely
    # because all the original fixtures were filtered out by stage scope.
    for mode in InteractionState:
        for stage in Stage:
            if stage is Stage.IDEA_BRAINSTORMING: continue
            for message, case_kind in examples:
                session=make_fixture(mode)
                session.current_stage_index=list(Stage).index(stage)
                session.model_context['dialogue_state']['pending_action'].update(subject=stage.value,stage=stage.value)
                expected=case_kind=='positive' and mode.value in content['modes'] and stage.value in content['stages']
                result=compare(session,message,semantic(message,case_kind),rule,expected)
                result.update(scenario='stage_coverage', initial_stage=stage.value)
                cases.append(result)
    conditions=content['execution'].get('conditions',{})
    for mode in InteractionState:
        if mode.value not in content['modes']: continue
        for stage_name in content['stages']:
            for restriction in ('min_repeat_count','pending_types'):
                if not conditions.get(restriction): continue
                session=make_fixture(mode);session.current_stage_index=list(Stage).index(Stage(stage_name))
                pending=session.model_context['dialogue_state']['pending_action']
                pending.update(subject=stage_name,stage=stage_name)
                if restriction=='min_repeat_count': pending['repeat_count']=conditions[restriction]-1
                else: pending['type']='OTHER_PENDING_TYPE'
                message='do not modify anything, go ahead'
                result=compare(session,message,semantic(message,'positive'),rule,False)
                result['scenario']='condition_negative';cases.append(result)
    from .dialogue_acts import apply_stage_field_updates
    for mode in InteractionState:
        for message in ('不要修改，保留当前设计并继续','do not modify anything, go ahead'):
            session = make_fixture(mode)
            if mode is InteractionState.EMVR_DIRECT:
                session.design_context['emvr_design']={'field_state':{'experiment_brief':'改变电流并观察圆心磁场',
                    'research_object':'电流环','changed_quantities':['电流'],'observed_quantities':['圆心磁场']}}
            else:
                stage=Stage.VARIABLES_AND_CONDITIONS
                session.current_stage_index=list(Stage).index(stage)
                values={'independent_variable':'距离','observations':'场线和通量','controlled_conditions':'电荷量和观察方式'}
                apply_stage_field_updates(session,[{'field':k,'operation':'REPLACE','value':v} for k,v in values.items()],stage=stage)
                session.model_context['dialogue_state']['pending_action'].update(subject=stage.value,stage=stage.value)
                session.stage_outputs[stage.value]={'assistant_message':'确认后继续','stage_payload':values}
            expected=mode.value in content['modes'] and session.current_stage.value in content['stages']
            result=compare(session,message,semantic(message,'positive'),rule,expected)
            result['scenario']='complete_stage'
            if expected: result['passed']=result['passed'] and result['with_rule']['state']['stage']!=session.current_stage.value
            cases.append(result)
    historical = {'status':'unavailable', 'reason':'historical_snapshot_incomplete', 'kind':'isolated_recorded_response_replay'}
    replay = (evidence or {}).get('replay') or {}
    if not isinstance(replay, dict): replay = {}
    session = restore(replay.get('state_before'))
    if session is not None and isinstance(replay.get('intent'), dict) and isinstance(replay.get('message'), str):
        from .dialogue_acts import keeps_current_design
        from .dialogue_state import current_pending_action
        expected = (keeps_current_design(replay['intent'].get('dialogue_acts'), replay['message']) and
                    bool((current_pending_action(session) or {}).get('candidate_answer')) and
                    matches_conditions(content['execution'],current_pending_action(session) or {}) and
                    session.interaction_state.value in content['modes'] and session.current_stage.value in content['stages'])
        historical = {'status':'completed', 'kind':'isolated_recorded_response_replay',
                      'result':compare(session,replay['message'],replay['intent'],rule,expected,kind='isolated_recorded_response_replay')}
    counts=coverage(content,cases)
    status=('insufficient_coverage' if not counts['sufficient'] else 'passed' if all(c['passed'] for c in cases) and
            (historical['status']=='unavailable' or historical['result']['passed']) else 'failed')
    return {'verifier_version':VERIFIER_VERSION, 'status':status, 'coverage':counts,
            'effective_conditions':effective_conditions(content),
            'layers':{'executor':status,'semantics':'not_tested','full_flow':'not_tested'},
            'kind':'isolated_simulated_replay', 'assertions':ASSERTIONS, 'cases':cases,
            'historical':historical, 'live_model_test':'not_run',
            'example_semantics':'fixture_responses_not_live_model_predictions',
            'rule_examples_semantically_verified':False,
            'limitations':['Historical replay is unavailable without the actual before-state and recorded semantic response.',
                           'Simulation tests controlled execution; it does not certify the model interpretation of custom examples.']}
