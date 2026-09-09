from __future__ import annotations

import unittest
from unittest.mock import patch

from ece329_workflow.design_state import (
    apply_design_updates,
    design_state_snapshot,
    is_topic_locked,
    topic_lock_snapshot,
)
from ece329_workflow.dialogue_acts import (
    DESIGN_ACT_FIELDS,
    STAGE_ACT_FIELDS,
    apply_stage_field_updates,
    compile_dialogue_acts,
)
from ece329_workflow.emvr_design import EMVR_EDITABLE_FIELDS
from ece329_workflow.dialogue_state import build_carried_context
from ece329_workflow.design_quality import evaluate_design_quality
from ece329_workflow.engine import (
    _EMVR_COMPLETION_REPAIR_FIELD_BINDINGS,
    _emvr_stage_entry_output,
    _has_structured_stage_content,
    _prepare_emvr_completion_repair,
)
from ece329_workflow.generator import guided_stage_entry_output
from ece329_workflow.models import DesignSession, InteractionState, Stage, StepOutput
from ece329_workflow.reporting import effective_emvr_stage_payload
from ece329_workflow.turn_planning import (
    build_stage_context_summary,
    build_turn_task_plan,
    compute_design_diff,
    finalize_turn_task_plan,
    student_change_notice,
    workflow_design_snapshot,
)


class TurnPlanningTests(unittest.TestCase):
    def test_every_completion_repair_binding_is_writable(self) -> None:
        writable = {*DESIGN_ACT_FIELDS, *STAGE_ACT_FIELDS, *EMVR_EDITABLE_FIELDS}

        self.assertTrue(
            set(_EMVR_COMPLETION_REPAIR_FIELD_BINDINGS.values()) <= writable
        )
        for stage in Stage:
            session = DesignSession(
                design_id=f"repair-binding-{stage.value}",
                interaction_state=InteractionState.EMVR_DIRECT,
                current_stage_index=list(Stage).index(stage),
            )
            output = StepOutput(assistant_message="当前草稿")
            _prepare_emvr_completion_repair(session, stage, output)
            if "completion_repair" not in output.stage_payload:
                continue
            pending = output.stage_payload["pending_action"]
            self.assertEqual(len(pending["answer_fields"]), 1, stage.value)
            self.assertIn(pending["answer_fields"][0], writable, stage.value)

    def test_emvr_hypothesis_entry_asks_student_instead_of_confirming_placeholder(self) -> None:
        session = DesignSession(
            design_id="emvr-hypothesis-entry",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.HYPOTHESIS),
            design_context={
                "idea": {},
                "stage_design_state": {
                    "builder_workspace_absolute_path": r"E:\暑研\EMVR_Blind_BuilderPack",
                },
                "emvr_design": {
                    "field_state": {
                        "lab_title": "双电荷场线实验",
                        "lab_id": "two_charge_field",
                    }
                },
            },
        )

        output = _emvr_stage_entry_output(session, Stage.HYPOTHESIS)

        self.assertEqual(
            output.stage_payload["pending_action"]["type"],
            "ANSWER_EMVR_STAGE_QUESTION",
        )
        self.assertFalse(output.stage_payload["pending_action"]["advance_on_accept"])
        self.assertEqual(
            output.stage_payload["pending_action"]["answer_fields"],
            ["research_hypothesis"],
        )
        self.assertNotIn(
            "预期变化趋势",
            output.stage_payload["pending_action"].get("proposal", {}),
        )

    def test_new_emvr_hypothesis_and_visual_fields_count_as_real_answers(self) -> None:
        session = DesignSession(
            design_id="emvr-new-report-fields",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "research_hypothesis",
                    "operation": "REPLACE",
                    "value": "距离减小时中间区域场强增大",
                },
                {
                    "field": "trend_annotation",
                    "operation": "REPLACE",
                    "value": "用实时曲线和方向箭头标出变化趋势",
                },
            ],
            stage=Stage.HYPOTHESIS,
        )

        self.assertTrue(_has_structured_stage_content(session, Stage.HYPOTHESIS))
        self.assertTrue(
            _has_structured_stage_content(session, Stage.EXPECTED_DATA_VISUALIZATION)
        )

    def test_emvr_completion_gap_binds_direct_report_field(self) -> None:
        session = DesignSession(
            design_id="emvr-visual-gap",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.EXPECTED_DATA_VISUALIZATION),
        )
        output = StepOutput(assistant_message="当前显示草稿")
        issue = {
            "field": "trend_annotation",
            "label": "趋势标注",
            "question": "理论趋势在界面上如何标注？",
        }

        with patch("ece329_workflow.engine.emvr_stage_completeness_issues", return_value=[issue]):
            _prepare_emvr_completion_repair(
                session,
                Stage.EXPECTED_DATA_VISUALIZATION,
                output,
            )

        self.assertEqual(
            output.stage_payload["pending_action"]["answer_fields"],
            ["trend_annotation"],
        )

    def test_emvr_completion_gaps_bind_to_canonical_writable_fields(self) -> None:
        session = DesignSession(
            design_id="emvr-report-field-bindings",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.RESEARCH_QUESTION),
        )
        cases = {
            "main_research_question": "research_question",
            "adjustable_quantity_in_vr": "changed_quantities",
            "object_inventory": "unity_objects",
            "baseline_comparisons": "comparison_logic",
            "visualization_requirements": "visualization_plan",
            "if_prediction_supported": "if_prediction_supported",
        }
        for report_field, expected_field in cases.items():
            output = StepOutput(assistant_message="当前草稿")
            issue = {
                "field": report_field,
                "label": report_field,
                "question": "请补充这一项。",
            }
            with self.subTest(report_field=report_field), patch(
                "ece329_workflow.engine.emvr_stage_completeness_issues",
                return_value=[issue],
            ):
                _prepare_emvr_completion_repair(
                    session,
                    Stage.RESEARCH_QUESTION,
                    output,
                )
                self.assertEqual(
                    output.stage_payload["pending_action"]["answer_fields"],
                    [expected_field],
                )

    def test_comparison_logic_satisfies_final_quality_baseline_check(self) -> None:
        session = DesignSession(
            design_id="emvr-comparison-logic-quality",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(
                Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            ),
        )
        apply_design_updates(
            session,
            [
                {
                    "field": field,
                    "operation": "REPLACE",
                    "value": value,
                }
                for field, value in {
                    "research_object": "两个点电荷",
                    "course_relationship": "库仑场的矢量叠加",
                    "learning_objective": "解释距离变化如何改变合场",
                    "research_question": "距离变化时中点场强如何变化？",
                    "hypothesis": "距离减小时中点场强增大",
                }.items()
            ],
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": field,
                    "operation": "REPLACE",
                    "value": value,
                }
                for field, value in {
                    "independent_variable": "两电荷间距",
                    "observations": "中点合场强度",
                    "controlled_conditions": "电荷量和介质保持不变",
                    "procedure_steps": "改变间距并记录合场",
                    "visualization_plan": "显示场强曲线和矢量箭头",
                    "result_interpretation": "比较实测趋势与理论预测",
                    "limitations": "仅适用于点电荷近似",
                    "comparison_logic": "以间距 1.0 m 为基准状态",
                }.items()
            ],
            stage=Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        )

        review = evaluate_design_quality(session, final_review=True)

        self.assertFalse(
            any(
                "baseline_comparisons" in issue.get("fields", [])
                for issue in review["issues"]
            )
        )

    def test_emvr_overlapping_stage_edit_uses_typed_emvr_state(self) -> None:
        compiled = compile_dialogue_acts(
            [
                {
                    "act_id": "replace-procedure",
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "procedure_steps",
                    "operation": "REPLACE",
                    "content": ["建立基准", "改变距离", "记录场线", "切换极性", "比较结果"],
                }
            ],
            pending_action={
                "interaction_state": InteractionState.EMVR_DIRECT.value,
                "type": "ANSWER_EMVR_STAGE_QUESTION",
                "subject": Stage.CONCEPTUAL_PROCEDURE.value,
                "answer_fields": ["procedure_steps"],
            },
        )

        self.assertFalse(compiled["stage_field_updates"])
        self.assertEqual(
            compiled["emvr_field_updates"][0]["field_id"],
            "procedure_steps",
        )
        self.assertEqual(len(compiled["emvr_field_updates"][0]["value"]), 5)

    def test_latest_student_stage_revision_overrides_stale_emvr_report_value(self) -> None:
        session = DesignSession(
            design_id="emvr-latest-report-value",
            interaction_state=InteractionState.EMVR_DIRECT,
            design_context={
                "emvr_design": {
                    "field_state": {
                        "desktop_interaction_plan": "旧的鼠标操作",
                        "expected_results": ["旧的预期结果"],
                    }
                }
            },
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "desktop_interaction_plan",
                    "operation": "REPLACE",
                    "value": "鼠标拖动带电球并实时刷新场线",
                },
                {
                    "field": "expected_results",
                    "operation": "REPLACE",
                    "value": "距离减小时中间区域场强按理论趋势变化",
                },
            ],
            stage=Stage.RESULT_INTERPRETATION,
        )

        setup = effective_emvr_stage_payload(session, Stage.CONCEPTUAL_OR_VR_SETUP)
        results = effective_emvr_stage_payload(session, Stage.RESULT_INTERPRETATION)

        self.assertEqual(
            setup["desktop_interaction_plan"],
            "鼠标拖动带电球并实时刷新场线",
        )
        self.assertEqual(
            results["expected_results"],
            "距离减小时中间区域场强按理论趋势变化",
        )

    def test_emvr_formula_actions_are_executable_state_tasks(self) -> None:
        plan = build_turn_task_plan(
            [
                {
                    "act_id": "formula-topic",
                    "type": "SET_EMVR_TOPIC",
                    "target": "emvr_formula_topic",
                    "operation": "EXECUTE",
                    "content": {"topic_description": "静电场实验"},
                }
            ]
        )

        self.assertEqual(plan["tasks"][0]["execution_phase"], "COMMIT_DESIGN")
        self.assertEqual(plan["tasks"][0]["status"], "READY")
        self.assertFalse(plan["has_unresolved_work"])

    def test_emvr_builder_fields_feed_quality_and_stage_context(self) -> None:
        session = DesignSession(
            design_id="emvr_unified_context",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.THEORETICAL_FRAMEWORK),
            design_context={
                "emvr_design": {
                    "field_state": {
                        "experiment_brief": "在VR中改变两个带电物体的距离并观察场线变化",
                        "research_question": "距离变化如何影响两物体之间的场线分布？",
                        "changed_quantities": ["两个带电物体之间的距离"],
                        "observed_quantities": ["中间区域的电场线弯曲与连接"],
                        "conceptual_objective": "解释静电场叠加如何改变空间场线",
                    }
                }
            },
        )

        snapshot = workflow_design_snapshot(session)
        context = build_carried_context(session)
        review = evaluate_design_quality(session)

        self.assertEqual(
            snapshot["independent_variable"],
            ["两个带电物体之间的距离"],
        )
        self.assertEqual(
            context["observations"],
            ["中间区域的电场线弯曲与连接"],
        )
        self.assertIn("解释静电场叠加", str(context["learning_objective"]))
        completeness_missing = {
            field
            for issue in review["issues"]
            if issue.get("category") == "COMPLETENESS"
            for field in issue.get("fields", [])
        }
        self.assertNotIn("independent_variable", completeness_missing)
        self.assertNotIn("observations", completeness_missing)
        self.assertTrue(
            review["feasibility"]["independent_variable_can_change"]
        )

    def test_mixed_turn_is_planned_as_independent_tasks(self) -> None:
        plan = build_turn_task_plan(
            [
                {
                    "act_id": "keep_cases",
                    "type": "CONTROL",
                    "target": "ACCEPT",
                    "operation": "EXECUTE",
                    "content": None,
                },
                {
                    "act_id": "rewrite_question",
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": "距离减小时，中间区域的场线如何变化？",
                },
                {
                    "act_id": "course_reference",
                    "type": "REQUEST_REFERENCE",
                    "target": "research_question",
                    "operation": "EXECUTE",
                    "content": None,
                },
            ]
        )

        self.assertEqual(
            plan["execution_order"],
            ["rewrite_question", "course_reference", "keep_cases"],
        )
        self.assertEqual(len(plan["tasks"]), 3)

    def test_correction_with_nested_updates_is_reported_as_committed_work(self) -> None:
        plan = build_turn_task_plan(
            [
                {
                    "act_id": "repair_observation",
                    "type": "CORRECT_ASSISTANT",
                    "target": "previous_design_draft",
                    "operation": "MERGE",
                    "content": {
                        "error_type": "MISUNDERSTANDING",
                        "affected_fields": ["observations"],
                        "stage_field_updates": [
                            {
                                "field": "observations",
                                "operation": "REPLACE",
                                "value": "中间区域的场量峰值与低谷位置",
                            }
                        ],
                    },
                }
            ]
        )

        self.assertEqual(plan["tasks"][0]["execution_phase"], "COMMIT_DESIGN")
        finalized = finalize_turn_task_plan(
            plan,
            {
                "changed_fields": ["observations"],
                "unchanged_requested_fields": [],
            },
            response_generated=True,
            transition_requested=False,
            transition_completed=False,
        )
        self.assertEqual(finalized["tasks"][0]["status"], "APPLIED")

    def test_semantic_key_prevents_paraphrase_duplication(self) -> None:
        session = DesignSession(
            design_id="semantic_dedupe",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "REPLACE",
                    "value": "闭合面完全包围场源",
                    "semantic_key": "surface_fully_encloses_source",
                }
            ],
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "MERGE",
                    "value": "曲面把整个场源包住",
                    "semantic_key": "surface_fully_encloses_source",
                }
            ],
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "procedure_steps",
                    "operation": "REPLACE",
                    "value": "先建立远距离基准",
                    "semantic_key": "establish_far_distance_baseline",
                },
                {
                    "field": "procedure_steps",
                    "operation": "MERGE",
                    "value": "首先记录两物体相距较远的基准",
                    "semantic_key": "establish_far_distance_baseline",
                },
            ],
            stage=Stage.CONCEPTUAL_PROCEDURE,
        )

        self.assertEqual(
            design_state_snapshot(session)["conceptual_structure"],
            "闭合面完全包围场源",
        )
        self.assertEqual(
            workflow_design_snapshot(session)["procedure_steps"],
            "先建立远距离基准",
        )

    def test_research_question_locks_topic_without_blocking_field_edits(self) -> None:
        session = DesignSession(
            design_id="topic_lock",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "research_object",
                    "operation": "REPLACE",
                    "value": "两个点电荷之间的场线分布",
                },
                {
                    "field": "research_question",
                    "operation": "REPLACE",
                    "value": "距离从远到近时，中间区域的场线如何变化？",
                },
            ],
        )

        self.assertTrue(is_topic_locked(session))
        lock = topic_lock_snapshot(session)
        self.assertIn("距离从远到近", lock["confirmed_research_question"])

        apply_design_updates(
            session,
            [
                {
                    "field": "learning_objective",
                    "operation": "REPLACE",
                    "value": "解释叠加关系与场线形状之间的联系",
                }
            ],
        )
        self.assertIn(
            "距离从远到近",
            topic_lock_snapshot(session)["confirmed_research_question"],
        )

    def test_change_notice_uses_only_actual_committed_delta(self) -> None:
        session = DesignSession(
            design_id="change_notice",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "observations",
                    "operation": "REPLACE",
                    "value": "中间区域的场线弯曲程度",
                    "semantic_key": "midplane_field_line_curvature",
                }
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        before = workflow_design_snapshot(session)
        plan = build_turn_task_plan(
            [
                {
                    "act_id": "same_observation",
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "observations",
                    "operation": "MERGE",
                    "content": "两源中间的场线弯折程度",
                }
            ]
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "observations",
                    "operation": "MERGE",
                    "value": "两源中间的场线弯折程度",
                    "semantic_key": "midplane_field_line_curvature",
                }
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        diff = compute_design_diff(before, workflow_design_snapshot(session), plan)
        notice = student_change_notice(diff, InteractionState.GUIDED_DESIGN)

        self.assertFalse(diff["has_changes"])
        self.assertIn("保持不变", notice)
        self.assertNotIn("状态", notice)

    def test_later_stage_entries_receive_complete_confirmed_context(self) -> None:
        session = DesignSession(
            design_id="stage_handoff",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=7,
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "research_object",
                    "operation": "REPLACE",
                    "value": "两个点电荷靠近时的场线变化",
                },
                {
                    "field": "learning_objective",
                    "operation": "REPLACE",
                    "value": "解释叠加原理如何改变场线形状",
                },
                {
                    "field": "research_question",
                    "operation": "REPLACE",
                    "value": "距离减小时，中间区域的场线如何变化？",
                },
                {
                    "field": "hypothesis",
                    "operation": "REPLACE",
                    "value": "距离越近，场线弯曲越明显",
                },
            ],
        )
        summary = build_stage_context_summary(session)
        output = guided_stage_entry_output(session)

        self.assertEqual(summary["for_stage"], Stage.VARIABLES_AND_CONDITIONS.value)
        self.assertIn("learning_objective", summary["confirmed"])
        self.assertIn("research_question", summary["confirmed"])
        self.assertTrue(output.stage_payload["reference_draft"])
        self.assertTrue(
            any(
                "研究问题" in item
                for item in output.stage_payload["reference_basis"]
            )
        )
        self.assertNotIn("我们已经有这些线索", output.assistant_message)

    def test_emvr_entry_starts_from_a_professional_editable_draft(self) -> None:
        session = DesignSession(
            design_id="emvr_reference",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=7,
        )
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "independent_variable",
                    "operation": "REPLACE",
                    "value": "两个电荷之间的距离",
                },
                {
                    "field": "observations",
                    "operation": "REPLACE",
                    "value": "中间区域的场线变化",
                },
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        apply_stage_field_updates(
            session,
            [
                {"field": "lab_title", "operation": "REPLACE", "value": "双电荷实验"},
                {"field": "lab_id", "operation": "REPLACE", "value": "ece329_charge_field"},
                {"field": "desktop_interaction_plan", "operation": "REPLACE", "value": "鼠标拖动物体，VR映射为手柄抓取"},
                {"field": "room_spatial_requirements", "operation": "REPLACE", "value": "对象在前方并保留绕行空间"},
                {"field": "hidden_object_lifecycle", "operation": "REPLACE", "value": "无"},
                {"field": "builder_workspace_absolute_path", "operation": "REPLACE", "value": r"E:\暑研\EMVR_Blind_BuilderPack"},
                {"field": "initial_reset_state", "operation": "REPLACE", "value": "Initial与Reset均恢复同种电荷、距离1.0 m并清除读数。"},
                {"field": "parameter_specifications", "operation": "REPLACE", "value": "距离为可调公式自变量，默认1.0 m，范围0.2 m至2.0 m，步长0.1 m。"},
                {"field": "model_constants_and_media", "operation": "REPLACE", "value": "固定真空介电常数epsilon_0=8.8541878128e-12 F/m，介质固定为真空。"},
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        output = _emvr_stage_entry_output(
            session,
            Stage.VARIABLES_AND_CONDITIONS,
        )

        self.assertTrue(output.stage_payload["reference_draft"])
        self.assertIn("可调参数", output.assistant_message)
        self.assertIn("观察量", output.assistant_message)
        pending = output.stage_payload["pending_action"]
        self.assertEqual(pending["type"], "CONFIRM_STAGE_OR_MODIFY")
        self.assertTrue(pending["advance_on_accept"])
        self.assertIn("ACCEPT_PREVIOUS_PROPOSAL", pending["allowed_intents"])

    def test_public_session_hides_orchestration_metadata(self) -> None:
        session = DesignSession(
            design_id="public_state",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "research_question",
                    "operation": "REPLACE",
                    "value": "距离如何影响场线？",
                    "semantic_key": "distance_changes_field_lines",
                }
            ],
        )
        public = session.to_dict()["design_context"]["design_state"]

        self.assertNotIn("topic_lock", public)
        self.assertNotIn("semantic_signatures", public)


if __name__ == "__main__":
    unittest.main()
