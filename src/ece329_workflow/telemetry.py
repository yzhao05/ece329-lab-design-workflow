"""Per-turn measurements; no prompts, credentials or invented token counts."""
from contextvars import ContextVar
from time import perf_counter, time
from uuid import uuid4
import json

CURRENT_TRACE = ContextVar('workflow_trace', default=None)


class TurnTrace:
    def __init__(self, session, request):
        self.started = perf_counter()
        self.data = {'id': uuid4().hex, 'design_id': session.design_id, 'turn_id': request.turn_id,
                     'created': time(), 'mode': session.interaction_state.value,
                     'initial_stage': session.current_stage.value, 'calls': [], 'stages': []}

    def finish(self, result=None, error=None):
        calls = self.data['calls']
        self.data.update(latency_ms=round((perf_counter() - self.started) * 1000, 2),
                         status='failed' if error else 'completed', error_type=type(error).__name__ if error else None,
                         revision=result.get('revision') if result else None,
                         workflow_status=result.get('workflow_status') if result else None,
                         completion_error=bool(result and result.get('completion_error')))
        for key in ('input_tokens', 'output_tokens'):
            values = [call.get(key) for call in calls]
            self.data[key] = sum(values) if all(v is not None for v in values) else None
        self.data['retry_count'] = sum(event['retry_count'] for event in self.data['stages'])
        return self.data


class ObservedTransport:
    def __init__(self, transport, session, route):
        self.transport, self.session, self.route = transport, session, route

    def create(self, payload):
        from .model_selection import model_details
        details = model_details(payload.get('model'), payload.get('reasoning', {}).get('effort', 'medium'))
        trace = CURRENT_TRACE.get()
        start = perf_counter()
        # Count only rules actually present in this request after prompt budgets
        # and recovery payload construction; never store the serialized prompt.
        serialized = json.dumps(payload, ensure_ascii=False)
        row = {'stage': self.session.current_stage.value, 'model_profile': self.route['profile'],
               'model_id': details['api_model'], 'selected_model': payload.get('model'),
               'provider': details['provider'], 'reasoning': details['reasoning'],
               'schema': payload.get('text', {}).get('format', {}).get('name'),
               'experience_rule_ids': [r['id'] for r in self.session.turn_context.get('experience_rules', []) if r['id'] in serialized],
               'input_tokens': None, 'output_tokens': None, 'error_type': None}
        try:
            response = self.transport.create(payload)
            usage = response.get('usage') or {}
            for key in ('input_tokens', 'output_tokens'):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    row[key] = value
            cached = (usage.get('input_tokens_details') or {}).get('cached_tokens')
            row['cached_input_tokens'] = cached if type(cached) is int and cached >= 0 else None
            return response
        except Exception as exc:
            row['error_type'] = type(exc).__name__
            raise
        finally:
            row['latency_ms'] = round((perf_counter() - start) * 1000, 2)
            if trace is not None:
                trace.data['calls'].append(row)
