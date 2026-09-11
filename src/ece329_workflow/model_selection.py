"""Server-owned model catalogue and isolated, reusable per-model generators."""
from dataclasses import replace
import re

MODEL_LABELS = {
    'gpt-5.4-mini': 'GPT 5.4-mini（recommend）',
    'gpt-5.4': 'GPT 5.4',
    'gpt-5.4-nano': 'GPT 5.4-nano',
    'gpt-5.5': 'GPT 5.5',
    'gpt-5.6-sol': 'GPT 5.6 Sol',
    'gpt-5.6-terra': 'GPT 5.6 Terra',
    'gpt-5.6-luna': 'GPT 5.6 Luna',
}
OPENAI_MODELS = tuple(MODEL_LABELS)
# IDs with ':' are application presets, never names sent to DeepSeek.
DEEPSEEK_MODELS = {
    'deepseek-flash': ('deepseek-flash', None, 'DeepSeek Flash（recommend）'),
    'deepseek-flash:fast': ('deepseek-flash', 'none', 'DeepSeek Flash（快速预设）'),
    'deepseek-flash:reasoning': ('deepseek-flash', 'high', 'DeepSeek Flash（深度思考预设）'),
    'deepseek-v4-pro': ('deepseek-v4-pro', None, 'DeepSeek V4 Pro'),
}
MODEL_LABELS.update({key: value[2] for key, value in DEEPSEEK_MODELS.items()})


def model_provider(model):
    return 'deepseek' if isinstance(model, str) and model.startswith('deepseek-') else 'openai'


def model_details(model, reasoning='medium'):
    provider = model_provider(model)
    if provider == 'deepseek':
        if model not in DEEPSEEK_MODELS:
            raise ValueError('Unsupported DeepSeek model; use deepseek-flash or deepseek-v4-pro')
        api_model, preset, _ = DEEPSEEK_MODELS[model]
        effort = preset or ('high' if reasoning in ('medium', 'xhigh') else reasoning)
        return {'provider': provider, 'api_model': api_model, 'reasoning': effort, 'stateful': False}
    return {'provider': provider, 'api_model': model, 'reasoning': reasoning, 'stateful': True}


def configured_models(default, allowed=None):
    defaults = tuple(DEEPSEEK_MODELS) if model_provider(default) == 'deepseek' else OPENAI_MODELS
    values = tuple(dict.fromkeys(allowed if allowed is not None else (*defaults, default)))
    if not values or len(values) > 12 or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', v) for v in values):
        raise ValueError('ECE329_ALLOWED_MODELS must contain 1-12 valid model IDs')
    if default not in values:
        raise ValueError('Default model must be included in ECE329_ALLOWED_MODELS')
    for model in values:
        model_details(model)
    return values


def primary_generator(generator):
    from .openai_generator import OpenAIStageGenerator
    primary = getattr(generator, 'primary', generator)
    return primary if isinstance(primary, OpenAIStageGenerator) else None


def catalogue(generator):
    primary = primary_generator(generator)
    if primary is None:
        return {'enabled': False, 'default_model': None, 'models': []}
    return {'enabled': True, 'default_model': primary.model,
            'models': [{'id': model, 'label': MODEL_LABELS.get(model, model),
                        'provider': model_provider(model), 'api_model': model_details(model)['api_model'],
                        'preset_reasoning': DEEPSEEK_MODELS[model][1] if model in DEEPSEEK_MODELS else None,
                        'recommended': model in ('gpt-5.4-mini', 'deepseek-flash')} for model in primary.allowed_models]}


def validate_model(generator, model):
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError('model must be a non-empty model ID')
    primary = primary_generator(generator)
    if primary is None:
        if model is not None:
            raise ValueError('Model selection requires a configured online generator')
        return None
    model = primary.model if model is None else model
    if model not in primary.allowed_models:
        raise ValueError('Selected model is not enabled; refresh the model list and select an allowed model')
    return model


def generator_for_model(generator, model):
    from .openai_generator import FallbackStageGenerator
    model = validate_model(generator, model)
    primary = primary_generator(generator)
    if primary is None or primary.model == model:
        return generator
    # Clone config, never mutate the shared model field. All intent recovery and
    # output repair calls now use the same model as the main response.
    selected = replace(primary, model=model)
    return FallbackStageGenerator(primary=selected, fallback=generator.fallback) if isinstance(generator, FallbackStageGenerator) else selected
