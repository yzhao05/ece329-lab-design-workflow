"""Bounded, maintainer-only observations of the real dialogue execution.

Observers never retry, call a model, or authorize a workflow mutation.
Missing observations are not evidence of success.
"""
from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
import re

from .telemetry import CURRENT_TRACE

MAX_EVENTS = 120
SECRET_KEYS = {'api_key', 'apikey', 'authorization', 'password', 'secret', 'access_token', 'accesstoken',
               'headers', 'reasoning', 'reasoning_content', 'chain_of_thought'}


def safe(value, depth=0):
    if depth > 7:
        return '[depth limit]'
    if isinstance(value, dict):
        return {str(k): safe(v, depth + 1) for k, v in list(value.items())[:40]
                if not any(word in str(k).lower() for word in SECRET_KEYS)}
    if isinstance(value, (list, tuple)):
        rows = [safe(v, depth + 1) for v in value[:24]]
        return rows + ([{'omitted_items': len(value) - 24}] if len(value) > 24 else [])
    if isinstance(value, str):
        value = re.sub(r'(?i)\b(?:sk-[\w-]+|bearer\s+\S+)', '[redacted]', value)
        value = re.sub(r'(?i)(?:api[_-]?key|password|access[_-]?token|secret)\s*[:=]\s*\S+', '[redacted]', value)
        return value[:800] + (' [truncated]' if len(value) > 800 else '')
    if isinstance(value, float) and not math.isfinite(value):
        return '[non-finite number]'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def record(step, status, **data):
    trace = CURRENT_TRACE.get()
    if trace is None:
        return
    diagnostic = trace.data.setdefault('execution_diagnostic', {
        'version': 1, 'events': [], 'truncated': False,
    })
    event = {'sequence': len(diagnostic['events']) + 1,
             'step': step, 'status': status, **safe(data)}
    if len(diagnostic['events']) >= MAX_EVENTS or len(json.dumps(diagnostic, ensure_ascii=False)) + len(json.dumps(event, ensure_ascii=False)) > 60000:
        diagnostic['truncated'] = True
        return
    diagnostic['events'].append(event)


def intent_view(raw):
    if not isinstance(raw, dict):
        return {'invalid_type': type(raw).__name__}
    result = {k: raw.get(k) for k in (
        'intent', 'confidence', 'advance_requested', 'target', 'source',
        'actions_authoritative')}
    for key in ('dialogue_acts', 'unresolved_content'):
        values = raw.get(key)
        if not isinstance(values, list):
            result[key] = None
            continue
        result[key] = []
        for action in values[:24]:
            if not isinstance(action, dict):
                result[key].append({'invalid_type': type(action).__name__})
                continue
            item = {k: action.get(k) for k in ('type', 'target', 'operation', 'confidence', 'source_start', 'source_end', 'reason')}
            if 'content' in action:
                encoded = json.dumps(action['content'], ensure_ascii=False, sort_keys=True, default=str)
                item['content_evidence'] = {'sha256': hashlib.sha256(encoded.encode()).hexdigest(),
                    'serialized_chars': len(encoded), 'type': type(action['content']).__name__,
                    'source': 'original_dialogue_and_state_evidence'}
            result[key].append(item)
        if len(values) > 24:
            result[key].append({'omitted_items': len(values) - 24})
    return safe(result)


def observe_validation(fn):
    @wraps(fn)
    def wrapped(raw, pending_action):
        if CURRENT_TRACE.get() is None:
            return fn(raw, pending_action)
        before = intent_view(raw)
        record('semantic_validation_input', 'success', validator=fn.__name__, result=before)
        try:
            result = fn(raw, pending_action)
        except Exception as exc:
            record('semantic_validation', 'failed', validator=fn.__name__,
                   code='backend_exception', error_type=type(exc).__name__)
            raise
        blocked = result.get('intent') == 'UNCLEAR' and (
            before.get('intent') != 'UNCLEAR' or result.get('source') == 'SEMANTIC_INVALID_ACTIONS'
            or str(result.get('source') or '').startswith('LOW_CONFIDENCE_'))
        record('semantic_validation', 'blocked' if blocked else 'success',
               validator=fn.__name__, before=before, after=intent_view(result))
        return result
    return wrapped


def observe_model_parse(fn):
    @wraps(fn)
    def wrapped(response):
        try:
            result = fn(response)
        except Exception as exc:
            reasons = {
                'Intent model output was invalid': ('invalid_json', '$'),
                'Intent model output must be an object': ('object_required', '$'),
                'Intent dialogue_acts_json was invalid': ('invalid_json', 'dialogue_acts_json'),
                'Intent dialogue_acts_json must encode an array': ('array_required', 'dialogue_acts_json'),
            }
            code, path = reasons.get(str(exc), ('unclassified_parse_failure', None))
            record('model_parse', 'failed', parser=fn.__name__, error_type=type(exc).__name__,
                   code=code, field_path=path)
            raise
        record('model_result', 'success', result=intent_view(result[0]))
        return result
    return wrapped


def pending_view(session):
    pending = session.model_context.get('dialogue_state', {}).get('pending_action')
    if not isinstance(pending, dict):
        return None
    candidate = pending.get('candidate_answer')
    # Content remains in original evidence; fingerprints identify changes without
    # copying the complete answer into every diagnostic event.
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:16] if isinstance(candidate, str) and candidate else None
    return safe({**{k: pending.get(k) for k in (
        'action_id', 'status', 'subject', 'answer_fields', 'candidate_binding_authorized',
        'candidate_resolution', 'last_candidate_resolution', 'repeat_count', 'created_at_revision')},
        'candidate_id': f"{pending.get('action_id')}:{digest}" if digest else None,
        'candidate_turn_count': len(pending['candidate_turns']) if isinstance(pending.get('candidate_turns'), list) else None,
    })


def observe_candidate(fn):
    @wraps(fn)
    def wrapped(session, *args, **kwargs):
        if CURRENT_TRACE.get() is None:
            return fn(session, *args, **kwargs)
        before = pending_view(session)
        try:
            result = fn(session, *args, **kwargs)
        except Exception as exc:
            record('candidate_lifecycle', 'failed', branch=fn.__name__,
                   before=before, after=pending_view(session), error_type=type(exc).__name__)
            raise
        after = pending_view(session)
        old_id, new_id = (before or {}).get('candidate_id'), (after or {}).get('candidate_id')
        candidate_change = 'pending_updated'
        if old_id or new_id:
            if not old_id:
                candidate_change = 'created'
            elif not new_id:
                candidate_change = 'rejected' if fn.__name__ == 'decline_candidate' else 'ended'
            elif old_id != new_id:
                candidate_change = 'replaced'
            elif (before or {}).get('status') != (after or {}).get('status') and (after or {}).get('status') == 'REJECTED':
                candidate_change = 'rejected'
            elif (before or {}).get('status') != (after or {}).get('status') and (after or {}).get('status') in {'ACCEPTED', 'CONFIRMED'}:
                candidate_change = 'accepted'
            else:
                candidate_change = 'retained'
        record('candidate_lifecycle', 'success', branch=fn.__name__, before=before,
               after=after, changed=before != after, execution_revision=session.revision,
               candidate_change=candidate_change, source_turn_id=CURRENT_TRACE.get().data.get('turn_id'))
        return result
    return wrapped


def observe_fields(fn):
    @wraps(fn)
    def wrapped(session, resolved, user_message, *args, **kwargs):
        if CURRENT_TRACE.get() is None:
            return fn(session, resolved, user_message, *args, **kwargs)
        from .feedback import snapshot
        before = snapshot(session)
        result = fn(session, resolved, user_message, *args, **kwargs)
        after = snapshot(session)
        changed = []
        def compare(left, right, path='design'):
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(set(left) | set(right)):
                    compare(left.get(key), right.get(key), f'{path}.{key}')
            elif left != right:
                changed.append(path)
        compare(before, after)
        record('field_processing', 'success', handler=fn.__name__,
               proposed_actions=intent_view(resolved).get('dialogue_acts'),
               changed_paths=changed, execution_revision=session.revision,
               source_turn_id=CURRENT_TRACE.get().data.get('turn_id'),
               scope='canonical_design_snapshot',
               unchanged_reason='not_recorded' if not changed else None)
        return result
    return wrapped


def observe_completion(fn):
    @wraps(fn)
    def wrapped(session, stage):
        try:
            result = fn(session, stage)
        except Exception as exc:
            from .models import StageCompletionError
            record('stage_check', 'blocked' if isinstance(exc, StageCompletionError) else 'failed',
                   validator=fn.__name__, stage=stage.value,
                   code='incomplete' if isinstance(exc, StageCompletionError) else 'backend_exception',
                   check_message=str(exc) if isinstance(exc, StageCompletionError) else None,
                   error_type=type(exc).__name__)
            raise
        record('stage_check', 'success', validator=fn.__name__, stage=stage.value)
        return result
    return wrapped


def observe_context(fn):
    @wraps(fn)
    def wrapped(session, user_message, pending_action, carried_context):
        result = fn(session, user_message, pending_action, carried_context)
        if CURRENT_TRACE.get() is not None:
            payload = json.loads(result)
            context = payload.get('carried_context', {})
            CURRENT_TRACE.get().execution_context_keys = set(context)
            record('context', 'success', source=fn.__name__,
                   scope='serialized_intent_input_before_transport',
                   included_context_keys=list(context), pending_id=(pending_action or {}).get('action_id'),
                   candidate=pending_view(session), serialized_chars=len(result),
                   history_revisions=None, truncation_status='not_recorded',
                   note='Context keys do not prove complete history or provider visibility')
        return result
    return wrapped


def observe_submitted_context(payload):
    trace = CURRENT_TRACE.get()
    if trace is None:
        return
    schema = payload.get('text', {}).get('format', {}).get('name', '')
    if 'intent' not in str(schema):
        return
    inputs = payload.get('input', [])
    texts = [inputs] if isinstance(inputs, str) else []
    for item in inputs if isinstance(inputs, list) else []:
        parts = item.get('content') if isinstance(item, dict) else None
        if isinstance(parts, list):
            texts.extend(part.get('text') for part in parts
                         if isinstance(part, dict) and part.get('type') == 'input_text')
    for text in texts:
        try:
            content = json.loads(text)
        except (TypeError, ValueError):
            record('context', 'not_recorded', scope='submitted_to_provider_adapter', schema=schema)
            continue
        if not isinstance(content, dict):
            continue
        context = content.get('carried_context')
        keys = set(context) if isinstance(context, dict) else set()
        original = getattr(trace, 'execution_context_keys', None)
        record('context', 'success', scope='submitted_to_provider_adapter', schema=schema,
               included_top_level_keys=list(content), included_context_keys=sorted(keys),
               omitted_context_keys=sorted(original - keys) if original is not None else None,
               serialized_chars=len(text), uses_remote_previous_response=bool(payload.get('previous_response_id')),
               max_output_tokens=payload.get('max_output_tokens'), history_revisions=None,
               history_scope='structured_context_only' if 'history' not in content and 'recent_turns' not in content else 'not_recorded')


def observe_recovery(fn):
    @wraps(fn)
    def wrapped(resolved, pending_action, user_message):
        result = fn(resolved, pending_action, user_message)
        if CURRENT_TRACE.get() is None:
            return result
        pending = pending_action if isinstance(pending_action, dict) else {}
        record('recovery', 'success' if result is not None else 'blocked',
               method=fn.__name__, recovered=result is not None,
               answer_fields=pending.get('answer_fields'),
               binding_authorized=pending.get('candidate_binding_authorized'),
               retry_limit=None, stop_reason='not_recorded')
        return result
    return wrapped


def capture(session, response):
    """Save private diagnostics by exact turn revision, never in public output."""
    trace = CURRENT_TRACE.get()
    if trace is None:
        return
    from .feedback_diagnostics import release_version
    diagnostic = deepcopy(trace.data.get('execution_diagnostic', {'version': 1, 'events': [], 'truncated': False}))
    diagnostic['correlation'] = {'request_id': trace.data['id'], 'turn_id': trace.data.get('turn_id'),
        'design_id': session.design_id, 'revision': response.get('revision'),
        'mode': trace.data['mode'], 'stage': trace.data['initial_stage'], 'release': release_version()}
    diagnostic['final_intent'] = safe(session.history[-1].get('resolved_intent')) if session.history else None
    diagnostic['pending_after'] = pending_view(session)
    moved_forward = session.current_stage_index > trace.data['initial_stage_index']
    completed = session.status.value == 'complete' and trace.data['initial_workflow_status'] != 'complete'
    diagnostic['advance'] = {'status': 'success' if moved_forward or completed else 'not_advanced',
        'before': trace.data['initial_stage'], 'after': session.current_stage.value,
        'workflow_status_before': trace.data['initial_workflow_status'], 'workflow_status_after': session.status.value}
    diagnostic['experience_execution'] = safe(trace.data.get('experience_execution', []))
    from .feedback import source_stamp
    business_fingerprint = source_stamp(session)['fingerprint']
    records = session.model_context.setdefault('execution_diagnostics', [])
    previous = records[-1] if records else {}
    reply_fingerprint = hashlib.sha256(str(response.get('assistant_message') or '').encode()).hexdigest()
    unchanged = (previous.get('business_fingerprint') == business_fingerprint
                 and previous.get('advance', {}).get('after') == session.current_stage.value)
    diagnostic['business_fingerprint'] = business_fingerprint
    diagnostic['reply_fingerprint'] = reply_fingerprint
    diagnostic['repetition'] = {
        'status': 'success', 'comparison_scope': 'canonical_design_and_stage',
        'consecutive_unchanged_turns': previous.get('repetition', {}).get('consecutive_unchanged_turns', 0) + 1 if unchanged else 0,
        'same_reply_without_design_progress': unchanged and previous.get('reply_fingerprint') == reply_fingerprint,
        'automatic_retry_added': False, 'stop_reason': None,
    }
    for step in ('model_result', 'semantic_validation', 'candidate_lifecycle', 'stage_check', 'reply', 'recovery', 'context'):
        if not any(e['step'] == step for e in diagnostic['events']):
            diagnostic['events'].append({'step': step, 'status': 'not_executed'
                if step in {'stage_check', 'recovery'} and not diagnostic['truncated'] else 'not_recorded',
                'scope': '_validate_completion' if step == 'stage_check' else step})
    trace.data['execution_diagnostic'] = deepcopy(diagnostic)
    records.append(diagnostic)
    del records[:-40]


def extraction_evidence(evidence):
    """One bounded diagnostic copy per turn in model input; raw export is intact."""
    result = deepcopy(evidence)
    if not isinstance(result, dict):
        return result
    seen = set()
    budget = 18000
    target = result.get('reported_turn')
    rows = ([target] if isinstance(target, dict) else [])
    for key in ('event_chain', 'recent_turns'):
        values = result.get(key)
        if isinstance(values, list):
            rows.extend(row for row in values if isinstance(row, dict))
    rows = [row for row in rows if isinstance(row.get('execution_diagnostic'), dict)]
    # Reserve an explicit omission marker for every row before spending any
    # budget on content. Metadata and duplicate references count as input too.
    marker = {'model_input_truncated': True}
    size = lambda value: len(json.dumps(value, ensure_ascii=False))
    marker_size = size(marker)
    for position, row in enumerate(rows):
        diagnostic = row['execution_diagnostic']
        available = max(0, budget - (len(rows) - position - 1) * marker_size)
        revision = row.get('revision')
        if type(revision) is int and revision in seen:
            reference = {'same_turn_reference': revision}
            row['execution_diagnostic'] = reference if size(reference) <= available else dict(marker)
            budget -= size(row['execution_diagnostic'])
            continue
        if type(revision) is int:
            seen.add(revision)
        metadata = {key: diagnostic[key] for key in
            ('version', 'correlation', 'final_intent', 'advance', 'pending_after', 'truncated', 'repetition') if key in diagnostic}
        metadata['experience_execution'] = diagnostic.get('experience_execution', [])
        compact = {key: safe(value) for key, value in metadata.items()}
        if compact != metadata:
            compact['model_input_truncated'] = True
        events = diagnostic.get('events')
        events = [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []
        compact['events'] = []
        compact['model_input_omitted_events'] = len(events)
        if size(compact) > available:
            # Do not send oversized state summaries or partially reconstruct
            # them as facts. Full originals remain in maintainer evidence.
            compact = {'version': diagnostic.get('version'), 'events': [],
                       'model_input_truncated': True, 'model_input_omitted_events': len(events)}
        if size(compact) > available:
            row['execution_diagnostic'] = dict(marker)
            budget -= marker_size
            continue
        # Failures plus final results take precedence; restore chronological order.
        prioritized = sorted(enumerate(events), key=lambda pair: (
            pair[1].get('status') not in {'blocked', 'failed'}, -pair[0]))
        selected = []
        for index, event in prioritized:
            proposed = {**compact, 'events': compact['events'] + [event],
                        'model_input_omitted_events': len(events) - len(selected) - 1}
            if size(proposed) <= available:
                selected.append((index, event))
                compact = proposed
        compact['events'] = [deepcopy(event) for _, event in sorted(selected)]
        row['execution_diagnostic'] = compact
        budget -= size(compact)
    return result
