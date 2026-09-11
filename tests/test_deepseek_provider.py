from copy import deepcopy
from io import BytesIO
import json

import pytest

from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.experience import ModelExperienceExtractor
from ece329_workflow.model_selection import catalogue, DEEPSEEK_MODELS, OPENAI_MODELS
from ece329_workflow.model_routing import ModelRouter
from ece329_workflow.models import InteractionState
from ece329_workflow.openai_generator import (
    generator_from_environment, OpenAIStageGenerator, OpenAIResponsesHTTPTransport,
    ModelConfigurationError, ModelOutputError,
    _extract_output_text,
)
from ece329_workflow.provider_transport import DeepSeekJSONTransport, ProviderResponsesTransport
from tests.test_model_selection import ModelTransport, add_session, QUESTION
from tests.test_feedback_pipeline import candidate


class ChatFixture:
    """Actual Chat Completions wire shape, behind the real DeepSeek adapter."""
    def __init__(self):
        self.requests = []
        self.inner = ModelTransport()

    def create(self, payload):
        self.requests.append(deepcopy(payload))
        schema = json.loads(payload['messages'][0]['content'].split('JSON Schema:\n')[-1])
        properties = schema['properties']
        if 'useful' in properties:
            text = json.dumps(candidate())
        else:
            name = ('ece329_context_intent' if 'resolved_value_json' in properties else
                    'ece329_compact_dialogue_acts' if 'actions' in properties else 'ece329_stage_output')
            text = self.inner.create({'model': payload['model'], 'text': {'format': {'name': name}}})['output_text']
        return {'id': 'chat-not-a-responses-chain',
                'choices': [{'finish_reason': 'stop', 'message': {'content': text, 'reasoning_content': 'private reasoning'}}],
                'usage': {'prompt_tokens': 120, 'completion_tokens': 32, 'prompt_cache_hit_tokens': 40}}


def mixed_engine():
    chat, openai = ChatFixture(), ModelTransport()
    dispatch = ProviderResponsesTransport(openai, DeepSeekJSONTransport('fixture-deepseek', http_transport=chat))
    generator = OpenAIStageGenerator(transport=dispatch, stateful=True, allowed_models=(*OPENAI_MODELS, *DEEPSEEK_MODELS))
    return WorkflowEngine(generator=generator), openai, chat


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('selected', list(DEEPSEEK_MODELS))
def test_both_modes_route_all_deepseek_presets_and_restore_history(mode, selected):
    engine, openai, chat = mixed_engine()
    session = add_session(engine, mode)
    response = engine.process_turn(session.design_id, {'message': QUESTION, 'model': selected, 'turn_id': 'deepseek-turn-001'})
    assert response['selected_model'] == selected
    assert not openai.requests and len(chat.requests) >= 2
    assert {r['model'] for r in chat.requests} == {DEEPSEEK_MODELS[selected][0]}
    assert all('previous_response_id' not in r and 'store' not in r for r in chat.requests)
    assert '保留切换模型前的观察要求' in json.dumps(chat.requests, ensure_ascii=False)
    assert 'openai_previous_response_id' not in engine.store.get(session.design_id).model_context
    thinking = 'disabled' if selected.endswith(':fast') else 'enabled'
    assert all(r['thinking']['type'] == thinking for r in chat.requests)
    before = len(chat.requests)
    engine.process_turn(session.design_id, {'message': QUESTION, 'model': selected, 'turn_id': 'deepseek-turn-001'})
    assert len(chat.requests) == before
    telemetry = engine.telemetry_records(session.design_id)[0]
    assert all(c['provider'] == 'deepseek' and c['model_id'] == DEEPSEEK_MODELS[selected][0] for c in telemetry['calls'])
    assert telemetry['input_tokens'] == 120 * len(chat.requests)
    assert all(c['cached_input_tokens'] == 40 for c in telemetry['calls'])
    assert 'private reasoning' not in json.dumps(response)
    engine.process_turn(session.design_id, {'message': QUESTION, 'model': 'gpt-5.5', 'turn_id': 'back-openai-001'})
    assert openai.requests and all(r['model'] == 'gpt-5.5' for r in openai.requests)
    assert all('previous_response_id' not in r for r in openai.requests)


def test_deepseek_only_and_mixed_environment_catalogue_do_not_expose_unconfigured_models():
    deepseek = generator_from_environment({'DEEPSEEK_API_KEY': 'ds-secret', 'ECE329_OPENAI_FALLBACK': 'false'})
    assert deepseek.model == 'deepseek-flash' and not deepseek.stateful
    assert set(m['id'] for m in catalogue(deepseek)['models']) == set(DEEPSEEK_MODELS)
    assert deepseek.runtime_info()['provider'] == 'deepseek'
    assert ModelRouter(deepseek, {}).registry['reasoning']['model'] == 'deepseek-v4-pro'
    mixed = generator_from_environment({'OPENAI_API_KEY': 'oa-secret', 'DEEPSEEK_API_KEY': 'ds-secret',
                                        'ECE329_OPENAI_FALLBACK': 'false'})
    assert len(catalogue(mixed)['models']) == 11
    assert 'secret' not in json.dumps(catalogue(mixed))
    explicit = generator_from_environment({'OPENAI_API_KEY': 'oa-secret', 'ECE329_OPENAI_FALLBACK': 'false',
        'ECE329_ALLOWED_MODELS': 'gpt-5.4-mini,deepseek-flash'})
    assert [m['id'] for m in catalogue(explicit)['models']] == ['gpt-5.4-mini']


@pytest.mark.parametrize('env', [
    {'ECE329_GENERATOR': 'deepseek'},
    {'ECE329_GENERATOR': 'openai', 'DEEPSEEK_API_KEY': 'ds'},
    {'DEEPSEEK_API_KEY': 'ds', 'DEEPSEEK_MODEL': 'deepseek-chat'},
    {'DEEPSEEK_API_KEY': 'ds', 'DEEPSEEK_BASE_URL': 'http://example.com'},
    {'DEEPSEEK_API_KEY': 'ds', 'DEEPSEEK_MAX_OUTPUT_TOKENS': '400000'},
    {'DEEPSEEK_API_KEY': 'ds', 'ECE329_ALLOWED_MODELS': 'gpt-5.5'},
])
def test_invalid_provider_configuration_fails_before_requests(env):
    with pytest.raises(ModelConfigurationError):
        generator_from_environment(env)


def test_http_requests_use_separate_hosts_and_credentials(monkeypatch):
    requests = []
    def respond(request, timeout):
        requests.append(request)
        return BytesIO(json.dumps({'choices': [{'finish_reason':'stop','message': {'content':'{"answer": 1}'}}]}).encode())
    monkeypatch.setattr('ece329_workflow.openai_generator.urlopen', respond)
    dispatch = ProviderResponsesTransport(OpenAIResponsesHTTPTransport('openai-only'), DeepSeekJSONTransport('deepseek-only'))
    payload = {'model':'deepseek-flash:fast','input':'test', 'max_output_tokens':1500,
               'text':{'format':{'schema':{'type':'object','properties':{'answer':{'type':'integer'}},'required':['answer'],'additionalProperties':False}}}}
    dispatch.create(payload)
    dispatch.create({'model':'gpt-5.5','input':'test'})
    assert requests[0].full_url == 'https://api.deepseek.com/chat/completions'
    assert requests[0].get_header('Authorization') == 'Bearer deepseek-only'
    assert requests[1].full_url == 'https://api.openai.com/v1/responses'
    assert requests[1].get_header('Authorization') == 'Bearer openai-only'
    assert 'deepseek-only' not in requests[1].data.decode()


@pytest.mark.parametrize('content,finish', [('{}','stop'), ('not JSON','stop'), ('{"answer":true}','stop'), ('{"answer":1}','length')])
def test_deepseek_rejects_missing_fields_wrong_types_and_truncation(content, finish):
    class Bad:
        def create(self, _): return {'choices':[{'finish_reason':finish,'message':{'content':content}}]}
    transport = DeepSeekJSONTransport('fixture', http_transport=Bad())
    with pytest.raises(ModelOutputError):
        _extract_output_text(transport.create({'model':'deepseek-flash','input':'test','text':{'format':{'schema':{
            'type':'object','required':['answer'],'properties':{'answer':{'type':'integer'}}}}}}))


def test_feedback_extraction_uses_deepseek_without_openai_key():
    chat = ChatFixture()
    generator = OpenAIStageGenerator(model='deepseek-flash:fast', transport=ProviderResponsesTransport(
        deepseek=DeepSeekJSONTransport('fixture', http_transport=chat)))
    result = ModelExperienceExtractor(generator).extract({'message':'重复询问', 'evidence':{}})
    assert result['useful'] and len(chat.requests) == 1
    assert chat.requests[0]['model'] == 'deepseek-flash'
    assert chat.requests[0]['thinking']['type'] == 'disabled'


@pytest.mark.parametrize('number', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_nonfinite_json_numbers_are_rejected_before_domain_parsing(number):
    from ece329_workflow.provider_transport import SchemaCheckedResponse
    response = SchemaCheckedResponse('{"answer":' + number + '}',
        {'type': 'object', 'properties': {'answer': {'type': 'number'}}}, 'stop', {})
    with pytest.raises(ModelOutputError):
        _extract_output_text(response)


@pytest.mark.parametrize('usage', [None, [], 'invalid', 42])
def test_malformed_optional_usage_does_not_discard_valid_content(usage):
    class Response:
        def create(self, payload):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":1}'}}], 'usage': usage}
    transport = DeepSeekJSONTransport('fixture', http_transport=Response())
    response = transport.create({'model': 'deepseek-flash', 'input': 'test',
        'text': {'format': {'schema': {'type': 'object', 'properties': {'answer': {'type': 'integer'}}}}}})
    assert json.loads(_extract_output_text(response)) == {'answer': 1}
    assert response['usage']['input_tokens'] is None


def test_schema_rejection_enters_existing_single_repair_and_preserves_usage():
    engine, _, chat = mixed_engine()
    session = add_session(engine)
    original = chat.create
    rejected = []
    def fail_once(payload):
        result = original(payload)
        schema = json.loads(payload['messages'][0]['content'].split('JSON Schema:\n')[-1])
        if 'assistant_message' in schema['properties'] and not rejected:
            rejected.append(True)
            result['choices'][0]['message']['content'] = '{}'
        return result
    chat.create = fail_once
    result = engine.process_turn(session.design_id, {'message':QUESTION,'model':'deepseek-flash:fast'})
    assert result['selected_model'] == 'deepseek-flash:fast'
    assert engine.generator_info()['repair_successes'] == 1
    trace = engine.telemetry_records(session.design_id)[0]
    assert trace['input_tokens'] == len(chat.requests)*120
    assert trace['retry_count'] == 1
