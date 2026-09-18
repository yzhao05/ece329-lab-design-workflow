from ece329_workflow.knowledge_base import KNOWLEDGE
from ece329_workflow.emvr_formula_flow import _formula_cards, _format_formula_cards


def test_magnetostatic_card_matches_its_free_space_formula():
    profile = next(p for p in KNOWLEDGE.formula_design_profiles
                   if p['profile_id'] == 'FD12_MAGNETIC_SOURCE_FIELD')
    materialized = KNOWLEDGE._materialize_formula_design_profile(profile)
    cards = _formula_cards([materialized])
    card = cards[0]
    assert '稳恒磁场' in card['title']
    assert not any('磁导率' in quantity for quantity in card['changed_quantities'])
    assert 'μ₀' in card['primary_formulas'][0]['expression']
    visible = _format_formula_cards(cards, '磁场')
    for condition in card['boundary_conditions']:
        assert condition in visible
    assert '不等于空间均匀' in visible
    assert '总磁场需沿给定电流分布积分或叠加' in visible
    assert '定律本身不要求对称性' in visible
    assert KNOWLEDGE.validate() == []


def test_formula_card_does_not_silently_drop_later_applicability_conditions():
    profiles = KNOWLEDGE.design_profiles_for_formula_ids(['biot_savart'], limit=1)
    cards = _formula_cards(profiles)
    cards[0]['boundary_conditions'] = [f'适用前提{i}' for i in range(6)]
    visible = _format_formula_cards(cards, '磁场')
    assert all(condition in visible for condition in cards[0]['boundary_conditions'])
