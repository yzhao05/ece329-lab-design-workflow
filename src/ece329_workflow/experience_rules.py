"""Versioned, bounded experience contracts. No rule-authored code is executable."""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os

from .models import StageCompletionError

CURRENT_RULE_RUN = ContextVar('experience_rule_run', default=None)
HOOKS = ['after_intent', 'before_pending', 'before_advance', 'before_reply']
ACTIONS = ['decline_uncommitted_candidate', 'recheck_stage', 'advance_if_complete', 'ensure_next_task']
PRECONDITIONS = ['explicit_keep_and_continue', 'uncommitted_candidate', 'no_new_edits']
ASSERTIONS = ['candidate_ended', 'saved_design_preserved', 'history_preserved',
              'completion_checked', 'concrete_next_task', 'no_stale_question']
EXCEPTIONS = ['bare_continue', 'accept_candidate', 'new_edit', 'ambiguous_intent']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def execution_identity(value):
    """Compare effective actions only AFTER each rule's conditions match.

    Applicability differences do not imply contradictory writes. The supported
    contracts share their safety checks; ask_missing_fields is a required part
    of ensure_next_task even when older JSON omits the explicit alias.
    """
    from .experience_actions import effective_actions
    program=deepcopy({k:v for k,v in value.items() if k not in
                     {'version','examples','unmapped_conditions','conditions','actions'}})
    program['actions']=effective_actions(value)
    return digest(program)


def contract():
    """An unverified authoring template, never an automatic legacy migration."""
    return {'version': 1, 'intent': 'KEEP_CURRENT_AND_CONTINUE', 'exceptions': EXCEPTIONS[:],
            'hooks': HOOKS[:], 'actions': [{'name': name, 'parameters': {}} for name in ACTIONS],
            'preconditions': PRECONDITIONS[:], 'assertions': ASSERTIONS[:],
            'conflict': {'group': 'pending_resolution', 'policy': 'deny_conflicting'},
            'max_per_turn': 1, 'max_repairs': 1,
            'examples': [{'input': '不要修改，保留当前设计并继续', 'applies': True},
                         {'input': 'do not modify anything, go ahead', 'applies': True},
                         {'input': '继续', 'applies': False},
                         {'input': '接受之前的修改，再继续', 'applies': False},
                         {'input': '保留当前设计，但把探针改成线圈', 'applies': False},
                         {'input': '可能吧', 'applies': False}]}


def validate_contract(raw):
    if isinstance(raw,dict) and type(raw.get('version')) is int and raw['version']==2:
        from .experience_actions import validate_configurable
        return validate_configurable(raw)
    template = contract()
    if not isinstance(raw, dict) or set(raw) != set(template):
        raise ValueError('unsupported_rule_contract: code maintenance required')
    for field in set(template) - {'examples'}:
        if type(raw[field]) is not type(template[field]) or raw[field] != template[field]:
            raise ValueError(f'unsupported_rule_action_or_condition: {field}; code maintenance required')
    examples = raw['examples']
    if not isinstance(examples, list) or not 2 <= len(examples) <= 12:
        raise ValueError('Rule requires positive and negative examples')
    for row in examples:
        if not isinstance(row, dict) or set(row) != {'input', 'applies'} or type(row['applies']) is not bool or not isinstance(row['input'], str) or not 1 <= len(row['input']) <= 1000:
            raise ValueError('Invalid rule example')
    if {r['applies'] for r in examples} != {True, False}:
        raise ValueError('Rule requires positive and negative examples')
    return deepcopy(raw)


def settings(environ=None):
    env = os.environ if environ is None else environ
    def number(value, name, upper):
        value = int(value)
        if not 1 <= value <= upper:
            raise ValueError(f'{name} must be between 1 and {upper}')
        return value
    return {'candidates': number(env.get('ECE329_EXPERIENCE_MAX_CANDIDATES', '8'), 'ECE329_EXPERIENCE_MAX_CANDIDATES', 20),
            'token_budget': number(env.get('ECE329_EXPERIENCE_TOKEN_BUDGET', '4096'), 'ECE329_EXPERIENCE_TOKEN_BUDGET', 16000)}


def token_bound(value):
    # Conservative UTF-8 byte bound; never slice a rule or omit its exceptions.
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def replace_pending(session, pending):
    """Synchronize the authoritative pending, design projection and carried view."""
    from .dialogue_state import dialogue_state, build_carried_context
    from .design_state import set_pending_action_snapshot
    state = dialogue_state(session)
    state['pending_action'] = deepcopy(pending)
    set_pending_action_snapshot(session, pending)
    state['carried_context'] = build_carried_context(session)


def decline_candidate(session, pending):
    if not isinstance(pending, dict):
        return pending
    result = {k: deepcopy(v) for k, v in pending.items() if not k.startswith('candidate_')}
    result.update(advance_on_accept=False, last_candidate_resolution='DECLINED_KEEP_CURRENT')
    replace_pending(session, result)
    return result


def business_state(session):
    from .feedback import snapshot
    from .dialogue_state import current_pending_action
    pending = current_pending_action(session) or {}
    design = snapshot(session)
    design.pop('revision', None)
    return {'stage': session.current_stage.value, 'design': design,
            'pending': {k: v for k, v in pending.items() if k not in
                        {'action_id', 'repeat_count', 'created_revision', 'revision'}}}


def _preserved(before, after):
    """Existing substantive values must survive; derived new fields may be added."""
    if isinstance(before, dict):
        ignored = {'pending_action', 'applied_update_ids', 'field_provenance', 'last_updated_stage',
                   'semantic_signatures', 'updated_revision', 'last_updated_revision'}
        return isinstance(after, dict) and all(k in ignored or
            k in after and _preserved(v, after[k]) for k, v in before.items())
    return before == after


class RuleRun:
    def __init__(self, session, message, trace=None, store=None):
        self.before = deepcopy(session)
        self.message, self.trace, self.store = message, trace, store
        self.events, self.rules, self.applied, self.seen = [], [], [], set()
        self.semantic_match = False
        self.blocked = False
        self.ready = None
        self.advance_checked = False
        self.repairs = 0
        self.finished = False
        self.start_hash = digest(business_state(session))
        if trace is not None:
            trace.data['experience_execution'] = self.events

    def record(self, step, code, rule=None, **data):
        row = {'step': step, 'code': code, 'mode': self.before.interaction_state.value,
               'stage': self.before.current_stage.value, **data}
        if rule:
            row.update(rule_id=rule['id'], version=rule['version'])
        if len(self.events) < 200:
            self.events.append(row)

    def select(self, rules, decisions):
        # Refreshing prompts after an advance must not replace the execution set.
        if self.seen or self.finished:
            return
        self.rules = deepcopy([r for r in rules if r.get('execution')])
        for decision in decisions:
            self.record('retrieval', decision['reason'], **{k:v for k,v in decision.items() if k != 'reason'})

    def hook(self, phase, session, intent=None, response=None):
        from .dialogue_acts import keeps_current_design
        from .dialogue_state import current_pending_action
        if phase == 'after_intent':
            if 'selection_complete' in self.seen:
                self.record(phase,'execution_limit'); return
            self.seen.add('selection_complete')
            self.semantic_match = isinstance(intent, dict) and keeps_current_design(intent.get('dialogue_acts'), self.message)
            eligible = []
            for rule in self.rules:
                code = 'applicable' if self.semantic_match else 'intent_not_matched'
                if self.semantic_match and not (current_pending_action(session) or {}).get('candidate_answer'):
                    code = 'candidate_missing'
                if session.interaction_state.value not in rule['rule']['modes']:
                    code = 'mode_mismatch'
                elif session.current_stage.value not in rule['rule']['stages']:
                    code = 'stage_mismatch'
                elif code == 'applicable':
                    from .experience_actions import matches_conditions
                    if not matches_conditions(rule['execution'], current_pending_action(session) or {}): code='condition_not_matched'
                self.record('applicability', code, rule, model_claimed_adoption=None)
                if code == 'applicable': eligible.append(rule)
            # Conditions were checked separately above. Compare normalized
            # effects, not applicability thresholds or redundant legacy aliases.
            if len({execution_identity(r['execution']) for r in eligible}) > 1:
                self.blocked = True
                for rule in eligible: self.record('conflict', 'conflict_blocked', rule)
            elif eligible:
                self.applied = eligible[:1]
                self.sources = [{'id':r['id'],'version':r['version']} for r in eligible]
                for rule in eligible[1:]: self.record('conflict', 'equivalent_rule_coalesced', rule)
        elif phase == 'before_pending' and self.applied and not self.blocked:
            rule = self.applied[0]
            key = (rule['id'], rule['version'], self.start_hash)
            if key in self.seen:
                self.record(phase, 'execution_limit', rule); return
            self.seen.add(key)
            # Recheck active version immediately before mutation.
            if self.store and not all(self.store.rule_is_current(source) for source in getattr(self,'sources',[rule])):
                self.blocked = True; self.record(phase, 'version_or_status_changed', rule); return
            old = session.model_context.get('experience_execution_guard', {})
            self.effect_state=digest((execution_identity(rule['execution']),self.start_hash))
            legacy_keys={digest((s['id'],s['version'],self.start_hash)) for s in getattr(self,'sources',[rule])}
            if old.get('key') in legacy_keys or self.effect_state in old.get('effect_states',[]):
                self.blocked = True; self.record(phase, 'no_progress_blocked', rule); return
            decline_candidate(session, current_pending_action(session))
            self.record('action', 'candidate_declined', rule, action='decline_uncommitted_candidate', repairs=0)
            self.check_completion(session)
        elif phase == 'before_advance' and self.applied and not self.blocked:
            self.check_completion(session)
            if not self.ready:
                self.record(phase, 'completion_blocked', self.applied[0])
                raise StageCompletionError('Experience rule cannot bypass stage completeness')
            self.record(phase, 'completion_passed', self.applied[0])
            self.advance_checked = True
        elif phase == 'before_reply' and not self.finished:
            self.finished = True
            self.finalize(session, response)

    def check_completion(self, session):
        from .engine import WorkflowEngine
        try:
            WorkflowEngine._validate_completion(session, session.current_stage)
            self.ready = True
        except StageCompletionError:
            self.ready = False
        self.record('action', 'stage_complete' if self.ready else 'stage_incomplete',
                    self.applied[0], action='recheck_stage')

    def finalize(self, session, response):
        from .dialogue_state import current_pending_action
        if not self.applied or self.blocked or not isinstance(response, dict): return
        rule = self.applied[0]
        # Old Guided sub-stages can be normalized back into idea development.
        # That is a migration, not a successful forward transition.
        moved = session.current_stage_index > self.before.current_stage_index or (
            session.status.value == 'complete' and self.before.status.value != 'complete')
        pending = current_pending_action(session) or {}
        # Reuse the validated workflow question; no model-authored assertion or
        # keyword-based guess about which fields are missing.
        task = str(pending.get('question') or response.get('student_task') or '').strip()
        old_question = (self.before.model_context.get('dialogue_state', {}).get('pending_action') or {}).get('question')
        if not moved and (self.ready is False or not task or task == old_question):
            task = self.next_question(session)
            pending = current_pending_action(session) or {}
        # A substring check cannot establish that a free-form reply contains no
        # contradictory requests. Render this controlled repair from state.
        valid_task = bool(task and pending.get('question') == task and not pending.get('candidate_answer')
                          and (moved or task != old_question))
        english = session.model_context.get('response_language') == 'en'
        prefix = ('The declined changes were not applied. Your saved design is preserved.' if english else
                  '已结束被拒绝的候选修改，保留当前已保存设计。')
        replacement = prefix + ('\n\n' + task if valid_task else '')
        from .experience_actions import effective_actions
        actions={a['name']:a['parameters'] for a in effective_actions(rule['execution'])}
        if actions.get('ensure_next_task',{}).get('style')=='task_only' and valid_task:
            replacement=task
        if 'ask_missing_fields' in actions and self.ready is False and valid_task:
            self.record('action','missing_fields_asked',rule,action='ask_missing_fields',source='stage_validator_and_pending')
        if session.status.value == 'complete':
            replacement += '\n\n' + ('The workflow is complete.' if english else '设计流程已完成。')
        if (valid_task or session.status.value == 'complete') and self.repairs < 1:
            response['assistant_message'] = replacement
            response['student_task'] = task
            self.repairs += 1
            self.record('action', 'next_task_added', rule, action='ensure_next_task', repairs=self.repairs)
        reply_evidence = {'pending_id':pending.get('action_id'), 'task':task,
                          'source':'validated_pending' if valid_task else 'unverified',
                          'completed_actions':['decline_uncommitted_candidate'],
                          'stage_complete':self.ready, 'rendered_from_state':self.repairs == 1}
        response.setdefault('stage_payload', {})['experience_reply'] = reply_evidence
        projection = session.design_context.get('design_state', {}).get('pending_action') or {}
        checks = {'candidate_ended': not pending.get('candidate_answer') and not projection.get('candidate_answer'),
                  'saved_design_preserved': _preserved(self.before.design_context, session.design_context),
                  'history_preserved': session.history[:len(self.before.history)] == self.before.history,
                  'completion_checked': self.ready is not None,
                  'concrete_next_task': valid_task or session.status.value == 'complete',
                  'reply_consistent': self.repairs == 1 and response.get('assistant_message') == replacement,
                  'no_stale_question': (moved or bool(task and task != old_question)) and not (
                      old_question and old_question != task and old_question in response.get('assistant_message', ''))}
        if moved and not self.advance_checked: checks['completion_checked'] = False
        if moved and self.advance_checked:
            self.record('action','advanced_after_checks',rule,action='advance_if_complete')
        progress = self.start_hash != digest(business_state(session))
        passed = all(checks.values()) and progress
        self.record('state_change', 'business_state_changed' if progress else 'no_progress', rule,
                    before_hash=self.start_hash, after_hash=digest(business_state(session)), advanced=moved)
        self.record('verification', 'execution_verified' if passed else 'execution_failed', rule,
                    assertions=checks, repairs=self.repairs, kind='online_execution', sources=getattr(self,'sources',[]))
        # Keep a bounded history of effective actions + business states. A new
        # primary ID or a different retrieval order cannot restart the same repair.
        effect_state=digest((execution_identity(rule['execution']),self.start_hash))
        previous=session.model_context.get('experience_execution_guard',{}).get('effect_states',[])
        session.model_context['experience_execution_guard'] = {
            'key':digest((rule['id'],rule['version'],self.start_hash)), 'passed':passed,
            'effect_states':[h for h in previous if h!=effect_state][-15:]+[effect_state]}
        if isinstance(response.get('stage_payload'), dict):
            from .design_state import design_state_snapshot
            response['stage_payload']['design_state'] = design_state_snapshot(session)
            response['stage_payload']['pending_action'] = deepcopy(pending) or None
        if len(session.history) > len(self.before.history) and session.history[-1].get('revision') == response.get('revision'):
            current_output = session.history[-1]['output']
            for key in ('assistant_message', 'student_task', 'stage_payload'):
                if key in response: current_output[key] = deepcopy(response[key])
        stored_output = session.stage_outputs.get(response.get('handled_stage'))
        if isinstance(stored_output, dict) and stored_output.get('revision') == response.get('revision'):
            for key in ('assistant_message', 'student_task', 'stage_payload'):
                if key in response: stored_output[key] = deepcopy(response[key])
        if not checks['saved_design_preserved'] or not checks['history_preserved']:
            # Fail closed before the engine's final save. The caller retains the
            # last committed session; no retry and no partial rollback of fields.
            raise ValueError('experience_postcondition_failed: saved state was not preserved')

    @staticmethod
    def next_question(session):
        from .generator import guided_stage_entry_output
        from .engine import _emvr_stage_entry_output
        from .models import InteractionState, Stage, StepOutput
        from .dialogue_state import save_pending_action
        if session.interaction_state is InteractionState.GUIDED_DESIGN:
            if session.current_stage is Stage.IDEA_BRAINSTORMING:
                from .idea_development import has_idea_development, build_gap_output
                if has_idea_development(session):
                    output = build_gap_output(deepcopy(session), '')
                else:
                    question = '你要研究哪一种电磁现象，以及观察什么变化？'
                    output = StepOutput(assistant_message=question, student_task=question,
                        stage_payload={'pending_action':{'type':'ANSWER_STAGE_QUESTION','subject':session.current_stage.value,
                            'question':question,'answer_fields':['research_object'],'advance_on_accept':False}})
            else:
                output = guided_stage_entry_output(deepcopy(session))
        else:
            output = _emvr_stage_entry_output(deepcopy(session), session.current_stage)
        pending = output.stage_payload.get('pending_action') or {}
        task = str(pending.get('question') or output.student_task or '').strip()
        if task:
            save_pending_action(session, session.current_stage, output)
        return task


def capture_replay(session):
    """Allowlisted historical state. No credentials, provider context or caches."""
    value = {'schema': 1, 'mode': session.interaction_state.value, 'stage_index': session.current_stage_index,
             'revision': session.revision, 'status': session.status.value,
             'completed_stages': deepcopy(session.completed_stages),
             'design_context': deepcopy(session.design_context), 'stage_outputs': deepcopy(session.stage_outputs),
             'history': [{k:deepcopy(v) for k,v in row.items() if not k.startswith('experience_replay_')}
                         for row in session.history],
             'model_context': {k: deepcopy(session.model_context[k]) for k in
                               ('dialogue_state', 'response_language', 'emvr_formula_flow') if k in session.model_context}}
    # A credential-shaped field in user-supplied context cannot be persisted as
    # replay evidence. Do not pretend the redacted state is complete.
    def sensitive(value):
        if isinstance(value, dict):
            return any(str(k).casefold() in {'api_key','access_token','access_token_hash','authorization','password','secret'} or sensitive(v) for k,v in value.items())
        return isinstance(value, list) and any(sensitive(v) for v in value)
    if sensitive(value): return {'schema':1,'complete':False,'reason':'sensitive_state_omitted'}
    # Never silently truncate a snapshot and claim it is replayable.
    if token_bound(value) > 96000:
        return {'schema': 1, 'complete': False, 'reason': 'snapshot_budget'}
    return {**value, 'complete': True}
