"""Server-owned capability registry; session preferences never replace validators."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from time import perf_counter

from .models import Stage
from .model_selection import primary_generator, validate_model, model_details, model_provider
from .telemetry import CURRENT_TRACE, ObservedTransport

PROFILES = ('fast', 'balanced', 'reasoning')
STRATEGIES = ('recommended', 'fast', 'quality', 'custom')
STAGE_POLICY = {s.value: ('reasoning' if s in {
    Stage.THEORETICAL_FRAMEWORK, Stage.HYPOTHESIS, Stage.EXPECTED_DATA_VISUALIZATION,
    Stage.RESULT_INTERPRETATION} else 'fast' if s in {
    Stage.IDEA_BRAINSTORMING, Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT} else 'balanced') for s in Stage}


class ModelRouter:
    def __init__(self, generator, environ=None):
        env = os.environ if environ is None else environ
        primary = primary_generator(generator)
        default = primary.model if primary else None
        preferred = 'deepseek-v4-pro' if model_provider(default) == 'deepseek' else 'gpt-5.6-sol'
        strong = preferred if primary and preferred in primary.allowed_models else default
        self.generator = generator
        self.failure_counts = {}
        self.registry = {'fast': {'model': default, 'reasoning': 'low'},
                         'balanced': {'model': default, 'reasoning': primary.reasoning_effort if primary else 'medium'},
                         'reasoning': {'model': strong, 'reasoning': 'high'}}
        self.policy = dict(STAGE_POLICY)
        if env.get('ECE329_MODEL_REGISTRY'):
            registry = json.loads(env['ECE329_MODEL_REGISTRY'])
            if not isinstance(registry, dict) or set(registry) != set(PROFILES):
                raise ValueError('ECE329_MODEL_REGISTRY must define fast, balanced and reasoning')
            for profile, value in registry.items():
                if not isinstance(value, dict) or set(value) != {'model', 'reasoning'}:
                    raise ValueError('Each profile requires model and reasoning')
                validate_model(generator, value['model'])
                if value['reasoning'] not in {'none', 'low', 'medium', 'high', 'xhigh'}:
                    raise ValueError('Invalid profile reasoning effort')
            self.registry = deepcopy(registry)
        if env.get('ECE329_STAGE_POLICY'):
            policy = json.loads(env['ECE329_STAGE_POLICY'])
            if not isinstance(policy, dict) or set(policy) - set(self.policy) or any(v not in PROFILES for v in policy.values()):
                raise ValueError('Invalid ECE329_STAGE_POLICY')
            self.policy.update(policy)

    def defaults(self):
        return {'strategy': 'recommended', 'profile': 'balanced', 'stage_overrides': {},
                'model_override': None, 'experience_enabled': True, 'adaptive_enabled': False}

    def validate(self, value):
        if not isinstance(value, dict) or set(value) - set(self.defaults()):
            raise ValueError('Invalid model configuration fields')
        config = {**self.defaults(), **deepcopy(value)}
        if config['strategy'] not in STRATEGIES or config['profile'] not in PROFILES or any(type(config[k]) is not bool for k in ('experience_enabled','adaptive_enabled')):
            raise ValueError('Invalid model strategy/profile/experience setting')
        overrides = config['stage_overrides']
        if not isinstance(overrides, dict) or set(overrides) - set(self.policy) or any(v not in PROFILES for v in overrides.values()):
            raise ValueError('Invalid stage overrides; local validation is always enabled')
        if config['model_override'] is not None:
            validate_model(self.generator, config['model_override'])
        if config['strategy'] != 'custom' and (config['model_override'] is not None or overrides):
            raise ValueError('Per-stage or model overrides require Custom strategy')
        return config

    def public(self):
        return {'enabled': primary_generator(self.generator) is not None,
                'registry': deepcopy(self.registry), 'stage_policy': dict(self.policy),
                'strategies': list(STRATEGIES), 'defaults': self.defaults(), 'local_validator': 'always_enabled'}

    def resolve(self, stage, config):
        strategy = config['strategy']
        profile = ('fast' if strategy == 'fast' else 'reasoning' if strategy == 'quality'
                   else config['stage_overrides'].get(stage, config['profile']) if strategy == 'custom'
                   else self.policy[stage])
        route = {**self.registry[profile], 'profile': profile, 'strategy': strategy}
        if self.can_escalate(stage, config, route) and self.failure_counts.get(stage, 0) >= 2:
            profile = PROFILES[PROFILES.index(profile) + 1]
            route.update(self.registry[profile], profile=profile, adaptive_reason='recent_validator_failures')
        if config['model_override'] is not None:
            route['model'] = config['model_override']
        if route['model'] is not None:
            route.update(model_details(route['model'], route['reasoning']))
        return route

    @staticmethod
    def can_escalate(stage, config, route):
        return (config.get('adaptive_enabled', False) and config['model_override'] is None
                and stage not in config['stage_overrides'] and route['profile'] != 'reasoning')


class RoutedGenerator:
    """Resolve again when a turn advances to another stage, including repairs."""
    def __init__(self, base, router, config, initial_stage, on_metrics=None):
        self.base, self.router, self.config = base, router, config
        self.initial_route = router.resolve(initial_stage, config)
        self.on_metrics = on_metrics

    @property
    def primary(self):
        primary = primary_generator(self.base)
        return replace(primary, model=self.initial_route['model'], reasoning_effort=self.initial_route['reasoning']) if primary else None

    def __getattr__(self, name):
        original = getattr(self.base, name)
        if name not in {'generate', 'resolve_intent'} or not callable(original):
            return original

        def call(session, *args, **kwargs):
            from .openai_generator import FallbackStageGenerator, ModelOutputError
            route = self.router.resolve(session.current_stage.value, self.config)
            primary = primary_generator(self.base)
            chosen = self.base
            if primary is not None:
                if session.model_context.get('selected_model') != route['model']:
                    session.model_context.pop('openai_previous_response_id', None)
                session.model_context['selected_model'] = route['model']
                selected = replace(primary, model=route['model'], reasoning_effort=route['reasoning'],
                                   transport=ObservedTransport(primary.transport, session, route))
                chosen = FallbackStageGenerator(primary=selected, fallback=self.base.fallback) if isinstance(self.base, FallbackStageGenerator) else selected
                if self.config.get('adaptive_enabled') and name == 'generate':
                    chosen = selected
            session.turn_context['model_route'] = route
            trace = CURRENT_TRACE.get()
            start = perf_counter()
            count = len(trace.data['calls']) if trace else 0
            event = {'stage': session.current_stage.value, 'phase': name, 'model_profile': route['profile'],
                     'model_id': route.get('api_model', route['model']), 'selected_model': route['model'],
                     'provider': route.get('provider'), 'reasoning': route['reasoning'],
                     'experience_rule_ids': [r['id'] for r in session.turn_context.get('experience_rules', [])],
                     'validator_pass': None, 'fallback_used': False}
            if route.get('adaptive_reason'):
                event['adaptive_reason'] = route['adaptive_reason']
            try:
                try:
                    result = getattr(chosen, name)(session, *args, **kwargs)
                except ModelOutputError:
                    if primary is None or name != 'generate' or not self.router.can_escalate(session.current_stage.value, self.config, route):
                        raise
                    stronger = {**route, **self.router.registry['reasoning'], 'profile':'reasoning', 'adaptive_reason':'validator_failure'}
                    stronger.update(model_details(stronger['model'], stronger['reasoning']))
                    if stronger['model'] == route['model'] and stronger['reasoning'] == route['reasoning']:
                        raise
                    session.model_context.pop('openai_previous_response_id', None)
                    session.model_context['selected_model'] = stronger['model']
                    event['adaptive_escalation'] = stronger
                    event['validation_failures_before_escalation'] = 1
                    upgraded = replace(primary, model=stronger['model'], reasoning_effort=stronger['reasoning'],
                                       transport=ObservedTransport(primary.transport, session, stronger))
                    try:
                        result = upgraded.generate(session, *args, **kwargs)
                    finally:
                        if self.on_metrics:
                            self.on_metrics(upgraded.runtime_info())
                info = chosen.runtime_info() if hasattr(chosen, 'runtime_info') else {}
                event['fallback_used'] = bool(info.get('fallback_calls', 0))
                if primary is not None and (trace is None or len(trace.data['calls']) > count):
                    if event['fallback_used']:
                        event['validator_pass'] = False if str(info.get('last_fallback_reason', '')).endswith('output_rejected') else None
                    else:
                        event['validator_pass'] = True
                return result
            except Exception as exc:
                # A transport outage is not a rejected design. Do not use it
                # to infer that a stronger model would pass validation.
                event['validator_pass'] = False if isinstance(exc, ModelOutputError) else None
                event['error_type'] = type(exc).__name__
                raise
            finally:
                if primary is not None and self.on_metrics:
                    self.on_metrics(chosen.runtime_info())
                event['latency_ms'] = round((perf_counter() - start) * 1000, 2)
                event['retry_count'] = max(0, len(trace.data['calls']) - count - 1) if trace else 0
                if trace:
                    event['experience_rule_ids'] = list(dict.fromkeys(rule for row in trace.data['calls'][count:] for rule in row['experience_rule_ids']))
                    trace.data['stages'].append(event)
        return call
