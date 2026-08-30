from __future__ import annotations

import unittest

from ece329_workflow.design_quality import evaluate_design_quality
from ece329_workflow.design_state import (
    apply_design_updates,
    ensure_design_state,
    set_baseline_comparisons,
)
from ece329_workflow.dialogue_acts import apply_stage_field_updates
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
