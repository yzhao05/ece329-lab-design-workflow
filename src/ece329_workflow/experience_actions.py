"""Small typed action library. Unsupported operations never become executable."""
from copy import deepcopy

ACTION_LIBRARY = {
    'decline_uncommitted_candidate': {'phase':'before_pending','writes':['pending.candidate_*'],
        'requires':['explicit_keep_and_continue','uncommitted_candidate','no_new_edits'], 'assertions':['candidate_ended'], 'limit':1},
    'recheck_stage': {'phase':'before_pending','writes':[], 'requires':['existing_stage_validator'],
        'assertions':['completion_checked'], 'limit':2},
    'ask_missing_fields': {'phase':'before_reply','writes':['pending','reply'], 'requires':['stage_incomplete','validator_task'],
        'assertions':['concrete_next_task','reply_consistent'], 'limit':1},
    'advance_if_complete': {'phase':'before_advance','writes':['stage'], 'requires':['stage_complete','user_confirmation'],
        'assertions':['completion_checked'], 'limit':1},
    'ensure_next_task': {'phase':'before_reply','writes':['pending','reply'], 'requires':['valid_pending_or_complete'],
        'assertions':['reply_consistent','no_stale_question'], 'limit':1},
}
PENDING_TYPES = ['CONFIRM_STAGE_OR_MODIFY','CONFIRM_OR_MODIFY','ANSWER_STAGE_QUESTION']


def effective_actions(execution):
    """Normalize legacy aliases without modifying the approved source JSON."""
    actions=deepcopy(execution['actions'])
    if not any(a['name']=='ask_missing_fields' for a in actions):
        position=next(i for i,a in enumerate(actions) if a['name']=='advance_if_complete')
        actions.insert(position,{'name':'ask_missing_fields','parameters':{}})
    for action in actions:
        if action['name']=='ensure_next_task' and not action['parameters']:
            action['parameters']={'style':'status_and_task'}
    return actions


def configurable_contract():
    from .experience_rules import contract
    value=contract();value['version']=2
    value['conditions']={'pending_types':[], 'min_repeat_count':0}
    value['unmapped_conditions']=[]
    value['actions'].insert(2,{'name':'ask_missing_fields','parameters':{}})
    value['actions'][-1]['parameters']={'style':'status_and_task'}
    return value


def validate_configurable(raw):
    from .experience_rules import contract, validate_contract
    template=configurable_contract()
    if set(raw)!=set(template): raise ValueError('unsupported_rule_contract: code maintenance required')
    conditions=raw['conditions']
    if (not isinstance(conditions,dict) or set(conditions)!={'pending_types','min_repeat_count'} or
        type(conditions['min_repeat_count']) is not int or not 0<=conditions['min_repeat_count']<=10 or
        not isinstance(conditions['pending_types'],list) or len(conditions['pending_types'])>3 or
        any(not isinstance(t,str) or t not in PENDING_TYPES for t in conditions['pending_types'])):
        raise ValueError('unsupported_rule_condition: code maintenance required')
    unknown=raw['unmapped_conditions']
    if not isinstance(unknown,list) or len(unknown)>12 or any(not isinstance(t,str) or not 1<=len(t)<=300 for t in unknown):
        raise ValueError('Invalid unmapped conditions')
    actions=raw['actions']
    if not isinstance(actions,list) or not 4<=len(actions)<=5: raise ValueError('unsupported_rule_actions')
    names=[]
    for action in actions:
        if not isinstance(action,dict) or set(action)!={'name','parameters'}: raise ValueError('unsupported_rule_action')
        name=action['name'];names.append(name)
        if not isinstance(name,str) or name not in ACTION_LIBRARY: raise ValueError('unsupported_rule_action: code maintenance required')
        allowed=[{}] if name!='ensure_next_task' else [{'style':'status_and_task'},{'style':'task_only'}]
        if action['parameters'] not in allowed: raise ValueError('unsupported_rule_parameters')
    required=[a['name'] for a in contract()['actions']]
    if names not in (required,required[:2]+['ask_missing_fields']+required[2:]):
        raise ValueError('unsupported_rule_action_order: preserve safety prerequisites')
    base={k:deepcopy(v) for k,v in raw.items() if k not in {'conditions','unmapped_conditions'}}
    base['version']=1;base['actions']=contract()['actions']
    validate_contract(base)
    return deepcopy(raw)


def matches_conditions(execution, pending):
    c=execution.get('conditions',{})
    repeats=pending.get('repeat_count',0)
    return (not execution.get('unmapped_conditions') and
            (not c.get('pending_types') or pending.get('type') in c['pending_types']) and
            type(repeats) is int and repeats>=c.get('min_repeat_count',0))


def fixture_conditions(session, execution):
    """Make declared test preconditions explicit, never infer example intent."""
    pending=session.model_context['dialogue_state']['pending_action']
    c=execution.get('conditions',{})
    if c.get('pending_types'): pending['type']=c['pending_types'][0]
    pending['repeat_count']=max(pending.get('repeat_count',0),c.get('min_repeat_count',0))
    return session


def effective_conditions(content):
    e=content.get('execution') or {}
    return {'executable':bool(e),'semantic_checks':['explicit_keep_and_continue','no_new_edits'] if e else [],
            'state_checks':['uncommitted_candidate'] if e else [], 'additional_checks':e.get('conditions',{}),
            'modes':content['modes'],'stages':content['stages'],
            'actions':effective_actions(e) if e else [], 'text_only':e.get('unmapped_conditions',[]),
            'required_behaviors':['ask_missing_fields_when_incomplete'] if e else [],
            'trigger_is_executable':False, 'trigger':content['trigger']}
