"""Isolated provider credentials and a stateless DeepSeek JSON adapter."""
from copy import deepcopy
import json
import math
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError

from .model_selection import model_details, model_provider
from .openai_generator import ModelConfigurationError, ModelOutputError, OpenAIResponsesHTTPTransport


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON number')
    return number


def _reject_constant(value):
    raise ValueError('Non-standard JSON constant')


class SchemaCheckedResponse(dict):
    """Validate at extraction time, inside the generator's bounded repair block.

    Usage remains available to telemetry even when model content is rejected.
    Schema and finish state are trusted adapter attributes, not server fields.
    """
    def __init__(self, text, schema, finish, usage):
        super().__init__(output_text=text, usage=usage)
        self.schema, self.finish = schema, finish

    def validate_output(self):
        try:
            text = self.get('output_text')
            if self.finish != 'stop' or not isinstance(text, str) or not text.strip():
                raise ValueError('Incomplete model content')
            Draft202012Validator(self.schema).validate(json.loads(
                text, parse_float=_finite_float, parse_constant=_reject_constant))
        except (TypeError, ValueError, ValidationError) as exc:
            raise ModelOutputError('DeepSeek returned incomplete or schema-invalid JSON') from exc


class DeepSeekJSONTransport:
    """Translate our internal Responses envelope to documented Chat Completions.

    DeepSeek JSON mode guarantees syntax only. Check the original JSON Schema
    locally before any output can reach intent handling or experience learning.
    The generator's existing repair limits remain the only retry mechanism.
    """
    def __init__(self, api_key, base_url='https://api.deepseek.com', timeout_seconds=90,
                 max_output_tokens=8192, http_transport=None):
        if not api_key.strip():
            raise ModelConfigurationError('DEEPSEEK_API_KEY is required')
        url = urlsplit(base_url.strip())
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip('/') not in ('', '/v1')):
            raise ModelConfigurationError('DEEPSEEK_BASE_URL must be an HTTPS base URL, optionally ending in /v1')
        self.max_output_tokens = max_output_tokens
        self.http = http_transport or OpenAIResponsesHTTPTransport(
            api_key, timeout_seconds, endpoint=base_url.strip().rstrip('/') + '/chat/completions', provider='DeepSeek')

    def create(self, payload):
        details = model_details(payload['model'], payload.get('reasoning', {}).get('effort', 'medium'))
        if details['provider'] != 'deepseek':
            raise ModelConfigurationError('DeepSeek transport cannot receive another provider model')
        if payload.get('previous_response_id'):
            raise ModelConfigurationError('DeepSeek requires explicit local history, not a response ID')
        schema = payload.get('text', {}).get('format', {}).get('schema')
        if not isinstance(schema, dict):
            raise ModelConfigurationError('DeepSeek workflow calls require a JSON Schema')
        instructions = str(payload.get('instructions', '')) + '\nReturn only a JSON object matching this JSON Schema:\n' + json.dumps(schema, ensure_ascii=False)
        messages = [{'role': 'system', 'content': instructions}]
        entries = payload.get('input', [])
        if isinstance(entries, str):
            entries = [{'role': 'user', 'content': entries}]
        for entry in entries:
            content = entry.get('content', '')
            if isinstance(content, list):
                if any(part.get('type') not in ('input_text', 'output_text') for part in content):
                    raise ModelConfigurationError('Workflow DeepSeek adapter currently accepts text only')
                content = '\n'.join(part['text'] for part in content)
            messages.append({'role': entry.get('role', 'user'), 'content': content})
        effort = details['reasoning']
        budget = max(payload.get('max_output_tokens', 0), self.max_output_tokens)
        if not 1 <= budget <= 384000:
            raise ModelConfigurationError('DeepSeek output token budget must be between 1 and 384000')
        request = {'model': details['api_model'], 'messages': messages, 'stream': False,
                   'response_format': {'type': 'json_object'},
                   'thinking': {'type': 'disabled' if effort == 'none' else 'enabled'},
                   'max_tokens': budget}
        if effort != 'none':
            request['reasoning_effort'] = effort
        response = self.http.create(request)
        try:
            choice = response['choices'][0]
            text = choice['message']['content']
            finish = choice.get('finish_reason')
        except (KeyError, IndexError, TypeError, AttributeError):
            text, finish = None, None
        usage = response.get('usage') if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        # Return final content only, never reasoning_content or a remote chain ID.
        return SchemaCheckedResponse(text, schema, finish, {
            'input_tokens': usage.get('prompt_tokens'), 'output_tokens': usage.get('completion_tokens'),
            'input_tokens_details': {'cached_tokens': usage.get('prompt_cache_hit_tokens')},
        })


class ProviderResponsesTransport:
    """Dispatch every call, including feedback extraction, by the selected ID."""
    def __init__(self, openai=None, deepseek=None):
        self.providers = {'openai': openai, 'deepseek': deepseek}

    def create(self, payload):
        provider = model_provider(payload.get('model'))
        transport = self.providers[provider]
        if transport is None:
            raise ModelConfigurationError(f'{provider.upper()}_API_KEY is not configured')
        return transport.create(deepcopy(payload))
