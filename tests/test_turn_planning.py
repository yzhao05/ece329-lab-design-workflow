from __future__ import annotations

import unittest

from ece329_workflow.design_state import (
    apply_design_updates,
    design_state_snapshot,
    is_topic_locked,
    topic_lock_snapshot,
)
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import build_carried_context
from ece329_workflow.design_quality import evaluate_design_quality
from ece329_workflow.engine import _emvr_stage_entry_output
from ece329_workflow.generator import guided_stage_entry_output
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.turn_planning import (
    build_stage_context_summary,
    build_turn_task_plan,
    compute_design_diff,
    finalize_turn_task_plan,
    student_change_notice,
    workflow_design_snapshot,
)


class TurnPlanningTests(unittest.TestCase):
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
                {"field": "parameter_specifications", "operation": "REPLACE", "value": "距离0.2 m至2.0 m，步长0.1 m"},
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
