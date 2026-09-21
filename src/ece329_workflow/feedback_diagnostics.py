"""Safe, deterministic diagnostics. Never retain request/response bodies here."""
from copy import deepcopy
import os
import re


class FeedbackValidationError(ValueError):
    def __init__(self, field_path, reason, **details):
        super().__init__('Feedback field validation failed')
        self.diagnostic = {'version': 1, 'source': 'local_validation',
                           'code': 'schema_validation_failed', 'reason': reason,
                           'field_path': field_path, **details}
        self.feedback_reason = 'schema_validation'


def validate(value, schema, path='', limit=1000):
    kind = schema['type']
    def fail(reason, **details):
        raise FeedbackValidationError(path or '$', reason, **details)
    types = {'object': dict, 'array': list, 'boolean': bool, 'string': str}
    if kind not in types:
        raise RuntimeError('Unsupported feedback schema type')
    if not isinstance(value, types[kind]):
        fail('type', expected_type=kind)
    if kind == 'object':
        properties = schema['properties']
        for key in schema.get('required', properties):
            if key not in value:
                raise FeedbackValidationError(f'{path}.{key}' if path else key, 'required')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            fail('additional_properties')  # Unknown field names can contain secrets.
        for key, spec in properties.items():
            if key in value:
                validate(value[key], spec, f'{path}.{key}' if path else key, limit)
    elif kind == 'array':
        for rule, name, default in [('minItems', 'min_items', 0), ('maxItems', 'max_items', 6)]:
            bound = schema.get(rule, default)
            if (rule == 'minItems' and len(value) < bound) or (rule == 'maxItems' and len(value) > bound):
                fail(name, limit=bound, actual_length=len(value))
        for index, item in enumerate(value):
            validate(item, schema['items'], f'{path}[{index}]', limit)
    elif kind == 'string':
        if not value.strip() or len(value) < schema.get('minLength', 1):
            fail('min_length', limit=schema.get('minLength', 1), actual_length=len(value))
        if len(value) > schema.get('maxLength', limit):
            fail('max_length', limit=schema.get('maxLength', limit), actual_length=len(value))
    if 'enum' in schema and value not in schema['enum']:
        fail('enum')
    return deepcopy(value)


PROVIDER_CODES = frozenset(('insufficient_quota', 'insufficient_balance', 'invalid_api_key',
    'rate_limit_exceeded', 'rate_limit_error', 'context_length_exceeded', 'invalid_request_error',
    'model_not_found', 'permission_denied', 'authentication_error', 'server_error',
    'service_unavailable', 'invalid_parameter', 'invalid_value', 'invalid_json'))


def safe_provider_code(value):
    return value if isinstance(value, str) and value in PROVIDER_CODES else None


def transport_metadata(headers=None, status=None, secret=''):
    request_id = None
    if headers:
        value = headers.get('x-request-id') or headers.get('request-id')
        if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,160}', value)
                and not value.lower().startswith(('sk-', 'sk_', 'bearer'))
                and (not secret or secret not in value)):
            request_id = value
    return {'http_status': status if type(status) is int else None, 'request_id': request_id}


class TransportResponse(dict):
    """Out-of-band HTTP metadata, never injected into prompts or model JSON."""
    pass


def release_version():
    value = os.environ.get('ECE329_RELEASE_VERSION') or os.environ.get('RENDER_GIT_COMMIT') or os.environ.get('GITHUB_SHA')
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,80}', value) and not value.startswith('sk-') else None


def mark_phase(trace, phase):
    if trace is None:
        return
    phases = trace.setdefault('phases', [])
    if phases and phases[-1]['status'] == 'running':
        phases[-1]['status'] = 'completed'
    trace['phase'] = phase
    phases.append({'phase': phase, 'status': 'running'})


def evidence_was_truncated(evidence):
    """Only inspect recorded flags at known evidence locations, never text."""
    if not isinstance(evidence, dict):
        return False
    turns = [evidence.get('reported_turn')]
    for key in ('event_chain', 'recent_turns'):
        rows = evidence.get(key)
        if isinstance(rows, list):
            turns.extend(rows)
    states = [evidence.get('current_state')]
    for turn in turns:
        if isinstance(turn, dict):
            if turn.get('truncated_fields'):
                return True
            states.extend([turn.get('state_before'), turn.get('state_after')])
    return bool(evidence.get('field_excerpt_truncated')) or any(
        state.get('pending_excerpt_truncated') is True for state in states if isinstance(state, dict))


def diagnostic_for(exc):
    from .openai_generator import ModelHTTPError, ModelTimeoutError, ModelConnectionError, ModelConfigurationError, ModelOutputError
    if isinstance(exc, FeedbackValidationError):
        return dict(exc.diagnostic)
    base = {'version': 1, 'source': 'backend', 'code': 'backend_processing_error', 'reason': 'unexpected_exception'}
    if isinstance(exc, ModelHTTPError):
        code = safe_provider_code(exc.error_code)
        reason = ('quota_exhausted' if code in ('insufficient_quota', 'insufficient_balance') else
                  'input_limit' if code == 'context_length_exceeded' else
                  'rate_limited' if exc.status_code == 429 else
                  'authentication' if exc.status_code == 401 else
                  'permission' if exc.status_code == 403 else
                  'payment_required' if exc.status_code == 402 else
                  'timeout' if exc.status_code in (408, 504) else
                  'provider_unavailable' if exc.status_code >= 500 else 'unknown')
        base.update(source='provider_http', code='request_failed', reason=reason,
                    http_status=exc.status_code, provider_error_code=code)
    elif isinstance(exc, (ModelTimeoutError, ModelConnectionError, ModelConfigurationError)):
        base.update(source='transport' if not isinstance(exc, ModelConfigurationError) else 'configuration',
                    code='request_failed', reason='timeout' if isinstance(exc, ModelTimeoutError) else
                    'connection' if isinstance(exc, ModelConnectionError) else 'configuration')
    elif isinstance(exc, ModelOutputError):
        reason = getattr(exc, 'feedback_reason', None) or 'unknown'
        base.update(source='local_parser', code='response_invalid', reason=reason)
        if reason == 'evidence_reference':
            base.update(source='local_validation', code='evidence_reference_invalid', field_path='diagnosis.facts')
    base.update(getattr(exc, 'transport_metadata', {}))
    return base
