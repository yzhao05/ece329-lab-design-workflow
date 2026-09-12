"""Review regressions: isolate canonical data and bound malformed scene recovery."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from ece329_workflow.generator import build_exploration_scenes, _format_exploration_scenes
from ece329_workflow.guardrails import shown_exploration_option_ids
from ece329_workflow.knowledge_base import KNOWLEDGE, LectureKnowledgeBase
from ece329_workflow.models import DesignSession, InteractionState, StepOutput
from ece329_workflow.openai_generator import (
    ModelOutputError, OpenAIStageGenerator, _validate_lecture_grounding,
)
from ece329_workflow.prompts import build_prompt_packet
from ece329_workflow.store import _session_payload, _session_from_payload


@pytest.mark.parametrize('sampling_path', ['catalog', 'normal', 'tail', 'restart'])
def test_nested_scene_edits_cannot_modify_the_shared_catalog(sampling_path):
    k = LectureKnowledgeBase()
    original = deepcopy(k.exploration_points)
    all_ids = {p['option_id'] for p in original}
    if sampling_path == 'catalog':
        points = k.exploration_scene_catalog()
    else:
        exclusions = {'normal': set(), 'tail': all_ids - {original[-1]['option_id']},
                      'restart': all_ids}[sampling_path]
        points = k.brainstorm_options('', limit=len(original), exclude_option_ids=exclusions)
    point = next(p for p in points if p.get('scene_template'))
    point['scene_template']['title'] = 'changed inside one session'
    point['source_pages'].append(9999)
    point['course_scope_concept_ids'].append('missing_concept')
    assert k.exploration_points == original
    assert k.validate() == []


def test_formula_pages_and_profile_conditions_are_not_shared_between_callers():
    k = LectureKnowledgeBase()
    original = k.formula_links_for_scene('ECE329-S163')
    changed = k.formula_links_for_scene('ECE329-S163')
    changed['primary_formulas'][0]['pages'].append(9999)
    changed['supporting_formulas'][0]['concept_ids'].clear()
    changed['formula_design_profiles'][0]['boundary_conditions'].clear()
    changed['formula_design_profiles'][0]['primary_formulas'][0]['pages'].clear()
    assert k.formula_links_for_scene('ECE329-S163') == original


def test_duplicate_candidate_cannot_hide_behind_different_generic_pictures():
    session = DesignSession(design_id='duplicate-scenes', interaction_state=InteractionState.GUIDED_DESIGN)
    packet = build_prompt_packet(session, '我想研究静电场')
    point = deepcopy(KNOWLEDGE.exploration_points[3])
    packet['context']['knowledge_retrieval']['brainstorm_options'] = [point]
    alternatives = [point, point, point]
    scenes = build_exploration_scenes(alternatives)
    assert len({scene['title'] for scene in scenes}) == 3
    output = StepOutput(assistant_message=_format_exploration_scenes(scenes), stage_payload={
        'brainstorm_activity': 'RELATIONSHIP_DISCOVERY', 'brainstorm_phase': 'BREADTH_EXPLORATION',
        'input_category': 'COURSE_CONTENT', 'alternative_ideas': alternatives, 'exploration_scenes': scenes,
    })
    with pytest.raises(ModelOutputError, match='distinct sampled option IDs'):
        _validate_lecture_grounding(session, output, packet)


def test_bad_scene_response_stops_after_one_repair_instead_of_looping():
    from tests.test_openai_generator import FakeTransport, guided_session, valid_output

    response = valid_output()
    payload = json.loads(response['stage_payload_json'])
    payload['alternative_ideas'] = [payload['alternative_ideas'][0]] * 3
    response['stage_payload_json'] = json.dumps(payload, ensure_ascii=False)
    transport = FakeTransport(output=response)
    with pytest.raises(ModelOutputError, match='distinct sampled option IDs'):
        OpenAIStageGenerator(transport=transport).generate(guided_session(), '研究传输线驻波')
    assert len(transport.requests) == 2


def test_cycle_boundary_and_remaining_exclusions_survive_store_roundtrip():
    session = DesignSession(design_id='cycle-persistence', interaction_state=InteractionState.GUIDED_DESIGN)
    previous = deepcopy(KNOWLEDGE.exploration_points[:-1])
    exclusions = {p['option_id'] for p in previous}
    crossing = KNOWLEDGE.brainstorm_options('', exclude_option_ids=exclusions)
    session.history = [{'output': {'stage_payload': {'alternative_ideas': points}}}
                       for points in [previous, crossing]]
    restored = _session_from_payload(_session_payload(session))
    shown = shown_exploration_option_ids(restored.history)
    assert shown == {p['option_id'] for p in crossing[1:]}
    next_batch = KNOWLEDGE.brainstorm_options('', exclude_option_ids=shown)
    assert shown.isdisjoint(p['option_id'] for p in next_batch)


def test_invalid_catalog_fails_at_load_instead_of_repeated_reply_repair():
    with patch.object(LectureKnowledgeBase, 'validate', return_value=['broken formula link']):
        with pytest.raises(ValueError, match='Invalid course knowledge catalog: broken formula link'):
            LectureKnowledgeBase()
