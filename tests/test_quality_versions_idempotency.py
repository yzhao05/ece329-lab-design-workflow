from __future__ import annotations

import unittest

from ece329_workflow.design_quality import evaluate_design_quality
from ece329_workflow.design_state import (
    apply_design_updates,
    ensure_design_state,
    set_baseline_comparisons,
)
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import UserIntent, resolved_intent
from ece329_workflow.emvr_design import (
    apply_emvr_field_updates,
    merge_emvr_structured_requirements,
)
from ece329_workflow.design_versions import (
    ensure_initial_version,
    execute_version_request,
    record_design_version,
)
from ece329_workflow.engine import WorkflowEngine, _record_mode_handoff
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import (
    DesignAccessDenied,
    DesignSession,
    InteractionState,
    SessionConflict,
    Stage,
    TurnRequest,
    WorkflowStatus,
)
from ece329_workflow.reporting import effective_emvr_stage_payload
from ece329_workflow.reporting import effective_experiment_brief
from ece329_workflow.builder_requirements import builder_requirement_values


class DesignQualityTests(unittest.TestCase):
    def test_quality_review_builds_causal_chain_and_reports_real_gaps(self) -> None:
        session = DesignSession("quality", InteractionState.GUIDED_DESIGN)
        ensure_design_state(session)
        apply_design_updates(
            session,
            [
                {"field": "research_object", "operation": "REPLACE", "value": "两个点电荷"},
                {
                    "field": "research_question",
                    "operation": "REPLACE",
                    "value": "距离从远到近时，中间区域的电场线如何变化？",
                },
            ],
            provenance="STUDENT",
        )
        session.design_context["stage_design_state"] = {
            "independent_variable": "两个电荷之间的距离",
            "observations": "中间区域电场线的弯曲与疏密",
            "field_provenance": {
                "independent_variable": "STUDENT",
                "observations": "STUDENT",
            },
        }

        review = evaluate_design_quality(session)

        self.assertEqual(review["causal_chain"]["cause"], "两个电荷之间的距离")
        self.assertIn("电场线", review["causal_chain"]["response"])
        self.assertFalse(review["feasibility"]["controls_are_defined"])
        self.assertTrue(
            any(item["source_type"] == "STUDENT" for item in review["traceability"])
        )

    def test_semantic_consistency_issue_is_preserved_beside_structural_review(self) -> None:
        session = DesignSession("semantic-quality", InteractionState.EMVR_DIRECT)
        assessment = {
            "issues": [
                {
                    "category": "CONSISTENCY",
                    "severity": "MAJOR",
                    "fields": ["research_question", "independent_variable"],
                    "finding": "研究问题讨论距离，但当前唯一自变量写成了材料。",
                    "suggestion": "保留距离为主自变量，把材料作为比较条件。",
                    "student_question": "是否保留距离变化作为主线？",
                }
            ]
        }

        review = evaluate_design_quality(session, assessment)

        self.assertTrue(
            any(item["source"] == "SEMANTIC_REVIEW" for item in review["issues"])
        )

    def test_final_review_does_not_reask_confirmed_result_interpretation(self) -> None:
        session = DesignSession(
            "quality-result-carry-forward",
            InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT),
        )
        result_interpretation = (
            "若场线变化符合叠加预测，则支持当前解释；若偏离，先检查边界设置和显示精度。"
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "result_interpretation",
                    "operation": "REPLACE",
                    "value": result_interpretation,
                }
            ],
            stage=Stage.RESULT_INTERPRETATION,
            provenance="STUDENT_CONFIRMED",
        )

        review = evaluate_design_quality(session, final_review=True)

        self.assertEqual(
            session.design_context["stage_design_state"]["result_interpretation"],
            result_interpretation,
        )
        completeness_fields = {
            field
            for issue in review["issues"]
            if issue.get("category") == "COMPLETENESS"
            for field in issue.get("fields", [])
        }
        self.assertNotIn("result_interpretation", completeness_fields)

    def test_quality_panel_uses_visible_comparison_cases_not_internal_metadata(self) -> None:
        session = DesignSession("quality-comparison", InteractionState.GUIDED_DESIGN)
        set_baseline_comparisons(
            session,
            [
                {
                    "comparison_id": "electrostatic_source_polarity_pair",
                    "title": "电荷极性",
                    "cases": ["同种电荷", "同号点电荷", "异种电荷"],
                    "case_semantic_keys": {
                        "同种电荷": "same_polarity",
                        "同号点电荷": "same_polarity",
                        "异种电荷": "opposite_polarity",
                    },
                    "adoption_status": "ACCEPTED",
                    "source_reference": "lecture_02",
                }
            ],
        )

        review = evaluate_design_quality(session)

        comparison = review["causal_chain"]["comparison"]
        self.assertEqual(comparison, "同种电荷；异种电荷")
        self.assertNotIn("electrostatic_source_polarity_pair", comparison)
        self.assertNotIn("ACCEPTED", comparison)
        self.assertNotIn("lecture_02", comparison)

    def test_quality_trace_shows_committed_value_and_friendly_source(self) -> None:
        session = DesignSession("quality-trace", InteractionState.GUIDED_DESIGN)
        question = "距离从远到近时，两种极性配置的中间区域场线怎样变化？"
        apply_design_updates(
            session,
            [
                {
                    "field": "research_question",
                    "operation": "REPLACE",
                    "value": question,
                }
            ],
            provenance="STUDENT_CONFIRMED",
        )

        review = evaluate_design_quality(session)
        trace = next(
            item
            for item in review["traceability"]
            if item["design_field"] == "research_question"
        )

        self.assertEqual(trace["course_item"], question)
        self.assertIn("已写入当前设计", trace["purpose"])
        self.assertIn("学生确认", trace["purpose"])
        self.assertNotEqual(trace["course_item"], trace["design_field_label"])

    def test_emvr_aggregate_objectives_keep_legacy_provenance(self) -> None:
        session = DesignSession("quality-emvr-objectives", InteractionState.EMVR_DIRECT)
        session.design_context = {
            "emvr_design": {
                "field_state": {
                    "learning_objectives": ["解释电荷间距如何改变电场线分布"],
                }
            },
            "stage_design_state": {
                "field_provenance": {
                    "learning_objectives": [
                        {"source": "STUDENT_CONFIRMED", "revision": 3}
                    ]
                }
            },
        }

        review = evaluate_design_quality(session)
        trace = next(
            item
            for item in review["traceability"]
            if item["design_field"] == "learning_objective"
        )

        self.assertEqual(trace["course_item"], "解释电荷间距如何改变电场线分布")
        self.assertEqual(trace["source_type"], "STUDENT_CONFIRMED")

    def test_mode_handoff_preserves_design_meaning_and_open_issues(self) -> None:
        session = DesignSession("handoff", InteractionState.GUIDED_DESIGN)
        apply_design_updates(
            session,
            [
                {"field": "research_question", "operation": "REPLACE", "value": "比较距离变化"},
                {"field": "learning_objective", "operation": "REPLACE", "value": "解释场叠加"},
                {"field": "observations", "operation": "REPLACE", "value": "场线弯曲"},
            ],
            provenance="STUDENT",
        )
        session.design_context["quality_review"] = {
            "issues": [{"finding": "控制条件仍需明确"}]
        }

        handoff = _record_mode_handoff(
            session,
            InteractionState.GUIDED_DESIGN,
            InteractionState.EMVR_DIRECT,
        )

        self.assertEqual(handoff["research_question"], "比较距离变化")
        self.assertEqual(handoff["learning_objective"], "解释场叠加")
        self.assertEqual(handoff["unresolved_quality_issues"][0]["finding"], "控制条件仍需明确")


class DesignVersionTests(unittest.TestCase):
    def test_valid_emvr_parameter_answer_and_advance_moves_forward_once(self) -> None:
        class ParameterAndAdvanceGenerator(RuleBasedStageGenerator):
            def resolve_intent(
                self,
                session,
                user_message,
                pending_action,
                carried_context,
            ):
                return resolved_intent(
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                    confidence=0.99,
                    source="SEMANTIC_MODEL",
                    dialogue_acts=[
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "parameter_specifications",
                            "operation": "REPLACE",
                            "content": [
                                "距离：可调公式自变量，默认2米，最小值0.5米，最大值5米，步长0.1米",
                                "电荷类型：可调离散自变量，默认同种电荷，选项同种电荷、异种电荷",
                            ],
                            "confidence": 0.99,
                        },
                        {
                            "type": "CONTROL",
                            "target": "ADVANCE",
                            "operation": "EXECUTE",
                            "content": None,
                            "confidence": 0.99,
                        },
                    ],
                    actions_authoritative=True,
                )

        engine = WorkflowEngine(generator=ParameterAndAdvanceGenerator())
        session = DesignSession(
            "emvr-parameter-advance",
            InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={
                "stage_design_state": {
                    "lab_title": "点电荷场线实验",
                    "lab_id": "ece329_charge_field_0",
                    "desktop_interaction_plan": "鼠标拖拽点电荷；VR手柄抓取点电荷",
                    "room_spatial_requirements": "学生站在中央，对象位于前方并保留两米空间",
                    "hidden_object_lifecycle": "无",
                    "builder_workspace_absolute_path": r"E:\暑研\EMVR_Blind_BuilderPack",
                    "initial_reset_state": "Initial与Reset均恢复同种电荷、距离2.0 m并清除测量读数。",
                    "model_constants_and_media": "固定真空介电常数epsilon_0=8.8541878128e-12 F/m，介质固定为真空。",
                    "controlled_conditions": "电荷量、介质和观察方式",
                    "reference_condition": "两球间距2米的同种电荷状态",
                },
                "emvr_design": {
                    "field_state": {
                        "changed_quantities": ["距离", "电荷类型"],
                        "observed_quantities": ["场线的合并、扭曲和重排形态"],
                    }
                },
            },
        )
        session.stage_outputs[Stage.VARIABLES_AND_CONDITIONS.value] = {
            "stage_payload": {
                "independent_variable": {"name": "距离；电荷类型"},
                "dependent_variable": {"name": "场线形态"},
                "controlled_variables": "电荷量、介质和观察方式",
                "reference_condition": "两球间距2米的同种电荷状态",
                "awaiting_user_design_input": True,
            }
        }
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {
                "message": (
                    "距离：最小值0.5米，最大值5米，步长0.1米，单位米。"
                    "电荷类型：离散选项同种电荷、异种电荷。可以继续。"
                )
            },
        )

        self.assertEqual(
            result["current_stage"],
            Stage.CONCEPTUAL_PROCEDURE.value,
        )
        self.assertNotIn("参数范围与单位", result["assistant_message"])
        stored = engine.store.get(session.design_id)
        self.assertIn(
            "0.5米",
            str(
                stored.design_context["emvr_design"]["stage_inputs"]
                [Stage.VARIABLES_AND_CONDITIONS.value][-1]["content"]
            ),
        )
        self.assertEqual(
            stored.completed_stages.count(Stage.VARIABLES_AND_CONDITIONS.value),
            1,
        )

    def test_engine_records_emvr_field_edit_in_same_turn(self) -> None:
        class EmvrEditGenerator(RuleBasedStageGenerator):
            def resolve_intent(
                self,
                session,
                user_message,
                pending_action,
                carried_context,
            ):
                return resolved_intent(
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                    confidence=0.99,
                    source="SEMANTIC_MODEL",
                    dialogue_acts=[
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "changed_quantities",
                            "operation": "REPLACE",
                            "content": ["两球间距"],
                            "confidence": 0.99,
                        }
                    ],
                    actions_authoritative=True,
                )

        engine = WorkflowEngine(generator=EmvrEditGenerator())
        session = DesignSession(
            "engine-emvr-version",
            InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={"emvr_design": {}},
        )
        engine.store.save(session)

        engine.process_turn(session.design_id, {"message": "变化量改为两球间距"})

        stored = engine.store.get(session.design_id)
        versions = stored.model_context["design_versions"]
        self.assertEqual(len(versions), 2)
        self.assertIn("changed_quantities", versions[-1]["changed_fields"])
        self.assertEqual(
            merge_emvr_structured_requirements(
                stored.design_context["emvr_design"]
            )["changed_quantities"],
            ["两球间距"],
        )

    def test_engine_applies_contentless_emvr_clear_and_versions_it(self) -> None:
        class EmvrClearGenerator(RuleBasedStageGenerator):
            def resolve_intent(
                self,
                session,
                user_message,
                pending_action,
                carried_context,
            ):
                return resolved_intent(
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                    confidence=0.99,
                    source="SEMANTIC_MODEL",
                    dialogue_acts=[
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "changed_quantities",
                            "operation": "CLEAR",
                            "content": None,
                            "confidence": 0.99,
                        }
                    ],
                    actions_authoritative=True,
                )

        engine = WorkflowEngine(generator=EmvrClearGenerator())
        session = DesignSession(
            "engine-emvr-clear",
            InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={
                "emvr_design": {
                    "structured_requirements": {
                        Stage.RESEARCH_QUESTION.value: {
                            "changed_quantities": ["旧距离参数"],
                        }
                    },
                    "field_state": {"changed_quantities": ["旧距离参数"]},
                }
            },
        )
        engine.store.save(session)

        engine.process_turn(session.design_id, {"message": "删除原来的变化量"})

        stored = engine.store.get(session.design_id)
        self.assertNotIn(
            "changed_quantities",
            merge_emvr_structured_requirements(
                stored.design_context["emvr_design"]
            ),
        )
        self.assertIn(
            "changed_quantities",
            stored.model_context["design_versions"][-1]["changed_fields"],
        )

    def test_emvr_only_edit_creates_version_and_can_be_undone(self) -> None:
        session = DesignSession("emvr-versions", InteractionState.EMVR_DIRECT)
        emvr = session.design_context.setdefault("emvr_design", {})
        ensure_initial_version(session)
        apply_emvr_field_updates(
            emvr,
            {
                "field_updates": [
                    {
                        "field_id": "changed_quantities",
                        "operation": "REPLACE",
                        "value": ["两球间距"],
                    }
                ]
            },
        )

        version = record_design_version(
            session,
            changed_fields=["changed_quantities"],
            reason="修改EMVR变化量",
        )
        result = execute_version_request(
            session,
            {"action": "UNDO_LAST", "fields": ["changed_quantities"]},
        )

        self.assertIsNotNone(version)
        self.assertEqual(result["changed_fields"], ["changed_quantities"])
        self.assertNotIn(
            "changed_quantities",
            merge_emvr_structured_requirements(emvr),
        )

    def test_guided_version_diff_ignores_stale_emvr_aliases(self) -> None:
        session = DesignSession("guided-after-emvr", InteractionState.GUIDED_DESIGN)
        session.design_context["emvr_design"] = {
            "field_state": {"limitations": ["旧EMVR局限"]}
        }
        ensure_initial_version(session)
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "limitations",
                    "operation": "REPLACE",
                    "value": "新的引导模式局限",
                }
            ],
            stage=Stage.DESIGN_VALUE_AND_LIMITATIONS,
        )

        version = record_design_version(
            session,
            changed_fields=["limitations"],
            reason="修改引导模式局限",
        )

        self.assertIsNotNone(version)

    def test_emvr_clear_hides_older_stage_snapshot_and_report_row(self) -> None:
        session = DesignSession("emvr-clear", InteractionState.EMVR_DIRECT)
        session.current_stage_index = list(Stage).index(Stage.RESEARCH_QUESTION)
        session.design_context["emvr_design"] = {
            "structured_requirements": {
                Stage.RESEARCH_QUESTION.value: {
                    "changed_quantities": ["旧距离参数"],
                }
            },
            "field_state": {"changed_quantities": ["旧距离参数"]},
        }
        session.stage_outputs[Stage.RESEARCH_QUESTION.value] = {
            "stage_payload": {"adjustable_quantity_in_vr": ["旧距离参数"]}
        }

        apply_emvr_field_updates(
            session.design_context["emvr_design"],
            {
                "field_updates": [
                    {
                        "field_id": "changed_quantities",
                        "operation": "CLEAR",
                        "value": None,
                    }
                ]
            },
        )

        self.assertNotIn(
            "changed_quantities",
            merge_emvr_structured_requirements(
                session.design_context["emvr_design"]
            ),
        )
        self.assertNotIn(
            "adjustable_quantity_in_vr",
            effective_emvr_stage_payload(session, Stage.RESEARCH_QUESTION),
        )

    def test_emvr_clear_cannot_be_revived_by_formula_brief_or_stage_cache(self) -> None:
        session = DesignSession("emvr-clear-projections", InteractionState.EMVR_DIRECT)
        session.design_context["emvr_design"] = {
            "authoritative_experiment_brief": {
                "objects": ["两个点电荷"],
                "operations": ["拖拽点电荷"],
                "observed_quantities": ["旧场线形态"],
            },
            "field_state": {
                "research_object": "两个点电荷",
                "required_behaviors": ["拖拽点电荷"],
                "observed_quantities": ["旧场线形态"],
                "desktop_interaction_plan": "旧桌面操作",
            },
        }
        session.stage_outputs[Stage.IDEA_BRAINSTORMING.value] = {
            "stage_payload": {
                "target_phenomenon": ["旧场线形态"],
                "possible_vr_interactions": ["拖拽点电荷"],
            }
        }
        session.stage_outputs[Stage.CONCEPTUAL_OR_VR_SETUP.value] = {
            "stage_payload": {
                "desktop_interaction_plan": "旧桌面操作",
                "interactions": ["拖拽点电荷"],
            }
        }

        apply_emvr_field_updates(
            session.design_context["emvr_design"],
            {
                "field_updates": [
                    {"field_id": "research_object", "operation": "CLEAR"},
                    {"field_id": "required_behaviors", "operation": "CLEAR"},
                    {"field_id": "observed_quantities", "operation": "CLEAR"},
                    {"field_id": "desktop_interaction_plan", "operation": "CLEAR"},
                ]
            },
        )

        brief = effective_experiment_brief(session)
        idea = effective_emvr_stage_payload(session, Stage.IDEA_BRAINSTORMING)
        setup = effective_emvr_stage_payload(session, Stage.CONCEPTUAL_OR_VR_SETUP)

        self.assertEqual(brief["objects"], [])
        self.assertEqual(brief["operations"], [])
        self.assertEqual(brief["observed_quantities"], [])
        self.assertNotIn("target_phenomenon", idea)
        self.assertNotIn("possible_vr_interactions", idea)
        self.assertNotIn("desktop_interaction_plan", setup)
        self.assertNotIn("interactions", setup)

    def test_cleared_builder_requirement_does_not_fall_back_to_stale_stage_value(self) -> None:
        session = DesignSession("emvr-clear-builder", InteractionState.EMVR_DIRECT)
        session.design_context["stage_design_state"] = {
            "parameter_specifications": "距离0.5米至5米，步长0.1米",
        }
        session.design_context["emvr_design"] = {
            "field_state": {
                "parameter_specifications": ["距离0.5米至5米，步长0.1米"],
            }
        }
        apply_emvr_field_updates(
            session.design_context["emvr_design"],
            {
                "field_updates": [
                    {"field_id": "parameter_specifications", "operation": "CLEAR"}
                ]
            },
        )

        self.assertEqual(
            builder_requirement_values(session)["parameter_specifications"],
            "",
        )

    def test_field_level_undo_does_not_erase_other_fields(self) -> None:
        session = DesignSession("versions", InteractionState.GUIDED_DESIGN)
        ensure_initial_version(session)
        apply_design_updates(
            session,
            [{"field": "research_object", "operation": "REPLACE", "value": "两个电荷"}],
            provenance="STUDENT",
        )
        record_design_version(
            session,
            changed_fields=["research_object"],
            reason="明确研究对象",
        )
        apply_design_updates(
            session,
            [{"field": "research_question", "operation": "REPLACE", "value": "距离如何改变场线"}],
            provenance="STUDENT",
        )
        record_design_version(
            session,
            changed_fields=["research_question"],
            reason="明确研究问题",
        )

        result = execute_version_request(
            session,
            {"action": "UNDO_LAST", "fields": ["research_question"]},
        )
        state = ensure_design_state(session)

        self.assertEqual(result["changed_fields"], ["research_question"])
        self.assertEqual(state["research_object"], "两个电荷")
        self.assertEqual(state["research_question"], "")

    def test_version_compare_returns_field_level_differences(self) -> None:
        session = DesignSession("compare", InteractionState.GUIDED_DESIGN)
        ensure_initial_version(session)
        apply_design_updates(
            session,
            [{"field": "learning_objective", "operation": "REPLACE", "value": "解释反射"}],
            provenance="STUDENT",
        )
        latest = record_design_version(
            session,
            changed_fields=["learning_objective"],
            reason="补充目标",
        )

        result = execute_version_request(
            session,
            {"action": "COMPARE", "version_id": "v0001", "other_version_id": latest["version_id"]},
        )

        self.assertEqual(result["differences"][0]["field"], "learning_objective")

    def test_restoring_a_field_also_restores_its_report_payload(self) -> None:
        session = DesignSession("report-restore", InteractionState.EMVR_DIRECT)
        apply_design_updates(
            session,
            [{"field": "research_question", "operation": "REPLACE", "value": "旧研究问题"}],
            provenance="STUDENT",
        )
        session.stage_outputs[Stage.RESEARCH_QUESTION.value] = {
            "stage_payload": {"main_research_question": "旧研究问题"}
        }
        ensure_initial_version(session)
        apply_design_updates(
            session,
            [{"field": "research_question", "operation": "REPLACE", "value": "新研究问题"}],
            provenance="STUDENT",
        )
        session.stage_outputs[Stage.RESEARCH_QUESTION.value]["stage_payload"][
            "main_research_question"
        ] = "新研究问题"
        record_design_version(
            session,
            changed_fields=["research_question"],
            reason="修改研究问题",
        )

        execute_version_request(
            session,
            {"action": "RESTORE", "version_id": "v0001", "fields": ["research_question"]},
        )

        self.assertEqual(ensure_design_state(session)["research_question"], "旧研究问题")
        self.assertEqual(
            session.stage_outputs[Stage.RESEARCH_QUESTION.value]["stage_payload"][
                "main_research_question"
            ],
            "旧研究问题",
        )

    def test_version_restore_preserves_stage_clear_tombstones(self) -> None:
        session = DesignSession("stage-clear-restore", InteractionState.EMVR_DIRECT)
        session.design_context["stage_design_state"] = {
            "expected_results": "旧的已确认预期结果。"
        }
        ensure_initial_version(session)
        apply_stage_field_updates(
            session,
            [{"field": "expected_results", "operation": "CLEAR"}],
            stage=Stage.RESULT_INTERPRETATION,
        )
        cleared_version = record_design_version(
            session,
            changed_fields=["expected_results"],
            reason="清空预期结果",
        )

        execute_version_request(
            session,
            {"action": "RESTORE", "version_id": "v0001", "fields": ["expected_results"]},
        )
        self.assertEqual(
            builder_requirement_values(session)["expected_results"],
            "旧的已确认预期结果。",
        )
        self.assertNotIn(
            "expected_results",
            session.design_context["stage_design_state"]["explicitly_cleared_fields"],
        )

        execute_version_request(
            session,
            {
                "action": "RESTORE",
                "version_id": cleared_version["version_id"],
                "fields": ["expected_results"],
            },
        )
        self.assertEqual(builder_requirement_values(session)["expected_results"], "")
        self.assertIn(
            "expected_results",
            session.design_context["stage_design_state"]["explicitly_cleared_fields"],
        )


class ReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WorkflowEngine(generator=RuleBasedStageGenerator())

    def test_turn_id_retry_returns_same_response_without_second_state_change(self) -> None:
        created = self.engine.create_design("我想研究传输线反射")
        request = TurnRequest(
            message="我想比较不同负载下的反射图样",
            turn_id="turn_retry_0001",
        )

        first = self.engine.process_turn(created["design_id"], request)
        revision_after_first = self.engine.store.get(created["design_id"]).revision
        second = self.engine.process_turn(created["design_id"], request)

        self.assertEqual(second, first)
        self.assertEqual(
            self.engine.store.get(created["design_id"]).revision,
            revision_after_first,
        )

    def test_turn_id_cannot_be_reused_for_different_content(self) -> None:
        created = self.engine.create_design("我想研究传输线反射")
        self.engine.process_turn(
            created["design_id"],
            TurnRequest(message="补充负载条件", turn_id="turn_conflict_1"),
        )

        with self.assertRaises(SessionConflict):
            self.engine.process_turn(
                created["design_id"],
                TurnRequest(message="改成材料条件", turn_id="turn_conflict_1"),
            )

    def test_resume_rotates_both_access_and_resume_credentials(self) -> None:
        created = self.engine.create_design("我想研究静电场")
        restored = self.engine.resume_design(
            created["design_id"], created["design_resume_token"]
        )

        self.assertFalse(
            self.engine.verify_design_token(
                created["design_id"], created["design_access_token"]
            )
        )
        self.assertTrue(
            self.engine.verify_design_token(
                created["design_id"], restored["design_access_token"]
            )
        )
        with self.assertRaises(DesignAccessDenied):
            self.engine.resume_design(
                created["design_id"], created["design_resume_token"]
            )

    def test_guided_export_contains_the_students_own_summary(self) -> None:
        session = DesignSession(
            "guided-export",
            InteractionState.GUIDED_DESIGN,
            status=WorkflowStatus.COMPLETE,
            design_context={
                "synthesis": {
                    "student_summary": "我设计的实验比较距离变化对电场线分布的影响。"
                }
            },
        )
        self.engine.store.save(session)

        text = self.engine.render_guided_summary_text(session.design_id).decode("utf-8")

        self.assertIn("我设计的实验", text)
        self.assertNotIn("Agent总结", text)

    def test_guided_export_uses_final_committed_design_values(self) -> None:
        session = DesignSession(
            "guided-export-committed",
            InteractionState.GUIDED_DESIGN,
            status=WorkflowStatus.COMPLETE,
            design_context={
                "synthesis": {
                    "student_summary": "我设计的实验比较距离变化对电场线分布的影响。"
                }
            },
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "research_question",
                    "operation": "REPLACE",
                    "value": "最终修订后的研究问题",
                }
            ],
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "independent_variable",
                    "operation": "REPLACE",
                    "value": "最终修订后的距离变量",
                }
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        self.engine.store.save(session)

        text = self.engine.render_guided_summary_text(session.design_id).decode("utf-8")

        self.assertIn("【学生完成的总结】", text)
        self.assertIn("【已确认并保留的设计记录】", text)
        self.assertIn("最终修订后的研究问题", text)
        self.assertIn("最终修订后的距离变量", text)

    def test_structured_version_request_bypasses_natural_language_routing(self) -> None:
        created = self.engine.create_design("我想研究静电场")
        result = self.engine.process_turn(
            created["design_id"],
            {
                "message": "显示修改记录",
                "turn_id": "turn_versions_01",
                "version_request": {"action": "VIEW_RECENT"},
            },
        )

        self.assertEqual(
            result["stage_payload"]["version_control"][0]["action"],
            "VIEW_RECENT",
        )
        self.assertTrue(result["stage_payload"]["preserve_pending_action"])


if __name__ == "__main__":
    unittest.main()
