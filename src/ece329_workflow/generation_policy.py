"""Application output budgets, distinct from provider context/account limits."""
import math

from .model_selection import model_provider, primary_generator, OPENAI_MODELS

EFFORTS = ('none', 'low', 'medium', 'high', 'xhigh', 'max')
MULTIPLIERS = dict(zip(EFFORTS, (1, 1.5, 2, 4, 6, 8)))


def supported_efforts(model):
    if model_provider(model) == 'deepseek':
        return ('none', 'low', 'high', 'max')
    return EFFORTS if model.startswith('gpt-5.6-') else EFFORTS[:-1]


class GenerationPolicy:
    def __init__(self, generator, env):
        def setting(name, default, upper):
            raw = env.get(name, str(default))
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f'{name} must be a positive integer') from None
            if isinstance(raw, bool) or not 1 <= value <= upper:
                raise ValueError(f'{name} must be between 1 and {upper}')
            return value
        self.call_cap = setting('ECE329_MAX_OUTPUT_TOKENS_PER_CALL', 64000, 384000)
        self.turn_cap = setting('ECE329_MAX_OUTPUT_TOKENS_PER_TURN', 256000, 2000000)
        self.max_calls = setting('ECE329_MAX_MODEL_CALLS_PER_TURN', 12, 64)
        primary = primary_generator(generator)
        self.models = {}
        if primary is None:
            return
        base = max(primary.intent_max_output_tokens, primary.max_output_tokens,
                   primary.stage_one_max_output_tokens, primary.final_max_output_tokens)
        for model in primary.allowed_models:
            # These are our workflow budgets, not a claim about provider limits.
            if model_provider(model) == 'deepseek':
                size = 12000 if model == 'deepseek-v4-pro' else 8192
                adapter = getattr(primary.transport, 'providers', {}).get('deepseek', primary.transport)
                size = max(size, getattr(adapter, 'max_output_tokens', 8192))
                provider_cap = 384000
            else:
                size = (4000 if 'nano' in model or 'luna' in model else
                        5000 if 'mini' in model or 'terra' in model else 8000)
                provider_cap = 128000 if model in OPENAI_MODELS else 16000
            budgets = {}
            for effort in supported_efforts(model):
                call = min(math.ceil(max(base, size) * MULTIPLIERS[effort]), self.call_cap,
                           self.turn_cap, provider_cap)
                budgets[effort] = {'max_output_tokens': call,
                                   'turn_output_tokens': min(call * 6, self.turn_cap),
                                   'max_model_calls': self.max_calls}
            self.models[model] = {'reasoning_efforts': list(budgets), 'budgets': budgets}

    def budget(self, model, effort):
        if model is None:
            return {}
        return dict(self.models[model]['budgets'][effort])


class TurnBudgetExceeded(Exception):
    """Terminal for this turn; must not enter model repair or fallback loops."""


class TurnBudget:
    def __init__(self, route):
        self.limit = route.get('turn_output_tokens', 0)
        self.max_calls = route.get('max_model_calls', 0)
        self.charged = 0
        self.calls = 0

    def reserve(self, requested):
        if self.calls >= self.max_calls or self.charged >= self.limit:
            raise TurnBudgetExceeded('This turn reached its model output/call limit')
        amount = min(requested, self.limit - self.charged)
        self.charged += amount
        self.calls += 1
        return amount

    def settle(self, reserved, actual):
        # Missing usage or uncertain transport failure consumes the reservation.
        # This is budget accounting, never represented as measured token usage.
        if type(actual) is int and actual >= 0:
            self.charged += actual - reserved
