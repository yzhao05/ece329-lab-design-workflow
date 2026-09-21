"""Measured usage and configurable cost estimates; never guess missing usage/prices."""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import json
import math
import os
import logging
import sqlite3
import re
from time import perf_counter, time
from uuid import uuid4

CURRENT_USAGE = ContextVar('supplemental_usage', default=None)


@contextmanager
def measure_translation(store, design_id, mode='unknown', stage='UNKNOWN'):
    calls = []
    record = {'id': uuid4().hex, 'design_id': design_id, 'created': time(), 'mode': mode,
              'stage': stage, 'stage_basis': 'translation_request', 'calls': calls}
    token = CURRENT_USAGE.set((calls, store.prices))
    started = perf_counter()
    try:
        yield
    finally:
        CURRENT_USAGE.reset(token)
        if calls and design_id:
            record['latency_ms'] = round((perf_counter() - started) * 1000, 2)
            try:
                with store.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    store._record_usage(db, record, 'translation')
            except (sqlite3.Error, OSError):
                logging.getLogger(__name__).warning('Translation usage persistence unavailable')


class PriceBook:
    def __init__(self, environ=None):
        env = os.environ if environ is None else environ
        try:
            self.rates = json.loads(env.get('ECE329_MODEL_PRICING_JSON', '{}'))
            if not isinstance(self.rates, dict):
                raise ValueError()
            for model, rates in self.rates.items():
                if not model or not isinstance(rates, dict) or set(rates) != {'input', 'cached_input', 'output'}:
                    raise ValueError()
                if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in rates.values()):
                    raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('ECE329_MODEL_PRICING_JSON must map API model IDs to nonnegative USD rates per million tokens: input, cached_input, output') from None

    def apply(self, call):
        provider = call.get('provider', 'unknown')
        key = f"{provider}/{call['model_id']}"
        rates = self.rates.get(key, self.rates.get(call['model_id']))
        call['pricing_key'] = key if key in self.rates else call['model_id'] if rates is not None else None
        call['price_rates_usd_per_million'] = deepcopy(rates)
        call['estimated_cost_usd'] = None
        counts = [call.get(key) for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')]
        if rates is None or any(value is None for value in counts):
            return
        incoming, cached, outgoing = counts
        cost = (Decimal(incoming - cached) * Decimal(str(rates['input']))
                + Decimal(cached) * Decimal(str(rates['cached_input']))
                + Decimal(outgoing) * Decimal(str(rates['output']))) / Decimal(1000000)
        call['estimated_cost_usd'] = float(cost)


def read_usage(response, call):
    usage = response.get('usage') if isinstance(response, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    # Provider adapters may already normalize their envelope. Raw Chat usage
    # is accepted only for DeepSeek, never guessed for an OpenAI response.
    native_deepseek = call.get('provider') == 'deepseek' and 'prompt_tokens' in usage
    call['usage_format'] = 'deepseek_chat' if native_deepseek else ('deepseek_normalized' if call.get('provider') == 'deepseek' else 'openai_responses')
    if native_deepseek:
        usage = {'input_tokens': usage.get('prompt_tokens'), 'output_tokens': usage.get('completion_tokens'),
                 'input_tokens_details': {'cached_tokens': usage.get('prompt_cache_hit_tokens')},
                 'output_tokens_details': usage.get('completion_tokens_details')}
    for key in ('input_tokens', 'output_tokens'):
        value = usage.get(key)
        call[key] = value if type(value) is int and value >= 0 else None
    details = usage.get('input_tokens_details')
    cached = details.get('cached_tokens') if isinstance(details, dict) else None
    incoming = call['input_tokens']
    call['cached_input_tokens'] = cached if type(cached) is int and incoming is not None and 0 <= cached <= incoming else None
    output_details = usage.get('output_tokens_details')
    reasoning = output_details.get('reasoning_tokens') if isinstance(output_details, dict) else None
    call['reasoning_tokens'] = reasoning if type(reasoning) is int and call['output_tokens'] is not None and 0 <= reasoning <= call['output_tokens'] else None
    reported = response.get('model') if isinstance(response, dict) else None
    call['reported_model'] = reported if isinstance(reported, str) and re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', reported) else None


def agent_role(schema):
    if not schema:
        return 'unknown_agent'
    if schema == 'feedback_experience_check':
        return 'experience_checker'
    if schema == 'feedback_experience':
        return 'experience_extractor'
    if schema == 'ece329_display_translation':
        return 'translator'
    if schema and any(word in schema for word in ('intent', 'dialogue_acts')):
        return 'intent_resolver'
    return 'design_agent'


def breakdown(records):
    """Attribute tokens to actual call stages. Keep unmeasured local work separate."""
    groups = {}
    stage_groups = {}
    for record in records:
        fallback_stage = record.get('initial_stage') or record.get('stage') or 'UNKNOWN'
        kind = record.get('kind', 'dialogue')
        if not record.get('calls'):
            stage_groups.setdefault(fallback_stage, {'calls': [], 'runs': set(), 'active_ms': 0})['runs'].add(record['id'])
        for call in record.get('calls', []):
            stage = call.get('stage') or fallback_stage
            agent = call.get('agent') or agent_role(call.get('schema'))
            key = (stage, agent, call.get('provider', 'unknown'), call.get('model_id', 'unknown'))
            bucket = groups.setdefault(key, {'calls': [], 'runs': set(), 'kinds': set(), 'presets': set(), 'reported': set()})
            bucket['calls'].append(call); bucket['runs'].add(record['id']); bucket['kinds'].add(kind)
            if call.get('selected_model'): bucket['presets'].add(call['selected_model'])
            if call.get('reported_model'): bucket['reported'].add(call['reported_model'])
            part = stage_groups.setdefault(stage, {'calls': [], 'runs': set(), 'active_ms': 0})
            part['calls'].append(call); part['runs'].add(record['id']); part['active_ms'] += call.get('latency_ms', 0)
        overhead = max(0, record.get('latency_ms', 0) - sum(c.get('latency_ms', 0) for c in record.get('calls', [])))
        if overhead:
            part = stage_groups.setdefault('UNATTRIBUTED_PROCESSING', {'calls': [], 'runs': set(), 'active_ms': 0})
            part['active_ms'] += overhead; part['runs'].add(record['id'])
    details = []
    for (stage, agent, provider, model), bucket in sorted(groups.items()):
        stats = summarize(bucket['calls'], sum(c.get('latency_ms', 0) for c in bucket['calls']))
        stats.update(run_count=len(bucket['runs']), time_basis='api')
        details.append({'stage': stage, 'agent': agent, 'provider': provider, 'model_id': model,
                        'selected_models': sorted(bucket['presets']), 'reported_models': sorted(bucket['reported']),
                        'kinds': sorted(bucket['kinds']), 'usage': stats})
    from .models import STAGE_SEQUENCE
    stage_order = {stage.value: index for index, stage in enumerate(STAGE_SEQUENCE)}
    stages = [{'stage': stage, 'usage': {**summarize(value['calls'], value['active_ms']),
                'run_count': len(value['runs']), 'time_basis': 'local' if stage == 'UNATTRIBUTED_PROCESSING' else 'api'}}
              for stage, value in sorted(stage_groups.items(), key=lambda pair: (stage_order.get(pair[0], 99), pair[0]))]
    return details, stages


def summarize(calls, active_ms=0):
    result = {'call_count': len(calls), 'active_ms': round(active_ms, 2), 'currency': 'USD'}
    for key in ('input_tokens', 'output_tokens', 'cached_input_tokens'):
        values = [call.get(key) for call in calls]
        result['known_' + key] = sum(v for v in values if v is not None)
        result[key] = sum(values) if all(v is not None for v in values) else None
    result['total_tokens'] = (result['input_tokens'] + result['output_tokens']
                              if result['input_tokens'] is not None and result['output_tokens'] is not None else None)
    result['missing_usage_calls'] = sum(c.get('input_tokens') is None or c.get('output_tokens') is None for c in calls)
    result['unpriced_calls'] = sum(c.get('estimated_cost_usd') is None for c in calls)
    result['known_cost_usd'] = float(sum((Decimal(str(c['estimated_cost_usd'])) for c in calls if c.get('estimated_cost_usd') is not None), Decimal(0)))
    result['estimated_cost_usd'] = result['known_cost_usd'] if not result['unpriced_calls'] else None
    return result


class UsageTransport:
    """Feedback calls are measured before output validation, including rejected JSON."""
    def __init__(self, transport, calls, prices):
        self.transport, self.calls, self.prices = transport, calls, prices

    def create(self, payload):
        from .model_selection import model_details
        detail = model_details(payload['model'], payload.get('reasoning', {}).get('effort', 'low'), apply_preset=False)
        call = {'model_id': detail['api_model'], 'selected_model': payload['model'], 'provider': detail['provider'],
                'schema': payload.get('text', {}).get('format', {}).get('name'),
                'reasoning_effort': detail['reasoning'], 'max_output_tokens': payload.get('max_output_tokens'),
                'input_tokens': None, 'output_tokens': None, 'cached_input_tokens': None}
        call['agent'] = agent_role(call['schema'])
        start = perf_counter()
        try:
            response = self.transport.create(payload)
            call.update(getattr(response, 'transport_metadata', {}))
            read_usage(response, call)
            return response
        except Exception as exc:
            call['error_type'] = type(exc).__name__
            call.update(getattr(exc, 'transport_metadata', {}))
            raise
        finally:
            call['latency_ms'] = round((perf_counter() - start) * 1000, 2)
            self.prices.apply(call)
            self.calls.append(call)


class UsageStore:
    def init_usage(self, db):
        db.executescript('''CREATE TABLE IF NOT EXISTS usage_designs (
            design_id TEXT PRIMARY KEY, created REAL NOT NULL, mode TEXT NOT NULL, complete INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_runs (
            id TEXT PRIMARY KEY, design_id TEXT NOT NULL, ticket_id TEXT, kind TEXT NOT NULL,
            created REAL NOT NULL, record TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_design_runs ON usage_runs(design_id,created);
            CREATE INDEX IF NOT EXISTS usage_ticket_runs ON usage_runs(ticket_id);''')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='design_sessions'").fetchone():
            db.executescript('''CREATE TRIGGER IF NOT EXISTS usage_source_deleted
                AFTER DELETE ON design_sessions BEGIN
                DELETE FROM usage_runs WHERE design_id=OLD.design_id;
                DELETE FROM usage_designs WHERE design_id=OLD.design_id; END;''')

    def register_usage_design(self, design_id, mode):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO usage_designs VALUES(?,?,?,1)', (design_id, time(), mode))

    def _record_usage(self, db, record, kind='dialogue', ticket_id=None):
        # Do not resurrect deleted/TTL-expired designs when a worker returns late.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='design_sessions'").fetchone():
            if not db.execute('SELECT 1 FROM design_sessions WHERE design_id=?', (record['design_id'],)).fetchone():
                return
        db.execute('INSERT OR IGNORE INTO usage_designs VALUES(?,?,?,0)',
                   (record['design_id'], record['created'], record.get('mode', 'unknown')))
        db.execute('INSERT OR IGNORE INTO usage_runs VALUES(?,?,?,?,?,?)',
                   (record['id'], record['design_id'], ticket_id, kind, record['created'], json.dumps(record)))

    def record_feedback_usage(self, record, ticket_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM feedback_tickets WHERE id=?', (ticket_id,)).fetchone():
                self._record_usage(db, record, 'feedback', ticket_id)

    @staticmethod
    def _summarize_runs(rows):
        records = [dict(json.loads(row['record']), kind=row['kind'] if 'kind' in row.keys() else 'feedback') for row in rows]
        result = summarize([call for record in records for call in record.get('calls', [])],
                           sum(record.get('latency_ms', 0) for record in records))
        result['run_count'] = len(records)
        result['breakdown'], result['stages'] = breakdown(records)
        return result

    def _ticket_usage(self, db, ticket_id):
        result = self._summarize_runs(db.execute('SELECT record FROM usage_runs WHERE ticket_id=?', (ticket_id,)))
        ticket = db.execute('SELECT attempts FROM feedback_tickets WHERE id=?', (ticket_id,)).fetchone()
        self._completeness(result, ticket is not None and result['run_count'] >= ticket['attempts'])
        return result

    @staticmethod
    def _completeness(stats, complete):
        stats['complete'] = bool(complete)
        if not complete:
            for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'total_tokens', 'estimated_cost_usd'):
                stats[key] = None

    def design_usage(self, design_id, through=None):
        with self.connection() as db:
            return self._design_usage(db, design_id, through)

    def _design_usage(self, db, design_id, through=None):
        rows = list(db.execute('SELECT kind,record FROM usage_runs WHERE design_id=? AND (? IS NULL OR created<=?) ORDER BY created,id',
                               (design_id, through, through)))
        result = self._summarize_runs(rows)
        result['dialogue'] = self._summarize_runs([r for r in rows if r['kind'] == 'dialogue'])
        result['feedback'] = self._summarize_runs([r for r in rows if r['kind'] == 'feedback'])
        result['translation'] = self._summarize_runs([r for r in rows if r['kind'] == 'translation'])
        design = db.execute('SELECT complete FROM usage_designs WHERE design_id=?', (design_id,)).fetchone()
        for stats in (result, result['dialogue'], result['feedback'], result['translation']):
            self._completeness(stats, design is not None and design['complete'])
        result['replies'] = [{'id': record['id'], 'revision': record.get('revision'), 'reply_ms': record['latency_ms']}
                             for row in rows if row['kind'] == 'dialogue' for record in [json.loads(row['record'])]]
        last = next((json.loads(row['record']) for row in reversed(rows) if row['kind'] == 'dialogue'), None)
        result['last_dialogue'] = ({'stage': last.get('final_stage') or last.get('initial_stage', 'UNKNOWN'),
                                   'handled_stage': last.get('handled_stage') or last.get('initial_stage', 'UNKNOWN'),
                                   'status': last.get('status', 'unknown'), 'workflow_status': last.get('workflow_status'),
                                   'at': last['created'] + last['latency_ms'] / 1000} if last else None)
        return result

    def usage_run(self, run_id):
        with self.connection() as db:
            row = db.execute('SELECT record FROM usage_runs WHERE id=?', (run_id,)).fetchone()
            return json.loads(row['record']) if row else None

    def usage_inbox(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Invalid usage offset')
        with self.connection() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM usage_designs').fetchone()[0]
            items = []
            for design in db.execute('SELECT * FROM usage_designs ORDER BY created DESC,design_id DESC LIMIT 50 OFFSET ?', (offset,)):
                stats = self._design_usage(db, design['design_id'])
                stats.pop('replies')
                items.append({**dict(design), 'usage': stats})
            return {'designs': items, 'total': total, 'durable': self.durable}
