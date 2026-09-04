from __future__ import annotations

import unittest

from ece329_workflow.design_state import (
    apply_design_updates,
    design_state_snapshot,
    ensure_design_state,
    format_design_summary,
    record_seen_scenes,
    set_baseline_comparisons,
    seen_scene_signatures,
)
from ece329_workflow.dialogue_state import (
    UserIntent,
    apply_resolved_intent,
    current_pending_action,
    resolved_intent,
    save_pending_action,
    validate_resolved_intent,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator, build_exploration_scenes
from ece329_workflow.models import DesignSession, InteractionState, Stage, StepOutput


class SummarySemanticGenerator(RuleBasedStageGenerator):
    def resolve_intent(self, session, user_message, pending_action, carried_context):
        return resolved_intent(
            UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY,
            target="current_design",
            confidence=0.99,
            source="SEMANTIC_TEST",
        )


class ComparisonSummarySemanticGenerator(RuleBasedStageGenerator):
    def resolve_intent(self, session, user_message, pending_action, carried_context):
        return resolved_intent(
            UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY,
            target="baseline_comparisons",
            resolved_value=["baseline_comparisons"],
            confidence=0.99,
            source="SEMANTIC_TEST",
        )


class CanonicalDesignStateTests(unittest.TestCase):
    def test_field_heading_typo_is_not_duplicated_in_theory_value(self) -> None:
        session = DesignSession(
            design_id="theory_heading_cleanup",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )

        apply_design_updates(
            session,
            [
                {
                    "field": "theoretical_framework",
                    "operation": "REPLACE",
                    "value": "**论依据是高斯定理**：闭合曲面总通量由面内净电荷决定。",
                }
            ],
        )

        self.assertEqual(
            design_state_snapshot(session)["theoretical_framework"],
            "高斯定理：闭合曲面总通量由面内净电荷决定。",
        )

    def test_comparison_cases_can_be_promoted_from_confirmed_structure(self) -> None:
        session = DesignSession(
            design_id="comparison_from_confirmed_structure",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        cases = [
            "曲面完全包住场源",
            "曲面部分包住场源",
            "曲面未包住场源",
        ]
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "REPLACE",
                    "value": f"参照情形包括{'\u3001'.join(cases)}。",
                }
            ],
        )
        act = {
            "type": "MODIFY_COMPARISON",
            "target": "baseline_comparisons",
            "operation": "MERGE",
            "content": {
                "action": "CREATE",
                "title": "闭合曲面与场源的位置关系",
                "new_cases": cases,
            },
            "confidence": 0.99,
        }
        raw = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            dialogue_acts=[act],
            actions_authoritative=True,
        )
        validated = validate_resolved_intent(raw, None)

        apply_resolved_intent(
            session,
            validated,
            None,
            "请把前面的参照情形恢复为基础比较。",
        )

        self.assertEqual(
            design_state_snapshot(session)["baseline_comparisons"][0]["cases"],
            cases,
        )

    def test_unconfirmed_legacy_creation_message_is_not_a_research_object(self) -> None:
        raw_request = "请先帮助我浏览课程里的实验方向"
        session = DesignSession(
            design_id="design_unconfirmed_legacy_seed",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {"original": raw_request},
                "design_state": {
                    "legacy_migrated": True,
                    "research_object": raw_request,
                    "field_provenance": {},
                },
            },
        )

        state = ensure_design_state(session)

        self.assertEqual(state["research_object"], "")

    def test_confirmed_legacy_original_is_preserved_as_research_object(self) -> None:
        original = "比较传输线终端条件与反射的关系"
        session = DesignSession(
            design_id="design_confirmed_legacy_seed",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "original": original,
                    "course_scope_confirmed": True,
                    "direction_locked": True,
                },
                "design_state": {
                    "legacy_migrated": True,
                    "research_object": original,
                    "field_provenance": {},
                },
            },
        )

        state = ensure_design_state(session)

        self.assertEqual(state["research_object"], original)

    def test_course_scoped_but_unselected_legacy_request_is_not_a_research_object(self) -> None:
        raw_request = "请先帮助我浏览课程里的实验方向"
        session = DesignSession(
            design_id="design_course_scoped_unselected_seed",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "original": raw_request,
                    "course_scope_confirmed": True,
                    "direction_locked": False,
                },
                "design_state": {
                    "legacy_migrated": True,
                    "research_object": raw_request,
                    "field_provenance": {},
                },
            },
        )

        state = ensure_design_state(session)

        self.assertEqual(state["research_object"], "")

    def test_scene_templates_are_excluded_across_rounds(self) -> None:
        session = DesignSession(
            design_id="design_scene_history",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        options = [
            {
                "option_id": f"option_{index}",
                "direction": "静电场与场源分布",
                "focus": "电荷分布与空间场形状之间的关系",
            }
            for index in range(3)
        ]
        first = build_exploration_scenes(options)
        record_seen_scenes(session, first)
        second = build_exploration_scenes(
            options,
            excluded_scene_signatures=seen_scene_signatures(session),
        )

        first_ids = {scene["scene_template_id"] for scene in first}
        second_ids = {scene["scene_template_id"] for scene in second}
        self.assertTrue(
            first_ids.isdisjoint(second_ids),
            f"first={first_ids}, second={second_ids}",
        )
        public_session = session.to_dict()
        self.assertNotIn(
            "seen_scene_template_ids",
            public_session["design_context"]["design_state"],
        )

    def test_pre_upgrade_scene_history_is_migrated_before_next_batch(self) -> None:
        session = DesignSession(
            design_id="design_legacy_scene_history",
            interaction_state=InteractionState.GUIDED_DESIGN,
            history=[
                {
                    "output": {
                        "stage_payload": {
                            "exploration_scenes": [
                                {
                                    "title": "让两个场源从远处慢慢靠近",
                                }
                            ]
                        }
                    }
                }
            ],
        )
        excluded = seen_scene_signatures(session)
        scenes = build_exploration_scenes(
            [
                {
                    "option_id": "new_option",
                    "direction": "静电场与场源分布",
                    "focus": "电荷分布与空间场形状之间的关系",
                }
            ],
            excluded_scene_signatures=excluded,
        )

        self.assertNotEqual(
            scenes[0]["title"],
            "让两个场源从远处慢慢靠近",
        )

    def test_merge_updates_are_idempotent(self) -> None:
        session = DesignSession(
            design_id="design_idempotent_merge",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        pending = {"action_id": "action_concept", "subject": "conceptual_structure"}
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "REPLACE",
                    "value": "显示电场线",
                }
            ],
            pending_action=pending,
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "MERGE",
                    "value": "增加等势面显示",
                }
            ],
            pending_action=pending,
        )
        revision_after_first_merge = design_state_snapshot(session)["revision"]
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "MERGE",
                    "value": "增加等势面显示",
                }
            ],
            pending_action=pending,
        )

        state = design_state_snapshot(session)
        self.assertEqual(state["revision"], revision_after_first_merge)
        self.assertEqual(
            state["conceptual_structure"].count("增加等势面显示"),
            1,
        )
        self.assertIn("显示电场线", state["conceptual_structure"])

    def test_read_only_summary_includes_learning_objective_and_preserves_pending(self) -> None:
        engine = WorkflowEngine(generator=SummarySemanticGenerator())
        session = DesignSession(
            design_id="design_read_only_summary",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
        )
        apply_design_updates(
            session,
            [
                {"field": "research_object", "operation": "REPLACE", "value": "两个点电荷"},
                {"field": "course_relationship", "operation": "REPLACE", "value": "静电场叠加"},
                {
                    "field": "learning_objective",
                    "operation": "REPLACE",
                    "value": "解释距离和极性怎样改变场线分布",
                },
                {
                    "field": "expected_phenomenon",
                    "operation": "REPLACE",
                    "value": "靠近时中间区域的场线重新分布",
                },
                {
                    "field": "theoretical_framework",
                    "operation": "REPLACE",
                    "value": "电场叠加与高斯定律",
                },
            ],
        )
        save_pending_action(
            session,
            session.current_stage,
            StepOutput(
                assistant_message="我们继续完善变量。",
                stage_payload={"proposal": {"stage": session.current_stage.value}},
                student_task="你认为哪些量需要改变和观察？",
            ),
        )
        before = current_pending_action(session)
        assert before is not None
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "把研究对象、课程关系、学习目标和预期现象逐项列出来"},
        )

        self.assertTrue(result["stage_payload"]["read_only_design_summary"])
        self.assertIn("学习目标：解释距离和极性怎样改变场线分布", result["assistant_message"])
        self.assertIn("理论依据：电场叠加与高斯定律", result["assistant_message"])
        self.assertIn("预期现象：靠近时中间区域的场线重新分布", result["assistant_message"])
        self.assertNotIn(
            "seen_scene_template_ids",
            result["stage_payload"]["design_state"],
        )
        after = current_pending_action(engine.store.get(session.design_id))
        assert after is not None
        self.assertEqual(after["action_id"], before["action_id"])

    def test_new_comparison_dimension_is_committed_and_idempotent(self) -> None:
        session = DesignSession(
            design_id="design_new_comparison_dimension",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={"idea": {}},
        )
        set_baseline_comparisons(
            session,
            [
                {
                    "comparison_id": "polarity_cases",
                    "title": "电荷符号",
                    "cases": ["同种电荷", "异种电荷"],
                    "adoption_status": "ACCEPTED",
                }
            ],
        )
        message = "增加不同电荷量大小的对比情形，其余内容保持不变"
        decision = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            resolved_value=message,
            confidence=0.98,
            source="SEMANTIC_TEST",
            semantic_updates={
                "comparison_updates": [
                    {
                        "comparison_id": "",
                        "action": "CREATE",
                        "title": "不同电荷量大小的对比情形",
                        "new_cases": ["不同电荷量大小的对比情形"],
                    }
                ]
            },
        )

        apply_resolved_intent(session, decision, None, message)
        apply_resolved_intent(session, decision, None, message)

        comparisons = design_state_snapshot(session)["baseline_comparisons"]
        self.assertEqual(len(comparisons), 2)
        self.assertEqual(
            comparisons[1]["cases"],
            ["不同电荷量大小的对比情形"],
        )
        comparison_only = format_design_summary(
            session,
            ["baseline_comparisons"],
        )
        self.assertIn("同种电荷、异种电荷", comparison_only)
        self.assertIn("不同电荷量大小的对比情形", comparison_only)
        self.assertNotIn("研究对象", comparison_only)
        self.assertNotIn(
            "不同电荷量大小的对比情形：不同电荷量大小的对比情形",
            comparison_only,
        )

    def test_confirmed_new_comparison_uses_original_student_evidence(self) -> None:
        session = DesignSession(
            design_id="design_confirmed_new_comparison",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={"idea": {}},
        )
        supplement = "增加等量电荷与不等量电荷这组基础比较"
        decision = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            resolved_value=supplement,
            confidence=0.98,
            source="CONFIRMED_PENDING_MODIFICATION",
            semantic_updates={
                "comparison_updates": [
                    {
                        "comparison_id": "",
                        "action": "CREATE",
                        "title": "电荷量大小",
                        "new_cases": ["等量电荷", "不等量电荷"],
                    }
                ]
            },
        )

        apply_resolved_intent(session, decision, None, "确认合并")

        comparisons = design_state_snapshot(session)["baseline_comparisons"]
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(
            comparisons[0]["cases"],
            ["等量电荷", "不等量电荷"],
        )
        self.assertEqual(
            decision["semantic_updates"]["applied_comparison_updates"],
            [
                {
                    "comparison_id": comparisons[0]["comparison_id"],
                    "action": "CREATE",
                    "cases": ["等量电荷", "不等量电荷"],
                }
            ],
        )

        apply_resolved_intent(session, decision, None, "再次确认")

        self.assertEqual(
            decision["semantic_updates"]["applied_comparison_updates"],
            [],
        )

    def test_summary_combines_identical_hypothesis_and_expected_phenomenon(self) -> None:
        session = DesignSession(
            design_id="design_deduplicated_prediction",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        prediction = "距离越近，场线弯曲越明显。"
        apply_design_updates(
            session,
            [
                {"field": "hypothesis", "operation": "REPLACE", "value": prediction},
                {
                    "field": "expected_phenomenon",
                    "operation": "REPLACE",
                    "value": prediction,
                },
            ],
        )

        summary = format_design_summary(session)

        self.assertIn("假设与预期现象", summary)
        self.assertEqual(summary.count(prediction), 1)

    def test_read_only_comparison_request_returns_only_latest_comparisons(self) -> None:
        engine = WorkflowEngine(generator=ComparisonSummarySemanticGenerator())
        session = DesignSession(
            design_id="design_comparison_only_summary",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={"idea": {}},
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "research_object",
                    "operation": "REPLACE",
                    "value": "两个点电荷",
                }
            ],
        )
        set_baseline_comparisons(
            session,
            [
                {
                    "comparison_id": "polarity_cases",
                    "title": "电荷符号",
                    "cases": ["同种电荷", "异种电荷"],
                    "adoption_status": "ACCEPTED",
                },
                {
                    "comparison_id": "magnitude_cases",
                    "title": "电荷量大小",
                    "cases": ["等量", "不等量"],
                    "adoption_status": "MODIFIED",
                },
            ],
        )
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "单独把更新后的基础比较列表展示给我"},
        )

        self.assertIn("基础比较：", result["assistant_message"])
        self.assertIn("同种电荷、异种电荷", result["assistant_message"])
        self.assertIn("等量、不等量", result["assistant_message"])
        self.assertNotIn("研究对象：", result["assistant_message"])


if __name__ == "__main__":
    unittest.main()
