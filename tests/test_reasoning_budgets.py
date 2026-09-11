from copy import deepcopy

import pytest

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.generation_policy import GenerationPolicy, TurnBudget, TurnBudgetExceeded, supported_efforts
from ece329_workflow.model_routing import ModelRouter
from ece329_workflow.model_selection import OPENAI_MODELS, DEEPSEEK_MODELS
from ece329_workflow.models import InteractionState, Stage
from ece329_workflow.security import APISettings
from tests.test_deepseek_provider import mixed_engine
from tests.test_model_selection import add_session, QUESTION
from tests.test_security_and_store import call_api


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('model', (*OPENAI_MODELS, *DEEPSEEK_MODELS))
def test_each_model_effort_reaches_wire_and_changes_budget(mode, model):
    engine, openai, chat = mixed_engine()
    session = add_session(engine, mode)
    router = ModelRouter(engine.generator, {})
    for index, effort in enumerate(supported_efforts(model)):
        config = router.validate({'strategy':'custom', 'model_override':model,
                                  'reasoning_overrides':{model:effort}})
        route = router.resolve(session.current_stage.value, config)
        before = len(chat.requests if model.startswith('deepseek') else openai.requests)
        result = engine.process_turn(session.design_id, {'message':QUESTION,
            'turn_id':f'effort-{index:03d}-{model.replace(":", "-")}', 'model_config':config})
        records = (chat.requests if model.startswith('deepseek') else openai.requests)[before:]
        assert records
        for record in records:
            if model.startswith('deepseek'):
                assert record['thinking']['type'] == ('disabled' if effort == 'none' else 'enabled')
                assert record.get('reasoning_effort') == (None if effort == 'none' else effort)
                assert record['max_tokens'] == route['max_output_tokens']
            else:
                assert record['reasoning']['effort'] == effort
                assert record['max_output_tokens'] == route['max_output_tokens']
                assert '_workflow_output_cap' not in record
        assert result['model_config']['reasoning_overrides'][model] == effort
        replay_count = len(chat.requests) + len(openai.requests)
        engine.process_turn(session.design_id, {'message':QUESTION,
            'turn_id':f'effort-{index:03d}-{model.replace(":", "-")}', 'model_config':config})
        assert len(chat.requests) + len(openai.requests) == replay_count


@pytest.mark.parametrize('model,effort', [('gpt-5.4-mini','max'), ('deepseek-flash','medium'),
                                        ('gpt-5.5',True), ('not-enabled','high')])
def test_invalid_effort_rejected_without_mutation(model, effort):
    engine, openai, chat = mixed_engine()
    session = add_session(engine)
    before = deepcopy(engine.store.get(session.design_id))
    with pytest.raises(ValueError):
        engine.process_turn(session.design_id, {'message':QUESTION,
            'model_config':{'reasoning_overrides':{model:effort}}})
    assert not openai.requests and not chat.requests
    assert engine.store.get(session.design_id) == before


def test_server_caps_and_model_effort_monotonicity():
    engine, _, _ = mixed_engine()
    policy = GenerationPolicy(engine.generator, {})
    assert policy.budget('gpt-5.4-mini','high')['max_output_tokens'] > policy.budget('gpt-5.4-mini','low')['max_output_tokens']
    assert policy.budget('gpt-5.5','high')['max_output_tokens'] > policy.budget('gpt-5.4-mini','high')['max_output_tokens']
    capped = GenerationPolicy(engine.generator, {'ECE329_MAX_OUTPUT_TOKENS_PER_CALL':'9000', 'ECE329_MAX_OUTPUT_TOKENS_PER_TURN':'10000'})
    for spec in capped.models.values():
        for value in spec['budgets'].values():
            assert value['max_output_tokens'] <= 9000 and value['turn_output_tokens'] <= 10000
    for name in ('ECE329_MAX_OUTPUT_TOKENS_PER_CALL','ECE329_MAX_OUTPUT_TOKENS_PER_TURN','ECE329_MAX_MODEL_CALLS_PER_TURN'):
        for invalid in ('0', '-1', 'nan', True):
            with pytest.raises(ValueError): GenerationPolicy(engine.generator, {name:invalid})


def test_uncertain_usage_and_repairs_share_one_budget():
    budget = TurnBudget({'turn_output_tokens':100, 'max_model_calls':3})
    assert budget.reserve(80) == 80
    budget.settle(80, 20)
    assert budget.reserve(90) == 80
    budget.settle(80, None)
    with pytest.raises(TurnBudgetExceeded): budget.reserve(1)
    calls = TurnBudget({'turn_output_tokens':1000, 'max_model_calls':1})
    reserved = calls.reserve(100)
    calls.settle(reserved, 1)
    with pytest.raises(TurnBudgetExceeded): calls.reserve(1)


@pytest.mark.parametrize('fallback', [False, True])
def test_budget_stop_is_terminal_and_owner_scoped(monkeypatch, fallback):
    engine, openai, chat = mixed_engine()
    if fallback:
        from ece329_workflow.openai_generator import FallbackStageGenerator
        from ece329_workflow.generator import RuleBasedStageGenerator
        engine.generator = FallbackStageGenerator(primary=engine.generator, fallback=RuleBasedStageGenerator())
    session = add_session(engine)
    api = WorkflowAPI(engine, APISettings())
    monkeypatch.setenv('ECE329_MAX_MODEL_CALLS_PER_TURN', '1')
    path = f'/v1/designs/{session.design_id}/turns'
    body = {'message':QUESTION, 'turn_id':'budget-terminal-01',
            'model_config':{'strategy':'custom','model_override':'gpt-5.4-mini','reasoning_overrides':{'gpt-5.4-mini':'high'}}}
    assert call_api(api, 'POST', path, body)[0].startswith('401')
    result = call_api(api, 'POST', path, body, request_headers={'Authorization':'Bearer model-owner'})
    assert result[0].startswith('409')
    assert result[2]['error'] == 'model_budget_exceeded' and result[2]['retryable'] is False
    assert len(openai.requests) == 1 and not chat.requests


def test_explicit_effort_prevents_adaptive_override():
    engine, _, _ = mixed_engine()
    router = ModelRouter(engine.generator, {})
    router.failure_counts[Stage.THEORETICAL_FRAMEWORK.value] = 5
    config = router.validate({'strategy':'fast','adaptive_enabled':True,'reasoning_overrides':{'gpt-5.4-mini':'none'}})
    route = router.resolve(Stage.THEORETICAL_FRAMEWORK.value, config)
    assert route['reasoning'] == 'none' and 'adaptive_reason' not in route


def test_switching_from_max_profile_to_older_model_uses_supported_default():
    engine, _, _ = mixed_engine()
    router = ModelRouter(engine.generator, {})
    router.registry['balanced'] = {'model':'gpt-5.6-sol', 'reasoning':'max'}
    config = router.validate({'strategy':'custom','model_override':'gpt-5.5'})
    route = router.resolve(Stage.THEORETICAL_FRAMEWORK.value,config)
    assert route['reasoning'] == 'xhigh' and route['max_output_tokens'] > 0


def test_deepseek_floor_cannot_raise_remaining_turn_budget():
    from types import SimpleNamespace
    from ece329_workflow.provider_transport import DeepSeekJSONTransport
    from ece329_workflow.telemetry import ObservedTransport
    requests = []
    class Chat:
        def create(self, payload):
            requests.append(payload)
            return {'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
                    'usage':{'completion_tokens':900 if len(requests)==1 else 150}}
    budget = TurnBudget({'turn_output_tokens':1050,'max_model_calls':4})
    session = SimpleNamespace(current_stage=Stage.THEORETICAL_FRAMEWORK,turn_context={})
    transport = ObservedTransport(DeepSeekJSONTransport('fixture',max_output_tokens=8192,http_transport=Chat()),
                                 session,{'profile':'balanced','max_output_tokens':1000},budget)
    payload = {'model':'deepseek-flash:fast','reasoning':{'effort':'max'},
               'text':{'format':{'schema':{'type':'object'}}}}
    transport.create(payload)
    transport.create(payload)
    with pytest.raises(TurnBudgetExceeded): transport.create(payload)
    assert [r['max_tokens'] for r in requests] == [1000,150]
    assert all(r['reasoning_effort']=='max' for r in requests)


def test_saved_effort_does_not_block_rule_mode_recovery():
    from ece329_workflow.generator import RuleBasedStageGenerator
    engine, _, _ = mixed_engine()
    session = add_session(engine)
    session.model_context['model_config'] = ModelRouter(engine.generator, {}).validate({
        'reasoning_overrides':{'gpt-5.4-mini':'high'}})
    engine.store.save(session)
    engine.generator = RuleBasedStageGenerator()
    result = engine.process_turn(session.design_id, {'message':QUESTION,'turn_id':'offline-effort-001'})
    assert result['model_config']['reasoning_overrides'] == {}
