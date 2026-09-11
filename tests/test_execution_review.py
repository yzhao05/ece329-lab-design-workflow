from types import SimpleNamespace

import pytest

from ece329_workflow.generation_policy import TurnBudget
from ece329_workflow.model_routing import ModelRouter, RoutedGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.openai_generator import OpenAIStageGenerator, ModelOutputError
from ece329_workflow.telemetry import ObservedTransport, CURRENT_TRACE
from ece329_workflow.localization import validate_translation_references


@pytest.mark.parametrize('usage', [None, False, 42, 'unknown', [], {'input_tokens_details':[]},
    {'input_tokens':True,'output_tokens':False,'input_tokens_details':'invalid'}])
def test_bad_optional_usage_keeps_valid_reply_and_conservative_budget(usage):
    response = {'output_text':'{"valid": true}', 'usage':usage}
    session = SimpleNamespace(current_stage=Stage.HYPOTHESIS, turn_context={})
    budget = TurnBudget({'turn_output_tokens':1000,'max_model_calls':2})
    trace = SimpleNamespace(data={'calls':[]})
    token = CURRENT_TRACE.set(trace)
    try:
        transport = ObservedTransport(SimpleNamespace(create=lambda _:response), session,
                                      {'profile':'balanced','max_output_tokens':500},budget)
        assert transport.create({'model':'gpt-5.4-mini'}) is response
    finally:
        CURRENT_TRACE.reset(token)
    assert budget.charged == 500
    assert trace.data['calls'][0]['output_tokens'] is None
    assert trace.data['calls'][0]['error_type'] is None


@pytest.mark.parametrize('effort', ['none','max'])
def test_adaptive_retry_honors_target_model_effort_and_state(effort):
    calls = []
    class RetryGenerator(OpenAIStageGenerator):
        def generate(self, session, message):
            self.transport.create({'model':self.model,'reasoning':{'effort':self.reasoning_effort}})
            calls.append((self.model,self.reasoning_effort))
            if len(calls) == 1:
                raise ModelOutputError('Reject initial model output')
            return 'recovered'
    base = RetryGenerator(transport=SimpleNamespace(create=lambda _: {'usage':{'output_tokens':10}}))
    router = ModelRouter(base, {})
    config = router.validate({'strategy':'fast','adaptive_enabled':True,
                              'reasoning_overrides':{'gpt-5.6-sol':effort}})
    session = DesignSession('retry-review',InteractionState.EMVR_DIRECT)
    runner = RoutedGenerator(base,router,config,session.current_stage.value)
    assert runner.generate(session,'try') == 'recovered'
    assert calls == [('gpt-5.4-mini','low'),('gpt-5.6-sol',effort)]
    assert session.model_context['selected_reasoning'] == effort
    assert session.turn_context['model_route']['reasoning'] == effort
    assert runner.budget.calls == 2 and runner.budget.charged == 20


@pytest.mark.parametrize('translated', [
    'Use Assets/Other.cs, value 0.05, `InitializeOnce()` and \\(E=q/r^2\\).',
    'Use Assets/Field.cs, value 0.5, `InitializeOnce()` and \\(E=q/r^2\\).',
    'Use Assets/Field.cs, value 0.05, `Initialize()` and \\(E=q/r^2\\).',
    'Use Assets/Field.cs, value 0.05, `InitializeOnce()` and \\(E=q/r^3\\).',
])
def test_translation_rejects_broken_builder_references_and_values(translated):
    with pytest.raises(ValueError):
        validate_translation_references('使用 Assets/Field.cs，值 0.05，`InitializeOnce()` 和 \\(E=q/r^2\\)。', translated)


def test_translation_keeps_reordered_prose_with_literal_references():
    validate_translation_references('使用 Assets/Field.cs，值 0.05，`InitializeOnce()` 和 \\(E=q/r^2\\)。',
        'Value 0.05. Use `InitializeOnce()` in Assets/Field.cs with \\(E=q/r^2\\).')
    validate_translation_references('调用 `OBJ_01`，打开 `Assets/Field.cs`。',
                                    'Use `OBJ_01` and open `Assets/Field.cs`.')


def test_translation_cannot_invert_numeric_polarity():
    with pytest.raises(ValueError):
        validate_translation_references('电荷 −1e-9 C。', 'Charge +1e-9 C.')
    validate_translation_references('电荷 −1e-9 C。', 'Charge -1e-9 C.')
