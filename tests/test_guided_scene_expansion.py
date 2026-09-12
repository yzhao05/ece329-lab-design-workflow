"""Formula binding, rendered identity and full no-replacement pool regressions."""
from collections import Counter
from copy import deepcopy

import pytest

from ece329_workflow.generator import build_exploration_scenes
from ece329_workflow.guardrails import shown_exploration_option_ids
from ece329_workflow.knowledge_base import KNOWLEDGE, LectureKnowledgeBase
from ece329_workflow.models import DesignSession, InteractionState, Stage, StepOutput
from ece329_workflow.prompts import build_prompt_packet


def test_expansion_covers_all_profiles_and_paradigms_without_changing_legacy_ids():
    k = KNOWLEDGE
    assert len(k.exploration_points) == 183
    assert len(k.guided_formula_pattern_scenes) == 45
    assert {s['profile_id'] for s in k.guided_formula_pattern_scenes} == set(k._formula_profile_by_id)
    assert set(Counter(s['pattern_id'] for s in k.guided_formula_pattern_scenes).values()) == {3}
    assert len({s['pattern_id'] for s in k.guided_formula_pattern_scenes}) == 15
    assert k.exploration_points[3]['option_id'] == 'lecture:lecture_02:1'
    assert k.exploration_points[137]['catalog_source_type'] == 'SUPPLEMENTAL_RELATION'
    assert k.validate() == []
    # The checked-in old roles remain canonical; runtime adds new bindings only.
    import json
    from importlib.resources import files
    old = json.loads(files('ece329_workflow').joinpath('knowledge/scene_formula_links.json').read_text(encoding='utf-8'))
    for scene_id, roles in old['scene_formula_roles'].items():
        assert k.scene_formula_roles[scene_id] == roles


def test_every_authored_picture_and_formula_survives_rendering_and_selection():
    k = KNOWLEDGE
    seen = {k.scene_signature(t) for t in [*k.scene_templates, *k.generic_scene_frames]}
    for source in k.guided_formula_pattern_scenes:
        point = k._exploration_by_scene_id[source['catalog_scene_id']]
        scene = build_exploration_scenes([point], excluded_scene_signatures=seen)[0]
        assert scene['title'] == source['title']
        assert source['physical_picture'] in scene['physical_picture']
        assert scene['thinking_prompt'] == source['thinking_prompt']
        assert scene['scene_template_signature'] not in seen
        seen.add(scene['scene_template_signature'])
        link = k.formula_links_for_scene(scene['catalog_scene_id'])
        assert link['primary_formula_ids'] == source['primary_formula_ids']
        assert link['primary_formulas']
        for formula in [*link['primary_formulas'], *link['supporting_formulas']]:
            assert formula['id'] in k._formula_by_id
            assert set(formula['pages']) <= set(point['formula_source_pages'])
        assert point['source_pages'] == k._lecture_by_id[point['concept_id']]['pages']
        for formula_id in source['primary_formula_ids']:
            assert any(s['scene_id'] == source['catalog_scene_id'] for s in k.scenes_for_formula(formula_id))


@pytest.mark.parametrize('domain', [None, 'electrostatics', 'magnetism', 'electromagnetics'])
def test_three_complete_sampling_cycles_never_repeat_before_pool_exhaustion(domain):
    k = KNOWLEDGE
    eligible = {p['option_id'] for p in k.exploration_points
                if domain is None or k._scene_matches_formula_domain(p, domain)}
    history, in_cycle = [], set()
    resets = 0
    while resets < 3:
        options = k.brainstorm_options('', exclude_option_ids=shown_exploration_option_ids(history),
                                       course_domain=domain, seed_key=f'cycle-test:{len(history)}')
        assert len(options) == len({p['option_id'] for p in options}) == 3
        for point in options:
            if point.get('sampling_cycle_start'):
                assert in_cycle == eligible
                in_cycle.clear()
                resets += 1
            assert point['option_id'] in eligible
            assert point['option_id'] not in in_cycle
            in_cycle.add(point['option_id'])
        history.append({'output': {'stage_payload': {'alternative_ideas': options}}})
        assert shown_exploration_option_ids(history) == in_cycle
        assert len(history) < 250  # Fail fast if an exhaustion cycle gets stuck.


@pytest.mark.parametrize('tail_count', [1, 2])
def test_last_unseen_entries_are_not_discarded_to_fill_a_batch(tail_count):
    k = KNOWLEDGE
    unseen = {p['option_id'] for p in k.exploration_points[-tail_count:]}
    excluded = {p['option_id'] for p in k.exploration_points} - unseen
    options = k.brainstorm_options('', exclude_option_ids=excluded, seed_key='tail')
    assert {p['option_id'] for p in options[:tail_count]} == unseen
    assert options[tail_count]['sampling_cycle_start'] is True


def test_scoped_cycle_reset_retains_exclusions_in_other_domains():
    k = KNOWLEDGE
    other = next(p for p in k.exploration_points if p['course_block'] == 'electrostatics')
    magnetic = {p['option_id'] for p in k.exploration_points if k._scene_matches_formula_domain(p, 'magnetism')}
    options = k.brainstorm_options('', course_domain='magnetism', exclude_option_ids=magnetic)
    history = [{'output': {'stage_payload': {'alternative_ideas': [other]}}},
               {'output': {'stage_payload': {'alternative_ideas': options}}}]
    assert other['option_id'] in shown_exploration_option_ids(history)


@pytest.mark.parametrize('mutation,expected', [
    ({'pattern_id': 'NOT_A_PATTERN'}, 'inapplicable'),
    ({'primary_formula_ids': ['missing_formula']}, 'unknown formula'),
    ({'supporting_profile_ids': ['missing_profile']}, 'unknown formula profile'),
    ({'title': '用透明曲面包住看不见的源'}, 'duplicates title'),
    ({'physical_picture': ''}, 'empty physical_picture'),
])
def test_invalid_new_scene_data_is_rejected(mutation, expected):
    k = LectureKnowledgeBase()
    k.guided_formula_pattern_scenes[0].update(deepcopy(mutation))
    # Rebuild the same indexes as a catalog reload, keeping original links free
    # of prior runtime merges so failures test the edited data only.
    import json
    from importlib.resources import files
    old = json.loads(files('ece329_workflow').joinpath('knowledge/scene_formula_links.json').read_text(encoding='utf-8'))
    k.profile_scene_links = old['profile_scene_links']
    k.scene_formula_roles = old['scene_formula_roles']
    k._register_guided_formula_links()
    k._formula_profile_ids_by_scene_id = k._build_scene_formula_index()
    assert any(expected in error for error in k.validate())


def test_emvr_still_does_not_sample_guided_scenes_and_guided_reports_real_total():
    for mode in (InteractionState.GUIDED_DESIGN, InteractionState.EMVR_DIRECT):
        session = DesignSession(design_id='scene-mode-test', interaction_state=mode)
        packet = build_prompt_packet(session, '我还没有具体想法')
        retrieval = packet['context']['knowledge_retrieval']
        assert retrieval['exploration_scene_catalog_size'] == 183
        assert len(retrieval['brainstorm_options']) == (0 if mode is InteractionState.EMVR_DIRECT else 3)


@pytest.mark.parametrize('scene_id', ['ECE329-S144', 'ECE329-S154', 'ECE329-S163'])
def test_selected_new_scene_keeps_its_precise_formula_through_later_stage(scene_id):
    point = KNOWLEDGE._exploration_by_scene_id[scene_id]
    session = DesignSession(
        design_id='selected-new-scene', interaction_state=InteractionState.GUIDED_DESIGN,
        current_stage_index=list(Stage).index(Stage.THEORETICAL_FRAMEWORK),
        design_context={'idea': {
            'original': point['focus'], 'current_focus': point['focus'],
            'selected_scene_ids': [scene_id], 'selected_course_relations': [point],
            'direction_locked': True,
        }},
    )
    packet = build_prompt_packet(session, '继续完善这个实验')
    links = packet['context']['knowledge_retrieval']['selected_scene_formula_links']
    assert len(links) == 1
    assert links[0]['scene_id'] == scene_id
    source = next(s for s in KNOWLEDGE.guided_formula_pattern_scenes if s['catalog_scene_id'] == scene_id)
    assert links[0]['primary_formula_ids'] == source['primary_formula_ids']


def test_every_new_batch_passes_online_grounding_without_a_repair_call():
    from ece329_workflow.generator import _format_exploration_scenes
    from ece329_workflow.openai_generator import _validate_lecture_grounding

    session = DesignSession(design_id='new-scenes-grounding', interaction_state=InteractionState.GUIDED_DESIGN)
    packet = build_prompt_packet(session, '我想探索课程中的现象')
    for offset in range(138, 183, 3):
        options = deepcopy(KNOWLEDGE.exploration_points[offset:offset + 3])
        packet['context']['knowledge_retrieval']['brainstorm_options'] = options
        scenes = build_exploration_scenes(options)
        output = StepOutput(assistant_message=_format_exploration_scenes(scenes), stage_payload={
            'brainstorm_phase': 'BREADTH_EXPLORATION',
            'brainstorm_activity': 'RELATIONSHIP_DISCOVERY',
            'input_category': 'COURSE_CONTENT',
            'alternative_ideas': options, 'exploration_scenes': scenes,
        })
        _validate_lecture_grounding(session, output, packet)


def test_cycle_boundary_cannot_be_reordered_by_online_model():
    from ece329_workflow.generator import _format_exploration_scenes
    from ece329_workflow.openai_generator import _validate_lecture_grounding, ModelOutputError

    excluded = {p['option_id'] for p in KNOWLEDGE.exploration_points[:-1]}
    options = KNOWLEDGE.brainstorm_options('', exclude_option_ids=excluded)
    session = DesignSession(design_id='cycle-order', interaction_state=InteractionState.GUIDED_DESIGN)
    packet = build_prompt_packet(session, '我想探索课程中的现象')
    packet['context']['knowledge_retrieval']['brainstorm_options'] = options
    reordered = options[::-1]
    scenes = build_exploration_scenes(reordered)
    output = StepOutput(assistant_message=_format_exploration_scenes(scenes), stage_payload={
        'brainstorm_phase': 'BREADTH_EXPLORATION', 'brainstorm_activity': 'RELATIONSHIP_DISCOVERY',
        'input_category': 'COURSE_CONTENT', 'alternative_ideas': reordered, 'exploration_scenes': scenes,
    })
    with pytest.raises(ModelOutputError, match='cycle boundary'):
        _validate_lecture_grounding(session, output, packet)


def test_supporting_formula_overlap_does_not_route_an_unrelated_scene_to_a_topic():
    # The line-propagation profile also supports wave-speed references. Those
    # helpers must not make its termination comparison a polarization scene.
    related = KNOWLEDGE._relevant_exploration_points('我想研究偏振')
    assert 'ECE329-S147' not in {point['catalog_scene_id'] for point in related}
    assert 'ECE329-S164' in {point['catalog_scene_id'] for point in related}
