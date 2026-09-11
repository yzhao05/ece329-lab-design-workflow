from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.localization import DisplayTranslator
from ece329_workflow.models import InteractionState, SessionConflict
from ece329_workflow.openai_generator import ModelOutputError, OpenAIStageGenerator
from tests.test_deepseek_provider import mixed_engine
from tests.test_model_selection import make_engine, add_session, QUESTION
from tests.test_security_and_store import call_api


@pytest.mark.parametrize('mode', list(InteractionState))
def test_language_reaches_generation_persists_and_is_part_of_idempotency(mode):
    engine, transport = make_engine()
    session = add_session(engine, mode)
    turn = {'message': QUESTION, 'language': 'en', 'turn_id': 'english-turn-001'}
    result = engine.process_turn(session.design_id, turn)
    assert result['language'] == 'en'
    assert engine.store.get(session.design_id).model_context['response_language'] == 'en'
    generated = [p for p in transport.requests if p['text']['format']['name'] == 'ece329_stage_output']
    assert generated and all('The user selected English' in p['instructions'] for p in generated)
    count = len(transport.requests)
    assert engine.process_turn(session.design_id, turn) == result
    assert len(transport.requests) == count
    with pytest.raises(SessionConflict):
        engine.process_turn(session.design_id, {**turn, 'language': 'zh'})
    engine.process_turn(session.design_id, {'message':QUESTION, 'language':'zh'})
    assert engine.store.get(session.design_id).model_context['response_language'] == 'zh'


@pytest.mark.parametrize('mode', list(InteractionState))
def test_display_translation_is_authorized_cached_and_never_advances_design(mode):
    engine, _, chat = mixed_engine()
    session = add_session(engine, mode)
    api = WorkflowAPI(engine)
    before = engine.store.get(session.design_id)
    body = {'texts':['模型设置', '恢复 OBJ_01 后完成 S2'], 'language':'en',
            'design_id':session.design_id, 'model':'deepseek-flash:fast'}
    assert call_api(api,'POST','/v1/localization',body)[0].startswith('401')
    headers = {'Authorization':'Bearer model-owner'}
    status, _, result = call_api(api,'POST','/v1/localization',body,request_headers=headers)
    assert status.startswith('200'), result
    assert result['translations'][0] == 'Model settings'
    assert 'OBJ_01' in result['translations'][1] and 'S2' in result['translations'][1]
    assert len(chat.requests) == 1 and chat.requests[0]['thinking']['type'] == 'disabled'
    assert call_api(api,'POST','/v1/localization',body,request_headers=headers)[2] == result
    assert len(chat.requests) == 1
    assert engine.store.get(session.design_id) == before
    assert not engine.telemetry_records(session.design_id)  # Translation is not a design turn.


@pytest.mark.parametrize('language', ['fr', '', 1, {}, []])
def test_invalid_language_does_not_mutate_session(language):
    engine, transport = make_engine()
    session = add_session(engine)
    before = engine.store.get(session.design_id)
    with pytest.raises(ValueError):
        engine.process_turn(session.design_id, {'message':QUESTION, 'language':language})
    assert engine.store.get(session.design_id) == before
    assert not transport.requests


@pytest.mark.parametrize('rows', [[], [{'id':0,'text':'仍是中文'}], [{'id':True,'text':'text'}],
    [{'id':0,'text':'Missing identifier'}], [{'id':0,'text':'OBJ_01'},{'id':0,'text':'OBJ_01'}]])
def test_invalid_translation_fails_once_without_repair_loop(rows):
    calls = []
    def create(payload):
        calls.append(deepcopy(payload))
        return {'output_text':json.dumps({'translations':rows})}
    service = DisplayTranslator(OpenAIStageGenerator(transport=SimpleNamespace(create=create)))
    with pytest.raises(ModelOutputError):
        service.translate(['对象 OBJ_01'], 'en')
    assert len(calls) == 1


def test_translation_limits_and_model_allowlist_are_enforced_before_calls():
    engine, transport = make_engine()
    service = DisplayTranslator(engine.generator)
    for texts in ([], ['x']*25, ['x'*8001], ['x'*7000]*2):
        with pytest.raises(ValueError):
            service.translate(texts,'en')
    with pytest.raises(ValueError):
        service.translate(['hello'],'en','not-allowed')
    assert not transport.requests


def test_translation_chunks_keep_builder_paths_and_equations_whole():
    from ece329_workflow.localization import split_display_text
    path='UnityProject/LocalPackages/com.emvr.lab-common/Runtime/SceneFlow/EmVrLabContractRunner.cs'
    source='说明'*1995+path+'；公式 E=q/(4*pi*epsilon*r^2)；'+'说明'*2100
    parts=list(split_display_text(source))
    assert ''.join(parts)==source
    assert any(path in part for part in parts)
    assert all(len(part)<=4000 for part in parts)


def test_english_exports_translate_all_visible_text_without_mutating_source():
    from io import BytesIO
    import re
    import pdfplumber
    from ece329_workflow.localization import english_pdf
    from tools.export_emvr37_review import reviewed_engine
    from tests.test_model_selection import ModelTransport
    engine, session = reviewed_engine()
    before = engine.store.get(session.design_id)
    translator = DisplayTranslator(OpenAIStageGenerator(transport=ModelTransport()))
    for builder in (False, True):
        data = english_pdf(engine, translator, session.design_id, builder)
        with pdfplumber.open(BytesIO(data)) as pdf:
            text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        assert not re.search(r'[\u3400-\u9fff]', text)
        assert session.design_id in text
        if builder:
            assert 'EMVR_Blind_BuilderPack' in text and 'InitializeOnce' in text
    assert engine.store.get(session.design_id) == before
