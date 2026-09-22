"""Cross-mode regressions for replay isolation, injection and bounded repairs."""
from copy import deepcopy
import json
import pytest

from ece329_workflow.models import InteractionState
from ece329_workflow.experience_rules import RuleRun
from ece329_workflow.experience_replay import fixture, semantic
from ece329_workflow.telemetry import injected_rule_ids
from tests.test_executable_experience import packet


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('change', ['reverse_sources', 'new_id'])
def test_equivalent_sources_cannot_restart_same_business_state(mode, change):
    original = fixture(mode)
    session = deepcopy(original)
    message = 'Keep the saved design without changes and continue'
    first = packet()
    second = deepcopy(first)
    second['id'] = 'EXP-' + 'b' * 32
    run = RuleRun(session, message)
    run.select([first, second], [])
    run.hook('after_intent', session, semantic(message, 'positive'))
    run.hook('before_pending', session)
    run.hook('before_reply', session, response={'assistant_message': '', 'revision': 1})
    guard = deepcopy(session.model_context['experience_execution_guard'])
    assert len(guard['effect_states']) == 1
    # Simulate a repeated business state, despite a new revision or primary ID.
    repeated = deepcopy(original)
    repeated.revision += 1
    repeated.model_context['experience_execution_guard'] = guard
    if change == 'new_id':
        second['id'] = 'EXP-' + 'c' * 32
        rows = [second]
    else:
        rows = [second, first]
    retry = RuleRun(repeated, message)
    retry.select(rows, [])
    retry.hook('after_intent', repeated, semantic(message, 'positive'))
    retry.hook('before_pending', repeated)
    assert retry.blocked
    assert retry.events[-1]['code'] == 'no_progress_blocked'
    assert repeated.model_context['dialogue_state']['pending_action']['candidate_answer']
    assert repeated.design_context == original.design_context


@pytest.mark.parametrize('mode', list(InteractionState))
def test_repeated_intent_hook_cannot_replace_selection(mode):
    session = fixture(mode)
    message = 'Keep the saved design and continue'
    run = RuleRun(session, message)
    run.select([packet()], [])
    run.hook('after_intent', session, semantic(message, 'positive'))
    before = deepcopy(run.applied)
    run.hook('after_intent', session, semantic(message, 'ambiguous'))
    assert run.applied == before and run.semantic_match
    run.hook('before_pending', session)
    run.hook('before_pending', session)
    assert sum(e['code'] == 'candidate_declined' for e in run.events) == 1


@pytest.mark.parametrize('envelope', ['intent', 'generation'])
def test_injection_requires_whole_packet_in_guidance(envelope):
    rule = packet()
    def payload(context):
        text = json.dumps(context)
        if envelope == 'generation':
            text = 'User mentioned CONTEXT_JSON:\nexample\nCONTEXT_JSON:\n' + text
        return {'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}]}
    assert injected_rule_ids(payload({'feedback_guidance': {'rules': [rule]}}), [rule]) == [rule['id']]
    # Quoted IDs, nested history, or incomplete/changed execution are not injection.
    for context in [
        {'user_message': rule['id']},
        {'history': [{'feedback_guidance': {'rules': [rule]}}]},
        {'feedback_guidance': {'rules': [{'id': rule['id']}]}},
        {'feedback_guidance': {'rules': [{**rule, 'version': 2}]}},
        {'feedback_guidance': {'rules': [{**rule, 'execution': {}}]}},
    ]:
        assert injected_rule_ids(payload(context), [rule]) == []
    assert injected_rule_ids({'input': 'Rule ' + rule['id']}, [rule]) == []


@pytest.mark.parametrize('mode', list(InteractionState))
def test_guard_history_is_bounded_and_allows_changed_design(mode):
    session = fixture(mode)
    session.model_context['experience_execution_guard'] = {'effect_states': [str(n) for n in range(16)]}
    message = 'Keep the saved design and continue'
    run = RuleRun(session, message)
    run.select([packet()], [])
    run.hook('after_intent', session, semantic(message, 'positive'))
    run.hook('before_pending', session)
    assert not run.blocked
    run.hook('before_reply', session, response={'assistant_message': '', 'revision': 1})
    guard = session.model_context['experience_execution_guard']
    assert len(guard['effect_states']) == 16
    assert guard['effect_states'][0] == '1'
    changed = fixture(mode)
    # A substantive pending change remains eligible; incidental revision/IDs do not.
    changed.model_context['dialogue_state']['pending_action']['candidate_answer'] += ' new proposed change'
    changed.model_context['experience_execution_guard'] = deepcopy(guard)
    next_run = RuleRun(changed, message)
    next_run.select([packet()], [])
    next_run.hook('after_intent', changed, semantic(message, 'positive'))
    next_run.hook('before_pending', changed)
    assert not next_run.blocked
    assert any(e['code'] == 'candidate_declined' for e in next_run.events)


@pytest.mark.parametrize('mode', list(InteractionState))
def test_production_prompt_formats_report_actual_injection(mode):
    from ece329_workflow.prompts import build_prompt_packet
    from ece329_workflow.dialogue_state import serialize_intent_input, current_pending_action
    session = fixture(mode)
    rule = packet()
    session.turn_context['experience_rules'] = [rule]
    message = 'Keep the saved design and continue'
    prompt = build_prompt_packet(session, message)
    intent = serialize_intent_input(session, message, current_pending_action(session), {})
    for text in [intent, 'CONTEXT_JSON:\n' + prompt['serialized_context']]:
        assert injected_rule_ids({'input': text}, [rule]) == [rule['id']]
