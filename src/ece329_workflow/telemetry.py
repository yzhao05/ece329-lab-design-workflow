"""Per-turn measurements; no prompts, credentials or invented token counts."""
from contextvars import ContextVar
from time import perf_counter, time
from uuid import uuid4
import json
from .usage import PriceBook, read_usage, summarize, agent_role

CURRENT_TRACE = ContextVar('workflow_trace', default=None)


def injected_rule_ids(payload, candidates):
    """Match whole selected rules in the actual top-level guidance block.

    An ID in a user quote, history, diagnostics or a partial rule is not an
    injection. Support the JSON intent input and CONTEXT_JSON stage envelope.
    """
    raw=payload.get('input',[])
    texts=[raw] if isinstance(raw,str) else [
        part.get('text','') for item in raw if isinstance(item,dict)
        for part in (item.get('content',[]) if isinstance(item.get('content'),list) else [])
        if isinstance(part,dict) and part.get('type')=='input_text'] if isinstance(raw,list) else []
    supplied=[]
    for text in texts:
        if not isinstance(text,str): continue
        try:
            context=json.loads(text)
        except (ValueError,TypeError):
            _,marker,text=text.rpartition('CONTEXT_JSON:\n')
            if not marker: continue
            try: context=json.loads(text)
            except (ValueError,TypeError): continue
        if not isinstance(context,dict): continue
        block=context.get('feedback_guidance')
        if not isinstance(block,dict): continue
        rows=block.get('rules',[])
        if isinstance(rows,list): supplied.extend(row for row in rows if isinstance(row,dict))
    return list(dict.fromkeys(r['id'] for r in candidates if any(
        all(key in actual and actual[key]==value for key,value in r.items()) for actual in supplied)))


class TurnTrace:
    def __init__(self, session, request):
        self.started = perf_counter()
        self.prices = PriceBook()
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
        self.data['final_stage'] = result.get('current_stage') if result else self.data['initial_stage']
        self.data['handled_stage'] = result.get('handled_stage') if result else self.data['initial_stage']
        for key in ('input_tokens', 'output_tokens'):
            values = [call.get(key) for call in calls]
            self.data[key] = sum(values) if all(v is not None for v in values) else None
        self.data['retry_count'] = sum(event['retry_count'] for event in self.data['stages'])
        self.data['usage'] = summarize(calls, self.data['latency_ms'])
        return self.data


class ObservedTransport:
    def __init__(self, transport, session, route, budget=None):
        self.transport, self.session, self.route = transport, session, route
        self.budget = budget

    def create(self, payload):
        from .model_selection import model_details
        details = model_details(payload.get('model'), payload.get('reasoning', {}).get('effort', 'medium'), apply_preset=False)
        trace = CURRENT_TRACE.get()
        if self.budget is not None:
            reserved = self.budget.reserve(self.route['max_output_tokens'])
            payload = {**payload, 'max_output_tokens': reserved}
            # The provider adapter must not raise this turn's remaining budget.
            if details['provider'] == 'deepseek':
                payload['_workflow_output_cap'] = reserved
            if trace:
                trace.data['output_budget_limit'] = self.budget.limit
        start = perf_counter()
        # Count only rules actually present in this request after prompt budgets
        # and recovery payload construction; never store the serialized prompt.
        row = {'stage': self.session.current_stage.value, 'model_profile': self.route['profile'],
               'model_id': details['api_model'], 'selected_model': payload.get('model'),
               'provider': details['provider'], 'reasoning': details['reasoning'],
               'schema': payload.get('text', {}).get('format', {}).get('name'),
               'experience_rule_ids': injected_rule_ids(payload,self.session.turn_context.get('experience_rules', [])),
               'input_tokens': None, 'output_tokens': None, 'error_type': None}
        row['max_output_tokens'] = payload.get('max_output_tokens')
        row['agent'] = agent_role(row['schema'])
        from .experience_rules import CURRENT_RULE_RUN
        if (run := CURRENT_RULE_RUN.get()) is not None:
            for rule in self.session.turn_context.get('experience_rules', []):
                run.record('injection', 'injected' if rule['id'] in row['experience_rule_ids'] else 'not_injected', rule,
                           request_schema=row['schema'])
        try:
            response = self.transport.create(payload)
            read_usage(response, row)
            return response
        except Exception as exc:
            row['error_type'] = type(exc).__name__
            raise
        finally:
            if self.budget is not None:
                self.budget.settle(reserved, row['output_tokens'])
                if trace:
                    trace.data['output_budget_charged'] = self.budget.charged
            row['latency_ms'] = round((perf_counter() - start) * 1000, 2)
            if trace is not None:
                trace.prices.apply(row)
                trace.data['calls'].append(row)
