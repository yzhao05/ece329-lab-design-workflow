"""Real routing and Responses payloads, with a deterministic model transport."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from threading import Barrier, Lock
from pathlib import Path

import pytest

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.model_selection import catalogue, generator_for_model
from ece329_workflow.models import DesignSession, InteractionState, SessionConflict
from ece329_workflow.openai_generator import OpenAIStageGenerator, generator_from_environment, ModelConfigurationError
from ece329_workflow.security import APISettings
from ece329_workflow.store import InMemorySessionStore, SQLiteSessionStore
from tests.test_openai_generator import valid_output
from tests.test_security_and_store import call_api, workspace_temp_path, remove_sqlite_files

QUESTION = '电场的叠加如何计算？'
NEW_MODELS = ('gpt-5.5', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna')


class ModelTransport:
    def __init__(self, barrier=None, broken_intent=False):
        self.requests = []
        self.lock = Lock()
        self.barrier = barrier
        self.broken_intent = broken_intent

    def create(self, payload):
        with self.lock:
            self.requests.append(deepcopy(payload))
            index = len(self.requests)
        schema = payload['text']['format']['name']
        if schema == 'ece329_context_intent':
            if self.barrier:
                self.barrier.wait(timeout=5)
            output = {'intent': 'ASK_COURSE_QUESTION', 'target': None, 'resolved_value_json': None,
                      'semantic_updates_json': json.dumps({'student_questions': [QUESTION]}),
                      'dialogue_acts_json': json.dumps([{'type': 'ASK_COURSE_QUESTION', 'content': QUESTION, 'confidence': 1.0}]),
                      'advance_requested': False, 'preserve_current_design': True, 'confidence': 1.0}
            if self.broken_intent:
                return {'output_text': 'invalid-json'}
        elif schema == 'ece329_compact_dialogue_acts':
            output = {'actions': [{'type': 'ASK_COURSE_QUESTION', 'target': '', 'operation': 'EXECUTE',
                                   'content': QUESTION, 'source_text': QUESTION, 'source_start': 0,
                                   'source_end': len(QUESTION), 'semantic_key': 'field_superposition', 'confidence': 1.0}]}
        else:
            output = valid_output(assistant_message='分别计算各电荷的电场矢量，再作矢量求和。',
                                  stage_payload_json=json.dumps({'preserve_pending_action': True}),
                                  student_task=None, visualization_json=None)
        return {'id': f"resp_{payload['model']}_{index}", 'output_text': json.dumps(output, ensure_ascii=False)}


def make_engine(store=None, transport=None):
    transport = transport or ModelTransport()
    engine = WorkflowEngine(generator=OpenAIStageGenerator(transport=transport, stateful=True),
                            store=store or InMemorySessionStore())
    return engine, transport


def add_session(engine, mode=InteractionState.EMVR_DIRECT, design_id='model-design'):
    session = DesignSession(design_id, mode, current_stage_index=8,
                            access_token_hash=hashlib.sha256(b'model-owner').hexdigest(),
                            stage_outputs={'CONCEPTUAL_PROCEDURE': {'assistant_message': '已记录流程'}})
    session.model_context['selected_model'] = 'gpt-5.4-mini'
    session.model_context['openai_previous_response_id'] = 'resp_old_mini'
    session.history.append({'user_message': '保留切换模型前的观察要求', 'output': {}})
    engine.store.save(session)
    return session


def test_catalogue_and_environment_allowlist():
    engine, _ = make_engine()
    config = catalogue(engine.generator)
    assert config['default_model'] == 'gpt-5.4-mini'
    assert config['models'][0]['label'] == 'GPT 5.4-mini（recommend）'
    assert [row['id'] for row in config['models']] == ['gpt-5.4-mini', 'gpt-5.4', 'gpt-5.4-nano', *NEW_MODELS]
    env_example = (Path(__file__).resolve().parents[1] / '.env.example').read_text(encoding='utf-8')
    allowlist = next(line.split('=', 1)[1] for line in env_example.splitlines() if line.startswith('ECE329_ALLOWED_MODELS='))
    assert [m for m in allowlist.split(',') if not m.startswith('deepseek-')] == [row['id'] for row in config['models']]
    generator = generator_from_environment({'ECE329_ALLOWED_MODELS': 'gpt-5.4-mini,gpt-5.4', 'ECE329_OPENAI_FALLBACK': 'false'}, transport=ModelTransport())
    assert [row['id'] for row in catalogue(generator)['models']] == ['gpt-5.4-mini', 'gpt-5.4']
    with pytest.raises(ModelConfigurationError, match='must be included'):
        generator_from_environment({'ECE329_ALLOWED_MODELS': 'gpt-5.4'}, transport=ModelTransport())


@pytest.mark.parametrize('mode', list(InteractionState))
@pytest.mark.parametrize('selected', ('gpt-5.4', *NEW_MODELS))
def test_switch_routes_intent_and_reply_and_resets_only_remote_chain(mode, selected):
    engine, transport = make_engine()
    session = add_session(engine, mode)
    result = engine.process_turn(session.design_id, {'message': QUESTION, 'model': selected, 'turn_id': 'select-full-0001'})
    assert result['selected_model'] == selected
    assert len(transport.requests) >= 2
    assert {r['model'] for r in transport.requests} == {selected}
    reply = next(r for r in transport.requests if r['text']['format']['name'] == 'ece329_stage_output')
    assert 'previous_response_id' not in reply
    assert '保留切换模型前的观察要求' in reply['input'][0]['content'][0]['text']
    saved = engine.store.get(session.design_id)
    assert saved.current_stage_index == session.current_stage_index
    assert saved.model_context['selected_model'] == selected
    assert saved.model_context['openai_previous_response_id'].startswith(f'resp_{selected}_')
    assert saved.history[-1]['selected_model'] == selected
    assert engine.generator.model == 'gpt-5.4-mini'
    count = len(transport.requests)
    result2 = engine.process_turn(session.design_id, {'message': QUESTION, 'turn_id': 'same-model-0002'})
    assert result2['selected_model'] == selected
    reply2 = next(r for r in transport.requests[count:] if r['text']['format']['name'] == 'ece329_stage_output')
    assert reply2['previous_response_id'] == saved.model_context['openai_previous_response_id']
    assert engine.get_design(session.design_id)['selected_model'] == selected


def test_compact_intent_repair_stays_on_selected_model():
    engine, transport = make_engine(transport=ModelTransport(broken_intent=True))
    session = add_session(engine, InteractionState.GUIDED_DESIGN)
    engine.process_turn(session.design_id, {'message': QUESTION, 'model': 'gpt-5.4-nano'})
    assert any(r['text']['format']['name'] == 'ece329_compact_dialogue_acts' for r in transport.requests)
    assert {r['model'] for r in transport.requests} == {'gpt-5.4-nano'}


def test_simultaneous_sessions_do_not_mutate_shared_generator():
    engine, transport = make_engine(transport=ModelTransport(barrier=Barrier(2)))
    a = add_session(engine, design_id='parallel-a')
    b = add_session(engine, design_id='parallel-b')
    assert engine._lock_for_design(a.design_id) is not engine._lock_for_design(b.design_id)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda args: engine.process_turn(args[0], {'message': QUESTION, 'model': args[1]}),
                                [(a.design_id, 'gpt-5.4'), (b.design_id, 'gpt-5.4-nano')]))
    assert [r['selected_model'] for r in results] == ['gpt-5.4', 'gpt-5.4-nano']
    assert engine.generator.model == 'gpt-5.4-mini'
    assert sorted(r['model'] for r in transport.requests if r['text']['format']['name'] == 'ece329_stage_output') == ['gpt-5.4', 'gpt-5.4-nano']


def test_same_request_id_cannot_change_model_and_old_replay_does_not_reexecute():
    engine, transport = make_engine()
    session = add_session(engine)
    request = {'message': QUESTION, 'model': 'gpt-5.4', 'turn_id': 'frozen-model-0001'}
    first = engine.process_turn(session.design_id, request)
    count = len(transport.requests)
    assert engine.process_turn(session.design_id, request) == first
    assert len(transport.requests) == count
    with pytest.raises(SessionConflict):
        engine.process_turn(session.design_id, {**request, 'model': 'gpt-5.4-nano'})
    engine.process_turn(session.design_id, {'message': QUESTION, 'model': 'gpt-5.4-mini', 'turn_id': 'frozen-model-0002'})
    assert engine.process_turn(session.design_id, request) == first
    assert engine.get_design(session.design_id)['selected_model'] == 'gpt-5.4-mini'


@pytest.mark.parametrize('model', ['not-allowed', '', 1, [], {}])
def test_api_rejects_invalid_models_before_saving_or_calling_transport(model):
    engine, transport = make_engine()
    session = add_session(engine)
    api = WorkflowAPI(engine, APISettings(rate_limit_requests=500))
    try:
        before = engine.store.get(session.design_id)
        status, _, _ = call_api(api, 'POST', f'/v1/designs/{session.design_id}/turns', {'message': QUESTION, 'model': model},
                                 request_headers={'Authorization': 'Bearer model-owner'})
        assert status.startswith('400')
        assert engine.store.get(session.design_id) == before and transport.requests == []
        assert call_api(api, 'POST', '/v1/designs', {'idea': QUESTION, 'model': model})[0].startswith('400')
        assert transport.requests == []
    finally:
        api.feedback.store.close()


def test_model_selection_survives_sqlite_restart():
    path = workspace_temp_path('.sqlite')
    try:
        engine, _ = make_engine(store=SQLiteSessionStore(path))
        session = add_session(engine)
        engine.process_turn(session.design_id, {'message': QUESTION, 'model': 'gpt-5.4-nano'})
        restarted, transport = make_engine(store=SQLiteSessionStore(path))
        assert restarted.get_design(session.design_id)['selected_model'] == 'gpt-5.4-nano'
        restarted.process_turn(session.design_id, {'message': QUESTION})
        assert {r['model'] for r in transport.requests} == {'gpt-5.4-nano'}
    finally:
        remove_sqlite_files(path)


def test_catalogue_disabled_in_rule_mode_and_feedback_generator_stays_default():
    from ece329_workflow.generator import RuleBasedStageGenerator
    assert catalogue(RuleBasedStageGenerator()) == {'enabled': False, 'default_model': None, 'models': []}
    engine, _ = make_engine()
    api = WorkflowAPI(engine, APISettings())
    try:
        assert call_api(api, 'GET', '/v1/models')[2]['models'][0]['recommended'] is True
        session = add_session(engine)
        engine.process_turn(session.design_id, {'message': QUESTION, 'model': 'gpt-5.4'})
        assert api.feedback.extractor.generator.model == 'gpt-5.4-mini'
    finally:
        api.feedback.store.close()


def test_api_create_and_resume_preserve_choice():
    engine, _ = make_engine()
    api = WorkflowAPI(engine, APISettings())
    try:
        headers = {'Idempotency-Key': 'create-with-model-0001'}
        body = {'idea': QUESTION, 'model': 'gpt-5.4-nano'}
        status, _, created = call_api(api, 'POST', '/v1/designs', body, request_headers=headers)
        assert status.startswith('201') and created['selected_model'] == 'gpt-5.4-nano'
        assert call_api(api, 'POST', '/v1/designs', body, request_headers=headers)[2] == created
        assert not call_api(api, 'POST', '/v1/designs', {**body, 'model': 'gpt-5.4'}, request_headers=headers)[0].startswith('201')
        resumed = call_api(api, 'POST', f"/v1/designs/{created['design_id']}/resume", {'resume_token': created['design_resume_token']})[2]
        assert resumed['selected_model'] == 'gpt-5.4-nano'
    finally:
        api.feedback.store.close()


@pytest.mark.parametrize('mode', list(InteractionState))
def test_existing_online_session_can_continue_after_rule_only_restart(mode):
    from ece329_workflow.generator import RuleBasedStageGenerator
    engine, _ = make_engine()
    session = add_session(engine, mode)
    restarted = WorkflowEngine(generator=RuleBasedStageGenerator(), store=engine.store)
    before = restarted.store.get(session.design_id)
    with pytest.raises(ValueError, match='online generator'):
        restarted.process_turn(session.design_id, {'message': QUESTION, 'model': 'gpt-5.4-mini'})
    assert restarted.store.get(session.design_id) == before
    request = {'message': '你现在在问什么问题？', 'turn_id': 'offline-recovery-0001'}
    result = restarted.process_turn(session.design_id, request)
    assert result['selected_model'] is None
    saved = restarted.store.get(session.design_id)
    assert 'openai_previous_response_id' not in saved.model_context
    assert saved.current_stage_index == before.current_stage_index
    assert saved.history[:len(before.history)] == before.history
    assert saved.stage_outputs['CONCEPTUAL_PROCEDURE'] == before.stage_outputs['CONCEPTUAL_PROCEDURE']
    assert restarted.process_turn(session.design_id, request) == result
