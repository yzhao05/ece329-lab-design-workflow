from copy import deepcopy
import json
import pytest

from ece329_workflow.harness_semantics import SELECTION, catalog
from ece329_workflow.knowledge_base import KNOWLEDGE
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.models import DesignSession, InteractionState, TurnRequest
from ece329_workflow.openai_generator import OpenAIStageGenerator, ModelOutputError
from ece329_workflow.workflow_harness import WorkflowHarnessTransport
from tests.test_workflow_harness import Provider, LocalRuntime
from tests.test_experience_continue import pending


@pytest.fixture(autouse=True)
def isolate_selection():
    token = SELECTION.set(None)
    yield
    SELECTION.reset(token)


def selection():
    return {**{key: [] for key in catalog(KNOWLEDGE)}, 'domains': ['magnetism'],
            'concept_ids': ['lecture_12'], 'formula_ids': [KNOWLEDGE.formulas[0]['id']]}


def answer(message, *, confidence=0.99, intent='REQUEST_MORE_EXAMPLES', action='REQUEST_REFERENCE'):
    return {'intent': intent, 'target': 'core_phenomenon', 'resolved_value_json': None,
            'semantic_updates_json': None, 'advance_requested': False, 'preserve_current_design': True,
            'confidence': confidence, 'dialogue_acts_json': json.dumps([{
                'type': action, 'target': 'core_phenomenon', 'operation': 'EXECUTE',
                'content': message, 'source_text': message, 'confidence': confidence}]),
            'knowledge_selection': selection()}


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('message', ['你有什么建议吗', 'What would you suggest?', '你觉得怎么比较更有意义？'])
def test_advice_goes_to_model_without_replaying_confirmation(mode, message):
    source = Provider(answer(message, confidence=0.4))
    engine = WorkflowEngine(generator=OpenAIStageGenerator(
        transport=WorkflowHarnessTransport(source, runtime_factory=LocalRuntime)))
    session = DesignSession('advice', mode)
    session.design_context['idea_brainstorming'] = {'research_object': 'two coils'}
    session.model_context['dialogue_state'] = {'pending_action': pending(mode)}
    original = deepcopy(session.design_context)
    result, _ = engine._resolve_turn_intent(session, TurnRequest(message), message)
    assert result['intent'] == 'REQUEST_MORE_EXAMPLES'
    assert not result['advance_requested']
    assert session.design_context['idea_brainstorming'] == original['idea_brainstorming']
    assert session.model_context['dialogue_state']['pending_action']['candidate_answer'] == pending(mode)['candidate_answer']
    assert len(source.payloads) == 1
    assert source.payloads[0]['text']['format']['name'] == 'ece329_harness_understanding'
    assert len(KNOWLEDGE.match_concepts('unrelated lexical query')) == 1
    assert KNOWLEDGE.match_concepts('unrelated lexical query')[0]['id'] == 'lecture_12'
    assert KNOWLEDGE.match_supplemental_concepts('all words') == []
    assert KNOWLEDGE.formula_references('different words')[0]['id'] == selection()['formula_ids'][0]


@pytest.mark.parametrize('invalid', ['unknown_reference', 'missing_user_evidence'])
def test_invalid_model_results_do_not_write_or_retry(invalid):
    message = 'What would you suggest?'
    output = answer(message)
    if invalid == 'unknown_reference': output['knowledge_selection']['concept_ids'] = ['invented-id']
    else:
        acts = json.loads(output['dialogue_acts_json'])
        acts[0].update(type='MODIFY_DESIGN_FIELD', operation='REPLACE')
        acts[0]['source_text'] = 'not said by this user'
        output['dialogue_acts_json'] = json.dumps(acts)
    source = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(source, runtime_factory=LocalRuntime))
    session = DesignSession('invalid', InteractionState.GUIDED_DESIGN)
    with pytest.raises(ModelOutputError): generator.resolve_intent(session, message, None, {})
    assert len(source.payloads) == 1 and SELECTION.get() is None
    assert not session.design_context


def test_catalog_contains_every_source_not_keyword_subset():
    sources = catalog(KNOWLEDGE)
    assert len(sources['concept_ids']) == len(KNOWLEDGE.lectures)
    assert len(sources['scene_ids']) == len(KNOWLEDGE.exploration_points)
    assert len(sources['formula_ids']) == len(KNOWLEDGE.formulas)
    # This is a catalog+context task, not an unlimited external search agent.
    assert len(json.dumps(sources, ensure_ascii=False).encode()) < 150000


@pytest.mark.parametrize('mode', list(InteractionState))
def test_read_only_intent_without_redundant_action_is_not_rejected(mode):
    message = '你有什么建议吗'
    output = answer(message, confidence=0.3)
    output['dialogue_acts_json'] = '[]'
    provider = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    session = DesignSession('read-only', mode)
    result = generator.resolve_intent(session, message, pending(mode), {})
    assert result['intent'] == 'REQUEST_MORE_EXAMPLES'
    assert not result['advance_requested'] and not session.design_context


def test_read_only_semantic_paraphrase_does_not_require_literal_source_matching():
    message = 'What would you suggest?'
    output = answer(message)
    actions = json.loads(output['dialogue_acts_json'])
    actions[0]['source_text'] = '用户询问建议'
    output['dialogue_acts_json'] = json.dumps(actions)
    provider = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    session = DesignSession('paraphrase', InteractionState.GUIDED_DESIGN)
    result = generator.resolve_intent(session, message, None, {})
    assert result['intent'] == 'REQUEST_MORE_EXAMPLES'
    assert not session.design_context and len(provider.payloads) == 1


@pytest.mark.parametrize('mode', list(InteractionState))
def test_model_selected_formulas_reach_response_prompt_without_profiles(mode):
    from ece329_workflow.prompts import build_prompt_packet
    SELECTION.set(selection())
    session = DesignSession('retrieval', mode)
    retrieval = build_prompt_packet(session, 'What would you suggest?')['context']['knowledge_retrieval']
    assert selection()['formula_ids'][0] in [row['id'] for row in retrieval['formulas']]
    assert retrieval['brainstorm_options'] == []  # No unrelated random refill.


def test_selection_is_isolated_between_parallel_contexts():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def read(identity):
        token = SELECTION.set({**selection(), 'concept_ids': [identity]})
        try:
            barrier.wait(timeout=5)
            return [row['id'] for row in KNOWLEDGE.match_concepts('same query')]
        finally:
            SELECTION.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(read, ['lecture_12', 'lecture_13'])) == [['lecture_12'], ['lecture_13']]
    assert SELECTION.get() is None


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('stage_one', [False, True])
def test_advice_request_returns_reference_in_main_conversation(mode, stage_one, monkeypatch):
    from tests.test_model_selection import ModelTransport, make_engine, add_session
    from tests.test_openai_generator import valid_output
    def legacy_reinterpretation(*args):
        pytest.fail('Harness result must not be reinterpreted by legacy meta phrase matching')
    monkeypatch.setattr('ece329_workflow.engine.reconcile_meta_requests', legacy_reinterpretation)
    message = '你有什么建议吗'
    class AdviceProvider(ModelTransport):
        def create(self, request):
            self.requests.append(deepcopy(request))
            output = answer(message) if request['text']['format']['name'] == 'ece329_harness_understanding' else valid_output(
                assistant_message='建议先固定几何条件，比较中心与周围的磁场。这只是参考，尚未替你确定设计。',
                stage_payload_json=json.dumps({'preserve_pending_action': True,
                    'experiment_outline_seed': {'research_object': 'unaccepted generated object'},
                    'stage_readiness': {'ready_for_confirmation': True, 'remaining_gaps': []}}),
                student_task=None, visualization_json=None)
            return {'output_text': json.dumps(output, ensure_ascii=False)}
    provider = AdviceProvider()
    engine, _ = make_engine(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    session = add_session(engine, mode)
    if stage_one:
        session.current_stage_index = 0
        session.design_context['idea'] = {
            'direction_locked': True, 'core_phenomenon': '比较线圈中心与周围的稳恒磁场',
            'direction_summary': '比较线圈中心与周围的稳恒磁场',
        }
        session.model_context['dialogue_state'] = {'pending_action': pending(mode)}
        engine.store.save(session)
    session.stage_outputs[session.current_stage.value] = {
        'assistant_message': '已保存的阶段产物', 'stage_payload': {'saved_artifact': True}}
    original_artifact = deepcopy(session.stage_outputs[session.current_stage.value])
    engine.store.save(session)
    result = engine.process_turn(session.design_id, {'message': message, 'turn_id': 'advice-main-conversation'})
    assert '参考' in result['assistant_message']
    assert '还没完全明白' not in result['assistant_message']
    assert 'experiment_outline_seed' not in result['stage_payload']
    assert 'stage_readiness' not in result['stage_payload']
    assert engine.store.get(session.design_id).current_stage == session.current_stage
    assert engine.store.get(session.design_id).stage_outputs[session.current_stage.value] == original_artifact
    if stage_one:
        saved = engine.store.get(session.design_id)
        assert saved.design_context['idea']['core_phenomenon'] == session.design_context['idea']['core_phenomenon']
    assert sum(p['text']['format']['name'] == 'ece329_harness_understanding' for p in provider.requests) == 1
    assert SELECTION.get() is None


@pytest.mark.parametrize('scene_response, expected', [
    ('REQUEST_NEW_BATCH', True), ('PROVIDE_BROAD_TOPIC', True),
    ('NONE', False), ('SELECT_OR_DEVELOP', False),
])
def test_scene_authorization_uses_joint_semantic_decision(scene_response, expected):
    message = 'Please show another set of scenes.'
    output = answer(message)
    output['target'] = 'exploration_scenes'
    output['semantic_updates_json'] = json.dumps({
        'stage_one_scene_response': scene_response, 'course_scope_status': 'COURSE_CONTENT',
        # Model-authored legacy flags must not override the semantic decision.
        'scene_batch_authorized': not expected,
    })
    provider = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    result = generator.resolve_intent(DesignSession('scenes', InteractionState.GUIDED_DESIGN), message, None, {})
    assert result['semantic_updates']['scene_batch_authorized'] is expected
    assert len(provider.payloads) == 1


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('invalid', ['confidence', 'null_actions', 'action_confidence'])
def test_malformed_envelope_has_controlled_failure(mode, invalid):
    message = 'Keep the saved design.'
    output = answer(message)
    if invalid == 'confidence':
        output['confidence'] = {'invalid': True}
    elif invalid == 'null_actions':
        output['dialogue_acts_json'] = None
        output['intent'] = 'MODIFY_PREVIOUS_PROPOSAL'
    else:
        actions = json.loads(output['dialogue_acts_json'])
        actions[0]['confidence'] = 'NaN'
        output['dialogue_acts_json'] = json.dumps(actions)
    provider = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    session = DesignSession('malformed', mode)
    with pytest.raises(ModelOutputError):
        generator.resolve_intent(session, message, None, {})
    assert not session.design_context and SELECTION.get() is None
    assert len(provider.payloads) == 1


@pytest.mark.parametrize('mode', list(InteractionState))
def test_harness_failure_does_not_reenable_local_semantic_recovery(mode):
    from ece329_workflow.openai_generator import FallbackStageGenerator
    from ece329_workflow.generator import RuleBasedStageGenerator
    from ece329_workflow.openai_generator import ModelServiceError
    class Offline(Provider):
        def create(self, payload):
            self.payloads.append(payload)
            raise ModelServiceError('offline')
    provider = Offline()
    primary = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    generator = FallbackStageGenerator(primary, RuleBasedStageGenerator())
    session = DesignSession('unavailable', mode)
    result = generator.resolve_intent(session, '请先提供建议，不要保存任何修改。这段文字够长但不是当前字段的答案。', pending(mode), {})
    assert result['intent'] == 'UNCLEAR'
    assert not result['semantic_updates']
    assert not result['resolved_value']
    assert result['source'] == 'HARNESS_SEMANTIC_UNAVAILABLE'
    assert len(provider.payloads) == 1


@pytest.mark.parametrize('decision, allowed', [('REQUEST_NEW_BATCH', True), ('NONE', False)])
def test_joint_scene_decision_reaches_existing_reply_guard(decision, allowed):
    from ece329_workflow.engine import _prevent_unrequested_scene_replay
    from ece329_workflow.generator import RuleBasedStageGenerator, build_exploration_scenes
    from ece329_workflow.models import StepOutput
    baseline = WorkflowEngine(generator=RuleBasedStageGenerator())
    initial = baseline.create_design('我想探索传输线')
    session = baseline.store.get(initial['design_id'])
    message = '换一组' if allowed else '你有什么建议吗'
    output = answer(message)
    output['target'] = 'exploration_scenes' if allowed else 'core_phenomenon'
    output['semantic_updates_json'] = json.dumps({'stage_one_scene_response': decision})
    provider = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    turn_intent = generator.resolve_intent(session, message, None, {})
    # Engine validates a second time; authorization must survive this boundary.
    from ece329_workflow.dialogue_state import validate_resolved_intent
    turn_intent = validate_resolved_intent(turn_intent, None)
    scenes = build_exploration_scenes(KNOWLEDGE.exploration_points[30:33])
    generated = StepOutput(assistant_message='New scenes', stage_payload={
        'exploration_scenes': scenes, 'alternative_ideas': KNOWLEDGE.exploration_points[30:33]})
    _prevent_unrequested_scene_replay(session, generated, None, message, turn_intent)
    assert bool(generated.stage_payload.get('exploration_scenes')) is allowed
    assert len(provider.payloads) == 1


def test_reference_payload_cannot_overwrite_guided_draft():
    from ece329_workflow.engine import _persist_guided_stage_draft
    from ece329_workflow.models import Stage
    session = DesignSession('reference-draft', InteractionState.GUIDED_DESIGN)
    session.design_context['guided_stage_drafts'] = {
        Stage.VARIABLES_AND_CONDITIONS.value: {'independent_variable': 'saved value'}}
    original = deepcopy(session.design_context)
    _persist_guided_stage_draft(session, Stage.VARIABLES_AND_CONDITIONS,
                               {'reference_only': True, 'independent_variable': 'unaccepted advice'})
    assert session.design_context == original


def test_duplicate_known_references_are_normalized_without_retry():
    message = 'What would you suggest?'
    output = answer(message)
    output['knowledge_selection']['concept_ids'] = ['lecture_12', 'lecture_12']
    provider = Provider(output)
    generator = OpenAIStageGenerator(transport=WorkflowHarnessTransport(provider, runtime_factory=LocalRuntime))
    result = generator.resolve_intent(DesignSession('duplicate-id', InteractionState.GUIDED_DESIGN), message, None, {})
    assert result['intent'] == 'REQUEST_MORE_EXAMPLES'
    assert SELECTION.get()['concept_ids'] == ['lecture_12']
    assert len(provider.payloads) == 1
