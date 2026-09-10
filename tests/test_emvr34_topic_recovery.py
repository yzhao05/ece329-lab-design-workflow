"""emvr34: coarse magnetism ideas and rejected formula batches remain usable."""
from copy import deepcopy
import json

import pytest

from ece329_workflow.dialogue_state import UserIntent, resolved_intent, serialize_intent_input
from ece329_workflow.emvr_formula_flow import (
    TOPIC_RECEIVED, FORMULA_CANDIDATES_PRESENTED, FORMULA_COMPOSITION_REVIEW,
    ensure_emvr_formula_flow, handle_emvr_formula_turn, normalize_topic_analysis,
    recover_topic_analysis_from_knowledge, score_formula_profiles,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.knowledge_base import KNOWLEDGE
from ece329_workflow.models import DesignSession, InteractionState
from ece329_workflow.openai_generator import OpenAIStageGenerator, ModelServiceError, generator_from_environment
from tests.test_emvr_formula_flow import FormulaTopicOutageGenerator, _formula_intent
from tests.test_openai_generator import FakeTransport


MAGNETIC_TOPIC = "我想做一个和磁场有关的实验"


def outage():
    return resolved_intent(UserIntent.UNCLEAR, confidence=0.5, source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION")


def fresh_session():
    session = DesignSession(design_id="emvr34", interaction_state=InteractionState.EMVR_DIRECT)
    ensure_emvr_formula_flow(session)
    return session


def assert_domain_cards(output, domain="magnetism"):
    data = output.stage_payload if hasattr(output, "stage_payload") else output["stage_payload"]
    assert data["emvr_formula_phase"] == FORMULA_CANDIDATES_PRESENTED
    cards = data["formula_cards"]
    assert cards
    lookup = {profile["profile_id"]: profile["course_block"] for profile in KNOWLEDGE.formula_design_profiles}
    assert {lookup[card["profile_id"]] for card in cards} == {domain}
    return cards


@pytest.mark.parametrize("message,domain", [
    (MAGNETIC_TOPIC, "magnetism"), ("magnetic field experiment", "magnetism"),
    ("我想做磁场实验，不是电场", "magnetism"), ("不是电场，是磁场", "magnetism"),
    ("不是静电场，是磁场", "magnetism"), ("不想研究静磁场，我想研究电场", "electrostatics"),
    ("我想研究静电场", "electrostatics"), ("我想研究电磁感应", "magnetism"),
    ("我想做电磁场实验", "electromagnetics"), ("电磁波在介质界面反射", "electromagnetics"),
    ("transmission line experiment", "electromagnetics"),
])
def test_catalog_domain_and_candidates_do_not_inherit_overview_lecture_block(message, domain):
    assert KNOWLEDGE.course_domain_for_text(message) == domain
    profiles = KNOWLEDGE.formula_design_references(message, limit=4)
    assert profiles and {profile["course_block"] for profile in profiles} == {domain}
    analysis = recover_topic_analysis_from_knowledge(message)
    assert analysis["specificity"] == "BROAD"
    assert analysis["mentioned_objects"] == analysis["changed_quantities"] == []
    assert {p["course_block"] for p in score_formula_profiles(analysis)} == {domain}


@pytest.mark.parametrize("message", ["我想做一个实验", "banana experiment", "不要磁场", "电场和磁场都感兴趣"])
def test_unidentified_or_ambiguous_topic_does_not_silently_default_to_electrostatics(message):
    assert recover_topic_analysis_from_knowledge(message) is None


@pytest.mark.parametrize("specificity", ["BROAD", "PARTIALLY_DEFINED", "SPECIFIC"])
def test_contradictory_semantic_evidence_cannot_fill_magnetic_cards_with_electric_profiles(specificity):
    analysis = normalize_topic_analysis({
        "course_domain": "electrostatics", "topic_description": MAGNETIC_TOPIC, "specificity": specificity,
        "profile_evidence": [{"profile_id": "FD02_COULOMB_SUPERPOSITION", "course_concept_match": True,
                              "variation_match": True, "observation_match": True}],
    })
    assert analysis["course_domain"] == "magnetism"
    assert {p["course_block"] for p in score_formula_profiles(analysis)} == {"magnetism"}


def test_explicit_formula_keeps_justified_cross_domain_connection():
    analysis = normalize_topic_analysis({"course_domain": "magnetism", "topic_description": MAGNETIC_TOPIC,
                                         "explicit_formula_ids": ["lorentz_force"], "specificity": "SPECIFIC"})
    assert any("lorentz_force" in p["supporting_formula_ids"] for p in score_formula_profiles(analysis))


def test_catalog_related_domain_requires_substantive_semantic_evidence():
    analysis = normalize_topic_analysis({"course_domain": "magnetism", "topic_description": "带电粒子在磁场中运动时受到的力",
        "specificity": "SPECIFIC", "mentioned_objects": ["运动电荷"], "changed_quantities": ["速度方向"],
        "observed_quantities": ["受力方向"], "profile_evidence": [{"profile_id": "FD01_FIELD_FOUNDATIONS",
            "course_concept_match": True, "variation_match": True, "observation_match": True}]})
    assert score_formula_profiles(analysis)[0]["profile_id"] == "FD01_FIELD_FOUNDATIONS"
    assert not KNOWLEDGE.validate()


def test_coarse_magnetic_idea_survives_total_semantic_service_failure_in_engine():
    engine = WorkflowEngine(generator=FormulaTopicOutageGenerator())
    created = engine.create_design("进入emvr模式", interaction_state=InteractionState.EMVR_DIRECT)
    response = engine.process_turn(created["design_id"], {"message": MAGNETIC_TOPIC})
    assert_domain_cards(response)
    flow = engine.store.get(created["design_id"]).design_context["emvr_design"]["formula_flow"]
    assert flow["formula_selection"]["primary_formula_ids"] == []
    assert flow["formula_selection"]["selection_status"] == "PENDING"
    assert "semantic_recovery" not in flow


@pytest.mark.parametrize("failure", ["transport", "invalid_shape"])
def test_real_online_adapter_failure_recovers_topic_at_engine_boundary(failure):
    transport = (FakeTransport(error=ModelServiceError("simulated unavailable service"))
                 if failure == "transport" else FakeTransport(output={"unexpected": "invalid schema"}))
    engine = WorkflowEngine(generator=generator_from_environment({"ECE329_GENERATOR": "openai"}, transport=transport))
    created = engine.create_design("进入emvr模式", interaction_state=InteractionState.EMVR_DIRECT)
    result = engine.process_turn(created["design_id"], {"message": MAGNETIC_TOPIC})
    assert_domain_cards(result)
    assert "没有完成解析" not in result["assistant_message"]
    assert transport.requests  # Exercise the actual adapter, not a hand-made fallback intent.


def test_existing_emvr34_session_correction_survives_actual_online_adapter_outage():
    transport = FakeTransport(error=ModelServiceError("simulated service failure"))
    engine = WorkflowEngine(generator=generator_from_environment({"ECE329_GENERATOR": "openai"}, transport=transport))
    session = legacy_wrong_candidates()
    engine.store.save(session)
    result = engine.process_turn(session.design_id, {"message": "你这四个和磁场没关系，换一组"})
    assert_domain_cards(result)
    assert "没有完成解析" not in result["assistant_message"]


def test_first_message_can_supply_broad_topic_without_requiring_reentry():
    engine = WorkflowEngine(generator=FormulaTopicOutageGenerator())
    result = engine.create_design("EMVR：我想做一个磁场实验", interaction_state=InteractionState.EMVR_DIRECT)
    assert_domain_cards(result)


def legacy_wrong_candidates():
    session = fresh_session()
    handle_emvr_formula_turn(session, "静电场", _formula_intent("SET_EMVR_TOPIC", {
        "course_domain": "electrostatics", "topic_description": "静电场", "specificity": "BROAD",
    }))
    flow = ensure_emvr_formula_flow(session)
    flow["topic_analysis"]["topic_description"] = MAGNETIC_TOPIC
    # This deliberately models the old emvr34 bug, including stale derived data.
    flow["experiment_methods"] = [{"method_id": "old-electric-method"}]
    flow["experiment_brief"] = {"topic": "错误静电方向"}
    return session


@pytest.mark.parametrize("correction", ["你这四个和磁场没关系，换一组", "这些不是磁场公式，请换一组", "换一组", "These are wrong formulas; I want a magnetic field experiment"])
def test_existing_wrong_batch_can_be_corrected_during_outage_without_selecting_it(correction):
    session = legacy_wrong_candidates()
    output, completed = handle_emvr_formula_turn(session, correction, outage(), selected_option_id="emvr-formula:FD02_COULOMB_SUPERPOSITION")
    assert_domain_cards(output)
    flow = ensure_emvr_formula_flow(session)
    assert not completed
    assert flow["formula_selection"]["primary_formula_ids"] == []
    assert "experiment_methods" not in flow and "experiment_brief" not in flow
    assert "没有完成解析" not in output.assistant_message


def test_successful_semantic_topic_correction_keeps_specific_user_details():
    session = legacy_wrong_candidates()
    analysis = recover_topic_analysis_from_knowledge(MAGNETIC_TOPIC)
    analysis.update({"specificity": "PARTIALLY_DEFINED", "mentioned_objects": ["长螺线管"],
                     "changed_quantities": ["电流"], "observed_quantities": ["中心磁场"]})
    output, _ = handle_emvr_formula_turn(session, "你这四个和磁场没关系，换一组", _formula_intent("SET_EMVR_TOPIC", analysis))
    assert_domain_cards(output)
    assert ensure_emvr_formula_flow(session)["topic_analysis"]["changed_quantities"] == ["电流"]


def test_rejecting_already_related_batch_shows_remaining_related_candidates_only():
    session = fresh_session()
    first, _ = handle_emvr_formula_turn(session, MAGNETIC_TOPIC, _formula_intent("SET_EMVR_TOPIC", recover_topic_analysis_from_knowledge(MAGNETIC_TOPIC)))
    previous_ids = {c["profile_id"] for c in assert_domain_cards(first)}
    second, _ = handle_emvr_formula_turn(session, "换一组", outage())
    next_ids = {c["profile_id"] for c in assert_domain_cards(second)}
    assert not previous_ids & next_ids
    exhausted, _ = handle_emvr_formula_turn(session, "换一组", outage())
    assert exhausted.stage_payload["emvr_formula_phase"] == TOPIC_RECEIVED
    assert not exhausted.stage_payload.get("formula_cards")


def test_specific_magnetic_concept_ranks_its_formula_family_first():
    for text, profile in [("研究长螺线管磁场", "FD13_CURRENT_SHEET_SOLENOID"), ("研究电磁感应", "FD15_FARADAY_INDUCTION")]:
        analysis = recover_topic_analysis_from_knowledge(text)
        assert score_formula_profiles(analysis)[0]["profile_id"] == profile


def test_induction_candidates_do_not_offer_electrostatic_zero_circulation():
    profiles = score_formula_profiles(recover_topic_analysis_from_knowledge("研究电磁感应"))
    induction = next(p for p in profiles if p["profile_id"] == "FD15_FARADAY_INDUCTION")
    assert "stokes_theorem_e" not in induction["supporting_formula_ids"]
    assert not KNOWLEDGE.validate()


def test_focused_parser_accepts_topic_correction_in_formula_choice_phase():
    analysis = recover_topic_analysis_from_knowledge(MAGNETIC_TOPIC)
    action = {"type": "SET_EMVR_TOPIC", "target": "emvr_formula_topic", "operation": "EXECUTE",
              "content": analysis, "confidence": 0.98, "source_text": MAGNETIC_TOPIC}
    transport = FakeTransport(output={"actions": [{**action, "content": json.dumps(analysis), "semantic_key": "correct_topic"}]})
    generator = OpenAIStageGenerator(transport=transport)
    raw, _, _ = generator._recover_emvr_formula_phase("{}", FORMULA_CANDIDATES_PRESENTED)
    assert raw["dialogue_acts"][0]["type"] == "SET_EMVR_TOPIC"
    assert "SET_EMVR_TOPIC" in transport.requests[0]["instructions"]


def test_online_catalog_recovery_uses_same_domain_rules_and_can_recover_rejection():
    session = legacy_wrong_candidates()
    text = serialize_intent_input(session, "你这四个和磁场没关系，换一组", None,
                                  {"emvr_formula_flow": ensure_emvr_formula_flow(session)})
    result = OpenAIStageGenerator._catalog_grounded_emvr_topic_recovery(text, FORMULA_CANDIDATES_PRESENTED)
    assert result is not None and result[1]["course_domain"] == "magnetism"
    selection_text = serialize_intent_input(session, "选第一组", None, {"emvr_formula_flow": ensure_emvr_formula_flow(session)})
    assert OpenAIStageGenerator._catalog_grounded_emvr_topic_recovery(selection_text, FORMULA_CANDIDATES_PRESENTED) is None


def test_selected_magnetic_formulas_generate_related_methods_without_electrostatic_seed():
    session = fresh_session()
    cards, _ = handle_emvr_formula_turn(session, MAGNETIC_TOPIC, _formula_intent("SET_EMVR_TOPIC", recover_topic_analysis_from_knowledge(MAGNETIC_TOPIC)))
    choice = next(c for c in assert_domain_cards(cards) if c["profile_id"] == "FD12_MAGNETIC_SOURCE_FIELD")
    response, _ = handle_emvr_formula_turn(session, "采用磁场源关系", resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), selected_option_id=choice["option_id"])
    if response.stage_payload["emvr_formula_phase"] == FORMULA_COMPOSITION_REVIEW:
        response, _ = handle_emvr_formula_turn(session, "组合成实验", resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION), selected_option_id="emvr-composition:combined")
    assert response.stage_payload["experiment_methods"]
    assert "点电荷" not in json.dumps(response.stage_payload["experiment_methods"], ensure_ascii=False)
    assert "coulomb_point_charge" not in ensure_emvr_formula_flow(session)["formula_selection"]["primary_formula_ids"]


def test_unsubstantiated_explicit_formula_cannot_bypass_domain_guard():
    analysis = normalize_topic_analysis({"course_domain": "magnetism", "topic_description": MAGNETIC_TOPIC,
        "specificity": "SPECIFIC", "explicit_formula_ids": ["coulomb_point_charge"]})
    assert {p["course_block"] for p in score_formula_profiles(analysis)} == {"magnetism"}


@pytest.mark.parametrize("text", ["不要磁场", "电场和磁场都感兴趣"])
def test_online_broad_topic_cannot_resolve_ambiguity_by_inventing_one_domain(text):
    assert normalize_topic_analysis({"course_domain": "electrostatics", "topic_description": text, "specificity": "BROAD"}) is None


def test_new_topic_correction_discards_deferred_old_topic_action():
    session = legacy_wrong_candidates()
    flow = ensure_emvr_formula_flow(session)
    flow["deferred_formula_actions"] = _formula_intent("SET_EMVR_TOPIC", {
        "course_domain": "electrostatics", "topic_description": "静电场", "specificity": "BROAD",
    })["semantic_updates"]["emvr_formula_actions"]
    output, _ = handle_emvr_formula_turn(session, "换一组", outage())
    assert_domain_cards(output)
    assert "deferred_formula_actions" not in flow


def test_direct_topic_change_during_outage_does_not_require_reject_keyword():
    session = legacy_wrong_candidates()
    output, _ = handle_emvr_formula_turn(session, "我想改成传输线实验", outage())
    assert_domain_cards(output, "electromagnetics")


def test_exhausted_candidates_stay_exhausted_on_reparsed_identical_topic():
    session = fresh_session()
    analysis = recover_topic_analysis_from_knowledge(MAGNETIC_TOPIC)
    handle_emvr_formula_turn(session, MAGNETIC_TOPIC, _formula_intent("SET_EMVR_TOPIC", analysis))
    handle_emvr_formula_turn(session, "换一组", outage())
    handle_emvr_formula_turn(session, "换一组", outage())
    excluded = deepcopy(ensure_emvr_formula_flow(session)["excluded_profile_ids"])
    for _ in range(5):
        output, _ = handle_emvr_formula_turn(session, "继续", _formula_intent("SET_EMVR_TOPIC", analysis))
        assert output.stage_payload["emvr_formula_phase"] == TOPIC_RECEIVED
        assert "用尽" in output.assistant_message
        assert ensure_emvr_formula_flow(session)["excluded_profile_ids"] == excluded
    paraphrased = {**analysis, "topic_description": "探索磁场及其空间表现"}
    output, _ = handle_emvr_formula_turn(session, "继续", _formula_intent("SET_EMVR_TOPIC", paraphrased))
    assert output.stage_payload["emvr_formula_phase"] == TOPIC_RECEIVED
    assert ensure_emvr_formula_flow(session)["excluded_profile_ids"] == excluded
    refined = recover_topic_analysis_from_knowledge("研究长螺线管磁场，改变电流观察中心磁场")
    output, _ = handle_emvr_formula_turn(session, refined["topic_description"], _formula_intent("SET_EMVR_TOPIC", refined))
    assert_domain_cards(output)


def test_selected_profile_cannot_import_formula_from_an_unselected_card():
    session = fresh_session()
    handle_emvr_formula_turn(session, MAGNETIC_TOPIC, _formula_intent("SET_EMVR_TOPIC", recover_topic_analysis_from_knowledge(MAGNETIC_TOPIC)))
    handle_emvr_formula_turn(session, "选择长螺线管", _formula_intent("SELECT_EMVR_FORMULAS", {
        "primary_profile_ids": ["FD13_CURRENT_SHEET_SOLENOID"],
        "primary_formula_ids": ["faraday_differential"],
    }))
    assert "faraday_differential" not in ensure_emvr_formula_flow(session)["formula_selection"]["primary_formula_ids"]
