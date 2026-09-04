from __future__ import annotations

import unittest

from ece329_workflow.dialogue_state import UserIntent, resolved_intent
from ece329_workflow.emvr_formula_flow import (
    EMVR_DETAIL_DESIGN,
    EXPERIMENT_METHODS_PRESENTED,
    EXPERIMENT_DIRECTION_REVIEW,
    FORMULA_CANDIDATES_PRESENTED,
    FORMULA_COMPOSITION_REVIEW,
    TOPIC_RECEIVED,
    ensure_emvr_formula_flow,
    formula_support_map_for_selection,
    handle_emvr_formula_turn,
    normalize_topic_analysis,
    score_formula_profiles,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.emvr_design import apply_emvr_field_updates
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.reporting import (
    build_emvr_task_report,
    effective_emvr_stage_payload,
    effective_experiment_brief,
)


def _topic_analysis() -> dict:
    return {
        "course_domain": "electrostatics",
        "topic_description": "两个带电球靠近时的空间电场变化",
        "mentioned_objects": ["两个带电球"],
        "changed_quantities": ["电荷间距离", "电荷极性配置"],
        "observed_quantities": ["中间区域电场线分布", "零场点位置"],
        "explicit_formula_ids": [],
        "specificity": "PARTIALLY_DEFINED",
        "profile_evidence": [
            {
                "profile_id": "FD02_COULOMB_SUPERPOSITION",
                "course_concept_match": True,
                "variation_match": True,
                "observation_match": True,
                "object_geometry_match": True,
                "boundary_match": True,
                "condition_conflict": False,
            }
        ],
        "confidence": 0.98,
    }


def _formula_intent(action_type: str, content: dict) -> dict:
    formula_action = {"type": action_type, "content": content, "act_id": "test-action"}
    return resolved_intent(
        UserIntent.ANSWER_CURRENT_QUESTION,
        confidence=0.99,
        source="SEMANTIC_TEST",
        semantic_updates={"emvr_formula_actions": [formula_action]},
        dialogue_acts=[
            {
                "type": action_type,
                "target": "emvr_formula_flow",
                "operation": "EXECUTE",
                "content": content,
                "confidence": 0.99,
            }
        ],
        actions_authoritative=True,
    )


class FormulaSemanticGenerator(RuleBasedStageGenerator):
    supports_emvr_formula_flow = True

    def resolve_intent(self, session, user_message, pending_action, carried_context):
        flow = carried_context.get("emvr_formula_flow", {})
        if flow.get("phase") == TOPIC_RECEIVED:
            return _formula_intent("SET_EMVR_TOPIC", _topic_analysis())
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.9,
            source="SEMANTIC_TEST",
        )


class EmvrFormulaFlowTests(unittest.TestCase):
    def _session(self) -> DesignSession:
        session = DesignSession(
            design_id="formula-flow-test",
            interaction_state=InteractionState.EMVR_DIRECT,
            design_context={"idea": {}, "emvr_design": {"field_state": {}}},
        )
        ensure_emvr_formula_flow(session)
        return session

    def test_topic_analysis_accepts_only_catalog_domains_and_formula_ids(self) -> None:
        valid = normalize_topic_analysis(
            {
                **_topic_analysis(),
                "explicit_formula_ids": ["coulomb_point_charge", "invented_formula"],
            }
        )
        invalid = normalize_topic_analysis(
            {**_topic_analysis(), "course_domain": "semiconductor_devices"}
        )

        self.assertEqual(valid["explicit_formula_ids"], ["coulomb_point_charge"])
        self.assertIsNone(invalid)

    def test_broad_electrostatic_topic_shows_formula_families_not_adjacent_theory(self) -> None:
        broad = normalize_topic_analysis(
            {
                **_topic_analysis(),
                "topic_description": "静电场实验",
                "mentioned_objects": [],
                "changed_quantities": [],
                "observed_quantities": [],
                "specificity": "BROAD",
                "profile_evidence": [],
            }
        )
        ranked = score_formula_profiles(broad, limit=4)

        self.assertEqual(
            [item["profile_id"] for item in ranked],
            [
                "FD02_COULOMB_SUPERPOSITION",
                "FD03_GAUSS_FLUX_SYMMETRY",
                "FD04_ELECTROSTATIC_POTENTIAL",
                "FD05_ELECTROSTATIC_BOUNDARY",
            ],
        )
        returned_formula_ids = {
            formula_id
            for item in ranked
            for formula_id in [
                *item["primary_formula_ids"],
                *item["supporting_formula_ids"],
            ]
        }
        self.assertNotIn("lorentz_force", returned_formula_ids)
        self.assertNotIn("ohm_medium", returned_formula_ids)

    def test_candidates_are_not_committed_before_student_confirmation(self) -> None:
        session = self._session()
        output, complete = handle_emvr_formula_turn(
            session,
            "我想研究静电场",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        emvr = session.design_context["emvr_design"]

        self.assertFalse(complete)
        self.assertEqual(output.stage_payload["emvr_formula_phase"], FORMULA_CANDIDATES_PRESENTED)
        self.assertEqual(emvr["formula_flow"]["formula_selection"]["selection_status"], "PENDING")
        self.assertNotIn("selected_primary_formula_ids", emvr)
        self.assertEqual(emvr["field_state"], {})

    def test_semantic_outage_preserves_formula_turn_and_next_action_recovers(self) -> None:
        session = self._session()
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_OPEN_QUESTION_LOCAL_CLARIFICATION",
        )

        outage, complete = handle_emvr_formula_turn(
            session,
            "我想拖动两个带电球，观察距离减小时中间区域的场线变化",
            failed_intent,
        )
        flow = session.design_context["emvr_design"]["formula_flow"]

        self.assertFalse(complete)
        self.assertTrue(outage.stage_payload["semantic_recovery_pending"])
        self.assertIn("不需要重新开始", outage.assistant_message)
        self.assertNotIn("topic_analysis", flow)
        self.assertEqual(
            flow["semantic_recovery"]["messages"],
            ["我想拖动两个带电球，观察距离减小时中间区域的场线变化"],
        )

        recovered, complete = handle_emvr_formula_turn(
            session,
            "按刚才的想法继续",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )

        self.assertFalse(complete)
        self.assertEqual(
            recovered.stage_payload["emvr_formula_phase"],
            FORMULA_CANDIDATES_PRESENTED,
        )
        self.assertNotIn("semantic_recovery", flow)

    def test_effective_brief_preserves_formula_flow_object_enumeration(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        objects = ["两个可拖动带电球", "电场线显示系统", "测量探针"]
        emvr["authoritative_experiment_brief"] = {
            "topic": "两个带电球靠近时的静电场变化",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "selected_experiment_method_ids": ["EMVR-METHOD-test"],
            "objects": objects,
            "operations": ["拖动带电球"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["中间区域电场线"],
            "boundary_conditions": ["静电状态"],
        }
        emvr["field_state"].update(
            {
                "research_object": "、".join(objects),
                "required_behaviors": ["拖动带电球"],
                "changed_quantities": ["电荷间距离"],
                "observed_quantities": ["中间区域电场线"],
                "object_constraints": ["静电状态"],
            }
        )

        brief = effective_experiment_brief(session)

        self.assertEqual(brief["objects"], objects)

    def test_confirmed_formula_drives_generated_methods_and_complete_brief(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        formula_option = cards.stage_payload["formula_cards"][0]["option_id"]
        composition, _ = handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=formula_option,
        )
        selection = session.design_context["emvr_design"]["formula_flow"]["formula_selection"]
        self.assertEqual(
            set(selection["primary_formula_ids"]),
            {"coulomb_point_charge", "electric_field_superposition"},
        )
        self.assertEqual(selection["supporting_formula_ids"], [])
        self.assertNotIn("potential_volume_charge", selection["primary_formula_ids"])
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "把这些公式组合成一个实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertGreater(len(methods.stage_payload["experiment_methods"]), 3)
        self.assertTrue(all(item["pattern_ids"] for item in methods.stage_payload["experiment_methods"]))
        self.assertTrue(methods.stage_payload["coverage_matrix"]["rows"])
        self.assertEqual(len(methods.stage_payload["coverage_matrix"]["columns"]), 15)
        self.assertTrue(
            all(
                item["method_id"].startswith("EMVR-METHOD-")
                for item in methods.stage_payload["experiment_methods"]
            )
        )
        generated_patterns = {
            pattern_id
            for item in methods.stage_payload["experiment_methods"]
            for pattern_id in item["pattern_ids"]
        }
        self.assertIn("INVERSE_PARAMETER_INFERENCE", generated_patterns)

        method_option = methods.stage_payload["experiment_methods"][0]["option_id"]
        review, _ = handle_emvr_formula_turn(
            session,
            "采用方法1",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=method_option,
        )
        self.assertEqual(review.stage_payload["emvr_formula_phase"], EXPERIMENT_DIRECTION_REVIEW)
        self.assertNotIn("coulomb_point_charge", review.assistant_message)
        self.assertIn("公式", review.assistant_message)

        locked, complete = handle_emvr_formula_turn(
            session,
            "确认并继续",
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                semantic_updates={"control_actions": ["ACCEPT"]},
            ),
        )
        emvr = session.design_context["emvr_design"]
        self.assertTrue(complete)
        self.assertEqual(locked.stage_payload["emvr_formula_phase"], EMVR_DETAIL_DESIGN)
        self.assertIn("authoritative_experiment_brief", emvr)
        self.assertEqual(emvr["field_state"]["research_object"], "两个带电球")
        self.assertEqual(emvr["field_state"]["changed_quantities"], ["电荷间距离", "电荷极性配置"])
        support_map = formula_support_map_for_selection(session)
        self.assertEqual(
            {item["formula_id"] for item in support_map},
            {
                *emvr["selected_primary_formula_ids"],
                *emvr["selected_supporting_formula_ids"],
            },
        )
        self.assertTrue(all(item["supports_design_fields"] for item in support_map))

    def test_later_field_revision_updates_formula_brief_and_final_report_view(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        composition, _ = handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "组合为一个实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        handle_emvr_formula_turn(
            session,
            "采用第一种方法",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=methods.stage_payload["experiment_methods"][0]["option_id"],
        )
        handle_emvr_formula_turn(
            session,
            "确认",
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                semantic_updates={"control_actions": ["ACCEPT"]},
            ),
        )

        apply_emvr_field_updates(
            session.design_context["emvr_design"],
            {
                "field_updates": [
                    {
                        "field_id": "experiment_brief",
                        "operation": "REPLACE",
                        "value": "比较电荷距离变化对中间平面电场强度的影响",
                    },
                    {
                        "field_id": "changed_quantities",
                        "operation": "REPLACE",
                        "value": ["两个电荷之间的距离"],
                    },
                    {
                        "field_id": "observed_quantities",
                        "operation": "REPLACE",
                        "value": ["中间平面的电场强度"],
                    },
                ]
            },
        )

        brief = effective_experiment_brief(session)
        report_view = effective_emvr_stage_payload(session, Stage.IDEA_BRAINSTORMING)
        report = build_emvr_task_report(session)
        support_map = formula_support_map_for_selection(session)
        self.assertEqual(
            report["idea"],
            "比较电荷距离变化对中间平面电场强度的影响",
        )
        self.assertEqual(brief["changed_quantities"], ["两个电荷之间的距离"])
        self.assertEqual(brief["observed_quantities"], ["中间平面的电场强度"])
        self.assertEqual(report_view["target_phenomenon"], ["中间平面的电场强度"])
        self.assertTrue(report_view["primary_formulas"])
        self.assertIn("=", report_view["primary_formulas"][0])
        self.assertTrue(
            all("中间平面的电场强度" in item["supports_design_content"] for item in support_map)
        )

    def test_inconsistent_persisted_phase_recovers_instead_of_false_completion(self) -> None:
        session = self._session()
        flow = session.design_context["emvr_design"]["formula_flow"]
        flow["phase"] = EXPERIMENT_METHODS_PRESENTED
        flow["formula_selection"]["primary_formula_ids"] = []
        flow["experiment_methods"] = []

        repaired = ensure_emvr_formula_flow(session)

        self.assertEqual(repaired["phase"], TOPIC_RECEIVED)

    def test_unity_inventory_uses_locked_objects_instead_of_generic_placeholders(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        emvr["authoritative_experiment_brief"] = {
            "topic": "两个带电球的静电场",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "formula_composition_strategy": "SINGLE",
            "selected_experiment_method_ids": ["EMVR-METHOD-test"],
            "selected_experiment_pattern_ids": ["PARAMETER_SWEEP"],
            "objects": ["两个可拖动带电球"],
            "operations": ["用手柄拖动带电球"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["中间区域电场强度"],
            "boundary_conditions": ["均匀线性介质"],
        }
        emvr["field_state"].update(
            {
                "research_object": "两个可拖动带电球",
                "required_behaviors": ["用手柄拖动带电球"],
                "changed_quantities": ["电荷间距离"],
                "observed_quantities": ["中间区域电场强度"],
            }
        )
        session.current_stage_index = list(Stage).index(Stage.CONCEPTUAL_OR_VR_SETUP)

        output = RuleBasedStageGenerator().generate(session, "继续完善Unity对象")
        names = [item["object_name"] for item in output.stage_payload["object_inventory"]]

        self.assertIn("两个可拖动带电球", names)
        self.assertNotIn("学生定义的可交互物理源或带电对象", names)
        self.assertTrue(
            all("中间区域电场强度" in item["visual_feedback"] for item in output.stage_payload["object_inventory"][:1])
        )

    def test_student_can_explicitly_combine_a_second_profile_as_support(self) -> None:
        session = self._session()
        handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        composition, _ = handle_emvr_formula_turn(
            session,
            "以库仑定律为主，并用高斯定律辅助比较",
            _formula_intent(
                "SELECT_EMVR_FORMULAS",
                {
                    "primary_profile_ids": ["FD02_COULOMB_SUPERPOSITION"],
                    "supporting_profile_ids": ["FD03_GAUSS_FLUX_SYMMETRY"],
                    "student_rationale": "用闭合面通量辅助比较",
                },
            ),
        )

        selection = session.design_context["emvr_design"]["formula_flow"]["formula_selection"]
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"], FORMULA_COMPOSITION_REVIEW
        )
        self.assertEqual(
            set(selection["primary_formula_ids"]),
            {"coulomb_point_charge", "electric_field_superposition"},
        )
        self.assertEqual(
            set(selection["supporting_formula_ids"]),
            {"gauss_integral", "gauss_differential"},
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "逐个公式设计后组合",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:separate_then_combine",
        )
        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertTrue(
            all(
                item["primary_formula_ids"]
                for item in methods.stage_payload["experiment_methods"]
            )
        )
        matrix_cells = {
            (row["formula_id"], pattern_id)
            for row in methods.stage_payload["coverage_matrix"]["rows"]
            for pattern_id in row["applicable_pattern_ids"]
        }
        generated_cells = {
            (assignment["formula_id"], assignment["pattern_id"])
            for method in methods.stage_payload["experiment_methods"]
            for assignment in method["formula_pattern_assignments"]
        }
        self.assertLessEqual(matrix_cells, generated_cells)

    def test_formula_choice_and_composition_can_be_handled_in_one_long_turn(self) -> None:
        session = self._session()
        handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        intent = resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "emvr_formula_actions": [
                    {
                        "type": "SELECT_EMVR_FORMULAS",
                        "content": {
                            "primary_profile_ids": ["FD02_COULOMB_SUPERPOSITION"],
                            "supporting_profile_ids": [],
                        },
                    },
                    {
                        "type": "SET_EMVR_FORMULA_COMPOSITION",
                        "content": {"strategy": "SEPARATE_THEN_COMBINE"},
                    },
                ]
            },
        )

        methods, _ = handle_emvr_formula_turn(
            session,
            "采用库仑与叠加公式，并先分别设计小实验再组合",
            intent,
        )

        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertEqual(
            session.design_context["emvr_design"]["formula_flow"]["formula_composition"]["strategy"],
            "SEPARATE_THEN_COMBINE",
        )

    def test_formula_cards_label_supporting_formulas_as_optional(self) -> None:
        session = self._session()
        output, _ = handle_emvr_formula_turn(
            session,
            "我想研究静电场",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )

        self.assertIn("可选辅助", output.assistant_message)

    def test_single_formula_skips_composition_question_and_generates_methods(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究静电边界",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        boundary_card = next(
            item
            for item in cards.stage_payload["formula_cards"]
            if item["profile_id"] == "FD05_ELECTROSTATIC_BOUNDARY"
        )

        methods, _ = handle_emvr_formula_turn(
            session,
            "采用静电边界条件",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=boundary_card["option_id"],
        )

        flow = session.design_context["emvr_design"]["formula_flow"]
        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertEqual(flow["formula_composition"]["strategy"], "SINGLE")
        self.assertTrue(methods.stage_payload["experiment_methods"])

    def test_legacy_fixed_scene_phase_is_migrated_to_pattern_generation(self) -> None:
        session = self._session()
        flow = session.design_context["emvr_design"]["formula_flow"]
        flow["phase"] = "FORMULA_SCENES_PRESENTED"
        flow["formula_scenes"] = [{"scene_id": "ECE329-S001"}]

        migrated = ensure_emvr_formula_flow(session)

        # The retired scene state did not contain a valid confirmed formula
        # selection, so recovery returns to the topic/formula boundary instead
        # of falsely advancing with an empty design.
        self.assertEqual(migrated["phase"], TOPIC_RECEIVED)
        self.assertNotIn("formula_scenes", migrated)

    def test_review_revision_changes_only_named_brief_field(self) -> None:
        session = self._session()
        flow = ensure_emvr_formula_flow(session)
        flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        flow["experiment_brief"] = {
            "topic": "原研究方向",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "selected_scene_ids": ["ECE329-S004"],
            "objects": ["两个点电荷"],
            "operations": ["拖动其中一个电荷"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["电场线形状"],
            "boundary_conditions": ["点电荷近似"],
        }
        output, complete = handle_emvr_formula_turn(
            session,
            "观察量改成中间区域场强，其他不变",
            _formula_intent(
                "REVISE_EMVR_DIRECTION",
                {"brief_updates": {"observed_quantities": ["中间区域场强"]}},
            ),
        )

        self.assertFalse(complete)
        self.assertEqual(flow["experiment_brief"]["observed_quantities"], ["中间区域场强"])
        self.assertEqual(flow["experiment_brief"]["objects"], ["两个点电荷"])
        self.assertEqual(flow["experiment_brief"]["changed_quantities"], ["电荷间距离"])
        self.assertIn("只调整", output.assistant_message)

    def test_review_merge_preserves_existing_items(self) -> None:
        session = self._session()
        flow = ensure_emvr_formula_flow(session)
        flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        flow["experiment_brief"] = {
            "topic": "点电荷空间场",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "selected_scene_ids": ["ECE329-S004"],
            "objects": ["两个点电荷"],
            "operations": ["拖动点电荷"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["电场线形状"],
            "boundary_conditions": ["点电荷近似"],
        }

        handle_emvr_formula_turn(
            session,
            "再加入零场点位置，原来的保留",
            _formula_intent(
                "REVISE_EMVR_DIRECTION",
                {
                    "brief_updates": {
                        "observed_quantities": {
                            "operation": "MERGE",
                            "value": ["零场点位置"],
                        }
                    }
                },
            ),
        )

        self.assertEqual(
            flow["experiment_brief"]["observed_quantities"],
            ["电场线形状", "零场点位置"],
        )

    def test_engine_advances_only_after_formula_scene_and_direction_lock(self) -> None:
        engine = WorkflowEngine(generator=FormulaSemanticGenerator())
        first = engine.create_design(
            "我想研究两个带电球靠近时的电场",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        self.assertEqual(first["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(first["stage_payload"]["emvr_formula_phase"], FORMULA_CANDIDATES_PRESENTED)

        formula = first["stage_payload"]["formula_cards"][0]
        second = engine.process_turn(
            first["design_id"],
            {"message": "采用这组公式", "selected_option_id": formula["option_id"]},
        )
        self.assertEqual(
            second["stage_payload"]["emvr_formula_phase"], FORMULA_COMPOSITION_REVIEW
        )
        third = engine.process_turn(
            first["design_id"],
            {
                "message": "组合成一个完整实验",
                "selected_option_id": "emvr-composition:combined",
            },
        )
        method = third["stage_payload"]["experiment_methods"][0]
        review = engine.process_turn(
            first["design_id"],
            {"message": "采用方法1", "selected_option_id": method["option_id"]},
        )
        self.assertEqual(review["current_stage"], Stage.IDEA_BRAINSTORMING.value)

        fourth = engine.process_turn(
            first["design_id"],
            {"message": "确认方向", "complete_stage": True},
        )
        self.assertEqual(fourth["current_stage"], Stage.COURSE_MAPPING_AND_DIRECTION.value)
        self.assertIn(Stage.IDEA_BRAINSTORMING.value, fourth["completed_stages"] if "completed_stages" in fourth else engine.get_design(first["design_id"])["completed_stages"])

    def test_guided_mode_does_not_initialize_or_render_formula_flow(self) -> None:
        engine = WorkflowEngine(generator=FormulaSemanticGenerator())
        result = engine.create_design("我想研究静电场")
        stored = engine.store.get(result["design_id"])

        self.assertEqual(result["interaction_state"], InteractionState.GUIDED_DESIGN.value)
        self.assertNotIn("formula_cards", result["stage_payload"])
        self.assertNotIn("formula_scenes", result["stage_payload"])
        self.assertNotIn("emvr_design", stored.design_context)

    def test_mode_handoff_keeps_topic_meaning_without_committing_a_brief(self) -> None:
        engine = WorkflowEngine(generator=FormulaSemanticGenerator())
        guided = engine.create_design("我想比较两个电荷靠近时的电场线")
        switched = engine.process_turn(guided["design_id"], {"message": "进入EMVR模式"})
        emvr = engine.store.get(guided["design_id"]).design_context["emvr_design"]

        self.assertEqual(switched["interaction_state"], InteractionState.EMVR_DIRECT.value)
        self.assertEqual(switched["stage_payload"]["emvr_formula_phase"], TOPIC_RECEIVED)
        self.assertIn("两个电荷", emvr["formula_flow"]["topic_seed"])
        self.assertNotIn("experiment_brief", emvr)
        self.assertEqual(emvr["field_state"], {})

    def test_new_topic_from_later_stage_reenters_formula_flow_without_legacy_write(self) -> None:
        class NewTopicGenerator(FormulaSemanticGenerator):
            def resolve_intent(self, session, user_message, pending_action, carried_context):
                if user_message == "换成研究静电边界":
                    return resolved_intent(
                        UserIntent.NEW_TOPIC,
                        confidence=0.99,
                        source="SEMANTIC_TEST",
                        dialogue_acts=[
                            {
                                "type": "NEW_TOPIC_CONTENT",
                                "target": "research_topic",
                                "operation": "REPLACE",
                                "content": "研究静电边界",
                                "confidence": 0.99,
                            }
                        ],
                        actions_authoritative=True,
                        preserve_current_design=False,
                    )
                return super().resolve_intent(
                    session, user_message, pending_action, carried_context
                )

        engine = WorkflowEngine(generator=NewTopicGenerator())
        first = engine.create_design(
            "两个点电荷靠近时的电场",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        formula = first["stage_payload"]["formula_cards"][0]
        scenes = engine.process_turn(
            first["design_id"],
            {"message": "采用公式", "selected_option_id": formula["option_id"]},
        )
        methods = engine.process_turn(
            first["design_id"],
            {
                "message": "组合公式",
                "selected_option_id": "emvr-composition:combined",
            },
        )
        method = methods["stage_payload"]["experiment_methods"][0]
        engine.process_turn(
            first["design_id"],
            {"message": "采用方法", "selected_option_id": method["option_id"]},
        )
        engine.process_turn(
            first["design_id"], {"message": "确认", "complete_stage": True}
        )

        changed = engine.process_turn(first["design_id"], {"message": "换成研究静电边界"})
        emvr = engine.store.get(first["design_id"]).design_context["emvr_design"]
        self.assertEqual(changed["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(changed["stage_payload"]["emvr_formula_phase"], TOPIC_RECEIVED)
        self.assertEqual(emvr["formula_flow"]["topic_seed"], "研究静电边界")
        self.assertNotIn("experiment_brief", emvr)
        self.assertEqual(emvr["field_state"], {})


if __name__ == "__main__":
    unittest.main()
